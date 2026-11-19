"""The second core's word maker beside `Timeline.next_word`: the same random changes to two timelines, and every word and every number compared.

`compare` runs on any MicroPython: the laptop's tests run it under the pinned
release, where viper becomes x86 code, and the rig's `word` test runs it on the
Pico, where it becomes the Arm code the player uses. Its changes come from a
generator of its own, so a seed means the same changes on both.
"""

import sys
import time

import synthsync.core.limits
import synthsync.core.timeline
import synthsync.device.word

# How many seeds and changes the rig's test makes, and how many words it times each way.
_SEEDS = 40
_STEPS = 300
_TIMED = 5000


class Shake:

	"""A small generator of numbers that gives the same ones on every Python."""

	def __init__ (self, seed: int) -> None:

		"""Begin from this seed."""

		self.state = (seed * 2654435761 + 12345) & 0xFFFFFFFF or 1

	def below (self, count: int) -> int:

		"""Give a whole number from 0 to one less than `count`."""

		state = self.state
		state ^= (state << 13) & 0xFFFFFFFF
		state ^= state >> 17
		state ^= (state << 5) & 0xFFFFFFFF
		self.state = state

		return state % count

	def choice (self, items: tuple) -> object:

		"""Give one of these."""

		return items[self.below(len(items))]

	def between (self, low: int, high: int) -> int:

		"""Give a whole number from `low` to `high`."""

		return low + self.below(high - low + 1)


def _attempt (method, arguments: tuple) -> tuple:

	"""Call a method and give what it returned, or that it refused."""

	try:
		return ("ok", method(*arguments))
	except ValueError:
		return ("refused",)


def _change (shake: Shake, outputs: int, slowest: int, fastest: int) -> tuple:

	"""Choose a change, as the page, the buttons and power-up make them, often at the edges of what is allowed."""

	what = shake.below(14)
	output = shake.below(outputs)

	if what == 0:
		return ("set_tempo", shake.choice((shake.between(slowest, fastest), slowest - 1, fastest + 1, fastest, slowest)))

	if what in (1, 2, 3):
		return ("set_ratio", output, shake.choice((96, 95, 94, 93, 1, shake.between(1, 96), 97, 28, 7)), shake.choice((1, 1, 2, 3, shake.between(1, 128), 129, 6)))

	if what == 4:
		return ("set_mute", output, shake.below(2) == 0)

	if what == 5:
		return ("set_width", output, shake.choice((0, 1, 2, 1000, 5000, shake.between(1, 20000))))

	if what == 6:
		return ("set_role", output, shake.below(4))

	if what == 7:
		return ("set_midi", shake.below(3))

	if what == 8:
		return ("start",)

	if what == 9:
		return ("pause",)

	if what == 10:
		return ("resume",)

	if what == 11:
		return ("stop",)

	if what == 12:
		return ("press", shake.between(1, 5))

	return ("position",)


def _same (first, second) -> bool:

	"""Say whether two timelines hold the same numbers everywhere a word or a change can reach."""

	if bytes(first.table) != bytes(second.table) or bytes(first.held) != bytes(second.held):
		return False

	for name in ("tempo", "running", "paused", "owed", "changes"):
		if getattr(first, name) != getattr(second, name):
			return False

	return True


def compare (seeds: int, steps: int) -> int:

	"""Drive pairs of timelines through the same changes, one making its words with `next_word` and the other with the word maker, and stop at the first difference. Gives how many words were compared.

	Every third seed uses the widest tempo range there is.
	"""

	words = 0
	changes = 0
	widest = synthsync.core.limits.Limits(synthsync.core.limits.SLOWEST_TEMPO, synthsync.core.limits.FASTEST_TEMPO)
	make = synthsync.device.word.next_word

	for seed in range(seeds):

		shake = Shake(seed)
		outputs = shake.choice((1, 2, 4, 8))
		limits = widest if seed % 3 == 0 else None
		slowest = widest.slowest_tempo if limits else synthsync.core.timeline.SLOWEST_TEMPO
		fastest = widest.fastest_tempo if limits else synthsync.core.timeline.FASTEST_TEMPO
		first = synthsync.core.timeline.Timeline(outputs, limits=limits)
		second = synthsync.core.timeline.Timeline(outputs, limits=limits)

		for step in range(steps):

			call = _change(shake, outputs, slowest, fastest)
			answers = (_attempt(getattr(first, call[0]), call[1:]), _attempt(getattr(second, call[0]), call[1:]))
			changes += 1

			if answers[0] != answers[1]:
				raise AssertionError("seed %d step %d: %r answered %r and %r" % (seed, step, call, answers[0], answers[1]))

			for _ in range(shake.choice((1, 3, 10, 60))):

				one = first.next_word()
				two = make(second.table)
				words += 1

				if one != two:
					raise AssertionError("seed %d step %d after %r: next_word gave %d and the word maker %d" % (seed, step, call, one, two))

			if not _same(first, second):
				raise AssertionError("seed %d step %d after %r: the timelines' numbers differ" % (seed, step, call))

	print("WORD the same through %d changes and %d words, %d seeds, on %s %s" % (changes, words, seeds, sys.implementation.name, ".".join(str(part) for part in sys.implementation.version[:3])))

	return words


def timed (outputs: int) -> tuple:

	"""Time `next_word` and the word maker on this many outputs crowded at 96 a beat and down, at 300 BPM with MIDI going, in microseconds a word."""

	timelines = []

	for _ in range(2):

		timeline = synthsync.core.timeline.Timeline(outputs)
		timeline.set_tempo(30000)

		for output in range(outputs):
			timeline.set_ratio(output, 96 - output, 1)
			timeline.set_width(output, 1000)

		timeline.set_midi(synthsync.core.timeline.MIDI_ALWAYS)
		timeline.start()
		timelines.append(timeline)

	first = timelines[0].next_word
	make = synthsync.device.word.next_word
	table = timelines[1].table
	ticks_us = time.ticks_us
	began = ticks_us()

	for _ in range(_TIMED):
		first()

	slow = time.ticks_diff(ticks_us(), began)
	began = ticks_us()

	for _ in range(_TIMED):
		make(table)

	quick = time.ticks_diff(ticks_us(), began)

	return slow / _TIMED, quick / _TIMED


def run (load: str = "idle") -> None:

	"""The rig's test: the word maker must give every word `next_word` gives, here on the Pico, and be quicker."""

	import rig

	passed = True
	words = 0

	try:
		words = compare(_SEEDS, _STEPS)
	except AssertionError as problem:
		print(problem)
		passed = False

	four = timed(4)
	eight = timed(8)
	passed = passed and eight[1] * 4 < eight[0]

	rig.report("word", passed, "words=%d next_word_us_4=%.1f maker_us_4=%.1f next_word_us_8=%.1f maker_us_8=%.1f load=%s" % (words, four[0], four[1], eight[0], eight[1], load))
