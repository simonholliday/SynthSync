"""Ratios read, write and step the same on the Pico's Python as on a laptop's."""

import synthsync.core.ratio


def refused (text: str) -> str:

	"""Give the message parse refuses some text with, or nothing if it accepts it."""

	try:
		synthsync.core.ratio.parse(text)
	except ValueError as error:
		return str(error)

	return ""


def check_numbers_and_fractions_read_exactly () -> None:

	"""What someone types becomes the exact ratio, in lowest terms."""

	parse = synthsync.core.ratio.parse

	assert parse("1") == (1, 1)
	assert parse("2") == (2, 1)
	assert parse("1.5") == (3, 2)
	assert parse("0.5") == (1, 2)
	assert parse(".5") == (1, 2)
	assert parse("5.") == (5, 1)
	assert parse("0.33") == (33, 100)
	assert parse("1.37") == (137, 100)
	assert parse("4/3") == (4, 3)
	assert parse("6/4") == (3, 2)
	assert parse(" 4 / 3 ") == (4, 3)
	assert parse("96") == (96, 1)
	assert parse("1/128") == (1, 128)
	assert parse("95.99") == (9599, 100)


def check_signs_in_front_are_understood () -> None:

	"""A leading × changes nothing and a leading ÷ turns the ratio over."""

	parse = synthsync.core.ratio.parse

	assert parse("×2") == (2, 1)
	assert parse("x2") == (2, 1)
	assert parse("*1.5") == (3, 2)
	assert parse("÷3") == (1, 3)
	assert parse("/3") == (1, 3)
	assert parse("÷1.5") == (2, 3)
	assert parse("×4/3") == (4, 3)


def check_bad_input_is_refused_with_a_reason () -> None:

	"""Anything that isn't a ratio is refused, and the message says what to do."""

	for text in ("", " ", "abc", "1.2.3", "1/", "/", "1/0", "0", "0.0", "-1", "1e3", "1.5/2", "4/3/2", "²", "1234567890"):
		assert refused(text), text

	assert "two decimal places" in refused("1.234")
	assert "slowest" in refused("1/129")
	assert "slowest" in refused("0.001") or "two decimal places" in refused("0.001")
	assert "fastest" in refused("96.01")
	assert "fastest" in refused("97")
	assert "too fine" in refused("9601/101")


def check_every_snap_value_is_in_order_and_reads_back () -> None:

	"""The snap values run from slowest to fastest, and each survives being written and read."""

	ratio = synthsync.core.ratio
	snaps = ratio.SNAPS

	assert len(snaps) == 27
	assert snaps[0] == (1, 64)
	assert snaps[-1] == (96, 1)
	assert ratio.ONE in snaps

	for index in range(len(snaps)):

		snap = snaps[index]

		assert ratio.make(snap[0], snap[1]) == snap
		assert ratio.parse(ratio.to_text(snap)) == snap
		assert ratio.parse(ratio.label(snap)) == snap
		assert ratio.is_snap(snap)

		if index:
			assert ratio.compare(snaps[index - 1], snap) == -1


def check_labels_read_the_way_the_page_shows_them () -> None:

	"""Whole multiples show as ×, whole divisions as ÷, and the rest as plainly as they can."""

	label = synthsync.core.ratio.label

	assert label((1, 1)) == "×1"
	assert label((2, 1)) == "×2"
	assert label((1, 3)) == "÷3"
	assert label((3, 2)) == "×1.5"
	assert label((2, 3)) == "÷1.5"
	assert label((4, 3)) == "×4/3"
	assert label((8, 3)) == "×8/3"
	assert label((16, 3)) == "×16/3"
	assert label((3, 4)) == "×0.75"
	assert label((33, 100)) == "×0.33"
	assert label((137, 100)) == "×1.37"
	assert label((2, 5)) == "÷2.5"
	assert label((5, 7)) == "÷1.4"
	assert label((7, 9)) == "×7/9"
	assert label((21, 20)) == "×1.05"


def check_plain_text_form_reads_back () -> None:

	"""The form used for saving is one whole number, or two with a slash."""

	ratio = synthsync.core.ratio

	assert ratio.to_text((2, 1)) == "2"
	assert ratio.to_text((3, 2)) == "3/2"
	assert ratio.to_text((1, 128)) == "1/128"


def check_stepping_through_snap_values () -> None:

	"""Next and previous move one musical value at a time and stop at the ends."""

	ratio = synthsync.core.ratio

	assert ratio.next_snap((1, 1)) == (4, 3)
	assert ratio.previous_snap((1, 1)) == (2, 3)
	assert ratio.next_snap((137, 100)) == (3, 2)
	assert ratio.previous_snap((137, 100)) == (4, 3)
	assert ratio.next_snap((96, 1)) == (96, 1)
	assert ratio.previous_snap((1, 64)) == (1, 64)
	assert ratio.previous_snap((1, 128)) == (1, 64)
	assert ratio.next_snap((1, 128)) == (1, 64)

	walked = [ratio.SNAPS[0]]

	while walked[-1] != ratio.SNAPS[-1]:
		walked.append(ratio.next_snap(walked[-1]))

	assert tuple(walked) == ratio.SNAPS


def check_nearest_snap_is_judged_by_proportion () -> None:

	"""A free ratio snaps to the musical value it sounds closest to."""

	nearest = synthsync.core.ratio.nearest_snap

	assert nearest((2, 1)) == (2, 1)
	assert nearest((137, 100)) == (4, 3)
	assert nearest((145, 100)) == (3, 2)
	assert nearest((1, 128)) == (1, 64)
	assert nearest((96, 1)) == (96, 1)
	assert nearest((95, 1)) == (96, 1)

	# 2.4 is closer to 2 by difference but closer to 8/3 by proportion.
	assert nearest((24, 10)) == (8, 3)

	# The square root of 2 x 8/3 is about 2.309: either side of it goes each way.
	assert nearest((230, 100)) == (2, 1)
	assert nearest((231, 100)) == (8, 3)


def check_compare_orders_ratios () -> None:

	"""Slower, equal and faster come out as -1, 0 and 1."""

	compare = synthsync.core.ratio.compare

	assert compare((1, 2), (2, 3)) == -1
	assert compare((3, 2), (3, 2)) == 0
	assert compare((4, 3), (5, 4)) == 1


def check_the_fastest_ratio_keeps_every_output_within_96_pulses_a_beat () -> None:

	"""At each PPQN the fastest ratio times the PPQN is 96, and it is a snap value, so a knob can always reach it."""

	for ppqn in synthsync.core.ratio.PPQNS:

		top = synthsync.core.ratio.fastest(ppqn)

		assert top[1] == 1
		assert top[0] * ppqn == synthsync.core.ratio.MOST_A_BEAT, ppqn
		assert synthsync.core.ratio.is_snap(top, ppqn), ppqn

	assert synthsync.core.ratio.fastest(4) == (24, 1)
	assert synthsync.core.ratio.fastest(48) == (2, 1)
	assert synthsync.core.ratio.DEFAULT_PPQN in synthsync.core.ratio.PPQNS
	assert list(synthsync.core.ratio.PPQNS) == sorted(synthsync.core.ratio.PPQNS)


def check_a_ratio_of_the_clock_is_given_to_the_scheduler_as_pulses_a_beat () -> None:

	"""What an output sends a beat is its ratio times the clock's pulses a beat, in lowest terms."""

	effective = synthsync.core.ratio.effective

	assert effective((1, 1), 4) == (4, 1)
	assert effective((1, 2), 4) == (2, 1)
	assert effective((1, 4), 4) == (1, 1)
	assert effective((1, 16), 4) == (1, 4)
	assert effective((3, 2), 4) == (6, 1)
	assert effective((2, 3), 48) == (32, 1)
	assert effective((1, 128), 1) == (1, 128)
	assert effective((4, 3), 1) == (4, 3)


def check_a_ratio_faster_than_the_ppqn_allows_is_refused () -> None:

	"""The fastest ratio depends on the clock's pulses a beat, and the refusal says so."""

	parse = synthsync.core.ratio.parse

	assert parse("24", 4) == (24, 1)
	assert parse("÷128", 48) == (1, 128)
	assert parse("2", 48) == (2, 1)
	assert parse("96") == (96, 1)

	for text, ppqn, said in (
		("25", 4, "At 4 PPQN the fastest ratio is ×24"),
		("24.01", 4, "At 4 PPQN the fastest ratio is ×24"),
		("3", 48, "At 48 PPQN the fastest ratio is ×2"),
		("97", 1, "The fastest ratio is ×96"),
	):

		try:
			parse(text, ppqn)
		except ValueError as error:
			assert str(error) == said, (text, ppqn)
		else:
			assert False, (text, ppqn)

	# A fraction fine enough is too fine once it is turned into pulses a beat.
	try:
		synthsync.core.ratio.make(2401, 101, 4)
	except ValueError as error:
		assert "too fine" in str(error)
	else:
		assert False


def check_snap_values_stop_at_the_fastest_the_ppqn_allows () -> None:

	"""Stepping and the nearest value never give a ratio the clock can't play."""

	next_snap = synthsync.core.ratio.next_snap
	nearest = synthsync.core.ratio.nearest_snap

	assert next_snap((16, 1), 4) == (24, 1)
	assert next_snap((24, 1), 4) == (24, 1)
	assert next_snap((1, 1), 48) == (4, 3)
	assert next_snap((2, 1), 48) == (2, 1)
	assert next_snap((64, 1)) == (96, 1)

	assert nearest((30, 1), 4) == (24, 1)
	assert nearest((23, 1), 4) == (24, 1)
	assert nearest((3, 1), 4) == (3, 1)
	assert nearest((5, 2), 48) == (2, 1)
	assert nearest((90, 1)) == (96, 1)


def check_the_snap_values_are_the_same_notes_at_every_ppqn () -> None:

	"""At one pulse a beat they are the list as it stands. At four, a quarter note is ÷4 and an 8th-note triplet ×3/4. At every PPQN each one, times the PPQN, is one of the list, and none is slower than ÷128 or faster than the fastest."""

	snaps = synthsync.core.ratio.snaps

	assert snaps(1) == list(synthsync.core.ratio.SNAPS)

	at_four = snaps(4)

	for note in ((1, 4), (3, 4), (1, 1), (1, 2), (3, 8), (1, 16), (6, 1), (24, 1)):
		assert note in at_four, note

	assert at_four[0] == (1, 128)
	assert at_four[-1] == (24, 1)

	at_24 = snaps(24)

	assert (1, 24) in at_24
	assert (1, 96) in at_24
	assert at_24[-1] == (4, 1)

	for ppqn in synthsync.core.ratio.PPQNS:

		found = snaps(ppqn)

		assert found == sorted(found, key=lambda snap: snap[0] / snap[1]), ppqn
		assert found[-1] == synthsync.core.ratio.fastest(ppqn), ppqn

		for snap in found:
			assert synthsync.core.ratio.effective(snap, ppqn) in synthsync.core.ratio.SNAPS, (ppqn, snap)
			assert synthsync.core.ratio.compare(snap, synthsync.core.ratio.SLOWEST) >= 0, (ppqn, snap)

	# Stepping and the nearest value go by the notes too.
	assert synthsync.core.ratio.next_snap((1, 1), 4) == (4, 3)
	assert synthsync.core.ratio.previous_snap((1, 1), 4) == (3, 4)
	assert synthsync.core.ratio.nearest_snap((74, 100), 4) == (3, 4)
	assert synthsync.core.ratio.is_snap((3, 4), 4)
	assert not synthsync.core.ratio.is_snap((3, 4), 1)
