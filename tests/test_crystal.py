"""The crystal tool's sums are right, tried on reports made up to a known error.

The measuring itself needs a Pico and an hour, and is done by hand.
"""

import ast
import random

import pytest

import tools.crystal


def reports (error: float, minutes: int, jitter: float = 0.0, delay: float = 0.004) -> list[tuple[float, int]]:

	"""Make up what a Pico that counts fast by `error` parts per million would report, every half minute.

	Each report takes `delay` seconds to arrive, give or take `jitter`.
	"""

	shake = random.Random(7)
	began = 1794600000.0
	made = []

	for number in range(minutes * 2 + 1):

		true = number * tools.crystal.EVERY
		made.append((began + true + delay + shake.uniform(-jitter, jitter), round(true * (1 + error / 1000000) * 1000000)))

	return made


@pytest.mark.parametrize("error", [0.0, 12.5, -30.0, 100.0])
def test_a_known_error_is_found (error: float) -> None:

	"""A Pico that counts fast or slow by a known amount is measured as that, whatever the delay over USB."""

	found, stray = tools.crystal.fit(reports(error, 60))

	assert abs(found - error) < 0.01
	assert stray < 0.01


def test_an_hour_gives_the_answer_to_a_part_per_million_through_the_usbs_jitter () -> None:

	"""With each report arriving up to two milliseconds early or late, an hour still gives the error to well within one part per million."""

	found, stray = tools.crystal.fit(reports(20.0, 60, jitter=0.002))

	assert abs(found - 20.0) < 0.5
	assert 0.5 < stray < 2.0

	# Ten minutes is five times rougher, which is why an hour is asked for.
	rough, stray = tools.crystal.fit(reports(20.0, 10, jitter=0.002))

	assert abs(rough - 20.0) < 5.0


def test_too_few_reports_measure_nothing () -> None:

	"""Two reports make a line whatever they say, so they aren't taken as a measurement."""

	with pytest.raises(ValueError):
		tools.crystal.fit(reports(20.0, 60)[:2])

	with pytest.raises(ValueError):
		tools.crystal.fit([(5.0, 0), (5.0, 30000000), (5.0, 60000000)])


def test_what_runs_on_the_pico_is_python () -> None:

	"""The Pico's side reads as Python once the times are filled in."""

	ast.parse(tools.crystal.COUNT % (tools.crystal.EVERY * 1000000, 120))
