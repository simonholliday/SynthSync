"""How PicoClock's own network opens the page by itself on a phone that joins it.

A phone that joins a WiFi network first asks a site of its maker's whether the
network reaches the internet. A hotel's network answers by sending the phone
to its sign-in page, and the phone opens that page by itself, in a window of
its own that uses the WiFi even when mobile data is on.

PicoClock's own network does the same with the page. While it is up, every
name a phone looks up is answered with the Pico's own address, which is where
the Pico's own network tells each phone to look names up. So the phone's
question comes to the Pico, and the page's server answers a request for any
other site by sending the phone to the page.

On a network the Pico has joined, that network answers the names, and the
page's server answers every request as its own.
"""

import asyncio
import socket

import picoclock.core.wifi

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:

	import typing

	class Listener (typing.Protocol):

		"""What look-ups arrive on: a socket, or something standing in for one."""

		def recvfrom (self, __most: int) -> "tuple[bytes, typing.Any]":

			"""Give the next look-up and who sent it, or fail with OSError when none is waiting."""

		def sendto (self, __data: bytes, __to: "typing.Any") -> object:

			"""Send an answer back, or fail with OSError when there is no room for it."""

# Where a phone sends a look-up.
PORT = 53

# How long a phone may keep an answer, in seconds. Short, so that a laptop that
# moves on to another network soon looks its names up there instead.
KEEP_FOR = 10

# How often to look for look-ups, in seconds, and the most to answer at each
# look. A phone waits a moment longer for its answer, and a flood of them can't
# hold up the page, the buttons or the saving.
LISTEN_EVERY = 0.05
MOST_AT_ONCE = 8

# The longest look-up worth reading. A plain one is far shorter.
_LONGEST = 512

# A look-up begins with twelve bytes: its number, what it is, and how many of each part follow.
_HEADER = 12

# What a question asks for: an address of the older kind, the only kind the Pico has, on the internet.
_A = b"\x00\x01"
_IN = b"\x00\x01"


def sent_to (status: "dict[str, typing.Any]") -> str:

	"""Give the address a phone is sent to, which is the Pico's own while its own network is up, or nothing."""

	if status["mode"] == picoclock.core.wifi.OWN or status["mode"] == picoclock.core.wifi.FOUND:
		return str(status["address"])

	return ""


def names_pico (host: "str | None", host_name: str) -> bool:

	"""Say whether a request's Host names the Pico: by an address, by its own name with .local or without, or as localhost, where the laptop stand-in is.

	A port after it makes no difference, and nor do capitals. A request with
	no Host at all comes from no browser. A site elsewhere that has pointed
	its own name at the Pico's address, to get past a browser's guard
	against one site's page using another's, still sends its own name here,
	and so doesn't name the Pico.
	"""

	if not host:
		return True

	# The port, if one is given, follows the last colon, unless the address is of the newer kind, in brackets.
	name = (host if host.endswith("]") else host.rsplit(":", 1)[0]).lower()

	return name.startswith("[") or _packed(name) is not None or name == host_name or name == host_name + ".local" or name == "localhost"


def for_here (host: "str | None", address: str, host_name: str) -> bool:

	"""Say whether a request with this Host is the Pico's own to answer, while a phone is sent to `address`.

	Every request is, unless the Pico's own network is up and the request
	names a site that isn't the Pico. One that names an address rather than
	a site reached the Pico by that address, so it can only have meant the
	Pico, and a name the Pico didn't answer was never looked up here. One
	that names the Pico by its own name is its own too.
	"""

	if address == "":
		return True

	return names_pico(host, host_name)


def _packed (address: str) -> "bytes | None":

	"""Give an address such as 192.168.4.1 as the four bytes a look-up's answer holds, or None if it isn't one."""

	parts = address.split(".")

	if len(parts) != 4:
		return None

	try:
		numbers = [int(part) for part in parts]
	except ValueError:
		return None

	for number in numbers:
		if number < 0 or number > 255:
			return None

	return bytes(numbers)


def answer (query: bytes, address: str) -> "bytes | None":

	"""Answer one look-up of a name with `address`, or give None for anything else.

	Only a plain question about one name is answered. One that asks for
	anything but an address of the older kind, as a phone does for an address
	of the newer kind, is answered with none, which says the name has none of
	that kind. Anything else is left unanswered, as is everything when
	`address` isn't an address such as 192.168.4.1.
	"""

	packed = _packed(address)

	if packed is None or len(query) < _HEADER:
		return None

	flags = query[2]

	# An answer, or a question of some other sort than which address a name has.
	if flags & 0xF8:
		return None

	if query[4:6] != b"\x00\x01":
		return None

	# The name is in parts, each after its own length, and ends with a part of no length.
	at = _HEADER

	while at < len(query) and query[at] != 0:

		# A part pointing elsewhere in the look-up is something no question needs.
		if query[at] & 0xC0:
			return None

		at += 1 + query[at]

	# The ending, then two bytes for what is asked and two for where.
	end = at + 5

	if end > len(query):
		return None

	asking = query[at + 1:at + 3] == _A and query[at + 3:at + 5] == _IN

	# The same number, said to be an answer from the one who knows, asked for again if the phone asked so.
	header = query[0:2] + bytes([0x84 | (flags & 0x01), 0x80]) + b"\x00\x01" + (b"\x00\x01" if asking else b"\x00\x00") + b"\x00\x00\x00\x00"
	reply = header + query[_HEADER:end]

	if asking:
		# The name, by pointing back at the question, then how long to keep the address, and the address.
		reply += b"\xc0\x0c" + _A + _IN + KEEP_FOR.to_bytes(4, "big") + b"\x00\x04" + packed

	return reply


def hear (listener: "Listener", where: "typing.Callable[[], str]") -> int:

	"""Answer the look-ups that have arrived, up to `MOST_AT_ONCE` of them, and say how many were answered.

	`where` gives the address to answer with, or nothing while the Pico's own
	network is down, when look-ups are left unanswered.
	"""

	address = None
	answered = 0

	for _ in range(MOST_AT_ONCE):

		try:
			query, sender = listener.recvfrom(_LONGEST)
		except OSError:
			break

		if address is None:
			address = where()

		reply = answer(query, address) if address else None

		if reply is None:
			continue

		try:
			listener.sendto(reply, sender)
			answered += 1

		except OSError:
			# With no room for it just now the answer is dropped, and the phone asks again.
			pass

	return answered


async def run (where: "typing.Callable[[], str]", port: int = PORT) -> None:

	"""Answer the names looked up on PicoClock's own network, for as long as this runs.

	`where` gives the address to answer with, or nothing while the Pico's own
	network is down.
	"""

	listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

	try:
		listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		listener.bind(socket.getaddrinfo("0.0.0.0", port)[0][-1])
		listener.setblocking(False)

		while True:
			await asyncio.sleep(LISTEN_EVERY)
			hear(listener, where)

	finally:
		listener.close()
