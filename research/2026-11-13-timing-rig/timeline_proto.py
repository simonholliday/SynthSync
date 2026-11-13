# Prototype of the exact timeline scheduler (research only, not the final module).
#
# Model: master position is measured in beats. Output k pulses whenever the position
# is a whole multiple of den_k/num_k beats (num/den = pulses per beat), counted from
# transport zero. Time is integer player ticks. Every pulse time is computed as the
# floor of an exact rational, so rounding never accumulates and coincident pulses of
# different outputs land on the same tick.
#
# Runs unchanged on CPython and MicroPython (plain ints only; MicroPython ints are
# arbitrary precision).

UNITS = 1000000000  # position grid used only for tempo-change anchors (1e-9 beat)


class Timeline:

	def __init__ (self, tick_hz, ratios, bpm_centi, width_ticks, chunk_ticks):

		self.k = 6000 * tick_hz  # ticks per beat = k / bpm_centi
		self.bpm = bpm_centi
		self.width = width_ticks
		self.chunk = chunk_ticks

		# Tempo anchor: position anchor_pos (in 1/UNITS beat) happens at tick anchor_t.
		self.anchor_pos = 0
		self.anchor_t = 0

		self.now = 0  # tick up to which words have been produced
		n = len(ratios)
		self.num = [r[0] for r in ratios]
		self.den = [r[1] for r in ratios]
		self.index = [0] * n      # next pulse number per output
		self.rise = [0] * n       # tick of next rising edge
		self.fall = [-1] * n      # tick of pending falling edge, -1 if low
		self.mask = 0

		for i in range(n):
			self.rise[i] = self._pulse_time(i, 0)

	def _pulse_time (self, i, j):

		# Position of pulse j is j*den/num beats. Convert to ticks from the anchor, floor.
		m = self.num[i]
		top = (j * self.den[i] * UNITS - self.anchor_pos * m) * self.k

		return self.anchor_t + top // (m * self.bpm * UNITS)

	def _period_ticks (self, i):

		return (self.den[i] * self.k) // (self.num[i] * self.bpm)

	def _position_at (self, t):

		# Position (1/UNITS beat) at tick t, rounded down.
		return self.anchor_pos + ((t - self.anchor_t) * self.bpm * UNITS) // self.k

	def set_bpm (self, bpm_centi):

		# Re-anchor at the render point, then recompute pending rises from position.
		self.anchor_pos = self._position_at(self.now)
		self.anchor_t = self.now
		self.bpm = bpm_centi

		for i in range(len(self.num)):
			self.rise[i] = max(self._pulse_time(i, self.index[i]), self.now)

	def set_ratio (self, i, num, den):

		# Next pulse is the first grid point of the new ratio at or after the render point.
		pos = self._position_at(self.now)
		self.num[i] = num
		self.den[i] = den
		j = -((-pos * num) // (den * UNITS))  # ceil(pos * num / den)
		t = self._pulse_time(i, j)

		if t < self.now:
			j += 1
			t = self._pulse_time(i, j)

		self.index[i] = j
		self.rise[i] = t

	def next_word (self):

		# Returns (mask, duration_ticks): hold `mask` for `duration` ticks.
		now = self.now
		nxt = now + self.chunk
		rise = self.rise
		fall = self.fall
		n = len(rise)

		for i in range(n):
			t = fall[i]
			if t >= 0 and t < nxt:
				nxt = t
			t = rise[i]
			if t < nxt:
				nxt = t

		if nxt <= now:
			# Events due right now: apply them, then look again for the hold time.
			mask = self.mask
			for i in range(n):
				if fall[i] >= 0 and fall[i] <= now:
					mask &= ~(1 << i)
					fall[i] = -1
				if rise[i] <= now:
					mask |= 1 << i
					period = self._period_ticks(i)
					w = self.width if self.width * 2 <= period else period // 2
					fall[i] = now + (w if w > 0 else 1)
					self.index[i] += 1
					rise[i] = self._pulse_time(i, self.index[i])
			self.mask = mask
			nxt = now + self.chunk
			for i in range(n):
				t = fall[i]
				if t >= 0 and t < nxt:
					nxt = t
				t = rise[i]
				if t < nxt:
					nxt = t

		self.now = nxt

		return self.mask, nxt - now
