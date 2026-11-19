"""A tempo knob's readings mean the right tempos, and the knob and the page share the tempo as they should, on either Python."""

import synthsync.core.knob
import synthsync.core.limits

FULL = synthsync.core.knob.FULLEST
MIDDLE = FULL // 2 + 1
NOT_NOTED = -1


def reading_for (knob: synthsync.core.knob.Knob, tempo: int) -> int:

	"""Find the reading that points closest to a tempo."""

	low = 0
	high = FULL

	while low < high:

		middle = (low + high) // 2

		if knob.wanted(middle) < tempo:
			low = middle + 1
		else:
			high = middle

	return low


def following_at (tempo: int) -> "tuple[synthsync.core.knob.Knob, int]":

	"""Make a knob that has the tempo, resting on this tempo, and give its reading."""

	knob = synthsync.core.knob.Knob()
	reading = reading_for(knob, tempo)

	assert knob.start(reading, tempo, reading) is None
	assert knob.following

	return knob, reading


def let_go_at (tempo: int, reading: int) -> synthsync.core.knob.Knob:

	"""Make a knob at this reading that doesn't have this tempo: the page set it."""

	knob = synthsync.core.knob.Knob()

	assert knob.start(reading, tempo, reading) is None
	assert not knob.following

	return knob


def check_the_usual_range_has_its_marks_at_the_quarters () -> None:

	"""20, 80, 120, 180 and 300 BPM, with 120 in the middle of the travel."""

	knob = synthsync.core.knob.Knob()
	dead = 61
	span = FULL - 2 * dead

	for quarter, tempo in enumerate((2000, 8000, 12000, 18000, 30000)):
		assert knob.tempo_at(dead + span * quarter // 4 + (1 if quarter else 0)) == tempo, quarter


def check_both_ends_of_the_range_can_be_reached () -> None:

	"""The last of the travel at each end is the very end of the range, in any range."""

	# The last of these aren't whole numbers of the range's steps, so rounding wouldn't reach them.
	for slowest, fastest in ((2000, 30000), (100, 100000), (10053, 11022)):

		knob = synthsync.core.knob.Knob(synthsync.core.limits.Limits(slowest, fastest))

		for reading in (0, 4, 40):
			assert knob.tempo_at(reading) == slowest

		for reading in (FULL, 4083, 4050):
			assert knob.tempo_at(reading) == fastest


def check_other_ranges_have_the_same_shape () -> None:

	"""The middle of the travel sits as far along any range, by proportion."""

	wide = synthsync.core.knob.Knob(synthsync.core.limits.Limits(100, 100000))
	narrow = synthsync.core.knob.Knob(synthsync.core.limits.Limits(10000, 11000))

	assert 9500 <= wide.wanted(MIDDLE) <= 9800
	assert 10640 <= narrow.wanted(MIDDLE) <= 10660


def check_the_tempo_only_ever_rises_along_the_travel () -> None:

	"""Turning the knob one way never moves the tempo the other."""

	for limits in (synthsync.core.limits.DEFAULT, synthsync.core.limits.Limits(100, 100000), synthsync.core.limits.Limits(10000, 11000)):

		knob = synthsync.core.knob.Knob(limits)
		last = 0

		for reading in range(0, FULL + 1, 7):

			tempo = knob.tempo_at(reading)

			assert tempo >= last, reading
			assert limits.slowest_tempo <= tempo <= limits.fastest_tempo

			last = tempo


def check_the_steps_suit_the_range_and_the_knob () -> None:

	"""Whole BPM in the usual range, tenths in a narrow one, and larger where a wide range is steep."""

	usual = synthsync.core.knob.Knob()

	for reading in range(0, FULL + 1, 50):
		assert usual.step(reading) == 100, reading

	narrow = synthsync.core.knob.Knob(synthsync.core.limits.Limits(10000, 11000))

	assert narrow.step(MIDDLE) == 10
	assert narrow.tempo_at(MIDDLE) % 10 == 0

	wide = synthsync.core.knob.Knob(synthsync.core.limits.Limits(100, 100000))

	assert wide.step(300) == 100
	assert wide.step(3900) >= 500


def check_a_knob_with_the_tempo_follows_the_hand () -> None:

	"""Every clear turn moves the tempo, to the whole BPM it points at."""

	knob, reading = following_at(12000)

	moved = knob.look(reading + 200, 12000)

	assert moved == knob.tempo_at(reading + 200)
	assert moved is not None and moved > 12000 and moved % 100 == 0
	assert knob.look(reading + 200, moved) is None


def check_a_wobble_leaves_the_tempo_alone () -> None:

	"""A few readings either way, even on the edge between two BPM, moves it at most once."""

	knob = synthsync.core.knob.Knob()
	reading = 0

	# The first reading past the middle of two whole BPM, near the steep end of the travel.
	for each in range(3500, 3900):
		if knob.tempo_at(each) != knob.tempo_at(each - 1):
			reading = each
			break

	tempo = knob.tempo_at(reading - 1)

	assert knob.start(reading - 1, tempo, reading - 1) is None

	changes = 0

	for turn in range(200):

		found = knob.look(reading - 1 + turn % 3, tempo)

		if found is not None:
			tempo = found
			changes += 1

	assert changes <= 1


def check_a_change_from_elsewhere_lets_go_of_the_knob () -> None:

	"""The page sets the tempo, and the knob, however it is nudged, doesn't touch it."""

	knob, reading = following_at(12000)

	assert knob.look(reading + 3, 15000) is None
	assert not knob.following

	for nudge in (0, 20, -40, synthsync.core.knob.MOVED - 3):
		assert knob.look(reading + 3 + nudge, 15000) is None

	assert not knob.following


def check_a_resting_knob_on_the_tempo_cannot_take_it_over () -> None:

	"""Reading the same as the tempo isn't enough: it has to have been moved."""

	knob = synthsync.core.knob.Knob()
	reading = reading_for(knob, 12000)

	knob.start(reading, 15000, reading)

	# The page has now set the very tempo the knob happens to point at.
	for wobble in (0, 2, -2, 1, -1):
		assert knob.look(reading + wobble, 12000) is None

	assert not knob.following


def check_a_moved_knob_takes_over_only_on_reaching_the_tempo () -> None:

	"""It does nothing on the way, and the tempo doesn't jump when it arrives."""

	knob = synthsync.core.knob.Knob()
	start = reading_for(knob, 6000)
	arrive = reading_for(knob, 15000)

	assert knob.start(start, 15000, start) is None

	for reading in range(start, arrive - 40, 20):
		assert knob.look(reading, 15000) is None
		assert not knob.following

	tempo = 15000
	seen = None

	for reading in range(arrive - 40, arrive + 200, 4):

		found = knob.look(reading, tempo)

		if knob.following and seen is None:
			seen = reading

		if found is not None:
			assert abs(found - tempo) <= 300
			tempo = found

	assert seen is not None and seen <= arrive + 4
	assert tempo > 15000


def check_a_knob_swept_past_the_tempo_takes_over_too () -> None:

	"""A fast turn can jump right over the tempo between two readings, and it still counts."""

	knob = synthsync.core.knob.Knob()
	low = reading_for(knob, 6000)
	high = reading_for(knob, 24000)

	assert knob.start(low, 12000, low) is None
	assert knob.look(low + 100, 12000) is None
	assert not knob.following

	assert knob.look(high, 12000) == knob.tempo_at(high)
	assert knob.following


def check_power_up_with_the_knob_where_it_was_keeps_the_saved_tempo () -> None:

	"""Set from the page and restarted, the page's tempo plays and the knob waits."""

	knob = synthsync.core.knob.Knob()

	assert knob.start(MIDDLE, 14000, MIDDLE + 20) is None
	assert not knob.following


def check_power_up_with_the_knob_moved_gives_it_the_tempo () -> None:

	"""Turned while off, or never noted, the knob sets the tempo at once."""

	for noted in (MIDDLE + 500, NOT_NOTED):

		knob = synthsync.core.knob.Knob()

		assert knob.start(MIDDLE, 14000, noted) == knob.tempo_at(MIDDLE)
		assert knob.following


def check_where_the_knob_is_gets_noted_only_while_it_has_the_tempo () -> None:

	"""So a knob the page has let go of still counts as moved at the next power-up."""

	knob, reading = following_at(12000)

	assert knob.to_note(reading, NOT_NOTED)
	assert not knob.to_note(reading, reading)
	assert knob.to_note(reading + 100, reading)

	knob.look(reading, 15000)

	assert not knob.to_note(reading + 100, reading)
