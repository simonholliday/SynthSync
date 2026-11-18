"""MIDI out, read back off its pin as a receiver would read it, on the Pico's Python as on a laptop's."""

import synthsync.core.timeline

import tests.checks.check_timeline

# MIDI's own numbers, written out here so that the scheduler is held to them:
# a bit lasts 32 microseconds, a byte ten bits, and at least a millisecond comes
# between Start or Continue and the next clock.
BIT = 32
BYTE = 320
GAP = 1000
OFF = synthsync.core.timeline.MIDI_OFF
PLAYING = synthsync.core.timeline.MIDI_PLAYING
ALWAYS = synthsync.core.timeline.MIDI_ALWAYS
CLOCK = 0xF8
START = 0xFA
CONTINUE = 0xFB
STOP = 0xFC

# A beat at 120 BPM, and the gap between its MIDI clocks.
BEAT = 500000
TICK_GAP = BEAT // 24


class Line (tests.checks.check_timeline.Player):

	"""Plays a timeline's words and also notes each change of MIDI's pin."""

	def __init__ (self, timeline: synthsync.core.timeline.Timeline) -> None:

		"""Start at tick zero with the pin at rest."""

		super().__init__(timeline)

		self.edges: list[int] = []
		self.longest = 0

	def play (self, words: int) -> None:

		"""Take this many words, noting when each changes MIDI's pin and how long the longest held."""

		for _ in range(words):

			before = self.pins
			at = self.tick

			super().play(1)

			if self.tick - at > self.longest:
				self.longest = self.tick - at

			if (before ^ self.pins) & synthsync.core.timeline.MIDI_BIT:
				self.edges.append(at)

	def high_at (self, tick: int) -> bool:

		"""Say whether the pin was high at this tick."""

		count = 0

		for edge in self.edges:
			if edge <= tick:
				count += 1

		return count % 2 == 1

	def received (self) -> list[tuple[int, int]]:

		"""Read everything sent, as each byte's first tick and its value, holding every byte to MIDI's framing.

		The pin is inverted, so it rests low and a start bit is high. Each byte
		has to change only on its own bit boundaries, end with a stop bit, and
		be over before the next begins. A byte still going out isn't counted.
		"""

		found = []
		edges = self.edges
		place = 0

		while place < len(edges):

			start = edges[place]

			if start + BYTE > self.tick:
				break

			value = 0

			for bit in range(8):
				if not self.high_at(start + BIT + bit * BIT + BIT // 2):
					value |= 1 << bit

			assert self.high_at(start + BIT // 2), "no start bit at %d" % start
			assert not self.high_at(start + 9 * BIT + BIT // 2), "no stop bit at %d" % start

			while place < len(edges) and edges[place] < start + BYTE:
				assert (edges[place] - start) % BIT == 0, "an edge off the bit grid at %d" % edges[place]
				place += 1

			found.append((start, value))

		return found

	def values (self) -> list[int]:

		"""Give just the bytes received, in order."""

		return [value for _, value in self.received()]

	def clocks (self) -> list[int]:

		"""Give the first tick of every clock byte received."""

		return [start for start, value in self.received() if value == CLOCK]

	def only (self, wanted: int) -> list[int]:

		"""Give the first tick of every byte of one kind received."""

		return [start for start, value in self.received() if value == wanted]


def line (mode: int, ratios: "list[tuple[int, int]] | None" = None, tempo: int = 12000) -> Line:

	"""Make a stopped clock whose first output pulses with every MIDI clock, unless other ratios are given."""

	timeline = tests.checks.check_timeline.make(ratios or [(24, 1), (1, 1)], tempo)
	timeline.set_midi(mode)

	return Line(timeline)


def check_midi_off_sends_nothing_and_starts_at_once () -> None:

	"""As a new timeline is, MIDI off leaves its pin alone and the first pulse comes straight away."""

	clock = line(OFF)

	assert clock.timeline.start() == 0

	clock.play_until(BEAT)

	assert clock.edges == []
	assert clock.rises[0][0] == 0

	clock.timeline.stop()
	clock.play(4)

	assert clock.timeline.quiet()


def check_start_goes_first_and_the_first_clock_finishes_on_the_first_pulse () -> None:

	"""Start, at least a millisecond, then a clock byte whose last bit ends as every output's first pulse begins."""

	clock = line(PLAYING)

	assert clock.timeline.start() == BYTE + GAP + BYTE

	clock.play_until(BEAT)

	received = clock.received()

	assert received[0] == (0, START)
	assert received[1] == (BYTE + GAP, CLOCK)
	assert clock.rises[0][0] == clock.rises[1][0] == received[1][0] + BYTE


def check_every_clock_finishes_on_its_place_at_every_tempo () -> None:

	"""24 clocks to the beat, each ending exactly as a pulse at the same rate begins, whole gaps or not."""

	for tempo in (2000, 12000, 13337, 17400, 30000):

		clock = line(PLAYING, tempo=tempo)
		clock.timeline.start()
		clock.play_until(1000000 if tempo > 2000 else 3000000)

		clocks = clock.clocks()
		rises = clock.rises[0]

		assert len(clocks) > 20

		for index in range(len(clocks) - 1):
			assert clocks[index] + BYTE == rises[index], "tempo %d, clock %d" % (tempo, index)


def check_clocks_stay_whole_among_busy_outputs () -> None:

	"""Pulses landing in the middle of a byte split its words but move none of its edges."""

	clock = line(PLAYING, [(24, 1), (96, 1), (137, 100), (7, 3)], 17400)
	clock.timeline.start()
	clock.play_until(1000000)

	clocks = clock.clocks()

	assert len(clocks) >= 60

	for index in range(len(clocks) - 1):
		assert clocks[index] + BYTE == clock.rises[0][index]


def check_no_clock_is_lost_or_doubled_through_changes_of_tempo () -> None:

	"""However sharply the tempo moves, there is one clock byte for every place on the grid, sent no later than that place."""

	seed = 12345
	clock = line(PLAYING)
	clock.timeline.start()

	for _ in range(300):

		seed = (seed * 1103 + 12345) % 65536
		clock.play(1 + seed % 40)
		seed = (seed * 1103 + 12345) % 65536
		clock.timeline.set_tempo(2000 + seed * 28000 // 65536)

	clock.play_until(clock.tick + 3000000)

	clocks = clock.clocks()
	rises = clock.rises[0]

	assert len(rises) > 100
	assert 0 <= len(clocks) - len(rises) <= 1

	for index in range(len(rises)):
		assert clocks[index] <= rises[index], "clock %d came after its place" % index


def check_a_clock_that_could_not_go_in_time_goes_late_and_is_not_lost () -> None:

	"""With the line taken right up to a place on the grid, that place's clock follows, and the count stays right."""

	clock = line(PLAYING)
	timeline = clock.timeline
	timeline.start()
	clock.play_until(100000)

	# Part of the way through a clock byte, close before its place on the grid.
	while not 0 < timeline.until[timeline.midi_slot] < BYTE:
		clock.play(1)

	# Switched off and on, MIDI says Stop, which holds the line past the next place on the grid.
	timeline.set_midi(OFF)
	timeline.set_midi(PLAYING)

	began = clock.tick

	clock.play_until(clock.tick + 100000)

	rises = [rise for rise in clock.rises[0] if rise >= began]
	clocks = [start for start in clock.clocks() if start >= began]

	assert len(rises) >= 4

	# The first waits for Stop to finish. Every other is back in its place.
	for index in range(len(rises)):
		assert clocks[index] <= rises[index] + 2 * BYTE, index


def check_a_pause_says_stop_after_any_byte_going_out_and_stops_the_clocks () -> None:

	"""Paused at every point of a clock byte, that byte finishes, Stop follows it whole, and no clock comes after."""

	for into in (0, 1, 31, 32, 127, 128, 200, 319, 320, 400):

		clock = line(PLAYING)
		clock.timeline.start()
		clock.play_until(BYTE + GAP + TICK_GAP - BYTE + into)
		clock.timeline.pause()
		clock.play_until(clock.tick + 100000)

		received = clock.received()

		assert received[-1][1] == STOP, into
		assert [value for _, value in received[:-1]].count(STOP) == 0

		assert clock.timeline.quiet()


def check_carrying_on_says_continue_and_keeps_every_clock_on_its_place () -> None:

	"""Continue comes first and at least a millisecond ahead of the next clock, and no place on the grid is missed or doubled."""

	for into in (2000, TICK_GAP // 2, TICK_GAP - 400, TICK_GAP - 100):

		clock = line(PLAYING)
		clock.timeline.start()
		clock.play_until(BYTE + GAP + BYTE + TICK_GAP * 3 + into)
		clock.timeline.pause()
		clock.play_until(clock.tick + 50000)
		clock.timeline.resume()
		resumed = clock.tick
		clock.play_until(clock.tick + 200000)

		received = clock.received()
		values = [value for _, value in received]
		place = values.index(CONTINUE)

		assert received[place][0] >= resumed
		assert values[place + 1] == CLOCK or into >= TICK_GAP - BYTE
		assert received[place + 1][0] >= received[place][0] + BYTE + GAP or values[place + 1] != CLOCK

		clocks = clock.clocks()
		rises = clock.rises[0]

		assert 0 <= len(clocks) - len(rises) <= 1, into

		for index in range(len(rises)):
			assert clocks[index] <= rises[index]


def check_carrying_on_with_a_clock_far_off_moves_nothing () -> None:

	"""With time for Continue and its gap before the next clock, the outputs carry on exactly as they would without MIDI."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(BYTE + GAP + BYTE + 2000)
	clock.timeline.pause()
	clock.play_until(clock.tick + 5000)

	assert clock.timeline.resume() == 0


def check_carrying_on_with_a_clock_close_waits_only_as_long_as_it_must () -> None:

	"""A clock due too soon after Continue holds everything back by just enough for the gap."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(BYTE + GAP + BYTE + TICK_GAP - 900)
	clock.timeline.pause()
	clock.play_until(clock.tick + 5000)

	held = clock.timeline.held[clock.timeline.midi_slot]
	lead = clock.timeline.resume()

	assert held < BYTE + GAP + BYTE
	assert lead == BYTE + GAP + BYTE - held


def check_stop_says_stop_once () -> None:

	"""Stop from playing says Stop. Stop from a pause has already said it."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(100000)
	clock.timeline.stop()
	clock.play_until(clock.tick + 10000)

	assert clock.values().count(STOP) == 1

	paused = line(PLAYING)
	paused.timeline.start()
	paused.play_until(100000)
	paused.timeline.pause()
	paused.play_until(paused.tick + 10000)
	paused.timeline.stop()
	paused.play(8)

	assert paused.values().count(STOP) == 1


def check_a_stop_never_cuts_a_byte_in_half () -> None:

	"""Stopped in the middle of a clock byte, which clears every other pin, the byte still finishes."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(BYTE + GAP + 64)

	assert clock.pins & synthsync.core.timeline.MIDI_BIT

	clock.timeline.stop()

	assert clock.timeline.mask & synthsync.core.timeline.MIDI_BIT

	clock.play_until(clock.tick + 2000)

	assert clock.values() == [START, CLOCK, STOP]


def check_a_start_never_cuts_a_byte_in_half () -> None:

	"""Started in the middle of a clock sent while stopped, that byte finishes and Start follows it whole."""

	clock = line(ALWAYS)
	clock.play(1)

	assert clock.pins & synthsync.core.timeline.MIDI_BIT

	clock.timeline.start()
	clock.play_until(BYTE + BYTE + GAP + BYTE + 2000)

	assert clock.values()[:3] == [CLOCK, START, CLOCK]


def check_start_again_at_once_keeps_every_byte_in_order () -> None:

	"""Stop and start together are both said, in order, and the gap is kept after the Start."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(100000)
	clock.timeline.stop()
	clock.timeline.start()
	clock.play_until(clock.tick + 100000)

	received = clock.received()
	values = [value for _, value in received]
	place = len(values) - 1 - values[::-1].index(START)

	assert values[place - 1] == STOP
	assert values[place + 1] == CLOCK
	assert received[place + 1][0] >= received[place][0] + BYTE + GAP


def check_too_many_presses_at_once_still_end_on_the_last () -> None:

	"""Presses faster than the line can carry lose the ones between, never the last, and every byte stays whole."""

	clock = line(PLAYING)
	clock.timeline.start()
	clock.play_until(100000)

	for _ in range(5):
		clock.timeline.pause()
		clock.timeline.resume()

	clock.timeline.pause()
	clock.play_until(clock.tick + 10000)

	assert clock.values()[-1] == STOP
	assert clock.timeline.quiet()


def check_clock_always_carries_on_while_stopped_at_the_tempo () -> None:

	"""Stopped from the start, clock bytes come one gap apart and nothing else does, and the words stay short."""

	clock = line(ALWAYS)
	clock.play_until(BEAT)

	clocks = clock.clocks()

	assert not clock.timeline.quiet()
	assert clock.timeline.still()
	assert clock.values() == [CLOCK] * len(clocks)
	assert len(clocks) >= 23
	assert clock.longest <= synthsync.core.timeline.MIDI_IDLE_HOLD

	for index in range(len(clocks) - 1):
		assert clocks[index + 1] - clocks[index] == TICK_GAP or clocks[index + 1] - clocks[index] == TICK_GAP + 1


def check_clock_always_follows_the_tempo_while_stopped () -> None:

	"""A new tempo sets the gap of the clocks that follow."""

	clock = line(ALWAYS)
	clock.play_until(100000)
	clock.timeline.set_tempo(24000)
	clock.play_until(clock.tick + 50000)
	began = len(clock.clocks())
	clock.play_until(clock.tick + 200000)

	clocks = clock.clocks()[began:]

	for index in range(len(clocks) - 1):
		assert clocks[index + 1] - clocks[index] in (TICK_GAP // 2, TICK_GAP // 2 + 1)


def check_the_clock_keeps_its_stride_when_the_music_stops () -> None:

	"""Stop or pause says Stop between clocks, and the next clock comes one gap after the last, not early or doubled, even when the last has only just begun."""

	for stopping in (True, False):

		for after in (3000, TICK_GAP - 100):

			clock = line(ALWAYS)
			clock.timeline.start()
			clock.play_until(BYTE + GAP + BYTE + TICK_GAP * 5 + after)

			if stopping:
				clock.timeline.stop()
			else:
				clock.timeline.pause()

			clock.play_until(clock.tick + TICK_GAP * 4)

			clocks = clock.clocks()

			assert STOP in clock.values()

			for index in range(1, len(clocks) - 1):
				assert clocks[index + 1] - clocks[index] in (TICK_GAP, TICK_GAP + 1), (stopping, after, index)


def check_a_start_after_clock_always_keeps_its_gap () -> None:

	"""The clocks sent while stopped give way to Start, and none comes in the millisecond after it."""

	for wait in (0, 1000, TICK_GAP - 100, TICK_GAP + 100):

		clock = line(ALWAYS)
		clock.play_until(wait + 20000)
		clock.timeline.start()
		clock.play_until(clock.tick + 100000)

		received = clock.received()
		values = [value for _, value in received]
		place = values.index(START)

		assert values[place + 1] == CLOCK
		assert received[place + 1][0] >= received[place][0] + BYTE + GAP
		assert received[place + 1][0] + BYTE == clock.rises[0][0]


def check_only_while_playing_goes_quiet_when_stopped () -> None:

	"""With clock only while playing, a stopped clock leaves nothing to feed once Stop has gone."""

	clock = line(PLAYING)

	assert clock.timeline.quiet()

	clock.timeline.start()
	clock.play_until(100000)
	clock.timeline.stop()

	assert not clock.timeline.quiet()

	clock.play_until(clock.tick + 11000)
	count = len(clock.received())

	assert clock.timeline.quiet()
	assert clock.timeline.midi_resting()

	clock.play(20)

	assert len(clock.received()) == count


def check_switching_off_while_playing_says_stop_and_goes_silent () -> None:

	"""Nothing is left waiting for clocks that won't come, and the pin ends at rest."""

	clock = line(ALWAYS)
	clock.timeline.start()
	clock.play_until(100000)
	clock.timeline.set_midi(OFF)
	clock.play_until(clock.tick + 100000)

	assert clock.values()[-1] == STOP
	assert not clock.pins & synthsync.core.timeline.MIDI_BIT
	assert clock.timeline.midi_on == 0

	edges = len(clock.edges)

	clock.play_until(clock.tick + 100000)

	assert len(clock.edges) == edges


def check_switching_off_while_stopped_ends_the_clock_without_a_stop () -> None:

	"""Stopped, there is nothing to say Stop to, and the byte going out still finishes."""

	clock = line(ALWAYS)
	clock.play(1)

	assert clock.pins & synthsync.core.timeline.MIDI_BIT

	clock.timeline.set_midi(OFF)
	clock.play_until(clock.tick + 20000)

	assert clock.values() == [CLOCK]
	assert clock.timeline.quiet()


def check_switching_on_while_running_joins_the_grid () -> None:

	"""Clocks begin at the next place on the grid, with no Start, ending exactly on pulses at the same rate."""

	clock = line(OFF)
	clock.timeline.start()
	clock.play_until(100000)
	clock.timeline.set_midi(PLAYING)
	clock.play_until(clock.tick + 200000)

	clocks = clock.clocks()
	rises = clock.rises[0]

	assert len(clocks) >= 8
	assert START not in clock.values()

	for start in clocks[:-1]:
		assert start + BYTE in rises


def check_changing_between_the_two_on_settings_starts_and_ends_the_stopped_clock () -> None:

	"""Clock while stopped comes and goes with the setting, and playing is untouched by it."""

	clock = line(PLAYING)
	clock.play(4)

	assert clock.edges == []

	clock.timeline.set_midi(ALWAYS)
	clock.play_until(clock.tick + 100000)
	count = len(clock.clocks())

	assert count >= 4

	clock.timeline.set_midi(PLAYING)
	clock.play_until(clock.tick + 100000)

	assert len(clock.clocks()) <= count + 1
	assert clock.timeline.quiet()


def check_a_setting_it_does_not_have_is_refused () -> None:

	"""Only the three settings exist."""

	timeline = synthsync.core.timeline.Timeline(1)

	for mode in (-1, 3):

		try:
			timeline.set_midi(mode)

		except ValueError:
			continue

		assert False, "%d was taken" % mode


def check_words_without_room_for_midi_refuse_it () -> None:

	"""A timeline made with no pin bit for MIDI can't be asked to send it."""

	timeline = synthsync.core.timeline.Timeline(1, pin_bits=synthsync.core.timeline.MOST_OUTPUTS + 1)

	try:
		timeline.set_midi(PLAYING)

	except ValueError:
		return

	assert False, "it was taken"
