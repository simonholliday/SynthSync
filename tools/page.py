"""A stand-in for the page: it talks to PicoClock the way a phone's browser does.

The tests use it to drive the laptop stand-in over real sockets, and it can
drive a real Pico over WiFi the same way. It speaks just enough HTTP and
WebSocket for that, with nothing beyond Python's own library.

    page = tools.page.Page("192.168.4.1")
    hello = await page.open()
    await page.say({"do": "tempo", "value": 14000})
    tempo = await page.wait_for("tempo")
    await page.close()

PicoClock sends its messages in numbered batches and a page answers each with
its number, which is how PicoClock knows what has arrived. This does that as
each batch comes in, as a browser does, whether or not anything is waiting to
hear what was in it. `page.answering = False` stops it, to stand in for a
phone that has dropped off the network without saying so.
"""

import asyncio
import base64
import json
import os
import struct
import typing

Message = dict[str, typing.Any]

# The kinds of WebSocket frame this deals with.
_TEXT = 1
_CLOSE = 8
_PING = 9
_PONG = 10


class Refused (Exception):

	"""Raised when PicoClock won't open a socket. `status` is the HTTP status it answered with."""

	def __init__ (self, status: int) -> None:

		"""Keep the status PicoClock answered with."""

		super().__init__("PicoClock answered %d and didn't open a socket" % status)

		self.status = status


async def fetch (host: str, port: int, path: str, body: object = None, headers: dict[str, str] | None = None) -> tuple[int, typing.Any]:

	"""Ask PicoClock for one address, and give back the status and whatever JSON came with it.

	With a body, it is sent as JSON by POST. Without one, the address is
	fetched by GET.
	"""

	reader, writer = await asyncio.open_connection(host, port)

	sending = dict(headers or {})
	sending.setdefault("Host", host if port == 80 else "%s:%d" % (host, port))
	sending["Connection"] = "close"
	data = b""

	if body is not None:
		data = json.dumps(body).encode()
		sending.setdefault("Content-Type", "application/json")
		sending["Content-Length"] = str(len(data))

	lines = ["%s %s HTTP/1.1" % ("GET" if body is None else "POST", path)]
	lines.extend("%s: %s" % (name, value) for name, value in sending.items())

	writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + data)
	await writer.drain()

	answer = await reader.read()

	writer.close()
	await writer.wait_closed()

	head, _, rest = answer.partition(b"\r\n\r\n")
	status = int(head.split(b" ", 2)[1])

	try:
		return status, json.loads(rest)
	except ValueError:
		return status, None


class Page:

	"""One page open on a PicoClock, over a real socket."""

	def __init__ (self, host: str, port: int = 80) -> None:

		"""Get ready to open a page on the PicoClock at this address."""

		self.host = host
		self.port = port
		self.number = 0

		# Whether each batch is answered with its number, and the number of the last one that came.
		self.answering = True
		self.batch = 0

		self._reader: asyncio.StreamReader | None = None
		self._writer: asyncio.StreamWriter | None = None

		# What has arrived and not yet been asked for, what reads it in, and why that ended.
		self._arrived: asyncio.Queue[Message] = asyncio.Queue()
		self._reading: asyncio.Task[None] | None = None
		self._ended: BaseException | None = None

	async def open (self, origin: str | None = None) -> Message:

		"""Open the socket and give back the greeting PicoClock sends.

		`origin` is the site a browser would say the page came from. Left out,
		none is given, as when something other than a browser connects.
		"""

		self._reader, self._writer = await asyncio.open_connection(self.host, self.port)

		lines = [
			"GET /api/socket HTTP/1.1",
			"Host: %s" % (self.host if self.port == 80 else "%s:%d" % (self.host, self.port)),
			"Upgrade: websocket",
			"Connection: Upgrade",
			"Sec-WebSocket-Key: %s" % base64.b64encode(os.urandom(16)).decode(),
			"Sec-WebSocket-Version: 13",
		]

		if origin is not None:
			lines.append("Origin: %s" % origin)

		self._writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
		await self._writer.drain()

		status = int((await self._reader.readline()).split(b" ", 2)[1])

		while (await self._reader.readline()).strip():
			pass

		if status != 101:
			await self.close()
			raise Refused(status)

		self._reading = asyncio.create_task(self._read())

		hello = await self.hear()

		if hello is None or hello.get("type") != "hello":
			raise OSError("PicoClock opened a socket and didn't say hello")

		self.number = hello["you"]

		return hello

	async def _send (self, kind: int, data: bytes) -> None:

		"""Send one frame, masked as a browser's must be."""

		assert self._writer is not None

		mask = os.urandom(4)
		head = bytes([0x80 | kind])

		if len(data) < 126:
			head += bytes([0x80 | len(data)])
		else:
			head += bytes([0x80 | 126]) + struct.pack(">H", len(data))

		self._writer.write(head + mask + bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))
		await self._writer.drain()

	async def say (self, message: Message) -> None:

		"""Send PicoClock one message."""

		await self._send(_TEXT, json.dumps(message).encode())

	async def _frame (self) -> tuple[int, bytes]:

		"""Read one frame, and give back its kind and what it carries."""

		assert self._reader is not None

		head = await self._reader.readexactly(2)
		length = head[1] & 0x7F

		if length == 126:
			length = struct.unpack(">H", await self._reader.readexactly(2))[0]
		elif length == 127:
			length = struct.unpack(">Q", await self._reader.readexactly(8))[0]

		return head[0] & 0x0F, await self._reader.readexactly(length)

	async def _read (self) -> None:

		"""Take in every batch PicoClock sends, answer it, and keep its messages until they are asked for.

		This runs from the moment the socket opens until it closes, so a
		batch is answered when it arrives, as a browser answers it.
		"""

		try:

			while True:

				kind, data = await self._frame()

				if kind == _TEXT:

					batch = json.loads(data)
					self.batch = batch["batch"]

					if self.answering:
						await self.say({"do": "heard", "batch": self.batch})

					for message in batch["messages"]:
						self._arrived.put_nowait(message)

				elif kind == _PING:
					await self._send(_PONG, data)

				elif kind == _CLOSE:
					raise ConnectionError("PicoClock closed the socket")

		except (OSError, asyncio.IncompleteReadError) as problem:
			self._ended = problem

		# Whoever is waiting to hear something is woken to find the socket has gone.
		self._arrived.put_nowait({})

	async def _next (self) -> Message:

		"""Wait for the next message, however long it takes. Raises what ended the socket once it has."""

		message = await self._arrived.get()

		if not message:

			# Left for the next to ask, too.
			self._arrived.put_nowait({})

			raise self._ended or ConnectionError("The socket is closed")

		return message

	async def hear (self, timeout: float = 2.0) -> Message | None:

		"""Give back the next message PicoClock sends, or None if nothing comes in time."""

		try:
			return await asyncio.wait_for(self._next(), timeout)
		except asyncio.TimeoutError:
			return None

	async def hear_for (self, seconds: float) -> list[Message]:

		"""Give back everything PicoClock sends over this long."""

		loop = asyncio.get_running_loop()
		until = loop.time() + seconds
		heard: list[Message] = []

		while True:

			left = until - loop.time()

			if left <= 0:
				return heard

			message = await self.hear(left)

			if message is None:
				return heard

			heard.append(message)

	async def wait_for (self, kind: str, timeout: float = 2.0, **matching: object) -> Message:

		"""Give back the next message of one type, passing over any others.

		Anything else named must match too: `wait_for("output", index=2)`
		waits for news of output 3. Raises TimeoutError if none comes.
		"""

		loop = asyncio.get_running_loop()
		until = loop.time() + timeout

		while True:

			message = await self.hear(max(until - loop.time(), 0.001))

			if message is None:
				raise TimeoutError("PicoClock sent no %s message in %.1f s" % (kind, timeout))

			if message.get("type") == kind and all(message.get(name) == value for name, value in matching.items()):
				return message

	async def close (self) -> None:

		"""Close the page."""

		if self._writer is None:
			return

		if self._reading is not None:
			self._reading.cancel()
			self._reading = None

		self._writer.close()

		try:
			await asyncio.wait_for(self._writer.wait_closed(), 2.0)

		except asyncio.TimeoutError:
			# A PicoClock that has stopped taking anything would keep the last of what
			# was said waiting for ever, so the socket is dropped with it unsent.
			self._writer.transport.abort()

		except OSError:
			pass

		self._writer = None
		self._reader = None
