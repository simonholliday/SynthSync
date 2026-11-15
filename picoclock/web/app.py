"""The page's side of PicoClock: everything a phone asks of it, and everything it tells a phone.

This never touches a pin. It changes things through the clock, which checks
each one and passes it to whatever engine is playing, so the same file serves
the page from the Pico and from a laptop.

    GET  /             the page: on the Pico one file, and on a laptop its six
    GET  /api/state    everything, for a page that has just opened
    GET  /api/health   how well the page's side of things has kept up
    POST /api/change   one message from the page, as JSON
    GET  /api/socket   a WebSocket carrying messages both ways

**What a page sends.** Each message is a JSON object that says what to do
under "do". `FROM_PAGE` lists them all.

    {"do": "tempo", "value": 12050}            in hundredths of a BPM
    {"do": "tempo", "text": "120.5"}           as typed
    {"do": "output", "index": 1, "name": "ratio", "value": "4/3"}
    {"do": "start"}   {"do": "pause"}   {"do": "stop"}
    {"do": "reset"}                            back to how a new one is
    {"do": "scan"}                             which networks are in range
    {"do": "join", "name": "...", "password": "..."}
    {"do": "forget"}                           back to the Pico's own network
    {"do": "switch"}                           now, to the network a trial join found
    {"do": "own_password", "value": "..."}
    {"do": "host_name", "value": "..."}
    {"do": "heard", "batch": 7}                the last batch it was sent

An output's "index" counts from 0, and its "name" is "ratio", "snap", "muted",
"pulse" or "role". Over the socket a message may also carry "id", a number the
page counts up, which comes back with the change it made.

**What the Pico sends.** Over the socket, messages come in numbered batches,
counted from 1 for each page. `BATCH` lists what a batch carries:

    {"batch": 7, "messages": [{"type": "tempo", ...}, {"type": "clock", ...}]}

Each message says what it is under "type". `TO_PAGE` lists them all. A page is
greeted with "hello", which holds its own number, the things that never change,
and one of each other message to begin from. After that it is sent a message
whenever something changes, whoever changed it:

    {"type": "tempo", "tempo": 12050, "text": "120.5", "by": 2, "id": 31}
    {"type": "output", "index": 1, "ratio": "4/3", "label": "×4/3", ...}
    {"type": "transport", "state": "running", "by": 0, "id": 0}
    {"type": "clock", "position": 12250, "faults": 0}
    {"type": "network", "mode": "own", "name": "...", "address": "...", ...}
    {"type": "networks", "found": [{"name": "...", "strength": -52, "secure": true}]}

"by" is the number of the page that made the change, or 0 when it wasn't a
page, and "id" is the "id" that page sent. A page dragging a knob uses the two
to tell its own changes coming back from someone else's. The clock's position
is in thousandths of a beat since it started, or -1 when that can't be said.
Passwords are never sent.

Only the page that asked gets an answer to "scan", as "networks", and only the
page that sent a message that couldn't be carried out is told so, as "error",
with words fit to show. Over POST the answer is the reply itself: `{"ok": true}`,
the networks, or `{"error": "..."}` with a status of 400.

A page is only ever sent the latest of anything, and at most 25 times a
second. If a knob moves ten times in that time, the page is sent the tenth.

**A page is only sent what it can take.** It answers each batch with "heard"
and the batch's number. It is never sent more than two batches beyond the last
it has heard, and all the open pages together never have more than about 3,000
bytes on their way to them. Whatever changes while a page is behind is sent
once it catches up, as things then stand, so holding back loses nothing. A page
that leaves a batch unanswered for eight seconds is taken to have gone and its
socket is closed, and a browser opens another when it can. A page that has
been sent nothing for five seconds is sent an empty batch, so one that has
gone is found out even when nothing is changing.

The first batch a page is sent is an empty one, and its greeting follows once
it has answered that. A greeting is most of what there is room for, so a phone
that asks for the socket and then takes nothing would otherwise keep every
other phone waiting for its own.

This matters because the Pico has about 8,000 bytes for everything waiting to
be sent to every connection. A send that finds them full holds up the page,
the buttons and the saving for ten seconds at a time, though never the clock.
A phone on a poor signal would otherwise fill them by itself.

`/api/health` says how that has gone since power-up, under the names in
`HEALTH`: how many pages are open, how many were given up on, how many bytes
are on their way, how many times everything but the clock was held up for
over a second, how long that came to and the longest of them in milliseconds,
and the clock's own count of timing faults.

A change needs a JSON body or the socket, and both refuse a request that comes
from a page on some other site, so a link elsewhere can't change the clock.

A connection that is opened and then says nothing is closed after a few
seconds. The Pico has only five connections to give, and one that never asks
for anything would otherwise keep its place for good.
"""

import asyncio
import json
import os
import time

import microdot
import microdot.websocket

import picoclock
import picoclock.core.clock
import picoclock.core.ratio
import picoclock.core.settings
import picoclock.core.timeline
import picoclock.core.transport

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:

	import typing

	Key = str | int
	Message = dict[str, typing.Any]

	class Network (typing.Protocol):

		"""What the page needs from whatever runs the WiFi."""

		def status (self) -> "Message":

			"""Say how the network stands.

			"mode" is "own", "joining", "found" or "joined". "name" is the
			network's name, "address" is where the page is on whichever
			network is up, and "problem" says why the last attempt to join
			failed, or is empty. "own" is the name of the Pico's own
			network, whether or not it is up. "found_at" is where PicoClock
			will be on a network a trial join found, once it has switched to
			it, and is empty unless the mode is "found".
			"""

		def scan (self) -> "list[Message]":

			"""List the networks in range, each with its "name", its "strength" and whether it's "secure"."""

	class Socket (typing.Protocol):

		"""One page's WebSocket."""

		async def receive (self) -> "str | bytes":

			"""Wait for the page's next message."""

		async def send (self, data: "str | bytes") -> None:

			"""Send the page a batch of messages."""

		# Whether a goodbye has already been said over it.
		closed: bool

# What a page may send: each message's "do", and what else it carries.
FROM_PAGE = {
	"tempo": ("value", "text"),
	"output": ("index", "name", "value"),
	"start": (),
	"pause": (),
	"stop": (),
	"reset": (),
	"scan": (),
	"join": ("name", "password"),
	"forget": (),
	"switch": (),
	"own_password": ("value",),
	"host_name": ("value",),
	"heard": ("batch",),
}

# What everything sent over a socket carries: its number, and the messages in it.
BATCH = ("batch", "messages")

# What the Pico sends: each message's "type", and what else it carries.
TO_PAGE = {
	"hello": ("you", "fixed", "now"),
	"tempo": ("tempo", "text", "by", "id"),
	"output": ("index", "ratio", "label", "snap", "muted", "pulse", "role", "by", "id"),
	"transport": ("state", "by", "id"),
	"clock": ("position", "faults"),
	"network": ("mode", "name", "address", "problem", "own", "found_at", "host_name", "saved", "by", "id"),
	"networks": ("found",),
	"error": ("message", "do", "id"),
}

# What "fixed" holds: the things a page needs that never change while it's open.
FIXED = (
	"version",
	"slowest_tempo",
	"fastest_tempo",
	"slowest_ratio",
	"fastest_ratio",
	"snaps",
	"shortest_pulse",
	"longest_pulse",
	"roles",
)

# The page's own files, and what kind each is. Nothing else is ever sent from
# the folder they are in.
PAGE_FILES = {
	"index.html": "text/html; charset=utf-8",
	"page.css": "text/css; charset=utf-8",
	"page.js": "text/javascript; charset=utf-8",
	"settings.js": "text/javascript; charset=utf-8",
	"knob.js": "text/javascript; charset=utf-8",
	"ratio.js": "text/javascript; charset=utf-8",
}

# What `/api/health` answers with.
HEALTH = ("pages", "given_up", "waiting", "holds", "held", "longest_hold", "faults")

# How often to look at where the clock has got to, in milliseconds. A page
# keeps its lights in time by itself between looks.
LOOK_EVERY = 250

# How long to leave between telling a page of one lot of changes and the next,
# in milliseconds. Whatever changes meanwhile is sent once, as it then stands.
TELL_EVERY = 40

# How many answers can wait for one page before more are dropped.
MOST_NOTES = 16

# How many batches a page may be sent beyond the last it has said it heard.
AHEAD = 2

# The most that may be on its way to all the open pages together, in bytes.
# The Pico has about 8,000 for everything waiting to be sent, and the page's
# files and every other answer need their share. A greeting is about 2,300.
ROOM = 3000

# How long a batch may get before what is left waits for the next, in bytes.
# One of every message for four outputs is about 950.
MOST_BATCH = 1200

# How long a page may leave a batch unanswered before it is taken to have
# gone, and how long a page is left with nothing sent before it is asked
# whether it is still there, in milliseconds.
DEAF_FOR = 8000
QUIET_FOR = 5000

# How late a look has to come to count as everything having been held up, in milliseconds.
HELD = 1000

# The page's files go to one phone at a time, and the open pages are sent
# nothing while they do. A connection may queue 6,400 bytes in the Pico's
# network stack, which has 8,000 for everything, so two loads together, or a
# load and the pages' news, could fill it, and on a poor signal that holds
# everything here for ten seconds. A load that hasn't finished after this long
# loses its turn, so one that went wrong can't keep everything waiting, in
# milliseconds. How often a waiting load looks again, in milliseconds.
TURN_MOST = 5000
TURN_LOOK = 20

# How long a batch is before anything is put in it, at the most, and what goes between two messages.
_WRAPPING = len('{"batch": 1000000, "messages": []}')
_BETWEEN = ", "

# Whether a batch can take one thing more: it can, it is long enough already,
# or there is no room for more until a page says it has heard what it was sent.
_FITS = 0
_ENOUGH = 1
_NO_ROOM = 2

# How long a new connection may take to begin asking for something, in seconds, before it is closed.
PATIENCE = 5.0

# What a page can be behind on, besides each output, which goes by its number.
TEMPO = picoclock.core.clock.TEMPO
TRANSPORT = picoclock.core.clock.TRANSPORT
NETWORK = picoclock.core.clock.NETWORK
CLOCK = "clock"

# Who made a change that no page made: page number 0, with no "id".
_NOBODY = (0, 0)

_OTHER_SITE = "PicoClock only takes changes from its own page"

# MicroPython counts milliseconds in a way of its own, which a laptop's Python doesn't have.
_ticks_ms: "typing.Any" = getattr(time, "ticks_ms", None)
_ticks_diff: "typing.Any" = getattr(time, "ticks_diff", None)


def _now () -> int:

	"""Give this moment, in milliseconds, to measure from."""

	if _ticks_ms is not None:
		return int(_ticks_ms())

	return int(time.monotonic() * 1000)


def _since (then: int) -> int:

	"""Give how many milliseconds have gone by since a moment `_now` gave."""

	if _ticks_ms is not None and _ticks_diff is not None:
		return int(_ticks_diff(_ticks_ms(), then))

	return int(time.monotonic() * 1000) - then


class Turn:

	"""A file of the page's, sent only once no other is being sent.

	Microdot reads a file's contents after the handler has given it back, so
	the turn is taken by the first read and given back when the file is
	closed, which Microdot does when the send is over, however it went.
	"""

	def __init__ (self, app: "App", file: "typing.Any") -> None:

		"""Wrap an open file for the app to send in its turn."""

		self._app = app
		self._file = file
		self._turn = 0

	async def read (self, size: int) -> bytes:

		"""Wait for this file's turn the first time, then read the next part of it."""

		if not self._turn:
			self._turn = await self._app._take_turn()

		part: bytes = self._file.read(size)

		return part

	def close (self) -> None:

		"""Give the turn back, and close the file."""

		if self._turn:
			self._app._end_turn(self._turn)
			self._turn = 0

		self._file.close()


def _same_site (request: microdot.Request) -> bool:

	"""Say whether a request comes from PicoClock's own page, or from no page at all.

	A browser says which site a page was loaded from. When that isn't the
	address the request was sent to, some other site's page is making it.
	"""

	origin = request.headers.get("Origin")

	if not origin:
		return True

	return bool(origin.split("://", 1)[-1] == request.headers.get("Host"))


class Patient:

	"""Reads a new connection for the server, and gives up on one that never says what it wants.

	MicroPython doesn't tell Python that a connection has been closed when
	it was closed before Python took it up. The server would wait on such a
	connection for ever, and five of them are every connection the Pico has.
	So the first line of a request is waited for only so long, and a
	connection that hasn't sent one by then is treated as closed. Once it
	has, it is read as usual: a page's socket may be silent for hours.
	"""

	def __init__ (self, reader: "typing.Any") -> None:

		"""Read from this connection."""

		self._reader = reader
		self._begun = False

	async def readline (self) -> bytes:

		"""Give the connection's next line, or nothing if its first line doesn't come in time."""

		if self._begun:
			return bytes(await self._reader.readline())

		self._begun = True

		try:
			return bytes(await asyncio.wait_for(self._reader.readline(), PATIENCE))

		except asyncio.TimeoutError:
			return b""

	def __getattr__ (self, name: str) -> "typing.Any":

		"""Pass everything else straight to the connection."""

		return getattr(self._reader, name)


class Web (microdot.Microdot):

	"""Microdot, with a limit on how long a new connection may take to ask for something."""

	async def handle_request (self, reader: "typing.Any", writer: "typing.Any") -> None:

		"""Answer one connection, giving up on it if it never asks for anything."""

		patient: "typing.Any" = Patient(reader)

		await microdot.Microdot.handle_request(self, patient, writer)


class Page:

	"""One open page: what it has yet to be sent, and how far it has kept up."""

	def __init__ (self, number: int) -> None:

		"""Begin with a greeting to send and nothing else."""

		self.number = number

		# Whether it has been sent its greeting.
		self.greeted = False

		# What has changed since this page was last told: only the names, so
		# what's sent is always how things stand at the moment of sending.
		self.stale: "set[Key]" = set()

		# Answers for this page alone, in order.
		self.notes: "list[Message]" = []

		# Set whenever there may be something to send.
		self.wake = asyncio.Event()

		# The number of the last batch sent, and how long each batch was that
		# the page has yet to say it heard, oldest first.
		self.sent = 0
		self.unheard: "list[int]" = []

		# How many looks have gone by with a batch unheard, and how many since anything was sent.
		self.waited = 0
		self.quiet = 0

		# Whether it is owed an empty batch, to see that it is still there.
		self.ask = False

		# Whether something was held back, the last time a batch was put together
		# for it, until a page says it has heard what it was sent.
		self.short = False

		# Set when the page has gone, and whether that was because it stopped answering.
		self.done = asyncio.Event()
		self.deaf = False


class App:

	"""Serves the page's API for one clock.

	`web` is the Microdot app to serve. `run()` keeps the open pages up to
	date with where the clock has got to, and wants running alongside it.
	"""

	def __init__ (self, clock: picoclock.core.clock.Clock, network: "Network | None" = None, static: "str | None" = None) -> None:

		"""Serve this clock.

		With a network, the page can see and change how the Pico is reached.
		`static` is the folder the page's own files are in. Without it only
		the API is served.
		"""

		self.clock = clock
		self.network = network
		self.static = static

		# Which of the page's files have a gzipped copy to send in their place,
		# as they do on the Pico, where room is short.
		self._packed: "list[str]" = []

		if static is not None:

			for name in PAGE_FILES:

				try:
					os.stat(static + "/" + name + ".gz")
					self._packed.append(name)
				except OSError:
					pass
		self.pages: "list[Page]" = []
		self.web = Web()

		# How many pages have ever opened, which numbers the next.
		self._opened = 0

		# Who last changed each thing, as a page's number and the "id" it sent.
		self._by: "dict[Key, tuple[int, int]]" = {}

		# What the clock and the network last looked like, to tell when they move on.
		self._clock_seen: "Message | None" = None
		self._network_seen: "Message | None" = None

		# How many bytes are on their way to pages that have yet to say they heard them.
		self._on_the_way = 0

		# How many pages were given up on, how often and for how long everything
		# here was held up, and the longest of those, in milliseconds.
		self.given_up = 0
		self.holds = 0
		self.held = 0
		self.longest_hold = 0

		# The page load whose turn it is, counted from 1, whether it is still being
		# sent, and for how many looks it has been.
		self._turn = 0
		self.sending_page = False
		self._turn_looks = 0

		self._fixed = self._describe()

		clock.watch(self._changed)
		self._add_routes()

	# -----------------------------------------------------------------------
	# How things stand.

	def _describe (self) -> "Message":

		"""Gather what a page needs that never changes while it's open."""

		snaps = []

		for ratio in picoclock.core.ratio.SNAPS:
			snaps.append({"ratio": picoclock.core.ratio.to_text(ratio), "label": picoclock.core.ratio.label(ratio)})

		return {
			"version": picoclock.__version__,
			"slowest_tempo": picoclock.core.timeline.SLOWEST_TEMPO,
			"fastest_tempo": picoclock.core.timeline.FASTEST_TEMPO,
			"slowest_ratio": picoclock.core.ratio.to_text(picoclock.core.ratio.SLOWEST),
			"fastest_ratio": picoclock.core.ratio.to_text(picoclock.core.ratio.FASTEST),
			"snaps": snaps,
			"shortest_pulse": picoclock.core.settings.SHORTEST_PULSE,
			"longest_pulse": picoclock.core.settings.LONGEST_PULSE,
			"roles": list(picoclock.core.settings.ROLES),
		}

	def _keys (self) -> "list[Key]":

		"""Name everything a page can be behind on."""

		keys: "list[Key]" = [TEMPO, TRANSPORT, CLOCK, NETWORK]

		for index in range(self.clock.settings.count):
			keys.append(index)

		return keys

	def _message (self, key: "Key") -> "Message":

		"""Build the message that says how one thing stands right now."""

		settings = self.clock.settings
		by = self._by.get(key, _NOBODY)

		if key == CLOCK:

			status = self.clock.engine.status()

			return {
				"type": "clock",
				"position": status.get("position", -1),
				"faults": status.get("faults", 0),
			}

		if key == TEMPO:

			return {
				"type": "tempo",
				"tempo": settings.tempo,
				"text": picoclock.core.settings.tempo_text(settings.tempo),
				"by": by[0],
				"id": by[1],
			}

		if key == TRANSPORT:

			return {
				"type": "transport",
				"state": self.clock.transport.state,
				"by": by[0],
				"id": by[1],
			}

		if key == NETWORK:

			message: "Message" = {"type": "network", "mode": "none", "name": "", "address": "", "problem": "", "own": "", "found_at": ""}

			if self.network is not None:

				status = self.network.status()

				for name in ("mode", "name", "address", "problem", "own", "found_at"):
					message[name] = status.get(name, "")

			message["host_name"] = settings.host_name
			message["saved"] = settings.network_name
			message["by"] = by[0]
			message["id"] = by[1]

			return message

		# Anything else is an output, by its number.
		assert isinstance(key, int)

		output = settings.outputs[key]

		return {
			"type": "output",
			"index": key,
			"ratio": picoclock.core.ratio.to_text(output.ratio),
			"label": picoclock.core.ratio.label(output.ratio),
			"snap": output.snap,
			"muted": output.muted,
			"pulse": "half" if output.pulse == picoclock.core.settings.HALF else output.pulse,
			"role": output.role,
			"by": by[0],
			"id": by[1],
		}

	def state (self) -> "Message":

		"""Give everything a page needs to draw itself: what never changes, and one of each message."""

		return {
			"fixed": self._fixed,
			"now": [self._message(key) for key in self._keys()],
		}

	# -----------------------------------------------------------------------
	# Keeping every open page in step.

	def _spread (self, keys: "list[Key]") -> None:

		"""Note that every open page is behind on these, and wake whatever sends to each."""

		for page in self.pages:

			for key in keys:
				page.stale.add(key)

			page.wake.set()

	def _changed (self, what: "Key", by: object) -> None:

		"""Hear from the clock that something has changed, and by whom."""

		who = by if isinstance(by, tuple) else _NOBODY
		keys = self._keys() if what == picoclock.core.clock.EVERYTHING else [what]

		for key in keys:
			self._by[key] = who

		self._spread(keys)

	def health (self) -> "Message":

		"""Say how well the page's side of things has kept up since power-up."""

		return {
			"pages": len(self.pages),
			"given_up": self.given_up,
			"waiting": self._on_the_way,
			"holds": self.holds,
			"held": self.held,
			"longest_hold": self.longest_hold,
			"faults": self.clock.engine.status().get("faults", 0),
		}

	def look (self) -> None:

		"""See whether the clock or the network has moved on by itself, whether each open page is still answering, and how long a page load has had."""

		if self.sending_page:

			self._turn_looks += 1

			if self._turn_looks * LOOK_EVERY >= TURN_MOST:
				self._end_turn(self._turn)

		if not self.pages:
			return

		now = self._message(CLOCK)

		if now != self._clock_seen:
			self._clock_seen = now
			self._spread([CLOCK])

		if self.network is not None:

			now = self.network.status()

			if now != self._network_seen:
				self._network_seen = now
				self._spread([NETWORK])

		# Time is counted in looks, so a moment when everything here was held
		# up is never taken for a page's silence.
		for page in list(self.pages):

			if page.unheard:

				page.waited += 1

				if page.waited * LOOK_EVERY >= DEAF_FOR:
					self._give_up(page)

			else:

				page.quiet += 1

				if page.quiet * LOOK_EVERY >= QUIET_FOR and not page.ask:
					page.ask = True
					page.wake.set()

	async def run (self) -> None:

		"""Keep the open pages up to date with where the clock is. Runs for as long as the page is served."""

		then = _now()

		while True:

			await asyncio.sleep(LOOK_EVERY / 1000)

			# A look that comes late was kept waiting by something that held up
			# everything here: the page, the buttons and the saving alike.
			late = _since(then) - LOOK_EVERY
			then = _now()

			if late > self.longest_hold:
				self.longest_hold = late

			if late >= HELD:
				self.holds += 1
				self.held += late

			self.look()

	# -----------------------------------------------------------------------
	# What a page asks for.

	def take (self, message: object, by: "tuple[int, int]" = _NOBODY) -> "Message | None":

		"""Carry out one message from a page, and give back an answer for that page alone, if there is one.

		Raises ValueError, in words fit to show on the page, when it can't be done.
		"""

		if not isinstance(message, dict):
			raise ValueError("A message is a JSON object that says what to do")

		clock = self.clock
		do = message.get("do")

		if do == "tempo":

			if "text" in message:

				if not isinstance(message["text"], str):
					raise ValueError("A typed tempo is sent as text")

				clock.set_tempo(picoclock.core.settings.parse_tempo(message["text"]), by)

			else:
				clock.set_tempo(message.get("value"), by)

		elif do == "output":
			clock.set_output(message.get("index"), message.get("name"), message.get("value"), by)

		elif do in picoclock.core.transport.COMMANDS:
			clock.press(do, by)

		elif do == "reset":
			clock.reset(by)

		elif do == "scan":

			if self.network is None:
				raise ValueError("There is no WiFi here to look for networks with")

			return {"type": "networks", "found": self.network.scan()}

		elif do == "join":
			clock.set_network(message.get("name"), message.get("password", ""), by)

		elif do == "forget":
			clock.forget_network(by)

		elif do == "switch":
			clock.switch_network(by)

		elif do == "own_password":
			clock.set_own_password(message.get("value"), by)

		elif do == "host_name":
			clock.set_host_name(message.get("value"), by)

		elif do == "heard":
			# Only a socket carries batches, and `hear` counts those off. Sent any other way there is nothing to count.
			pass

		else:
			raise ValueError("That isn't something PicoClock can be told to do")

		return None

	def hear (self, page: Page, text: "str | bytes") -> None:

		"""Take one message as it arrived on a page's socket, and note any answer for that page."""

		try:
			message = json.loads(text)
		except (ValueError, TypeError):
			message = None

		do = message.get("do") if isinstance(message, dict) else None
		number = message.get("id") if isinstance(message, dict) else None

		if do == "heard":
			self._heard(page, message.get("batch"))
			return

		if isinstance(number, bool) or not isinstance(number, int):
			number = 0

		try:
			answer = self.take(message, (page.number, number))

		except ValueError as error:

			answer = {
				"type": "error",
				"message": str(error),
				"do": do if isinstance(do, str) else "",
				"id": number,
			}

			# The page is showing what it asked for, so send it how things really stand.
			if do == "tempo":
				page.stale.add(TEMPO)

			elif do == "output":

				index = message.get("index")

				if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < self.clock.settings.count:
					page.stale.add(index)

			elif do in ("join", "forget", "switch", "own_password", "host_name"):
				page.stale.add(NETWORK)

		if answer is not None and len(page.notes) < MOST_NOTES:
			page.notes.append(answer)

		page.wake.set()

	def _fit (self, length: int, first: bool) -> int:

		"""Say whether a batch may grow to `length` bytes. `first` says it would then hold one thing."""

		# With nothing on its way to any page, one thing always goes, however long it is.
		if first and not self._on_the_way:
			return _FITS

		if length > ROOM - self._on_the_way:
			return _NO_ROOM

		return _FITS if first or length <= MOST_BATCH else _ENOUGH

	def _batch (self, page: Page) -> "str | None":

		"""Put together the next batch for one page, as it goes over the socket.

		A new page's first batch is empty, and what it is owed follows once
		it has answered that, beginning with its greeting. Gives None when
		the page has to catch up first, when there is no room, or when there
		is nothing to say.
		"""

		# The page itself is being sent to a phone, which may need nearly all the network stack's room.
		if self.sending_page or len(page.unheard) >= AHEAD:
			page.short = True
			return None

		parts: "list[str]" = []

		if page.sent == len(page.unheard):

			# The page has answered nothing yet. Before anything of any size it is sent
			# an empty batch, to see that it is taking what it is sent, and then nothing
			# more until it has answered that.
			if page.sent:
				page.short = True
				return None

			page.ask = True

		else:
			parts = self._news(page)

		if not parts and not page.ask:
			return None

		page.ask = False
		page.sent += 1

		batch = '{"batch": %d, "messages": [%s]}' % (page.sent, _BETWEEN.join(parts))

		if not page.unheard:
			page.waited = 0

		page.unheard.append(len(batch))
		page.quiet = 0

		self._on_the_way += len(batch)

		return batch

	def _news (self, page: Page) -> "list[str]":

		"""Gather what goes in the next batch for a page that has shown it is there, each thing as it goes over the socket.

		That is its greeting if it hasn't had one, then any answers for it
		alone, in order, then how each thing it is behind on stands right
		now, for as much as there is room. What is gathered is no longer
		owed to the page.
		"""

		parts: "list[str]" = []
		size = _WRAPPING
		fit = _FITS

		if not page.greeted:

			hello = {"type": "hello", "you": page.number}
			hello.update(self.state())

			text = json.dumps(hello)
			size += len(text) + len(_BETWEEN)

			if self._fit(size, True) != _FITS:
				page.short = True
				return parts

			# The greeting says how everything stands, so the page is behind on nothing.
			page.greeted = True
			page.stale.clear()

			parts.append(text)

		while page.notes and fit == _FITS:

			text = json.dumps(page.notes[0])
			longer = size + len(text) + len(_BETWEEN)
			fit = self._fit(longer, not parts)

			if fit == _FITS:
				page.notes.pop(0)
				parts.append(text)
				size = longer

		if page.stale and fit == _FITS:

			# In the same order every time, whatever order they changed in.
			for key in self._keys():

				if key not in page.stale:
					continue

				text = json.dumps(self._message(key))
				longer = size + len(text) + len(_BETWEEN)
				fit = self._fit(longer, not parts)

				if fit != _FITS:
					break

				page.stale.discard(key)
				parts.append(text)
				size = longer

		# What there was no room for goes when `_heard` or `_drop` says there is some.
		# What was only too much for one batch goes in the next, a moment after this one.
		page.short = fit == _NO_ROOM

		return parts

	def _heard (self, page: Page, number: object) -> None:

		"""Take a page's word for the last batch it was sent, and send on whatever that makes room for."""

		if isinstance(number, bool) or not isinstance(number, int):
			return

		# The batches not yet heard are the last so many that were sent.
		arrived = number - (page.sent - len(page.unheard))

		if arrived <= 0 or number > page.sent:
			# Old news, or a batch that was never sent.
			return

		for _ in range(arrived):
			self._on_the_way -= page.unheard.pop(0)

		page.waited = 0

		self._make_room()

	def _make_room (self) -> None:

		"""Wake whatever sends to each page that was held back, now that there is more room.

		Room made by one page is room for any of them.
		"""

		for page in self.pages:

			if page.short:
				page.wake.set()

	async def _tell (self, page: Page, socket: "Socket") -> None:

		"""Send one page whatever it is behind on, for as long as it is open.

		Only this sends to the page, because two things writing to one
		socket at once can run their messages together.
		"""

		try:

			while True:

				await page.wake.wait()
				page.wake.clear()

				while True:

					batch = self._batch(page)

					if batch is None:
						break

					await socket.send(batch)
					await asyncio.sleep(TELL_EVERY / 1000)

		except (OSError, microdot.websocket.WebSocketError):
			# The page has gone, and there is nobody left to tell.
			pass

		finally:
			page.done.set()

	async def _listen (self, page: Page, socket: "Socket") -> None:

		"""Carry out whatever one page sends, for as long as it is open."""

		try:

			while True:
				self.hear(page, await socket.receive())

		except (OSError, microdot.websocket.WebSocketError):
			# The page has closed, or dropped off the network.
			pass

		finally:
			page.done.set()

	def _give_up (self, page: Page) -> None:

		"""Stop waiting for a page that has left a batch unanswered for too long."""

		# It stays among the pages until whatever looks after its socket has had its turn, and is counted once.
		if page.deaf:
			return

		self.given_up += 1

		page.deaf = True
		page.done.set()

	def _drop (self, page: Page) -> None:

		"""Forget a page that has closed, and whatever was on its way to it."""

		if page not in self.pages:
			return

		self.pages.remove(page)

		for length in page.unheard:
			self._on_the_way -= length

		page.unheard = []

		self._make_room()

	async def serve (self, socket: "Socket") -> None:

		"""Look after one page's socket from the moment it opens until the page goes."""

		self._opened += 1

		page = Page(self._opened)

		# From here the page is behind on every change, so nothing can fall
		# between what its greeting says and what follows it.
		self.pages.append(page)
		page.wake.set()

		teller = asyncio.create_task(self._tell(page, socket))
		listener = asyncio.create_task(self._listen(page, socket))

		try:
			await page.done.wait()

		finally:

			self._drop(page)

			teller.cancel()
			listener.cancel()

		if page.deaf:
			# Microdot ends a socket by saying goodbye over it. To a page that has stopped
			# answering, that would be one thing more waiting to be sent, so it is told it already has.
			socket.closed = True

	# -----------------------------------------------------------------------
	# The addresses a page uses.

	def _add_routes (self) -> None:

		"""Tell Microdot which address does what."""

		async def state (request: microdot.Request) -> "Message":

			"""Give everything, for a page that has just opened."""

			return self.state()

		async def health (request: microdot.Request) -> "Message":

			"""Say how well the page's side of things has kept up."""

			return self.health()

		async def change (request: microdot.Request) -> "Message | tuple[Message, int]":

			"""Carry out one message sent as JSON."""

			if not _same_site(request):
				return {"error": _OTHER_SITE}, 403

			try:
				message = request.json
			except ValueError:
				message = None

			if not isinstance(message, dict):
				return {"error": "A change is sent as JSON"}, 400

			try:
				answer = self.take(message)
			except ValueError as error:
				return {"error": str(error)}, 400

			return answer if answer is not None else {"ok": True}

		async def upgrade (request: microdot.Request) -> microdot.websocket.WebSocket:

			"""Turn a request into a socket, unless another site's page is asking."""

			if not _same_site(request):
				microdot.abort(403, _OTHER_SITE)

			return await microdot.websocket.websocket_upgrade(request)

		async def socket (request: microdot.Request, ws: microdot.websocket.WebSocket) -> None:

			"""Look after one page's socket."""

			await self.serve(ws)

		async def index (request: microdot.Request) -> microdot.Response:

			"""Send the page."""

			return self._send("index.html")

		async def file (request: microdot.Request, name: str) -> "microdot.Response | tuple[str, int]":

			"""Send one of the page's other files, and nothing that isn't one."""

			if name not in PAGE_FILES:
				return "", 404

			try:
				return self._send(name)
			except OSError:
				return "", 404

		self.web.get("/api/state")(state)
		self.web.get("/api/health")(health)
		self.web.post("/api/change")(change)
		self.web.get("/api/socket")(microdot.websocket.websocket_wrapper(socket, upgrade))

		if self.static is not None:
			self.web.get("/")(index)
			self.web.get("/<name>")(file)

	def _send (self, name: str) -> microdot.Response:

		"""Send one of the page's files, gzipped if there is a gzipped copy.

		A browser is told to ask again every time, so a page is never run
		against a PicoClock newer or older than itself.
		"""

		assert self.static is not None

		packed = name in self._packed
		response = microdot.send_file(self.static + "/" + name, content_type=PAGE_FILES[name], max_age=0, compressed=packed, file_extension=".gz" if packed else "")

		# Microdot's type stubs say a body is bytes, but it takes anything it can read and close, as the file here is.
		response.body = Turn(self, response.body)  # type: ignore[assignment]

		return response

	async def _take_turn (self) -> int:

		"""Wait for this page load's turn, and give its number.

		Loads go one at a time. Once it is this one's turn, nothing more goes
		to the open pages, and what is already on its way to them is let
		arrive before the load begins, so a load and the pages' news never
		fill the network stack's memory between them. A turn that has lasted
		TURN_MOST is ended by `look`, which counts the time in looks, so a
		moment when everything here was held up never cuts a turn short.
		"""

		while self.sending_page:
			await asyncio.sleep(TURN_LOOK / 1000)

		self._turn += 1
		self.sending_page = True
		self._turn_looks = 0

		turn = self._turn

		while self._on_the_way and self.sending_page and self._turn == turn:
			await asyncio.sleep(TURN_LOOK / 1000)

		return turn

	def _end_turn (self, turn: int) -> None:

		"""Let the next load go, and the open pages be sent what they have missed, unless this turn already ran out."""

		if turn != self._turn or not self.sending_page:
			return

		self.sending_page = False
		self._make_room()
