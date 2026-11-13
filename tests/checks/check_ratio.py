"""Ratios read, write and step the same on the Pico's Python as on a laptop's."""

import picoclock.core.ratio


def refused (text: str) -> str:

	"""Give the message parse refuses some text with, or nothing if it accepts it."""

	try:
		picoclock.core.ratio.parse(text)
	except ValueError as error:
		return str(error)

	return ""


def check_numbers_and_fractions_read_exactly () -> None:

	"""What someone types becomes the exact ratio, in lowest terms."""

	parse = picoclock.core.ratio.parse

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

	parse = picoclock.core.ratio.parse

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

	ratio = picoclock.core.ratio
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

	label = picoclock.core.ratio.label

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

	ratio = picoclock.core.ratio

	assert ratio.to_text((2, 1)) == "2"
	assert ratio.to_text((3, 2)) == "3/2"
	assert ratio.to_text((1, 128)) == "1/128"


def check_stepping_through_snap_values () -> None:

	"""Next and previous move one musical value at a time and stop at the ends."""

	ratio = picoclock.core.ratio

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

	nearest = picoclock.core.ratio.nearest_snap

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

	compare = picoclock.core.ratio.compare

	assert compare((1, 2), (2, 3)) == -1
	assert compare((3, 2), (3, 2)) == 0
	assert compare((4, 3), (5, 4)) == 1
