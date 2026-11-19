"""The rig's tests of the busiest outputs with MIDI's clock going, as SynthSync plays by default, on a real Pico.

`clustermidi` has four outputs whose edges crowd together, and `eight` has all
eight outputs as busy as the page can make them, so it drives GP2 to GP9.

It is a file of its own, loaded only when these tests run. How much is in the
Pico's memory changes how much room the player has at its busiest, so the rig's
other tests shouldn't have to carry it.
"""

import time

import rig
import synthsync.core.timeline
import synthsync.device.board

_TEMPO = 30000
_COUNT = 4000
_SECONDS = 30

# The page's busiest on eight outputs: output 1 sends the clock at 48 a beat,
# the most a clock can be, and outputs 2 to 8 are as fast as outputs may be and
# nearly in step.
_EIGHT = ((48, 1),) + tuple((96 - output, 1) for output in range(7))

# The longest a look at the buttons may take on average with nothing changing,
# in microseconds. Timed this way, the quick look takes about 24 and taking
# both buttons' rule through about 91.
_LOOK_MOST_US = 50

# The loads that use Bluetooth, and how long it is left to settle, in milliseconds.
_BLUETOOTH_LOADS = ("ble", "all")
_BLUETOOTH_SETTLES = 2000


def _bluetooth_already_on (load: str) -> None:

	"""Turn Bluetooth on before the clock starts, if the load is going to use it.

	SynthSync never uses Bluetooth, so it never starts it with the clock
	playing. Doing that holds the second core up for longer than anything
	SynthSync does, and among outputs this busy it empties the player whatever
	else is true of the code. With Bluetooth on first, the load's use of it is
	still in the test.
	"""

	if load in _BLUETOOTH_LOADS:

		import bluetooth

		bluetooth.BLE().active(True)
		time.sleep_ms(_BLUETOOTH_SETTLES)


def _look_us (player) -> int:

	"""Time the player's look at the buttons with nothing changing, as the second core makes one about once a millisecond, and give the average in microseconds.

	It is timed once the player is closed, so the second core has finished
	with it. With nothing changing, the look returns at once: taking both
	buttons' rule through takes about a tenth of a millisecond, longer than
	the eight words in hand last among the busiest outputs.
	"""

	looks = 200
	began = time.ticks_us()

	for _ in range(looks):
		player._look(1)

	return time.ticks_diff(time.ticks_us(), began) // looks


def run (load: str = "idle") -> None:

	"""Time the fastest of four outputs whose edges crowd together, with MIDI's clock going: every gap must be exact and the player must never run short."""

	_time("clustermidi", rig._CLUSTERED, load)


def run_eight (load: str = "idle") -> None:

	"""Time output 1 while all eight outputs are as busy as the page can make them, with MIDI's clock going: every gap must be exact and the player must never run short."""

	_time("eight", _EIGHT, load)


def _time (name: str, ratios, load: str) -> None:

	"""Play these ratios with 1 ms pulses and MIDI's clock going, time output 1, and report it under this name."""

	_bluetooth_already_on(load)

	ratio = ratios[0]
	capture = rig.Capture(synthsync.device.board.FIRST_OUTPUT_PIN, _COUNT)
	player = rig.make_player(ratios, _TEMPO)

	for output in range(len(ratios)):
		player.set_width(output, 1000)

	player.set_midi(synthsync.core.timeline.MIDI_ALWAYS)
	player.settle()
	player.start()

	try:
		described = rig.run_load(load, capture, _SECONDS)
		status = player.status()
		gaps = capture.gaps()

	finally:
		player.close()
		capture.close()

	look = _look_us(player)
	worst = rig.worst_error(gaps, rig.gap_lengths(_TEMPO, ratio[0], ratio[1]))
	drifted = rig.drift(gaps, _TEMPO, ratio[0], ratio[1])
	passed = len(gaps) >= _COUNT - rig._SKIP and worst <= 2 and abs(drifted) <= rig._DRIFT_MOST and look <= _LOOK_MOST_US and rig.sound(status)

	rig.report(name, passed, "pulses=%d worst_cycles=%d drift_cycles=%d look_us=%d %s load=%s" % (len(gaps), worst, drifted, look, rig.describe(status), described))
