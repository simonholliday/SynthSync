"""The rig's test of the widest tempo range, 1 to 1000 BPM, on a real Pico.

It is a file of its own, loaded only when this test runs. How much is in the
Pico's memory changes how much room the player has at its busiest, so the rig's
other tests shouldn't have to carry it.
"""

import time

import rig
import synthsync.core.limits
import synthsync.core.timeline
import synthsync.device.board
import synthsync.device.player

# The busiest and the slowest the widest tempo range allows, on four outputs.
# Whichever comes first is the one timed.
_FASTEST_ALLOWED = ((28, 1), (27, 1), (7, 3), (1, 6))
_SLOWEST_ALLOWED = ((1, 6), (28, 1), (27, 1), (7, 3))

# How many tempo jumps the range is put through, how far apart in milliseconds
# at the least, and how long they are given in seconds: under the heaviest load
# the main loop comes up for air only every 100 ms or so.
_JUMPS = 50
_JUMP_EVERY = 20
_JUMP_SECONDS = 12
_JUMP_GAPS = 6000


def _play (ratios, tempo: int, count: int, load: str, seconds: int, meanwhile=None) -> tuple:

	"""Play the widest range's player at this tempo with MIDI going, time output 1, and give how it went."""

	board = synthsync.device.board
	limits = synthsync.core.limits.Limits(synthsync.core.limits.SLOWEST_TEMPO, synthsync.core.limits.FASTEST_TEMPO)
	capture = rig.Capture(board.FIRST_OUTPUT_PIN, count)
	player = synthsync.device.player.Player(board.FIRST_OUTPUT_PIN, len(ratios), limits)

	rig.listen_to_buttons(player)
	player.set_tempo(tempo)

	for output in range(len(ratios)):
		player.set_ratio(output, ratios[output][0], ratios[output][1])

	player.set_midi(synthsync.core.timeline.MIDI_ALWAYS)
	player.settle()
	player.start()

	try:
		rig.run_load(load, capture, seconds, (lambda: meanwhile(player)) if meanwhile is not None else None)
		status = player.status()
		timed, worst = capture.judge(rig.gap_lengths(tempo, ratios[0][0], ratios[0][1]))
		drifted = capture.drift(tempo, ratios[0][0], ratios[0][1])

	finally:
		player.close()
		capture.close()

	return timed, worst, drifted, status


def run (load: str = "idle") -> None:

	"""Play both ends of the range at the fastest and slowest ratios it allows, with MIDI going, and jump between them.

	Every gap has to be exact at each, and the player must never run dry.
	"""

	fast = _play(_FASTEST_ALLOWED, 100000, 2000, load, 30)
	slow = _play(_FASTEST_ALLOWED, 100, 6, load, 30)
	long_gap = _play(_SLOWEST_ALLOWED, 100000, 12, load, 30)

	state = [0, time.ticks_ms()]

	def jump (player) -> None:

		"""Jump from one end of the range to the other, at most every so often."""

		if state[0] < _JUMPS and time.ticks_diff(time.ticks_ms(), state[1]) >= _JUMP_EVERY:
			state[0] += 1
			state[1] = time.ticks_ms()
			player.set_tempo(100 if state[0] % 2 else 100000)

	# The gaps aren't judged here, since the tempo keeps changing under them.
	jumped = _play(_FASTEST_ALLOWED, 100000, _JUMP_GAPS, load, _JUMP_SECONDS, jump)
	runs = (fast, slow, long_gap, jumped)
	timed_well = True

	for each, wanted in ((fast, 1990), (slow, 4), (long_gap, 10)):
		timed_well = timed_well and each[0] >= wanted and each[1] <= 2 and abs(each[2]) <= rig._DRIFT_MOST and rig.sound(each[3])

	passed = timed_well and state[0] == _JUMPS and rig.sound(jumped[3])

	rig.report("extremes", passed, "fast_pulses=%d fast_worst=%d fast_drift=%d slow_pulses=%d slow_worst=%d slow_drift=%d long_pulses=%d long_worst=%d long_drift=%d jumps=%d fewest_waiting=%d slowest_command_us=%d faults=%d load=%s" % (fast[0], fast[1], fast[2], slow[0], slow[1], slow[2], long_gap[0], long_gap[1], long_gap[2], state[0], min(each[3]["fewest_waiting"] for each in runs), max(each[3]["slowest_command"] for each in runs), sum(each[3]["faults"] for each in runs), load))
