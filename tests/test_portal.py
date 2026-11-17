"""The names a phone looks up on PicoClock's own network are answered over a real socket, and the socket is let go when that stops."""

import asyncio
import socket

import picoclock.web.portal

import tests.checks.check_portal


def free_port () -> int:

	"""Find a port nothing on this machine is listening on for look-ups."""

	probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

	try:
		probe.bind(("127.0.0.1", 0))
		return int(probe.getsockname()[1])

	finally:
		probe.close()


async def ask (port: int, query: bytes) -> "bytes | None":

	"""Send one look-up to the answerer and give back what comes, or None if nothing does within a second."""

	phone = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	phone.setblocking(False)

	try:
		phone.sendto(query, ("127.0.0.1", port))

		for _ in range(100):

			await asyncio.sleep(0.01)

			try:
				return phone.recvfrom(512)[0]
			except BlockingIOError:
				pass

		return None

	finally:
		phone.close()


def test_a_look_up_is_answered_over_a_socket_only_while_the_own_network_is_up () -> None:

	"""A look-up sent to the answerer comes back with the Pico's address while there is one to give, and gets nothing while there isn't."""

	port = free_port()
	where = ["192.168.4.1"]
	query = tests.checks.check_portal.look_up("connectivitycheck.gstatic.com")

	async def work () -> None:

		"""The check itself, which needs an event loop to run in."""

		answering = asyncio.create_task(picoclock.web.portal.run(lambda: where[0], port))

		# Its socket is opened at its first turn, and a look-up sent before then is lost.
		await asyncio.sleep(0.1)

		try:
			reply = await ask(port, query)

			assert reply == picoclock.web.portal.answer(query, "192.168.4.1")

			where[0] = ""

			assert await ask(port, query) is None

		finally:
			answering.cancel()

			try:
				await answering
			except asyncio.CancelledError:
				pass

	asyncio.run(work())

	# Once it has stopped, the port is free again, as it must be for the answerer to start again after a failure.
	again = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

	try:
		again.bind(("0.0.0.0", port))
	finally:
		again.close()
