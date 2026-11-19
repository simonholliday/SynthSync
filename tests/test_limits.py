"""The widest tempo range never takes the scheduler's numbers past what the Pico can hold without asking for memory."""

import ast
import inspect
import random

import pytest

import synthsync.core.limits
import synthsync.core.ratio
import synthsync.core.timeline

SMALL = 2 ** 30
WIDE = synthsync.core.limits.Limits(100, 100000)


class Watched (synthsync.core.timeline.Timeline):

	"""A timeline that checks what its long division is promised, each time."""

	def _muldiv (self, count: int, each: int, extra: int, divisor: int) -> None:

		"""Hold every call to what `_muldiv` says it needs, and then to its answer being small."""

		assert 0 <= count < SMALL
		assert 2 * divisor + each < SMALL
		assert divisor + extra < SMALL

		super()._muldiv(count, each, extra, divisor)

		assert 0 <= self._answer[0] < SMALL


def small (timeline: Watched) -> None:

	"""Check that everything the scheduler keeps per slot is small, with room to add one gap."""

	for name in ("period", "period_part", "divisor", "until", "until_part", "held", "fall"):

		for value in getattr(timeline, name):
			assert -SMALL < value < SMALL, name

	for slot in range(timeline.slots):

		assert timeline.until[slot] + timeline.period[slot] < SMALL
		assert timeline.until_part[slot] + timeline.period_part[slot] < SMALL


def ratio_within (shake: random.Random, ppqn: int) -> tuple[int, int]:

	"""Pick a ratio of the clock the widest range allows, often at one of its extremes, and give what it sends a beat."""

	top = synthsync.core.ratio.fastest(ppqn, WIDE)
	bottom = synthsync.core.ratio.slowest(ppqn, WIDE)
	choice = shake.choice([top, bottom, (1, 1)] + synthsync.core.ratio.snaps(ppqn, WIDE))

	if shake.random() < 0.3:

		beats = shake.randint(1, 100)
		pulses = shake.randint(1, 200)

		try:
			choice = synthsync.core.ratio.make(pulses, beats, ppqn, WIDE)

		except ValueError:
			pass

	return synthsync.core.ratio.effective(choice, ppqn)


@pytest.mark.parametrize("seed", range(6))
def test_nothing_grows_too_large_anywhere_in_the_widest_range (seed: int) -> None:

	"""Through jumps between 1 and 1000 BPM and every kind of ratio, with MIDI going, every number stays small."""

	shake = random.Random(seed)
	ppqn = shake.choice(synthsync.core.ratio.ppqns(WIDE))
	timeline = Watched(8, limits=WIDE)

	timeline.set_midi(synthsync.core.timeline.MIDI_ALWAYS)
	timeline.start()

	for _ in range(250):

		what = shake.random()

		if what < 0.45:
			timeline.set_tempo(shake.choice([100, 100000, 101, 99999, shake.randint(100, 100000)]))
		elif what < 0.9:
			timeline.set_ratio(shake.randrange(8), *ratio_within(shake, ppqn))
		elif what < 0.95:
			timeline.pause()
			timeline.resume()

		for _ in range(shake.randint(1, 30)):
			assert 0 <= timeline.next_word() < SMALL

		small(timeline)


def test_the_largest_ratios_of_the_widest_range_survive_a_change_of_tempo () -> None:

	"""The finest ratio it allows, at the fastest tempo, is the hardest case for the division."""

	largest = WIDE.largest_part
	timeline = Watched(2, limits=WIDE)

	timeline.set_tempo(100000)
	timeline.set_ratio(0, largest, largest - 1)
	timeline.set_ratio(1, largest - 2, largest)
	timeline.start()

	for _ in range(50):
		timeline.next_word()

	timeline.set_tempo(99999)
	timeline.set_tempo(100000)

	small(timeline)


def looked_up_every_word (statements: list[ast.stmt]) -> set[str]:

	"""Find the attributes of `self` and the capitalised module names that code reaches whatever its `if`s decide."""

	found: set[str] = set()
	waiting: list[ast.AST] = list(statements)

	while waiting:

		node = waiting.pop()

		if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
			found.add(node.attr)
		elif isinstance(node, ast.Name) and node.id.isupper():
			found.add(node.id)

		# What an `if` guards is reached only sometimes, but its test every time.
		waiting.extend([node.test] if isinstance(node, ast.If) else ast.iter_child_nodes(node))

	return found


def test_every_word_looks_up_only_a_few_names () -> None:

	"""Each name looked up costs every word time, and more when it shares an entry in MicroPython's lookup cache.

	Which names share one depends on what else is loaded, so the fewer a word
	needs, the less the player's margin at its busiest depends on that. Before
	these were gathered into `parts`, loading more code took the player from
	four words to spare to none. A change here is timed on the board first.
	"""

	source = ast.parse(inspect.getsource(synthsync.core.timeline))
	timeline = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "Timeline")
	next_word = next(node for node in timeline.body if isinstance(node, ast.FunctionDef) and node.name == "next_word")

	assert looked_up_every_word(next_word.body) == {"parts", "mask", "led_fall", "midi_due", "midi_on"}


def test_what_every_word_takes_at_once_stays_what_the_timeline_holds () -> None:

	"""Nothing may swap one of these for another: `parts` would go on holding the old one."""

	timeline = synthsync.core.timeline.Timeline(4, 150, 4, 10)
	until, until_part, fall, period, period_part, divisor, width, muted, bit, slots, midi_slot, cycles_per_tick, overhead_cycles, pin_bits, hold, led_bit = timeline.parts

	timeline.set_tempo(14000)
	timeline.set_ratio(0, 3, 2)
	timeline.start()

	for _ in range(50):
		timeline.next_word()

	timeline.stop()

	for held, name in ((until, "until"), (until_part, "until_part"), (fall, "fall"), (period, "period"), (period_part, "period_part"), (divisor, "divisor"), (width, "width"), (muted, "muted"), (bit, "bit")):
		assert held is getattr(timeline, name), name

	assert (slots, midi_slot, cycles_per_tick, overhead_cycles, pin_bits) == (timeline.slots, timeline.midi_slot, 150, 4, 10)
	assert (hold, led_bit) == (synthsync.core.timeline.HOLD_LIMIT, synthsync.core.timeline.LED_BIT)


def test_the_usual_range_is_the_same_in_the_scheduler_and_the_limits () -> None:

	"""The scheduler keeps its own copy."""

	usual = synthsync.core.limits.DEFAULT

	assert (synthsync.core.timeline.SLOWEST_TEMPO, synthsync.core.timeline.FASTEST_TEMPO) == (usual.slowest_tempo, usual.fastest_tempo)
	assert list(synthsync.core.timeline.Timeline(1)._answer)[2:] == list(synthsync.core.timeline.Timeline(1, limits=usual)._answer)[2:]
	assert list(synthsync.core.timeline.Timeline(1)._answer)[2:] == [usual.slowest_tempo, usual.fastest_tempo, usual.most_beats, usual.most_a_beat, usual.largest_part]
