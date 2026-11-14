"""The page's API says and does the same things on the Pico's Python as on a laptop's.

Most of these stand in for a page's socket themselves, so several pages can be
open at once and a check can say exactly what arrives when. The ones that go
through Microdot's own test client prove the addresses are wired up.
"""

import asyncio
import json
import os
import sys
import time

import microdot.test_client

import picoclock
import picoclock.core.clock
import picoclock.core.ratio
import picoclock.core.settings
import picoclock.web.app

import tests.checks.check_clock

# mypy treats this name as true, and nothing else does, so `typing` is never
# imported where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing

OWN_SITE = {"Origin": "http://192.168.4.1", "Host": "192.168.4.1"}
OTHER_SITE = {"Origin": "http://elsewhere.example", "Host": "192.168.4.1"}


class Network:

	"""Stands in for the Pico's WiFi."""

	def __init__ (self) -> None:

		"""Begin as the Pico's own network, with one other network in range."""

		self.mode = "own"
		self.problem = ""
		self.scans = 0

	def status (self) -> "dict[str, typing.Any]":

		"""Say how the network stands."""

		return {"mode": self.mode, "name": "PicoClock 1A2B3C", "address": "192.168.4.1", "problem": self.problem, "own": "PicoClock 1A2B3C"}

	def scan (self) -> "list[dict[str, typing.Any]]":

		"""List the one network in range."""

		self.scans += 1

		return [{"name": "Studio", "strength": -52, "secure": True}]


class Socket:

	"""Stands in for one page's socket: a check says what arrives on it, and what the app sends is kept.

	Like a real page, it answers each batch it is sent with the batch's
	number, until a check tells it to stop.
	"""

	def __init__ (self) -> None:

		"""Begin open, with nothing said either way."""

		# Every message the app has sent, in order, and the batches they came in.
		self.sent: "list[dict[str, typing.Any]]" = []
		self.batches: "list[dict[str, typing.Any]]" = []
		self.task: "typing.Any" = None

		# Set by the app when no goodbye is to be said over this socket.
		self.closed = False

		self._arriving: "list[str]" = []
		self._closed = False
		self._deaf = False
		self._answering = True
		self._answered = 0
		self._wake = asyncio.Event()

	def say (self, message: object) -> None:

		"""Have the page send a message."""

		self._arriving.append(message if isinstance(message, str) else json.dumps(message))
		self._wake.set()

	def close (self) -> None:

		"""Have the page close."""

		self._closed = True
		self._wake.set()

	def go_deaf (self) -> None:

		"""Have the page drop off the network without closing, so nothing more can be sent to it."""

		self._deaf = True

	def stop_answering (self) -> None:

		"""Have the page take what it is sent and say nothing back, as when what it is sent is stuck on the way."""

		self._answering = False

	def answer (self, number: "int | None" = None) -> None:

		"""Have the page say it has heard the last batch it was sent, or as far as the batch with this number."""

		if number is None:
			number = self.batches[-1]["batch"]

		if number > self._answered:
			self._answered = number

		self.say({"do": "heard", "batch": number})

	async def receive (self) -> str:

		"""Wait for the page's next message, as the app does."""

		while not self._arriving:

			if self._closed:
				raise OSError(32, "the page has closed")

			self._wake.clear()
			await self._wake.wait()

		return self._arriving.pop(0)

	async def send (self, data: "str | bytes") -> None:

		"""Keep a batch the app sends, and answer it as a page does."""

		if self._deaf:
			raise OSError(104, "the page has gone")

		batch = json.loads(data)

		self.batches.append(batch)
		self.sent.extend(batch["messages"])

		if self._answering:
			self.answer()

	def of (self, kind: str) -> "list[dict[str, typing.Any]]":

		"""Give the messages of one type sent so far, in order."""

		return [message for message in self.sent if message["type"] == kind]

	def unanswered (self) -> "list[int]":

		"""Give the numbers of the batches sent since the page stopped answering."""

		return [batch["batch"] for batch in self.batches if batch["batch"] > self._answered]

	def clear (self) -> None:

		"""Forget what has been sent so far."""

		self.sent = []
		self.batches = []


def make (outputs: int = 4) -> "tuple[picoclock.web.app.App, tests.checks.check_clock.Engine, Network]":

	"""Make an app on a stand-in engine, stand-in storage and a stand-in network."""

	clock, engine, storage = tests.checks.check_clock.make(outputs)
	network = Network()

	return picoclock.web.app.App(clock, network), engine, network


async def settle (app: picoclock.web.app.App) -> None:

	"""Let everything that is ready to run do so, and every open page be sent what it's behind on."""

	for _ in range(10):
		await asyncio.sleep(0)

	# The app leaves a moment between one lot of messages to a page and the next.
	# A page that has to catch up before it is sent more is not waited for.
	for _ in range(200):

		if not [page for page in app.pages if (page.stale or page.notes or page.ask or not page.greeted) and not page.short]:
			break

		await asyncio.sleep(0.005)

	for _ in range(10):
		await asyncio.sleep(0)


async def open_page (app: picoclock.web.app.App, answering: bool = True) -> Socket:

	"""Open a page on the app, and let it be greeted. One that isn't `answering` takes what it is sent and says nothing back."""

	socket = Socket()

	if not answering:
		socket.stop_answering()

	socket.task = asyncio.create_task(app.serve(socket))

	await settle(app)

	return socket


async def close_pages (app: picoclock.web.app.App, *sockets: Socket) -> None:

	"""Close these pages and wait for the app to finish with each."""

	for socket in sockets:
		socket.close()

	for socket in sockets:
		await socket.task

	await settle(app)


async def post (client: microdot.test_client.TestClient, body: object, headers: "dict[str, str] | None" = None) -> "typing.Any":

	"""Send a change the way a page without a socket would, and give back Microdot's response.

	Microdot's test client takes a dict, a list or text as a body. Its type
	hints say otherwise, so the call goes through a name with no hints.
	"""

	send: "typing.Any" = client.post

	return await send("/api/change", body=body, headers=headers)


async def through_socket (client: microdot.test_client.TestClient, page: "typing.Callable[[], typing.Iterator[str]]", headers: "dict[str, str]") -> "typing.Any":

	"""Open the socket through Microdot's test client, with a page that sends what `page` yields.

	The test client can't show what the app sends unasked, so checks made
	this way look at what the messages did. Its type hints are out here too.
	"""

	connect: "typing.Any" = client.websocket

	return await connect("/api/socket", page, headers=headers)


def fits (message: "dict[str, typing.Any]") -> bool:

	"""Say whether a message carries exactly what the app's table says its type does."""

	expected = ["type"] + list(picoclock.web.app.TO_PAGE[message["type"]])

	return sorted(message.keys()) == sorted(expected)


def by_type (messages: "list[dict[str, typing.Any]]", kind: str) -> "list[dict[str, typing.Any]]":

	"""Pick the messages of one type out of a list."""

	return [message for message in messages if message["type"] == kind]


def check_a_page_that_has_just_opened_gets_everything_in_one_request () -> None:

	"""`/api/state` gives what never changes and one of each message, in the shapes the tables list."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)

		app.clock.set_tempo(12345)
		app.clock.set_output(2, "ratio", "1.5")

		response = await client.get("/api/state")

		assert response.status_code == 200

		state = response.json
		fixed = state["fixed"]
		now = state["now"]

		assert sorted(state.keys()) == ["fixed", "now"]
		assert sorted(fixed.keys()) == sorted(picoclock.web.app.FIXED)
		assert fixed["version"] == picoclock.__version__
		assert fixed["slowest_tempo"] == 2000
		assert fixed["fastest_tempo"] == 30000
		assert fixed["slowest_ratio"] == "1/128"
		assert fixed["fastest_ratio"] == "96"
		assert len(fixed["snaps"]) == len(picoclock.core.ratio.SNAPS)
		assert {"ratio": "3/2", "label": "×1.5"} in fixed["snaps"]
		assert fixed["roles"] == ["clock", "start", "stop", "run"]

		for message in now:
			assert fits(message), message

		assert len(now) == 8
		assert len(by_type(now, "output")) == 4
		assert sorted(message["index"] for message in by_type(now, "output")) == [0, 1, 2, 3]

		tempo = by_type(now, "tempo")[0]

		assert tempo["tempo"] == 12345
		assert tempo["text"] == "123.45"

		output = [message for message in by_type(now, "output") if message["index"] == 2][0]

		assert output["ratio"] == "3/2"
		assert output["label"] == "×1.5"
		assert output["snap"] is True
		assert output["muted"] is False
		assert output["pulse"] == 10
		assert output["role"] == "clock"

		assert by_type(now, "transport")[0]["state"] == "stopped"
		assert by_type(now, "clock")[0] == {"type": "clock", "position": -1, "faults": 0}

		network_now = by_type(now, "network")[0]

		assert network_now["mode"] == "own"
		assert network_now["name"] == "PicoClock 1A2B3C"
		assert network_now["address"] == "192.168.4.1"
		assert network_now["own"] == "PicoClock 1A2B3C"
		assert network_now["host_name"] == "picoclock"
		assert network_now["saved"] == ""

	tests.checks.check_clock.run(work())


def check_a_change_sent_as_json_is_carried_out () -> None:

	"""A POST with a JSON body changes the clock, and the answer says so."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)

		response = await post(client, {"do": "tempo", "value": 13000})

		assert response.status_code == 200
		assert response.json == {"ok": True}
		assert app.clock.settings.tempo == 13000

		response = await post(client, {"do": "tempo", "text": " 98.7 "}, dict(OWN_SITE))

		assert response.status_code == 200
		assert app.clock.settings.tempo == 9870

		response = await post(client, {"do": "start"})

		assert response.status_code == 200
		assert engine.calls == [("set_tempo", 9870), ("press", "start")]

		response = await post(client, {"do": "scan"})

		assert response.status_code == 200
		assert response.json == {"type": "networks", "found": [{"name": "Studio", "strength": -52, "secure": True}]}

	tests.checks.check_clock.run(work())


def check_a_bad_value_is_refused_in_the_settings_own_words () -> None:

	"""What the page is told is the very message the settings give, with a status of 400."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)

		refused = [
			({"do": "tempo", "value": 30001}, "The tempo has to be between 20 and 300 BPM"),
			({"do": "tempo", "value": 120.5}, "A tempo is a whole number of hundredths of a BPM"),
			({"do": "tempo", "text": "12o"}, "Type a tempo as a number of beats a minute, such as 120 or 123.45"),
			({"do": "tempo", "text": "120.555"}, "A tempo takes at most two decimal places"),
			({"do": "tempo", "text": 120}, "A typed tempo is sent as text"),
			({"do": "tempo"}, "A tempo is a whole number of hundredths of a BPM"),
			({"do": "output", "index": 0, "name": "ratio", "value": "2"}, "Output 1 is the main clock, and always pulses once a beat"),
			({"do": "output", "index": 9, "name": "ratio", "value": "2"}, "There is no such output"),
			({"do": "output", "index": 1, "name": "pulse", "value": 0}, "A pulse lasts between 1 and 100 milliseconds, or half the gap between pulses"),
			({"do": "output", "index": 1}, "An output has a ratio, a snap switch, a mute, a pulse and a role"),
			({"do": "own_password", "value": "short"}, "The password needs between 8 and 63 characters"),
			({"do": "join", "name": "", "password": ""}, "A network's name has between 1 and 32 characters"),
			({"do": "rewind"}, "That isn't something PicoClock can be told to do"),
			({}, "That isn't something PicoClock can be told to do"),
		]

		for message, words in refused:

			response = await post(client, message)

			assert response.status_code == 400, message
			assert response.json == {"error": words}, response.json

		app.clock.flush()

		assert app.clock.settings.to_dict() == picoclock.core.settings.Settings(4).to_dict()
		assert engine.calls == []

	tests.checks.check_clock.run(work())


def check_a_change_must_be_json_and_from_the_pages_own_site () -> None:

	"""A form, a bare link or another site's page can't change the clock."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)
		wanted = json.dumps({"do": "tempo", "value": 20000})

		# What a form on another site can send without the browser asking first.
		response = await post(client, wanted, {"Content-Type": "text/plain"})

		assert response.status_code == 400
		assert response.json == {"error": "A change is sent as JSON"}

		response = await post(client, "do=tempo&value=20000", {"Content-Type": "application/x-www-form-urlencoded"})

		assert response.status_code == 400

		response = await post(client, "{not json", {"Content-Type": "application/json"})

		assert response.status_code == 400

		response = await post(client, [1, 2])

		assert response.status_code == 400

		# A link can only ask for something, never send it.
		response = await client.get("/api/change")

		assert response.status_code in (404, 405)

		response = await post(client, {"do": "tempo", "value": 20000}, dict(OTHER_SITE))

		assert response.status_code == 403
		assert response.json == {"error": "PicoClock only takes changes from its own page"}

		assert app.clock.settings.tempo == 12000

	tests.checks.check_clock.run(work())


def check_the_socket_is_wired_up_and_refuses_another_sites_page () -> None:

	"""A message sent through Microdot's own socket is carried out, unless the page is some other site's."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)

		def page () -> "typing.Iterator[str]":
			yield json.dumps({"do": "tempo", "value": 14000})
			yield json.dumps({"do": "start"})

		response = await through_socket(client, page, dict(OTHER_SITE))

		assert response.status_code == 403
		assert app.clock.settings.tempo == 12000
		assert app.pages == []

		await through_socket(client, page, dict(OWN_SITE))

		assert app.clock.settings.tempo == 14000
		assert engine.calls == [("set_tempo", 14000), ("press", "start")]
		assert app.pages == []

	tests.checks.check_clock.run(work())


def check_a_page_is_greeted_with_its_number_and_everything () -> None:

	"""The first thing a page is sent holds its own number and all it needs to draw itself."""

	async def work () -> None:

		app, engine, network = make()

		first = await open_page(app)
		second = await open_page(app)

		assert len(first.sent) == 1
		assert len(second.sent) == 1

		hello = first.sent[0]

		assert fits(hello)
		assert hello["type"] == "hello"
		assert hello["you"] == 1
		assert second.sent[0]["you"] == 2
		assert hello["fixed"] == app.state()["fixed"]
		assert len(hello["now"]) == 8

		for message in hello["now"]:
			assert fits(message), message

		await close_pages(app, first, second)

	tests.checks.check_clock.run(work())


def check_a_change_from_one_page_reaches_every_page () -> None:

	"""Two pages open at once stay in step, and each can tell whose change it was."""

	async def work () -> None:

		app, engine, network = make()
		first = await open_page(app)
		second = await open_page(app)

		first.say({"do": "tempo", "value": 9000, "id": 41})
		await settle(app)

		expected = {"type": "tempo", "tempo": 9000, "text": "90", "by": 1, "id": 41}

		assert first.sent[1:] == [expected]
		assert second.sent[1:] == [expected]

		second.say({"do": "output", "index": 3, "name": "muted", "value": True})
		await settle(app)

		assert first.of("output") == second.of("output")
		assert len(first.of("output")) == 1

		output = first.of("output")[0]

		assert fits(output)
		assert output["index"] == 3
		assert output["muted"] is True
		assert output["by"] == 2
		assert output["id"] == 0

		# A page that opens later begins from how things now stand.
		third = await open_page(app)
		now = third.sent[0]["now"]

		assert by_type(now, "tempo")[0]["tempo"] == 9000
		assert [message for message in by_type(now, "output") if message["index"] == 3][0]["muted"] is True

		await close_pages(app, first, second, third)

	tests.checks.check_clock.run(work())


def check_a_page_is_only_sent_the_latest_of_a_burst () -> None:

	"""However fast a knob is dragged, the other pages are sent where it has got to, not every step."""

	async def work () -> None:

		app, engine, network = make()
		dragging = await open_page(app)
		watching = await open_page(app)

		for step in range(60):
			dragging.say({"do": "tempo", "value": 10000 + step * 100, "id": step + 1})

		await settle(app)

		assert app.clock.settings.tempo == 15900

		for socket in (dragging, watching):
			assert socket.of("tempo") == [{"type": "tempo", "tempo": 15900, "text": "159", "by": 1, "id": 60}]

		app.clock.flush()

		assert engine.calls == [("set_tempo", 15900)]

		# Even when the app could send between every two steps, it sends only a few.
		dragging.clear()
		watching.clear()

		for step in range(60):
			dragging.say({"do": "tempo", "value": 20000 + step * 100, "id": step + 61})
			await asyncio.sleep(0)
			await asyncio.sleep(0)

		await settle(app)

		for socket in (dragging, watching):
			assert 1 <= len(socket.of("tempo")) <= 6, len(socket.of("tempo"))
			assert socket.of("tempo")[-1] == {"type": "tempo", "tempo": 25900, "text": "259", "by": 1, "id": 120}

		await close_pages(app, dragging, watching)

	tests.checks.check_clock.run(work())


def check_a_refusal_goes_only_to_the_page_that_asked_and_puts_its_control_back () -> None:

	"""The page that sent a bad value is told why and sent the true value, and no other page hears of it."""

	async def work () -> None:

		app, engine, network = make()
		asking = await open_page(app)
		other = await open_page(app)

		asking.say({"do": "tempo", "text": "999", "id": 7})
		await settle(app)

		assert other.sent[1:] == []
		assert asking.of("error") == [{"type": "error", "message": "The tempo has to be between 20 and 300 BPM", "do": "tempo", "id": 7}]
		assert fits(asking.of("error")[0])
		assert [message["tempo"] for message in asking.of("tempo")] == [12000]

		asking.clear()
		asking.say({"do": "output", "index": 2, "name": "ratio", "value": "1000"})
		await settle(app)

		assert other.sent[1:] == []
		assert len(asking.of("error")) == 1
		assert asking.of("error")[0]["do"] == "output"
		assert [message["index"] for message in asking.of("output")] == [2]

		asking.clear()
		asking.say({"do": "host_name", "value": "no spaces allowed"})
		await settle(app)

		assert len(asking.of("error")) == 1
		assert len(asking.of("network")) == 1

		# Things that aren't messages at all are refused too, and nothing breaks.
		asking.clear()

		for nonsense in ("{", "[1, 2]", "7", json.dumps({"do": ["start"]}), json.dumps({"do": "tempo", "value": 12100, "id": "x"})):
			asking.say(nonsense)

		await settle(app)

		assert len(asking.of("error")) == 4
		assert asking.of("error")[0]["do"] == ""
		assert asking.of("error")[0]["id"] == 0
		assert app.clock.settings.tempo == 12100
		assert asking.of("tempo")[-1]["id"] == 0

		await close_pages(app, asking, other)

	tests.checks.check_clock.run(work())


def check_start_pause_and_stop_from_a_page () -> None:

	"""The page's three buttons reach the engine, and every page is told what the clock is now doing."""

	async def work () -> None:

		app, engine, network = make()
		first = await open_page(app)
		second = await open_page(app)

		for command in ("start", "start", "pause", "pause", "start", "stop", "stop"):
			first.say({"do": command})
			await settle(app)

		assert engine.calls == [("press", command) for command in ("start", "start", "pause", "pause", "start", "stop", "stop")]

		for socket in (first, second):
			assert [message["state"] for message in socket.of("transport")] == ["running", "paused", "running", "stopped"]
			assert [message["by"] for message in socket.of("transport")] == [1, 1, 1, 1]

		await close_pages(app, first, second)

	tests.checks.check_clock.run(work())


def check_a_stop_is_heard_before_where_the_clock_then_is () -> None:

	"""However a stop and the next look at the clock fall, a page hears that the clock stopped before it hears that it is back at nothing.

	A page works out its lights from the two together. Sent the other way
	round, whatever waits for the stop has already let the position go by.
	"""

	async def work () -> None:

		app, engine, network = make()
		page = await open_page(app)

		app.clock.press("start")
		engine.position = 2500
		app.look()
		await settle(app)

		page.clear()

		# Both happen before the page can be sent either.
		app.clock.press("stop")
		engine.position = -1
		app.look()
		await settle(app)

		assert [message["type"] for message in page.sent] == ["transport", "clock"]
		assert page.sent[0]["state"] == "stopped"
		assert page.sent[1]["position"] == -1
		assert len(page.batches) == 1

		await close_pages(app, page)

	tests.checks.check_clock.run(work())


def check_a_ratio_comes_back_in_the_form_the_page_shows () -> None:

	"""However a ratio is typed, every page gets it as a fraction to send back and a label to show."""

	async def work () -> None:

		app, engine, network = make()
		page = await open_page(app)

		typed = [("0.75", "3/4", "×0.75"), ("÷3", "1/3", "÷3"), ("x24", "24", "×24"), ("4/3", "4/3", "×4/3"), ("1", "1", "×1")]

		for text, ratio, label in typed:

			page.clear()
			page.say({"do": "output", "index": 1, "name": "ratio", "value": text})
			await settle(app)

			assert len(page.sent) == 1
			assert page.sent[0]["ratio"] == ratio
			assert page.sent[0]["label"] == label

		page.clear()
		page.say({"do": "output", "index": 1, "name": "pulse", "value": "half"})
		page.say({"do": "output", "index": 1, "name": "role", "value": "run"})
		page.say({"do": "output", "index": 1, "name": "snap", "value": False})
		await settle(app)

		assert len(page.sent) == 1
		assert page.sent[0]["pulse"] == "half"
		assert page.sent[0]["role"] == "run"
		assert page.sent[0]["snap"] is False

		await close_pages(app, page)

	tests.checks.check_clock.run(work())


def check_a_reset_sends_every_page_everything_afresh () -> None:

	"""After a factory reset each page is sent one of every message, all as new."""

	async def work () -> None:

		app, engine, network = make()
		first = await open_page(app)
		second = await open_page(app)

		first.say({"do": "tempo", "value": 20000})
		first.say({"do": "output", "index": 1, "name": "ratio", "value": "8"})
		first.say({"do": "join", "name": "Studio", "password": "a password"})
		await settle(app)

		first.clear()
		second.clear()
		second.say({"do": "reset", "id": 5})
		await settle(app)

		for socket in (first, second):

			assert len(socket.sent) == 8

			for message in socket.sent:
				assert fits(message), message

			assert socket.of("tempo")[0]["tempo"] == 12000
			assert socket.of("tempo")[0]["by"] == 2
			assert socket.of("tempo")[0]["id"] == 5
			assert socket.of("network")[0]["saved"] == ""
			assert [message["ratio"] for message in socket.of("output")] == ["1"] * 4

		await close_pages(app, first, second)

	tests.checks.check_clock.run(work())


def check_where_the_clock_is_goes_out_only_when_it_has_moved () -> None:

	"""Each look sends the clock's position if it has changed since the last, and nothing if it hasn't."""

	async def work () -> None:

		app, engine, network = make()

		# With no page open there is nobody to tell, and nothing fails.
		app.look()

		page = await open_page(app)
		page.clear()

		app.look()
		await settle(app)

		# The first look has nothing to compare with, so it sends both.
		assert page.of("clock") == [{"type": "clock", "position": -1, "faults": 0}]
		assert len(page.of("network")) == 1

		page.clear()
		app.look()
		await settle(app)

		assert page.sent == []

		engine.position = 2500
		app.look()
		await settle(app)
		engine.position = 2750
		engine.faults = 1
		app.look()
		await settle(app)

		assert page.sent == [{"type": "clock", "position": 2500, "faults": 0}, {"type": "clock", "position": 2750, "faults": 1}]

		# The network moving on by itself is seen the same way.
		page.clear()
		network.mode = "joined"
		app.look()
		await settle(app)

		assert len(page.sent) == 1
		assert page.sent[0]["mode"] == "joined"

		await close_pages(app, page)

	tests.checks.check_clock.run(work())


def check_looking_goes_on_by_itself_while_the_app_runs () -> None:

	"""With `run` going, a page hears where the clock is without anything asking."""

	async def work () -> None:

		app, engine, network = make()
		page = await open_page(app)
		task = asyncio.create_task(app.run())

		engine.position = 1000
		await asyncio.sleep(picoclock.web.app.LOOK_EVERY / 1000 + 0.2)

		assert {"type": "clock", "position": 1000, "faults": 0} in page.sent

		task.cancel()
		await close_pages(app, page)

	tests.checks.check_clock.run(work())


def check_a_change_no_page_made_still_reaches_the_pages () -> None:

	"""A press of a hardware button goes through the clock, so every page hears of it, from nobody."""

	async def work () -> None:

		app, engine, network = make()
		page = await open_page(app)

		app.clock.press("start")
		app.clock.set_tempo(8000)
		await settle(app)

		assert page.of("transport") == [{"type": "transport", "state": "running", "by": 0, "id": 0}]
		assert page.of("tempo") == [{"type": "tempo", "tempo": 8000, "text": "80", "by": 0, "id": 0}]

		await close_pages(app, page)

	tests.checks.check_clock.run(work())


def check_the_networks_in_range_go_only_to_the_page_that_asked () -> None:

	"""A scan's answer is for the page that asked, and a scan with no WiFi is refused."""

	async def work () -> None:

		app, engine, network = make()
		asking = await open_page(app)
		other = await open_page(app)

		asking.say({"do": "scan"})
		await settle(app)

		assert other.sent[1:] == []
		assert asking.sent[1:] == [{"type": "networks", "found": [{"name": "Studio", "strength": -52, "secure": True}]}]
		assert fits(asking.sent[1])
		assert network.scans == 1

		await close_pages(app, asking, other)

		clock, engine, storage = tests.checks.check_clock.make()
		bare = picoclock.web.app.App(clock)
		page = await open_page(bare)

		assert by_type(page.sent[0]["now"], "network")[0]["mode"] == "none"

		page.say({"do": "scan"})
		await settle(bare)

		assert [message["message"] for message in page.of("error")] == ["There is no WiFi here to look for networks with"]

		await close_pages(bare, page)

	tests.checks.check_clock.run(work())


def check_network_changes_are_remembered_and_no_password_is_ever_sent () -> None:

	"""Joining, forgetting, the Pico's own password and its name all reach the settings, and passwords stay there."""

	async def work () -> None:

		app, engine, network = make()
		first = await open_page(app)
		second = await open_page(app)
		settings = app.clock.settings

		first.say({"do": "join", "name": "Studio", "password": "studio secret"})
		await settle(app)

		assert settings.network_name == "Studio"
		assert settings.network_password == "studio secret"
		assert second.of("network")[-1]["saved"] == "Studio"
		assert second.of("network")[-1]["by"] == 1

		first.say({"do": "own_password", "value": "pico's secret"})
		first.say({"do": "host_name", "value": "Drum-Clock"})
		await settle(app)

		assert settings.own_password == "pico's secret"
		assert settings.host_name == "drum-clock"
		assert second.of("network")[-1]["host_name"] == "drum-clock"

		# An open network has no password to give.
		second.say({"do": "join", "name": "Cafe"})
		await settle(app)

		assert settings.network_name == "Cafe"
		assert settings.network_password == ""

		second.say({"do": "forget"})
		await settle(app)

		assert settings.network_name == ""
		assert first.of("network")[-1]["saved"] == ""

		third = await open_page(app)
		everything = json.dumps(first.sent + second.sent + third.sent + [app.state()])

		assert "secret" not in everything
		assert "password" not in everything

		await close_pages(app, first, second, third)

	tests.checks.check_clock.run(work())


def check_a_page_that_closes_or_drops_away_is_forgotten () -> None:

	"""Once a page has gone nothing more is kept for it, and the pages still open carry on."""

	async def work () -> None:

		app, engine, network = make()
		staying = await open_page(app)
		closing = await open_page(app)
		dropping = await open_page(app)

		assert len(app.pages) == 3

		await close_pages(app, closing)

		assert len(app.pages) == 2

		# A phone that walks out of range doesn't say goodbye: sending to it fails.
		dropping.go_deaf()
		staying.say({"do": "tempo", "value": 10100})
		await settle(app)

		assert len(app.pages) == 1
		assert staying.of("tempo")[-1]["tempo"] == 10100

		staying.say({"do": "tempo", "value": 10200})
		await settle(app)

		assert staying.of("tempo")[-1]["tempo"] == 10200

		await close_pages(app, staying, dropping)

		assert app.pages == []

		# With nobody left, changes still work.
		app.clock.set_tempo(10300)
		app.look()

	tests.checks.check_clock.run(work())


def looks (milliseconds: int) -> int:

	"""Give how many looks the app takes in this long."""

	return milliseconds // picoclock.web.app.LOOK_EVERY


def check_messages_come_in_numbered_batches_and_a_page_answers_each () -> None:

	"""Everything sent over a socket is in a batch, counted from 1 for each page, and what changes together goes together."""

	async def work () -> None:

		app, engine, network = make()
		first = await open_page(app)
		second = await open_page(app)

		# The first batch is empty, to see that the page is there, and the greeting follows its answer.
		for socket in (first, second):

			assert [batch["batch"] for batch in socket.batches] == [1, 2]
			assert sorted(socket.batches[0].keys()) == sorted(picoclock.web.app.BATCH)
			assert socket.batches[0]["messages"] == []
			assert [message["type"] for message in socket.batches[1]["messages"]] == ["hello"]

		# A reset changes everything at once, and it goes to each page as one batch, in the same order every time.
		second.say({"do": "reset", "id": 5})
		await settle(app)

		for socket in (first, second):

			assert [batch["batch"] for batch in socket.batches] == [1, 2, 3]
			assert [message["type"] for message in socket.batches[2]["messages"]] == ["tempo", "transport", "clock", "network", "output", "output", "output", "output"]
			assert [message["index"] for message in socket.batches[2]["messages"][4:]] == [0, 1, 2, 3]

		# An answer for one page alone comes before the news it goes with.
		first.say({"do": "tempo", "text": "999", "id": 7})
		await settle(app)

		assert [message["type"] for message in first.batches[3]["messages"]] == ["error", "tempo"]
		assert len(second.batches) == 3

		# Every batch was answered, so nothing is taken to be still on its way.
		assert app.health()["waiting"] == 0

		await close_pages(app, first, second)

		# With more outputs than one batch has room for, the rest follow in the next.
		big, engine, network = make(8)
		page = await open_page(big)

		page.say({"do": "reset"})
		await settle(big)

		assert [batch["batch"] for batch in page.batches] == [1, 2, 3, 4]
		assert len(json.dumps(page.batches[2])) <= picoclock.web.app.MOST_BATCH
		assert len(page.batches[2]["messages"]) + len(page.batches[3]["messages"]) == 12
		assert [message["index"] for message in page.of("output")] == [0, 1, 2, 3, 4, 5, 6, 7]

		await close_pages(big, page)

	tests.checks.check_clock.run(work())


def check_a_page_that_falls_behind_is_sent_no_more_until_it_catches_up_and_then_only_the_latest () -> None:

	"""A page that hasn't said it heard what it was sent is sent a little more and then nothing, and loses nothing by it."""

	async def work () -> None:

		app, engine, network = make()
		ahead = picoclock.web.app.AHEAD
		keeping_up = await open_page(app)
		behind = await open_page(app)

		behind.stop_answering()

		for step in range(6):
			keeping_up.say({"do": "tempo", "value": 10000 + step * 100, "id": step + 1})
			await settle(app)

		# The page that answers heard every step, and the other only as many as it may be ahead by.
		assert [message["tempo"] for message in keeping_up.of("tempo")] == [10000, 10100, 10200, 10300, 10400, 10500]
		assert [message["tempo"] for message in behind.of("tempo")] == [10000 + step * 100 for step in range(ahead)]
		assert len(behind.batches) == 2 + ahead
		assert app.health()["waiting"] > 0

		# Other things change meanwhile, and still nothing more is sent.
		keeping_up.say({"do": "output", "index": 2, "name": "ratio", "value": "3/2"})
		keeping_up.say({"do": "start"})
		await settle(app)

		assert len(behind.batches) == 2 + ahead

		# A number that makes no sense, one never sent and ones already counted change nothing, and nothing is said about them.
		for nonsense in (None, "3", True, 99, 0, -1, 1, 2):
			behind.say({"do": "heard", "batch": nonsense})

		behind.say({"do": "heard"})
		await settle(app)

		assert len(behind.batches) == 2 + ahead
		assert behind.of("error") == []

		# Once it says it has caught up, it is sent how things now stand, once each.
		before = len(behind.sent)
		behind.answer()
		await settle(app)

		caught_up = behind.sent[before:]

		assert [message["type"] for message in caught_up] == ["tempo", "transport", "output"]
		assert caught_up[0]["tempo"] == 10500
		assert caught_up[1]["state"] == "running"
		assert caught_up[2]["ratio"] == "3/2"
		assert len(behind.batches) == 3 + ahead

		# It still isn't answering, so that batch is on its way and nothing else is.
		assert app.health()["waiting"] == len(json.dumps(behind.batches[-1]))

		behind.answer()
		await settle(app)

		assert app.health()["waiting"] == 0

		await close_pages(app, keeping_up, behind)

	tests.checks.check_clock.run(work())


def check_a_page_that_stops_answering_is_given_up_on_and_one_that_is_only_slow_is_not () -> None:

	"""A batch left unanswered for too long closes the page's socket with no goodbye, and the other pages carry on."""

	async def work () -> None:

		app, engine, network = make()
		deadline = looks(picoclock.web.app.DEAF_FOR)
		staying = await open_page(app)
		slow = await open_page(app)
		gone = await open_page(app)

		slow.stop_answering()
		gone.stop_answering()

		staying.say({"do": "tempo", "value": 9000})
		await settle(app)

		# Just short of the deadline all three are still open.
		for _ in range(deadline - 1):
			app.look()

		await settle(app)

		assert len(app.pages) == 3

		# By now each has been sent a second batch. The slow one answers the first of its
		# two in time, and is given the whole time again for the other.
		assert len(slow.unanswered()) == 2

		slow.answer(slow.unanswered()[0])

		# Saying again what it said long ago buys the other one no more time.
		gone.answer(1)
		await settle(app)

		app.look()
		await settle(app)

		assert len(app.pages) == 2
		assert gone.task.done()
		assert gone.closed is True
		assert staying.closed is False
		assert app.health()["given_up"] == 1
		assert app.health()["pages"] == 2

		# What was on its way to the page that went is no longer counted.
		for _ in range(deadline - 2):
			app.look()

		await settle(app)

		assert len(app.pages) == 2

		slow.answer()
		await settle(app)

		assert app.health()["waiting"] == 0

		staying.say({"do": "tempo", "value": 9100})
		await settle(app)

		assert staying.of("tempo")[-1]["tempo"] == 9100
		assert slow.of("tempo")[-1]["tempo"] == 9100

		await close_pages(app, staying, slow, gone)

		assert slow.closed is False
		assert app.pages == []

	tests.checks.check_clock.run(work())


def check_a_page_sent_nothing_for_a_while_is_asked_whether_it_is_still_there () -> None:

	"""With nothing changing, a page is sent an empty batch now and then, so one that has gone is found out."""

	async def work () -> None:

		app, engine, network = make()
		there = await open_page(app)
		gone = await open_page(app)

		# The first look tells them where the clock and the network are, and after that nothing changes.
		app.look()
		await settle(app)

		gone.stop_answering()
		before = len(there.batches)

		for _ in range(looks(picoclock.web.app.QUIET_FOR) - 1):
			app.look()

		await settle(app)

		assert len(there.batches) == before

		app.look()
		await settle(app)

		for socket in (there, gone):
			assert len(socket.batches) == before + 1
			assert socket.batches[-1]["messages"] == []

		# The one that answers stays, and is asked again in its turn. The one that doesn't is given up on.
		for _ in range(looks(picoclock.web.app.DEAF_FOR)):
			app.look()

		await settle(app)

		assert len(app.pages) == 1
		assert gone.closed is True
		assert len(there.batches) == before + 2
		assert there.batches[-1]["messages"] == []

		await close_pages(app, there, gone)

	tests.checks.check_clock.run(work())


def check_all_the_pages_together_are_never_sent_more_than_there_is_room_for () -> None:

	"""However many pages are behind, what is on its way to them all stays within the room there is, and a new page waits its turn."""

	async def work () -> None:

		app, engine, network = make()
		room = picoclock.web.app.ROOM
		sockets = []

		for _ in range(5):
			sockets.append(await open_page(app))

		for socket in sockets:
			socket.stop_answering()

		# Everything changes at once, for five pages that aren't answering.
		sockets[0].say({"do": "reset"})
		await settle(app)

		waiting = app.health()["waiting"]

		assert 0 < waiting <= room
		assert waiting == sum(len(json.dumps(batch)) for socket in sockets for batch in socket.batches if batch["batch"] in socket.unanswered())

		# Not every page could be sent all of it.
		assert sum(len(socket.sent) for socket in sockets) < 5 * 9

		# A page that opens now can't be greeted yet.
		late = await open_page(app)

		assert late.sent == []
		assert app.health()["waiting"] == waiting

		app.clock.set_tempo(7700)
		await settle(app)

		assert app.health()["waiting"] <= room

		# As each page answers, room is made for the others, and for the greeting.
		for _ in range(6):

			for socket in sockets:

				if socket.unanswered():
					socket.answer()

			await settle(app)

			assert app.health()["waiting"] <= room

		assert late.sent[0]["type"] == "hello"
		assert by_type(late.sent[0]["now"], "tempo")[0]["tempo"] == 7700

		# Its greeting was put together when it was sent, so there was nothing to add.
		assert len(late.sent) == 1

		# Every page ends up knowing how things stand.
		for socket in sockets:

			assert socket.of("tempo")[-1]["tempo"] == 7700
			assert sorted(message["index"] for message in socket.of("output")) == [0, 1, 2, 3]

		for socket in sockets:
			socket.answer()

		await settle(app)

		assert app.health()["waiting"] == 0

		await close_pages(app, late, *sockets)

	tests.checks.check_clock.run(work())


def check_a_page_is_greeted_only_once_it_has_shown_it_is_taking_what_it_is_sent () -> None:

	"""A phone that asks for the socket and then takes nothing is sent next to nothing, and keeps no other phone waiting."""

	async def work () -> None:

		app, engine, network = make()
		stuck = []

		# More of them than there would be room to greet.
		for _ in range(4):
			stuck.append(await open_page(app, answering=False))

		for socket in stuck:
			assert [batch["messages"] for batch in socket.batches] == [[]]

		assert 0 < app.health()["waiting"] < 200

		# A phone that does answer is greeted at once all the same, and kept up to date.
		page = await open_page(app)

		assert [message["type"] for message in page.sent] == ["hello"]

		page.say({"do": "tempo", "value": 9000})
		app.look()
		await settle(app)

		assert page.of("tempo")[-1]["tempo"] == 9000
		assert len(page.of("clock")) == 1

		# The ones that took nothing are still owed their greeting, and are sent nothing else meanwhile.
		for socket in stuck:
			assert len(socket.batches) == 1

		# One that answers late is greeted then, with how things stand by then.
		stuck[0].answer()
		await settle(app)

		assert [message["type"] for message in stuck[0].sent] == ["hello"]
		assert by_type(stuck[0].sent[0]["now"], "tempo")[0]["tempo"] == 9000

		# The rest are given up on in their turn.
		for _ in range(looks(picoclock.web.app.DEAF_FOR)):
			app.look()

		await settle(app)

		assert app.health()["given_up"] == 4
		assert len(app.pages) == 1
		assert page.closed is False

		# Something that changes at the moment a page opens isn't sent ahead of its greeting, and is in it.
		prompt = Socket()
		prompt.task = asyncio.create_task(app.serve(prompt))

		await asyncio.sleep(0)

		app.clock.set_tempo(9100)
		await settle(app)

		assert prompt.batches[0]["messages"] == []
		assert [message["type"] for message in prompt.sent] == ["hello"]
		assert by_type(prompt.sent[0]["now"], "tempo")[0]["tempo"] == 9100

		await close_pages(app, page, prompt, *stuck)

	tests.checks.check_clock.run(work())


def check_how_well_things_have_kept_up_can_be_asked_for () -> None:

	"""`/api/health` counts the pages, and how often and for how long everything was held up."""

	async def work () -> None:

		app, engine, network = make()
		client = microdot.test_client.TestClient(app.web)
		page = await open_page(app)

		engine.faults = 3

		response = await client.get("/api/health")

		assert response.status_code == 200
		assert sorted(response.json.keys()) == sorted(picoclock.web.app.HEALTH)
		assert response.json == {"pages": 1, "given_up": 0, "waiting": 0, "holds": 0, "held": 0, "longest_hold": 0, "faults": 3}

		# Something that keeps everything here from running for a while is counted, once it lets go.
		held = picoclock.web.app.HELD
		picoclock.web.app.HELD = 150

		try:

			task = asyncio.create_task(app.run())

			await asyncio.sleep(picoclock.web.app.LOOK_EVERY / 1000 + 0.1)

			assert app.health()["holds"] == 0
			assert app.health()["longest_hold"] < 150

			time.sleep(picoclock.web.app.LOOK_EVERY / 1000 + 0.3)
			await asyncio.sleep(0.05)

			health = app.health()

			assert health["holds"] == 1
			assert 150 <= health["held"] <= 1000, health
			assert health["longest_hold"] == health["held"]

			task.cancel()

		finally:
			picoclock.web.app.HELD = held

		await close_pages(app, page)

	tests.checks.check_clock.run(work())


class Silent:

	"""Stands in for a connection that was opened and never says anything, or says it late."""

	def __init__ (self, after: float = 1000.0, line: bytes = b"") -> None:

		"""Say nothing for `after` seconds, and then `line`."""

		self.after = after
		self.line = line
		self.written = b""
		self.closed = False

	async def readline (self) -> bytes:

		"""Keep the server waiting."""

		await asyncio.sleep(self.after)

		line = self.line
		self.line = b""

		return line

	def get_extra_info (self, name: str) -> "tuple[str, int]":

		"""Say where the connection is from, which the server asks."""

		return ("192.168.4.16", 50000)

	async def awrite (self, data: bytes) -> None:

		"""Keep what the server sends."""

		self.written += data

	async def aclose (self) -> None:

		"""Note that the server closed the connection."""

		self.closed = True


def check_a_connection_that_never_asks_for_anything_is_closed_and_one_that_is_only_slow_is_answered () -> None:

	"""The Pico has five connections in all, so one that says nothing can't be left holding its place for good."""

	async def work () -> None:

		app, engine, network = make()
		patience = picoclock.web.app.PATIENCE
		picoclock.web.app.PATIENCE = 0.05

		try:

			# It never says anything: the server gives up on it and closes it.
			silent = Silent()
			answering = asyncio.create_task(app.web.handle_request(silent, silent))

			await asyncio.sleep(0.3)

			assert silent.closed
			assert answering.done()

			# It says what it wants within the time allowed, and is answered.
			slow = Silent(0.01, b"GET /api/state HTTP/1.1\r\n")
			await app.web.handle_request(slow, slow)

			assert slow.closed
			assert slow.written.startswith(b"HTTP/1.0 200") or slow.written.startswith(b"HTTP/1.1 200")
			assert b'"fixed"' in slow.written

		finally:
			picoclock.web.app.PATIENCE = patience

		# A page's socket, once open, may say nothing for as long as it likes.
		socket = await open_page(app)

		picoclock.web.app.PATIENCE = 0.05

		try:
			await asyncio.sleep(0.3)

			assert len(app.pages) == 1
			assert not socket.task.done()

		finally:
			picoclock.web.app.PATIENCE = patience

		await close_pages(app, socket)

	tests.checks.check_clock.run(work())


def check_the_pages_own_files_are_sent_gzipped_or_plain_and_nothing_else_is () -> None:

	"""Given the folder the page is in, the app sends its files, a gzipped copy as such, and nothing else it finds there."""

	# Kept apart for each Python, in case both run these at once.
	folder = "/tmp/picoclock-check-files-" + sys.implementation.name
	files = {
		"index.html": b"<p>the page</p>",
		"page.js.gz": b"\x1f\x8bnot really gzipped",
		"secret.txt": b"not the page's",
	}

	try:
		os.mkdir(folder)
	except OSError:
		pass

	for name in files:
		with open(folder + "/" + name, "wb") as handle:
			handle.write(files[name])

	async def work () -> None:

		clock, engine, storage = tests.checks.check_clock.make()
		client = microdot.test_client.TestClient(picoclock.web.app.App(clock, None, folder).web)

		for path in ("/", "/index.html"):

			response = await client.get(path)

			assert response.status_code == 200
			assert response.body == files["index.html"]
			assert response.headers["Content-Type"] == "text/html; charset=utf-8"
			assert response.headers["Cache-Control"] == "max-age=0"
			assert "Content-Encoding" not in response.headers

		response = await client.get("/page.js")

		assert response.status_code == 200
		assert response.body == files["page.js.gz"]
		assert response.headers["Content-Type"] == "text/javascript; charset=utf-8"
		assert response.headers["Content-Encoding"] == "gzip"

		# One of the page's files that isn't there, a file that is there but isn't the page's, and a way out of the folder.
		for path in ("/knob.js", "/secret.txt", "/page.js.gz", "/..%2Fsecret.txt", "/a/b"):

			response = await client.get(path)

			assert response.status_code == 404, path

		# The API is still there beside the page.
		response = await client.get("/api/state")

		assert response.status_code == 200

		# With no folder there is no page, only the API.
		clock, engine, storage = tests.checks.check_clock.make()
		client = microdot.test_client.TestClient(picoclock.web.app.App(clock).web)

		assert (await client.get("/")).status_code == 404
		assert (await client.get("/api/state")).status_code == 200

	try:
		tests.checks.check_clock.run(work())

	finally:

		for name in files:
			os.remove(folder + "/" + name)

		os.rmdir(folder)


def check_the_app_takes_every_message_its_table_lists_and_no_other () -> None:

	"""`FROM_PAGE` names exactly the things a page can ask for."""

	app, engine, network = make()

	examples: "dict[str, dict[str, typing.Any]]" = {
		"tempo": {"value": 12100},
		"output": {"index": 1, "name": "snap", "value": False},
		"join": {"name": "Studio", "password": "a password"},
		"own_password": {"value": "a password"},
		"host_name": {"value": "picoclock"},
		"heard": {"batch": 1},
	}

	for do in sorted(picoclock.web.app.FROM_PAGE.keys()):

		message: "dict[str, typing.Any]" = {"do": do}
		message.update(examples.get(do, {}))

		for name in message:
			assert name == "do" or name in picoclock.web.app.FROM_PAGE[do], (do, name)

		answer = app.take(message)

		assert answer is None or fits(answer)

	for unknown in ("", "restart", "state", "hello", None, 7):

		try:
			app.take({"do": unknown})
			assert False, "took a message that isn't in the table"
		except ValueError as error:
			assert str(error) == "That isn't something PicoClock can be told to do"
