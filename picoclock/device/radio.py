"""The Pico's radio, worked as `picoclock.core.wifi` asks.

`Wifi` there decides what the radio should be doing: running the Pico's own
network, joining someone else's, or staying on one it has joined. This does
it. `make` puts the two together for a clock, and `run` keeps them going.

All of it is the first core's work. Starting or stopping a network keeps the
first core for up to half a second, and listening for networks for a little
longer. The clock is on the second core and notices neither.
"""

import asyncio
import machine
import network
import time

import picoclock.core.wifi
import picoclock.device.chip

# How often to see what the radio should be doing, in milliseconds.
LOOK_EVERY = 100

# The radio chip's own number for WPA2, the kind of password every phone can use.
_WPA2 = 0x00400004

# What the radio chip says of a join, and what that is called here. Its 2
# means it has been let in and is waiting to be given an address.
_LINKS = {
	network.STAT_IDLE: picoclock.core.wifi.IDLE,
	network.STAT_CONNECTING: picoclock.core.wifi.TRYING,
	2: picoclock.core.wifi.NO_ADDRESS,
	network.STAT_GOT_IP: picoclock.core.wifi.LINKED,
	network.STAT_NO_AP_FOUND: picoclock.core.wifi.NOT_FOUND,
	network.STAT_WRONG_PASSWORD: picoclock.core.wifi.WRONG_PASSWORD,
	network.STAT_CONNECT_FAIL: picoclock.core.wifi.FAILED,
}


class Radio:

	"""Starts and stops the Pico's own network, joins and leaves another, and listens for those in range.

	Whatever changes what the radio chip is doing goes through
	`picoclock.device.chip.unbroken`, so that an interrupt from the Pico's
	prompt can't leave the chip half way through it.
	"""

	def __init__ (self) -> None:

		"""Take hold of the radio's two sides. Nothing is switched on."""

		self._own = network.WLAN(network.AP_IF)
		self._other = network.WLAN(network.STA_IF)

		# Whether a join has been begun and not left. The chip goes on saying
		# how its last join ended long after it has been told to leave.
		self._away = False

	def own_up (self, name: str, password: str, host_name: str) -> None:

		"""Start the Pico's own network, in place of one already up.

		The name and the password are given before the network is switched
		on, so the one MicroPython would otherwise start, whose password
		anyone can look up, never appears.
		"""

		# The name the Pico answers to is taken up as a network starts, so it goes first.
		network.hostname(host_name)

		picoclock.device.chip.unbroken(self._start_own, name, password)

	def _start_own (self, name: str, password: str) -> None:

		"""Give the radio the own network's name and password, and switch it on."""

		# Given these while its network is up, the radio stops the network and starts it again by itself.
		self._own.config(ssid=name, password=password, security=_WPA2)
		self._own.active(True)

	def own_down (self) -> None:

		"""Stop the Pico's own network, if it is up."""

		if self._own.active():
			picoclock.device.chip.unbroken(self._own.active, False)

	def join (self, name: str, password: str, host_name: str) -> None:

		"""Begin joining a network, or begin again. It carries on by itself, and `link` says how it is going."""

		network.hostname(host_name)
		self._away = True

		picoclock.device.chip.unbroken(self._start_join, name, password)

	def _start_join (self, name: str, password: str) -> None:

		"""Switch the joining side on, if it isn't, and ask for the network."""

		if not self._other.active():

			self._other.active(True)

			# The radio would otherwise doze between messages to save power, and a knob turned on the page would lag.
			self._other.config(pm=network.WLAN.PM_NONE)

		# With no password the radio takes the network to be an open one.
		self._other.connect(name, password if password else None)

	def leave (self) -> None:

		"""Stop joining, or leave the network that was joined."""

		self._away = False

		if self._other.active():
			picoclock.device.chip.unbroken(self._stop_join)

	def _stop_join (self) -> None:

		"""Leave the network and switch the joining side off."""

		self._other.disconnect()
		self._other.active(False)

	def link (self) -> str:

		"""Say how the join is going, in `picoclock.core.wifi`'s words."""

		if not self._away:
			return picoclock.core.wifi.IDLE

		return _LINKS.get(self._other.status(), picoclock.core.wifi.FAILED)

	def address (self) -> str:

		"""Give the address the page is at on whichever network is up, or nothing."""

		if self._own.active():
			return self._own.ipconfig("addr4")[0]

		if self._away and self._other.status() == network.STAT_GOT_IP:
			return self._other.ipconfig("addr4")[0]

		return ""

	def on (self) -> str:

		"""Say which sides of the radio are switched on: "own", "joining", "both" or "none".

		It should never be "both". MicroPython's own network answers requests
		for an address on every network the Pico is on, so with both up it
		could hand out addresses on someone's home network.
		"""

		own = self._own.active()
		other = self._other.active()

		if own and other:
			return "both"

		return "own" if own else "joining" if other else "none"

	def heard (self) -> list:

		"""Listen for networks in range, and give the name, the strength and the kind of security of each transmitter heard.

		The radio listens for about two thirds of a second, and the first
		core does nothing else meanwhile.
		"""

		found: list = []

		picoclock.device.chip.unbroken(self._listen, found)

		return [(each[0], each[3], each[4]) for each in found]

	def _listen (self, found: list) -> None:

		"""Listen, and put what was heard in a list made for it."""

		found.extend(self._other.scan())

	def stations (self) -> int:

		"""Say how many phones, or anything else, have joined the Pico's own network."""

		if not self._own.active():
			return 0

		return len(self._own.status("stations"))


def make (clock) -> picoclock.core.wifi.Wifi:

	"""Make the WiFi for a clock on the Pico's own radio. Its own network is named after its hardware id."""

	return picoclock.core.wifi.Wifi(clock, Radio(), picoclock.core.wifi.own_name(machine.unique_id()))


async def run (wifi: picoclock.core.wifi.Wifi) -> None:

	"""Keep the radio doing what the settings ask, for as long as the Pico is on.

	Whenever what it is doing changes, a line says so over USB, for anyone
	watching and for the tool that gives the Pico a network to join. The
	line never names a network: only how things stand and where the page is.
	"""

	looked = time.ticks_ms()
	mode = None
	reason = None

	while True:

		now = time.ticks_ms()
		wifi.look(time.ticks_diff(now, looked))
		looked = now

		if wifi.mode != mode or wifi.reason != reason:

			mode = wifi.mode
			reason = wifi.reason

			print("PICOCLOCK NETWORK mode=%s address=%s reason=%s" % (mode or "none", wifi.status()["address"] or "none", reason or "none"))

		await asyncio.sleep_ms(LOOK_EVERY)
