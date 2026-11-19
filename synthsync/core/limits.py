"""How far the tempo and the ratios may go, which `config.txt` can change.

The clock plays from 20 to 300 BPM unless `config.txt` gives it another range,
which can be anything from 1 to 1000 BPM. A range can be as narrow as wanted.

A wide range doesn't ask more of the timing than the usual one does. No output
may wait longer between pulses than ÷128 does at 20 BPM, 384 seconds, or send
them faster than ×96 does at 300 BPM, 480 a second. So a range reaching below
20 BPM allows fewer beats between pulses, and one reaching above 300 BPM allows
fewer pulses a beat. They follow the range and not the tempo of the moment,
because a ratio has to stay playable wherever the tempo is turned next.

This runs on the Pico as well as on a laptop, so it uses whole numbers only.
"""

# Tempo is in hundredths of a BPM. These are as far as any range can go.
SLOWEST_TEMPO = 100
FASTEST_TEMPO = 100000

# The range unless `config.txt` gives another.
DEFAULT_SLOWEST_TEMPO = 2000
DEFAULT_FASTEST_TEMPO = 30000

# The most beats between two pulses, and the most pulses a beat, at any tempo.
MOST_BEATS = 128
MOST_A_BEAT = 96

# Neither half of a ratio may be larger than this. The scheduler multiplies the
# pulses by the tempo, and that product has to stay small enough for the Pico to
# handle without reaching for memory.
LARGEST_PART = 9600

# A change of tempo works with the old and new products together, and needs
# three of them to stay below this.
_SMALL = 2 ** 30 - 1

# 384 seconds is this many thousandths of a beat for each hundredth of a BPM.
_BEATS_A_TEMPO = 64

# 480 pulses a second, as pulses a beat times hundredths of a BPM.
_PULSES_BY_TEMPO = 2880000

# What one step of the tempo can be, in hundredths of a BPM, largest first, and
# how many steps a range is given if it can have them. A whole BPM is the
# largest, so the usual range moves in whole BPM.
_STEPS = (100, 50, 20, 10, 5, 2, 1)
_ENOUGH_STEPS = 100


class Limits:

	"""One range of tempos, and what it allows the ratios."""

	def __init__ (self, slowest_tempo: int = DEFAULT_SLOWEST_TEMPO, fastest_tempo: int = DEFAULT_FASTEST_TEMPO) -> None:

		"""Work out what this range allows, in hundredths of a BPM. It has to be one the clock can play."""

		if slowest_tempo < SLOWEST_TEMPO or fastest_tempo > FASTEST_TEMPO or slowest_tempo >= fastest_tempo:
			raise ValueError("A tempo range runs upwards, within 1 to 1000 BPM")

		self.slowest_tempo = slowest_tempo
		self.fastest_tempo = fastest_tempo
		self.most_beats = min(MOST_BEATS, slowest_tempo * _BEATS_A_TEMPO // 1000)
		self.most_a_beat = min(MOST_A_BEAT, _PULSES_BY_TEMPO // fastest_tempo)
		self.largest_part = min(LARGEST_PART, _SMALL // (3 * fastest_tempo))
		self.tempo_step = self._step()

	def _step (self) -> int:

		"""Choose how far one step of a tempo control goes: the largest that still gives the range plenty of steps."""

		span = self.fastest_tempo - self.slowest_tempo

		for step in _STEPS:
			if span // step >= _ENOUGH_STEPS:
				return step

		return 1

	def tempo_in (self, tempo: int) -> int:

		"""Give the tempo in this range that is nearest to this one."""

		return max(self.slowest_tempo, min(self.fastest_tempo, tempo))


# The range the clock has when nothing says otherwise.
DEFAULT = Limits()
