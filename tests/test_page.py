"""The page works in a real browser at a phone's size, against the laptop stand-in.

Every test opens the real page from a stand-in PicoClock running as its own
program, and checks what the page did by asking the stand-in, not the page.
"""

import time
import typing
import urllib.error
import urllib.request

import pytest

import picoclock.core.ratio

import tests.pages
import tests.stand

if typing.TYPE_CHECKING:
	import playwright.sync_api

# Counts how many times the first output's light comes on over a while, and how much of the while it is on for.
WATCH_LIGHT = """([row, milliseconds]) => new Promise((done) => {
	const light = document.querySelectorAll(".output .light")[row];
	const began = performance.now();
	let frames = 0, lit = 0, rises = 0, glows = 0, before = light.classList.contains("on");

	function look (moment) {
		const on = light.classList.contains("on");
		frames += 1;
		lit += on ? 1 : 0;
		glows += light.classList.contains("fast") ? 1 : 0;
		rises += on && !before ? 1 : 0;
		before = on;

		if (moment - began < milliseconds) {
			requestAnimationFrame(look);
		} else {
			done({frames, lit, rises, glows});
		}
	}

	requestAnimationFrame(look);
})"""


@pytest.fixture(scope="module", params=("six files", "one file"))
def stand (request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> typing.Iterator[tests.stand.Stand]:

	"""One stand-in PicoClock for all of these tests, serving the page's own six files, and then the page packed into one as the Pico holds it."""

	stand = tests.stand.Stand(static=tests.stand.packed(tmp_path_factory.mktemp("packed")) if request.param == "one file" else None)

	yield stand

	stand.stop()


def open_page (page: "playwright.sync_api.Page", stand: tests.stand.Stand) -> list[str]:

	"""Open the page on a stand-in, wait until it has heard from it, and give back a list its script errors will go in."""

	problems: list[str] = []

	page.on("pageerror", lambda error: problems.append(str(error)))
	page.on("console", lambda message: problems.append(message.text) if message.type == "error" else None)

	page.goto(stand.address + "/")
	page.wait_for_function("!document.body.classList.contains('lost') && document.querySelectorAll('.output').length > 0")

	return problems


@pytest.fixture()
def phone (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> "typing.Iterator[playwright.sync_api.Page]":

	"""The page on a phone, on a stand-in with every setting as new."""

	stand.as_new()

	page = tests.pages.phone(chromium)
	problems = open_page(page, stand)

	yield page

	assert problems == []

	page.context.close()


def row (page: "playwright.sync_api.Page", number: int) -> "playwright.sync_api.Locator":

	"""Give the row of one output, counted from 1 as the page shows them."""

	return page.locator(".output").nth(number - 1)


def type_into (field: "playwright.sync_api.Locator", text: str) -> None:

	"""Type into a box and press Enter, as someone would."""

	field.click()
	field.fill(text)
	field.press("Enter")


def test_the_page_opens_showing_how_things_stand (phone: "playwright.sync_api.Page") -> None:

	"""A page that has just opened shows the tempo, what the clock is doing, and a row for every output."""

	assert phone.title() == "PicoClock"
	assert phone.input_value("#tempo-text") == "120"
	assert phone.inner_text("#link") == ""
	assert phone.get_attribute("#tempo-knob", "aria-valuetext") == "120 BPM"

	assert phone.get_attribute("#start", "aria-pressed") == "true"
	assert phone.get_attribute("#pause", "aria-pressed") == "false"
	assert phone.get_attribute("#stop", "aria-pressed") == "false"

	assert phone.locator(".output").count() == 4
	assert phone.locator(".output .name").all_inner_texts() == ["1", "2", "3", "4"]

	# Output 1 is the main clock, so it has nothing to set a ratio with.
	assert row(phone, 1).locator(".knob, .ratio-text, .snap, .role").count() == 0
	assert "main clock" in row(phone, 1).inner_text()

	for number in (2, 3, 4):
		assert row(phone, number).locator(".knob").count() == 1
		assert row(phone, number).locator(".ratio-text").input_value() == "×1"
		assert row(phone, number).locator(".snap").is_checked()
		assert row(phone, number).locator(".extra").is_hidden()

	# Every output is on, and says so to a screen reader by its number.
	for number in (1, 2, 3, 4):
		assert row(phone, number).locator(".on").is_checked()
		assert row(phone, number).get_by_role("switch", name="Output %d" % number).count() == 1


def test_the_page_fits_a_phone_and_is_big_enough_to_touch (phone: "playwright.sync_api.Page") -> None:

	"""Nothing spills off the side, and everything that is pressed is at least 44 pixels tall."""

	assert phone.evaluate("document.documentElement.scrollWidth") <= tests.pages.PHONE["width"]

	row(phone, 2).locator(".more").click()

	for selector in ("#start", "#pause", "#stop", ".output .power", ".output .more", ".output .snap-switch", ".output .ratio-text", ".output .role", ".output .pulse"):

		for index in range(phone.locator(selector).count()):

			target = phone.locator(selector).nth(index)

			if target.is_visible():
				box = target.bounding_box()
				assert box is not None and box["height"] >= 44, selector

	assert phone.evaluate("document.documentElement.scrollWidth") <= tests.pages.PHONE["width"]


def test_a_narrow_phone_still_shows_every_number_whole (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""On the narrowest phones nothing spills off the side, and a long tempo and a long ratio are not cut short."""

	stand.as_new()
	stand.change({"do": "tempo", "value": 12025})
	stand.change({"do": "output", "index": 1, "name": "snap", "value": False})
	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "1.37"})

	for width in (375, 360, 320):

		page = tests.pages.phone(chromium, width)
		problems = open_page(page, stand)

		assert tests.pages.eventually(lambda: page.input_value("#tempo-text") == "120.25")
		assert tests.pages.eventually(lambda: row(page, 2).locator(".ratio-text").input_value() == "×1.37")

		row(page, 2).locator(".more").tap()

		assert page.evaluate("document.documentElement.scrollWidth") <= width, width

		# A box whose width isn't a whole number of pixels can measure one pixel fuller than it is.
		for selector in ("#tempo-text", ".output:nth-child(2) .ratio-text"):
			assert page.evaluate("selector => { const box = document.querySelector(selector); return box.scrollWidth <= box.clientWidth + 1; }", selector), (width, selector)

		# Everything in a row is still on one line, and still big enough to press.
		tops = page.evaluate("[...document.querySelectorAll('.output:nth-child(2) .row > *')].map(part => { const box = part.getBoundingClientRect(); return [box.top + box.height / 2, box.width, box.height]; })")

		assert max(top for top, _, _ in tops) - min(top for top, _, _ in tops) < 2, (width, tops)
		assert all(wide >= 40 and tall >= 44 for _, wide, tall in tops), (width, tops)

		assert problems == []

		page.context.close()


def test_typing_a_tempo_sets_it (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A tempo typed with decimals reaches the clock exactly, and the knob follows."""

	type_into(phone.locator("#tempo-text"), "98.5")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 9850)
	assert tests.pages.eventually(lambda: phone.get_attribute("#tempo-knob", "aria-valuetext") == "98.5 BPM")
	assert phone.input_value("#tempo-text") == "98.5"

	type_into(phone.locator("#tempo-text"), " 123.45 ")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 12345)
	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "123.45")


def test_a_tempo_the_pico_refuses_is_explained_and_put_back (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The page shows the Pico's own words for why, and the box goes back to the tempo that stands."""

	type_into(phone.locator("#tempo-text"), "999")

	assert tests.pages.eventually(lambda: phone.inner_text("#tempo-problem") == "The tempo has to be between 20 and 300 BPM")
	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "120")

	type_into(phone.locator("#tempo-text"), "12o")

	assert tests.pages.eventually(lambda: phone.inner_text("#tempo-problem") == "Type a tempo as a number of beats a minute, such as 120 or 123.45")
	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "120")
	assert stand.now("tempo")["tempo"] == 12000

	# The next good tempo clears the explanation.
	type_into(phone.locator("#tempo-text"), "121")

	assert tests.pages.eventually(lambda: phone.inner_text("#tempo-problem") == "")
	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 12100)

	# A refusal straight after one of this page's own changes still puts the box back.
	type_into(phone.locator("#tempo-text"), "5")

	assert tests.pages.eventually(lambda: phone.inner_text("#tempo-problem") == "The tempo has to be between 20 and 300 BPM")
	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "121")


def test_dragging_the_tempo_knob_moves_in_whole_bpm (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A finger dragged up the knob raises the tempo a whole BPM every three pixels, and the number follows."""

	x, y = tests.pages.centre(phone, "#tempo-knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -60)

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 14000)
	assert phone.input_value("#tempo-text") == "140"

	tests.pages.drag_by_touch(phone, x, y, 0, 30)

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 13000)
	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "130")

	# From a tempo with decimals, the first step lands on the next whole BPM that way.
	stand.change({"do": "tempo", "value": 12345})

	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "123.45")

	phone.focus("#tempo-knob")
	phone.keyboard.press("ArrowUp")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 12400)

	stand.change({"do": "tempo", "value": 12345})

	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "123.45")

	phone.keyboard.press("ArrowDown")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 12300)

	# It stops at the ends, however far it is dragged.
	tests.pages.drag_by_touch(phone, x, y, 0, 400)

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 2000)


def test_a_dragged_knob_never_jumps_back_under_the_finger (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""While a knob is dragged steadily one way, its number only ever goes that way, though its own changes keep coming back."""

	phone.evaluate("""() => {
		window.seen = [];
		const box = document.getElementById("tempo-text");
		const look = () => { window.seen.push(Number(box.value)); requestAnimationFrame(look); };
		requestAnimationFrame(look);
	}""")

	x, y = tests.pages.centre(phone, "#tempo-knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -240, moves=80, pause=0.015)

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 20000)

	time.sleep(0.3)

	seen = phone.evaluate("window.seen")

	assert seen[0] == 120
	assert seen[-1] == 200
	assert len(set(seen)) > 20
	assert seen == sorted(seen), seen


def test_presses_made_in_one_instant_all_arrive_in_order (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Stop, start and pause pressed together leave the clock paused, and three settings changed together all take."""

	phone.evaluate("""() => {
		document.getElementById("stop").click();
		document.getElementById("start").click();
		document.getElementById("pause").click();
	}""")

	assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "paused")
	assert tests.pages.eventually(lambda: phone.get_attribute("#pause", "aria-pressed") == "true")

	phone.evaluate("""() => {
		const row = document.querySelectorAll(".output")[2];
		const kind = row.querySelector(".pulse-kind");
		row.querySelector(".snap").click();
		kind.value = "half";
		kind.dispatchEvent(new Event("change"));
		row.querySelector(".on").click();
	}""")

	assert tests.pages.eventually(lambda: stand.now("output", 2)["snap"] is False)
	assert tests.pages.eventually(lambda: stand.now("output", 2)["muted"] is True)
	assert tests.pages.eventually(lambda: stand.now("output", 2)["pulse"] == "half")


def test_start_pause_and_stop_work_the_clock_and_show_what_it_is_doing (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Each button does its one thing, and the lit button is always what the clock is doing."""

	def pressed () -> list[str | None]:

		"""Say which of the three buttons is lit."""

		return [phone.get_attribute(selector, "aria-pressed") for selector in ("#start", "#pause", "#stop")]

	phone.tap("#pause")

	assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "paused")
	assert tests.pages.eventually(lambda: pressed() == ["false", "true", "false"])

	phone.tap("#start")

	assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "running")
	assert tests.pages.eventually(lambda: pressed() == ["true", "false", "false"])

	phone.tap("#stop")

	assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "stopped")
	assert tests.pages.eventually(lambda: pressed() == ["false", "false", "true"])

	# Pause does nothing to a stopped clock.
	phone.tap("#pause")
	phone.tap("#start")

	assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "running")


def test_a_ratio_knob_steps_through_the_musical_values (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""With snap on, each step of an output's knob is the next musical value, shown as the Pico labels it."""

	knob = row(phone, 2).locator(".knob")
	text = row(phone, 2).locator(".ratio-text")

	knob.focus()

	for expected in ("×4/3", "×1.5", "×2"):
		phone.keyboard.press("ArrowUp")
		assert tests.pages.eventually(lambda: text.input_value() == expected), expected

	assert tests.pages.eventually(lambda: stand.now("output", 1)["ratio"] == "2")
	assert knob.get_attribute("aria-valuetext") == "times 2"

	for expected in ("×1.5", "×4/3", "×1", "÷1.5", "÷2"):
		phone.keyboard.press("ArrowDown")
		assert tests.pages.eventually(lambda: text.input_value() == expected), expected

	assert tests.pages.eventually(lambda: stand.now("output", 1)["ratio"] == "1/2")
	assert knob.get_attribute("aria-valuetext") == "divided by 2"

	# By finger, twelve pixels a step.
	x, y = tests.pages.centre(phone, ".output:nth-child(2) .knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -36)

	assert tests.pages.eventually(lambda: stand.now("output", 1)["ratio"] == "4/3")
	assert tests.pages.eventually(lambda: text.input_value() == "×4/3")

	# The other outputs haven't moved.
	assert stand.now("output", 2)["ratio"] == "1"


def test_with_snap_off_a_ratio_knob_moves_freely (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Turning snap off lets the knob stop between the musical values."""

	knob = row(phone, 3).locator(".knob")
	text = row(phone, 3).locator(".ratio-text")

	row(phone, 3).locator(".snap-switch").tap()

	assert tests.pages.eventually(lambda: stand.now("output", 2)["snap"] is False)

	knob.focus()
	phone.keyboard.press("ArrowUp")

	assert tests.pages.eventually(lambda: stand.now("output", 2)["ratio"] == "103/100")
	assert tests.pages.eventually(lambda: text.input_value() == "×1.03")

	phone.keyboard.press("ArrowDown")
	phone.keyboard.press("ArrowDown")

	assert tests.pages.eventually(lambda: stand.now("output", 2)["ratio"] == "97/100")

	# Back on, the next step goes to the nearest musical value that way.
	row(phone, 3).locator(".snap-switch").tap()

	assert tests.pages.eventually(lambda: stand.now("output", 2)["snap"] is True)

	knob.focus()
	phone.keyboard.press("ArrowUp")

	assert tests.pages.eventually(lambda: stand.now("output", 2)["ratio"] == "1")


def test_typing_a_ratio_sets_it_and_a_bad_one_is_explained (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A ratio can be typed as a decimal, a fraction or a division, and one out of range goes back with the reason."""

	text = row(phone, 4).locator(".ratio-text")
	problem = row(phone, 4).locator(".problem")

	for typed, ratio, label in (("4/3", "4/3", "×4/3"), ("0.5", "1/2", "÷2"), ("÷3", "1/3", "÷3"), ("x24", "24", "×24"), ("1.37", "137/100", "×1.37")):

		type_into(text, typed)

		assert tests.pages.eventually(lambda: stand.now("output", 3)["ratio"] == ratio), typed
		assert tests.pages.eventually(lambda: text.input_value() == label), typed

	try:
		picoclock.core.ratio.parse("1000")
		words = ""
	except ValueError as error:
		words = str(error)

	type_into(text, "1000")

	assert words
	assert tests.pages.eventually(lambda: problem.inner_text() == words)
	assert tests.pages.eventually(lambda: text.input_value() == "×1.37")
	assert stand.now("output", 3)["ratio"] == "137/100"

	# The explanation is beside the output it is about, and nowhere else.
	assert row(phone, 2).locator(".problem").inner_text() == ""
	assert phone.inner_text("#tempo-problem") == ""


def test_pulse_and_role_are_folded_away_until_wanted (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The button at the end of a row opens the rest of that output's settings, and each one reaches the clock."""

	more = row(phone, 2).locator(".more")
	extra = row(phone, 2).locator(".extra")

	assert extra.is_hidden()
	assert more.get_attribute("aria-expanded") == "false"

	more.tap()

	assert extra.is_visible()
	assert more.get_attribute("aria-expanded") == "true"

	extra.locator(".pulse-kind").select_option("half")

	assert tests.pages.eventually(lambda: stand.now("output", 1)["pulse"] == "half")
	assert tests.pages.eventually(lambda: extra.locator(".pulse-field").is_hidden())

	extra.locator(".pulse-kind").select_option("length")

	assert tests.pages.eventually(lambda: stand.now("output", 1)["pulse"] == 10)
	assert tests.pages.eventually(lambda: extra.locator(".pulse-field").is_visible())

	type_into(extra.locator(".pulse"), "25")

	assert tests.pages.eventually(lambda: stand.now("output", 1)["pulse"] == 25)

	type_into(extra.locator(".pulse"), "500")

	assert tests.pages.eventually(lambda: row(phone, 2).locator(".problem").inner_text() == "A pulse lasts between 1 and 100 milliseconds, or half the gap between pulses")
	assert tests.pages.eventually(lambda: extra.locator(".pulse").input_value() == "25")

	# An output that isn't a clock has no ratio to set, so its knob and number are shut.
	extra.locator(".role").select_option("run")

	assert tests.pages.eventually(lambda: stand.now("output", 1)["role"] == "run")
	assert tests.pages.eventually(lambda: row(phone, 2).locator(".knob").get_attribute("aria-disabled") == "true")
	assert row(phone, 2).locator(".ratio-text").is_disabled()

	extra.locator(".role").select_option("clock")

	assert tests.pages.eventually(lambda: row(phone, 2).locator(".knob").get_attribute("aria-disabled") == "false")

	more.tap()

	assert extra.is_hidden()


def test_an_output_switched_off_is_dimmed_and_shut_and_comes_back_as_it_was (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The switch in a row turns that output off. Its settings stay, shown dimmed and not struck through, and can't be worked until it is on again."""

	stand.change({"do": "output", "index": 2, "name": "ratio", "value": "2/3"})

	third = row(phone, 3)
	text = third.locator(".ratio-text")

	assert tests.pages.eventually(lambda: text.input_value() == "÷1.5")

	third.locator(".more").tap()
	third.locator(".power").tap()

	assert tests.pages.eventually(lambda: stand.now("output", 2)["muted"] is True)
	assert tests.pages.eventually(lambda: "off" in (third.get_attribute("class") or "").split())
	assert not third.locator(".on").is_checked()

	# What it was set to is still there to read: fainter, and with no line through it.
	assert text.input_value() == "÷1.5"
	assert text.evaluate("element => getComputedStyle(element).textDecorationLine") == "none"
	assert third.locator(".name").evaluate("element => getComputedStyle(element).textDecorationLine") == "none"

	for part in (".knob", ".ratio-text", ".snap-switch", ".more", ".extra"):
		assert float(third.locator(part).evaluate("element => getComputedStyle(element.closest('.row > *, .extra')).opacity")) < 0.5, part

	assert float(third.locator(".power").evaluate("element => getComputedStyle(element).opacity")) == 1

	# A finger on the knob, the snap switch, the button at the end or the role does nothing.
	x, y = tests.pages.centre(phone, ".output:nth-child(3) .knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -80)

	for part in (".snap-switch", ".more", ".role"):
		x, y = tests.pages.centre(phone, ".output:nth-child(3) " + part)
		phone.touchscreen.tap(x, y)

	time.sleep(0.4)

	assert stand.now("output", 2)["ratio"] == "2/3"
	assert stand.now("output", 2)["snap"] is True
	assert stand.now("output", 2)["role"] == "clock"
	assert third.locator(".extra").is_visible()
	assert phone.evaluate("document.activeElement === document.body")

	# The other rows are untouched.
	assert row(phone, 2).locator(".on").is_checked()
	assert "off" not in (row(phone, 2).get_attribute("class") or "").split()

	third.locator(".power").tap()

	assert tests.pages.eventually(lambda: stand.now("output", 2)["muted"] is False)
	assert tests.pages.eventually(lambda: "off" not in (third.get_attribute("class") or "").split())
	assert text.input_value() == "÷1.5"

	# On again, it can be worked as before.
	x, y = tests.pages.centre(phone, ".output:nth-child(3) .knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -80)

	assert tests.pages.eventually(lambda: stand.now("output", 2)["ratio"] != "2/3")


def test_the_main_clock_can_be_switched_off_and_given_a_pulse_but_nothing_else (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Output 1's row has the switch and opens to a pulse, with no role to choose."""

	row(phone, 1).locator(".more").tap()

	extra = row(phone, 1).locator(".extra")

	assert extra.locator(".role").count() == 0

	extra.locator(".pulse-kind").select_option("half")
	row(phone, 1).locator(".power").tap()

	assert tests.pages.eventually(lambda: stand.now("output", 0)["muted"] is True)
	assert tests.pages.eventually(lambda: stand.now("output", 0)["pulse"] == "half")
	assert stand.now("output", 0)["ratio"] == "1"


def test_two_pages_open_at_once_stay_in_step (chromium: "playwright.sync_api.Browser", phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A change made on one page shows on the other, and so does one that no page made."""

	laptop = tests.pages.desktop(chromium)
	problems = open_page(laptop, stand)

	type_into(phone.locator("#tempo-text"), "133")

	assert tests.pages.eventually(lambda: laptop.input_value("#tempo-text") == "133")

	row(laptop, 3).locator(".power").click()

	assert tests.pages.eventually(lambda: "off" in (row(phone, 3).get_attribute("class") or "").split())
	assert not row(phone, 3).locator(".on").is_checked()

	row(laptop, 2).locator(".knob").focus()
	laptop.keyboard.press("PageUp")

	assert tests.pages.eventually(lambda: row(phone, 2).locator(".ratio-text").input_value() == "×2")
	assert row(laptop, 2).locator(".ratio-text").input_value() == "×2"

	laptop.click("#pause")

	assert tests.pages.eventually(lambda: phone.get_attribute("#pause", "aria-pressed") == "true")

	stand.change({"do": "tempo", "value": 8800})

	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "88")
	assert tests.pages.eventually(lambda: laptop.input_value("#tempo-text") == "88")

	# A knob dragged on one page is followed by the other, and ends where the finger stopped.
	x, y = tests.pages.centre(phone, "#tempo-knob")

	tests.pages.drag_by_touch(phone, x, y, 0, -90)

	assert tests.pages.eventually(lambda: laptop.input_value("#tempo-text") == "118")
	assert phone.input_value("#tempo-text") == "118"
	assert stand.now("tempo")["tempo"] == 11800

	assert problems == []

	laptop.context.close()


def test_the_lights_blink_in_time_and_go_out_with_the_clock (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""An output's light comes on once for each of its pulses, briefly, and never while the clock is stopped or paused."""

	# At 120 BPM output 1 pulses twice a second.
	seen = phone.evaluate(WATCH_LIGHT, [0, 3000])

	assert 5 <= seen["rises"] <= 7, seen
	assert 0.04 < seen["lit"] / seen["frames"] < 0.3, seen

	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "4"})

	seen = phone.evaluate(WATCH_LIGHT, [1, 3000])

	assert 22 <= seen["rises"] <= 26, seen

	stand.change({"do": "output", "index": 1, "name": "muted", "value": True})
	time.sleep(0.3)

	assert phone.evaluate(WATCH_LIGHT, [1, 800])["lit"] == 0

	stand.change({"do": "pause"})
	time.sleep(0.3)

	assert phone.evaluate(WATCH_LIGHT, [0, 800])["lit"] == 0

	stand.change({"do": "stop"})
	time.sleep(0.3)

	assert phone.evaluate(WATCH_LIGHT, [0, 800])["lit"] == 0

	# Started again, it blinks from the first beat.
	stand.change({"do": "start"})

	seen = phone.evaluate(WATCH_LIGHT, [0, 2000])

	assert 3 <= seen["rises"] <= 5, seen


def test_an_output_too_fast_to_blink_glows_and_a_run_output_stays_lit (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""At ×96 the light is a steady glow, and a run output's light is on for as long as the clock runs."""

	stand.change({"do": "tempo", "value": 30000})
	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "96"})
	stand.change({"do": "output", "index": 2, "name": "role", "value": "run"})
	time.sleep(0.3)

	seen = phone.evaluate(WATCH_LIGHT, [1, 500])

	assert seen["glows"] == seen["frames"], seen
	assert seen["lit"] == 0

	seen = phone.evaluate(WATCH_LIGHT, [2, 500])

	assert seen["lit"] == seen["frames"], seen

	stand.change({"do": "pause"})
	time.sleep(0.3)

	seen = phone.evaluate(WATCH_LIGHT, [2, 300])

	assert seen["lit"] == 0, seen


def test_the_rows_follow_how_many_outputs_the_pico_has (chromium: "playwright.sync_api.Browser") -> None:

	"""The page draws as many rows as it is told of, so the number is never written into it."""

	for outputs in (1, 6):

		stand = tests.stand.Stand(outputs)

		try:

			page = tests.pages.phone(chromium)
			problems = open_page(page, stand)

			assert page.locator(".output").count() == outputs
			assert page.locator(".output .knob").count() == outputs - 1
			assert problems == []

			page.context.close()

		finally:
			stand.stop()


def test_losing_the_pico_is_said_and_mended_when_it_comes_back (chromium: "playwright.sync_api.Browser") -> None:

	"""When the Pico can't be reached the page says so, can't be worked and shows no lights, and it picks up again by itself."""

	stand = tests.stand.Stand()
	page = tests.pages.phone(chromium)

	try:

		open_page(page, stand)

		assert page.inner_text("#link") == ""
		assert page.evaluate("document.getElementById('main-view').inert") is False
		assert page.evaluate(WATCH_LIGHT, [0, 1000])["rises"] >= 1

		stand.stop()

		assert tests.pages.eventually(lambda: page.inner_text("#link") == "Not connected. Trying again…")
		assert page.inner_text("#where") == ""
		assert page.evaluate("document.body.classList.contains('lost')") is True
		assert page.evaluate("document.getElementById('main-view').inert") is True
		assert page.evaluate("document.getElementById('settings-view').inert") is True

		# The clock may well play on, but the page can't know, so its lights mustn't say it does.
		dark = page.evaluate(WATCH_LIGHT, [0, 1000])

		assert dark["lit"] == 0 and dark["glows"] == 0, dark

		# It comes back at the same address with a different tempo, as after a power cut.
		stand = tests.stand.Stand(port=stand.port)
		stand.change({"do": "tempo", "value": 9900})

		assert tests.pages.eventually(lambda: page.inner_text("#link") == "", within=10)
		assert tests.pages.eventually(lambda: page.input_value("#tempo-text") == "99")
		assert page.evaluate("document.getElementById('main-view').inert") is False
		assert page.evaluate(WATCH_LIGHT, [0, 1500])["rises"] >= 1

		# And it works again.
		page.tap("#stop")

		assert tests.pages.eventually(lambda: stand.now("transport")["state"] == "stopped")

	finally:
		page.context.close()
		stand.stop()


def test_the_page_says_what_it_has_heard_so_it_goes_on_being_sent_everything (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The page answers every batch of messages, so however many changes are made it is shown each, and nothing is left on its way."""

	given_up = stand.health()["given_up"]

	# A page that never answered would be sent two of these and no more.
	for tempo in (131, 132, 133, 134, 135, 136):

		stand.change({"do": "tempo", "value": tempo * 100})

		assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == str(tempo))

	# The clock is running, so news of where it is goes on arriving and being answered.
	assert tests.pages.eventually(lambda: stand.health()["waiting"] == 0)
	assert stand.health()["pages"] >= 1
	assert stand.health()["given_up"] == given_up


def test_a_page_whose_answers_stop_arriving_is_let_go_and_comes_back_by_itself (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""A phone whose answers stop getting through is sent a little more and then nothing, is let go after a few seconds, and picks up again by itself."""

	page = tests.pages.phone(chromium)

	# This stands between the page and the stand-in, and can stop the page's answers getting through.
	arriving = [True]

	def between (socket: "playwright.sync_api.WebSocketRoute") -> None:

		"""Pass everything both ways, except the page's answers while they are stopped."""

		server = socket.connect_to_server()

		def upward (message: "str | bytes") -> None:

			"""Send one of the page's messages on to the stand-in."""

			if arriving[0] or '"heard"' not in str(message):
				server.send(message)

		socket.on_message(upward)
		server.on_message(lambda message: socket.send(message))

	stand.as_new()
	page.route_web_socket("**/api/socket", between)

	try:

		problems = open_page(page, stand)
		given_up = stand.health()["given_up"]

		arriving[0] = False

		# The clock is running, so within a second the stand-in has sent as far ahead as it will.
		time.sleep(1.0)

		stand.change({"do": "tempo", "value": 9900})
		time.sleep(0.5)

		# The page hasn't been sent the change, and doesn't know anything is wrong.
		assert page.input_value("#tempo-text") == "120"
		assert page.inner_text("#link") == ""
		assert stand.health()["waiting"] > 0

		# A few seconds on, the stand-in lets the page go, and the page says so.
		assert tests.pages.eventually(lambda: stand.health()["given_up"] == given_up + 1, within=12)

		arriving[0] = True

		# It opens the line again by itself, and shows how things stand now.
		assert tests.pages.eventually(lambda: page.input_value("#tempo-text") == "99", within=10)
		assert tests.pages.eventually(lambda: page.inner_text("#link") == "")
		assert tests.pages.eventually(lambda: stand.health()["waiting"] == 0)
		assert problems == []

	finally:
		page.context.close()


def test_only_the_pages_own_files_are_sent (stand: tests.stand.Stand) -> None:

	"""The page and its five other files are there, or the page alone when it is packed as the Pico holds it, told to be asked for afresh each time, and nothing else is."""

	def fetch (path: str) -> tuple[int, str, str]:

		"""Ask for one address, and give back the status, the kind of thing it is and how long it may be kept."""

		try:
			with urllib.request.urlopen(stand.address + path, timeout=5) as response:
				return response.status, response.headers.get("Content-Type", ""), response.headers.get("Cache-Control", "")

		except urllib.error.HTTPError as refused:
			return refused.code, "", ""

	assert fetch("/") == (200, "text/html; charset=utf-8", "max-age=0")
	assert fetch("/index.html") == (200, "text/html; charset=utf-8", "max-age=0")

	if stand.packed:

		# Packed, the page has everything inside it, and the other five aren't there to ask for.
		for name in ("page.css", "page.js", "settings.js", "knob.js", "ratio.js", "index.html.gz"):
			assert fetch("/" + name)[0] == 404, name

	else:

		assert fetch("/page.css") == (200, "text/css; charset=utf-8", "max-age=0")

		for name in ("page.js", "settings.js", "knob.js", "ratio.js"):
			assert fetch("/" + name) == (200, "text/javascript; charset=utf-8", "max-age=0")

	for path in ("/app.py", "/__init__.py", "/favicon.ico", "/..%2Fapp.py", "/static/page.js", "/page.js.gz"):
		assert fetch(path)[0] == 404, path
