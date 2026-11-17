"""The page's knob works by mouse, keyboard and touch in a real browser, and steps ratios the way the Pico does."""

import typing

import pytest

import synthsync.core.ratio

import tests.pages

if typing.TYPE_CHECKING:
	import playwright.sync_api

ORIGIN = "http://knob.test"

# A page with one knob that counts from 0 to 100, on a page tall enough to scroll.
HARNESS = """<!doctype html>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
	body { margin: 0; height: 3000px; }
	#knob { width: 160px; height: 160px; margin: 100px; }
</style>
<div id="knob"></div>
<output id="value"></output>
<script type="module">
	import {Knob} from "/knob.js";
	import * as ratio from "/ratio.js";

	let value = 50;

	const knob = new Knob(document.getElementById("knob"), {
		label: "Test knob",
		pixelsPerStep: 4,
		bigStep: 10,
		onStep: (steps) => { value = Math.min(100, Math.max(0, value + steps)); show(); },
		onHold: (held) => window.holds.push(held),
	});

	function show () {
		knob.point(value / 100);
		knob.say(value + " things", value, 0, 100);
		document.getElementById("value").textContent = value;
	}

	window.holds = [];
	window.knob = knob;
	window.ratio = ratio;

	show();
	window.ready = true;
</script>
"""

SNAPS = [synthsync.core.ratio.to_text(snap) for snap in synthsync.core.ratio.SNAPS]


def open_harness (page: "playwright.sync_api.Page") -> None:

	"""Load the one-knob page and wait until its knob is drawn."""

	tests.pages.serve_static(page, ORIGIN, {"/": HARNESS})

	page.goto(ORIGIN + "/")
	page.wait_for_function("window.ready === true")


def value (page: "playwright.sync_api.Page") -> int:

	"""Read what the knob has counted to."""

	return int(page.inner_text("#value"))


@pytest.fixture()
def laptop (chromium: "playwright.sync_api.Browser") -> "typing.Iterator[playwright.sync_api.Page]":

	"""The one-knob page in a laptop's browser."""

	page = tests.pages.desktop(chromium)
	open_harness(page)

	yield page

	page.context.close()


@pytest.fixture()
def phone (chromium: "playwright.sync_api.Browser") -> "typing.Iterator[playwright.sync_api.Page]":

	"""The one-knob page on a phone."""

	page = tests.pages.phone(chromium)
	open_harness(page)

	yield page

	page.context.close()


def test_dragging_by_mouse_turns_it_up_and_right_for_more (laptop: "playwright.sync_api.Page") -> None:

	"""Every four pixels dragged is one step: up or right for more, down or left for less."""

	tests.pages.drag_by_mouse(laptop, "#knob", 0, -40)

	assert value(laptop) == 60

	tests.pages.drag_by_mouse(laptop, "#knob", 20, 0)

	assert value(laptop) == 65

	tests.pages.drag_by_mouse(laptop, "#knob", -12, 48)

	assert value(laptop) == 50

	# A drag can leave the knob and still turn it, and it stops at the end of its travel.
	tests.pages.drag_by_mouse(laptop, "#knob", 0, -400)

	assert value(laptop) == 100


def test_pressing_without_moving_changes_nothing (laptop: "playwright.sync_api.Page") -> None:

	"""A touch that doesn't drag can't jump the value, wherever on the knob it lands."""

	x, y = tests.pages.centre(laptop, "#knob")

	for across, down in ((0, 0), (60, -60), (-70, 20)):
		laptop.mouse.move(x + across, y + down)
		laptop.mouse.down()
		laptop.mouse.up()

	assert value(laptop) == 50
	assert laptop.evaluate("window.holds") == [True, False] * 3


def test_the_arrow_keys_step_and_the_page_keys_step_further (laptop: "playwright.sync_api.Page") -> None:

	"""Arrows move one step, Page Up and Page Down ten, and Home and End do nothing."""

	laptop.focus("#knob")

	for key in ("ArrowUp", "ArrowUp", "ArrowRight"):
		laptop.keyboard.press(key)

	assert value(laptop) == 53

	laptop.keyboard.press("ArrowDown")
	laptop.keyboard.press("ArrowLeft")

	assert value(laptop) == 51

	laptop.keyboard.press("PageUp")

	assert value(laptop) == 61

	laptop.keyboard.press("PageDown")
	laptop.keyboard.press("PageDown")

	assert value(laptop) == 41

	laptop.keyboard.press("Home")
	laptop.keyboard.press("End")

	assert value(laptop) == 41


def test_a_screen_reader_hears_a_slider_with_its_value (laptop: "playwright.sync_api.Page") -> None:

	"""It announces itself as a slider, can be reached by Tab, and says its value in words as it moves."""

	knob = laptop.locator("#knob")

	assert knob.get_attribute("role") == "slider"
	assert knob.get_attribute("tabindex") == "0"
	assert knob.get_attribute("aria-label") == "Test knob"
	assert knob.get_attribute("aria-valuemin") == "0"
	assert knob.get_attribute("aria-valuemax") == "100"
	assert knob.get_attribute("aria-valuenow") == "50"
	assert knob.get_attribute("aria-valuetext") == "50 things"

	laptop.keyboard.press("Tab")

	assert laptop.evaluate("document.activeElement.id") == "knob"

	laptop.keyboard.press("ArrowUp")

	assert knob.get_attribute("aria-valuenow") == "51"
	assert knob.get_attribute("aria-valuetext") == "51 things"

	# The drawing is for the eye only.
	assert laptop.locator("#knob svg").get_attribute("aria-hidden") == "true"


def test_the_pointer_turns_with_the_value (laptop: "playwright.sync_api.Page") -> None:

	"""The pointer sweeps three quarters of a turn, and the lit arc runs from its origin to the pointer."""

	hand = laptop.locator("#knob .knob-hand")
	lit = laptop.locator("#knob .knob-lit")

	assert hand.get_attribute("transform") == "rotate(0.00 50 50)"

	laptop.evaluate("window.knob.point(0)")

	assert hand.get_attribute("transform") == "rotate(-135.00 50 50)"
	assert lit.get_attribute("d") == ""

	laptop.evaluate("window.knob.point(1)")

	assert hand.get_attribute("transform") == "rotate(135.00 50 50)"
	assert (lit.get_attribute("d") or "").startswith("M ")

	# Told to point past either end, it stops at the end.
	laptop.evaluate("window.knob.point(7)")

	assert hand.get_attribute("transform") == "rotate(135.00 50 50)"


def test_a_disabled_knob_does_not_turn (laptop: "playwright.sync_api.Page") -> None:

	"""Once disabled, neither a drag nor a key moves it, and a screen reader is told."""

	laptop.evaluate("window.knob.disable(true)")

	tests.pages.drag_by_mouse(laptop, "#knob", 0, -40)
	laptop.locator("#knob").dispatch_event("keydown", {"key": "ArrowUp"})

	assert value(laptop) == 50
	assert laptop.locator("#knob").get_attribute("aria-disabled") == "true"

	laptop.evaluate("window.knob.disable(false)")
	tests.pages.drag_by_mouse(laptop, "#knob", 0, -40)

	assert value(laptop) == 60


def test_dragging_by_finger_turns_it_and_does_not_scroll_the_page (phone: "playwright.sync_api.Page") -> None:

	"""On a phone a finger on the knob turns it and the page stays put, while a finger elsewhere scrolls."""

	x, y = tests.pages.centre(phone, "#knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -60)

	assert value(phone) == 65
	assert phone.evaluate("window.scrollY") == 0

	tests.pages.drag_by_touch(phone, x, y, 0, 100)

	assert value(phone) == 40
	assert phone.evaluate("window.scrollY") == 0
	assert phone.evaluate("window.holds") == [True, False, True, False]

	# The same drag below the knob does scroll, so this test would notice if the knob did.
	tests.pages.drag_by_touch(phone, x, y + 300, 0, -200)
	phone.wait_for_function("window.scrollY > 0")

	assert value(phone) == 40


def test_a_ratio_sits_where_it_sounds (laptop: "playwright.sync_api.Page") -> None:

	"""Along the knob, halving is as far one way as doubling is the other, from end to end."""

	def along (text: str) -> float:

		"""Ask the page where a ratio sits."""

		return float(laptop.evaluate("(text) => window.ratio.along(text, '1/128', '96')", text))

	assert along("1/128") == pytest.approx(0)
	assert along("96") == pytest.approx(1)
	assert along("2") - along("1") == pytest.approx(along("1") - along("1/2"))
	assert along("8") - along("4") == pytest.approx(along("1/16") - along("1/32"))
	assert along("3/2") == pytest.approx(along("1.5"))
	assert 0.5 < along("1") < 0.53


def test_stepping_through_the_musical_values_matches_the_pico (laptop: "playwright.sync_api.Page") -> None:

	"""One step from any ratio lands where the Pico's own code says the next musical value is."""

	def step (text: str, steps: int) -> str:

		"""Ask the page for the musical value some steps on from a ratio."""

		return str(laptop.evaluate("([text, snaps, steps]) => window.ratio.stepSnap(text, snaps, steps)", [text, SNAPS, steps]))

	tried = SNAPS + ["137/100", "5/7", "1/128", "1/100", "95", "7/2", "0.3", "1.99", "2.01"]

	for text in tried:

		ratio = synthsync.core.ratio.parse(text)

		assert step(text, 1) == synthsync.core.ratio.to_text(synthsync.core.ratio.next_snap(ratio)), text
		assert step(text, -1) == synthsync.core.ratio.to_text(synthsync.core.ratio.previous_snap(ratio)), text

	assert step("1", 3) == "2"
	assert step("1", -2) == "1/2"
	assert step("1.1", 2) == "3/2"
	assert step("1.1", -2) == "2/3"
	assert step("64", 5) == "96"
	assert step("1/32", -9) == "1/64"


def test_the_page_labels_a_ratio_exactly_as_the_pico_does (laptop: "playwright.sync_api.Page") -> None:

	"""Every musical value, every decimal a knob can reach and some awkward fractions read the same on both."""

	texts = SNAPS + ["137/100", "5/7", "7/9", "4/5", "1/128", "95/2", "3/4", "100/3", "7/2", "9600/101"]
	texts += ["%d.%02d" % (hundredths // 100, hundredths % 100) for hundredths in range(1, 9601, 7)]
	texts += ["0.5", "0.25", "1.5", "2", "96", "0.8", "0.75", "0.01"]

	labels = laptop.evaluate("(texts) => texts.map(window.ratio.label)", texts)

	for text, shown in zip(texts, labels):
		assert shown == synthsync.core.ratio.label(synthsync.core.ratio.parse(text)), text


def test_stepping_freely_moves_evenly_and_always_moves (laptop: "playwright.sync_api.Page") -> None:

	"""With snap off a step is a small proportion, never nothing, and every answer is one the Pico accepts."""

	def step (text: str, steps: int) -> str:

		"""Ask the page for the ratio some steps on from another, moving freely."""

		return str(laptop.evaluate("([text, steps]) => window.ratio.stepFree(text, steps, '96')", [text, steps]))

	assert step("1", 1) == "1.03"
	assert step("1", -1) == "0.97"
	assert step("1", 24) == "2"
	assert step("4", -24) == "2"
	assert step("0.02", 1) == "0.03"
	assert step("0.02", -1) == "0.01"
	assert step("0.01", -5) == "0.01"
	assert step("96", 3) == "96"
	assert step("4/3", 1) == "1.37"
	assert step("1/128", 1) == "0.02"

	# Walking the whole way up never sticks, never passes the top, and never leaves what the Pico takes.
	text = "0.01"
	seen = 0

	while text != "96":

		after = step(text, 1)
		parsed = synthsync.core.ratio.parse(after)

		assert synthsync.core.ratio.compare(parsed, synthsync.core.ratio.parse(text)) > 0, (text, after)
		assert synthsync.core.ratio.compare(parsed, synthsync.core.ratio.fastest(1)) <= 0

		text = after
		seen += 1

	assert 150 < seen < 300

	# Every doubling from ×1 can be landed on exactly, and so can the halvings on the way down.
	assert [step("1", 24 * doublings) for doublings in (1, 2, 3, 4)] == ["2", "4", "8", "16"]

	passed = []
	text = "1"

	while text != "0.01":
		text = step(text, -1)
		passed.append(text)

	assert passed[23] == "0.5"
	assert "0.25" in passed


def test_the_page_turns_the_musical_values_into_ratios_of_the_clock_as_the_pico_does (laptop: "playwright.sync_api.Page") -> None:

	"""Divided by each PPQN and trimmed to what the clock can play, the page's list is the Pico's own, value for value."""

	beat_snaps = [synthsync.core.ratio.to_text(snap) for snap in synthsync.core.ratio.SNAPS]
	slowest = synthsync.core.ratio.to_text(synthsync.core.ratio.SLOWEST)

	for ppqn in synthsync.core.ratio.PPQNS:

		fastest = synthsync.core.ratio.to_text(synthsync.core.ratio.fastest(ppqn))
		page = laptop.evaluate("([snaps, ppqn, slowest, fastest]) => window.ratio.ofClock(snaps, ppqn, slowest, fastest)", [beat_snaps, ppqn, slowest, fastest])

		assert page == [synthsync.core.ratio.to_text(snap) for snap in synthsync.core.ratio.snaps(ppqn)], ppqn
