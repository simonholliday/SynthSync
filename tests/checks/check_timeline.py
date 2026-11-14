"""The scheduler puts every pulse exactly where the maths says, on either Python.

Time and position are checked with whole numbers only, so the same checks run
on the Pico's Python. Position is counted in units of 1 / UNITS of a beat, in
which one tick at a tempo of t hundredths of a BPM is exactly t units.
"""

import gc
import sys

import picoclock.core.timeline

UNITS = 6000 * 1000000


class Player:

	"""Plays a timeline's words and notes when each output's pin rises and falls."""

	def __init__ (self, timeline: picoclock.core.timeline.Timeline) -> None:

		"""Start with every pin low, at tick zero and position zero."""

		self.timeline = timeline
		self.tick = 0
		self.position = 0
		self.pins = 0
		self.largest = 0

		# Ticks still to pass before the beat moves on: the lead after a start or a resume.
		self.lead = 0
		self.rises: list[list[int]] = [[] for _ in range(timeline.outputs)]
		self.falls: list[list[int]] = [[] for _ in range(timeline.outputs)]
		self.rise_positions: list[list[int]] = [[] for _ in range(timeline.outputs)]

	def play (self, words: int) -> None:

		"""Take this many words, moving time on by what each one holds."""

		timeline = self.timeline

		for _ in range(words):

			word = timeline.next_word()
			pins, ticks = timeline.unpack(word)

			assert ticks >= 1
			assert ticks <= picoclock.core.timeline.HOLD_LIMIT
			assert 0 <= word < 2 ** 30

			if word > self.largest:
				self.largest = word

			for output in range(timeline.outputs):

				now = (pins >> output) & 1
				before = (self.pins >> output) & 1

				if now and not before:
					self.rises[output].append(self.tick)
					self.rise_positions[output].append(self.position)

				if before and not now:
					self.falls[output].append(self.tick)

			self.pins = pins
			self.tick += ticks

			# The beat stands still through a pause and through a lead.
			if timeline.paused:
				continue

			waited = min(self.lead, ticks)
			self.lead -= waited
			self.position += (ticks - waited) * timeline.tempo

	def play_until (self, tick: int) -> None:

		"""Take words until this tick has been reached."""

		while self.tick < tick:
			self.play(1)

	def play_for (self, ticks: int) -> None:

		"""Take words until this many more ticks have passed."""

		self.play_until(self.tick + ticks)


def on_grid (position: int, tempo: int, pulses: int, beats: int) -> int:

	"""Give the number of the grid line a pulse is on, or -1 if it isn't on one.

	A pulse at this position is on its grid when a whole multiple of
	beats / pulses lies at or after it and less than one tick later.
	"""

	line = beats * UNITS
	number = -((-position * pulses) // line)

	if number * line < (position + tempo) * pulses:
		return number

	return -1


class Follower:

	"""Checks that every pulse played is the next line of its output's grid.

	A pulse must sit within one tick of a grid line, counted from zero. A line
	may be passed over without an edge only when the scheduler itself counts a
	pulse as having run into the one before, and that is added up to compare.
	"""

	def __init__ (self, timeline: picoclock.core.timeline.Timeline, player: Player, ratios: list[tuple[int, int]]) -> None:

		"""Begin expecting line zero on every output."""

		self.timeline = timeline
		self.player = player
		self.ratios = ratios
		self.seen = [0] * len(ratios)
		self.expected = [0] * len(ratios)
		self.watched = [True] * len(ratios)
		self.skipped = 0

	def follow (self) -> None:

		"""Check the pulses played since the last call. Call it before changing anything."""

		for output in range(len(self.ratios)):

			positions = self.player.rise_positions[output]

			if not self.watched[output]:
				# Not a clock just now, so its pin isn't on any grid.
				self.seen[output] = len(positions)
				continue

			while self.seen[output] < len(positions):

				line = on_grid(positions[self.seen[output]], self.timeline.tempo, self.ratios[output][0], self.ratios[output][1])

				assert line >= self.expected[output], (output, line, self.expected[output])

				self.skipped += line - self.expected[output]
				self.expected[output] = line + 1
				self.seen[output] += 1

	def ignore (self, output: int) -> None:

		"""Note that an output has stopped being a clock, so its pin is no longer on a grid."""

		self.watched[output] = False

	def moved (self, output: int) -> None:

		"""Note that an output has just joined a grid afresh: a new ratio, an unmute, or becoming a clock."""

		self.seen[output] = len(self.player.rise_positions[output])
		self.watched[output] = True

		line = self.ratios[output][1] * UNITS
		self.expected[output] = -((-self.player.position * self.ratios[output][0]) // line)


def make (ratios: list[tuple[int, int]], tempo: int) -> picoclock.core.timeline.Timeline:

	"""Make a timeline with these ratios at this tempo, not yet started."""

	timeline = picoclock.core.timeline.Timeline(len(ratios))
	timeline.set_tempo(tempo)

	for output in range(len(ratios)):
		timeline.set_ratio(output, ratios[output][0], ratios[output][1])

	return timeline


def state (timeline: picoclock.core.timeline.Timeline) -> list[int]:

	"""Copy the time to every output's next pulse, whole ticks and parts."""

	return list(timeline.until) + list(timeline.until_part)


class Shuffle:

	"""Gives the same run of scattered numbers on every Python, with no random module."""

	def __init__ (self, seed: int) -> None:

		"""Begin from a seed."""

		self.value = seed

	def below (self, limit: int) -> int:

		"""Give the next number, from 0 up to but not including the limit."""

		self.value = (self.value * 1103515245 + 12345) % 2147483648

		return (self.value >> 8) % limit


def check_every_pulse_is_on_the_exact_tick_at_a_steady_tempo () -> None:

	"""Pulse number j of an output rises on exactly the tick the maths gives, with nothing carried forward."""

	ratios = [(1, 1), (2, 1), (3, 1), (1, 4), (3, 2), (137, 100), (24, 1), (4, 3)]
	tempo = 12345
	timeline = make(ratios, tempo)
	player = Player(timeline)

	timeline.start()
	player.play(20000)

	for output in range(len(ratios)):

		pulses, beats = ratios[output]
		rises = player.rises[output]

		assert len(rises) > 5

		for number in range(len(rises)):
			assert rises[number] == (number * beats * UNITS) // (pulses * tempo)


def check_outputs_that_should_line_up_share_a_word () -> None:

	"""Every beat of the main clock is the very same tick on the outputs that divide it."""

	timeline = make([(1, 1), (2, 1), (3, 1), (24, 1)], 13370)
	player = Player(timeline)

	timeline.start()
	player.play(8000)

	assert len(player.rises[0]) > 20

	for output in (1, 2, 3):

		ticks = set(player.rises[output])

		for tick in player.rises[0]:
			assert tick in ticks


def check_a_pulse_lasts_ten_milliseconds_or_half_the_gap () -> None:

	"""A pulse is 10 ms long, shortened to half the gap when an output runs too fast to fit it."""

	timeline = make([(1, 1), (96, 1)], 30000)
	player = Player(timeline)

	timeline.start()
	player.play(4000)

	assert player.falls[0][0] - player.rises[0][0] == 10000

	# At 300 BPM, 96 pulses a beat are 2083 ticks apart, so each lasts 1041.
	assert player.falls[1][0] - player.rises[1][0] == 1041
	assert player.rises[1][1] - player.rises[1][0] in (2083, 2084)


def check_half_the_gap_can_be_asked_for () -> None:

	"""An output set to half stays high for half of each gap."""

	timeline = make([(1, 1), (2, 1)], 12000)
	timeline.width[1] = 0
	player = Player(timeline)

	timeline.start()
	player.play(400)

	assert player.falls[1][0] - player.rises[1][0] == 125000


def check_a_tempo_change_keeps_every_output_in_place () -> None:

	"""After many tempo changes each output is still exactly where the beat says it should be."""

	ratios = [(1, 1), (3, 1), (2, 3), (137, 100), (16, 3)]
	timeline = make(ratios, 12000)
	player = Player(timeline)
	shuffle = Shuffle(5)

	timeline.start()

	for _ in range(300):

		player.play(1 + shuffle.below(40))
		timeline.set_tempo(2000 + shuffle.below(28001))

		# Working an output's place out afresh from the beat must change nothing.
		before = state(timeline)

		for output in range(len(ratios)):
			timeline._place(output)

		assert state(timeline) == before

	for output in range(len(ratios)):
		assert len(player.rises[output]) > 3


def check_pulses_stay_on_their_grid_through_tempo_and_ratio_changes () -> None:

	"""Whatever changes, each pulse is on its own grid from zero, and none is skipped or doubled."""

	ratios = [(1, 1), (2, 1), (3, 2), (1, 2)]
	choices = [(1, 1), (2, 1), (3, 1), (4, 3), (2, 3), (1, 4), (137, 100), (24, 1), (33, 100), (96, 1), (1, 128), (9599, 100)]
	timeline = make(ratios, 12000)
	player = Player(timeline)
	shuffle = Shuffle(9)
	follower = Follower(timeline, player, ratios)

	timeline.start()

	for step in range(400):

		player.play(1 + shuffle.below(60))
		follower.follow()

		if step % 3 == 0:
			timeline.set_tempo(2000 + shuffle.below(28001))

		if step % 5 == 0:

			output = 1 + shuffle.below(len(ratios) - 1)
			ratios[output] = choices[shuffle.below(len(choices))]
			timeline.set_ratio(output, ratios[output][0], ratios[output][1])
			follower.moved(output)

	assert follower.seen[0] > 3
	assert sum(follower.seen) > 100

	# A line with no edge is only ever one the scheduler says ran into the pulse before it.
	assert follower.skipped == timeline.merged


def check_a_pulse_is_cut_short_when_a_tempo_jump_brings_the_next_one_forward () -> None:

	"""A big jump in tempo while a pin is high still leaves an edge for the next pulse."""

	# 24 pulses a beat at 100 BPM are 25 ms apart, each 10 ms long, and the first
	# word ends a quarter of the way through that pulse.
	timeline = make([(24, 1), (96, 1)], 10000)
	player = Player(timeline)

	timeline.start()
	player.play(1)

	assert player.tick == 2500
	assert player.pins & 1

	# Tripling the tempo puts the next pulse about 7 ms away, inside what's left of the 10 ms pulse.
	timeline.set_tempo(30000)
	player.play(60)

	assert timeline.merged == 0
	assert len(player.falls[0]) >= 2

	high = player.falls[0][0] - player.rises[0][0]
	low = player.rises[0][1] - player.falls[0][0]

	# The pulse was cut to leave about as long low as it stayed high after the change.
	assert high < 10000
	assert low > 3000

	# And the pulse after it is exactly where the new tempo puts it.
	assert on_grid(player.rise_positions[0][1], 30000, 24, 1) == 1


def check_stopping_sets_every_output_low_and_starting_begins_again () -> None:

	"""Stop leaves nothing playing, and the next start pulses every output together at once."""

	timeline = make([(1, 1), (3, 1)], 12000)
	player = Player(timeline)

	assert timeline.quiet()
	assert timeline.unpack(timeline.next_word())[0] == 0

	timeline.start()
	assert not timeline.quiet()
	assert timeline.unpack(timeline.next_word())[0] == 3

	timeline.stop()
	assert timeline.quiet()
	assert timeline.unpack(timeline.next_word())[0] == 0
	assert timeline.beat == 0

	timeline.start()
	player.play(50)

	assert player.rises[0][0] == 0
	assert player.rises[1][0] == 0


def check_changes_while_stopped_take_hold_at_the_next_start () -> None:

	"""A tempo or ratio set while stopped is what plays when the clock starts."""

	timeline = make([(1, 1), (1, 1)], 12000)
	player = Player(timeline)

	timeline.set_tempo(6000)
	timeline.set_ratio(1, 4, 1)
	timeline.start()
	player.play(600)

	assert player.rises[0][1] == 1000000
	assert player.rises[1][1] == 250000


def check_position_says_how_far_the_clock_has_played () -> None:

	"""The position, in thousandths of a beat, keeps to the exact one through any number of tempo changes."""

	timeline = make([(1, 1), (3, 1), (4, 3)], 12000)
	player = Player(timeline)
	shuffle = Shuffle(7)

	assert timeline.position() == -1

	timeline.start()

	# No word has been played yet, so no beat has begun.
	assert timeline.position() == -1

	player.play_until(625000)

	assert player.tick == 625000
	assert timeline.position() == 1250

	for _ in range(300):

		timeline.set_tempo(2000 + shuffle.below(28001))
		player.play(1 + shuffle.below(60))

		exact = player.position * 1000 // UNITS

		assert abs(timeline.position() - exact) <= 1, (timeline.position(), exact)

	assert timeline.position() > 3000

	timeline.stop()

	assert timeline.position() == -1


def refused (timeline: picoclock.core.timeline.Timeline, what: str, first: int, second: int, third: int) -> bool:

	"""Try one change and say whether it was refused."""

	try:
		if what == "tempo":
			timeline.set_tempo(first)
		else:
			timeline.set_ratio(first, second, third)

	except ValueError:
		return True

	return False


def check_values_out_of_range_are_refused () -> None:

	"""A tempo or ratio the clock can't play is refused rather than played wrongly."""

	timeline = make([(1, 1), (1, 1)], 12000)

	assert refused(timeline, "tempo", 1999, 0, 0)
	assert refused(timeline, "tempo", 30001, 0, 0)
	assert refused(timeline, "ratio", 2, 1, 1)
	assert refused(timeline, "ratio", -1, 1, 1)
	assert refused(timeline, "ratio", 1, 0, 1)
	assert refused(timeline, "ratio", 1, 1, 129)
	assert refused(timeline, "ratio", 1, 97, 1)
	assert refused(timeline, "ratio", 1, 9601, 101)
	assert not refused(timeline, "ratio", 1, 3, 2)

	assert timeline.tempo == 12000


def check_working_out_a_word_asks_for_no_memory () -> None:

	"""On the Pico's Python, playing words and changing tempo and ratio leave the heap untouched."""

	if sys.implementation.name != "micropython":
		return

	timeline = make([(1, 1), (24, 1), (4, 3), (137, 100)], 12000)
	timeline.start()

	next_word = timeline.next_word

	for _ in range(50):
		next_word()

	# Only MicroPython's gc can say how much of the heap is in use.
	used = getattr(gc, "mem_alloc")

	gc.collect()
	before = used()

	for _ in range(3000):
		next_word()

	after_words = used()

	timeline.set_tempo(9999)
	timeline.set_ratio(2, 3, 2)
	timeline.set_ratio(3, 1, 4)

	for _ in range(500):
		next_word()

	after_changes = used()

	assert after_words == before, after_words - before
	assert after_changes == before, after_changes - before
