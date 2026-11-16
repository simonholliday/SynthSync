"""A pause holds every output exactly where it was, on the Pico's Python as on a laptop's."""

import gc
import sys

import picoclock.core.timeline

import tests.checks.check_timeline

RATIOS = [(1, 1), (3, 1), (137, 100), (24, 1)]


def twins (tempo: int) -> tuple[tests.checks.check_timeline.Player, tests.checks.check_timeline.Player]:

	"""Make two identical clocks, both started: one to leave alone and one to pause."""

	plain = tests.checks.check_timeline.Player(tests.checks.check_timeline.make(RATIOS, tempo))
	held = tests.checks.check_timeline.Player(tests.checks.check_timeline.make(RATIOS, tempo))

	plain.timeline.start()
	held.timeline.start()

	return plain, held


def check_a_pause_moves_every_later_pulse_by_exactly_its_own_length () -> None:

	"""Pulses before a pause are untouched, and each one after comes later by precisely how long it lasted."""

	plain, held = twins(12345)

	plain.play_until(3000000)
	held.play_until(700000)

	paused_at = held.tick
	held.timeline.pause()
	held.play_for(123456)
	waited = held.tick - paused_at

	assert held.timeline.resume() == 0

	held.play_until(3000000 + waited)

	for output in range(len(RATIOS)):

		before = [tick for tick in plain.rises[output] if tick < paused_at]
		after = [tick + waited for tick in plain.rises[output] if tick >= paused_at]
		heard = held.rises[output]

		assert heard[:len(before)] == before
		assert len(after) > 2

		# The last one or two may fall just past where the paused clock was played to.
		assert heard[len(before):len(before) + len(after) - 2] == after[:-2]


def check_a_pulse_already_begun_finishes_and_no_new_one_begins () -> None:

	"""During a pause the pins go low when their pulses end, as they would have, and stay low."""

	plain, held = twins(12000)

	plain.play_until(600000)

	# Stop part-way through the pulses that begin on the second beat, at 500 ms.
	# Three of the four outputs pulse there: 137 to 100 has no line on that beat.
	held.play_until(502500)

	assert held.tick == 502500
	assert held.pins == 0b1011 | picoclock.core.timeline.LED_BIT

	paused_at = held.tick
	begun = [len(held.rises[output]) for output in range(len(RATIOS))]

	held.timeline.pause()

	assert not held.timeline.quiet()

	# The pulses end within 10 ms, and the tempo LED's flash, lit for a quarter of a beat up to 100 ms, after them.
	held.play_for(40000)

	assert held.pins == picoclock.core.timeline.LED_BIT

	# Nothing is left to play only once the flash is over, or the player would stop with the LED lit.
	assert not held.timeline.quiet()

	held.play_for(60000)

	assert held.timeline.quiet()
	assert held.pins == 0

	for output in range(len(RATIOS)):
		assert len(held.rises[output]) == begun[output]

	for output in (0, 1, 3):

		# Each pulse in flight ended on the tick it would have anyway.
		ends = [tick for tick in plain.falls[output] if tick > paused_at]
		assert held.falls[output][-1] == ends[0]


def check_run_outputs_drop_for_a_pause_and_lead_the_clocks_back () -> None:

	"""A run output goes low the moment the clock pauses, and high again 10 ms before the clocks carry on."""

	timeline = tests.checks.check_timeline.make([(2, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	assert timeline.start() == 10000

	player.play_until(400000)

	# The clock's pulses so far: 10 ms after the start, and every 250 ms from there.
	assert player.rises[0] == [10000, 260000]

	paused_at = player.tick
	timeline.pause()
	player.play(1)

	assert (player.pins >> 1) & 1 == 0
	assert player.falls[1] == [paused_at]

	player.play_for(50000)
	resumed_at = player.tick

	assert timeline.resume() == 10000

	player.play_until(resumed_at + 400000)

	assert player.rises[1] == [0, resumed_at]

	# The next pulse was 110 ms off when the clock paused, and comes that long after the lead.
	assert player.rises[0][2] == resumed_at + 10000 + (510000 - paused_at)
	assert player.rises[0][3] == player.rises[0][2] + 250000


def check_changes_made_during_a_pause_take_hold_when_the_clock_carries_on () -> None:

	"""A tempo or ratio changed while paused leaves every pulse on its grid afterwards."""

	ratios = [(1, 1), (3, 1), (4, 3)]
	timeline = tests.checks.check_timeline.make(ratios, 12000)
	player = tests.checks.check_timeline.Player(timeline)
	follower = tests.checks.check_timeline.Follower(timeline, player, ratios)

	timeline.start()
	player.play_for(1300000)
	follower.follow()

	timeline.pause()
	player.play_for(30000)

	timeline.set_tempo(8765)
	ratios[1] = (24, 1)
	timeline.set_ratio(1, 24, 1)
	follower.moved(1)

	player.play_for(30000)
	timeline.resume()
	player.play_for(3000000)
	follower.follow()

	assert follower.skipped == 0
	assert follower.seen[0] > 5
	assert follower.seen[1] > 50

	# Placing every output afresh from the beat still changes nothing.
	before = tests.checks.check_timeline.state(timeline)

	for output in range(len(ratios)):
		timeline._place(output)

	assert tests.checks.check_timeline.state(timeline) == before


def check_a_role_changed_during_a_pause_waits_for_the_clock () -> None:

	"""An output made a run output while paused stays low until the clock carries on."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1)], 12000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play_for(100000)
	timeline.pause()
	player.play_for(20000)

	timeline.set_role(1, picoclock.core.timeline.RUN)
	player.play_for(20000)

	assert (player.pins >> 1) & 1 == 0

	timeline.set_mute(1, True)
	timeline.set_mute(1, False)
	player.play_for(20000)

	assert (player.pins >> 1) & 1 == 0

	assert timeline.resume() == 10000

	player.play(1)

	assert (player.pins >> 1) & 1 == 1


def check_a_lead_is_plain_time_whatever_changes_during_it () -> None:

	"""A tempo or ratio changed during the 10 ms lead doesn't move the first beat."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	assert timeline.start() == 10000

	player.play_until(5000)

	assert player.tick == 5000

	timeline.set_tempo(24000)
	timeline.set_ratio(2, 4, 1)
	player.play_until(600000)

	# The first beat is still 10 ms after the start, and the rest follow at the new tempo.
	assert player.rises[0][:3] == [10000, 260000, 510000]
	assert player.rises[2][:3] == [10000, 72500, 135000]


def check_a_lead_after_a_pause_is_plain_time_too () -> None:

	"""Changes made in the 10 ms after carrying on leave every pulse on its grid, and none comes early."""

	ratios = [(1, 1), (1, 1), (3, 1)]
	timeline = tests.checks.check_timeline.make(ratios, 12000)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)
	follower = tests.checks.check_timeline.Follower(timeline, player, ratios)
	follower.ignore(1)

	player.lead = timeline.start()
	player.play_for(700000)
	follower.follow()

	timeline.pause()
	player.play_for(50000)

	resumed_at = player.tick
	player.lead = timeline.resume()

	assert player.lead == 10000

	player.play_until(resumed_at + 5000)
	follower.follow()

	# Part-way through the lead: a faster tempo, and a much faster ratio on output 3.
	timeline.set_tempo(29000)
	ratios[2] = (96, 1)
	timeline.set_ratio(2, 96, 1)
	follower.moved(2)

	begun = len(player.rises[2])

	player.play_until(resumed_at + 10000)

	# Nothing begins before the lead is over.
	assert len(player.rises[2]) == begun

	player.play_for(900000)
	follower.follow()

	assert follower.skipped == 0
	assert len(player.rises[2]) > begun + 100
	assert player.rises[2][begun] >= resumed_at + 10000


def check_a_lead_cut_short_by_a_pause_is_waited_out_afterwards () -> None:

	"""If the clock is paused during the lead after a start pulse, what's left of the lead is kept."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.START_PULSE)
	player = tests.checks.check_timeline.Player(timeline)

	assert timeline.start() == 10000

	player.play_until(2500)

	assert player.tick == 2500

	timeline.pause()
	player.play_for(30000)

	resumed_at = player.tick

	# 7.5 ms of the lead was still to run.
	assert timeline.resume() == 7500

	player.play_for(600000)

	assert player.rises[0][:2] == [resumed_at + 7500, resumed_at + 507500]


def check_position_holds_through_a_pause_and_stands_still_through_a_lead () -> None:

	"""Where the clock says it is doesn't move while it is paused, or while clocks wait for a run output."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.RUN)
	player = tests.checks.check_timeline.Player(timeline)

	assert timeline.start() == 10000

	player.play_until(5000)

	# Still in the lead, so the first beat hasn't begun.
	assert timeline.position() == -1

	player.play_until(135000)

	assert player.tick == 135000
	assert timeline.position() == 250

	timeline.pause()
	player.play_for(40000)

	assert timeline.position() == 250

	assert timeline.resume() == 10000

	player.play_for(5000)

	# Half-way through the lead after carrying on, and the beat hasn't moved.
	assert timeline.position() == 250

	player.play_for(5000 + 125000)

	assert timeline.position() == 500

	timeline.pause()
	timeline.stop()

	assert timeline.position() == -1


def check_position_is_not_given_while_a_change_is_half_made () -> None:

	"""Every change to where the clock is counts twice, and a position read part-way through one is withheld."""

	timeline = tests.checks.check_timeline.make([(1, 1), (2, 1)], 12000)
	player = tests.checks.check_timeline.Player(timeline)
	counted = [timeline.changes]

	def moved () -> bool:

		"""Say whether the count has moved on by a whole change since it was last looked at."""

		before = counted[0]
		counted[0] = timeline.changes

		return timeline.changes == before + 2

	assert timeline.changes % 2 == 0

	timeline.start()
	assert moved()

	player.play_until(125000)
	assert not moved()
	assert timeline.position() == 250

	timeline.set_tempo(24000)
	assert moved()

	timeline.pause()
	assert moved()

	timeline.resume()
	assert moved()

	assert timeline.position() == 250

	# As the second core leaves things half-way through any of those.
	timeline.changes += 1

	assert timeline.position() == -1

	timeline.changes += 1

	assert timeline.position() == 250
	assert moved()

	timeline.stop()
	assert moved()


def check_pause_and_resume_out_of_turn_change_nothing () -> None:

	"""Pausing a stopped or paused clock, or resuming one that isn't paused, does nothing."""

	timeline = tests.checks.check_timeline.make([(1, 1), (2, 1)], 12000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.pause()

	assert not timeline.paused
	assert timeline.resume() == 0

	timeline.start()
	player.play_for(100000)

	assert timeline.resume() == 0

	timeline.pause()
	before = tests.checks.check_timeline.state(timeline) + list(timeline.held)
	timeline.pause()

	assert tests.checks.check_timeline.state(timeline) + list(timeline.held) == before


def check_stopping_or_starting_ends_a_pause () -> None:

	"""A paused clock that is stopped is simply stopped, and one that is started begins again at zero."""

	timeline = tests.checks.check_timeline.make([(1, 1), (1, 1)], 12000)
	timeline.set_role(1, picoclock.core.timeline.STOP_PULSE)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play_for(100000)
	timeline.pause()
	player.play_for(20000)

	stopped_at = player.tick
	timeline.stop()

	assert not timeline.paused
	assert not timeline.running

	player.play_for(20000)

	assert player.rises[1] == [stopped_at]
	assert timeline.quiet()

	timeline.start()
	timeline.pause()
	started_at = player.tick

	assert timeline.start() == 0
	assert not timeline.paused

	player.play(1)

	assert player.rises[0][-1] == started_at


def check_pausing_asks_for_no_memory () -> None:

	"""On the Pico's Python, pausing, changing things while paused and carrying on leave the heap untouched."""

	if sys.implementation.name != "micropython":
		return

	# Only MicroPython's gc can say how much of the heap is in use.
	used = getattr(gc, "mem_alloc")

	timeline = tests.checks.check_timeline.make([(1, 1), (24, 1), (4, 3), (2, 1)], 12000)
	timeline.set_role(3, picoclock.core.timeline.RUN)
	next_word = timeline.next_word

	timeline.start()

	for _ in range(20):
		next_word()

	gc.collect()
	before = used()

	for step in range(200):

		timeline.pause()
		next_word()
		timeline.set_tempo(9000 + step)
		timeline.set_ratio(1, 3 + step % 5, 2)
		timeline.quiet()
		timeline.resume()
		next_word()
		next_word()

	assert used() == before, used() - before
