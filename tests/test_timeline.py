"""The scheduler stays exact over a long run of random changes.

The shared checks in `tests/checks/check_timeline.py` cover the named cases on
both Pythons. These run far longer and lean on `random`, so they stay here.
"""

import random

import picoclock.core.ratio
import picoclock.core.timeline

import tests.checks.check_timeline

UNITS = tests.checks.check_timeline.UNITS
SMALL = 2 ** 30


def random_ratio (rng: random.Random) -> tuple[int, int]:

	"""Pick a ratio someone might set: a snap value, a two-place decimal, or an odd fraction."""

	kind = rng.randrange(3)

	if kind == 0:
		return rng.choice(picoclock.core.ratio.SNAPS)

	if kind == 1:
		return picoclock.core.ratio.parse("%d.%02d" % (rng.randrange(0, 96), rng.randrange(1, 100)))

	while True:

		try:
			return picoclock.core.ratio.make(rng.randrange(1, 9601), rng.randrange(1, 9601))
		except ValueError:
			continue


def test_muldiv_matches_plain_arithmetic () -> None:

	"""The bit-by-bit multiply-and-divide agrees with ordinary whole-number arithmetic."""

	rng = random.Random(21)
	timeline = picoclock.core.timeline.Timeline(1)
	tried = 0

	while tried < 100000:

		each = rng.randrange(1, 288000001)
		divisor = rng.randrange(max(1, each // 16), min(288000000, each * 16) + 1)
		count = rng.randrange(0, 400000000)
		extra = rng.randrange(0, each)
		total = count * each + extra

		if total // divisor >= SMALL:
			continue

		timeline._muldiv(count, each, extra, divisor)

		assert (timeline._answer[0], timeline._answer[1]) == divmod(total, divisor)

		tried += 1


def test_gaps_are_exact_for_every_snap_value_at_the_ends_of_the_tempo_range () -> None:

	"""The gap between pulses is exactly beats x 6,000,000,000 / (pulses x tempo) ticks."""

	timeline = picoclock.core.timeline.Timeline(1)

	for tempo in (2000, 2001, 12000, 12345, 29999, 30000):

		timeline.set_tempo(tempo)

		for pulses, beats in picoclock.core.ratio.SNAPS + ((1, 128), (9599, 100), (9600, 101), (75, 9600)):

			timeline.set_ratio(0, pulses, beats)

			whole, part = divmod(beats * UNITS, pulses * tempo)

			assert timeline.period[0] == whole
			assert timeline.period_part[0] == part
			assert timeline.divisor[0] == pulses * tempo
			assert whole < SMALL


def test_a_long_run_of_random_changes_stays_exact () -> None:

	"""Through thousands of changes of every kind, every clock pulse is the next line of its own grid.

	Each pulse must sit within one tick of a grid line counted from zero, and
	be the line straight after the one before, so none is skipped or doubled.
	"""

	rng = random.Random(22)
	count = 8
	ratios = [(1, 1)] * count
	timeline = tests.checks.check_timeline.make(ratios, 12000)
	player = tests.checks.check_timeline.Player(timeline)
	follower = tests.checks.check_timeline.Follower(timeline, player, ratios)
	tempo_changes = 0
	ratio_changes = 0
	other_changes = 0
	pauses = 0

	timeline.start()

	for step in range(6000):

		player.play_for(rng.randrange(1, 250000))
		follower.follow()

		if rng.randrange(12) == 0:

			# A pause: nothing new may begin on any output, and changes made meanwhile still count.
			begun = [len(player.rises[output]) for output in range(count)]

			timeline.pause()
			player.play_for(rng.randrange(1, 60000))

			if rng.randrange(2) == 0:
				timeline.set_tempo(rng.randrange(2000, 30001))

			if rng.randrange(2) == 0:

				output = rng.randrange(1, count)
				ratios[output] = random_ratio(rng)
				timeline.set_ratio(output, ratios[output][0], ratios[output][1])

				if timeline.role[output] == picoclock.core.timeline.CLOCK and not timeline.muted[output]:
					follower.moved(output)

			player.play_for(rng.randrange(1, 60000))

			assert [len(player.rises[output]) for output in range(count)] == begun

			player.lead = timeline.resume()
			pauses += 1

		for values in (timeline.until, timeline.until_part, timeline.period, timeline.period_part, timeline.divisor, timeline.fall):
			assert max(values) < SMALL

		if rng.randrange(3) == 0:
			timeline.set_tempo(rng.randrange(2000, 30001))
			tempo_changes += 1

		if rng.randrange(4) == 0:

			output = rng.randrange(1, count)
			ratios[output] = random_ratio(rng)
			timeline.set_ratio(output, ratios[output][0], ratios[output][1])
			ratio_changes += 1

			if timeline.role[output] == picoclock.core.timeline.CLOCK and not timeline.muted[output]:
				follower.moved(output)

		if rng.randrange(6) == 0:

			output = rng.randrange(1, count)
			muted = not timeline.muted[output]
			timeline.set_mute(output, muted)
			other_changes += 1

			if not muted and timeline.role[output] == picoclock.core.timeline.CLOCK:
				follower.moved(output)

			if muted:
				follower.ignore(output)

		if rng.randrange(6) == 0:
			timeline.set_width(rng.randrange(0, count), rng.choice((0, 500, 2000, 10000, 60000)))
			other_changes += 1

		if rng.randrange(8) == 0:

			output = rng.randrange(1, count)
			role = rng.randrange(4)
			timeline.set_role(output, role)
			other_changes += 1

			if role == picoclock.core.timeline.CLOCK and not timeline.muted[output]:
				follower.moved(output)
			else:
				follower.ignore(output)

		# A muted output, and a start or stop pulse while the clock runs, are always low.
		pins = timeline.mask

		for output in range(count):
			if timeline.muted[output] or timeline.role[output] in (picoclock.core.timeline.START_PULSE, picoclock.core.timeline.STOP_PULSE):
				assert not (pins >> output) & 1, (step, output)

		if step % 50 == 0:

			# Working every clock's place out afresh from the beat must change nothing.
			before = tests.checks.check_timeline.state(timeline)

			for output in range(count):
				if timeline.role[output] == picoclock.core.timeline.CLOCK:
					timeline._place(output)

			assert tests.checks.check_timeline.state(timeline) == before

	assert tempo_changes > 1500
	assert ratio_changes > 1000
	assert other_changes > 2000
	assert pauses > 300
	assert sum(follower.seen) > 20000
	assert player.tick > 600 * 1000000

	# A line passed over without an edge is only ever one the scheduler counted as
	# running into the pulse before it, and with pulses cut short that is rare.
	assert follower.skipped == timeline.merged
	assert timeline.merged < 20


def test_the_densest_pattern_never_emits_a_word_too_long_or_too_short () -> None:

	"""With every output at its fastest and the tempo at its top, words still fit the player."""

	timeline = tests.checks.check_timeline.make([(96, 1), (64, 1), (48, 1), (32, 1), (24, 1), (16, 1), (12, 1), (8, 1)], 30000)
	player = tests.checks.check_timeline.Player(timeline)

	timeline.start()
	player.play(50000)

	# The longest hold at 150 cycles a tick, less the player's overhead, in the bits above the pins.
	assert player.largest < ((picoclock.core.timeline.HOLD_LIMIT * 150) << 9)
	assert len(player.rises[0]) > 5000
