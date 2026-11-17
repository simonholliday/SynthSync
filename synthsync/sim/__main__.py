"""Serve SynthSync from this computer, with no Pico.

    python -m synthsync.sim
    python -m synthsync.sim --port 8080 --outputs 8
    python -m synthsync.sim --config config.txt
    python -m synthsync.sim --host 0.0.0.0
    python -m synthsync.sim --static FOLDER

It behaves as a Pico that has just been switched on: the clock starts running
at once with the settings a new one has. Nothing is saved. It has four outputs
unless `--outputs` says otherwise, or `--config` gives it a `config.txt` to
read as the Pico reads its own, and the page then shows whatever in that file
couldn't be used. With `--host
0.0.0.0` a phone on the same network can reach it at this computer's address.
It serves the page's own six files, or with `--static` whatever is in another
folder, such as the page packed into one file as the Pico holds it.
"""

import argparse
import asyncio
import os
import sys

import synthsync.core.clock
import synthsync.core.config
import synthsync.core.settings
import synthsync.core.timeline
import synthsync.core.transport
import synthsync.sim.engine
import synthsync.sim.network
import synthsync.web.app


class Stand:

	"""Everything that makes up a stand-in SynthSync, put together."""

	def __init__ (self, outputs: int = synthsync.core.config.DEFAULT_OUTPUTS, static: str | None = None, config_problems: list[str] | None = None) -> None:

		"""Make a stand-in with this many outputs, running, as a Pico is once switched on.

		`static` is the folder the page is served from, and is the page's own
		six files unless given. `config_problems` is what the page is told
		couldn't be used in `config.txt`.
		"""

		if static is None:
			static = os.path.join(os.path.dirname(synthsync.web.app.__file__), "static")

		self.engine = synthsync.sim.engine.Engine(outputs)
		self.clock = synthsync.core.clock.Clock(synthsync.core.settings.Settings(outputs), self.engine)
		self.network = synthsync.sim.network.make(self.clock)
		self.app = synthsync.web.app.App(self.clock, self.network, static, config_problems)

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
	parser.add_argument("--outputs", type=int, help="how many outputs the stand-in has, from 1 to 8, which is 4 unless given")
	parser.add_argument("--config", help="a config.txt to read as the Pico reads its own, in place of --outputs")
	parser.add_argument("--static", help="a folder to serve the page from in place of its own six files")
	options = parser.parse_args(arguments)

	if options.outputs is not None and options.config is not None:
		parser.error("--outputs and --config both say how many outputs there are: give one or the other")

	if options.outputs is not None and (options.outputs < 1 or options.outputs > synthsync.core.timeline.MOST_OUTPUTS):
		parser.error("a SynthSync has between 1 and %d outputs" % synthsync.core.timeline.MOST_OUTPUTS)

	if options.config is not None and not os.path.isfile(options.config):
		parser.error("there is no file at %s" % options.config)

	wired = synthsync.core.config.read(options.config) if options.config is not None else synthsync.core.config.Config()

	for problem in wired.problems:
		print(problem, flush=True)

	stand = Stand(options.outputs or wired.outputs, options.static, wired.problems)

	print("SynthSync's stand-in is at http://%s:%d" % ("localhost" if options.host == "127.0.0.1" else options.host, options.port), flush=True)

	try:
		asyncio.run(stand.serve(options.host, options.port))
	except KeyboardInterrupt:
		pass

	return 0


if __name__ == "__main__":
	sys.exit(main())
