"""The rig's test of the busiest outputs with MIDI's clock going, as SynthSync plays by default, on a real Pico.

It is a file of its own, loaded only when this test runs. How much is in the
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


def run (load: str = "idle") -> None:

	"""Time the fastest of four outputs whose edges crowd together, with MIDI's clock going: every gap must be exact and the player must never run short."""

	_bluetooth_already_on(load)

	ratio = rig._CLUSTERED[0]
	capture = rig.Capture(synthsync.device.board.FIRST_OUTPUT_PIN, _COUNT)
	player = rig.make_clustered_player(_TEMPO)

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

	worst = rig.worst_error(gaps, rig.gap_lengths(_TEMPO, ratio[0], ratio[1]))
	drifted = rig.drift(gaps, _TEMPO, ratio[0], ratio[1])
	passed = len(gaps) >= _COUNT - rig._SKIP and worst <= 2 and abs(drifted) <= rig._DRIFT_MOST and rig.sound(status)

	rig.report("clustermidi", passed, "pulses=%d worst_cycles=%d drift_cycles=%d %s load=%s" % (len(gaps), worst, drifted, rig.describe(status), described))
