"""A stand-in SynthSync running on its own, for the tests that open the page in a real browser.

Those tests can't run an event loop of their own, so the stand-in runs as a
separate program, and they talk to it over plain requests.
"""

import json
import pathlib
import socket
import subprocess
import sys
import time
import typing
import urllib.error
import urllib.request

import tools.stage

ROOT = pathlib.Path(__file__).parent.parent

# How long a stand-in that has said it is serving may take to answer, in seconds.
READY_WITHIN = 10


def packed (folder: pathlib.Path) -> pathlib.Path:

	"""Pack the page into a folder as the Pico holds it, as one gzipped file, and give the folder it is in, for a stand-in to serve."""

	tools.stage.pack_page(folder)

	return folder / "synthsync" / "web" / "static"


def free_port () -> int:

	"""Find a port nothing is listening on."""

	with socket.socket() as probe:
		probe.bind(("127.0.0.1", 0))
		return int(probe.getsockname()[1])


class Stand:

	"""One stand-in SynthSync, started from the command line as a person would."""

	def __init__ (self, outputs: int = 4, port: int | None = None, static: pathlib.Path | None = None, config: pathlib.Path | None = None) -> None:

		"""Start a stand-in with this many outputs, and wait until it says it is serving.

		`static` is a folder to serve the page from, such as one the page has
		been packed into as the Pico holds it. Without it the page's own seven
		files are served. `config` is a `config.txt` for it to read, which
		then says how many outputs it has in place of `outputs`.
		"""

		self.port = port if port is not None else free_port()
		self.address = "http://127.0.0.1:%d" % self.port
		self.packed = static is not None

		wired = ["--config", str(config)] if config is not None else ["--outputs", str(outputs)]

		self.process = subprocess.Popen(
			[sys.executable, "-B", "-m", "synthsync.sim", "--port", str(self.port)] + wired + (["--static", str(static)] if static is not None else []),
			cwd = ROOT,
			stdout = subprocess.PIPE,
			stderr = subprocess.STDOUT,
			text = True,
		)

		assert self.process.stdout is not None

		# What it said before it said where it is, which is what it couldn't use in `config.txt`.
		self.said: list[str] = []
		line = self.process.stdout.readline()

		while line and "stand-in is at" not in line:
			self.said.append(line.rstrip("\n"))
			line = self.process.stdout.readline()

		assert "stand-in is at" in line, self.said

		# It says where it is just before it begins to listen, so it is asked until it answers, for a while.
		began = time.monotonic()

		while True:

			try:
				self.state()
				break

			except OSError:

				self.process.poll()

				assert self.process.returncode is None, "the stand-in stopped as soon as it started"
				assert time.monotonic() - began < READY_WITHIN, "the stand-in said it was serving but didn't answer within %d seconds" % READY_WITHIN

				time.sleep(0.02)

	def stop (self) -> None:

		"""Stop the stand-in."""

		self.process.terminate()
		self.process.wait(timeout=10)

	def state (self) -> dict[str, typing.Any]:

		"""Ask the stand-in for everything."""

		with urllib.request.urlopen(self.address + "/api/state", timeout=5) as response:
			return typing.cast(dict[str, typing.Any], json.loads(response.read()))

	def health (self) -> dict[str, int]:

		"""Ask the stand-in how well its side of things has kept up."""

		with urllib.request.urlopen(self.address + "/api/health", timeout=5) as response:
			return typing.cast(dict[str, int], json.loads(response.read()))

	def now (self, kind: str, index: int | None = None) -> dict[str, typing.Any]:

		"""Give one of the messages that say how things stand: the tempo, the transport, or one output."""

		for message in self.state()["now"]:
			if message["type"] == kind and (index is None or message["index"] == index):
				return typing.cast(dict[str, typing.Any], message)

		raise KeyError(kind)

	def change (self, message: dict[str, typing.Any]) -> tuple[int, typing.Any]:

		"""Send the stand-in one message, and give back the status and the answer."""

		request = urllib.request.Request(self.address + "/api/change", data=json.dumps(message).encode(), headers={"Content-Type": "application/json"})

		try:
			with urllib.request.urlopen(request, timeout=5) as response:
				return response.status, json.loads(response.read())

		except urllib.error.HTTPError as refused:
			return refused.code, json.loads(refused.read())

	def as_new (self) -> None:

		"""Put every setting back and set the clock running, as it is when first started."""

		self.change({"do": "reset"})
		self.change({"do": "stop"})
		self.change({"do": "start"})

		# The stand-in takes a moment to act on a change to its network, as a Pico does.
		# A test must not begin while it is still leaving the network the last test joined.
		until = time.monotonic() + 5

		while self.now("network")["mode"] != "own" or self.now("network")["problem"]:

			if time.monotonic() > until:
				raise RuntimeError("The stand-in didn't go back to its own network")

			time.sleep(0.02)
