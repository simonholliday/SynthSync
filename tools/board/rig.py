"""The timing rig's half that runs on the Pico.

It times real pulses with hardware, not with Python. A second PIO state machine
counts system-clock cycles between the rising edges on a pin, and a DMA channel
stores the counts, so nothing the processor is doing can colour the measurement.
It resolves 2 cycles, which is 13 ns at 150 MHz.

While it captures, it can load the Pico the way real use would: collecting
garbage, writing to flash, running the radio.

`tools/rig.py` copies this to the board and runs it. It is not part of
PicoClock itself, and it only runs on MicroPython.

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
import time

import picoclock.core.clock
import picoclock.core.settings
import picoclock.core.transport
import picoclock.device.board
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
_FDEBUG = _PIO0 + 0x008
_RXF0 = _PIO0 + 0x020

# The capture runs on state machine 1 of block 0, and the thing under test on 0.
_CAPTURE_MACHINE = 1
_CAPTURE_REQUEST = 4 + _CAPTURE_MACHINE

# The first counts are part-intervals from before the capture settled.
_SKIP = 2


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

		control = self.dma.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=_CAPTURE_REQUEST)

		self.dma.config(read=_RXF0 + 4 * _CAPTURE_MACHINE, write=self.counts, count=count, ctrl=control, trigger=True)
		self.machine.active(1)

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


def clear_fault () -> None:

	"""Forget any earlier report that state machine 0 ran out of words."""

	machine.mem32[_FDEBUG] = 1 << 24


def fault () -> int:

	"""Say whether state machine 0 has run out of words since the last clear."""

	return (machine.mem32[_FDEBUG] >> 24) & 1


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

	A steady output's gaps come in at most two lengths, one cycle or one tick
	apart, because the exact gap is rarely a whole number. Those are the
	allowed lengths, worked out from the tempo and the ratio.
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


# ---------------------------------------------------------------------------
# Tests.


def test_freerun (load: str = "idle", hz: int = 200, count: int = 2000) -> None:

	"""Time the rig's own reference: a pulse made by PIO alone should show no error at all.

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
	lengths a tick apart. They are worked out here the way the scheduler
	promises them: pulse number n is on tick n x beats x 6,000,000,000 /
	(pulses x tempo), rounded down.
	"""

	lengths = set()
	previous = 0

	for number in range(1, 600):
		tick = (number * beats * _UNITS) // (pulses * tempo)
		lengths.add((tick - previous) * (SYSTEM_HZ // 1000000))
		previous = tick

	return lengths


def listen_to_buttons (player) -> None:

	"""Have the second core read the start, pause and stop pins, as it always does in use, so what that costs it is in every timing.

	Nothing is wired to them on the test board, so they read as let go.
	"""

	for name, (pin, low) in picoclock.device.panel.wiring().items():
		if name != picoclock.device.panel.RESET:
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


def sound (status: dict) -> bool:

	"""Say whether the player's own counts show nothing wrong.

	It never ran dry, the scheduler refused nothing, and no command was held
	up. A command that waited for the first core to collect its garbage takes
	ten times the limit. The words put by can cover for one, so that it makes
	no fault, which is why it is looked for by itself.
	"""

	return status["faults"] == 0 and status["refused"] == 0 and status["slowest_command"] <= _SLOWEST_COMMAND


def describe (status: dict) -> str:

	"""Write the player's own counts for a result line."""

	return "faults=%d refused=%d fewest_waiting=%d slowest_command_us=%d merged=%d" % (status["faults"], status["refused"], status["fewest_waiting"], status["slowest_command"], status["merged"])


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
	passed = len(gaps) >= count - _SKIP and worst <= 2 and sound(status)

	report("player", passed, "pulses=%d worst_cycles=%d %s load=%s" % (len(gaps), worst, describe(status), described))


def test_sweep (load: str = "idle") -> None:

	"""Change the tempo many times a second in the busiest pattern: the player must never run dry."""

	count = 2000
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(_DENSE, 12000)
	state = [0, time.ticks_ms()]

	def meanwhile () -> None:

		"""Set another tempo, at most fifty times a second."""

		if time.ticks_diff(time.ticks_ms(), state[1]) >= 20:
			state[0] += 1
			state[1] = time.ticks_ms()
			player.set_tempo(6000 + (state[0] * 977) % 24001)

	player.start()

	described = run_load(load, capture, 60, meanwhile)
	status = player.status()
	gaps = capture.gaps()

	player.close()
	capture.close()

	# Whatever the tempo does, a gap is never shorter than at 300 BPM nor longer than at 60.
	shortest = min(gap_lengths(30000, _DENSE[0][0], _DENSE[0][1]))
	longest = max(gap_lengths(6000, _DENSE[0][0], _DENSE[0][1]))
	in_range = len(gaps) > 0 and min(gaps) >= shortest - 2 and max(gaps) <= longest + 2
	passed = len(gaps) >= count - _SKIP and in_range and sound(status)

	report("sweep", passed, "pulses=%d tempo_changes=%d %s load=%s" % (len(gaps), state[0], describe(status), described))


def test_pause (load: str = "idle") -> None:

	"""Pause and carry on a dozen times: no pulse may go missing or appear, and the rest stay exact."""

	count = 600
	ratios = ((24, 1), (1, 1), (2, 1), (3, 1))
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_player(ratios, 12500)

	# How many pauses have been made, when the last change was, and whether the clock is paused.
	state = [0, time.ticks_ms(), False]

	def meanwhile () -> None:

		"""Pause once a second for 150 ms, and stop pausing in time for the last one to finish."""

		since = time.ticks_diff(time.ticks_ms(), state[1])

		if state[2] and since >= 150:
			player.resume()
			state[2] = False
			state[1] = time.ticks_ms()

		elif not state[2] and since >= 1000 and state[0] < 12 and capture.taken() < count - 120:
			player.pause()
			state[0] += 1
			state[2] = True
			state[1] = time.ticks_ms()

	player.start()

	described = run_load(load, capture, 60, meanwhile)

	if state[2]:
		player.resume()

	status = player.status()
	gaps = capture.gaps()

	player.close()
	capture.close()

	# 24 pulses a beat at 125 BPM are 20 ms apart. A gap much longer than that
	# is one that spans a pause, and there must be exactly one for each pause.
	allowed = gap_lengths(12500, 24, 1)
	long_enough = max(allowed) + (SYSTEM_HZ // 1000) * 100
	spans = [cycles for cycles in gaps if cycles > long_enough]
	steady = [cycles for cycles in gaps if cycles <= long_enough]
	worst = worst_error(steady, allowed)
	passed = len(gaps) >= count - _SKIP and len(spans) == state[0] and state[0] > 0 and worst <= 2 and sound(status)

	report("pause", passed, "pulses=%d pauses=%d long_gaps=%d worst_cycles=%d %s load=%s" % (len(gaps), state[0], len(spans), worst, describe(status), described))


def test_startstop (load: str = "idle") -> None:

	"""Start and stop over and over: every start must pulse, and every stop must leave all pins low."""

	first = picoclock.device.board.FIRST_OUTPUT_PIN
	ratios = ((4, 1), (4, 1), (4, 1), (4, 1))
	capture = Capture(first, 4000)
	player = make_player(ratios, 12000)
	pins = [machine.Pin(first + output) for output in range(len(ratios))]

	# How many starts, how many stops that left a pin high, when the last change was, and whether it's running.
	state = [0, 0, time.ticks_ms(), False]

	def meanwhile () -> None:

		"""Start, run for 60 ms, stop, wait 60 ms, and look at the pins before starting again."""

		if time.ticks_diff(time.ticks_ms(), state[2]) < 60:
			return

		if state[3]:
			player.stop()
			state[3] = False

		else:

			if state[0] and any(pin.value() for pin in pins):
				state[1] += 1

			player.start()
			state[0] += 1
			state[3] = True

		state[2] = time.ticks_ms()

	described = run_load(load, capture, 8, meanwhile)

	player.stop()
	time.sleep_ms(60)

	left_high = state[1] + (1 if any(pin.value() for pin in pins) else 0)
	status = player.status()
	edges = capture.taken()

	player.close()
	capture.close()

	# Each start pulses at once, so there is at least one edge for every start.
	passed = state[0] > 0 and edges >= state[0] and left_high == 0 and sound(status)

	report("startstop", passed, "starts=%d edges=%d stops_left_high=%d %s load=%s" % (state[0], edges, left_high, describe(status), described))


# Where the save tests keep their settings, apart from PicoClock's own file.
_SAVE_PATH = "/_rig_settings.json"


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

	player.close()
	capture.close()

	with open(_SAVE_PATH) as handle:
		kept = handle.read() == settings.to_text()

	worst = worst_error(gaps, gap_lengths(30000, _DENSE[0][0], _DENSE[0][1]))
	passed = len(gaps) >= count - _SKIP and worst <= 2 and sound(status) and storage.saves > 20 and kept

	report("save", passed, "pulses=%d saves=%d worst_cycles=%d kept=%s %s load=%s" % (len(gaps), storage.saves, worst, kept, describe(status), described))


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
	stopped = not after["running"] and 0 <= took <= _HELD_PRESS
	passed = len(gaps) >= count - _SKIP and worst <= 2 and sound(status) and stopped

	report("cluster", passed, "pulses=%d worst_cycles=%d stop_ms=%d %s load=%s" % (len(gaps), worst, took, describe(status), described))


def test_burst (load: str = "idle") -> None:

	"""Send every setting at once, over and over, while four outputs' edges crowd together: the player must never run dry.

	A burst is what a factory reset or a power-up sends: the tempo and every
	part of every output, back to back.
	"""

	count = 4000
	ratios = _CLUSTERED
	capture = Capture(picoclock.device.board.FIRST_OUTPUT_PIN, count)
	player = make_clustered_player(30000)

	# How many bursts have been sent, and when the last one was.
	state = [0, time.ticks_ms()]

	def meanwhile () -> None:

		"""Send a burst ten times a second, keeping the pattern as busy as it began."""

		if time.ticks_diff(time.ticks_ms(), state[1]) < 100:
			return

		state[0] += 1
		state[1] = time.ticks_ms()

		player.set_tempo(29000 + (state[0] * 977) % 1001)

		for output in range(len(ratios)):
			player.set_ratio(output, ratios[output][0] - state[0] % 3, 1)
			player.set_width(output, 1000 + (state[0] % 2) * 20)
			player.set_role(output, 0)
			player.set_mute(output, False)

	player.start()

	described = run_load(load, capture, 30, meanwhile)
	status = player.status()
	edges = capture.taken()

	player.close()
	capture.close()

	passed = edges >= count and state[0] >= 20 and sound(status)

	report("burst", passed, "pulses=%d bursts=%d %s load=%s" % (edges, state[0], describe(status), described))


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
	answered = state[0] - state[1]
	passed = len(gaps) >= count - _SKIP and worst <= 2 and sound(status)
	# Under the heaviest load the main loop comes up for air less often, so fewer
	# are asked. A save every 700 ms with a backlog of 300 ms withholds close to
	# half the answers, so a quarter answered is asked for, not a half.
	passed = passed and state[0] >= 50 and answered >= state[0] // 4 and state[1] >= 1 and state[2] <= 100 and state[3] >= 3

	report("position", passed, "asked=%d withheld=%d worst_thousandths=%d saves=%d pulses=%d worst_cycles=%d %s load=%s" % (state[0], state[1], state[2], state[3], len(gaps), worst, describe(status), described))


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

	A knock too short to be a press must do nothing. A press must work when
	it is seen as it happens, and when it comes and goes while the main loop
	is held up. A stop pressed while the first core is held up for ten
	seconds, as a poor WiFi signal can hold it, must stop the clock within
	50 ms all the same, and the clock must hear of it once the hold is over.
	The reset button must do nothing for a second's press, and put every
	setting back when held for three. The light must come on once a beat
	while the clock runs, and flash fast while reset is held.
	"""

	transport = picoclock.core.transport
	player = picoclock.device.player.Player(picoclock.device.board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	storage = picoclock.device.storage.Storage(settings, player, _INPUTS_PATH)
	clock = picoclock.core.clock.Clock(settings, player, storage)
	light = Light()
	never = Never()

	clock.load()
	player.settle()

	# The panel in use, what went wrong, how many presses were made, how many
	# times the light came on in two seconds of running and in a reset's hold,
	# the longest and shortest hold that a reset took, and the longest a stop
	# took while the first core was held up, in milliseconds.
	panel = [None]
	wrong = []
	counts = [0, 0, 0, 0, 99999, -1]

	def use (name: str, low: bool) -> None:

		"""Put one button on the input pin, in place of whichever was there."""

		if panel[0] is not None:
			panel[0].close()

		panel[0] = picoclock.device.panel.Panel(clock, player, {name: (_INPUT_PIN, low)}, light)

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

			use(transport.START, low)
			yield 100

			finger.press(8)
			yield 200
			expect("a knock started the clock, " + way, clock.transport.state == transport.STOPPED)

			finger.press(80)
			counts[0] += 1
			yield 300
			expect("start didn't start the clock, " + way, clock.transport.state == transport.RUNNING and doing() == transport.RUNNING)

			# Two seconds of running at 120 BPM is four beats.
			before = light.flashes
			yield 2000
			counts[1] = light.flashes - before

			use(transport.PAUSE, low)
			yield 100
			finger.press(80)
			counts[0] += 1
			yield 300
			expect("pause didn't pause the clock, " + way, clock.transport.state == transport.PAUSED and doing() == transport.PAUSED)

			use(transport.STOP, low)
			yield 100
			finger.press(80)
			counts[0] += 1
			yield 300
			expect("stop didn't stop the clock, " + way, clock.transport.state == transport.STOPPED and doing() == transport.STOPPED)

			# A press that comes and goes while the main loop is held up, and then a knock that does.
			use(transport.START, low)
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

			# Reset: a second's press does nothing, and three seconds puts every setting back.
			clock.set_tempo(7700)
			clock.flush()
			storage.save_now()
			use(picoclock.device.panel.RESET, low)
			yield 100
			expect("nothing was saved to begin with, " + way, saved())

			finger.press(1000)
			yield 1400
			expect("a short press of reset changed the settings, " + way, settings.tempo == 7700 and saved())

			# Held for six seconds, it must go off once, three seconds in.
			before = light.flashes
			began = time.ticks_ms()
			finger.press(6000)
			counts[0] += 1

			while settings.tempo == 7700 and time.ticks_diff(time.ticks_ms(), began) < 5500:
				yield 5

			took = time.ticks_diff(time.ticks_ms(), began)
			counts[2] = light.flashes - before
			counts[3] = max(counts[3], took)
			counts[4] = min(counts[4], took)

			expect("holding reset didn't put the settings back, " + way, settings.tempo == 12000 and not saved())
			expect("reset went off before three seconds, " + way, took >= 3000)
			expect("reset stopped the clock, " + way, clock.transport.state == transport.RUNNING and doing() == transport.RUNNING)

			# Still held, it doesn't go off again.
			clock.set_tempo(8800)
			clock.flush()

			while time.ticks_diff(time.ticks_ms(), began) < 6300:
				yield 50

			expect("reset went off twice in one hold, " + way, settings.tempo == 8800)

			# Stop pressed with the first core held up, and nothing given a turn until the hold is over.
			use(transport.STOP, low)
			yield 100
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
	player.close()

	machine.Pin(_FINGER_PIN, machine.Pin.IN, None)
	machine.Pin(_INPUT_PIN, machine.Pin.IN, None)

	# With nothing else to do, the buttons and the light are looked at often
	# enough to count the light's flashes and to time the reset closely.
	steady = load != "idle" or (3 <= counts[1] <= 5 and 20 <= counts[2] <= 30 and counts[3] <= 3100)
	passed = never.over and not wrong and counts[0] == 12 and steady and sound(status)

	report("inputs", passed, "presses=%d wrong=%d beat_flashes=%d hold_flashes=%d reset_after_ms=%d..%d stop_while_held_ms=%d first_wrong=%s %s load=%s" % (counts[0], len(wrong), counts[1], counts[2], counts[4], counts[3], counts[5], wrong[0].replace(" ", "_") if wrong else "none", describe(status), described))


# How far each flash of the panel LED may be from what it should be, in
# cycles: from output 1's rise to the LED's, and in how long it is lit. The
# program takes a few cycles to see a pin change and act on it.
_LED_LATE = 10
_LED_SLACK = 20


def _flash_program (watched: int):

	"""Build a program that times each flash of the panel LED from a rise of output 1.

	For each flash it gives how long the LED took to come on after output 1
	rose, and then how long it stayed on, two cycles to a count. The LED's
	pin is the jump pin.
	"""

	def flashes ():

		wrap_target()
		wait(0, gpio, watched)
		wait(1, gpio, watched)

		mov(x, invert(null))
		label("dark")
		jmp(pin, "lit")
		jmp(x_dec, "dark")
		label("lit")
		mov(isr, invert(x))
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


def test_led (load: str = "idle") -> None:

	"""Time every flash of the panel LED against output 1, through a tempo change, a long pulse and output 1 switched off.

	Each flash must start within a few cycles of output 1's pulse. It must
	last a quarter of a beat, at most 100 ms, or for as long as the pulse
	when that is longer. With output 1 off the LED must stay dark. The tempo
	and the pulse are changed through the clock, as the page changes them,
	and the panel tells the LED, as it does at power-up.
	"""

	board = picoclock.device.board
	cycles = SYSTEM_HZ // 1000000
	player = picoclock.device.player.Player(board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	clock = picoclock.core.clock.Clock(settings, player)
	never = Never()

	clock.load()

	# The LED's pin is only named here, not set up, so the LED's program keeps it.
	timer = rp2.StateMachine(_CAPTURE_MACHINE, _flash_program(board.FIRST_OUTPUT_PIN), freq=SYSTEM_HZ, jmp_pin=machine.Pin(board.LED_PIN))
	led = picoclock.device.led.Led(settings.tempo)
	panel = picoclock.device.panel.Panel(clock, player, {}, Light(), led)

	timer.active(1)
	clock.press(picoclock.core.transport.START)

	# Each stage: the tempo, output 1's pulse in milliseconds, whether output 1
	# is on, how long to watch for in milliseconds, and how long each flash
	# should be lit for in cycles, or 0 for no flash at all.
	stages = (
		(30000, 10, True, 2400, 50000 * cycles),
		(30000, 80, True, 2400, 80000 * cycles),
		(6000, 10, True, 7500, 100000 * cycles),
		(6000, 10, False, 3500, 0),
	)

	# Per stage: the flashes counted, and the latest start and the furthest length
	# of those. The LED takes a new time after a flash, and only once the first
	# core has given it, so a stage's flashes count from the first after the LED
	# has taken its time and the player its pulse, less one that may already have
	# begun.
	seen = [[0, 0, 0] for _ in stages]
	stage = [0]
	waiting = [-1]
	ready = [False]

	def take () -> None:

		"""Take every count the timer has given, in pairs: how late the LED came on, then how long it was on."""

		while timer.rx_fifo():

			count = timer.get()

			# A start is a few counts at most and a length is millions, so a pair split by a full queue is never mistaken.
			if count < 1000:
				waiting[0] = count
				continue

			if waiting[0] < 0:
				continue

			late = 2 * waiting[0]
			lit = 2 * count
			waiting[0] = -1

			if not ready[0]:
				continue

			counts = seen[stage[0]]
			counts[0] += 1

			if counts[0] <= 1:
				continue

			counts[1] = max(counts[1], late)
			expected = stages[stage[0]][4]
			counts[2] = max(counts[2], abs(lit - expected) if expected else lit)

	def script ():

		"""Set each stage up as the page would, and watch it for its time."""

		for index in range(len(stages)):

			tempo, pulse, on, watch, lit = stages[index]

			take()
			stage[0] = index
			ready[0] = False
			clock.set_tempo(tempo)
			clock.set_output(0, "pulse", pulse)
			clock.set_output(0, "muted", not on)
			clock.flush()
			player.settle()
			panel.look()
			yield watch

		never.over = True
		yield 0

	steps = script()
	due = [time.ticks_ms()]

	def meanwhile () -> None:

		"""Look at the panel as the main loop would, take what the timer has, and move on when a stage is over."""

		panel.look()
		clock.flush()

		# What came before the LED took this stage's time is set aside, unjudged.
		if not ready[0] and led.holds(stages[stage[0]][0]):
			take()
			ready[0] = True

		take()

		if never.over or time.ticks_diff(time.ticks_ms(), due[0]) < 0:
			return

		due[0] = time.ticks_add(time.ticks_ms(), next(steps))

	described = run_load(load, never, 60, meanwhile)
	status = player.status()

	take()
	timer.active(0)
	panel.close()
	led.close()
	player.close()

	flashes = [counts[0] for counts in seen]
	late = max(counts[1] for counts in seen[:3])
	worst = max(counts[2] for counts in seen[:3])
	dark = seen[3][0]

	# Over two seconds at 300 BPM is twelve flashes, and seven and a half at 60 is
	# seven. Under load the LED may take its time a second or so late.
	enough = flashes[0] >= 5 and flashes[1] >= 5 and flashes[2] >= 3
	passed = never.over and enough and late <= _LED_LATE and worst <= _LED_SLACK and dark == 0 and sound(status)

	report("led", passed, "flashes=%d,%d,%d late_cycles=%d worst_cycles=%d flashes_when_off=%d %s load=%s" % (flashes[0], flashes[1], flashes[2], late, worst, dark, describe(status), described))


def test_powerup (load: str = "idle") -> None:

	"""Start the clock as power-up does, from no saved settings, from a damaged file and from a good one, and then everything that follows it.

	Each start must leave the clock running and pulsing: at the saved tempo
	when the file is good, and as a new PicoClock otherwise.

	What follows the clock is the buttons, the saving, the Pico's own network
	and the page. With all of it going, the network must be up under
	PicoClock's own name and the page's port answered for. Stopped, the
	network must be off and the port let go, with the clock still playing.
	That must hold when it is interrupted from the Pico's prompt too, which
	is how it is stopped in real life. And if the network and the page can't
	be made at all, the rest must carry on without them.

	Whatever settings the Pico had saved are put back afterwards.
	"""

	import network
	import socket

	path = picoclock.device.storage.PATH
	first = machine.Pin(picoclock.device.board.FIRST_OUTPUT_PIN)
	wrong = []
	slowest = 0

	try:
		with open(path) as handle:
			kept = handle.read()
	except OSError:
		kept = None

	good = picoclock.core.settings.Settings(picoclock.device.board.OUTPUTS)
	good.set_tempo(24000)

	# What is saved, the tempo the clock should come up at, and whether something should be said about the file.
	cases = (
		("no file", None, 12000, False),
		("a file cut short", good.to_text()[:40], 12000, True),
		("a file of rubbish", "\x00\xff not settings at all {{{", 12000, True),
		("a good file", good.to_text(), 24000, False),
	)

	for name, text, tempo, told in cases:

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

		# Count output 1's pulses for a second: two at 120 BPM and four at 240.
		pulses = 0
		was = first.value()
		deadline = time.ticks_add(time.ticks_ms(), 1000)

		while time.ticks_diff(deadline, time.ticks_ms()) > 0:

			now = first.value()

			if now and not was:
				pulses += 1

			was = now

		status = parts.player.status()
		expected = tempo // 6000

		if not (status["running"] and not status["paused"] and sound(status)):
			wrong.append(name + ": the clock isn't running cleanly")

		if parts.clock.settings.tempo != tempo or parts.player.timeline.tempo != tempo:
			wrong.append(name + ": the tempo is wrong")

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

	# Everything that follows the clock, left going for a few seconds. The radio is given a
	# name of the rig's first, so that one left over from an earlier run can't pass for PicoClock's.
	own.config(ssid="PICO-not-ours")

	parts = start.begin()
	asyncio.run(start.carry_on(parts, 2500))
	stood = start.reached(parts)

	if len(parts.jobs) != 6 or parts.panel is None:
		wrong.append("the rest: %d jobs were started" % len(parts.jobs))

	if not stood.startswith("page=1 ") or not answered():
		wrong.append("the rest: the page isn't being served")

	if " address=192.168.4.1 " not in stood or not stood.endswith(" own=" + called) or not own.active() or own.config("ssid") != called:
		wrong.append("the rest: the network isn't up under its own name")

	start.stop(parts)
	status = parts.player.status()
	free = int(parts.stood.split(" free=")[1].split()[0]) if " free=" in parts.stood else 0

	if not parts.stood.startswith("page=1 "):
		wrong.append("stopped: no note was left of how things stood")

	if own.active() or answered():
		wrong.append("stopped: the network (%d) or the page's port (%d) was left on" % (own.active(), answered()))

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

	if own.active() or answered():
		wrong.append("interrupted: the network (%d) or the page's port (%d) was left on" % (own.active(), answered()))

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

	try:
		os.remove(path)
	except OSError:
		pass

	if kept is not None:
		with open(path, "w") as handle:
			handle.write(kept)

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
	that first, fall back the same way, and keep it. The two networks must
	never be up together. At the end everything must be off.

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
		expect(what + ": the radio says otherwise", wifi[0].status() == {"mode": "own", "name": name, "address": "192.168.4.1", "problem": problem, "own": name})

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

	def away (what: str) -> None:

		"""See that the own network is off and a join of the missing network is under way."""

		expect(what + ": the own network is still up", not own.active())
		expect(what + ": the joining side isn't on", other.active())
		expect(what + ": the radio says otherwise", wifi[0].status() == {"mode": "joining", "name": missing, "address": "", "problem": "", "own": name})

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
	passed = never.over and not wrong and started[1] == 5 and 1000 <= taken < count and worst <= 2 and sound(status)

	report("network", passed, "starts=%d first_start_ms=%d change_in_force_ms=%d found=%d look_ms=%d joins=%d back_after_ms=%d wrong=%d first_wrong=%s pulses=%d worst_cycles=%d %s load=%s" % (started[1], took, counts[0], counts[1], counts[2], started[3], counts[3], len(wrong), wrong[0].replace(" ", "_") if wrong else "none", len(gaps), worst, describe(status), described))


# Where the page test keeps its settings, and the page's files while it runs.
_PAGE_PATH = "/_rig_page.json"
_PAGE_FOLDER = "/_rig_page"

# What a browser sends with a request from PicoClock's own page.
_OWN_SITE = {"Origin": "http://192.168.4.1", "Host": "192.168.4.1"}


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

	Every request a phone makes is made here: the page's six files, read from
	the Pico's own storage, everything a new page needs, changes sent as JSON
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
	names = sorted(picoclock.web.app.PAGE_FILES)
	sizes = {}

	# The page's files go onto the Pico's own storage, which is where it reads them from in use.
	try:
		os.mkdir(_PAGE_FOLDER)
	except OSError:
		pass

	for name in names:

		with open("picoclock/web/static/" + name + ".gz", "rb") as handle:
			data = handle.read()

		with open(_PAGE_FOLDER + "/" + name + ".gz", "wb") as handle:
			handle.write(data)

		sizes[name] = len(data)

	# Output 2 is the one timed, and nothing below changes it. The changes go to outputs 3 and 4.
	capture = Capture(board.FIRST_OUTPUT_PIN + 1, count)
	player = picoclock.device.player.Player(board.FIRST_OUTPUT_PIN, 4)
	settings = picoclock.core.settings.Settings(4)
	storage = picoclock.device.storage.Storage(settings, player, _PAGE_PATH)
	clock = picoclock.core.clock.Clock(settings, player, storage)

	clock.set_tempo(tempo)
	clock.set_output(1, "ratio", "24")
	clock.load()
	clock.press(picoclock.core.transport.START)
	player.settle()

	wifi = picoclock.device.radio.make(clock)
	wifi.look(0)

	app = picoclock.web.app.App(clock, wifi, _PAGE_FOLDER)
	client = microdot.test_client.TestClient(app.web)
	visitor = Visitor()
	wrong = []

	# How many requests have been made, how many rounds of them, the longest one took in
	# milliseconds, the memory left after the first round, how many networks the look
	# found, how long it took, and the memory left after the latest round.
	counts = [0, 0, 0, 0, -1, 0, 0]
	ratios = ("3/2", "2", "1/4", "5")

	def busy () -> bool:

		"""Say whether this round makes changes. Three do and then five don't, so that settings are left alone long enough to be saved."""

		return counts[1] % 8 < 3

	def expect (what: str, truth: bool) -> None:

		"""Note something that should be so and isn't."""

		if not truth:
			wrong.append(what)

	async def fetch (name: str) -> None:

		"""Ask for one of the page's files."""

		response = await client.get("/" if name == "index.html" else "/" + name, headers={"Host": "192.168.4.1"})

		expect(name + " wasn't sent whole and gzipped", response.status_code == 200 and response.headers.get("Content-Encoding") == "gzip" and len(response.body) == sizes[name])

	async def everything () -> None:

		"""Ask for all a new page needs."""

		response = await client.get("/api/state", headers={"Host": "192.168.4.1"})

		expect("a new page wasn't given everything", response.status_code == 200 and len(response.json["now"]) == 8 and response.json["fixed"]["roles"] == list(picoclock.core.settings.ROLES))

	async def change () -> None:

		"""Change output 3's ratio as a page without a socket does."""

		if not busy():
			return

		ratio = ratios[counts[1] % len(ratios)]
		response = await client.post("/api/change", headers=dict(_OWN_SITE), body={"do": "output", "index": 2, "name": "ratio", "value": ratio})

		expect("a change wasn't carried out", response.status_code == 200 and response.json == {"ok": True} and settings.outputs[2].ratio == picoclock.core.ratio.parse(ratio))

	async def refuse () -> None:

		"""Send a change that makes no sense, one from another site's page, and ask for a file that isn't the page's."""

		response = await client.post("/api/change", headers=dict(_OWN_SITE), body={"do": "output", "index": 2, "name": "ratio", "value": "nonsense"})
		expect("nonsense wasn't refused", response.status_code == 400 and "error" in response.json)

		response = await client.post("/api/change", headers={"Origin": "http://elsewhere.example", "Host": "192.168.4.1"}, body={"do": "stop"})
		expect("another site's page wasn't refused", response.status_code == 403 and clock.transport.state == picoclock.core.transport.RUNNING)

		response = await client.get("/settings.json", headers={"Host": "192.168.4.1"})
		expect("a file that isn't the page's was sent", response.status_code == 404)

	async def socket () -> None:

		"""Open a socket through the server, change output 4 over it, and close."""

		if not busy():
			return

		pulse = 5 + counts[1] % 20

		def page ():
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
	gaps = capture.gaps()

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
	worst = worst_error(gaps, gap_lengths(tempo, 24, 1))
	passed = len(gaps) >= count - _SKIP and worst <= 2 and sound(status) and not wrong and counts[1] >= 3 and heard and kept and storage.saves >= 1

	report("page", passed, "requests=%d rounds=%d slowest_ms=%d look_ms=%d found=%d told=%d/%d/%d saves=%d free_kb=%d..%d wrong=%d first_wrong=%s pulses=%d worst_cycles=%d %s load=%s" % (counts[0], counts[1], counts[2], counts[5], counts[4], told.get("hello", 0), told.get("output", 0), told.get("clock", 0), storage.saves, counts[3] // 1024, counts[6] // 1024, len(wrong), wrong[0].replace(" ", "_") if wrong else "none", len(gaps), worst, describe(status), described))


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
	names = sorted(picoclock.web.app.PAGE_FILES)

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

		with open("picoclock/web/static/" + name + ".gz", "rb") as handle:
			data = handle.read()

		with open(_PAGE_FOLDER + "/" + name + ".gz", "wb") as handle:
			handle.write(data)

	count = _AIR_FOR // 1000 * 130
	capture = Capture(board.FIRST_OUTPUT_PIN + 1, count)
	parts = start.begin()
	saved = bool(parts.clock.settings.network_name)

	try:

		# Output 2 is the one timed: 120 pulses a second.
		parts.clock.set_tempo(tempo)
		parts.clock.set_output(1, "ratio", "24")

		asyncio.run(start.carry_on(parts, _AIR_FOR, _PAGE_FOLDER))

		status = parts.player.status()
		pulses, worst = capture.judge(gap_lengths(tempo, 24, 1), _AIR_SKIP)
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

	free = int(parts.stood.split(" free=")[1].split()[0]) if " free=" in parts.stood else 0
	passed = saved and joined and opened >= 4 and pulses >= 3000 and worst <= 2 and sound(status)

	# How often and for how long everything but the clock was held up for over a second, the longest of those, and the pages given up on.
	report("air", passed, "saved=%d joined=%d pages_opened=%d saves=%d free_kb=%d holds=%d held_ms=%d longest_hold_ms=%d given_up=%d pulses=%d worst_cycles=%d %s" % (saved, joined, opened, saves, free // 1024, kept_up.get("holds", -1), kept_up.get("held", -1), kept_up.get("longest_hold", -1), kept_up.get("given_up", -1), pulses, worst, describe(status)))


TESTS = {
	"air": test_air,
	"burst": test_burst,
	"cluster": test_cluster,
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
	"sweep": test_sweep,
	"pause": test_pause,
	"startstop": test_startstop,
}


def run (test: str, load: str = "idle") -> None:

	"""Run one test by name under one load by name."""

	TESTS[test](load)
