"""A pretend radio, so the page's network settings can be tried on a laptop.

Nothing here touches a real network. A few made-up networks are "in range",
and the pretend radio joins one when it is given the right password. What to
do with the radio is decided by the same code that decides it on a Pico, so a
join here goes as it does there, only sooner: a Pico tries for twenty seconds
before it gives up, and this tries for one.
"""

import asyncio
import time

import synthsync.core.clock
import synthsync.core.wifi

# The name the pretend Pico gives its own network. A real one ends in six
# characters of its hardware id, and this one's id is all noughts.
OWN_NAME = synthsync.core.wifi.own_name(bytes(3))

# The networks that are always in range, with their passwords. An empty
# password is an open network.
IN_RANGE = {
	"Studio": "studio wifi",
	"Upstairs": "upstairs wifi",
	"Cafe": "",
}

_STRENGTHS = {
	"Studio": -48,
	"Upstairs": -71,
	"Cafe": -83,
}

# How long a join is tried for, how long a joined network may be lost for, how
# long is left between tries, and how long the own network stays up after a
# trial join, in milliseconds. Each is a fraction of what a Pico allows, so
# that nobody trying the page has to wait as long.
JOIN_FOR = 1000
LOST_FOR = 3000
AGAIN_AFTER = 250
STAY_FOR = 5000
LISTEN_EVERY = 5000

# How often the pretend radio is looked at, in milliseconds.
LOOK_EVERY = 50


class Radio:

	"""Stands in for the Pico's radio: its own network, and joining one of the made-up ones."""

	def __init__ (self) -> None:

		"""Begin with everything off."""

		self.own_is_up = False
		self.result = synthsync.core.wifi.IDLE

	def own_up (self, name: str, password: str, host_name: str) -> None:

		"""Start the pretend own network."""

		self.own_is_up = True

	def own_down (self) -> None:

		"""Stop the pretend own network."""

		self.own_is_up = False

	def join (self, name: str, password: str, host_name: str) -> None:

		"""Try a network: it is joined if it is one of those in range and the password is right."""

		if name not in IN_RANGE:
			self.result = synthsync.core.wifi.NOT_FOUND

		elif IN_RANGE[name] != password:
			self.result = synthsync.core.wifi.WRONG_PASSWORD

		else:
			self.result = synthsync.core.wifi.LINKED

	def leave (self) -> None:

		"""Stop trying, or leave."""

		self.result = synthsync.core.wifi.IDLE

	def link (self) -> str:

		"""Say how the join is going."""

		return self.result

	def address (self) -> str:

		"""Give the address the page is at, which on a laptop is the laptop itself."""

		return "localhost" if self.own_is_up or self.result == synthsync.core.wifi.LINKED else ""

	def heard (self) -> list[tuple[str, int, bool]]:

		"""Give what a scan would hear: every pretend network, once."""

		return [(name, _STRENGTHS[name], bool(IN_RANGE[name])) for name in IN_RANGE]

	def stations (self) -> int:

		"""Say how many phones have joined the pretend own network: none, as a laptop's page isn't on it."""

		return 0


def make (clock: synthsync.core.clock.Clock) -> synthsync.core.wifi.Wifi:

	"""Make the stand-in's WiFi for a clock, with its own network already up as a Pico's is by the time a page can open."""

	wifi = synthsync.core.wifi.Wifi(clock, Radio(), OWN_NAME, JOIN_FOR, LOST_FOR, AGAIN_AFTER, STAY_FOR, LISTEN_EVERY)
	wifi.look(0)

	return wifi


async def run (wifi: synthsync.core.wifi.Wifi) -> None:

	"""Keep the pretend radio doing what the settings ask, for as long as the stand-in runs."""

	looked = time.monotonic()

	while True:

		await asyncio.sleep(LOOK_EVERY / 1000)

		now = time.monotonic()
		wifi.look(int((now - looked) * 1000))
		looked = now
