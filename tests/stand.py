"""A stand-in PicoClock running on its own, for the tests that open the page in a real browser.

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

ROOT = pathlib.Path(__file__).parent.parent


def free_port () -> int:

	"""Find a port nothing is listening on."""

	with socket.socket() as probe:
		probe.bind(("127.0.0.1", 0))
		return int(probe.getsockname()[1])


class Stand:

	"""One stand-in PicoClock, started from the command line as a person would."""

	def __init__ (self, outputs: int = 4, port: int | None = None) -> None:

		"""Start a stand-in with this many outputs, and wait until it says it is serving."""

		self.port = port if port is not None else free_port()
		self.address = "http://127.0.0.1:%d" % self.port

		self.process = subprocess.Popen(
			[sys.executable, "-B", "-m", "picoclock.sim", "--port", str(self.port), "--outputs", str(outputs)],
			cwd = ROOT,
			stdout = subprocess.PIPE,
			stderr = subprocess.STDOUT,
			text = True,
		)

		assert self.process.stdout is not None

		line = self.process.stdout.readline()

		assert "stand-in is at" in line, line

		# It says where it is just before it begins to listen.
		for _ in range(200):

			try:
				self.state()
				break
			except OSError:
				self.process.poll()
				assert self.process.returncode is None, "the stand-in stopped as soon as it started"

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
