"""The page's settings view works in a real browser at a phone's size, against the laptop stand-in.

The stand-in's WiFi is pretend: three made-up networks in range, which it
joins when given the right password. That is enough to try everything the
view does without a Pico.
"""

import contextlib
import json
import typing

import pytest

import picoclock
import picoclock.sim.network

import tests.pages
import tests.stand

if typing.TYPE_CHECKING:
	import playwright.sync_api


@pytest.fixture(scope="module", params=("six files", "one file"))
def stand (request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> typing.Iterator[tests.stand.Stand]:

	"""One stand-in PicoClock for all of these tests, serving the page's own six files, and then the page packed into one as the Pico holds it."""

	stand = tests.stand.Stand(static=tests.stand.packed(tmp_path_factory.mktemp("packed")) if request.param == "one file" else None)

	yield stand

	stand.stop()


@pytest.fixture()
def phone (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> "typing.Iterator[playwright.sync_api.Page]":

	"""The settings view on a phone, on a stand-in with every setting as new."""

	stand.as_new()

	page = tests.pages.phone(chromium)
	problems: list[str] = []

	page.on("pageerror", lambda error: problems.append(str(error)))
	page.on("console", lambda message: problems.append(message.text) if message.type == "error" else None)

	page.goto(stand.address + "/#settings")
	page.wait_for_function("!document.body.classList.contains('lost') && document.getElementById('wifi-status').textContent !== ''")

	yield page

	assert problems == []

	page.context.close()


def text (page: "playwright.sync_api.Page", selector: str) -> str:

	"""Read what one element says."""

	return page.inner_text(selector).strip()


def pick (page: "playwright.sync_api.Page", name: str) -> None:

	"""Look for networks and pick one from the list by its name."""

	page.tap("#scan")
	page.locator(".found", has_text=name).tap()


def in_sight (page: "playwright.sync_api.Page", selector: str) -> bool:

	"""Say whether the whole of one element is on the phone's screen, as the page is scrolled just now."""

	return bool(page.evaluate(
		"(selector) => { const box = document.querySelector(selector).getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight; }",
		selector,
	))


def scroll_to (page: "playwright.sync_api.Page", selector: str) -> None:

	"""Scroll one element to the top of the screen, as far down as it is on a phone with many networks in range, so the WiFi section's first lines are out of sight."""

	page.evaluate("(selector) => document.querySelector(selector).scrollIntoView({block: 'start'})", selector)

	assert not in_sight(page, "#wifi-status")


def test_the_settings_open_from_the_main_view_and_close_again (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""Settings swaps the main view for the settings, and Done or the phone's back button swaps them back."""

	page = tests.pages.phone(chromium)

	page.goto(stand.address + "/")
	page.wait_for_function("!document.body.classList.contains('lost')")

	assert page.is_visible("#main-view")
	assert page.is_hidden("#settings-view")
	assert page.is_hidden("#to-main")

	page.tap("#to-settings")

	assert tests.pages.eventually(lambda: page.is_visible("#settings-view"))
	assert page.is_hidden("#main-view")
	assert page.is_hidden("#to-settings")
	assert page.url.endswith("#settings")

	page.tap("#to-main")

	assert tests.pages.eventually(lambda: page.is_visible("#main-view"))
	assert page.is_hidden("#settings-view")

	page.tap("#to-settings")
	assert tests.pages.eventually(lambda: page.is_visible("#settings-view"))

	page.go_back()

	assert tests.pages.eventually(lambda: page.is_visible("#main-view"))

	page.context.close()


def test_it_says_how_picoclock_is_reached_and_how_it_is_doing (phone: "playwright.sync_api.Page") -> None:

	"""The view opens saying which network PicoClock is running, what its own is called, its version and its faults."""

	own = picoclock.sim.network.OWN_NAME

	assert text(phone, "#wifi-status") == "PicoClock is running its own network, %s. This page is at http://localhost." % own
	assert text(phone, "#own-status") == "It is called %s, and it is on now." % own
	assert text(phone, "#version") == "PicoClock " + picoclock.__version__
	assert text(phone, "#faults") == "Timing faults: 0, as it should be."
	assert phone.input_value("#host-name") == "picoclock"

	assert phone.is_hidden("#join")
	assert phone.is_hidden("#forget")
	assert phone.is_hidden("#reset-confirm")
	assert text(phone, "#wifi-problem") == ""


def test_the_networks_in_range_are_listed_strongest_first (phone: "playwright.sync_api.Page") -> None:

	"""Looking for networks lists each with how strong it is and whether it wants a password, and one more for any other."""

	phone.tap("#scan")

	assert tests.pages.eventually(lambda: phone.locator(".found").count() == 4)
	assert phone.locator(".found b").all_inner_texts() == ["Studio", "Upstairs", "Cafe", "Another network…"]
	assert phone.locator(".found span").all_inner_texts() == ["strong, needs a password", "fair, needs a password", "weak, open", "one that isn't listed"]
	assert text(phone, "#scan-status") == ""


def test_a_join_is_tried_first_says_where_picoclock_will_be_and_switches_when_asked (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Picking a network explains what joining does before anything is sent. The right password finds it, the page says where PicoClock will be there, and Switch joins it."""

	pick(phone, "Studio")

	assert phone.is_visible("#join")
	assert phone.is_visible("#join-password")
	assert phone.is_hidden("#join-name-field")
	assert text(phone, "#join-go") == "Join Studio"

	warning = text(phone, "#join-warning")

	assert "switch its own network off to try Studio" in warning
	assert "this page will stop responding" in warning
	assert "this page says where PicoClock will be on Studio" in warning
	assert "about 20 seconds" in warning

	# Nothing has been asked of the Pico yet.
	assert stand.now("network")["saved"] == ""

	phone.fill("#join-password", "studio wifi")
	scroll_to(phone, "#join-go")
	phone.tap("#join-go")

	# The stand-in's line never goes, so this page sees the trial come back as a phone on the Pico's own network does.
	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert stand.now("network")["saved"] == "Studio"

	assert tests.pages.eventually(lambda: text(phone, "#wifi-status") == "PicoClock can join Studio. Once it has, this page will be at http://localhost.")
	assert phone.is_visible("#trial")

	# Where PicoClock will be is brought into sight, from wherever the form that asked had been scrolled to.
	assert tests.pages.eventually(lambda: in_sight(phone, "#wifi-status") and in_sight(phone, "#trial-address"))
	assert text(phone, "#trial-address") == "http://localhost"
	assert "Note that address down" in text(phone, "#trial-words")
	assert "two minutes" in text(phone, "#trial-words")
	assert text(phone, "#switch") == "Switch to Studio now"
	assert text(phone, "#own-status") == "It is called %s, and it is on until PicoClock switches to Studio." % picoclock.sim.network.OWN_NAME
	assert phone.is_hidden("#join")

	phone.tap("#switch")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "joined")
	assert tests.pages.eventually(lambda: text(phone, "#wifi-status") == "PicoClock is on Studio. This page is at http://localhost, or http://picoclock.local.")
	assert phone.is_hidden("#trial")
	assert text(phone, "#own-status") == "It is called %s. It is off while PicoClock is on Studio." % picoclock.sim.network.OWN_NAME
	assert text(phone, "#forget") == "Leave Studio and go back to PicoClock's own network"

	# Joining another from there says it will leave the first, and where the news of it will be.
	pick(phone, "Cafe")

	warning = text(phone, "#join-warning")

	assert "leave Studio to try Cafe" in warning
	assert "join this phone to PicoClock's own network, %s, and open http://192.168.4.1" % picoclock.sim.network.OWN_NAME in warning

	phone.tap("#join-cancel")

	assert phone.is_hidden("#join")

	phone.tap("#forget")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "own")
	assert tests.pages.eventually(lambda: phone.is_hidden("#forget"))
	assert stand.now("network")["saved"] == ""


def test_a_trial_that_nobody_acts_on_switches_by_itself (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Left alone, PicoClock joins the network it found once its stay on its own network is up, which on the stand-in is five seconds."""

	pick(phone, "Studio")
	phone.fill("#join-password", "studio wifi")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "joined", within=picoclock.sim.network.STAY_FOR / 1000 + 3)
	assert tests.pages.eventually(lambda: text(phone, "#wifi-status").startswith("PicoClock is on Studio."))
	assert phone.is_hidden("#trial")


class Gate:

	"""Holds the sockets a page opens while the Pico's own network is off, and lets them through to the stand-in once it is back."""

	def __init__ (self) -> None:

		"""Begin shut, holding nothing."""

		self.open = False
		self.held: "list[playwright.sync_api.WebSocketRoute]" = []

	def hold (self, socket: "playwright.sync_api.WebSocketRoute") -> None:

		"""Keep a socket silent while the gate is shut, or let it through. Left without handlers of its own, it passes everything both ways."""

		if self.open:
			socket.connect_to_server()
		else:
			self.held.append(socket)

	def let_through (self) -> None:

		"""Open the gate, and let every socket held so far through at once."""

		self.open = True

		for socket in self.held:
			socket.connect_to_server()

		self.held = []


def cut_when (page: "playwright.sync_api.Page", do: str, last: "typing.Callable[[dict[str, typing.Any]], dict[str, typing.Any]] | None" = None, back: "Gate | None" = None) -> None:

	"""Pass the page's socket through to the stand-in, and drop it once the page has asked `do` and heard the answer.

	That is what a Pico does when it changes networks. It answers at once and
	acts half a second later, which takes the line with it, so the page hears
	its own asking come back unchanged and then nothing, and trying again
	finds nothing either. `last`, if given, makes one more word of the
	network from that answer, for the page to hear just as the line goes, as
	a Pico might just manage to say as it begins a join. With `back`, trying
	again waits at that gate, and finds the stand-in once the test lets it
	through, as a phone that stayed on PicoClock's own network finds the Pico
	once that network is back.
	"""

	gone = [False]
	asked = [False]

	def between (socket: "playwright.sync_api.WebSocketRoute") -> None:

		"""Pass one socket through, or once the line has gone, leave it silent, as one opened to a Pico that has gone.

		A socket closed here, before it has been connected anywhere, stalls
		Playwright, so a test still running when the page tries again would hang.
		"""

		if gone[0]:

			if back is not None:
				back.hold(socket)

			return

		server = socket.connect_to_server()

		def from_page (message: "str | bytes") -> None:

			"""Send one of the page's messages on, noting the one that will take the line away."""

			if json.loads(message).get("do") == do:
				asked[0] = True

			server.send(message)

		def from_stand (message: "str | bytes") -> None:

			"""Send one of the stand-in's batches on, and drop the line once the answer to the asking has gone."""

			socket.send(message)

			batch = json.loads(message)
			news = [each for each in batch["messages"] if each["type"] == "network"]

			if not (asked[0] and news):
				return

			gone[0] = True

			if last is not None:
				socket.send(json.dumps({"batch": batch["batch"] + 1, "messages": [last(news[-1])]}))

			socket.close()
			server.close()

		socket.on_message(from_page)
		server.on_message(from_stand)

	page.route_web_socket("**/api/socket", between)


@contextlib.contextmanager
def settings_cut_when (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand, do: str, last: "typing.Callable[[dict[str, typing.Any]], dict[str, typing.Any]] | None" = None, back: "Gate | None" = None) -> "typing.Iterator[playwright.sync_api.Page]":

	"""Open the settings view on a phone whose line will go as soon as it asks `do`, and close it afterwards, however the test went.

	A page left open with its socket still being passed through keeps the
	browser from closing, so a test that failed would hang instead.
	"""

	page = tests.pages.phone(chromium)

	try:
		cut_when(page, do, last, back)
		page.goto(stand.address + "/#settings")
		page.wait_for_function("!document.body.classList.contains('lost') && document.getElementById('wifi-status').textContent !== ''")

		yield page

	finally:
		page.context.close()


def test_once_picoclock_changes_networks_the_page_says_where_it_has_gone (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""The line goes as PicoClock changes networks, before the page hears what came of it, so the page says where PicoClock is going in place of trying again."""

	own = picoclock.sim.network.OWN_NAME

	stand.as_new()

	# A trial: the Pico's own network goes as soon as it is asked to try another.
	with settings_cut_when(chromium, stand, "join") as page:

		pick(page, "Studio")
		page.fill("#join-password", "studio wifi")
		scroll_to(page, "#join-go")
		page.tap("#join-go")

		assert tests.pages.eventually(lambda: text(page, "#link") == "Not connected.")
		assert text(page, "#where") == (
			f"PicoClock is trying Studio. In a few seconds its own network, {own}, comes back to say where PicoClock will be. "
			f"If this phone has gone to another network meanwhile, join it to {own} again."
		)

		# What it says is brought into sight, from wherever the button that sent PicoClock away had been scrolled to.
		assert in_sight(page, "#where")

	# A switch, from where a trial that worked leaves PicoClock. The page hears the join begin just as
	# the line goes, which is still the switch, not another trial.
	stand.as_new()
	stand.change({"do": "join", "name": "Studio", "password": "studio wifi"})

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")

	def joining (heard: "dict[str, typing.Any]") -> "dict[str, typing.Any]":

		"""Word of the join beginning: the network found, no address yet, and nothing found any more."""

		return dict(heard, mode="joining", address="", found_at="", by=0, id=0)

	with settings_cut_when(chromium, stand, "switch", joining) as page:

		page.tap("#switch")

		assert tests.pages.eventually(lambda: text(page, "#link") == "Not connected.")
		assert text(page, "#where") == "PicoClock is switching to Studio. Join this phone to Studio too, then open http://localhost."
		assert page.get_attribute("#where a", "href") == "http://localhost"

	# Leaving a network that was joined, for PicoClock's own. The switch that was asked for has been made.
	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "joined")

	with settings_cut_when(chromium, stand, "forget") as page:

		scroll_to(page, "#forget")
		page.tap("#forget")

		assert tests.pages.eventually(lambda: text(page, "#link") == "Not connected.")
		assert text(page, "#where") == f"PicoClock is going back to its own network, {own}. Join this phone to it, then open http://192.168.4.1."
		assert in_sight(page, "#where")


def test_a_trial_that_took_the_line_says_how_it_went_once_the_line_is_back (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""A phone that stays on PicoClock's own network through a trial finds the Pico again when that network is back, and the page then shows what was found, in sight."""

	stand.as_new()

	back = Gate()

	with settings_cut_when(chromium, stand, "join", back=back) as page:

		pick(page, "Studio")
		page.fill("#join-password", "studio wifi")
		scroll_to(page, "#join-go")
		page.tap("#join-go")

		assert tests.pages.eventually(lambda: text(page, "#link") == "Not connected.")
		assert in_sight(page, "#where")

		# Scrolled away while waiting, as anyone might be, and then the line comes back.
		scroll_to(page, "#own-go")
		back.let_through()

		assert tests.pages.eventually(lambda: text(page, "#link") == "" and page.is_visible("#trial"))
		assert text(page, "#wifi-status") == "PicoClock can join Studio. Once it has, this page will be at http://localhost."
		assert text(page, "#trial-address") == "http://localhost"
		assert text(page, "#where") == ""
		assert tests.pages.eventually(lambda: in_sight(page, "#wifi-status") and in_sight(page, "#trial-address"))


def test_a_join_that_fails_says_why (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""With the wrong password PicoClock comes back to its own network, says what went wrong, and keeps nothing of the network."""

	pick(phone, "Studio")
	phone.fill("#join-password", "not the password")
	scroll_to(phone, "#join-go")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: text(phone, "#wifi-problem") == "Studio didn't accept that password")
	assert "its own network" in text(phone, "#wifi-status")
	assert tests.pages.eventually(lambda: in_sight(phone, "#wifi-problem"))
	assert stand.now("network")["mode"] == "own"

	# A network that never worked isn't kept, so there is nothing to forget.
	assert stand.now("network")["saved"] == ""
	assert phone.is_hidden("#forget")

	# What went wrong stays until the next thing is asked, and goes when it is.
	pick(phone, "Studio")
	phone.fill("#join-password", "studio wifi")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert tests.pages.eventually(lambda: text(phone, "#wifi-problem") == "")


def test_a_network_still_saved_is_said_to_be_gone_back_to_by_itself (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""Back on its own network with a network still saved, as after losing it, PicoClock is said to go back to that one by itself.

	The stand-in's networks never go away, so the page is greeted as a Pico
	that has lost one greets it.
	"""

	own = picoclock.sim.network.OWN_NAME

	stand.as_new()
	page = tests.pages.phone(chromium)

	def between (socket: "playwright.sync_api.WebSocketRoute") -> None:

		"""Pass the page's socket through, with the network made one that has lost Studio, whenever the page hears of it."""

		server = socket.connect_to_server()

		def from_stand (message: "str | bytes") -> None:

			"""Send one batch on, changing the network wherever it is said, in a greeting or as news."""

			batch = json.loads(message)

			for each in batch["messages"]:
				for said in [each] + (each["now"] if each["type"] == "hello" else []):
					if said["type"] == "network":
						said.update(saved="Studio", problem="PicoClock lost Studio and couldn't get it back")

			socket.send(json.dumps(batch))

		server.on_message(from_stand)

	try:
		page.route_web_socket("**/api/socket", between)
		page.goto(stand.address + "/#settings")
		page.wait_for_function("!document.body.classList.contains('lost') && document.getElementById('wifi-status').textContent !== ''")

		assert text(page, "#wifi-status") == (
			f"PicoClock is running its own network, {own}. This page is at http://localhost. "
			f"When Studio is back in range, PicoClock goes back to it by itself, as soon as no phone is on {own}."
		)
		assert text(page, "#wifi-problem") == "PicoClock lost Studio and couldn't get it back"
		assert text(page, "#forget") == "Forget Studio"

	finally:
		page.context.close()


def test_a_password_the_pico_will_not_take_is_refused_beside_the_form (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A password too short to be one keeps the form open with the reason, and nothing is remembered."""

	pick(phone, "Studio")
	phone.fill("#join-password", "short")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: text(phone, "#join-problem") == "A network's password has between 8 and 63 characters, or none at all")
	assert phone.is_visible("#join")
	assert stand.now("network")["saved"] == ""

	# Trying again clears the reason.
	phone.fill("#join-password", "studio wifi")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert tests.pages.eventually(lambda: phone.is_hidden("#join"))


def test_an_open_network_asks_for_no_password (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A network with no password shows no box for one, and joins at a press."""

	pick(phone, "Cafe")

	assert phone.is_hidden("#join-password-field")
	assert phone.is_hidden("#join-show-switch")

	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert stand.now("network")["name"] == "Cafe"


def test_a_network_that_is_not_listed_can_be_joined_by_name (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The last choice in the list asks for a name as well as a password."""

	pick(phone, "Another network")

	assert phone.is_visible("#join-name")
	assert text(phone, "#join-go") == "Join"
	assert "try that network" in text(phone, "#join-warning")

	phone.fill("#join-name", "Upstairs")
	phone.fill("#join-password", "upstairs wifi")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")
	assert stand.now("network")["name"] == "Upstairs"

	pick(phone, "Another network")
	phone.fill("#join-name", "Garden")
	phone.fill("#join-password", "garden wifi")
	phone.tap("#join-go")

	assert tests.pages.eventually(lambda: text(phone, "#wifi-problem") == "There was no network called Garden in range")


def test_a_password_can_be_shown_while_it_is_typed (phone: "playwright.sync_api.Page") -> None:

	"""Each password box hides what's typed until its switch is flicked."""

	pick(phone, "Studio")

	assert phone.get_attribute("#join-password", "type") == "password"

	phone.tap("#join-show-switch")

	assert phone.get_attribute("#join-password", "type") == "text"

	assert phone.get_attribute("#own-password", "type") == "password"

	phone.locator("#own-form .switch").tap()

	assert phone.get_attribute("#own-password", "type") == "text"


def test_the_own_networks_password_can_be_changed (phone: "playwright.sync_api.Page") -> None:

	"""A new password is taken and said to be changed, and one too short is refused with the reason."""

	phone.fill("#own-password", "short")
	phone.tap("#own-go")

	assert tests.pages.eventually(lambda: text(phone, "#own-problem") == "The password needs between 8 and 63 characters")
	assert text(phone, "#own-done") == ""

	phone.fill("#own-password", "a better password")
	phone.tap("#own-go")

	assert tests.pages.eventually(lambda: text(phone, "#own-done") == "The password is changed.")
	assert text(phone, "#own-problem") == ""
	assert phone.input_value("#own-password") == ""


def test_its_name_on_wifi_can_be_changed (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""The name PicoClock answers to is set as typed, tidied, and a name that can't be one is refused."""

	phone.fill("#host-name", "Drum-Clock")
	phone.tap("#host-go")

	assert tests.pages.eventually(lambda: stand.now("network")["host_name"] == "drum-clock")

	phone.locator("#about").tap()

	assert tests.pages.eventually(lambda: phone.input_value("#host-name") == "drum-clock")

	phone.fill("#host-name", "no spaces")
	phone.tap("#host-go")

	assert tests.pages.eventually(lambda: text(phone, "#host-problem") == "The name can use letters, digits and hyphens, up to 32 of them, and can't begin or end with a hyphen")
	assert stand.now("network")["host_name"] == "drum-clock"

	# Once PicoClock has joined, the new name is the one the page gives beside its address.
	stand.change({"do": "join", "name": "Studio", "password": "studio wifi"})

	assert tests.pages.eventually(lambda: stand.now("network")["mode"] == "found")

	stand.change({"do": "switch"})

	assert tests.pages.eventually(lambda: text(phone, "#wifi-status").endswith(", or http://drum-clock.local."))


def test_a_reset_asks_first_and_then_puts_everything_back (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Reset shows what it will do and waits. Cancel changes nothing, and going ahead brings back a new PicoClock's settings."""

	stand.change({"do": "tempo", "value": 9900})
	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "3"})
	stand.change({"do": "join", "name": "Studio", "password": "studio wifi"})

	phone.tap("#reset-ask")

	assert phone.is_visible("#reset-confirm")
	assert phone.is_hidden("#reset-ask")
	assert "password picoclock" in text(phone, "#reset-confirm")

	phone.tap("#reset-cancel")

	assert phone.is_hidden("#reset-confirm")
	assert phone.is_visible("#reset-ask")
	assert stand.now("tempo")["tempo"] == 9900

	phone.tap("#reset-ask")
	phone.tap("#reset-go")

	assert tests.pages.eventually(lambda: stand.now("tempo")["tempo"] == 12000)
	assert stand.now("output", 1)["ratio"] == "1"
	assert stand.now("network")["saved"] == ""
	assert stand.now("transport")["state"] == "running"

	assert tests.pages.eventually(lambda: text(phone, "#reset-done") == "Every setting is as new.")
	assert phone.is_hidden("#reset-confirm")

	# The network it had joined is left half a second after the reset, so that the page hears the answer first.
	assert tests.pages.eventually(lambda: "its own network" in text(phone, "#wifi-status"))

	phone.tap("#to-main")

	assert tests.pages.eventually(lambda: phone.input_value("#tempo-text") == "120")


def test_what_another_page_does_to_the_network_shows_here (chromium: "playwright.sync_api.Browser", phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""A join made from somewhere else changes what this view says, and leaves a half-filled form here alone."""

	pick(phone, "Upstairs")
	phone.fill("#join-password", "half typed")

	stand.change({"do": "join", "name": "Cafe", "password": ""})

	assert tests.pages.eventually(lambda: text(phone, "#wifi-status").startswith("PicoClock can join Cafe."))
	assert phone.is_visible("#join")
	assert phone.input_value("#join-password") == "half typed"


def test_a_timing_fault_is_shown_plainly (chromium: "playwright.sync_api.Browser", stand: tests.stand.Stand) -> None:

	"""If the clock has ever slipped, the About panel says how many times, in the colour of a problem."""

	page = tests.pages.phone(chromium)

	# The stand-in can't slip, so this stands between it and the page and says that it has.
	slipped = [0]

	def between (socket: "playwright.sync_api.WebSocketRoute") -> None:

		"""Pass everything both ways, changing only how many faults the clock is said to have had."""

		server = socket.connect_to_server()

		def onward (message: "str | bytes") -> None:

			"""Send one of the stand-in's batches of messages on to the page."""

			heard = json.loads(message)

			for each in heard["messages"]:

				if each["type"] == "clock":
					each["faults"] = slipped[0]

			socket.send(json.dumps(heard))

		socket.on_message(lambda message: server.send(message))
		server.on_message(onward)

	stand.as_new()

	page.route_web_socket("**/api/socket", between)
	page.goto(stand.address + "/#settings")
	page.wait_for_function("document.getElementById('faults').textContent !== ''")

	assert text(page, "#faults") == "Timing faults: 0, as it should be."
	assert "problem" not in (page.get_attribute("#faults", "class") or "")

	# The clock is running, so word of where it is, and of its faults, comes four times a second.
	slipped[0] = 3

	assert tests.pages.eventually(lambda: text(page, "#faults").startswith("Timing faults: 3."))
	assert tests.pages.eventually(lambda: "problem" in (page.get_attribute("#faults", "class") or ""))

	page.context.close()


def test_the_settings_fit_a_phone_with_everything_open (phone: "playwright.sync_api.Page") -> None:

	"""With the list of networks, a join form and the reset question all showing, nothing spills off the side."""

	pick(phone, "Another network")
	phone.tap("#reset-ask")

	assert phone.evaluate("document.documentElement.scrollWidth") <= tests.pages.PHONE["width"]

	for selector in ("#scan", ".found", "#join-go", "#join-cancel", "#host-go", "#own-go", "#reset-go", "#reset-cancel", "#to-main"):

		for index in range(phone.locator(selector).count()):

			box = phone.locator(selector).nth(index).bounding_box()

			assert box is not None and box["height"] >= 44, selector


def test_the_clocks_pulses_a_beat_are_chosen_here_and_show_on_the_main_view (phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Each rate other gear uses is offered with the note it gives, four is chosen to begin with, and a choice reaches the clock, output 1's line and any output it had to slow."""

	rates = phone.locator("#rates .rate")

	assert rates.count() == 8
	assert [text.strip() for text in rates.all_inner_texts()] == ["1 · quarters", "2 · 8ths", "4 · 16ths", "8 · 32nds", "12", "16", "24 · DIN sync", "48"]
	assert phone.locator("#rates input:checked").get_attribute("value") == "4"

	stand.change({"do": "output", "index": 1, "name": "ratio", "value": "24"})

	rates.filter(has_text="24 · DIN sync").tap()

	assert tests.pages.eventually(lambda: stand.now("ppqn")["ppqn"] == 24)
	assert stand.now("output", 1)["ratio"] == "4"
	assert phone.locator("#rates input:checked").get_attribute("value") == "24"
	assert text(phone, "#ppqn-problem") == ""

	phone.tap("#to-main")

	assert tests.pages.eventually(lambda: text(phone, ".output:nth-child(1) .clock-words") == "main clock · 24 PPQN")
	assert phone.locator(".output:nth-child(2) .ratio-text").input_value() == "×4"


def test_a_choice_of_pulses_a_beat_made_elsewhere_shows_here (chromium: "playwright.sync_api.Browser", phone: "playwright.sync_api.Page", stand: tests.stand.Stand) -> None:

	"""Another page, or a reset, moves the choice shown here."""

	stand.change({"do": "ppqn", "value": 2})

	assert tests.pages.eventually(lambda: phone.locator("#rates input:checked").get_attribute("value") == "2")

	stand.change({"do": "reset"})

	assert tests.pages.eventually(lambda: phone.locator("#rates input:checked").get_attribute("value") == "4")
