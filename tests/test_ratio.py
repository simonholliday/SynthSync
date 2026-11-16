"""Ratios agree with exact fractions over many random cases.

The shared checks in `tests/checks/check_ratio.py` cover the named cases on both
Pythons. These lean on `fractions`, which MicroPython doesn't have.
"""

import fractions
import random

import pytest

import picoclock.core.ratio


def as_fraction (ratio: tuple[int, int]) -> fractions.Fraction:

	"""Turn a ratio into the exact fraction it stands for."""

	return fractions.Fraction(ratio[0], ratio[1])


def test_every_two_place_decimal_in_range_reads_exactly () -> None:

	"""Every value a number field can hold becomes exactly that value."""

	for hundredths in range(1, 9601):

		text = "%d.%02d" % (hundredths // 100, hundredths % 100)
		exact = fractions.Fraction(hundredths, 100)

		if exact < fractions.Fraction(1, 128):
			with pytest.raises(ValueError):
				picoclock.core.ratio.parse(text)
			continue

		ratio = picoclock.core.ratio.parse(text)

		assert as_fraction(ratio) == exact
		assert ratio[0] <= picoclock.core.ratio.LARGEST_PART
		assert ratio[1] <= picoclock.core.ratio.LARGEST_PART


def test_random_fractions_read_exactly_or_are_refused () -> None:

	"""A typed fraction is kept exactly when the clock can play it at its PPQN, and refused when it can't."""

	rng = random.Random(11)
	slowest = as_fraction(picoclock.core.ratio.SLOWEST)

	for ppqn in picoclock.core.ratio.PPQNS:

		fastest = as_fraction(picoclock.core.ratio.fastest(ppqn))
		accepted = 0

		for _ in range(4000):

			top = rng.randrange(1, 20000)
			bottom = rng.randrange(1, 20000)
			exact = fractions.Fraction(top, bottom)
			a_beat = exact * ppqn
			playable = (
				slowest <= exact <= fastest
				and exact.numerator <= picoclock.core.ratio.LARGEST_PART
				and exact.denominator <= picoclock.core.ratio.LARGEST_PART
				and a_beat.numerator <= picoclock.core.ratio.LARGEST_PART
			)

			if playable:
				ratio = picoclock.core.ratio.parse("%d/%d" % (top, bottom), ppqn)
				assert ratio == (exact.numerator, exact.denominator)
				assert picoclock.core.ratio.effective(ratio, ppqn) == (a_beat.numerator, a_beat.denominator)
				accepted += 1

			else:
				with pytest.raises(ValueError):
					picoclock.core.ratio.parse("%d/%d" % (top, bottom), ppqn)

		assert accepted > 100, ppqn


def test_labels_and_text_read_back_for_random_ratios () -> None:

	"""Whatever the page shows for a ratio, typing it back gives the same ratio."""

	rng = random.Random(12)

	for _ in range(20000):

		try:
			ratio = picoclock.core.ratio.make(rng.randrange(1, 9601), rng.randrange(1, 9601))
		except ValueError:
			continue

		assert picoclock.core.ratio.parse(picoclock.core.ratio.to_text(ratio)) == ratio
		assert picoclock.core.ratio.parse(picoclock.core.ratio.label(ratio)) == ratio


def test_nearest_snap_matches_a_search_by_proportion () -> None:

	"""The nearest snap value is the one with the smallest proportion to the ratio."""

	rng = random.Random(13)
	snaps = picoclock.core.ratio.SNAPS

	def distance (ratio: tuple[int, int], snap: tuple[int, int]) -> fractions.Fraction:

		"""How far apart two ratios are as a proportion, always at least 1."""

		proportion = as_fraction(ratio) / as_fraction(snap)

		return proportion if proportion >= 1 else 1 / proportion

	for _ in range(5000):

		try:
			ratio = picoclock.core.ratio.make(rng.randrange(1, 9601), rng.randrange(1, 9601))
		except ValueError:
			continue

		found = picoclock.core.ratio.nearest_snap(ratio)
		best = min(distance(ratio, snap) for snap in snaps)

		assert distance(ratio, found) == best

		# A tie goes to the slower value.
		ties = [snap for snap in snaps if distance(ratio, snap) == best]
		assert found == ties[0]


def test_numerator_limit_keeps_the_scheduler_in_small_numbers () -> None:

	"""The largest ratio part times the fastest tempo stays under what the scheduler's sums allow."""

	fastest_tempo_in_hundredths = 30000

	# The scheduler's exact rescale needs three times this product to stay under 2**30.
	assert 3 * picoclock.core.ratio.LARGEST_PART * fastest_tempo_in_hundredths < 2 ** 30
