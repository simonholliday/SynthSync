"""Ratios: how many pulses an output sends for each pulse of the clock.

The clock sends a number of pulses on every beat, its PPQN: pulses per quarter
note. Output 1 sends the clock itself, and every other output sends a ratio of
it, so at 4 PPQN a ratio of ÷4 is one pulse a beat.

A ratio is kept as a pair of whole numbers in lowest terms. (2, 1) is two
pulses for each of the clock's, (1, 2) is one for every two of the clock's,
and (4, 3) is exactly four for every three, never 1.3333. The scheduler counts
in beats, so `effective` gives what an output sends a beat.

This runs on the Pico as well as on a laptop, so it uses whole numbers only.
"""

# The PPQNs the clock can be set to, slowest first: the rates other gear uses.
# 4 suits most step sequencers, which take a step on each pulse: a 16th note.
PPQNS = (1, 2, 4, 8, 12, 16, 24, 48)
DEFAULT_PPQN = 4

# The slowest ratio an output can run at, of the clock, whatever its PPQN.
SLOWEST = (1, 128)

# The most pulses a beat any output may send. The timing is proven at this
# rate, so the fastest ratio depends on the PPQN: `fastest` gives it.
MOST_A_BEAT = 96

# Neither half of a ratio may be larger than this. The scheduler multiplies the
# pulses by the tempo in hundredths of a BPM, and that product has to stay small
# enough for the Pico to handle without reaching for memory. Two decimal places
# can never exceed it.
LARGEST_PART = 9600

# One pulse for each of the clock's: what every output starts at.
ONE = (1, 1)

# The musical values a knob stops on when its snap switch is on, as pulses a
# beat, slowest first: halves and doubles, triplets, dotted values, and the
# sync rates other gear expects (×2, ×24 and ×48 a beat). They are the same
# notes whatever the clock's PPQN, so `snaps` gives them as ratios of the
# clock: at 4 PPQN a quarter note is ÷4 and an 8th-note triplet ×3/4.
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


def fastest (ppqn: int) -> tuple[int, int]:

	"""Give the fastest ratio an output can have when the clock sends this many pulses a beat.

	It keeps every output within 96 pulses a beat: ×24 at 4 PPQN, ×2 at 48.
	"""

	return (MOST_A_BEAT // ppqn, 1)


def effective (ratio: tuple[int, int], ppqn: int) -> tuple[int, int]:

	"""Give what an output with this ratio sends a beat, as pulses to beats in lowest terms, when the clock sends `ppqn` a beat."""

	pulses = ratio[0] * ppqn
	beats = ratio[1]
	divisor = _common(pulses, beats)

	return (pulses // divisor, beats // divisor)


def make (pulses: int, beats: int, ppqn: int = 1) -> tuple[int, int]:

	"""Build the ratio of this many pulses to this many of the clock's, in lowest terms.

	Refuses a ratio PicoClock can't play when the clock sends `ppqn` pulses a
	beat, with a message that says why.
	"""

	if pulses <= 0 or beats <= 0:
		raise ValueError("A ratio has to be more than zero")

	divisor = _common(pulses, beats)
	pulses //= divisor
	beats //= divisor

	if pulses * SLOWEST[1] < beats * SLOWEST[0]:
		raise ValueError("The slowest ratio is ÷128")

	top = fastest(ppqn)

	if compare((pulses, beats), top) > 0:

		if ppqn == 1:
			raise ValueError("The fastest ratio is ×%d" % top[0])

		raise ValueError("At %d PPQN the fastest ratio is ×%d" % (ppqn, top[0]))

	# The scheduler is given the ratio a beat, so that has to stay small enough too.
	if pulses > LARGEST_PART or beats > LARGEST_PART or effective((pulses, beats), ppqn)[0] > LARGEST_PART:
		raise ValueError("That ratio is too fine to play exactly: use at most two decimal places, or a fraction of smaller numbers")

	return (pulses, beats)


def parse (text: str, ppqn: int = 1) -> tuple[int, int]:

	"""Read a ratio as someone would type it.

	Takes a number with up to two decimal places, such as "1.5", or a fraction,
	such as "4/3". A leading × is ignored and a leading ÷ turns it over, so
	"÷3" is one pulse for every three of the clock's. The ratio has to be one
	the clock can play at `ppqn`.
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

	return make(pulses, beats, ppqn)


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


def snaps (ppqn: int = 1) -> list[tuple[int, int]]:

	"""Give the musical values as ratios of a clock of this many pulses a beat, slowest first, keeping those it can play.

	At one pulse a beat they are `SNAPS` as they stand. The fastest ratio at
	every PPQN, 96 pulses a beat, is always among them.
	"""

	found = []
	top = fastest(ppqn)

	for pulses, beats in SNAPS:

		beats *= ppqn
		divisor = _common(pulses, beats)
		snap = (pulses // divisor, beats // divisor)

		if compare(snap, SLOWEST) >= 0 and compare(snap, top) <= 0:
			found.append(snap)

	return found


def is_snap (ratio: tuple[int, int], ppqn: int = 1) -> bool:

	"""Say whether a ratio is one of the musical values the snap switch stops on, at this PPQN."""

	return ratio in snaps(ppqn)


def next_snap (ratio: tuple[int, int], ppqn: int = 1) -> tuple[int, int]:

	"""Give the first snap value faster than this ratio at this PPQN, or the fastest if there is none."""

	found = snaps(ppqn)

	for snap in found:
		if compare(snap, ratio) > 0:
			return snap

	return found[-1]


def previous_snap (ratio: tuple[int, int], ppqn: int = 1) -> tuple[int, int]:

	"""Give the first snap value slower than this ratio at this PPQN, or the slowest if there is none."""

	found = snaps(ppqn)
	slower = found[0]

	for snap in found:

		if compare(snap, ratio) >= 0:
			break

		slower = snap

	return slower


def nearest_snap (ratio: tuple[int, int], ppqn: int = 1) -> tuple[int, int]:

	"""Give the snap value closest to this ratio, of those the clock can play at this PPQN.

	Closest is judged the way it sounds, by proportion: ×3 is as far from ×2 as
	×1.33 is. A ratio exactly between two snap values goes to the slower one.
	"""

	found = snaps(ppqn)

	if compare(ratio, found[0]) <= 0:
		return found[0]

	if compare(ratio, found[-1]) >= 0:
		return found[-1]

	slower = previous_snap(ratio, ppqn)
	faster = next_snap(slower, ppqn)

	if compare(faster, ratio) == 0:
		return faster

	# The ratio is nearer the slower value when ratio / slower <= faster / ratio,
	# which is ratio squared <= slower times faster, worked in whole numbers.
	left = ratio[0] * ratio[0] * slower[1] * faster[1]
	right = slower[0] * faster[0] * ratio[1] * ratio[1]

	if left <= right:
		return slower

	return faster
