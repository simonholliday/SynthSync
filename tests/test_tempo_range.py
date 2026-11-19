"""The page keeps to the tempo range `config.txt` gives, in a real browser at a phone's size."""

import pathlib
import typing

import pytest

import tests.pages
import tests.stand

if typing.TYPE_CHECKING:
	import playwright.sync_api


@pytest.fixture(scope="module", params=("seven files", "one file"))
def packed (request: pytest.FixtureRequest) -> bool:

	"""Whether the page is served packed into one file, as the Pico holds it."""

	return bool(request.param == "one file")


@pytest.fixture()
def opened (chromium: "playwright.sync_api.Browser", packed: bool, tmp_path: pathlib.Path) -> "typing.Iterator[typing.Callable[[str], tuple[playwright.sync_api.Page, tests.stand.Stand]]]":

	"""Give a way to open the page on a stand-in that has this `config.txt`."""

	stands: list[tests.stand.Stand] = []

	def open_with (text: str) -> "tuple[playwright.sync_api.Page, tests.stand.Stand]":

		"""Start the stand-in and open the page on it."""

		config = tmp_path / "config.txt"
		config.write_text(text)

		stand = tests.stand.Stand(config=config, static=tests.stand.packed(tmp_path / "packed") if packed else None)
		stands.append(stand)

		page = tests.pages.phone(chromium)

		page.goto(stand.address)
		page.wait_for_function("!document.body.classList.contains('lost') && document.getElementById('tempo-text').value !== ''")

		return page, stand

	yield open_with

	for stand in stands:
		stand.stop()


def test_a_narrow_range_moves_the_knob_in_small_steps_and_stops_at_its_ends (opened: typing.Any) -> None:

	"""From 100 to 110 BPM the knob moves in tenths, and goes no further than the range."""

	page, stand = opened("min_bpm = 100\nmax_bpm = 110\n")

	assert page.input_value("#tempo-text") == "110"

	page.focus("#tempo-knob")
	page.keyboard.press("ArrowDown")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 10990)

	page.keyboard.press("PageDown")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 10950)

	stand.change({"do": "tempo", "value": 10005})

	assert tests.pages.eventually(lambda: page.input_value("#tempo-text") == "100.05")

	page.keyboard.press("ArrowDown")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 10000)

	page.keyboard.press("ArrowDown")
	page.wait_for_timeout(150)

	assert stand.now("tempo")["tempo"] == 10000
	assert page.get_attribute("#tempo-knob", "aria-valuemin") == "100"
	assert page.get_attribute("#tempo-knob", "aria-valuemax") == "110"


def test_a_sweep_of_the_knob_is_the_same_drag_whatever_the_range (opened: typing.Any) -> None:

	"""A range of 100 steps has each one nearly three times as far apart as the usual range's 280."""

	page, stand = opened("min_bpm = 100\nmax_bpm = 110\n")

	tests.pages.drag_by_mouse(page, "#tempo-knob", -88, 0)

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 10900)


def test_tap_tempo_stays_open_for_the_slowest_beat_of_the_range (opened: typing.Any) -> None:

	"""At 1 BPM a beat is a minute, so the pad can't lock itself after ten seconds, as it does in the usual range."""

	page, _ = opened("min_bpm = 1\nmax_bpm = 100\n")

	page.clock.install()
	page.click("#tap-open")

	assert page.is_visible("#tap")

	page.clock.fast_forward(30000)

	assert page.is_visible("#tap")

	page.clock.fast_forward(36000)

	assert tests.pages.eventually(lambda: not page.is_visible("#tap"))


def test_a_typed_tempo_outside_the_range_is_refused_with_the_range_in_the_words (opened: typing.Any) -> None:

	"""The page says what the range is."""

	page, stand = opened("min_bpm = 100\nmax_bpm = 110\n")

	page.fill("#tempo-text", "120")
	page.keyboard.press("Enter")

	assert tests.pages.eventually(lambda: "between 100 and 110 BPM" in page.inner_text("#tempo-problem"))
	assert stand.now("tempo")["tempo"] == 11000


def test_a_wide_range_reaches_both_ends_and_keeps_ratios_within_its_limits (opened: typing.Any) -> None:

	"""From 1 to 1000 BPM, a tempo at either end plays, and the fastest ratio is what that range allows."""

	page, stand = opened("min_bpm = 1\nmax_bpm = 1000\n")

	assert stand.now("ppqn")["fastest_ratio"] == "7"
	assert stand.now("ppqn")["slowest_ratio"] == "1/24"

	for typed, tempo in (("1000", 100000), ("1", 100)):

		page.fill("#tempo-text", typed)
		page.keyboard.press("Enter")

		assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == tempo)

	# The knob for output 2 stops at the fastest ratio there is, which isn't one of the usual musical values.
	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "6"})

	knob = "#outputs .output:nth-child(2) .ratio-knob"

	assert tests.pages.eventually(lambda: page.get_attribute(knob, "aria-valuenow") == "6")
	assert page.get_attribute(knob, "aria-valuemax") == "7"
	assert page.get_attribute(knob, "aria-valuemin") == "0.0417"

	page.focus(knob)
	page.keyboard.press("ArrowUp")

	assert tests.pages.eventually(lambda: stand.now("output", 1)["ratio"] == "7")


def test_settings_offers_only_the_ppqns_the_range_can_send (opened: typing.Any) -> None:

	"""48 pulses a beat is too many at 1000 BPM, so it isn't offered."""

	page, _ = opened("max_bpm = 1000\n")

	page.goto(page.url.split("#")[0] + "#settings")
	page.wait_for_selector("#rates > *")

	assert "48" not in page.inner_text("#rates")
	assert "24" in page.inner_text("#rates")
