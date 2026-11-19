"""Everything SynthSync remembers: the tempo, the clock's PPQN, MIDI out, each output's settings, and the network.

One model serves the page, the saved file and the laptop stand-in. A change is
checked as it's made and refused with a message the page can show. The saved
form uses only whole numbers, text and yes-or-no values, never decimals, so it
reads back exactly on the Pico.

Loading is forgiving on purpose. A file that is missing, damaged or from an
older version must never stop the clock starting, so whatever can be read is
kept and everything else falls back to its default.

How many outputs there are is `config.txt`'s to say, not these settings'. A
saved file can hold more outputs than are wired now. Those past the last one
are kept as they were saved, unchecked, and saved again with the rest, so
wiring them again brings their settings back. Starting again as new forgets
them with everything else.
"""

import json

import synthsync.core.limits
import synthsync.core.ratio
import synthsync.core.timeline

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing

# Raised each time the saved form changes shape. 2 added the clock's PPQN, 3
# MIDI out, and 4 where the tempo knob was.
VERSION = 4

# Tempo is held in hundredths of a BPM: 12000 is 120 BPM.
DEFAULT_TEMPO = 12000

# What MIDI out can do, in the order of the scheduler's numbers for them: nothing,
# clock only while playing, or clock all the time.
MIDI_MODES = ("off", "playing", "always")
DEFAULT_MIDI = "always"

# A pulse is this many milliseconds long, or HALF for half the gap between pulses.
DEFAULT_PULSE = 10
SHORTEST_PULSE = 1
LONGEST_PULSE = 100
HALF = 0

# What an output can be for, in the order the scheduler numbers them.
ROLES = ("clock", "start", "stop", "run")

DEFAULT_PASSWORD = "synthsync"
DEFAULT_HOST_NAME = "synthsync"

# Where the tempo knob was when last noted, as a reading from 0 to 4095, or this
# when it never has been.
NO_KNOB = -1
_FULLEST_KNOB = 4095

_DIGITS = "0123456789"
_NAME_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789-"
_MAIN_CLOCK = "Output 1 is the main clock, and always sends the clock itself: its pulses a beat are set in Settings"


def parse_tempo (text: str) -> int:

	"""Read a tempo typed in BPM, such as "120" or "123.45", as hundredths of a BPM."""

	message = "Type a tempo as a number of beats a minute, such as 120 or 123.45"
	cleaned = text.strip()

	if cleaned.count(".") > 1:
		raise ValueError(message)

	whole, _, places = cleaned.partition(".")

	if (not whole and not places) or len(whole) > 4:
		raise ValueError(message)

	for character in whole + places:
		if character not in _DIGITS:
			raise ValueError(message)

	if len(places) > 2:
		raise ValueError("A tempo takes at most two decimal places")

	# Pad the places to two, so "120.5" is 120.50.
	return int(whole or "0") * 100 + int((places + "00")[:2])


def tempo_text (tempo: int) -> str:

	"""Write a tempo held in hundredths of a BPM the way it's typed: "120", "120.5", "123.45"."""

	whole = tempo // 100
	places = tempo % 100

	if places == 0:
		return "%d" % whole

	if places % 10 == 0:
		return "%d.%d" % (whole, places // 10)

	return "%d.%02d" % (whole, places)


def _attempt (problems: list[str], what: str, change: "typing.Callable[[], object]") -> None:

	"""Make one change from a saved file, and note it rather than fail if it can't be made. What the change gives back isn't wanted."""

	try:
		change()
	except (ValueError, TypeError, KeyError, IndexError) as error:
		problems.append("%s: %s" % (what, error))


class Output:

	"""One output's settings."""

	def __init__ (self) -> None:

		"""Begin as a clock at one pulse a beat, with a 10 ms pulse, snap on and not muted."""

		self.ratio = synthsync.core.ratio.ONE
		self.snap = True
		self.muted = False
		self.pulse = DEFAULT_PULSE
		self.role = ROLES[0]

	def width_ticks (self) -> int:

		"""Give the pulse length the way the scheduler takes it: ticks, or 0 for half the gap."""

		return self.pulse * 1000

	def role_number (self) -> int:

		"""Give the role the way the scheduler takes it: a number."""

		return ROLES.index(self.role)


class Settings:

	"""The tempo, the clock's PPQN, MIDI out, every output's settings and the network settings, with their checks."""

	def __init__ (self, outputs: int, limits: "synthsync.core.limits.Limits" = synthsync.core.limits.DEFAULT) -> None:

		"""Begin with the defaults, for this many outputs, in this tempo range."""

		self.count = outputs
		self.limits = limits
		self.tempo = limits.tempo_in(DEFAULT_TEMPO)
		self.ppqn = self._playable_ppqn(synthsync.core.ratio.DEFAULT_PPQN)
		self.midi = DEFAULT_MIDI
		self.knob_at = NO_KNOB
		self.outputs = [Output() for _ in range(outputs)]

		# The saved settings of outputs past the last one wired, as they were saved.
		self.beyond: list[object] = []

		self.own_password = DEFAULT_PASSWORD
		self.host_name = DEFAULT_HOST_NAME
		self.network_name = ""
		self.network_password = ""

	def reset (self) -> None:

		"""Go back to the defaults, as a factory reset does."""

		self.tempo = self.limits.tempo_in(DEFAULT_TEMPO)
		self.ppqn = self._playable_ppqn(synthsync.core.ratio.DEFAULT_PPQN)
		self.midi = DEFAULT_MIDI
		self.knob_at = NO_KNOB
		self.outputs = [Output() for _ in range(self.count)]
		self.beyond = []
		self.own_password = DEFAULT_PASSWORD
		self.host_name = DEFAULT_HOST_NAME
		self.network_name = ""
		self.network_password = ""

	def set_tempo (self, tempo: object) -> None:

		"""Set the tempo, in hundredths of a BPM."""

		if isinstance(tempo, bool) or not isinstance(tempo, int):
			raise ValueError("A tempo is a whole number of hundredths of a BPM")

		if tempo < self.limits.slowest_tempo or tempo > self.limits.fastest_tempo:
			raise ValueError("The tempo has to be between %s and %s BPM" % (tempo_text(self.limits.slowest_tempo), tempo_text(self.limits.fastest_tempo)))

		self.tempo = tempo

	def set_knob_at (self, reading: object) -> None:

		"""Note where the tempo knob is, as a reading from 0 to 4095."""

		if isinstance(reading, bool) or not isinstance(reading, int) or reading < 0 or reading > _FULLEST_KNOB:
			raise ValueError("The knob reads from 0 to %d" % _FULLEST_KNOB)

		self.knob_at = reading

	def _playable_ppqn (self, ppqn: int) -> int:

		"""Give this PPQN, or the nearest below it that this tempo range can send."""

		found = synthsync.core.ratio.ppqns(self.limits)[0]

		for each in synthsync.core.ratio.ppqns(self.limits):
			if each <= ppqn:
				found = each

		return found

	def set_midi (self, mode: object) -> None:

		"""Set what MIDI out does: "off", "playing" or "always"."""

		if mode not in MIDI_MODES:
			raise ValueError("MIDI clock is off, on while playing, or always on")

		self.midi = mode

	def midi_number (self) -> int:

		"""Give what MIDI out does as the scheduler's number for it."""

		return MIDI_MODES.index(self.midi)

	def set_ppqn (self, ppqn: object) -> "list[int]":

		"""Set how many pulses the clock sends a beat, and give the outputs whose ratio had to move with it.

		Every output's ratio is a ratio of the clock, so each one keeps its
		ratio and changes speed with the clock. One that would then pass the
		most pulses a beat any output may send is moved to the fastest ratio
		there is at the new PPQN, which is a snap value. One too fine to play
		exactly at the new PPQN is moved to its nearest snap value, as turning
		snap on would. A tempo range reaching below 20 BPM can also move one
		that would wait too long between pulses to the slowest there is.
		"""

		allowed = synthsync.core.ratio.ppqns(self.limits)

		if isinstance(ppqn, bool) or not isinstance(ppqn, int) or ppqn not in allowed:
			choices = ["%d" % each for each in allowed]
			raise ValueError("The clock sends %s or %s pulses a beat" % (", ".join(choices[:-1]), choices[-1]))

		moved = []

		for index in range(self.count):

			output = self.outputs[index]

			# What an output sends a beat is its ratio times the PPQN, so a
			# fraction fine enough at a low PPQN can have too large a numerator
			# for the scheduler at a higher one.
			fitted = synthsync.core.ratio.fit(output.ratio, ppqn, self.limits)

			if fitted != output.ratio:
				output.ratio = fitted
				moved.append(index)

		self.ppqn = ppqn

		return moved

	def set_output (self, index: object, name: object, value: object) -> "tuple[str, ...]":

		"""Change one setting of one output, and give the names of every setting that changed with it.

		The setting is "ratio", "snap", "muted", "pulse" or "role". Outputs are
		numbered from 0 here, and from 1 on the page. Turning snap on moves the
		ratio to the nearest snap value, so that change gives ("snap", "ratio").
		"""

		changed: "tuple[str, ...]" = (str(name),)

		if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= self.count:
			raise ValueError("There is no such output")

		output = self.outputs[index]

		if name == "ratio":

			if not isinstance(value, str):
				raise ValueError("A ratio is typed as a number such as 1.5, or as a fraction such as 4/3")

			ratio = synthsync.core.ratio.parse(value, self.ppqn, self.limits)

			if index == 0 and ratio != synthsync.core.ratio.ONE:
				raise ValueError(_MAIN_CLOCK)

			output.ratio = ratio

		elif name == "snap" or name == "muted":

			if not isinstance(value, bool):
				raise ValueError("That setting is either on or off")

			if name == "snap":

				# Turned on, snap takes a ratio set freely to the nearest value the knob would have stopped on.
				if value and not output.snap:

					nearest = synthsync.core.ratio.nearest_snap(output.ratio, self.ppqn, self.limits)

					if nearest != output.ratio:
						output.ratio = nearest
						changed = ("snap", "ratio")

				output.snap = value

			else:
				output.muted = value

		elif name == "pulse":

			if value == "half":
				output.pulse = HALF

			elif isinstance(value, bool) or not isinstance(value, int) or value < SHORTEST_PULSE or value > LONGEST_PULSE:
				raise ValueError("A pulse lasts between %d and %d milliseconds, or half the gap between pulses" % (SHORTEST_PULSE, LONGEST_PULSE))

			else:
				output.pulse = value

		elif name == "role":

			if not isinstance(value, str) or value not in ROLES:
				raise ValueError("An output is a clock, a start pulse, a stop pulse or a run output")

			if index == 0 and value != ROLES[0]:
				raise ValueError(_MAIN_CLOCK)

			output.role = value

		else:
			raise ValueError("An output has a ratio, a snap switch, a mute, a pulse and a role")

		return changed

	def set_own_password (self, password: object) -> None:

		"""Set the password of the Pico's own WiFi network."""

		if not isinstance(password, str) or len(password) < 8 or len(password) > 63:
			raise ValueError("The password needs between 8 and 63 characters")

		for character in password:
			if ord(character) < 32 or ord(character) > 126:
				raise ValueError("The password can use letters, digits, spaces and common punctuation only")

		self.own_password = password

	def set_host_name (self, name: object) -> None:

		"""Set the name the Pico answers to on a network: "synthsync" makes it synthsync.local."""

		message = "The name can use letters, digits and hyphens, up to 32 of them, and can't begin or end with a hyphen"

		if not isinstance(name, str):
			raise ValueError(message)

		cleaned = name.strip().lower()

		if not cleaned or len(cleaned) > 32 or cleaned[0] == "-" or cleaned[-1] == "-":
			raise ValueError(message)

		for character in cleaned:
			if character not in _NAME_CHARACTERS:
				raise ValueError(message)

		self.host_name = cleaned

	def set_network (self, name: object, password: object) -> None:

		"""Remember a WiFi network to join, with its password, which is empty for an open network."""

		if not isinstance(name, str) or not name or len(name.encode()) > 32:
			raise ValueError("A network's name has between 1 and 32 characters")

		if not isinstance(password, str) or (password and len(password) < 8) or len(password) > 63:
			raise ValueError("A network's password has between 8 and 63 characters, or none at all")

		self.network_name = name
		self.network_password = password

	def forget_network (self) -> None:

		"""Forget the WiFi network to join, so the Pico uses its own."""

		self.network_name = ""
		self.network_password = ""

	def to_dict (self) -> dict[str, object]:

		"""Give everything in the plain form that is saved."""

		outputs: list[object] = []

		for output in self.outputs:
			outputs.append({
				"ratio": synthsync.core.ratio.to_text(output.ratio),
				"snap": output.snap,
				"muted": output.muted,
				"pulse": "half" if output.pulse == HALF else output.pulse,
				"role": output.role,
			})

		outputs.extend(self.beyond)

		return {
			"version": VERSION,
			"tempo": self.tempo,
			"ppqn": self.ppqn,
			"midi": self.midi,
			"knob_at": self.knob_at,
			"outputs": outputs,
			"own_password": self.own_password,
			"host_name": self.host_name,
			"network_name": self.network_name,
			"network_password": self.network_password,
		}

	def _load_tempo (self, tempo: object) -> None:

		"""Take a saved tempo. One any range could hold goes to the nearest in this one, so a narrower range in `config.txt` never counts as damage."""

		if isinstance(tempo, int) and not isinstance(tempo, bool) and synthsync.core.limits.SLOWEST_TEMPO <= tempo <= synthsync.core.limits.FASTEST_TEMPO:
			tempo = self.limits.tempo_in(tempo)

		self.set_tempo(tempo)

	def _load_ppqn (self, ppqn: object) -> None:

		"""Take a saved PPQN, going to the nearest below it that this tempo range can send."""

		if isinstance(ppqn, int) and not isinstance(ppqn, bool) and ppqn in synthsync.core.ratio.PPQNS:
			ppqn = self._playable_ppqn(ppqn)

		self.set_ppqn(ppqn)

	def _load_ratio (self, index: int, value: object) -> None:

		"""Take an output's saved ratio. One the usual range allows goes to the nearest that this range does."""

		if isinstance(value, str):
			value = synthsync.core.ratio.to_text(synthsync.core.ratio.fit(synthsync.core.ratio.parse(value, self.ppqn), self.ppqn, self.limits))

		self.set_output(index, "ratio", value)

	def load_dict (self, data: object) -> list[str]:

		"""Take settings from the saved form, keeping what can be read and defaulting the rest.

		Hands back a list of what couldn't be used, which is empty when all of
		it could.
		"""

		problems: list[str] = []

		self.reset()

		if not isinstance(data, dict):
			problems.append("the saved settings aren't in a form that can be read")
			return problems

		if "tempo" in data:
			_attempt(problems, "tempo", lambda: self._load_tempo(data["tempo"]))

		# Before the outputs, whose ratios are checked against it. A file saved
		# before there was a PPQN has none, and its ratios are read as ratios of
		# the default clock.
		if "ppqn" in data:
			_attempt(problems, "ppqn", lambda: self._load_ppqn(data["ppqn"]))

		# A file saved before there was MIDI out has none, and gets the default.
		if "midi" in data:
			_attempt(problems, "midi", lambda: self.set_midi(data["midi"]))

		# A file saved before there was a tempo knob has no place for it.
		if "knob_at" in data and data["knob_at"] != NO_KNOB:
			_attempt(problems, "knob", lambda: self.set_knob_at(data["knob_at"]))

		saved = data.get("outputs", [])

		if not isinstance(saved, list):
			problems.append("outputs: not a list")
			saved = []

		for index in range(min(self.count, len(saved))):

			entry = saved[index]

			if not isinstance(entry, dict):
				problems.append("output %d: not readable" % (index + 1))
				continue

			if "ratio" in entry:
				_attempt(problems, "output %d ratio" % (index + 1), lambda: self._load_ratio(index, entry["ratio"]))

			for name in ("snap", "muted", "pulse", "role"):
				if name in entry:
					_attempt(problems, "output %d %s" % (index + 1, name), lambda: self.set_output(index, name, entry[name]))

		# Kept for when they are wired again, and checked then.
		self.beyond = saved[self.count:synthsync.core.timeline.MOST_OUTPUTS]

		if "own_password" in data:
			_attempt(problems, "own password", lambda: self.set_own_password(data["own_password"]))

		if "host_name" in data:
			_attempt(problems, "host name", lambda: self.set_host_name(data["host_name"]))

		if data.get("network_name"):
			_attempt(problems, "network", lambda: self.set_network(data["network_name"], data.get("network_password", "")))

		return problems

	def to_text (self) -> str:

		"""Give everything as the text that is written to the saved file."""

		return json.dumps(self.to_dict())

	def load_text (self, text: str) -> list[str]:

		"""Take settings from the text of a saved file, however damaged it is.

		Hands back a list of what couldn't be used.
		"""

		try:
			data = json.loads(text)
		except ValueError:
			self.reset()
			return ["the saved settings couldn't be read, so the defaults are in use"]

		return self.load_dict(data)
