"""Tap tempo's counting gives the tempo the taps' own times say, in a real browser's JavaScript."""

import typing

import pytest

import tests.pages

if typing.TYPE_CHECKING:
	import playwright.sync_api

ORIGIN = "http://tap.test"

HARNESS = """<!doctype html>
<script type="module">
	import {TapTempo} from "/tap.js";

	window.tempos = (moments) => {
		const counter = new TapTempo();
		return moments.map((moment) => counter.tap(moment));
	};

	window.TapTempo = TapTempo;
	window.ready = true;
</script>
"""


@pytest.fixture()
def page (chromium: "playwright.sync_api.Browser") -> "typing.Iterator[playwright.sync_api.Page]":

	"""A page holding nothing but the counter."""

	opened = tests.pages.desktop(chromium)

	tests.pages.serve_static(opened, ORIGIN, {"/": HARNESS})
	opened.goto(ORIGIN + "/")
	opened.wait_for_function("window.ready === true")

	yield opened

	opened.context.close()


def tempos (page: "playwright.sync_api.Page", moments: list[float]) -> list[int | None]:

	"""Tap at these moments, and give what each tap said."""

	said: list[int | None] = page.evaluate("(moments) => window.tempos(moments)", moments)

	return said


def steady (gap: float, count: int, start: float = 1000) -> list[float]:

	"""Give the moments of evenly spaced taps."""

	return [start + gap * index for index in range(count)]


def test_three_taps_say_nothing_and_the_fourth_gives_the_tempo (page: "playwright.sync_api.Page") -> None:

	"""Half a second between taps is 120 BPM, once there are four of them."""

	assert tempos(page, steady(500, 5)) == [None, None, None, 120, 120]


def test_the_tempo_is_a_whole_bpm (page: "playwright.sync_api.Page") -> None:

	"""A gap between two whole tempos gives the nearer."""

	assert tempos(page, steady(493, 4))[-1] == 122


def test_only_the_last_eight_taps_count (page: "playwright.sync_api.Page") -> None:

	"""Eight taps are seven gaps. Once the slow ones have passed out of those, the tempo is the quick ones' alone."""

	slow = steady(1000, 4)
	quick = steady(500, 8, slow[-1] + 500)
	said = tempos(page, slow + quick)

	assert said[3] == 60
	assert said[-3] == 105
	assert said[-2] == 120


def test_the_average_settles_an_uneven_hand (page: "playwright.sync_api.Page") -> None:

	"""Taps either side of the beat come to the tempo they are around."""

	assert tempos(page, [0, 520, 980, 1510, 1990, 2510, 2990, 3500])[-1] == 120


def test_a_long_gap_starts_the_count_again (page: "playwright.sync_api.Page") -> None:

	"""More than 3.2 seconds between taps forgets those before, however many there were."""

	first = steady(500, 5)
	again = steady(250, 4, first[-1] + 3201)

	assert tempos(page, first + again)[5:] == [None, None, None, 240]


def test_the_slowest_beat_is_not_a_long_gap (page: "playwright.sync_api.Page") -> None:

	"""Three seconds between taps is 20 BPM, and 3.2 seconds exactly still counts."""

	assert tempos(page, steady(3000, 4))[-1] == 20
	assert tempos(page, steady(3200, 4))[-1] == 19


def test_it_counts_every_tap_and_says_how_many_are_wanted (page: "playwright.sync_api.Page") -> None:

	"""It keeps counting past the eight it averages, and a reset begins again."""

	said = page.evaluate("""() => {
		const counter = new window.TapTempo();
		const seen = [[counter.count, counter.left()]];

		for (let index = 0; index < 10; index++) {
			counter.tap(index * 500);
			seen.push([counter.count, counter.left()]);
		}

		counter.reset();
		seen.push([counter.count, counter.left()]);

		return seen;
	}""")

	assert said[0] == [0, 4]
	assert said[1] == [1, 3]
	assert said[4] == [4, 0]
	assert said[10] == [10, 0]
	assert said[11] == [0, 4]
