"""What a tempo knob's readings mean: a potentiometer on the panel, sharing the tempo with the page.

There is one tempo, and whichever control was touched last has it. The knob
never makes the tempo jump. Once the tempo has been set from anywhere else, the
knob is ignored until it has clearly been moved and has then come to the tempo,
and from there the tempo follows it. A knob left alone can't take over, however
its reading wobbles.

At power-up the knob is compared with where it was last noted. If it has moved
since, or was never noted, it sets the tempo, because nothing is following the
clock yet and what the knob points at is what the musician last chose. If not,
the saved tempo plays.

The travel is curved so that the middle of it is the musical middle. In the
usual range, 20 to 300 BPM, the quarter marks are 20, 80, 120, 180 and 300. Any
other range has the same shape by proportion, so that its slow end is as easy
to set as its fast end.

The tempo moves in the steps its range calls for, or in larger ones where the
curve is too steep for the knob to hold a finer one steady.

This runs on the Pico's first core and on a laptop. It has no part in the
timing, so unlike the scheduler it can use decimals.
"""

import math

import synthsync.core.limits

# Readings run from 0 to this.
FULLEST = 4095

# How much of each end of the travel counts as the end, so that both can be reached.
_DEAD = 61

# How far a reading must move to count as the knob having been moved: 2% of the
# travel, many times what a knob left alone wanders by.
MOVED = 82

# How far the knob may drift from where it was noted while it has the tempo.
_NOTE_AFTER = MOVED // 2

# The quarter marks of the usual range, in BPM, and how far along the range each
# one is, by proportion.
_MARKS = (20, 80, 120, 180, 300)
_SHAPE = tuple(math.log(mark / _MARKS[0]) / math.log(_MARKS[-1] / _MARKS[0]) for mark in _MARKS)

# The sizes a step can be, in hundredths of a BPM, and how many readings one has
# to span for the knob to rest on it steadily.
_STEPS = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000)
_READINGS_A_STEP = 6

# How far past a step the knob has to go before the tempo leaves it, in steps.
_HOLD = 0.75


class Knob:

	"""Turns a knob's readings into tempos, and decides when it has the tempo."""

	def __init__ (self, limits: synthsync.core.limits.Limits = synthsync.core.limits.DEFAULT) -> None:

		"""Begin with a knob that has yet to be read, for this tempo range."""

		self.limits = limits
		self.following = False
		self._spread = math.log(limits.fastest_tempo / limits.slowest_tempo)

		# Where the knob was when the tempo was last taken from it, whether it has
		# been moved from there, which side of the tempo it then was, and the
		# tempo this knob last knew of.
		self._anchor = 0
		self._moved = False
		self._side = 0
		self._known = -1

	def _along (self, reading: int) -> float:

		"""Say how far along the travel a reading is, in quarters of it, from 0 to 4."""

		return max(0.0, min(4.0, (reading - _DEAD) * 4.0 / (FULLEST - 2 * _DEAD)))

	def wanted (self, reading: int) -> float:

		"""Give the tempo this reading points at, exactly, in hundredths of a BPM."""

		along = self._along(reading)
		quarter = min(int(along), 3)
		share = _SHAPE[quarter] + (_SHAPE[quarter + 1] - _SHAPE[quarter]) * (along - quarter)

		return self.limits.slowest_tempo * math.exp(self._spread * share)

	def step (self, reading: int) -> int:

		"""Give the size of a tempo step at this reading: the range's own, or larger where the knob couldn't hold that steady."""

		high = min(reading + _READINGS_A_STEP, FULLEST)
		across = self.wanted(high) - self.wanted(max(high - _READINGS_A_STEP, 0))

		for size in _STEPS:
			if size >= self.limits.tempo_step and size >= across:
				return size

		return _STEPS[-1]

	def tempo_at (self, reading: int) -> int:

		"""Give the tempo a knob at this reading plays: the nearest step, and at either end of the travel the very end of the range."""

		along = self._along(reading)

		if along <= 0.0:
			return self.limits.slowest_tempo

		if along >= 4.0:
			return self.limits.fastest_tempo

		size = self.step(reading)

		return self.limits.tempo_in(int(self.wanted(reading) / size + 0.5) * size)

	def _following (self, reading: int, tempo: int) -> "int | None":

		"""Give the tempo a knob that has the tempo moves it to, or None to leave it. It has to pass most of a step, so it can't flicker between two."""

		target = self.tempo_at(reading)

		if target == tempo:
			return None

		along = self._along(reading)

		if 0.0 < along < 4.0 and abs(self.wanted(reading) - tempo) <= self.step(reading) * _HOLD:
			return None

		self._known = target

		return target

	def start (self, reading: int, tempo: int, noted: int) -> "int | None":

		"""Begin at power-up, with the saved tempo and where the knob was noted, and give the tempo to change to, if any."""

		self._anchor = reading
		self._moved = False
		self._known = tempo

		if noted < 0 or abs(reading - noted) > MOVED:
			self.following = True
			target = self.tempo_at(reading)

			if target == tempo:
				return None

			self._known = target

			return target

		self.following = abs(self.wanted(reading) - tempo) <= self.step(reading)

		return None

	def look (self, reading: int, tempo: int) -> "int | None":

		"""Take a new reading, with the tempo as it stands, and give the tempo to change to, if any."""

		# The tempo was set from somewhere else, which lets go of the knob.
		if tempo != self._known:
			self._known = tempo
			self.following = False
			self._anchor = reading
			self._moved = False

		if self.following:
			return self._following(reading, tempo)

		apart = self.wanted(reading) - tempo

		if not self._moved:

			if abs(reading - self._anchor) <= MOVED:
				return None

			self._moved = True
			self._side = 1 if apart > 0 else -1

		# It has come to the tempo, or gone past it.
		if abs(apart) <= self.step(reading) or (apart > 0) != (self._side > 0):
			self.following = True
			return self._following(reading, tempo)

		return None

	def to_note (self, reading: int, noted: int) -> bool:

		"""Say whether where the knob is should be noted, to be saved: only while it has the tempo, and it has moved."""

		return self.following and (noted < 0 or abs(reading - noted) > _NOTE_AFTER)
