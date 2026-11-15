"""The clock as a whole: its settings, whether it's running, and the engine that plays it.

Everything that changes PicoClock goes through here, whether it comes from the
page, a hardware input or power-up, so a change means the same thing wherever
it comes from. Each change is checked, noted to be saved, passed to the engine,
and told to whoever is watching, which is how every open page stays in step.

The one exception is the Pico's start, pause and stop buttons. They are read
where the clock is played, so they work even while everything else is held
up, and the clock catches up with what they did a moment later.

The engine is whatever turns the settings into pulses. On the Pico it is the
player, and on a laptop it is a stand-in with no pins. Both take the same
calls, which `Engine` lists.

A knob being dragged changes a setting many times a second. The setting itself
changes at once, but the engine is only given the latest value, at most fifty
times a second, so a burst can never crowd the part that keeps time.
"""

import asyncio

import picoclock.core.settings
import picoclock.core.transport

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:

	import typing

	class Engine (typing.Protocol):

		"""What the clock needs from whatever plays it.

		Every call returns at once. Values have been checked before they get
		here, as `picoclock.core.settings` does.
		"""

		def set_tempo (self, tempo: int) -> None:

			"""Change the tempo, in hundredths of a BPM."""

		def set_ratio (self, output: int, pulses: int, beats: int) -> None:

			"""Give an output a new ratio: this many pulses to this many beats."""

		def set_mute (self, output: int, muted: bool) -> None:

			"""Silence an output, or let it sound again."""

		def set_width (self, output: int, ticks: int) -> None:

			"""Set how long an output's pulses last, in microseconds, or 0 for half the gap."""

		def set_role (self, output: int, role: int) -> None:

			"""Say what an output is for, as one of the scheduler's role numbers."""

		def press (self, command: str) -> None:

			"""Start, pause or stop, as `picoclock.core.transport`'s table says for whatever the clock is really doing."""

		def transport (self) -> str | None:

			"""Say whether the clock is really stopped, running or paused, or None while a press is still on its way to it."""

		def status (self) -> dict[str, typing.Any]:

			"""Say how the clock is doing.

			`position` is how far it has played, in thousandths of a beat, or
			-1 when that can't be said just now. `faults` is how many times
			the timing has slipped, which should stay at zero.
			"""

	class Storage (typing.Protocol):

		"""What the clock needs from whatever saves its settings."""

		def changed (self) -> None:

			"""Note that a setting has changed and is to be saved."""

		def forget (self) -> None:

			"""Remove what was saved."""

	Watcher = typing.Callable[[str | int, object], None]

# How often the engine is given what has changed, in milliseconds.
FLUSH_EVERY = 20

# What a watcher is told has changed: one of these, or an output's number.
TEMPO = "tempo"
TRANSPORT = "transport"
NETWORK = "network"
EVERYTHING = "everything"

# The parts of an output the engine has yet to be given.
_RATIO = 1
_WIDTH = 2
_ROLE = 4
_MUTE = 8
_WHOLE_OUTPUT = 15

# Which part of an output each of its settings changes. Snap is only for the
# page's knob, so the engine never hears of it.
_PARTS = {
	"ratio": _RATIO,
	"pulse": _WIDTH,
	"role": _ROLE,
	"muted": _MUTE,
	"snap": 0,
}


class Clock:

	"""Holds the settings, the transport and the engine together.

	Each change takes a `by`, which is handed to the watchers untouched. The
	page's API uses it to say which page made a change, so that page can tell
	its own changes from someone else's.
	"""

	def __init__ (self, settings: picoclock.core.settings.Settings, engine: "Engine", storage: "Storage | None" = None) -> None:

		"""Bring these settings and this engine together. With storage, changes are saved."""

		self.settings = settings
		self.engine = engine
		self.storage = storage
		self.transport = picoclock.core.transport.Transport()

		self._watchers: "list[Watcher]" = []

		# How many times a network has been asked for. Whatever works the radio
		# uses it to tell a fresh request to join from any other change, even
		# when the request names the network the settings already hold.
		self.joins = 0

		# How many times the page has asked to switch now to a network a trial join found, for the same reason.
		self.switches = 0

		# What the engine hasn't yet been given: the tempo, and parts of each output.
		self._stale_tempo = False
		self._stale = [0] * settings.count

	def watch (self, watcher: "Watcher") -> None:

		"""Ask to be told of every change.

		The watcher is called with what changed and the `by` the change came
		with. What changed is TEMPO, TRANSPORT, NETWORK, EVERYTHING, or the
		number of an output, counted from 0.
		"""

		self._watchers.append(watcher)

	def _tell (self, what: "str | int", by: object) -> None:

		"""Tell every watcher of one change."""

		for watcher in self._watchers:
			watcher(what, by)

	def _keep (self) -> None:

		"""Note that the settings are to be saved."""

		if self.storage is not None:
			self.storage.changed()

	def _all_stale (self) -> None:

		"""Note that the engine needs every setting afresh."""

		self._stale_tempo = True

		for index in range(self.settings.count):
			self._stale[index] = _WHOLE_OUTPUT

	def load (self) -> None:

		"""Give the engine every setting, as at power-up."""

		self._all_stale()
		self.flush()

	def set_tempo (self, tempo: object, by: object = None) -> None:

		"""Change the tempo, in hundredths of a BPM."""

		self.settings.set_tempo(tempo)

		self._stale_tempo = True
		self._keep()
		self._tell(TEMPO, by)

	def set_output (self, index: object, name: object, value: object, by: object = None) -> None:

		"""Change one setting of one output: its "ratio", "snap", "muted", "pulse" or "role".

		Outputs are numbered from 0 here, and from 1 on the page.
		"""

		self.settings.set_output(index, name, value)

		# The settings have just checked that the output and the setting exist.
		assert isinstance(index, int) and isinstance(name, str)

		self._stale[index] |= _PARTS[name]
		self._keep()
		self._tell(index, by)

	def press (self, command: str, by: object = None) -> str:

		"""Start, pause or stop the clock, and say what that did.

		The answer is one of the transport's actions, which is NOTHING when
		the command changes nothing, such as starting a clock that's running.

		The engine is given every press, and carries it out by the same table
		from what the clock is really doing. A button on the Pico may have
		changed that a moment ago, before this clock heard of it, and the
		press then still means what it should.
		"""

		self.catch_up()

		action = self.transport.press(command)

		# Whatever was changed just before goes first, so a start begins with it.
		self.flush()
		self.engine.press(command)

		if action != picoclock.core.transport.NOTHING:
			self._tell(TRANSPORT, by)

		return action

	def catch_up (self) -> None:

		"""Take what the engine says the clock is doing, when it isn't what this clock thinks, and tell every watcher.

		The Pico's start, pause and stop buttons are read where the clock is
		played, so a press works even while everything else is held up. This
		is how the rest hears of it afterwards. While a press from here is
		still on its way to the engine, the engine can't say, and nothing is
		taken.
		"""

		state = self.engine.transport()

		if state is None or state == self.transport.state:
			return

		self.transport.state = state
		self._tell(TRANSPORT, None)

	def reset (self, by: object = None) -> None:

		"""Put every setting back to how a new PicoClock has it, and forget what was saved.

		The clock carries on as it was, running or not, with the new settings.
		"""

		self.settings.reset()

		if self.storage is not None:
			self.storage.forget()

		self._all_stale()
		self._tell(EVERYTHING, by)

	def set_network (self, name: object, password: object, by: object = None) -> None:

		"""Remember a WiFi network to join, with its password, and count it as a request to join it."""

		self.settings.set_network(name, password)

		self.joins += 1
		self._keep()
		self._tell(NETWORK, by)

	def forget_network (self, by: object = None) -> None:

		"""Forget the WiFi network to join, so the Pico goes back to its own."""

		self.settings.forget_network()

		self._keep()
		self._tell(NETWORK, by)

	def switch_network (self, by: object = None) -> None:

		"""Have the Pico join for good, without waiting, the network a trial join found. Nothing is saved: the settings already hold it."""

		self.switches += 1
		self._tell(NETWORK, by)

	def set_own_password (self, password: object, by: object = None) -> None:

		"""Change the password of the Pico's own network."""

		self.settings.set_own_password(password)

		self._keep()
		self._tell(NETWORK, by)

	def set_host_name (self, name: object, by: object = None) -> None:

		"""Change the name the Pico answers to on a network."""

		self.settings.set_host_name(name)

		self._keep()
		self._tell(NETWORK, by)

	def flush (self) -> None:

		"""Give the engine the latest of whatever has changed since it was last given anything."""

		settings = self.settings
		engine = self.engine

		# Each note is struck off only once the engine has taken the value, so
		# one it couldn't take is given again next time.
		if self._stale_tempo:
			engine.set_tempo(settings.tempo)
			self._stale_tempo = False

		for index in range(settings.count):

			stale = self._stale[index]

			if not stale:
				continue

			output = settings.outputs[index]

			if stale & _RATIO:
				engine.set_ratio(index, output.ratio[0], output.ratio[1])
				self._stale[index] &= ~_RATIO

			if stale & _WIDTH:
				engine.set_width(index, output.width_ticks())
				self._stale[index] &= ~_WIDTH

			if stale & _ROLE:
				engine.set_role(index, output.role_number())
				self._stale[index] &= ~_ROLE

			if stale & _MUTE:
				engine.set_mute(index, output.muted)
				self._stale[index] &= ~_MUTE

	async def run (self) -> None:

		"""Keep the engine up to date with the settings, and this clock up to date with the buttons. Runs for as long as the clock does."""

		while True:

			await asyncio.sleep(FLUSH_EVERY / 1000)

			self.catch_up()

			try:
				self.flush()

			except OSError:
				# The engine isn't taking anything just now. What it missed is
				# still noted, and is given again on the next round.
				pass
