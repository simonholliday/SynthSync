"""What `config.txt` on the Pico says about how it is wired.

The file is for a musician to edit, in Thonny or any text editor. Each setting
is one line: a name, an equals sign and a value.

    # How many clock outputs are wired, from 1 to 8.
    outputs = 6

Anything after a # is a note, and blank lines are skipped. A name may be in
any capitals. The file is read once, when the Pico is switched on and before
the clock starts, because it decides how many outputs the player is made with.

Nothing in it can stop the clock starting. A missing file means every
default, and `place` makes one at power-up for the musician to edit. A line that can't be used is skipped, its setting keeps its default,
and the page says which line it was and why, in words fit to show. Of two
good lines for one setting, the first is used. A file that isn't text, or is
far too long to be this one, isn't read at all.

It describes the hardware, not the patch, so starting again as new leaves it
alone, and so do an install and a new release copied over the old one.

It holds one setting so far: `outputs`, from 1 to 8, which is 4 unless set.
Output 1 is on GP2, and each one after it on the next pin.
"""

import os

import synthsync.core.timeline

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing

# What the file is called, as it is named to a musician.
NAME = "config.txt"

DEFAULT_OUTPUTS = 4

# The longest the file may be, in bytes. The one SynthSync comes with is under
# a thousand. A file far longer is some other file, and reading it all could
# take memory the clock needs.
LONGEST = 4096

# How many of the lines that couldn't be used are named, before the rest are
# counted. Each is about a hundred bytes, and they go in every page's greeting.
MOST_TOLD = 4

# How much of a name that isn't a setting is shown back, in characters.
SHOWN = 24

# What an OSError holds when there is no such file, on either Python.
_MISSING = 2

_DIGITS = "0123456789"


class Config:

	"""What the file says, and what in it couldn't be used."""

	def __init__ (self) -> None:

		"""Begin with every default, and nothing to say."""

		self.outputs = DEFAULT_OUTPUTS
		self.problems: "list[str]" = []


def _outputs (config: Config, value: str) -> "str | None":

	"""Take how many outputs are wired, or say why it can't be taken."""

	message = "outputs has to be a whole number from 1 to %d" % synthsync.core.timeline.MOST_OUTPUTS

	if not value or len(value) > 2:
		return message

	for character in value:
		if character not in _DIGITS:
			return message

	outputs = int(value)

	if outputs < 1 or outputs > synthsync.core.timeline.MOST_OUTPUTS:
		return message

	config.outputs = outputs

	return None


# Every setting the file can hold, and what takes each one.
_SETTINGS: "dict[str, typing.Callable[[Config, str], str | None]]" = {
	"outputs": _outputs,
}


def _shown (name: str) -> str:

	"""Give a name the file holds as it can be shown back: cut short if it is long, and with anything that isn't a character to see in its place."""

	shown = ""

	for character in name[:SHOWN]:
		shown += "?" if ord(character) < 32 or ord(character) == 127 else character

	return shown + ("..." if len(name) > SHOWN else "")


def _take (config: Config, line: str, number: int, used: "dict[str, int]") -> "str | None":

	"""Take one line of the file, or say why it can't be used. `used` holds the line each setting was taken from."""

	setting = line.split("#", 1)[0].strip()

	if not setting:
		return None

	name, equals, value = setting.partition("=")
	name = name.strip().lower()

	if not equals or not name:
		return "a setting is a name, an equals sign and a value, such as outputs = 4"

	if name not in _SETTINGS:
		return "SynthSync has no setting called \"%s\"" % _shown(name)

	if name in used:
		return "%s is set already, on line %d" % (name, used[name])

	problem = _SETTINGS[name](config, value.strip())

	if problem is None:
		used[name] = number

	return problem


def parse (data: bytes) -> Config:

	"""Read what the file holds, keeping what can be used and saying what can't."""

	config = Config()

	if len(data) > LONGEST:
		config.problems.append("%s is too long to be SynthSync's settings, so none of it was used" % NAME)
		return config

	try:
		text = data.decode()

	except UnicodeError:
		text = "\x00"

	if "\x00" in text:
		config.problems.append("%s isn't plain text, so none of it was used" % NAME)
		return config

	# A file saved by Windows may begin with a mark that says it is UTF-8, and
	# its lines end with a carriage return before the new line.
	if text.startswith("\ufeff"):
		text = text[1:]

	lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
	used: "dict[str, int]" = {}
	untold = 0

	for index in range(len(lines)):

		problem = _take(config, lines[index], index + 1, used)

		if problem is None:
			continue

		if len(config.problems) < MOST_TOLD:
			config.problems.append("%s, line %d: %s" % (NAME, index + 1, problem))
		else:
			untold += 1

	if untold:
		config.problems.append("%s has %d more line%s that couldn't be used" % (NAME, untold, "" if untold == 1 else "s"))

	return config


def place (path: str) -> bool:

	"""Write the file SynthSync comes with at this path unless something is there already, and say whether it did.

	It goes under another name first, so a power cut can't leave half a file.
	Its text is loaded only here, because after the first power-up it is never
	wanted again. Anything but the path being free is left for the caller.
	"""

	try:
		os.stat(path)
		return False

	except OSError as problem:

		if not problem.args or problem.args[0] != _MISSING:
			raise

	import synthsync.core.default_config

	fresh = path + ".new"

	with open(fresh, "wb") as handle:
		handle.write(synthsync.core.default_config.TEXT)

	os.rename(fresh, path)

	return True


def read (path: str) -> Config:

	"""Read the file at this path, as the Pico does when it is switched on.

	A missing file means every default, with nothing to say, because a new
	Pico may not have one.
	"""

	try:

		with open(path, "rb") as handle:
			data = handle.read(LONGEST + 1)

	except OSError as problem:

		config = Config()

		if not problem.args or problem.args[0] != _MISSING:
			config.problems.append("%s couldn't be read, so none of it was used" % NAME)

		return config

	return parse(data)
