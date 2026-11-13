"""Ratios: how many pulses an output sends for each beat.

A ratio is kept as a pair of whole numbers, pulses and beats, in lowest terms.
(2, 1) is two pulses a beat, (1, 2) is one pulse every two beats, and (4, 3) is
exactly four pulses every three beats, never 1.3333.

This runs on the Pico as well as on a laptop, so it uses whole numbers only.
"""

# The slowest and fastest ratios an output can run at.
SLOWEST = (1, 128)
FASTEST = (96, 1)

# Neither half of a ratio may be larger than this. The scheduler multiplies the
# pulses by the tempo in hundredths of a BPM, and that product has to stay small
# enough for the Pico to handle without reaching for memory. Two decimal places
# can never exceed it.
LARGEST_PART = 9600

# One pulse a beat: what every output starts at.
ONE = (1, 1)

# The musical values a knob stops on when its snap switch is on, slowest first:
# halves and doubles, triplets, dotted values, and the common sync formats.
SNAPS = (
	(1, 64), (1, 32), (1, 16), (1, 12), (1, 8), (1, 6), (1, 4), (1, 3), (1, 2), (2, 3),
	(1, 1),
	(4, 3), (3, 2), (2, 1), (8, 3), (3, 1), (4, 1), (16, 3), (6, 1), (8, 1),
	(12, 1), (16, 1), (24, 1), (32, 1), (48, 1), (64, 1), (96, 1),
)

_DIGITS = "0123456789"
_HOW_TO_TYPE = "Type a ratio as a number such as 1.5, or as a fraction such as 4/3"


def _common (first: int, second: int) -> int:

	"""Find the largest whole number that divides both."""

	while second:
		first, second = second, first % second

	return first


def _whole (text: str) -> int:

	"""Read a whole number written in plain digits, and refuse anything else."""

	if not text or len(text) > 9:
		raise ValueError(_HOW_TO_TYPE)

	for character in text:
		if character not in _DIGITS:
			raise ValueError(_HOW_TO_TYPE)

	return int(text)


def _decimal (text: str) -> tuple[int, int]:

	"""Read a number with up to two decimal places as a pair of whole numbers."""

	if text.count(".") > 1:
		raise ValueError(_HOW_TO_TYPE)

	whole, _, places = text.partition(".")

	if not whole and not places:
		raise ValueError(_HOW_TO_TYPE)

	if len(places) > 2:
		raise ValueError("A ratio takes at most two decimal places")

	scale = 10 ** len(places)
	top = (_whole(whole) if whole else 0) * scale + (_whole(places) if places else 0)

	return top, scale


def _short_decimal (top: int, bottom: int) -> str:

	"""Write top / bottom as a decimal if it comes out even within two places, else nothing."""

	if (top * 100) % bottom:
		return ""

	hundredths = top * 100 // bottom
	whole = hundredths // 100
	places = hundredths % 100

	if places == 0:
		return "%d" % whole

	if places % 10 == 0:
		return "%d.%d" % (whole, places // 10)

	return "%d.%02d" % (whole, places)


def make (pulses: int, beats: int) -> tuple[int, int]:

	"""Build the ratio of this many pulses to this many beats, in lowest terms.

	Refuses a ratio PicoClock can't play, with a message that says why.
	"""

	if pulses <= 0 or beats <= 0:
		raise ValueError("A ratio has to be more than zero")

	divisor = _common(pulses, beats)
	pulses //= divisor
	beats //= divisor

	if pulses * SLOWEST[1] < beats * SLOWEST[0]:
		raise ValueError("The slowest ratio is ÷128")

	if pulses * FASTEST[1] > beats * FASTEST[0]:
		raise ValueError("The fastest ratio is ×96")

	if pulses > LARGEST_PART or beats > LARGEST_PART:
		raise ValueError("That ratio is too fine to play exactly: use at most two decimal places, or a fraction of smaller numbers")

	return (pulses, beats)


def parse (text: str) -> tuple[int, int]:

	"""Read a ratio as someone would type it.

	Takes a number with up to two decimal places, such as "1.5", or a fraction,
	such as "4/3". A leading × is ignored and a leading ÷ turns it over, so
	"÷3" is one pulse every three beats.
	"""

	cleaned = text.strip().replace(" ", "")
	divide = False

	if cleaned[:1] in ("×", "x", "X", "*"):
		cleaned = cleaned[1:]

	elif cleaned[:1] in ("÷", "/"):
		cleaned = cleaned[1:]
		divide = True

	if "/" in cleaned:
		top, _, bottom = cleaned.partition("/")
		pulses, beats = _whole(top), _whole(bottom)

	else:
		pulses, beats = _decimal(cleaned)

	if divide:
		pulses, beats = beats, pulses

	return make(pulses, beats)


def to_text (ratio: tuple[int, int]) -> str:

	"""Write a ratio in the plain form that parse reads back exactly: "3/2", or "2"."""

	pulses, beats = ratio

	if beats == 1:
		return "%d" % pulses

	return "%d/%d" % (pulses, beats)


def label (ratio: tuple[int, int]) -> str:

	"""Write a ratio the way the page shows it: ×2, ÷3, ×1.5, ÷1.5, ×4/3."""

	pulses, beats = ratio

	if beats == 1:
		return "×%d" % pulses

	if pulses == 1:
		return "÷%d" % beats

	if pulses < beats:
		# Slower than the beat reads best as a division when that comes out even.
		turned_over = _short_decimal(beats, pulses)

		if turned_over:
			return "÷" + turned_over

	decimal = _short_decimal(pulses, beats)

	if decimal:
		return "×" + decimal

	return "×%d/%d" % (pulses, beats)


def compare (first: tuple[int, int], second: tuple[int, int]) -> int:

	"""Say whether the first ratio is slower (-1), the same (0) or faster (1) than the second."""

	left = first[0] * second[1]
	right = second[0] * first[1]

	if left < right:
		return -1

	if left > right:
		return 1

	return 0


def is_snap (ratio: tuple[int, int]) -> bool:

	"""Say whether a ratio is one of the musical values the snap switch stops on."""

	return ratio in SNAPS


def next_snap (ratio: tuple[int, int]) -> tuple[int, int]:

	"""Give the first snap value faster than this ratio, or the fastest if there is none."""

	for snap in SNAPS:
		if compare(snap, ratio) > 0:
			return snap

	return SNAPS[-1]


def previous_snap (ratio: tuple[int, int]) -> tuple[int, int]:

	"""Give the first snap value slower than this ratio, or the slowest if there is none."""

	found = SNAPS[0]

	for snap in SNAPS:

		if compare(snap, ratio) >= 0:
			break

		found = snap

	return found


def nearest_snap (ratio: tuple[int, int]) -> tuple[int, int]:

	"""Give the snap value closest to this ratio.

	Closest is judged the way it sounds, by proportion: ×3 is as far from ×2 as
	×1.33 is. A ratio exactly between two snap values goes to the slower one.
	"""

	if compare(ratio, SNAPS[0]) <= 0:
		return SNAPS[0]

	if compare(ratio, SNAPS[-1]) >= 0:
		return SNAPS[-1]

	slower = previous_snap(ratio)
	faster = next_snap(slower)

	if compare(faster, ratio) == 0:
		return faster

	# The ratio is nearer the slower value when ratio / slower <= faster / ratio,
	# which is ratio squared <= slower times faster, worked in whole numbers.
	left = ratio[0] * ratio[0] * slower[1] * faster[1]
	right = slower[0] * faster[0] * ratio[1] * ratio[1]

	if left <= right:
		return slower

	return faster
