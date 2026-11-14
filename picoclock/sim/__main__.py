"""Serve PicoClock from this computer, with no Pico.

    python -m picoclock.sim
    python -m picoclock.sim --port 8080 --outputs 8
    python -m picoclock.sim --host 0.0.0.0

It behaves as a Pico that has just been switched on: the clock starts running
at once with the settings a new one has. Nothing is saved. With `--host
0.0.0.0` a phone on the same network can reach it at this computer's address.
"""

import argparse
import asyncio
import os
import sys

import picoclock.core.clock
import picoclock.core.settings
import picoclock.core.transport
import picoclock.sim.engine
import picoclock.sim.network
import picoclock.web.app


class Stand:

	"""Everything that makes up a stand-in PicoClock, put together."""

	def __init__ (self, outputs: int = 4) -> None:

		"""Make a stand-in with this many outputs, running, as a Pico is once switched on."""

		self.engine = picoclock.sim.engine.Engine(outputs)
		self.clock = picoclock.core.clock.Clock(picoclock.core.settings.Settings(outputs), self.engine)
		self.network = picoclock.sim.network.make(self.clock)
		self.app = picoclock.web.app.App(self.clock, self.network, os.path.join(os.path.dirname(picoclock.web.app.__file__), "static"))

		self.clock.load()
		self.clock.press(picoclock.core.transport.START)

	async def serve (self, host: str, port: int) -> None:

		"""Serve the page and its API at this address until stopped."""

		await asyncio.gather(
			self.engine.run(),
			self.clock.run(),
			self.app.run(),
			picoclock.sim.network.run(self.network),
			self.app.web.start_server(host=host, port=port),
		)


def main (arguments: list[str] | None = None) -> int:

	"""Serve the stand-in until Ctrl-C."""

	parser = argparse.ArgumentParser(prog="python -m picoclock.sim", description="Serve PicoClock from this computer, with no Pico.")
	parser.add_argument("--host", default="127.0.0.1", help="the address to serve at: 0.0.0.0 lets other devices on the network reach it")
	parser.add_argument("--port", type=int, default=8000, help="the port to serve at")
	parser.add_argument("--outputs", type=int, default=4, help="how many outputs the stand-in has, from 1 to 8")
	options = parser.parse_args(arguments)

	if options.outputs < 1 or options.outputs > 8:
		parser.error("a PicoClock has between 1 and 8 outputs")

	stand = Stand(options.outputs)

	print("PicoClock's stand-in is at http://%s:%d" % ("localhost" if options.host == "127.0.0.1" else options.host, options.port), flush=True)

	try:
		asyncio.run(stand.serve(options.host, options.port))
	except KeyboardInterrupt:
		pass

	return 0


if __name__ == "__main__":
	sys.exit(main())
