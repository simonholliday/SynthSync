"""Serve SynthSync from this computer, with no Pico.

    python -m synthsync.sim
    python -m synthsync.sim --port 8080 --outputs 8
    python -m synthsync.sim --host 0.0.0.0
    python -m synthsync.sim --static FOLDER

It behaves as a Pico that has just been switched on: the clock starts running
at once with the settings a new one has. Nothing is saved. With `--host
0.0.0.0` a phone on the same network can reach it at this computer's address.
It serves the page's own six files, or with `--static` whatever is in another
folder, such as the page packed into one file as the Pico holds it.
"""

import argparse
import asyncio
import os
import sys

import synthsync.core.clock
import synthsync.core.settings
import synthsync.core.transport
import synthsync.sim.engine
import synthsync.sim.network
import synthsync.web.app


class Stand:

	"""Everything that makes up a stand-in SynthSync, put together."""

	def __init__ (self, outputs: int = 4, static: str | None = None) -> None:

		"""Make a stand-in with this many outputs, running, as a Pico is once switched on.

		`static` is the folder the page is served from, and is the page's own
		six files unless given.
		"""

		if static is None:
			static = os.path.join(os.path.dirname(synthsync.web.app.__file__), "static")

		self.engine = synthsync.sim.engine.Engine(outputs)
		self.clock = synthsync.core.clock.Clock(synthsync.core.settings.Settings(outputs), self.engine)
		self.network = synthsync.sim.network.make(self.clock)
		self.app = synthsync.web.app.App(self.clock, self.network, static)

		self.clock.load()
		self.clock.press(synthsync.core.transport.START)

	async def serve (self, host: str, port: int) -> None:

		"""Serve the page and its API at this address until stopped."""

		await asyncio.gather(
			self.engine.run(),
			self.clock.run(),
			self.app.run(),
			synthsync.sim.network.run(self.network),
			self.app.web.start_server(host=host, port=port),
		)


def main (arguments: list[str] | None = None) -> int:

	"""Serve the stand-in until Ctrl-C."""

	parser = argparse.ArgumentParser(prog="python -m synthsync.sim", description="Serve SynthSync from this computer, with no Pico.")
	parser.add_argument("--host", default="127.0.0.1", help="the address to serve at: 0.0.0.0 lets other devices on the network reach it")
	parser.add_argument("--port", type=int, default=8000, help="the port to serve at")
	parser.add_argument("--outputs", type=int, default=4, help="how many outputs the stand-in has, from 1 to 8")
	parser.add_argument("--static", help="a folder to serve the page from in place of its own six files")
	options = parser.parse_args(arguments)

	if options.outputs < 1 or options.outputs > 8:
		parser.error("a SynthSync has between 1 and 8 outputs")

	stand = Stand(options.outputs, options.static)

	print("SynthSync's stand-in is at http://%s:%d" % ("localhost" if options.host == "127.0.0.1" else options.host, options.port), flush=True)

	try:
		asyncio.run(stand.serve(options.host, options.port))
	except KeyboardInterrupt:
		pass

	return 0


if __name__ == "__main__":
	sys.exit(main())
