"""Mutes, pulse lengths and roles behave the same on the Pico's Python as on a laptop's."""

import gc
import sys

import picoclock.core.timeline

import tests.checks.check_timeline

UNITS = tests.checks.check_timeline.UNITS


def pin (player: tests.checks.check_timeline.Player, output: int) -> int:

	"""Say whether an output's pin is high in the word just played."""

	return (player.pins >> output) & 1


def refuses (timeline: picoclock.core.timeline.Timeline, what: str, output: int, value: int) -> bool:

	"""Try one change and say whether it was refused."""

	try:
		if what == "role":
			timeline.set_role(output, value)
		elif what == "width":
			timeline.set_width(output, value)
		else:
			timeline.set_mute(output, bool(value))

	except ValueError:
		return True

	return False


def check_a_muted_clock_stays_low_and_comes_back_in_step () -> None:

	"""A muted output makes no pulses, and when unmuted its next pulse is the next line of its grid."""

	timeline = tests.checks.check_timeline.make([(1, 1), (4, 1)], 12000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(40)
	timeline.set_mute(1, True)

	heard = len(player.rises[1])
	main = len(player.rises[0])

	player.play(300)

	assert len(player.rises[1]) == heard
	assert len(player.rises[0]) > main
	assert pin(player, 1) == 0

	timeline.set_mute(1, False)

	next_line = -((-player.position * 4) // UNITS)

	player.play(300)

	assert len(player.rises[1]) > heard

	for number in range(heard, len(player.rises[1])):
		assert tests.checks.check_timeline.on_grid(player.rise_positions[1][number], 12000, 4, 1) == next_line + number - heard


def check_muting_part_way_through_a_pulse_drops_the_pin_at_once () -> None:

	"""A mute doesn't wait for the pulse to end."""

	timeline = tests.checks.check_timeline.make([(24, 1), (96, 1)], 10000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(1)

	assert pin(player, 0) == 1

	timeline.set_mute(0, True)
	player.play(1)

	assert pin(player, 0) == 0
	assert player.falls[0] == [2500]


def check_a_pulse_can_be_given_a_length_or_set_to_half () -> None:

	"""An output's pulses last as long as it's told, cut to half the gap when that won't fit."""

	# At 120 BPM, four pulses a beat are 125 ms apart.
	timeline = tests.checks.check_timeline.make([(4, 1), (4, 1), (4, 1), (4, 1)], 12000)
	timeline.set_width(1, 2000)
	timeline.set_width(2, 0)
	timeline.set_width(3, 400000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(200)

	assert player.falls[0][0] - player.rises[0][0] == 10000
	assert player.falls[1][0] - player.rises[1][0] == 2000
	assert player.falls[2][0] - player.rises[2][0] == 62500
	assert player.falls[3][0] - player.rises[3][0] == 62500

	# A new length takes hold at the next pulse.
	timeline.set_width(1, 5000)
	player.play(200)

	assert player.falls[1][-1] - player.rises[1][len(player.falls[1]) - 1] == 5000


def check_a_start_pulse_and_a_run_output_lead_the_clocks_by_ten_milliseconds () -> None:

	"""A start pulse fires and a run output goes high at once, and the clocks' first beat is 10 ms later."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1), (1, 1), (2, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.START_PULSE)
	timeline.set_role(2, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(1)

	assert player.pins == 0b0110

	player.play_until(4000000)

	# The start pulse is one 10 ms pulse and no more.
	assert player.rises[1] == [0]
	assert player.falls[1] == [10000]

	# The run output goes up once and stays up.
	assert player.rises[2] == [0]
	assert player.falls[2] == []

	# The clocks begin together 10 ms in, and keep exact time from there.
	for number in range(len(player.rises[0])):
		assert player.rises[0][number] == 10000 + number * 500000

	for number in range(len(player.rises[3])):
		assert player.rises[3][number] == 10000 + number * 250000

	assert len(player.rises[0]) > 5


def check_either_a_start_pulse_or_a_run_output_alone_is_enough_to_lead () -> None:

	"""One start pulse, or one run output, holds the first beat back by 10 ms."""

	for role in (picoclock.core.timeline.START_PULSE, picoclock.core.timeline.RUN):

		timeline = tests.checks.check_timeline.make([(1, 1), (1, 1)], 12000)
		timeline.set_role(1, role)
		player = tests.checks.check_timeline.Player(timeline)

		timeline.start()
		player.play_until(600000)

		assert player.rises[1] == [0]
		assert player.rises[0][0] == 10000
		assert player.rises[0][1] == 510000


def check_clocks_start_at_once_when_there_is_nothing_to_follow () -> None:

	"""With no start pulse or run output, or with them muted, the first beat is immediate."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.START_PULSE)
	timeline.set_role(2, picoclock.core.timeline.RUN)
	timeline.set_mute(1, True)
	timeline.set_mute(2, True)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(100)

	assert player.rises[0][0] == 0
	assert player.rises[1] == []
	assert player.rises[2] == []


def check_a_stop_pulse_fires_when_the_clock_stops () -> None:

	"""Stopping sets everything low except a stop pulse, which fires once and then goes quiet."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.STOP_PULSE)
	timeline.set_role(2, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(30)

	assert player.rises[1] == []
	assert pin(player, 2) == 1

	timeline.stop()

	assert not timeline.quiet()

	stopped_at = player.tick

	player.play(1)

	assert player.pins == 0b010

	player.play_for(12000)

	assert timeline.quiet()
	assert player.pins == 0
	assert player.rises[1] == [stopped_at]
	assert player.falls[1] == [stopped_at + 10000]
	assert player.falls[2] == [stopped_at]

	# And nothing more plays until the next start.
	player.play(20)

	assert player.pins == 0
	assert len(player.rises[0]) == len(player.falls[0])


def check_changing_what_an_output_is_for_leaves_nothing_stuck_high () -> None:

	"""An output changing role goes low first, a new run output goes high, and a new clock joins its grid."""

	timeline = tests.checks.check_timeline.make([(1, 1), (3, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	player.lead = timeline.start()
	player.play(25)

	assert pin(player, 1) == 1

	# Run to clock: low at once, then on the grid of three pulses a beat.
	timeline.set_role(1, picoclock.core.timeline.CLOCK)

	next_line = -((-player.position * 3) // UNITS)
	before = len(player.rises[1])

	player.play(1)

	assert pin(player, 1) == 0

	player.play_for(1500000)

	for number in range(before, len(player.rises[1])):
		assert tests.checks.check_timeline.on_grid(player.rise_positions[1][number], 12000, 3, 1) == next_line + number - before

	assert len(player.rises[1]) > before + 3

	# Clock to run, part-way through: high at once and held.
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player.play(60)

	assert pin(player, 1) == 1

	# Run to start pulse: low, and it stays low until the next start.
	timeline.set_role(1, picoclock.core.timeline.START_PULSE)
	player.play(60)

	assert pin(player, 1) == 0

	# Stop pulse set while running does nothing until the clock stops.
	timeline.set_role(1, picoclock.core.timeline.STOP_PULSE)
	player.play(60)

	assert pin(player, 1) == 0


def check_a_role_set_while_stopped_is_used_at_the_next_start () -> None:

	"""Roles, mutes and lengths can all be set before the clock starts."""

	timeline = picoclock.core.timeline.Timeline(3)
	timeline.set_role(1, picoclock.core.timeline.START_PULSE)
	timeline.set_width(1, 3000)
	timeline.set_role(2, picoclock.core.timeline.STOP_PULSE)
	timeline.set_width(2, 0)
	player = tests.checks.check_timeline.Player(timeline)

	assert timeline.quiet()

	timeline.start()
	player.play(50)

	assert player.falls[1] == [3000]

	stopped_at = player.tick
	timeline.stop()
	player.play(5)

	# A stop pulse set to "half the gap" has no gap to halve, so it lasts the usual 10 ms.
	assert player.falls[2] == [stopped_at + 10000]


def check_run_pins_names_the_run_outputs_that_sound () -> None:

	"""The run outputs that aren't muted are reported as their pins."""

	timeline = picoclock.core.timeline.Timeline(4)

	assert timeline.run_pins() == 0

	timeline.set_role(1, picoclock.core.timeline.RUN)
	timeline.set_role(3, picoclock.core.timeline.RUN)

	assert timeline.run_pins() == 0b1010

	timeline.set_mute(3, True)

	assert timeline.run_pins() == 0b0010


def check_a_mute_on_a_run_output_follows_the_clock () -> None:

	"""A run output unmuted while the clock runs goes high, and stays low if the clock is stopped."""

	timeline = picoclock.core.timeline.Timeline(2)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	timeline.set_mute(1, True)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.set_mute(1, False)
	player.play(2)

	assert pin(player, 1) == 0

	timeline.start()
	player.play(5)

	assert pin(player, 1) == 1

	timeline.set_mute(1, True)
	player.play(2)

	assert pin(player, 1) == 0

	timeline.set_mute(1, False)
	player.play(2)

	assert pin(player, 1) == 1


def check_changes_the_clock_cannot_make_are_refused () -> None:

	"""A role, a length or an output that doesn't exist is refused."""

	timeline = picoclock.core.timeline.Timeline(2)

	assert refuses(timeline, "role", 1, 4)
	assert refuses(timeline, "role", 1, -1)
	assert refuses(timeline, "role", 2, picoclock.core.timeline.RUN)
	assert refuses(timeline, "width", 1, -1)
	assert refuses(timeline, "width", 1, 1000001)
	assert refuses(timeline, "width", 5, 1000)
	assert refuses(timeline, "mute", 2, 1)
	assert not refuses(timeline, "width", 1, 0)
	assert not refuses(timeline, "role", 1, picoclock.core.timeline.STOP_PULSE)


def check_roles_and_mutes_ask_for_no_memory () -> None:

	"""On the Pico's Python, starting, stopping, muting and changing roles leave the heap untouched."""

	if sys.implementation.name != "micropython":
		return

	# Only MicroPython's gc can say how much of the heap is in use.
	used = getattr(gc, "mem_alloc")

	timeline = tests.checks.check_timeline.make([(1, 1), (24, 1), (4, 3), (2, 1)], 12000)
	next_word = timeline.next_word

	timeline.start()

	for _ in range(20):
		next_word()

	gc.collect()
	before = used()

	for step in range(200):

		timeline.set_mute(1, step % 2 == 0)
		timeline.set_role(2, step % 4)
		timeline.set_width(3, (step % 5) * 1000)
		next_word()

		if step % 20 == 19:
			timeline.stop()
			next_word()
			timeline.quiet()
			timeline.run_pins()
			timeline.start()

	assert used() == before, used() - before
