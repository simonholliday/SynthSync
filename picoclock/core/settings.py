"""Everything PicoClock remembers: the tempo, the clock's PPQN, each output's settings, and the network.

One model serves the page, the saved file and the laptop stand-in. A change is
checked as it's made and refused with a message the page can show. The saved
form uses only whole numbers, text and yes-or-no values, never decimals, so it
reads back exactly on the Pico.

Loading is forgiving on purpose. A file that is missing, damaged or from an
older version must never stop the clock starting, so whatever can be read is
kept and everything else falls back to its default.
"""

import json

import picoclock.core.ratio
import picoclock.core.timeline

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing

# Raised each time the saved form changes shape. 2 added the clock's PPQN.
VERSION = 2

# Tempo is held in hundredths of a BPM: 12000 is 120 BPM.
DEFAULT_TEMPO = 12000

# A pulse is this many milliseconds long, or HALF for half the gap between pulses.
DEFAULT_PULSE = 10
SHORTEST_PULSE = 1
LONGEST_PULSE = 100
HALF = 0

# What an output can be for, in the order the scheduler numbers them.
ROLES = ("clock", "start", "stop", "run")

DEFAULT_PASSWORD = "picoclock"
DEFAULT_HOST_NAME = "picoclock"

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

		self.ratio = picoclock.core.ratio.ONE
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

	"""The tempo, the clock's PPQN, every output's settings and the network settings, with their checks."""

	def __init__ (self, outputs: int) -> None:

		"""Begin with the defaults, for this many outputs."""

		self.count = outputs
		self.tempo = DEFAULT_TEMPO
		self.ppqn = picoclock.core.ratio.DEFAULT_PPQN
		self.outputs = [Output() for _ in range(outputs)]
		self.own_password = DEFAULT_PASSWORD
		self.host_name = DEFAULT_HOST_NAME
		self.network_name = ""
		self.network_password = ""

	def reset (self) -> None:

		"""Go back to the defaults, as a factory reset does."""

		self.tempo = DEFAULT_TEMPO
		self.ppqn = picoclock.core.ratio.DEFAULT_PPQN
		self.outputs = [Output() for _ in range(self.count)]
		self.own_password = DEFAULT_PASSWORD
		self.host_name = DEFAULT_HOST_NAME
		self.network_name = ""
		self.network_password = ""

	def set_tempo (self, tempo: object) -> None:

		"""Set the tempo, in hundredths of a BPM."""

		if isinstance(tempo, bool) or not isinstance(tempo, int):
			raise ValueError("A tempo is a whole number of hundredths of a BPM")

		if tempo < picoclock.core.timeline.SLOWEST_TEMPO or tempo > picoclock.core.timeline.FASTEST_TEMPO:
			raise ValueError("The tempo has to be between 20 and 300 BPM")

		self.tempo = tempo

	def set_ppqn (self, ppqn: object) -> "list[int]":

		"""Set how many pulses the clock sends a beat, and give the outputs whose ratio had to move with it.

		Every output's ratio is a ratio of the clock, so each one keeps its
		ratio and changes speed with the clock. One that would then pass the
		most pulses a beat any output may send is moved to the fastest ratio
		there is at the new PPQN, which is a snap value. One too fine to play
		exactly at the new PPQN is moved to its nearest snap value, as turning
		snap on would.
		"""

		if isinstance(ppqn, bool) or not isinstance(ppqn, int) or ppqn not in picoclock.core.ratio.PPQNS:
			choices = ["%d" % each for each in picoclock.core.ratio.PPQNS]
			raise ValueError("The clock sends %s or %s pulses a beat" % (", ".join(choices[:-1]), choices[-1]))

		top = picoclock.core.ratio.fastest(ppqn)
		moved = []

		for index in range(self.count):

			output = self.outputs[index]

			if picoclock.core.ratio.compare(output.ratio, top) > 0:
				output.ratio = top
				moved.append(index)

			# What an output sends a beat is its ratio times the PPQN, so a
			# fraction fine enough at a low PPQN can have too large a numerator
			# for the scheduler at a higher one.
			elif picoclock.core.ratio.effective(output.ratio, ppqn)[0] > picoclock.core.ratio.LARGEST_PART:
				output.ratio = picoclock.core.ratio.nearest_snap(output.ratio, ppqn)
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

			ratio = picoclock.core.ratio.parse(value, self.ppqn)

			if index == 0 and ratio != picoclock.core.ratio.ONE:
				raise ValueError(_MAIN_CLOCK)

			output.ratio = ratio

		elif name == "snap" or name == "muted":

			if not isinstance(value, bool):
				raise ValueError("That setting is either on or off")

			if name == "snap":

				# Turned on, snap takes a ratio set freely to the nearest value the knob would have stopped on.
				if value and not output.snap:

					nearest = picoclock.core.ratio.nearest_snap(output.ratio, self.ppqn)

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

		"""Set the name the Pico answers to on a network: "picoclock" makes it picoclock.local."""

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
				"ratio": picoclock.core.ratio.to_text(output.ratio),
				"snap": output.snap,
				"muted": output.muted,
				"pulse": "half" if output.pulse == HALF else output.pulse,
				"role": output.role,
			})

		return {
			"version": VERSION,
			"tempo": self.tempo,
			"ppqn": self.ppqn,
			"outputs": outputs,
			"own_password": self.own_password,
			"host_name": self.host_name,
			"network_name": self.network_name,
			"network_password": self.network_password,
		}

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
			_attempt(problems, "tempo", lambda: self.set_tempo(data["tempo"]))

		# Before the outputs, whose ratios are checked against it. A file saved
		# before there was a PPQN has none, and its ratios are read as ratios of
		# the default clock.
		if "ppqn" in data:
			_attempt(problems, "ppqn", lambda: self.set_ppqn(data["ppqn"]))

		saved = data.get("outputs", [])

		if not isinstance(saved, list):
			problems.append("outputs: not a list")
			saved = []

		for index in range(min(self.count, len(saved))):

			entry = saved[index]

			if not isinstance(entry, dict):
				problems.append("output %d: not readable" % (index + 1))
				continue

			for name in ("ratio", "snap", "muted", "pulse", "role"):
				if name in entry:
					_attempt(problems, "output %d %s" % (index + 1, name), lambda: self.set_output(index, name, entry[name]))

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
