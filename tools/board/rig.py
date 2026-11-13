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
import gc
import machine
import os
import rp2
import time

import picoclock.core.settings
import picoclock.device.board
import picoclock.device.player
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

	"""Bring up the Pico's own network and the scanning side."""

	import network

	own = network.WLAN(network.AP_IF)
	own.config(ssid="PicoClock rig", password="rig-test-only")
	own.active(True)

	scanner = network.WLAN(network.STA_IF)
	scanner.active(True)

	return own, scanner


def _load_wifi (finished) -> str:

	"""Run the Pico's own network and scan for others, back to back."""

	own, scanner = _radio_up()
	scans = 0
	longest = 0

	while not finished():

		began = time.ticks_ms()
		scanner.scan()
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
			scanner.scan()

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


def make_player (ratios, tempo: int):

	"""Make the real player with these ratios at this tempo, not yet started."""

	player = picoclock.device.player.Player(picoclock.device.board.FIRST_OUTPUT_PIN, len(ratios))
	player.set_tempo(tempo)

	for output in range(len(ratios)):
		player.set_ratio(output, ratios[output][0], ratios[output][1])

	player.settle()

	return player


def describe (status: dict) -> str:

	"""Write the player's own counts for a result line."""

	return "faults=%d refused=%d fewest_waiting=%d merged=%d" % (status["faults"], status["refused"], status["fewest_waiting"], status["merged"])


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
	passed = len(gaps) >= count - _SKIP and worst <= 2 and status["faults"] == 0 and status["refused"] == 0

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
	passed = len(gaps) >= count - _SKIP and in_range and status["faults"] == 0 and status["refused"] == 0

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
	passed = len(gaps) >= count - _SKIP and len(spans) == state[0] and state[0] > 0 and worst <= 2 and status["faults"] == 0 and status["refused"] == 0

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
	passed = state[0] > 0 and edges >= state[0] and left_high == 0 and status["faults"] == 0 and status["refused"] == 0

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
	passed = len(gaps) >= count - _SKIP and worst <= 2 and status["faults"] == 0 and status["refused"] == 0 and storage.saves > 20 and kept

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


TESTS = {
	"freerun": test_freerun,
	"player": test_player,
	"save": test_save,
	"saved": test_saved,
	"sweep": test_sweep,
	"pause": test_pause,
	"startstop": test_startstop,
}


def run (test: str, load: str = "idle") -> None:

	"""Run one test by name under one load by name."""

	TESTS[test](load)
