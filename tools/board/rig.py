"""The timing rig's half that runs on the Pico.

It times real pulses with hardware, not with Python. A second PIO state machine
counts system-clock cycles between the rising edges on a pin, and a DMA channel
stores the counts, so nothing the processor is doing can colour the measurement.
It resolves 2 cycles, which is 13 ns at 150 MHz.

While it captures, it can load the Pico the way real use would: collecting
garbage, writing to flash, running the radio.

`tools/rig.py` copies this to the board and runs it, and `tools/soak.py`
runs its `soak` for hours. It is not part of PicoClock itself, and it only
runs on MicroPython.

Pins: the test board has GP0 jumpered to GP1, so a pulse sent on GP0 is read on
GP1. GP13, GP14 and GP15 carry a clock chip and are never touched.
"""

import array
import asyncio
import gc
import json
import machine
import os
import rp2
import sys
import time

import picoclock.core.clock
import picoclock.core.ratio
import picoclock.core.settings
import picoclock.core.transport
import picoclock.core.timeline
import picoclock.device.board
import picoclock.device.chip
import picoclock.device.led
import picoclock.device.panel
import picoclock.device.player
import picoclock.device.start
import picoclock.device.storage

SYSTEM_HZ = machine.freq()

# One beat is this many units of position, and one tick at a tempo of t
# hundredths of a BPM moves t of them: the scheduler's own arithmetic.
_UNITS = 6000 * 1000000

# PIO block 0 and the registers used here. The radio owns a state machine in
# block 2, so block 0 is clear.
_PIO0 = 0x50200000
_RXF0 = _PIO0 + 0x020

# The capture runs on state machine 1 of block 0, and the thing under test on 0.
_CAPTURE_MACHINE = 1
_CAPTURE_REQUEST = 4 + _CAPTURE_MACHINE

# The first counts are part-intervals from before the capture settled.
_SKIP = 2

# The furthest consecutive gaps together may come from as many exact gaps, in
# cycles: the tick every pulse is rounded to, the two cycles the capture sees
# an edge to, and the rounding down of the sum.
_DRIFT_MOST = SYSTEM_HZ // 1000000 + 4


@rp2.asm_pio()
def _capture_program ():

	"""Count between rising edges on the jump pin, two cycles to a count."""

	wrap_target()
	mov(x, invert(null))
	label("high")
	jmp(x_dec, "still_high")
	label("still_high")
	jmp(pin, "high")
	label("low")
	jmp(pin, "risen")
	jmp(x_dec, "low")
	label("risen")
	mov(isr, invert(x))
	push(noblock)
	wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def _freerun_program ():

	"""Pulse at a fixed gap with no help from the processor: the rig's own reference."""

	wrap_target()
	pull(noblock)
	mov(x, osr)
	mov(y, x)
	set(pins, 1) [31]
	set(pins, 0)
	label("wait")
	jmp(y_dec, "wait")
	wrap()


class Capture:

	"""Times the gaps between rising edges on one pin."""

	def __init__ (self, pin: int, count: int) -> None:

		"""Begin capturing this many gaps on a pin.

		Make this before the state machine that drives the pin: setting a pin
		up as an input afterwards would take it away from that state machine.
		"""

		self.count = count
		self.counts = array.array("I", [0] * count)
		self.machine = rp2.StateMachine(_CAPTURE_MACHINE, _capture_program, freq=SYSTEM_HZ, jmp_pin=machine.Pin(pin, machine.Pin.IN))
		self.dma = rp2.DMA()
		self.control = self.dma.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=_CAPTURE_REQUEST)

		self.dma.config(read=_RXF0 + 4 * _CAPTURE_MACHINE, write=self.counts, count=count, ctrl=self.control, trigger=True)
		self.machine.active(1)

	def again (self, count: int) -> None:

		"""Begin a fresh capture of this many gaps into the same memory, at most as many as it was made for.

		Whatever was waiting from before is dropped. Every count the state
		machine gives is a whole gap, so the first one taken after this is too.
		The soak times window after window this way, asking nothing new of the
		memory.
		"""

		self.halt()

		while self.machine.rx_fifo():
			self.machine.get()

		self.count = min(count, len(self.counts))
		self.dma.config(read=_RXF0 + 4 * _CAPTURE_MACHINE, write=self.counts, count=self.count, ctrl=self.control, trigger=True)

	def halt (self) -> None:

		"""Stop taking gaps, keeping those already taken."""

		self.dma.active(0)

	def taken (self) -> int:

		"""Say how many gaps have been captured so far."""

		return self.count - self.dma.count

	def full (self) -> bool:

		"""Say whether every gap asked for has been captured."""

		return self.dma.count == 0

	def close (self) -> None:

		"""Stop capturing and give the hardware back."""

		self.machine.active(0)
		self.dma.close()

	def gaps (self) -> list[int]:

		"""Give each captured gap in system-clock cycles."""

		# Each count is two cycles, and four more pass between one count and the next.
		return [2 * self.counts[index] + 4 for index in range(_SKIP, self.taken())]

	def judge (self, allowed: set, skip: int = _SKIP) -> tuple:

		"""Count the gaps captured after the first `skip`, and give the furthest any is from the nearest length it may be, in cycles.

		It makes no list of them, for a test that has captured thousands and
		has little memory left in one piece.
		"""

		lengths = tuple(allowed)
		taken = self.taken()
		worst = 0

		for index in range(skip, taken):

			cycles = 2 * self.counts[index] + 4
			error = abs(cycles - lengths[0])

			for length in lengths:
				if abs(cycles - length) < error:
					error = abs(cycles - length)

			if error > worst:
				worst = error

		return max(taken - skip, 0), worst

	def drift (self, tempo: int, pulses: int, beats: int, skip: int = _SKIP) -> int:

		"""Say how far the gaps captured after the first `skip`, all together, came from as many exact gaps, in cycles: what `drift` says of a list."""

		total = 0

		for index in range(skip, self.taken()):
			total += 2 * self.counts[index] + 4

		return drift_cycles(total, max(self.taken() - skip, 0), tempo, pulses, beats)


def drift_cycles (total: int, gaps: int, tempo: int, pulses: int, beats: int) -> int:

	"""Say how far `gaps` consecutive gaps adding up to `total` cycles came from as many exact gaps at this tempo and ratio, in cycles, rounded down.

	Each gap alone may be a tick short or long, so judging gaps one at a time
	can't see a scheduler that loses the fraction it carries, which drifts:
	every gap rounded down passes. Consecutive gaps together run from one
	pulse to another, each on the tick at or just before its exact time, so
	they come within a tick of as many exact gaps, give or take the two
	cycles the timing sees an edge to: `_DRIFT_MOST`.
	"""

	scale = pulses * tempo

	return (total * scale - gaps * beats * _UNITS * (SYSTEM_HZ // 1000000)) // scale


def drift (gaps: list, tempo: int, pulses: int, beats: int) -> int:

	"""Say how far a list of consecutive gaps, in cycles, together came from as many exact gaps."""

	return drift_cycles(sum(gaps), len(gaps), tempo, pulses, beats)


def kept_going (taken: int, milliseconds: int, tempo: int, pulses: int, beats: int) -> bool:

	"""Say whether an output played for this long gave close to as many pulses as it should have, so that it didn't stop part way.

	A gap that never ends is never captured, so an output that dies looks
	only like fewer pulses. The capture starts a little before the output and
	the first gaps aren't counted, so a fiftieth and a few are allowed for.
	"""

	expected = milliseconds * tempo * pulses // (6000000 * beats)

	return taken >= expected - expected // 50 - 5


# ---------------------------------------------------------------------------
# Loads: things for the Pico to be busy with on core 0 while pulses are timed.
# Each runs until `finished()` says the capture is over, then describes itself.


def _load_idle (finished) -> str:

	"""Do nothing."""

	while not finished():
		time.sleep_ms(10)

	return "idle"


def _load_gc (finished) -> str:

	"""Fill and collect a large heap, over and over."""

	held = [bytearray(64) for _ in range(1800)]
	runs = 0
	longest = 0

	while not finished():

		litter = [str(number) for number in range(300)]
		began = time.ticks_us()
		gc.collect()
		took = time.ticks_diff(time.ticks_us(), began)

		if took > longest:
			longest = took

		runs += 1

	return "gc: %d collections, longest %.1f ms, %d KB in use" % (runs, longest / 1000, gc.mem_alloc() // 1024)


def _load_flash (finished) -> str:

	"""Rewrite a file the size of the saved settings, as a save does."""

	data = bytes(300)
	writes = 0
	longest = 0

	while not finished():

		began = time.ticks_us()

		with open("/_rig.bin", "wb") as handle:
			handle.write(data)

		took = time.ticks_diff(time.ticks_us(), began)

		if took > longest:
			longest = took

		writes += 1
		time.sleep_ms(150)

	os.remove("/_rig.bin")

	return "flash: %d writes, longest %.1f ms" % (writes, longest / 1000)


def _radio_up ():

	"""Bring up the Pico's own network, and give it with the side of the radio that listens for others.

	A test that has PicoClock's own network up already keeps it: the load
	then runs beside the real thing. The listening side is not switched on.
	It can listen without being, and PicoClock never has it on beside its
	own network.
	"""

	import network

	own = network.WLAN(network.AP_IF)

	if not own.active():
		own.config(ssid="PicoClock rig", password="rig-test-only")
		own.active(True)

	return own, network.WLAN(network.STA_IF)


def _listen (own, scanner) -> bool:

	"""Listen for networks as a page asking for them would, and say whether it was done.

	It is left alone while the Pico's own network is down, which is while a
	test has PicoClock joining a network: no page can reach it to ask then,
	and listening in the middle of a join holds the first core for ten seconds.
	"""

	if not own.active():
		return False

	scanner.scan()

	return True


def _load_wifi (finished) -> str:

	"""Run the Pico's own network and scan for others, back to back."""

	own, scanner = _radio_up()
	scans = 0
	longest = 0

	while not finished():

		began = time.ticks_ms()

		if not _listen(own, scanner):
			time.sleep_ms(20)
			continue

		took = time.ticks_diff(time.ticks_ms(), began)

		if took > longest:
			longest = took

		scans += 1

	return "wifi: own network up, %d scans, longest %d ms" % (scans, longest)


def _load_ble (finished) -> str:

	"""Advertise over Bluetooth."""

	import bluetooth

	radio = bluetooth.BLE()
	radio.active(True)
	radio.gap_advertise(100000, adv_data=b"\x02\x01\x06\x0e\x09PicoClock rig")

	while not finished():
		time.sleep_ms(10)

	radio.gap_advertise(None)

	return "ble: advertising"


def _load_all (finished) -> str:

	"""Own network, Bluetooth advertising, garbage collection and scans, all together."""

	import bluetooth

	own, scanner = _radio_up()
	radio = bluetooth.BLE()
	radio.active(True)
	radio.gap_advertise(100000, adv_data=b"\x02\x01\x06\x0e\x09PicoClock rig")

	held = [bytearray(64) for _ in range(1200)]
	loops = 0

	while not finished():

		litter = [str(number) for number in range(300)]
		gc.collect()

		if loops % 20 == 0:
			_listen(own, scanner)

		loops += 1

	return "all: own network, bluetooth, %d collections with scans, %d KB in use" % (loops, gc.mem_alloc() // 1024)


LOADS = {
	"idle": _load_idle,
	"gc": _load_gc,
	"flash": _load_flash,
	"wifi": _load_wifi,
	"ble": _load_ble,
	"all": _load_all,
}


def run_load (name: str, capture: Capture, seconds: int, meanwhile=None) -> str:

	"""Keep core 0 busy with one load until the capture is full or time runs out.

	`meanwhile`, if given, is called each time the load comes up for air. A
	test uses it to work the clock's controls while the load runs.
	"""

	deadline = time.ticks_add(time.ticks_ms(), seconds * 1000)

	def finished () -> bool:

		"""Say whether the capture is over."""

		if meanwhile is not None:
			meanwhile()

		return capture.full() or time.ticks_diff(deadline, time.ticks_ms()) < 0

	return LOADS[name](finished)


def worst_error (gaps: list[int], allowed: set[int]) -> int:

	"""Give the furthest any gap is from the nearest length it is allowed to be, in cycles.

	A steady output's gaps come in at most two lengths, a tick apart, because
	the exact gap is rarely a whole number of ticks. Those are the allowed
	lengths, worked out from the tempo and the ratio.
	"""

	worst = 0

	for cycles in gaps:

		error = min(abs(cycles - length) for length in allowed)

		if error > worst:
			worst = error

	return worst


def report (name: str, passed: bool, details: str) -> None:

	"""Print one test's result as a line the laptop half reads."""

	print("RIG %s %s %s" % (name, "PASS" if passed else "FAIL", details))


# Where the board's own settings are kept while a test writes over them, so
# that a test cut short leaves them to be put back. It holds nothing when there
# were none.
_KEPT = "/_rig_kept_settings.json"


def keep_settings () -> "str | None":

	"""Read the board's own settings, and keep a copy of them aside while a test writes over them, giving back what they were, or None for none."""

	try:
		with open(picoclock.device.storage.PATH) as handle:
			kept = handle.read()

	except OSError:
		kept = None

	with open(_KEPT, "w") as handle:
		handle.write(kept or "")

	return kept


def put_back_settings (kept: "str | None") -> None:

	"""Put the board's own settings back as they were before a test, or take away a test's own when there were none, and forget the copy."""

	path = picoclock.device.storage.PATH

	try:
		os.remove(path)
	except OSError:
		pass

	if kept:
		with open(path, "w") as handle:
			handle.write(kept)

	try:
		os.remove(_KEPT)
	except OSError:
		pass


def put_back_left_over () -> None:

	"""Put back the settings a test cut short left in keeping, if one did."""

	try:
		with open(_KEPT) as handle:
			kept = handle.read()

	except OSError:
		return

	put_back_settings(kept)


# ---------------------------------------------------------------------------
# Tests.


def test_freerun (load: str = "idle", hz: int = 200, count: int = 2000) -> None:

	"""Time the rig's own reference: a pulse made by PIO alone, which should be within the two cycles the capture sees an edge to.

	This proves the rig rather than PicoClock. If it fails, nothing else the
	rig says can be trusted.
	"""

	gap = SYSTEM_HZ // hz
	capture = Capture(1, count)
	generator = rp2.StateMachine(0, _freerun_program, freq=SYSTEM_HZ, set_base=machine.Pin(0))

	# One turn of the program is 37 cycles plus the count it is given.
	generator.put(gap - 37)
	generator.active(1)

	described = run_load(load, capture, count // hz + 8)

	generator.active(0)

	gaps = capture.gaps()
	capture.close()

	worst = worst_error(gaps, {gap})
	passed = len(gaps) >= count - _SKIP and worst <= 2

	report("freerun", passed, "pulses=%d worst_cycles=%d load=%s" % (len(gaps), worst, described))


# The busiest pattern the research used: four outputs, all fast, at the top tempo.
_DENSE = ((32, 1), (24, 1), (16, 1), (12, 1))


def gap_lengths (tempo: int, pulses: int, beats: int) -> set:

	"""Give the lengths, in cycles, the gaps between an output's pulses take at a steady tempo.

	The exact gap is rarely a whole number of ticks, so the gaps come in two
	lengths a tick apart, the exact gap rounded down and up, from wherever in
	the pattern they are watched: the scheduler's own `gap_ticks`.
	"""

	per_tick = SYSTEM_HZ // 1000000

	return {ticks * per_tick for ticks in picoclock.core.timeline.gap_ticks(tempo, pulses, beats)}


def listen_to_buttons (player) -> None:

	"""Have the second core read the play/pause and stop pins, as it always does in use, so what that costs it is in every timing.

	Nothing is wired to them on the test board, so they read as let go.
	"""

	for name, (pin, low) in picoclock.device.panel.wiring().items():
		player.listen(name, pin, low)


def make_player (ratios, tempo: int):

	"""Make the real player with these ratios at this tempo, reading the buttons, not yet started."""

	player = picoclock.device.player.Player(picoclock.device.board.FIRST_OUTPUT_PIN, len(ratios))
	listen_to_buttons(player)
	player.set_tempo(tempo)

	for output in range(len(ratios)):
		player.set_ratio(output, ratios[output][0], ratios[output][1])

	player.settle()

	return player


# The longest one command may keep the second core while the clock plays, in
# microseconds. A tempo change with four outputs takes about 2,000.
_SLOWEST_COMMAND = 6000

# How long after it is sent a change can take effect on the pins while the
# clock plays, in microseconds, before the words put by to cover the change
# itself. The player is eight words ahead and is playing another, each of them
# up to HOLD_LIMIT ticks; the words put by for a change can run one word past
# what was asked; and the second core may be busy with another command first.
# A change is heard up to about 25 ms after it is sent, by design: the words
# ahead are what keep the player from ever running dry.
_HEARD_WITHIN = (picoclock.device.player._DEPTH + 2) * picoclock.core.timeline.HOLD_LIMIT + _SLOWEST_COMMAND

# How far the rig's own clock and the capture can disagree about when a gap
# was, in microseconds: the gaps are placed from the start's pulse, which comes
# a little after the start was sent.
_PLACING = 3000


def sound (status: dict) -> bool:

	"""Say whether the player's own counts show nothing wrong.

	It never ran dry, the scheduler refused nothing, and no command was held
	up. A command that waited for the first core to collect its garbage takes
	ten times the limit. The words put by can cover for one, so that it makes
	no fault, which is why it is looked for by itself. Nor was its queue ever
	found empty, or down to one word, while the clock played, nor did a flash
	write outlast the words put by for it: near misses that come before a
	fault. `shortfall`, which wants all of those, doesn't ask this.
	"""

	return status["faults"] == 0 and status["refused"] == 0 and status["slowest_command"] <= _SLOWEST_COMMAND and status["empty"] == 0 and status["one_left"] == 0 and status["writes_past_backlog"] == 0


def describe (status: dict) -> str:

	"""Write the player's own counts for a result line."""

	return "faults=%d refused=%d fewest_waiting=%d slowest_command_us=%d merged=%d empty=%d one_left=%d empty_where=%d empty_after=%d longest_write_ms=%d writes_past_backlog=%d" % (status["faults"], status["refused"], status["fewest_waiting"], status["slowest_command"], status["merged"], status["empty"], status["one_left"], status["empty_where"], status["empty_after"], status["longest_write"], status["writes_past_backlog"])


def test_player (load: str = "idle") -> None:

	"""Time the real player's fastest output in the busiest pattern: every gap must be exact."""

	count = 2000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(_DENSE, 30000)

	player.start()

	described = run_load(load, capture, 30)
	status = player.status()
	gaps = capture.gaps()

	player.close()
	capture.close()

	worst = worst_error(gaps, gap_lengths(30000, _DENSE[0][0], _DENSE[0][1]))
	drifted = drift(gaps, 30000, _DENSE[0][0], _DENSE[0][1])
	passed = len(gaps) >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status)

	report("player", passed, "pulses=%d worst_cycles=%d drift_cycles=%d %s load=%s" % (len(gaps), worst, drifted, describe(status), described))


def test_sweep (load: str = "idle") -> None:

	"""Change the tempo many times a second in the busiest pattern: the player must never run dry."""

	count = 2000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(_DENSE, 12000)
	state = [0, time.ticks_ms(), time.ticks_us()]

	# Each change of tempo, as when it was sent in microseconds after the start, and the tempo.
	changes = []

	def meanwhile () -> None:

		"""Set another tempo, at most fifty times a second, and note when."""

		if time.ticks_diff(time.ticks_ms(), state[1]) >= 20:
			state[0] += 1
			state[1] = time.ticks_ms()
			tempo = 6000 + (state[0] * 977) % 24001
			player.set_tempo(tempo)
			changes.append((time.ticks_diff(time.ticks_us(), state[2]), tempo))

	player.start()
	state[2] = time.ticks_us()

	described = run_load(load, capture, 60, meanwhile)
	status = player.status()
	gaps = capture.gaps()
	before = 2 * capture.counts[_SKIP - 1] + 4

	player.close()
	capture.close()

	# A tempo change is covered by words for each output.
	late = _HEARD_WITHIN + picoclock.device.player._COVER_TEMPO + picoclock.device.player._COVER_TEMPO_EACH * len(_DENSE)
	outside = sweep_outside(gaps, before, changes, late, 12000, _DENSE[0][0], _DENSE[0][1])
	passed = len(gaps) >= count - _SKIP and outside == 0 and sound(status)

	report("sweep", passed, "pulses=%d tempo_changes=%d gaps_outside_their_tempos=%d %s load=%s" % (len(gaps), state[0], outside, describe(status), described))


def sweep_outside (gaps: list, before: int, changes: list, late: int, tempo: int, pulses: int, beats: int) -> int:

	"""Count the gaps that aren't as long as some tempo that may have been in force while they lasted gives.

	A gap that spans changes of tempo lasts between what the slowest and the
	fastest of them give, since the scheduler keeps the place a pulse has
	reached through a change. A pulse lost or doubled comes out of that
	range. A change takes effect up to `late` microseconds after it was
	sent, so one sent that long before a gap began may still have been in
	force during it. `before` is the first gap, which places the rest in
	time: together they run from the start's pulse, when the first change
	was timed from.
	"""

	per_us = SYSTEM_HZ // 1000000
	outside = 0
	later = 0
	current = tempo
	began = before

	for cycles in gaps:

		start = began // per_us - late - _PLACING
		end = (began + cycles) // per_us + _PLACING

		while later < len(changes) and changes[later][0] < start:
			current = changes[later][1]
			later += 1

		shortest = picoclock.core.timeline.gap_ticks(current, pulses, beats)[0]
		longest = picoclock.core.timeline.gap_ticks(current, pulses, beats)[1]
		look = later

		while look < len(changes) and changes[look][0] <= end:
			shortest = min(shortest, picoclock.core.timeline.gap_ticks(changes[look][1], pulses, beats)[0])
			longest = max(longest, picoclock.core.timeline.gap_ticks(changes[look][1], pulses, beats)[1])
			look += 1

		if cycles < shortest * per_us - 2 or cycles > longest * per_us + 2:
			outside += 1

		began += cycles

	return outside


# The state machine that times how long the pause test's run output stays low:
# the first of block 1, which nothing else uses meanwhile.
_LOW_TIMER = 4


def _low_program (watched: int):

	"""Build a program that times each stretch a pin spends low, from its fall to its next rise, two cycles to a count. The pin is the jump pin too."""

	def lows ():

		"""Wait for the pin to fall, count until it rises, and give the count."""

		wrap_target()
		wait(1, gpio, watched)
		wait(0, gpio, watched)
		mov(x, invert(null))
		label("low")
		jmp(pin, "risen")
		jmp(x_dec, "low")
		label("risen")
		mov(isr, invert(x))
		push(noblock)
		wrap()

	return rp2.asm_pio(fifo_join=rp2.PIO.JOIN_RX)(lows)


def test_pause (load: str = "idle") -> None:

	"""Pause and carry on a dozen times: no pulse may go missing or appear, every output must keep its place to the tick, and the rest stay exact.

	Output 4 is a run output, low exactly while the clock is paused, so how
	long each pause really lasted is timed on its pin. A pause is heard up to
	25 ms after it is sent, while the words ahead play out, so the times the
	commands were sent can't say that closely.
	"""

	count = 600
	first = picoclock.device.board.FIRST_OUTPUT_PIN
	ratios = ((24, 1), (1, 1), (2, 1), (3, 1))
	capture = Capture(first, count)
	player = make_player(ratios, 12500)

	player.set_role(3, picoclock.core.timeline.RUN)
	player.settle()

	# The run output's pin is only named here, not set up, so the player keeps it.
	timer = rp2.StateMachine(_LOW_TIMER, _low_program(first + 3), freq=SYSTEM_HZ, jmp_pin=machine.Pin(first + 3))
	lows = []

	# How many pauses have been made, when the last change was, whether the
	# clock is paused, and when the pause began, in microseconds. Then how
	# long each pause lasted as its commands were sent.
	state = [0, time.ticks_ms(), False, 0]
	paused = []

	def taken () -> None:

		"""Take how long the run output was low for each pause it has carried on from."""

		# Each count is two cycles, and two more pass between the fall and the first count.
		while timer.rx_fifo():
			lows.append(2 * timer.get() + 2)

	def meanwhile () -> None:

		"""Pause once a second for 150 ms, and stop pausing in time for the last one to finish, noting how long each pause lasted."""

		since = time.ticks_diff(time.ticks_ms(), state[1])

		if state[2] and since >= 150:
			player.resume()
			paused.append(time.ticks_diff(time.ticks_us(), state[3]))
			state[2] = False
			state[1] = time.ticks_ms()

		elif not state[2] and since >= 1000 and state[0] < 12 and capture.taken() < count - 120:
			taken()
			player.pause()
			state[3] = time.ticks_us()
			state[0] += 1
			state[2] = True
			state[1] = time.ticks_ms()

	timer.active(1)
	player.start()

	described = run_load(load, capture, 60, meanwhile)

	if state[2]:
		player.resume()
		paused.append(time.ticks_diff(time.ticks_us(), state[3]))

	# The last carrying on is heard a little after it was sent.
	time.sleep_ms(_HEARD_WITHIN // 1000 + 10)
	taken()

	status = player.status()
	gaps = capture.gaps()

	player.close()
	capture.close()
	timer.active(0)

	# 24 pulses a beat at 125 BPM are 20 ms apart. A gap much longer than that
	# is one that spans a pause, and there must be exactly one for each pause.
	allowed = gap_lengths(12500, 24, 1)
	long_enough = max(allowed) + (SYSTEM_HZ // 1000) * 100
	spans = [cycles for cycles in gaps if cycles > long_enough]
	steady = [cycles for cycles in gaps if cycles <= long_enough]
	worst = worst_error(steady, allowed)

	# Every output keeps its place through a pause to the tick, so the gap a
	# pause falls in is one gap, the pause, and the lead the run output's rise
	# gives the clocks as they carry on.
	per_us = SYSTEM_HZ // 1000000
	lead = picoclock.core.timeline.LEAD_TICKS * per_us
	span_error = max([abs(span - max(allowed) - lows[index] - lead) for index, span in enumerate(spans) if index < len(lows)] or [0])

	# And each pause was heard when it should have been.
	late = _HEARD_WITHIN + picoclock.device.player._COVER_TRANSPORT
	worst_late = max([abs(lows[index] // per_us - paused[index]) for index in range(min(len(lows), len(paused)))] or [0])
	passed = len(gaps) >= count - _SKIP and len(spans) == state[0] == len(paused) == len(lows) and state[0] > 0 and worst <= 2 and span_error <= _DRIFT_MOST and worst_late <= late and sound(status)

	report("pause", passed, "pulses=%d pauses=%d long_gaps=%d timed_pauses=%d worst_cycles=%d worst_span_cycles=%d most_heard_late_us=%d %s load=%s" % (len(gaps), state[0], len(spans), len(lows), worst, span_error, worst_late, describe(status), described))


def test_startstop (load: str = "idle") -> None:

	"""Start and stop over and over: every start must pulse once, and every stop must take every pin low once it is heard, the tempo LED's among them.

	A stop is heard up to about 30 ms after it is sent, while the words ahead
	play out. The pulses last half their gap, 125 ms, and the LED's flash
	100 ms, so all are still high when each stop comes, 40 ms after its
	start, and a stop that left any of them high is still seen once it should
	have been heard. A run the main loop comes back to late may pulse again
	before its stop, and is allowed that.
	"""

	first = picoclock.device.board.FIRST_OUTPUT_PIN
	ratios = ((4, 1), (4, 1), (4, 1), (4, 1))
	tempo = 6000
	capture = Capture(first, 4000)
	player = make_player(ratios, tempo)
	pins = [first + output for output in range(len(ratios))] + [picoclock.device.board.LED_PIN]
	gap_ms = 6000000 // (tempo * ratios[0][0])

	# How long after a stop the pins are looked at, in milliseconds.
	look_ms = (_HEARD_WITHIN + picoclock.device.player._COVER_TRANSPORT) // 1000 + 3

	for output in range(len(ratios)):
		player.set_width(output, 0)

	player.settle()

	# How many starts, how many stops left a pin high, when the last step was,
	# the step due next, and the most edges the runs so far may have made.
	state = [0, 0, time.ticks_ms(), "start", 0]

	def high () -> bool:

		"""Say whether any output's pin, or the tempo LED's, is high."""

		levels = machine.mem32[_GPIO_IN]

		return any((levels >> pin) & 1 for pin in pins)

	def meanwhile () -> None:

		"""Start, stop 40 ms later, look at the pins once the stop should have been heard, and start again 55 ms on."""

		since = time.ticks_diff(time.ticks_ms(), state[2])

		if state[3] == "start" and since >= 55:
			player.start()
			state[0] += 1
			state[3] = "stop"

		elif state[3] == "stop" and since >= 40:
			player.stop()
			state[4] += 1 + (since + look_ms) // gap_ms
			state[3] = "look"

		elif state[3] == "look" and since >= look_ms:

			if high():
				state[1] += 1

			state[3] = "start"

		else:
			return

		state[2] = time.ticks_ms()

	described = run_load(load, capture, 8, meanwhile)

	if state[3] == "stop":
		player.stop()
		state[4] += 1 + (time.ticks_diff(time.ticks_ms(), state[2]) + look_ms) // gap_ms

	time.sleep_ms(look_ms)

	left_high = state[1] + (1 if high() else 0)
	status = player.status()
	edges = capture.taken()

	player.close()
	capture.close()

	# Each start pulses at once, and once more for every gap a run outlasted.
	passed = state[0] > 0 and state[0] <= edges <= state[4] and left_high == 0 and sound(status)

	report("startstop", passed, "starts=%d edges=%d most_edges=%d stops_left_high=%d %s load=%s" % (state[0], edges, state[4], left_high, describe(status), described))


# Where the save tests keep their settings, apart from PicoClock's own file, and
# a path in a folder that isn't there, for a save that must fail.
_SAVE_PATH = "/_rig_settings.json"
_SAVE_NOWHERE = "/_rig_no_such_folder/settings.json"


def test_save (load: str = "idle") -> None:

	"""Save the settings about a hundred times in the busiest pattern: the clock must not notice.

	It leaves its file on the Pico for `saved` to check after a reset, so run
	that next.
	"""

	count = 5000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(_DENSE, 30000)
	settings = picoclock.core.settings.Settings(len(_DENSE))
	storage = picoclock.device.storage.Storage(settings, player, _SAVE_PATH)
	state = [0, time.ticks_ms()]

	def meanwhile () -> None:

		"""Change a setting the clock doesn't play, and save it, a few times a second."""

		if time.ticks_diff(time.ticks_ms(), state[1]) >= 100:
			state[0] += 1
			settings.set_host_name("rig-%d" % state[0])
			storage.save_now()
			state[1] = time.ticks_ms()

	player.start()

	described = run_load(load, capture, 90, meanwhile)
	status = player.status()
	gaps = capture.gaps()

	# A save that fails, here into a folder that isn't there, is due again.
	broken = picoclock.device.storage.Storage(settings, player, _SAVE_NOWHERE)
	settings.set_host_name("rig-nowhere")

	try:
		broken.save_now()
		again = False
	except OSError:
		again = broken._due is not None

	settings.set_host_name("rig-%d" % state[0])

	player.close()
	capture.close()

	with open(_SAVE_PATH) as handle:
		kept = handle.read() == settings.to_text()

	worst = worst_error(gaps, gap_lengths(30000, _DENSE[0][0], _DENSE[0][1]))
	drifted = drift(gaps, 30000, _DENSE[0][0], _DENSE[0][1])
	passed = len(gaps) >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status) and storage.saves > 20 and kept and again

	report("save", passed, "pulses=%d saves=%d worst_cycles=%d drift_cycles=%d kept=%s failed_save_due_again=%s %s load=%s" % (len(gaps), storage.saves, worst, drifted, kept, again, describe(status), described))


def test_saved (load: str = "idle") -> None:

	"""After a reset, read back what `save` left, and remove it."""

	settings = picoclock.core.settings.Settings(len(_DENSE))
	storage = picoclock.device.storage.Storage(settings, None, _SAVE_PATH)

	try:
		os.stat(_SAVE_PATH)
		found = True
	except OSError:
		found = False

	problems = storage.load()
	name = settings.host_name

	storage.forget()

	leftovers = [entry for entry in os.listdir("/") if entry.startswith("_rig")]
	passed = found and problems == [] and name.startswith("rig-") and leftovers == []

	report("saved", passed, "found=%s host_name=%s problems=%d leftovers=%d" % (found, name, len(problems), len(leftovers)))


# Where `stopsave` keeps its settings, and removes them again.
_STOPSAVE_PATH = "/_rig_stopsave.json"

# The longest a start sent as a save ends may take to reach the pins, in
# microseconds. With nothing to wait for, it is the second core's next turn.
_START_AFTER_SAVE = 5000

# How long a tap of play/pause lasts, and how long the stand-in for a write
# lasts around it, in milliseconds: long enough to be believed, and over well
# before the write is.
_TAP = 30
_TAP_WRITE = 80


def test_stopsave (load: str = "idle") -> None:

	"""Save with the clock stopped and paused, and tap play/pause during saves, stopped and playing: no start may wait and no tap may be lost.

	With every pin low, a save needs no backlog. So the queue must never be
	found empty while the clock is stopped or paused, and a start sent the
	moment a save made while stopped has ended must reach the pins at once,
	not after a third of a second of silence. A tap of play/pause through the
	jumper that comes and goes while a write is under way must be carried out
	once the write is over, whether the clock was stopped or playing.
	"""

	transport = picoclock.core.transport
	first = picoclock.device.board.FIRST_OUTPUT_PIN
	player = make_player(((4, 1), (4, 1), (4, 1), (4, 1)), 12000)
	settings = picoclock.core.settings.Settings(4)
	storage = picoclock.device.storage.Storage(settings, player, _STOPSAVE_PATH)
	finger = Finger(True)
	never = Never()

	player.listen(transport.PLAY_PAUSE, _INPUT_PIN, True)

	# The slowest start after a save made while stopped, in microseconds, how
	# many such starts there were, how many saves were made while paused, and
	# whether each tap was carried out: stopped, then playing.
	results = {"slowest_start": 0, "starts": 0, "paused_saves": 0, "tap_stopped": False, "tap_playing": False}

	def doing () -> str:

		"""Say what the player is doing, once it has taken everything it was told."""

		player.settle()
		status = player.status()

		if not status["running"]:
			return transport.STOPPED

		return transport.PAUSED if status["paused"] else transport.RUNNING

	def save () -> None:

		"""Change a setting the clock doesn't play, and save it."""

		settings.set_host_name("rig-%d" % time.ticks_ms())
		storage.save_now()

	def first_edge_us () -> int:

		"""Say how long output 1's pin takes to go high from now, in microseconds, giving up after a second."""

		began = time.ticks_us()

		while not (machine.mem32[_GPIO_IN] >> first) & 1:

			if time.ticks_diff(time.ticks_us(), began) > 1000000:
				break

		return time.ticks_diff(time.ticks_us(), began)

	def tapped_write () -> None:

		"""Stand in for a write that lasts a while, with a tap of play/pause coming and going in the middle of it."""

		finger.press(_TAP)
		time.sleep_ms(_TAP_WRITE)

	def script ():

		"""Work through every step, giving back how long to wait before the next."""

		# Saves with the clock stopped, each followed at once by a start.
		for _ in range(10):

			save()
			player.start()
			took = first_edge_us()

			results["starts"] += 1
			results["slowest_start"] = max(results["slowest_start"], took)

			yield 150
			player.stop()
			yield 150

		# Saves with the clock paused, once its last pulses have played out.
		for _ in range(5):

			player.start()
			yield 300
			player.pause()
			yield 300

			save()
			results["paused_saves"] += 1

			player.resume()
			yield 200
			player.stop()
			yield 150

		# A tap while a write holds the second core, with the clock stopped: it must start.
		player.protect(tapped_write)
		yield 100
		results["tap_stopped"] = doing() == transport.RUNNING

		# And with the clock playing: it must pause, once the write's backlog has played out.
		yield 300
		player.protect(tapped_write)
		yield 500
		results["tap_playing"] = doing() == transport.PAUSED

		player.stop()
		yield 150

	steps = script()
	due = [time.ticks_ms()]

	def meanwhile () -> None:

		"""Take the next step when it is due."""

		if never.over or time.ticks_diff(time.ticks_ms(), due[0]) < 0:
			return

		try:
			due[0] = time.ticks_add(time.ticks_ms(), next(steps))
		except StopIteration:
			never.over = True

	described = run_load(load, never, 60, meanwhile)
	status = player.status()

	storage.forget()

	try:
		os.stat(_STOPSAVE_PATH)
		left = True
	except OSError:
		left = False

	finger.close()
	player.close()
	machine.Pin(_INPUT_PIN, machine.Pin.IN, None)

	quiet = status["empty"] == 0 and status["one_left"] == 0
	passed = never.over and results["starts"] == 10 and results["slowest_start"] <= _START_AFTER_SAVE and results["paused_saves"] == 5 and results["tap_stopped"] and results["tap_playing"] and quiet and not left and sound(status)

	report("stopsave", passed, "starts=%d slowest_start_us=%d paused_saves=%d tap_stopped=%s tap_playing=%s saves=%d left=%s %s load=%s" % (results["starts"], results["slowest_start"], results["paused_saves"], results["tap_stopped"], results["tap_playing"], storage.saves, left, describe(status), described))


# Where `shortfall` writes, and removes again within the same write.
_SHORTFALL_PATH = "/_rig_shortfall.bin"


def test_shortfall (load: str = "idle") -> None:

	"""Give a flash write far too short a backlog, and see the player's counts say so.

	The counts of an empty queue and of writes that outlast their backlog
	read zero in every other test, which is right but shows nothing about
	whether they work. Here the backlog lasts a millisecond and the write
	several tens, so the write must be seen to outlast its backlog.

	A write holds the second core in short pieces, not all at once: with
	outputs that change seldom, the player's eight words last 20 ms and a
	write 70 ms past its backlog emptied nothing. So the outputs are at
	their busiest, where eight words last well under a millisecond and even
	a short hold runs the player dry. The queue must be seen empty and the
	fault counted: it is the one test where a fault is wanted.
	"""

	player = make_player(_DENSE, 30000)
	player.start()
	player.settle()
	time.sleep_ms(300)

	def write () -> None:

		"""Write enough to the flash to take tens of milliseconds, and take it off again."""

		try:
			with open(_SHORTFALL_PATH, "w") as handle:
				handle.write("x" * 16384)
		finally:
			os.remove(_SHORTFALL_PATH)

	player.protect(write, 1000)
	time.sleep_ms(300)

	status = player.status()
	player.close()

	passed = status["writes_past_backlog"] >= 1 and status["empty"] >= 1 and status["empty_where"] in (1, 2) and status["faults"] >= 1 and status["longest_write"] > 20

	report("shortfall", passed, "%s load=%s" % (describe(status), load))


# Four fast outputs at ratios that nearly match. Their edges fall within
# microseconds of each other, which is when the player gets through its words
# quickest.
_CLUSTERED = ((96, 1), (95, 1), (94, 1), (93, 1))


def make_clustered_player (tempo: int):

	"""Make the real player with the clustered pattern and 1 ms pulses, not yet started."""

	player = make_player(_CLUSTERED, tempo)

	for output in range(len(_CLUSTERED)):
		player.set_width(output, 1000)

	player.settle()

	return player


def test_cluster (load: str = "idle") -> None:

	"""Time the fastest output while four outputs' edges crowd together: every gap must be exact.

	Then press stop through the jumper, with the second core as busy as it
	gets: the clock must stop within 50 ms all the same.
	"""

	count = 4000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_clustered_player(30000)
	finger = Finger(True)

	player.listen(picoclock.core.transport.STOP, _INPUT_PIN, True)
	player.start()

	described = run_load(load, capture, 30)
	status = player.status()
	gaps = capture.gaps()

	began = time.ticks_ms()
	finger.press(80)
	time.sleep_ms(300)

	after = player.status()
	took = time.ticks_diff(after["pressed_at"], began) if after["presses"] == status["presses"] + 1 else -1

	finger.close()
	player.close()
	capture.close()
	machine.Pin(_INPUT_PIN, machine.Pin.IN, None)

	worst = worst_error(gaps, gap_lengths(30000, _CLUSTERED[0][0], _CLUSTERED[0][1]))
	drifted = drift(gaps, 30000, _CLUSTERED[0][0], _CLUSTERED[0][1])
	stopped = not after["running"] and 0 <= took <= _HELD_PRESS
	passed = len(gaps) >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status) and stopped

	report("cluster", passed, "pulses=%d worst_cycles=%d drift_cycles=%d stop_ms=%d %s load=%s" % (len(gaps), worst, drifted, took, describe(status), described))


def test_burst (load: str = "idle") -> None:

	"""Send every setting at once, over and over, while four outputs' edges crowd together: the player must never run dry, and output 1 must keep time between bursts.

	A burst is what a factory reset or a power-up sends: the tempo and every
	part of every output, back to back. Each gap clear of every burst must be
	exactly what the last burst's tempo and ratio give. One a burst may have
	been heard during can be shorter, or as long as one gap of the old ratio
	and one of the new: moving onto the new ratio's grid can bring the next
	pulse forward or put it up to a whole new gap away.
	"""

	count = 4000
	ratios = _CLUSTERED
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_clustered_player(30000)

	# How many bursts have been sent, when the last one was, and when the clock
	# was started, in microseconds. Then each burst, as when it began and ended
	# in microseconds after the start, and the tempo and output 1's ratio it set.
	state = [0, time.ticks_ms(), 0]
	bursts = []

	def meanwhile () -> None:

		"""Send a burst ten times a second, keeping the pattern as busy as it began, and note when."""

		if time.ticks_diff(time.ticks_ms(), state[1]) < 100:
			return

		state[0] += 1
		state[1] = time.ticks_ms()
		began = time.ticks_diff(time.ticks_us(), state[2])
		tempo = 29000 + (state[0] * 977) % 1001

		player.set_tempo(tempo)

		for output in range(len(ratios)):
			player.set_ratio(output, ratios[output][0] - state[0] % 3, 1)
			player.set_width(output, 1000 + (state[0] % 2) * 20)
			player.set_role(output, 0)
			player.set_mute(output, False)

		bursts.append((began, time.ticks_diff(time.ticks_us(), state[2]), tempo, ratios[0][0] - state[0] % 3))

	player.start()
	state[2] = time.ticks_us()

	described = run_load(load, capture, 30, meanwhile)
	status = player.status()
	edges = capture.taken()
	gaps = capture.gaps()
	before = 2 * capture.counts[_SKIP - 1] + 4

	player.close()
	capture.close()

	# A burst is covered by words for the tempo with every output, then for each output's four parts.
	cover = picoclock.device.player._COVER_TEMPO + picoclock.device.player._COVER_TEMPO_EACH * len(ratios)
	cover += len(ratios) * (picoclock.device.player._COVER_RATIO + picoclock.device.player._COVER_ROLE + 2 * picoclock.device.player._COVER_SWITCH)
	judged, off, too_long = burst_gaps(gaps, before, bursts, _HEARD_WITHIN + cover, 30000, ratios[0][0])
	passed = edges >= count and state[0] >= 20 and judged >= count // 4 and off == 0 and too_long == 0 and sound(status)

	report("burst", passed, "pulses=%d bursts=%d judged=%d gaps_off=%d gaps_too_long=%d %s load=%s" % (edges, state[0], judged, off, too_long, describe(status), described))


def burst_gaps (gaps: list, before: int, bursts: list, late: int, tempo: int, pulses: int) -> tuple:

	"""Judge output 1's gaps through bursts of changes, and say how many were clear of every burst, how many of those were off, and how many near a burst were too long.

	A burst's changes take effect up to `late` microseconds after it ended.
	`before` is the first gap, which places the rest in time from the
	start's pulse, as in `sweep_outside`. `tempo` and `pulses` are what
	output 1 plays before the first burst, to one beat.
	"""

	per_us = SYSTEM_HZ // 1000000
	judged = 0
	off = 0
	too_long = 0
	heard = 0
	began = before

	# The longest gap any burst can give, at its slowest tempo and its fewest pulses.
	longest = max(gap_lengths(29000, pulses - 2, 1))

	for cycles in gaps:

		start = began // per_us - _PLACING
		end = (began + cycles) // per_us + _PLACING

		# Bursts whose changes were all heard before this gap began.
		while heard < len(bursts) and bursts[heard][1] + late < start:
			tempo = bursts[heard][2]
			pulses = bursts[heard][3]
			heard += 1

		if heard < len(bursts) and bursts[heard][0] <= end:

			if cycles > 2 * longest + 2:
				too_long += 1

		else:

			judged += 1

			if worst_error([cycles], gap_lengths(tempo, pulses, 1)) > 2:
				off += 1

		began += cycles

	return judged, off, too_long


# How long the collect test makes each of the first core's collections last at
# least, in milliseconds: longer than the player's eight words last while its
# outputs are slow, which is 20 ms at most.
_COLLECT_LONGEST = 30


def test_collect (load: str = "idle") -> None:

	"""Change outputs while the first core collects a large heap, a collection straight after each change: the player must never run dry.

	Anything the second core asks of the memory waits for a collection on
	the first core to finish. With PicoClock's page and network loaded, one
	can take longer than the player's words last, so carrying out a command
	must ask for nothing. Here the heap is filled, beside whatever the load
	holds, until a collection takes longer than the words last, and output 2
	is timed throughout.
	"""

	count = 600
	tempo = 12000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN + 1, count)

	# Output 2 at 16 pulses a second, the rest slow, so each word holds the pins as long as a word can.
	player = make_player(((1, 1), (8, 1), (1, 1), (1, 1)), tempo)

	# Small pieces, added a few at a time until a collection takes long enough.
	held = []

	ratios = ((3, 2), (2, 1), (1, 4), (5, 1), (4, 3), (1, 2), (3, 1), (7, 1))
	widths = (5000, 8000, 12000, 20000, 35000, 50000)

	# How many changes have been sent, and the longest collection after one, in milliseconds.
	state = [0, 0]

	def meanwhile () -> None:

		"""Change output 3's ratio or output 4's pulse, collect the heap at once, and make the heap larger if that was quick."""

		step = state[0]

		if step % 2:
			player.set_width(3, widths[(step // 2) % len(widths)])
		else:
			pulses, beats = ratios[(step // 2) % len(ratios)]
			player.set_ratio(2, pulses, beats)

		state[0] += 1

		began = time.ticks_ms()
		gc.collect()
		took = time.ticks_diff(time.ticks_ms(), began)

		if took > state[1]:
			state[1] = took

		# Room is left for the load's own garbage.
		if took < _COLLECT_LONGEST and gc.mem_free() > 60000:
			held.append([(number,) for number in range(200)])

	player.start()

	described = run_load(load, capture, count // 16 + 10, meanwhile)
	status = player.status()
	pulses, worst = capture.judge(gap_lengths(tempo, 8, 1))
	drifted = capture.drift(tempo, 8, 1)

	player.close()
	capture.close()
	held = None

	passed = pulses >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and state[1] >= _COLLECT_LONGEST and sound(status)

	report("collect", passed, "pulses=%d worst_cycles=%d drift_cycles=%d changes=%d longest_collect_ms=%d %s load=%s" % (pulses, worst, drifted, state[0], state[1], describe(status), described))


# Where the position test keeps its settings.
_POSITION_PATH = "/_rig_position.json"


def test_position (load: str = "idle") -> None:

	"""Ask the player where the clock has got to while it runs and saves, and hold each answer to the Pico's own clock.

	An answer may be withheld, as it is while a save's backlog plays. One that
	is given must be within a tenth of a beat of where the time says the clock
	is: the scheduler works about 20 ms ahead of the pins, which is a twentieth
	of a beat here.
	"""

	tempo = 15000
	ratios = ((24, 1), (1, 1), (4, 1), (3, 2))
	count = 500
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(ratios, tempo)
	settings = picoclock.core.settings.Settings(len(ratios))
	storage = picoclock.device.storage.Storage(settings, player, _POSITION_PATH)

	# How many times it was asked, how many answers were withheld, the worst
	# answer in thousandths of a beat, how many saves, and when it last asked and saved.
	state = [0, 0, 0, 0, time.ticks_ms(), time.ticks_ms()]

	player.start()
	player.settle()

	started = time.ticks_us()

	def meanwhile () -> None:

		"""Ask fifty times a second, and save now and then so that a backlog plays."""

		now = time.ticks_ms()

		if time.ticks_diff(now, state[4]) >= 20:

			state[4] = now
			elapsed = time.ticks_diff(time.ticks_us(), started)
			position = player.status()["position"]
			state[0] += 1

			if position < 0:
				state[1] += 1

			else:

				# A beat is 6,000,000,000 / tempo microseconds, and the position is in thousandths.
				error = abs(position - elapsed * tempo // 6000000)

				if error > state[2]:
					state[2] = error

		if time.ticks_diff(now, state[5]) >= 700:

			state[5] = now
			state[3] += 1
			settings.set_host_name("rig-%d" % state[3])
			storage.save_now()

	described = run_load(load, capture, 30, meanwhile)
	status = player.status()
	gaps = capture.gaps()

	storage.forget()
	player.close()
	capture.close()

	worst = worst_error(gaps, gap_lengths(tempo, ratios[0][0], ratios[0][1]))
	drifted = drift(gaps, tempo, ratios[0][0], ratios[0][1])
	answered = state[0] - state[1]
	passed = len(gaps) >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status)
	# Under the heaviest load the main loop comes up for air less often, so fewer
	# are asked. A save every 700 ms with a backlog of 300 ms withholds close to
	# half the answers, so a quarter answered is asked for, not a half.
	passed = passed and state[0] >= 50 and answered >= state[0] // 4 and state[1] >= 1 and state[2] <= 100 and state[3] >= 3

	report("position", passed, "asked=%d withheld=%d worst_thousandths=%d saves=%d pulses=%d worst_cycles=%d drift_cycles=%d %s load=%s" % (state[0], state[1], state[2], state[3], len(gaps), worst, drifted, describe(status), described))


# Where the inputs test keeps its settings.
_INPUTS_PATH = "/_rig_inputs.json"

# How long the first core is held up for while a stop is pressed, as a poor
# WiFi signal can hold it, and the longest the stop may then take to happen, in
# milliseconds: twenty for the press to be believed, and a little to act on it.
_HELD_FOR = 10000
_HELD_PRESS = 50

# The jumper: GP0 plays a finger on a button, and GP1 is the input the button is on.
_FINGER_PIN = 0
_INPUT_PIN = 1

# Where every pin's level can be read at once, whoever is driving it.
_GPIO_IN = 0xD0000004


class Finger:

	"""Presses a button by driving GP0, and lets go on time whatever the main loop is doing."""

	def __init__ (self, low: bool) -> None:

		"""Begin not pressing. `low` is whether pressing pulls the pin low."""

		self._pressed = 0 if low else 1
		self._pin = machine.Pin(_FINGER_PIN, machine.Pin.OUT, value=1 - self._pressed)
		self._timer = machine.Timer()
		self._let_go = self._release

	def _release (self, timer) -> None:

		"""Let go. A timer's interrupt runs this, so that it happens even while the main loop is held up."""

		self._pin.value(1 - self._pressed)

	def press (self, milliseconds: int) -> None:

		"""Press now, and let go after so long."""

		self._pin.value(self._pressed)
		self._timer.init(mode=machine.Timer.ONE_SHOT, period=milliseconds, callback=self._let_go, hard=True)

	def close (self) -> None:

		"""Stop driving the pin."""

		self._timer.deinit()
		self._pin.init(machine.Pin.IN, None)


class Light:

	"""The Pico's own light, counting how many times it comes on."""

	def __init__ (self) -> None:

		"""Begin with the light out."""

		self._pin = machine.Pin("LED", machine.Pin.OUT)
		self._lit = 0
		self.flashes = 0

	def value (self, lit: int) -> None:

		"""Put the light on or out."""

		if lit and not self._lit:
			self.flashes += 1

		self._lit = lit
		self._pin.value(lit)

	def is_on (self) -> bool:

		"""Say whether the light is on."""

		return bool(self._lit)


class TempoLed:

	"""The tempo LED, counting how often the first core lights it to warn of a reset, and checking its pin does as it is told."""

	def __init__ (self) -> None:

		"""Take hold of the LED as PicoClock does, leaving it to the player."""

		self._led = picoclock.device.led.Led()
		self._lit = False
		self.taken = False
		self.flashes = 0
		self.wrong = 0

	def show (self, lit) -> None:

		"""Light the LED, put it out or give it back to the player, as the panel asks, and see that the pin did so."""

		self._led.show(lit)

		if lit and not self._lit:
			self.flashes += 1

		self._lit = lit is True
		self.taken = lit is not None

		if self.taken and self.is_on() != lit:
			self.wrong += 1

	def is_on (self) -> bool:

		"""Say whether the LED's pin is high, read from the pins themselves."""

		return (machine.mem32[_GPIO_IN] >> picoclock.device.board.LED_PIN) & 1 == 1

	def close (self) -> None:

		"""Give the LED back to the player."""

		self._led.close()


class Never:

	"""Stands in for a capture in a test that times no pulses: it is full when the test says it is over."""

	def __init__ (self) -> None:

		"""Begin with the test still going."""

		self.over = False

	def full (self) -> bool:

		"""Say whether the test is over."""

		return self.over


def test_inputs (load: str = "idle") -> None:

	"""Press each button through the jumper, pressed low and then pressed high, and see the clock do as it was told.

	Play/pause must start a stopped clock, pause a running one and carry a
	paused one on from where it was. A knock too short to be a press must do
	nothing. A press must work when it is seen as it happens, and when it
	comes and goes while the main loop is held up. A stop pressed while the
	first core is held up for ten seconds, as a poor WiFi signal can hold it,
	must stop the clock within 50 ms all the same, and the clock must hear of
	it once the hold is over. Stop held must stop the clock at once, change
	nothing for a second's hold, and put every setting back after five
	seconds, once, leaving the clock stopped. Both lights must stay as they
	are for the first two seconds of the hold, flash fast from then until
	five, stay lit until the button is let go, and then be the clock's again.
	The light must come on once a beat while the clock runs.
	"""

	transport = picoclock.core.transport
	player = picoclock.device.player.Player(picoclock.device.board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	storage = picoclock.device.storage.Storage(settings, player, _INPUTS_PATH)
	clock = picoclock.core.clock.Clock(settings, player, storage)
	light = Light()
	shown = TempoLed()
	never = Never()

	clock.load()
	player.settle()

	# The panel in use, what went wrong, how many presses were made, how many
	# times the light came on in two seconds of running and in a reset's hold,
	# the longest and shortest hold that a reset took, the longest a stop took
	# while the first core was held up, in milliseconds, and how many times
	# the tempo LED came on in a reset's hold.
	panel = [None]
	wrong = []
	counts = [0, 0, 0, 0, 99999, -1, 0]

	def use (name: str, low: bool) -> None:

		"""Put one button on the input pin, in place of whichever was there."""

		if panel[0] is not None:
			panel[0].close()

		panel[0] = picoclock.device.panel.Panel(clock, player, {name: (_INPUT_PIN, low)}, light, shown)

	def expect (what: str, truth: bool) -> None:

		"""Note something that should be so and isn't."""

		if not truth:
			wrong.append(what)

	def doing () -> str:

		"""Say what the player is doing, once it has taken everything it was told."""

		player.settle()
		status = player.status()

		if not status["running"]:
			return transport.STOPPED

		return transport.PAUSED if status["paused"] else transport.RUNNING

	def saved () -> bool:

		"""Say whether there are settings saved on the Pico."""

		try:
			os.stat(_INPUTS_PATH)
			return True
		except OSError:
			return False

	def script ():

		"""Work through every press, giving back how long to wait before the next step."""

		for low in (True, False):

			way = "pressed low" if low else "pressed high"
			finger = Finger(low)

			use(transport.PLAY_PAUSE, low)
			yield 100

			finger.press(8)
			yield 200
			expect("a knock started the clock, " + way, clock.transport.state == transport.STOPPED)

			finger.press(80)
			counts[0] += 1
			yield 300
			expect("play/pause didn't start the clock, " + way, clock.transport.state == transport.RUNNING and doing() == transport.RUNNING)

			# Two seconds of running at 120 BPM is four beats.
			before = light.flashes
			yield 2000
			counts[1] = light.flashes - before

			finger.press(80)
			counts[0] += 1
			yield 300
			expect("play/pause didn't pause the clock, " + way, clock.transport.state == transport.PAUSED and doing() == transport.PAUSED)
			held = player.timeline.beat

			finger.press(80)
			counts[0] += 1
			yield 300
			expect("play/pause didn't carry the clock on, " + way, clock.transport.state == transport.RUNNING and doing() == transport.RUNNING)
			expect("play/pause began again at zero, " + way, held >= 4 and player.timeline.beat >= held)

			use(transport.STOP, low)
			yield 100
			finger.press(80)
			counts[0] += 1
			yield 300
			expect("stop didn't stop the clock, " + way, clock.transport.state == transport.STOPPED and doing() == transport.STOPPED)

			# A press that comes and goes while the main loop is held up, and then a knock that does.
			use(transport.PLAY_PAUSE, low)
			yield 100
			finger.press(60)
			counts[0] += 1
			time.sleep_ms(300)
			yield 200
			expect("a press made while busy was lost, " + way, clock.transport.state == transport.RUNNING)

			use(transport.STOP, low)
			yield 100
			finger.press(8)
			time.sleep_ms(300)
			yield 200
			expect("a knock made while busy stopped the clock, " + way, clock.transport.state == transport.RUNNING)

			# Stop held for a second stops the clock at once and puts nothing back.
			clock.set_tempo(7700)
			clock.flush()
			storage.save_now()
			yield 100
			expect("nothing was saved to begin with, " + way, saved())

			finger.press(1000)
			counts[0] += 1
			yield 300
			expect("stop held for a second didn't stop the clock at once, " + way, clock.transport.state == transport.STOPPED and doing() == transport.STOPPED)

			# Counted from once the first core has heard of the stop, so that a
			# beat's flash in the moment before it heard isn't taken for a warning.
			before = (light.flashes, shown.flashes)
			yield 1100
			expect("a second's hold of stop changed the settings, " + way, settings.tempo == 7700 and saved())
			expect("a second's hold of stop warned of a reset, " + way, (light.flashes, shown.flashes) == before)

			# Held for eight seconds from running: it stops the clock at once,
			# warns from two seconds, puts every setting back at five, once,
			# and leaves the clock stopped.
			clock.press(transport.START)
			yield 300
			# Held to eight seconds, so that each look at the lights comes well
			# away from letting go, even when a scan for networks keeps the main
			# loop from looking for over half a second.
			began = time.ticks_ms()
			finger.press(8000)
			counts[0] += 1
			yield 300
			expect("stop held didn't stop the clock at once, " + way, clock.transport.state == transport.STOPPED and doing() == transport.STOPPED)
			before = (light.flashes, shown.flashes)

			while time.ticks_diff(time.ticks_ms(), began) < 1900:
				yield 5

			expect("the lights warned of a reset before two seconds, " + way, (light.flashes, shown.flashes) == before)

			while settings.tempo == 7700 and time.ticks_diff(time.ticks_ms(), began) < 6500:
				yield 5

			took = time.ticks_diff(time.ticks_ms(), began)
			counts[2] = light.flashes - before[0]
			counts[6] = shown.flashes - before[1]
			counts[3] = max(counts[3], took)
			counts[4] = min(counts[4], took)

			expect("holding stop didn't put the settings back, " + way, settings.tempo == 12000 and not saved())
			expect("reset went off before five seconds, " + way, took >= 5000)
			expect("the clock started again after a reset, " + way, clock.transport.state == transport.STOPPED and doing() == transport.STOPPED)

			# Still held, it doesn't go off again, and both lights stay lit.
			clock.set_tempo(8800)
			clock.flush()

			while time.ticks_diff(time.ticks_ms(), began) < 6800:
				yield 50

			expect("reset went off twice in one hold, " + way, settings.tempo == 8800)
			expect("the lights weren't lit once the settings were back, " + way, light.is_on() and shown.is_on())

			# Let go, the tempo LED is given back to the player, and flashes on the beat once the clock runs.
			while time.ticks_diff(time.ticks_ms(), began) < 9000:
				yield 50

			expect("the lights stayed lit after stop was let go, " + way, not light.is_on() and not shown.is_on() and not shown.taken)

			clock.press(transport.START)
			flashed = False

			for _ in range(150):
				flashed = flashed or shown.is_on()
				yield 5

			expect("the tempo LED didn't flash on the beat after a reset, " + way, flashed)

			# Stop pressed with the first core held up, and nothing given a turn until the hold is over.
			before = player.status()["presses"]
			began = time.ticks_ms()
			finger.press(80)
			counts[0] += 1

			while time.ticks_diff(time.ticks_ms(), began) < _HELD_FOR:
				pass

			status = player.status()
			took = time.ticks_diff(status["pressed_at"], began)
			counts[5] = max(counts[5], took)

			expect("a stop pressed while held up wasn't carried out, " + way, status["presses"] == before + 1 and not status["running"])
			expect("a stop pressed while held up took over 50 ms, " + way, 0 <= took <= _HELD_PRESS)
			yield 100
			expect("the clock didn't hear of a stop pressed while held up, " + way, clock.transport.state == transport.STOPPED)

			clock.press(transport.STOP)
			panel[0].close()
			panel[0] = None
			finger.close()
			yield 100

	steps = script()
	due = [time.ticks_ms()]

	def meanwhile () -> None:

		"""Look at the button as the main loop would, and take the next step when it is due."""

		if panel[0] is not None:
			panel[0].look()

		clock.catch_up()
		clock.flush()

		if never.over or time.ticks_diff(time.ticks_ms(), due[0]) < 0:
			return

		try:
			due[0] = time.ticks_add(time.ticks_ms(), next(steps))
		except StopIteration:
			never.over = True

	described = run_load(load, never, 120, meanwhile)
	status = player.status()

	if panel[0] is not None:
		panel[0].close()

	storage.forget()
	shown.close()
	player.close()

	machine.Pin(_FINGER_PIN, machine.Pin.IN, None)
	machine.Pin(_INPUT_PIN, machine.Pin.IN, None)

	# With nothing else to do, the buttons and the light are looked at often
	# enough to count the light's flashes and to time the reset closely.
	steady = load != "idle" or (3 <= counts[1] <= 5 and 20 <= counts[2] <= 30 and 20 <= counts[6] <= 30 and counts[3] <= 5100)
	passed = never.over and not wrong and counts[0] == 16 and shown.wrong == 0 and steady and sound(status)

	report("inputs", passed, "presses=%d wrong=%d beat_flashes=%d hold_flashes=%d,%d led_wrong=%d reset_after_ms=%d..%d stop_while_held_ms=%d first_wrong=%s %s load=%s" % (counts[0], len(wrong), counts[1], counts[2], counts[6], shown.wrong, counts[4], counts[3], counts[5], wrong[0].replace(" ", "_") if wrong else "none", describe(status), described))


# How late each flash of the tempo LED may begin after output 1's pulse on the
# same beat, and how far its length may be from what it should be, in cycles.
# The player sets both pins with one instruction, so they rise together: what
# is left is the few cycles the timer takes to see a pin and act on it.
_LED_LATE = 10
_LED_SLACK = 20

# The state machine that counts the LED's flashes: the first of block 1, free
# now that the LED has no program of its own.
_FLASH_COUNTER = 4

# What the timer gives for a pulse of output 1 that no flash began with.
_NO_FLASH = 0xFFFFFFFF


def _flash_program (watched: int):

	"""Build a program that looks, at each rise of output 1, for a flash of the tempo LED beginning with it, and times the flash.

	For a rise with a flash it gives how many of its 32 looks were left when
	the LED was seen lit, two cycles to a look, and then how long the LED
	stayed lit, two cycles to a count. For a rise with none it gives
	`_NO_FLASH`. The LED's pin is the jump pin.
	"""

	def flashes ():

		"""At each rise of output 1, look for the LED to light with it, and time how long it stays lit."""

		wrap_target()
		label("top")
		wait(0, gpio, watched)
		wait(1, gpio, watched)

		set(x, 31)
		label("dark")
		jmp(pin, "lit")
		jmp(x_dec, "dark")

		mov(isr, invert(null))
		push(noblock)
		jmp("top")

		label("lit")
		mov(isr, x)
		push(noblock)

		mov(x, invert(null))
		label("on")
		jmp(x_dec, "still")
		label("still")
		jmp(pin, "on")
		mov(isr, invert(x))
		push(noblock)
		wrap()

	return rp2.asm_pio(fifo_join=rp2.PIO.JOIN_RX)(flashes)


def _count_program (watched: int):

	"""Build a program that gives one word for each time a pin rises: here, each flash of the tempo LED."""

	def rises ():

		"""Give a word each time the pin rises."""

		wrap_target()
		wait(0, gpio, watched)
		wait(1, gpio, watched)
		push(noblock)
		wrap()

	return rp2.asm_pio(fifo_join=rp2.PIO.JOIN_RX)(rises)


def test_led (load: str = "idle") -> None:

	"""Time the tempo LED against the beat, at one and at four pulses a beat, through a tempo change and a long pulse, and with output 1 switched off.

	At one pulse a beat every pulse of output 1 is a beat, and each must have
	a flash beginning on the same cycle. At four, only every fourth one may.
	Each flash lasts a quarter of a beat and at most 100 ms, whatever output
	1's pulse is. With output 1 off there is nothing to time against, so the
	flashes are counted instead: they go on, once a beat.
	"""

	board = picoclock.device.board
	cycles = SYSTEM_HZ // 1000000
	player = picoclock.device.player.Player(board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	clock = picoclock.core.clock.Clock(settings, player)
	never = Never()

	clock.load()

	# The LED's pin is only named here, not set up, so the player keeps it.
	timer = rp2.StateMachine(_CAPTURE_MACHINE, _flash_program(board.FIRST_OUTPUT_PIN), freq=SYSTEM_HZ, jmp_pin=machine.Pin(board.LED_PIN))
	counter = rp2.StateMachine(_FLASH_COUNTER, _count_program(board.LED_PIN), freq=SYSTEM_HZ)
	panel = picoclock.device.panel.Panel(clock, player, {}, Light(), picoclock.device.led.Led())

	timer.active(1)
	counter.active(1)
	clock.press(picoclock.core.transport.START)

	# Each stage: the clock's pulses a beat, the tempo, output 1's pulse in
	# milliseconds, whether output 1 is on, how long to watch for in
	# milliseconds, and how long each flash should be lit for in cycles.
	stages = (
		(1, 30000, 10, True, 2400, 50000 * cycles),
		(1, 30000, 80, True, 2400, 50000 * cycles),
		(1, 6000, 10, True, 7500, 100000 * cycles),
		(4, 12000, 10, True, 4000, 100000 * cycles),
		(4, 12000, 10, False, 4000, 100000 * cycles),
	)

	# Per stage: the pulses of output 1 with a flash, those without, the latest
	# start and the furthest length of those flashes, and the flashes counted.
	# A stage counts from its second pulse, as one may already have begun.
	seen = [[0, 0, 0, 0, 0] for _ in stages]
	stage = [0]
	waiting = [-1]
	ready = [False]
	skipped = [False]

	# When each stage began to be judged and when it ended, in milliseconds: under load a stage runs long.
	began = [0] * len(stages)
	ended = [0] * len(stages)

	def take () -> None:

		"""Take every count the timer and the counter have given."""

		flashed = 0

		while counter.rx_fifo():
			counter.get()
			flashed += 1

		if ready[0]:
			seen[stage[0]][4] += flashed

		while timer.rx_fifo():

			count = timer.get()

			# A start is 31 looks at most and a length is millions, so a pair split by a full queue is never mistaken.
			if count == _NO_FLASH:

				if ready[0] and skipped[0]:
					seen[stage[0]][1] += 1

				skipped[0] = True
				waiting[0] = -1
				continue

			if count < 32:
				waiting[0] = count
				continue

			if waiting[0] < 0:
				continue

			late = 2 * (31 - waiting[0])
			lit = 2 * count
			waiting[0] = -1

			if not ready[0]:
				continue

			if not skipped[0]:
				skipped[0] = True
				continue

			counts = seen[stage[0]]
			counts[0] += 1
			counts[2] = max(counts[2], late)
			counts[3] = max(counts[3], abs(lit - stages[stage[0]][5]))

	def script ():

		"""Set each stage up as the page would, and watch it for its time."""

		for index in range(len(stages)):

			ppqn, tempo, pulse, on, watch, lit = stages[index]

			take()

			if index:
				ended[index - 1] = time.ticks_ms()

			stage[0] = index
			ready[0] = False
			skipped[0] = False
			clock.set_ppqn(ppqn)
			clock.set_tempo(tempo)
			clock.set_output(0, "pulse", pulse)
			clock.set_output(0, "muted", not on)
			clock.flush()
			player.settle()
			take()
			ready[0] = True
			began[index] = time.ticks_ms()
			yield watch

		take()
		ended[-1] = time.ticks_ms()
		never.over = True
		yield 0

	steps = script()
	due = [time.ticks_ms()]

	def meanwhile () -> None:

		"""Look at the panel as the main loop would, take what the timer has, and move on when a stage is over."""

		panel.look()
		clock.flush()
		take()

		if never.over or time.ticks_diff(time.ticks_ms(), due[0]) < 0:
			return

		due[0] = time.ticks_add(time.ticks_ms(), next(steps))

	described = run_load(load, never, 60, meanwhile)
	status = player.status()

	take()
	timer.active(0)
	counter.active(0)
	panel.close()
	player.close()

	timed = [counts[0] for counts in seen]
	missed = [counts[1] for counts in seen]
	late = max(counts[2] for counts in seen[:4])
	worst = max(counts[3] for counts in seen[:4])
	counted = [counts[4] for counts in seen]

	# At one pulse a beat no pulse goes without a flash: two seconds at 300 BPM is
	# ten beats, and seven and a half at 60 is seven. At four, three in four do,
	# and in four seconds at 120 BPM there are eight beats. Off, output 1 gives the
	# timer nothing, and the LED flashes once a beat for as long as the stage
	# really lasted, which a load can stretch.
	beats_off = time.ticks_diff(ended[4], began[4]) * stages[4][1] // 6000000
	every_beat = timed[0] >= 5 and timed[1] >= 5 and timed[2] >= 3 and missed[0] + missed[1] + missed[2] == 0
	one_in_four = timed[3] >= 5 and 3 * timed[3] - 3 <= missed[3] <= 3 * timed[3] + 3
	when_off = timed[4] == 0 and beats_off >= 6 and abs(counted[4] - beats_off) <= 1
	passed = never.over and every_beat and one_in_four and when_off and late <= _LED_LATE and worst <= _LED_SLACK and sound(status)

	report("led", passed, "flashes=%d,%d,%d,%d pulses_without=%d,%d,%d,%d late_cycles=%d worst_cycles=%d flashes_when_off=%d/%d %s load=%s" % (timed[0], timed[1], timed[2], timed[3], missed[0], missed[1], missed[2], missed[3], late, worst, counted[4], beats_off, describe(status), described))


def test_powerup (load: str = "idle") -> None:

	"""Start the clock as switching on does, from missing, damaged and good settings, with the board's own settings put back whatever happens."""

	kept = keep_settings()

	try:
		_powerup_cases(load)

	finally:
		put_back_settings(kept)


def _powerup_cases (load: str) -> None:

	"""Start the clock as power-up does, from no saved settings, from a damaged file and from a good one, and then everything that follows it.

	Each start must leave the clock running and pulsing: at the saved tempo
	when the file is good, and as a new PicoClock otherwise.

	What follows the clock is the buttons, the saving, the Pico's own network
	and the page. With all of it going, the network must be up under
	PicoClock's own name, and the page's port and the port for the names a
	phone looks up answered for. Stopped, the network must be off and both
	ports let go, with the clock still playing. That must hold when it is
	interrupted from the Pico's prompt too, which is how it is stopped in
	real life. And if the network and the page can't be made at all, the
	rest must carry on without them.

	`test_powerup` puts back whatever settings the Pico had saved, whatever happens.
	"""

	import network
	import socket

	import picoclock.web.portal

	path = picoclock.device.storage.PATH
	first = machine.Pin(picoclock.device.board.FIRST_OUTPUT_PIN)
	wrong = []
	slowest = 0

	good = picoclock.core.settings.Settings(picoclock.device.board.OUTPUTS)
	good.set_tempo(24000)
	good.set_ppqn(2)

	# What is saved, the tempo and the pulses a beat the clock should come up at, and whether something should be said about the file.
	cases = (
		("no file", None, 12000, 4, False),
		("a file cut short", good.to_text()[:40], 12000, 4, True),
		("a file of rubbish", "\x00\xff not settings at all {{{", 12000, 4, True),
		("a good file", good.to_text(), 24000, 2, False),
	)

	for name, text, tempo, ppqn, told in cases:

		try:
			os.remove(path)
		except OSError:
			pass

		if text is not None:
			with open(path, "w") as handle:
				handle.write(text)

		began = time.ticks_ms()
		parts = picoclock.device.start.begin()
		took = time.ticks_diff(time.ticks_ms(), began)
		slowest = max(slowest, took)

		# Count output 1's pulses for a second: eight at 120 BPM and four pulses a beat, and eight at 240 and two.
		pulses = 0
		was = first.value()
		deadline = time.ticks_add(time.ticks_ms(), 1000)

		while time.ticks_diff(deadline, time.ticks_ms()) > 0:

			now = first.value()

			if now and not was:
				pulses += 1

			was = now

		status = parts.player.status()
		expected = tempo * ppqn // 6000

		if not (status["running"] and not status["paused"] and sound(status)):
			wrong.append(name + ": the clock isn't running cleanly")

		if parts.clock.settings.tempo != tempo or parts.player.timeline.tempo != tempo:
			wrong.append(name + ": the tempo is wrong")

		if parts.clock.settings.ppqn != ppqn:
			wrong.append(name + ": the pulses a beat are wrong")

		if not expected - 1 <= pulses <= expected + 1:
			wrong.append(name + ": %d pulses in a second" % pulses)

		if bool(parts.problems) != told:
			wrong.append(name + ": said %d things about the file" % len(parts.problems))

		parts.player.close()

	start = picoclock.device.start
	own = network.WLAN(network.AP_IF)
	identity = machine.unique_id()
	called = "PicoClock %02X%02X%02X" % (identity[-3], identity[-2], identity[-1])

	def answered () -> bool:

		"""Say whether something is listening where the page is served.

		Asking for the port outright says nothing about that. It is refused
		for as long as the network stack still holds a connection that has
		finished, which after a soft reset it does for good, so a page used
		a moment before this runs would pass for a server left on. This asks
		to listen there alongside whatever else is willing to share the
		port, which is refused only when something else is listening.
		"""

		probe = socket.socket()

		try:
			probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			probe.bind(("0.0.0.0", start.PAGE_PORT))
			probe.listen(1)
			return False

		except OSError:
			return True

		finally:
			probe.close()

	def answering () -> bool:

		"""Say whether something is answering the names a phone looks up, by asking for that port as anything else would, which is refused only while it is taken."""

		probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

		try:
			probe.bind(("0.0.0.0", picoclock.web.portal.PORT))
			return False

		except OSError:
			return True

		finally:
			probe.close()

	# Everything that follows the clock, left going for a few seconds. The radio is given a
	# name of the rig's first, so that one left over from an earlier run can't pass for PicoClock's.
	own.config(ssid="PICO-not-ours")

	parts = start.begin()
	asyncio.run(start.carry_on(parts, 2500))
	stood = start.reached(parts)

	if len(parts.jobs) != 7 or parts.panel is None:
		wrong.append("the rest: %d jobs were started" % len(parts.jobs))

	if not stood.startswith("page=1 ") or not answered():
		wrong.append("the rest: the page isn't being served")

	if not answering():
		wrong.append("the rest: nothing answers the names a phone looks up")

	if " address=192.168.4.1 " not in stood or not stood.endswith(" own=" + called) or not own.active() or own.config("ssid") != called:
		wrong.append("the rest: the network isn't up under its own name")

	start.stop(parts)
	status = parts.player.status()
	free = int(parts.stood.split(" free=")[1].split()[0]) if " free=" in parts.stood else 0

	if not parts.stood.startswith("page=1 "):
		wrong.append("stopped: no note was left of how things stood")

	if own.active() or answered() or answering():
		wrong.append("stopped: the network (%d), the page's port (%d) or the names' port (%d) was left on" % (own.active(), answered(), answering()))

	if not (status["running"] and sound(status)):
		wrong.append("stopped: the clock didn't play on cleanly")

	parts.player.close()

	# The same again, started as `main.py` starts it and interrupted as the Pico's prompt
	# interrupts it: from inside one of its own jobs, a second and a half in.
	reach = start.reach

	async def interrupt () -> None:

		"""Interrupt PicoClock the way Ctrl-C does."""

		await asyncio.sleep_ms(1500)

		raise KeyboardInterrupt()

	start.reach = lambda parts, static=None: reach(parts, static) + [interrupt]
	start.RUNNING[0] = None

	try:
		start.run()
		wrong.append("interrupted: it stopped by itself")

	except KeyboardInterrupt:
		pass

	except Exception as problem:
		wrong.append("interrupted: stopping failed with %r" % (problem,))

	finally:
		start.reach = reach

	parts = start.RUNNING[0]

	if parts is None or not parts.stood.startswith("page=1 "):
		wrong.append("interrupted: no note was left of how things stood")

	if own.active() or answered() or answering():
		wrong.append("interrupted: the network (%d), the page's port (%d) or the names' port (%d) was left on" % (own.active(), answered(), answering()))

	if parts is not None:

		status = parts.player.status()

		if not (status["running"] and sound(status)):
			wrong.append("interrupted: the clock didn't play on cleanly")

		parts.player.close()

	# And again with the network and the page failing to be made.
	parts = start.begin()

	def broken (parts, static=None) -> list:

		"""Fail as a missing or damaged part of the page's code would."""

		raise RuntimeError("the rig broke this on purpose")

	start.reach = broken

	try:
		asyncio.run(start.carry_on(parts, 500))

	except Exception:
		wrong.append("no page: everything else stopped with it")

	finally:
		start.reach = reach

	if len(parts.jobs) != 3 or not start.reached(parts).startswith("page=0 "):
		wrong.append("no page: %d jobs were started" % len(parts.jobs))

	start.stop(parts)
	status = parts.player.status()

	if not (status["running"] and sound(status)):
		wrong.append("no page: the clock didn't play on cleanly")

	parts.player.close()

	report("powerup", not wrong, "cases=%d wrong=%d slowest_start_ms=%d free_kb=%d first_wrong=%s load=%s" % (len(cases), len(wrong), slowest, free // 1024, wrong[0].replace(" ", "_") if wrong else "none", load))


# Where the network test keeps its settings.
_NETWORK_PATH = "/_rig_network.json"


def test_network (load: str = "idle") -> None:

	"""Work the radio as PicoClock does, while the clock's fastest output is timed.

	The Pico's own network must come up under PicoClock's own name and no
	other, at 192.168.4.1. A new password must start it again, half a second
	after it was asked for and not before, and so must putting every setting
	back. A change that leaves the password alone must not. Looking for other
	networks must give a tidy list.

	Asked to join a network that isn't there, it must switch its own network
	off, try more than once, and bring its own back with the reason, keeping
	nothing of the network. Started with such a network saved, it must try
	that first, fall back the same way, and keep it. Asked then for another
	that isn't there, it must fall back again and save the first one again.
	The two networks must never be up together. At the end everything must
	be off.

	Joining a network that is there needs a network, and a phone joining the
	Pico's needs a phone. Neither is something this can do: the laptop the
	rig runs from has no WiFi.
	"""

	import network

	import picoclock.core.wifi
	import picoclock.device.radio

	# Room for a minute of pulses, so that everything the radio does falls inside what is timed.
	count = 9600
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(_DENSE, 30000)
	settings = picoclock.core.settings.Settings(len(_DENSE))
	storage = picoclock.device.storage.Storage(settings, player, _NETWORK_PATH)

	# The clock is never asked to give the player its settings, so the player keeps the busy pattern it was made with.
	clock = picoclock.core.clock.Clock(settings, player, storage)
	own = network.WLAN(network.AP_IF)
	other = network.WLAN(network.STA_IF)
	never = Never()

	identity = machine.unique_id()
	name = "PicoClock %02X%02X%02X" % (identity[-3], identity[-2], identity[-1])
	missing = "no such network 4f1c"
	also_missing = "no such network 9d2e"
	wrong = []

	# A join is tried for six seconds here, where PicoClock allows twenty, so that the test is over sooner.
	radio = picoclock.device.radio.Radio()
	wifi = [picoclock.core.wifi.Wifi(clock, radio, name, 6000)]

	# When the own network was last asked to start, how many times it has been, when it was
	# last up again, and how many times a join has been begun.
	started = [0, 0, 0, 0]
	start = radio.own_up
	join = radio.join

	def own_up (*details) -> None:

		"""Start the own network as the radio does, and note when."""

		started[0] = time.ticks_ms()
		started[1] += 1
		start(*details)
		started[2] = time.ticks_ms()

	def joining (*details) -> None:

		"""Begin a join as the radio does, and count it."""

		started[3] += 1
		join(*details)

	radio.own_up = own_up
	radio.join = joining

	def expect (what: str, truth: bool) -> None:

		"""Note something that should be so and isn't."""

		if not truth:
			wrong.append(what)

	def standing (what: str, problem: str = "") -> None:

		"""See that the own network is up under PicoClock's own name, where the page is expected, and the other side is off."""

		expect(what + ": the own network isn't up", own.active())
		expect(what + ": the own network has the wrong name", own.config("ssid") == name)
		expect(what + ": the own network isn't secured with WPA2", own.config("security") == 0x00400004)
		expect(what + ": the page isn't at 192.168.4.1", own.ipconfig("addr4")[0] == "192.168.4.1")
		expect(what + ": the joining side was left on", not other.active())
		expect(what + ": the radio says otherwise", wifi[0].status() == {"mode": "own", "name": name, "address": "192.168.4.1", "problem": problem, "own": name, "found_at": ""})

		# Nothing is in range of the board to join its own network, so the count of those that have is nought.
		expect(what + ": the radio counts phones on the own network that aren't there", wifi[0].radio.stations() == 0)

	# The longest a change took to be in force, how many networks were in range, how long looking
	# for them took, and the longest a join that failed took to bring the own network back.
	counts = [0, 0, 0, 0]

	def restarted (what: str, changed: int, before: int) -> None:

		"""See that one change started the own network again once, half a second or more after it was made."""

		waited = time.ticks_diff(started[0], changed)
		in_force = time.ticks_diff(started[2], changed)

		expect(what + ": the own network started %d times" % (started[1] - before), started[1] == before + 1)
		expect(what + ": it started again after only %d ms" % waited, started[1] != before + 1 or waited >= 500)

		if started[1] == before + 1 and in_force > counts[0]:
			counts[0] = in_force

	def away (what: str, joining: str = missing) -> None:

		"""See that the own network is off and a join of a missing network is under way."""

		expect(what + ": the own network is still up", not own.active())
		expect(what + ": the joining side isn't on", other.active())
		expect(what + ": the radio says otherwise", wifi[0].status() == {"mode": "joining", "name": joining, "address": "", "problem": "", "own": name, "found_at": ""})

	def script ():

		"""Say what to do next, and how long to wait before the step after it, in milliseconds."""

		before = started[1]
		changed = time.ticks_ms()
		clock.set_own_password("a new password")
		yield 2500
		restarted("a new password", changed, before)
		standing("a new password")
		expect("a new password: the radio is running with the old one", wifi[0]._own_password == "a new password")

		before = started[1]
		clock.set_host_name("rig-name")
		yield 2500
		expect("a new name: the own network started again", started[1] == before)
		standing("a new name")

		began = time.ticks_ms()
		found = wifi[0].scan()
		counts[2] = time.ticks_diff(time.ticks_ms(), began)
		counts[1] = len(found)
		names = [each["name"] for each in found]
		strengths = [each["strength"] for each in found]

		expect("looking: a network is listed twice", len(set(names)) == len(names))
		expect("looking: a network with no name is listed", "" not in names)
		expect("looking: the list isn't strongest first", strengths == sorted(strengths, reverse=True))
		odd = [strength for strength in strengths if not -120 <= strength < 0]

		# The radio has once reported a strength that couldn't be one, and what it was is worth knowing.
		expect("looking: a strength isn't in dBm: %s" % odd, not odd)
		expect("looking: a network lacks its details", all(sorted(each) == ["name", "secure", "strength"] for each in found))
		standing("looking")
		yield 100

		before = started[1]
		changed = time.ticks_ms()
		clock.reset()
		yield 2500
		restarted("a reset", changed, before)
		standing("a reset")
		expect("a reset: the password isn't back as new", wifi[0]._own_password == picoclock.core.settings.DEFAULT_PASSWORD)
		expect("a reset: the Pico doesn't answer to its name as new", network.hostname() == picoclock.core.settings.DEFAULT_HOST_NAME)

		before = started[1]
		clock.reset()
		yield 2500
		expect("a second reset: the own network started again", started[1] == before)
		standing("a second reset")

		# A join asked for from the page, of a network that isn't there.
		before = started[1]
		tries = started[3]
		changed = time.ticks_ms()
		clock.set_network(missing, "not a password")
		yield 2500
		away("a join")
		expect("a join: the own network came back early", started[1] == before)
		yield 6500
		expect("a join: it was tried %d times" % (started[3] - tries), started[3] - tries >= 2)
		expect("a join: the own network started %d times" % (started[1] - before), started[1] == before + 1)
		standing("a join", "There was no network called %s in range" % missing)
		expect("a join: the network that never worked was kept", settings.network_name == "")
		counts[3] = time.ticks_diff(started[2], changed)

		# The same network, saved from before, at power-up.
		wifi[0].close()
		expect("switched off: the own network is still up", not own.active())

		settings.set_network(missing, "not a password")
		wifi[0] = picoclock.core.wifi.Wifi(clock, radio, name, 6000)
		before = started[1]
		tries = started[3]

		# The first look comes at once, as at power-up. Left for later, the radio would be
		# wholly off meanwhile, and a load that listens for networks would have nothing to listen with.
		wifi[0].look(0)
		yield 2500
		away("a saved network")
		expect("a saved network: the own network came up first", started[1] == before)
		yield 6500
		expect("a saved network: it was tried %d times" % (started[3] - tries), started[3] - tries >= 2)
		standing("a saved network", "There was no network called %s in range" % missing)
		expect("a saved network: it was forgotten", settings.network_name == missing)

		# Another network that isn't there, asked for from the page while that one is saved.
		before = started[1]
		tries = started[3]
		clock.set_network(also_missing, "not a password either")
		yield 2500
		away("another join", also_missing)
		yield 6500
		expect("another join: it was tried %d times" % (started[3] - tries), started[3] - tries >= 2)
		expect("another join: the own network started %d times" % (started[1] - before), started[1] == before + 1)
		standing("another join", "There was no network called %s in range" % also_missing)
		expect("another join: the network saved before it wasn't saved again", settings.network_name == missing and settings.network_password == "not a password")

	steps = script()
	due = [time.ticks_ms()]
	looked = [time.ticks_ms()]

	def meanwhile () -> None:

		"""Look as the radio's own job would, and take the next step when it is due."""

		now = time.ticks_ms()
		wifi[0].look(time.ticks_diff(now, looked[0]))
		looked[0] = now

		# Whatever the radio is doing, its own network and the joining side are never both on.
		if own.active() and other.active():
			expect("the two networks were up together", False)

		if never.over or time.ticks_diff(time.ticks_ms(), due[0]) < 0:
			return

		try:
			due[0] = time.ticks_add(time.ticks_ms(), next(steps))
		except StopIteration:
			never.over = True

	player.start()
	playing_from = time.ticks_ms()

	# Names of the rig's own, so that ones left over from an earlier run can't pass for PicoClock's.
	# The radio keeps the last network name it was given until the Pico loses power.
	settings.set_host_name("rig-first")
	own.config(ssid="PICO-not-ours")

	began = time.ticks_ms()
	wifi[0].look(0)
	took = time.ticks_diff(time.ticks_ms(), began)

	standing("at first")
	expect("at first: the Pico answers to the wrong name", network.hostname() == "rig-first")

	described = run_load(load, never, 90, meanwhile)
	status = player.status()
	taken = capture.taken()
	played_for = time.ticks_diff(time.ticks_ms(), playing_from)
	gaps = capture.gaps()

	wifi[0].close()

	expect("at the end: the own network is still up", not own.active())
	expect("at the end: the joining side is still on", not other.active())
	expect("at the end: the radio still gives an address", wifi[0].status()["address"] == "")

	# With the radio off there is nothing to look with, which the page must be told in words.
	try:
		wifi[0].scan()
		expect("at the end: looking worked with the radio off", False)

	except ValueError as problem:
		expect("at the end: looking failed with nothing fit to show", len(str(problem)) > 20)

	except Exception:
		expect("at the end: looking failed in a way that would drop the page", False)

	storage.forget()
	player.close()
	capture.close()

	worst = worst_error(gaps, gap_lengths(30000, _DENSE[0][0], _DENSE[0][1]))
	drifted = drift(gaps, 30000, _DENSE[0][0], _DENSE[0][1])
	going = kept_going(taken, played_for, 30000, _DENSE[0][0], _DENSE[0][1]) and status["running"]

	# The own network starts at first, for the new password, for the reset,
	# and after each of the three joins that fail.
	passed = never.over and not wrong and started[1] == 6 and taken < count and going and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status)

	report("network", passed, "starts=%d first_start_ms=%d change_in_force_ms=%d found=%d look_ms=%d joins=%d back_after_ms=%d wrong=%d first_wrong=%s pulses=%d played_ms=%d worst_cycles=%d drift_cycles=%d %s load=%s" % (started[1], took, counts[0], counts[1], counts[2], started[3], counts[3], len(wrong), wrong[0].replace(" ", "_") if wrong else "none", len(gaps), played_for, worst, drifted, describe(status), described))


# Where the page test keeps its settings, and the page's files while it runs.
_PAGE_PATH = "/_rig_page.json"
_PAGE_FOLDER = "/_rig_page"

# What a browser sends with a request from PicoClock's own page.
_OWN_SITE = {"Origin": "http://192.168.4.1", "Host": "192.168.4.1"}


def page_files () -> list:

	"""Give the page's files as an install leaves them on the Pico, which is the page alone, with everything inside it."""

	return sorted(name[:-3] for name in os.listdir("picoclock/web/static") if name.endswith(".gz"))


def copy_page_file (name: str) -> int:

	"""Copy one of the page's gzipped files onto the Pico's own storage, where it is read from in use, and give its length.

	It goes a kilobyte at a time: the page is one file of about 18 KB, and
	under the heavier loads there may be no room for it in one piece.
	"""

	length = 0

	with open("picoclock/web/static/" + name + ".gz", "rb") as source:
		with open(_PAGE_FOLDER + "/" + name + ".gz", "wb") as target:

			while True:

				part = source.read(1024)

				if not part:
					return length

				target.write(part)
				length += len(part)


async def _count_body (response, sent) -> None:

	"""Stand in for the test client's own reading of a body: count a gzipped page's bytes as they come, and keep anything else.

	The test client keeps a body by adding each part to the last, which asks
	for ever longer pieces of memory, and under load there may be none for
	the page. The real server sends it a kilobyte at a time and keeps none
	of it.
	"""

	response.body = b""
	response.length = 0
	parts = sent.body_iter()
	whole = sent.headers.get("Content-Encoding") != "gzip"

	async for part in parts:

		response.length += len(part)

		if whole:
			response.body += part.encode() if isinstance(part, str) else part

	if hasattr(parts, "aclose"):
		await parts.aclose()


class Visitor:

	"""Stands in for a page that stays open: it answers each batch it is sent, as a page does, and counts the messages by type."""

	def __init__ (self) -> None:

		"""Begin open, having been sent nothing."""

		self.counts = {}
		self.batches = 0

		# How many batches didn't carry the number that follows the one before.
		self.misnumbered = 0

		# Set by the app if it gives up on this page.
		self.closed = False

		self._gone = False
		self._answers = []
		self._wake = asyncio.Event()

	async def receive (self) -> str:

		"""Give the page's next answer, waiting for one until the page is closed."""

		while not self._answers:

			if self._gone:
				raise OSError(32, "the page has closed")

			self._wake.clear()
			await self._wake.wait()

		return self._answers.pop(0)

	async def send (self, data) -> None:

		"""Count the messages in one batch from the app, and say it was heard."""

		batch = json.loads(data)

		self.batches += 1

		if batch["batch"] != self.batches:
			self.misnumbered += 1

		for message in batch["messages"]:
			self.counts[message["type"]] = self.counts.get(message["type"], 0) + 1

		self._answers.append(json.dumps({"do": "heard", "batch": batch["batch"]}))
		self._wake.set()

	def close (self) -> None:

		"""Close the page."""

		self._gone = True
		self._wake.set()


def test_page (load: str = "idle") -> None:

	"""Serve the page and its API as the Pico does, and ask for them over and over, while a fast output is timed.

	Every request a phone makes is made here: the page, which is one file read
	from the Pico's own storage, everything a new page needs, changes sent as JSON
	and through a socket, ones that must be refused, and a look for networks.
	A page that stays open must be told of each change and of where the clock
	has got to. The Pico's own network is up throughout and settings are
	saved as they change, as in real use.

	Nothing here goes over the air, because the laptop the rig runs from has
	no WiFi. Each request is handed straight to the server, the way Microdot's
	own tests do it, so everything behind the radio is tested and the radio
	carrying it is not.
	"""

	import microdot.test_client

	import picoclock.device.radio
	import picoclock.web.app

	board = picoclock.device.board
	tempo = 30000
	count = 3000
	names = page_files()
	sizes = {}

	# The page's files go onto the Pico's own storage, which is where it reads them from in use.
	try:
		os.mkdir(_PAGE_FOLDER)
	except OSError:
		pass

	for name in names:
		sizes[name] = copy_page_file(name)

	# Output 2 is the one timed, and nothing below changes it. The changes go to outputs 3 and 4.
	capture = Capture(board.FIRST_OUTPUT_PIN + 1, count)
	player = picoclock.device.player.Player(board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	storage = picoclock.device.storage.Storage(settings, player, _PAGE_PATH)
	clock = picoclock.core.clock.Clock(settings, player, storage)

	# At one pulse a beat, so output 2 at ×24 sends the 24 a beat this has always timed.
	clock.set_ppqn(1)
	clock.set_tempo(tempo)
	clock.set_output(1, "ratio", "24")
	clock.load()
	clock.press(picoclock.core.transport.START)
	player.settle()

	wifi = picoclock.device.radio.make(clock)
	wifi.look(0)

	app = picoclock.web.app.App(clock, wifi, _PAGE_FOLDER)
	client = microdot.test_client.TestClient(app.web)

	# The page is counted as it comes, not kept, as a phone's browser would take it in.
	microdot.test_client.TestResponse._initialize_body = _count_body
	visitor = Visitor()
	wrong = []

	# An install leaves the page as one file, with everything inside it, so a phone asks for nothing else.
	if names != ["index.html"]:
		wrong.append("the page isn't one file: " + ",".join(names))

	# How many requests have been made, how many rounds of them, the longest one took in
	# milliseconds, the memory left after the first round, how many networks the look
	# found, how long it took, and the memory left after the latest round.
	counts = [0, 0, 0, 0, -1, 0, 0]
	ratios = ("3/2", "2", "1/4", "5")

	def busy () -> bool:

		"""Say whether this round makes changes. Rounds in one second of every four do, so that settings are then left alone for longer than a save waits."""

		return time.ticks_ms() // 1000 % 4 == 0

	def expect (what: str, truth: bool) -> None:

		"""Note something that should be so and isn't."""

		if not truth:
			wrong.append(what)

	async def fetch (name: str) -> None:

		"""Ask for one of the page's files."""

		response = await client.get("/" if name == "index.html" else "/" + name, headers={"Host": "192.168.4.1"})

		expect(name + " wasn't sent whole and gzipped", response.status_code == 200 and response.headers.get("Content-Encoding") == "gzip" and response.length == sizes[name])

	async def everything () -> None:

		"""Ask for all a new page needs."""

		response = await client.get("/api/state", headers={"Host": "192.168.4.1"})

		expect("a new page wasn't given everything", response.status_code == 200 and len(response.json["now"]) == 9 and response.json["fixed"]["roles"] == list(picoclock.core.settings.ROLES))

	async def change () -> None:

		"""Change output 3's ratio as a page without a socket does."""

		if not busy():
			return

		ratio = ratios[counts[1] % len(ratios)]
		response = await client.post("/api/change", headers=dict(_OWN_SITE), body={"do": "output", "index": 2, "name": "ratio", "value": ratio})

		expect("a change wasn't carried out", response.status_code == 200 and response.json == {"ok": True} and settings.outputs[2].ratio == picoclock.core.ratio.parse(ratio, settings.ppqn))

	async def refuse () -> None:

		"""Send a change that makes no sense, one from another site's page, and ask for a file that isn't the page's."""

		response = await client.post("/api/change", headers=dict(_OWN_SITE), body={"do": "output", "index": 2, "name": "ratio", "value": "nonsense"})
		expect("nonsense wasn't refused", response.status_code == 400 and "error" in response.json)

		response = await client.post("/api/change", headers={"Origin": "http://elsewhere.example", "Host": "192.168.4.1"}, body={"do": "stop"})
		expect("another site's page wasn't refused", response.status_code == 403 and clock.transport.state == picoclock.core.transport.RUNNING)

		# A site that has pointed its own name at the Pico gets nothing of it, nor the network's name.
		response = await client.post("/api/change", headers={"Origin": "http://clock-stopper.example", "Host": "clock-stopper.example"}, body={"do": "stop"})
		expect("a page sent to another site's name wasn't refused", response.status_code in (302, 403) and clock.transport.state == picoclock.core.transport.RUNNING)

		response = await client.get("/api/state", headers={"Host": "clock-stopper.example"})
		expect("the state was given to a page sent to another site's name", response.status_code in (302, 403))

		# By its own name, with .local, it is itself.
		response = await client.get("/api/state", headers={"Host": settings.host_name + ".local"})
		expect("the Pico didn't answer to its own name", response.status_code == 200)

		response = await client.get("/settings.json", headers={"Host": "192.168.4.1"})
		expect("a file that isn't the page's was sent", response.status_code == 404)

		# A phone's check for the internet, asked of the Pico's own network, is sent to the page.
		response = await client.get("/generate_204", headers={"Host": "connectivitycheck.gstatic.com"})
		expect("a phone's check for the internet wasn't sent to the page", response.status_code == 302 and response.headers.get("Location") == "http://192.168.4.1/")

	async def socket () -> None:

		"""Open a socket through the server, change output 4 over it, and close."""

		if not busy():
			return

		pulse = 5 + counts[1] % 20

		def page ():

			"""Change output 4's pulse and snap, one after the other, as a page would."""

			yield json.dumps({"do": "output", "index": 3, "name": "pulse", "value": pulse, "id": counts[1]})
			yield json.dumps({"do": "output", "index": 3, "name": "snap", "value": counts[1] % 2 == 0})

		await client.websocket("/api/socket", page, headers=dict(_OWN_SITE))

		expect("a change over the socket wasn't carried out", settings.outputs[3].pulse == pulse and settings.outputs[3].snap == (counts[1] % 2 == 0))

	async def look () -> None:

		"""Ask which networks are in range, once: it keeps the first core for most of a second."""

		if counts[4] >= 0:
			return

		began = time.ticks_ms()
		response = await client.post("/api/change", headers=dict(_OWN_SITE), body={"do": "scan"})
		counts[5] = time.ticks_diff(time.ticks_ms(), began)

		expect("the networks in range weren't listed", response.status_code == 200 and isinstance(response.json["found"], list))
		counts[4] = len(response.json.get("found", ())) if response.status_code == 200 else 0

	requests = [lambda name=name: fetch(name) for name in names] + [everything, change, refuse, socket, look]

	async def one () -> None:

		"""Make the next request, and give everything else that is due its turn."""

		request = requests[counts[0] % len(requests)]
		began = time.ticks_ms()

		await request()
		await asyncio.sleep_ms(0)

		took = time.ticks_diff(time.ticks_ms(), began)
		counts[0] += 1

		# Looking for networks is slow by nature, and is timed by itself.
		if took > counts[2] and request is not look:
			counts[2] = took

		if counts[0] % len(requests) == 0:

			counts[1] += 1

			# What is left once each round's garbage is gone. The first is kept to hold the last against.
			gc.collect()
			counts[6] = gc.mem_free()

			if counts[1] == 1:
				counts[3] = counts[6]

	jobs = [asyncio.create_task(job()) for job in (clock.run, storage.run, app.run, lambda: picoclock.device.radio.run(wifi), lambda: app.serve(visitor))]

	def meanwhile () -> None:

		"""Make one request each time the load comes up for air."""

		asyncio.run(one())

	described = run_load(load, capture, 60, meanwhile)
	status = player.status()

	# Judged where they lie: with the page's server loaded, a list of them may not find room in one piece.
	timed, worst = capture.judge(gap_lengths(tempo, 24, 1))
	drifted = capture.drift(tempo, 24, 1)

	async def finish () -> None:

		"""Close the open page and end the jobs, giving each a turn to notice."""

		visitor.close()
		await asyncio.sleep_ms(100)

		for job in jobs:
			job.cancel()

		await asyncio.sleep_ms(50)

	asyncio.run(finish())

	expect("the open page wasn't forgotten when it closed", app.pages == [])
	expect("the open page was given up on, though it answered every batch", app.given_up == 0 and not visitor.closed)
	expect("the open page's batches weren't numbered from 1, each one more than the last", visitor.batches >= 3 and not visitor.misnumbered)
	expect("something was still taken to be on its way to a page that had gone", app.health()["waiting"] == 0)

	wifi.close()
	storage.forget()
	player.close()
	capture.close()

	for name in names:
		os.remove(_PAGE_FOLDER + "/" + name + ".gz")

	os.rmdir(_PAGE_FOLDER)

	told = visitor.counts
	heard = told.get("hello", 0) == 1 and told.get("output", 0) >= 3 and told.get("clock", 0) >= 5

	# Memory that went down by more than a few KB between the first round and the last would be a page or a request never let go of.
	kept = counts[3] - counts[6] <= 8192
	passed = timed >= count - _SKIP and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status) and not wrong and counts[1] >= 3 and heard and kept and storage.saves >= 1

	report("page", passed, "requests=%d rounds=%d slowest_ms=%d look_ms=%d found=%d told=%d/%d/%d saves=%d free_kb=%d..%d wrong=%d first_wrong=%s pulses=%d worst_cycles=%d drift_cycles=%d %s load=%s" % (counts[0], counts[1], counts[2], counts[5], counts[4], told.get("hello", 0), told.get("output", 0), told.get("clock", 0), storage.saves, counts[3] // 1024, counts[6] // 1024, len(wrong), wrong[0].replace(" ", "_") if wrong else "none", timed, worst, drifted, describe(status), described))


# How long the air test runs PicoClock for, in milliseconds, and how many of
# its first pulses aren't judged: the ones from before its tempo was set.
_AIR_FOR = 45000
_AIR_SKIP = 40


def test_air (load: str = "idle") -> None:

	"""Run the whole of PicoClock on the WiFi network it has saved, with its page being used hard from that network, and time a fast output.

	This is the measurement the design is for: real requests arriving over
	the air, the first core answering them and saving settings, and every
	pulse still exact. The laptop half of the rig does the asking, with
	`tools/hammer.py`, for as long as this runs. It changes outputs 3 and 4
	only, so output 2 pulses steadily and can be held to the cycle.

	It needs a Pico that has joined a network before, and so has one saved.
	The saved settings are put back afterwards.
	"""

	import picoclock.core.wifi
	import picoclock.web.app

	start = picoclock.device.start
	board = picoclock.device.board
	path = picoclock.device.storage.PATH
	tempo = 30000
	names = page_files()

	try:
		with open(path) as handle:
			kept = handle.read()
	except OSError:
		kept = None

	# The page's files go onto the Pico's own storage, which is where it reads them from in use.
	try:
		os.mkdir(_PAGE_FOLDER)
	except OSError:
		pass

	for name in names:
		copy_page_file(name)

	count = _AIR_FOR // 1000 * 130
	capture = Capture(board.FIRST_OUTPUT_PIN + 1, count)
	parts = start.begin()
	saved = bool(parts.clock.settings.network_name)

	try:

		# Output 2 is the one timed: 120 pulses a second, at one pulse a beat and ×24.
		parts.clock.set_ppqn(1)
		parts.clock.set_tempo(tempo)
		parts.clock.set_output(1, "ratio", "24")

		playing_from = time.ticks_ms()
		asyncio.run(start.carry_on(parts, _AIR_FOR, _PAGE_FOLDER))
		played_for = time.ticks_diff(time.ticks_ms(), playing_from)

		status = parts.player.status()
		pulses, worst = capture.judge(gap_lengths(tempo, 24, 1), _AIR_SKIP)
		drifted = capture.drift(tempo, 24, 1, _AIR_SKIP)
		going = kept_going(pulses + _AIR_SKIP, played_for, tempo, 24, 1) and status["running"]
		joined = parts.wifi is not None and parts.wifi.mode == picoclock.core.wifi.JOINED
		opened = parts.app._opened if parts.app is not None else 0
		kept_up = parts.app.health() if parts.app is not None else {}
		saves = parts.storage.saves

	finally:

		# Whatever went wrong, the Pico is left as it was found: stopped, with the settings it had, the network among them.
		start.stop(parts)
		parts.player.close()
		capture.close()

		for name in names:

			try:
				os.remove(_PAGE_FOLDER + "/" + name + ".gz")
			except OSError:
				pass

		try:
			os.rmdir(_PAGE_FOLDER)
		except OSError:
			pass

		if kept is not None:
			with open(path, "w") as handle:
				handle.write(kept)

		else:

			# There were none to begin with, so the test's own aren't left behind.
			try:
				os.remove(path)
			except OSError:
				pass

	free = int(parts.stood.split(" free=")[1].split()[0]) if " free=" in parts.stood else 0
	passed = saved and joined and opened >= 4 and going and worst <= 2 and abs(drifted) <= _DRIFT_MOST and sound(status)

	# How often and for how long everything but the clock was held up for over a second, the longest of those, and the pages given up on.
	report("air", passed, "saved=%d joined=%d pages_opened=%d saves=%d free_kb=%d holds=%d held_ms=%d longest_hold_ms=%d given_up=%d pulses=%d played_ms=%d worst_cycles=%d drift_cycles=%d %s" % (saved, joined, opened, saves, free // 1024, kept_up.get("holds", -1), kept_up.get("held", -1), kept_up.get("longest_hold", -1), kept_up.get("given_up", -1), pulses, played_for, worst, drifted, describe(status)))


# ---------------------------------------------------------------------------
# The soak: the whole of PicoClock for hours, timed in windows.

# How long each window times output 2 for, in milliseconds, and the most gaps
# one holds: a minute at the fastest the soak sets output 2, 120 pulses a second.
_SOAK_WINDOW = 60000
_SOAK_MOST = 7300

# How long the tempo, the transport and output 2 must have been left alone
# before a window begins: this many milliseconds, and at least this many of
# output 2's gaps, so that no gap a window judges began before a change.
_SOAK_QUIET = 3000
_SOAK_QUIET_GAPS = 3

# The pause after each window, in milliseconds, which is when the laptop half
# makes its changes, and how far past its time a window may run before it is
# taken to have lost pulses.
_SOAK_BETWEEN = 5000
_SOAK_LATE = 2000

# How long the soak goes without printing before it says it is still there, in
# milliseconds, and where it keeps the settings it found until it puts them back,
# in case it is cut short.
_SOAK_ALIVE = 60000
_SOAK_KEPT = "/_rig_soak_kept.json"


class Settled:

	"""Hears every change to the tempo, the PPQN, the transport or output 2, which a window timing output 2 can't span."""

	def __init__ (self, clock) -> None:

		"""Begin listening to the clock."""

		self.changes = 0
		self.at = time.ticks_ms()

		clock.watch(self.heard)

	def heard (self, what, by) -> None:

		"""Count a change, if it is one that moves output 2's pulses."""

		if what == picoclock.core.clock.TEMPO or what == picoclock.core.clock.PPQN or what == picoclock.core.clock.TRANSPORT or what == picoclock.core.clock.EVERYTHING or what == 1:
			self.changes += 1
			self.at = time.ticks_ms()

	def since (self) -> int:

		"""Say how long since the last change, in milliseconds."""

		return time.ticks_diff(time.ticks_ms(), self.at)


def soak_lengths (tempo: int, pulses: int, beats: int) -> tuple:

	"""Give the two lengths output 2's gaps may be, in cycles: the exact gap in ticks, rounded down and up, as `gap_lengths` gives them, shorter first."""

	per_tick = SYSTEM_HZ // 1000000
	shorter, longer = picoclock.core.timeline.gap_ticks(tempo, pulses, beats)

	return (shorter * per_tick, longer * per_tick)


async def soak_judge (capture: Capture, lengths: tuple) -> tuple:

	"""Count a window's gaps after the first few, and give the furthest any is from a length it may be and what they add up to, in cycles, giving the other jobs a turn as it goes."""

	taken = capture.taken()
	shorter, longer = lengths
	worst = 0
	total = 0

	for index in range(_SKIP, taken):

		cycles = 2 * capture.counts[index] + 4
		error = min(abs(cycles - shorter), abs(cycles - longer))
		total += cycles

		if error > worst:
			worst = error

		if index % 500 == 0:
			await asyncio.sleep_ms(0)

	return max(taken - _SKIP, 0), worst, total


def soak_playing (parts) -> bool:

	"""Say whether output 2 is pulsing as a clock: the transport running, and output 2 not muted."""

	output = parts.clock.settings.outputs[1]

	return parts.clock.transport.state == picoclock.core.transport.RUNNING and not output.muted and output.role == picoclock.core.settings.ROLES[0]


async def soak_window (parts, capture: Capture, settled: Settled, counts: dict) -> bool:

	"""Time output 2 for one window, once it has been left alone long enough, and print a line saying how it went. Say whether a window was timed.

	A change to the tempo, the transport or output 2 during the window spoils
	it, and it is counted as spoiled rather than judged.
	"""

	settings = parts.clock.settings
	tempo = settings.tempo
	pulses, beats = picoclock.core.ratio.effective(settings.outputs[1].ratio, settings.ppqn)
	gap_ms = 6000000 * beats // (tempo * pulses) + 1

	if not soak_playing(parts) or settled.since() < max(_SOAK_QUIET, _SOAK_QUIET_GAPS * gap_ms):
		return False

	# Enough gaps for the window's length, and the few at the start that aren't judged.
	wanted = min(_SOAK_MOST, _SOAK_WINDOW * tempo * pulses // (6000000 * beats) + _SKIP)
	due = wanted * gap_ms
	changes = settled.changes
	began = time.ticks_ms()

	capture.again(wanted)

	while not capture.full() and settled.changes == changes and time.ticks_diff(time.ticks_ms(), began) < due + _SOAK_LATE:
		await asyncio.sleep_ms(100)

	capture.halt()
	took = time.ticks_diff(time.ticks_ms(), began)

	if settled.changes != changes:
		counts["spoiled"] += 1
		print("SOAK spoiled after_ms=%d" % took)
		return True

	timed, worst, total = await soak_judge(capture, soak_lengths(tempo, pulses, beats))
	drifted = drift_cycles(total, timed, tempo, pulses, beats)
	short = not capture.full()

	gc.collect()
	free = gc.mem_free()
	status = parts.player.status()
	kept_up = parts.app.health() if parts.app is not None else {}
	network = parts.wifi.status() if parts.wifi is not None else {}

	counts["windows"] += 1
	counts["pulses"] += timed
	counts["short"] += 1 if short else 0
	counts["worst"] = max(counts["worst"], worst)
	counts["drift"] = max(counts["drift"], abs(drifted))
	counts["least_free"] = min(counts["least_free"], free) if counts["least_free"] else free
	counts["first_free"] = counts["first_free"] or free
	counts["last_free"] = free

	print("SOAK window n=%d tempo=%d ratio=%d/%d pulses=%d worst_cycles=%d drift_cycles=%d short=%d took_ms=%d free=%d saves=%d mode=%s address=%s holds=%d held=%d longest_hold=%d given_up=%d pages=%d %s" % (counts["windows"], tempo, pulses, beats, timed, worst, drifted, 1 if short else 0, took, free, parts.storage.saves, network.get("mode") or "none", network.get("address") or "none", kept_up.get("holds", -1), kept_up.get("held", -1), kept_up.get("longest_hold", -1), kept_up.get("given_up", -1), kept_up.get("pages", -1), describe(status)))

	return True


async def soak_windows (parts, capture: Capture, settled: Settled, counts: dict) -> None:

	"""Time output 2 in one window after another, for as long as the soak lasts.

	Anything that goes wrong here is printed and counted, and the windows
	carry on: the soak is for what PicoClock does, and a mistake in the
	measuring shouldn't end it unseen.
	"""

	said = time.ticks_ms()

	while True:

		try:
			timed = await soak_window(parts, capture, settled, counts)

		except Exception as problem:
			counts["broke"] += 1
			print("SOAK broke")
			sys.print_exception(problem)
			timed = True

		if timed:
			said = time.ticks_ms()
			await asyncio.sleep_ms(_SOAK_BETWEEN)
			continue

		# Paused, stopped or muted for a while: worth a line, so a quiet soak can be told from a dead one.
		if time.ticks_diff(time.ticks_ms(), said) > _SOAK_ALIVE:
			print("SOAK waiting playing=%d quiet_ms=%d" % (1 if soak_playing(parts) else 0, settled.since()))
			said = time.ticks_ms()

		await asyncio.sleep_ms(200)


async def soaking (parts, capture: Capture, settled: Settled, counts: dict, minutes: int) -> None:

	"""Run the whole of PicoClock and the windows beside it, for this many minutes."""

	windows = asyncio.create_task(soak_windows(parts, capture, settled, counts))

	try:
		await picoclock.device.start.carry_on(parts, minutes * 60000, _PAGE_FOLDER)

	finally:

		# Windows timed only before something ended the job would make a soak look whole.
		if windows.done():
			counts["ended"] = 1

		windows.cancel()
		await asyncio.sleep_ms(50)


def soak (minutes: int) -> None:

	"""Run the whole of PicoClock on the WiFi network it has saved for this many minutes, timing output 2 in windows of a minute throughout.

	This is the air test made long. The laptop half, `tools/soak.py`, uses
	the page throughout and changes the tempo, the transport and output 2
	between windows. Each window is held to the cycle against whatever the
	tempo and output 2 were set to when it began. A line is printed for each
	window, and one result line at the end.

	The clock runs at the PPQN PicoClock starts at, 4, as most people's will:
	output 1 sends four pulses a beat, and every other output a ratio of it.

	The settings it found are kept on the Pico's storage too while it runs,
	and put back at the end. A soak cut short by restarting the Pico leaves
	them there, and the next soak puts those back instead.
	"""

	import picoclock.core.wifi

	start = picoclock.device.start
	board = picoclock.device.board
	path = picoclock.device.storage.PATH
	names = page_files()

	try:
		with open(_SOAK_KEPT) as handle:
			kept = handle.read()

	except OSError:

		try:
			with open(path) as handle:
				kept = handle.read()

		except OSError:
			kept = None

	if kept is not None and _SOAK_KEPT[1:] not in os.listdir("/"):
		with open(_SOAK_KEPT, "w") as handle:
			handle.write(kept)

	try:
		os.mkdir(_PAGE_FOLDER)
	except OSError:
		pass

	for name in names:
		copy_page_file(name)

	# Made before the player, which would otherwise lose the pin to it.
	capture = Capture(board.FIRST_OUTPUT_PIN + 1, _SOAK_MOST)
	parts = start.begin()
	saved = bool(parts.clock.settings.network_name)
	settled = Settled(parts.clock)
	counts = {"windows": 0, "spoiled": 0, "short": 0, "pulses": 0, "worst": 0, "drift": 0, "first_free": 0, "last_free": 0, "least_free": 0, "broke": 0, "ended": 0}

	print("SOAK began minutes=%d saved=%d" % (minutes, saved))

	try:

		# Output 2 begins at 48 pulses a second: 120 BPM and 24 pulses a beat.
		# The laptop half's ratios are of a clock of this PPQN too.
		ppqn = picoclock.core.ratio.DEFAULT_PPQN
		parts.clock.set_ppqn(ppqn)
		parts.clock.set_tempo(12000)
		parts.clock.set_output(1, "ratio", "24/%d" % ppqn)

		asyncio.run(soaking(parts, capture, settled, counts, minutes))

		status = parts.player.status()
		joined = parts.wifi is not None and parts.wifi.mode == picoclock.core.wifi.JOINED
		opened = parts.app._opened if parts.app is not None else 0
		kept_up = parts.app.health() if parts.app is not None else {}
		saves = parts.storage.saves

	finally:

		# Whatever went wrong, the Pico is left as it was found: stopped, with the
		# settings it had, the network among them. A soak stopped early is
		# interrupted from the laptop, and an interrupt tends to come twice, so the
		# stopping is seen through as `start.run` sees it through, and the rest is
		# done with interrupts held off.
		while True:

			try:
				start.stop(parts)
				break

			except KeyboardInterrupt:
				pass

		picoclock.device.chip.hold()

		try:

			parts.player.close()
			capture.close()

			for name in names:

				try:
					os.remove(_PAGE_FOLDER + "/" + name + ".gz")
				except OSError:
					pass

			try:
				os.rmdir(_PAGE_FOLDER)
			except OSError:
				pass

			if kept is not None:

				with open(path, "w") as handle:
					handle.write(kept)

				os.remove(_SOAK_KEPT)

			else:

				# There were none to begin with, so the soak's own aren't left behind.
				try:
					os.remove(path)
				except OSError:
					pass

		finally:
			picoclock.device.chip.release()

	# Windows come about once a minute and pages about twice. Half as many, and
	# at most a quarter of the windows spoiled, leaves room for every pause and
	# stop the laptop half makes while still noticing a soak that went quiet.
	enough = counts["windows"] >= minutes // 2 and counts["spoiled"] <= counts["windows"] // 4 and opened >= minutes
	passed = saved and joined and enough and counts["ended"] == 0 and counts["worst"] <= 2 and counts["drift"] <= _DRIFT_MOST and counts["short"] == 0 and counts["broke"] == 0 and sound(status)

	report("soak", passed, "windows=%d spoiled=%d short=%d broke=%d ended_early=%d pulses=%d worst_cycles=%d drift_cycles=%d free_kb=%d..%d least_free_kb=%d saves=%d joined=%d pages_opened=%d holds=%d held_ms=%d longest_hold_ms=%d given_up=%d %s" % (counts["windows"], counts["spoiled"], counts["short"], counts["broke"], counts["ended"], counts["pulses"], counts["worst"], counts["drift"], counts["first_free"] // 1024, counts["last_free"] // 1024, counts["least_free"] // 1024, saves, joined, opened, kept_up.get("holds", -1), kept_up.get("held", -1), kept_up.get("longest_hold", -1), kept_up.get("given_up", -1), describe(status)))


TESTS = {
	"air": test_air,
	"burst": test_burst,
	"cluster": test_cluster,
	"collect": test_collect,
	"shortfall": test_shortfall,
	"freerun": test_freerun,
	"inputs": test_inputs,
	"led": test_led,
	"network": test_network,
	"page": test_page,
	"player": test_player,
	"position": test_position,
	"powerup": test_powerup,
	"save": test_save,
	"saved": test_saved,
	"stopsave": test_stopsave,
	"sweep": test_sweep,
	"pause": test_pause,
	"startstop": test_startstop,
}


def run (test: str, load: str = "idle") -> None:

	"""Run one test by name under one load by name, first putting back any settings a test cut short left in keeping."""

	put_back_left_over()
	TESTS[test](load)
