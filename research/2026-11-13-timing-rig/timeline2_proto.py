# Prototype 2 of the timeline scheduler (research only): the per-word path uses only
# small integers and preallocated arrays, so it never touches the heap and cannot be
# held up by a garbage collection on the other core.
#
# Scheduler tick = 1 microsecond. Each output keeps the time until its next pulse as a
# whole number of ticks plus an exact fraction acc/d, so nothing is ever rounded away.

import array

K = 6000 * 1000000  # ticks per beat = K / bpm_centi
CPT = 150           # player (PIO) cycles per scheduler tick

try:
	import micropython
except ImportError:
	class micropython:  # host stand-in: the decorator is a compiler directive on the device
		@staticmethod
		def native (f):
			return f


def scale (u, a, d_old, d_new):

	# Exact change of denominator: returns (q, r) with q*d_new + r == u*d_old + a,
	# using only values below 2**30. Needs d_old, d_new < 2**28.
	q = 0
	r = 0
	bit = 1 << 29

	while bit and not (u & bit):
		bit >>= 1

	while bit:
		q <<= 1
		r <<= 1
		if u & bit:
			r += d_old
		if r >= d_new:
			q += r // d_new
			r %= d_new
		bit >>= 1

	r += a
	if r >= d_new:
		q += r // d_new
		r %= d_new

	return q, r


class Timeline2:

	def __init__ (self, ratios, bpm_centi, width, chunk):

		n = len(ratios)
		self.n = n
		self.num = [x[0] for x in ratios]
		self.den = [x[1] for x in ratios]
		self.q = array.array('i', [0] * n)
		self.r = array.array('i', [0] * n)
		self.d = array.array('i', [0] * n)
		self.until = array.array('i', [0] * n)
		self.acc = array.array('i', [0] * n)
		self.fall = array.array('i', [-1] * n)
		self.width = width
		self.chunk = chunk
		self.mask = 0
		self.bpm = bpm_centi
		self._load(bpm_centi)

	def _load (self, b):

		# Control path: ordinary integers are fine here.
		for i in range(self.n):
			d = self.num[i] * b
			top = self.den[i] * K
			self.q[i] = top // d
			self.r[i] = top % d
			self.d[i] = d

	def set_bpm (self, b):

		for i in range(self.n):
			u, a = scale(self.until[i], self.acc[i], self.d[i], self.num[i] * b)
			self.until[i] = u
			self.acc[i] = a

		self.bpm = b
		self._load(b)

	@micropython.native
	def next_word (self):

		# Returns one player word: pin states in the low 8 bits, hold time above.
		until = self.until
		fall = self.fall
		acc = self.acc
		q = self.q
		r = self.r
		d = self.d
		n = self.n
		width = self.width
		mask = self.mask
		nxt = self.chunk

		for i in range(n):
			if fall[i] == 0:
				mask &= ~(1 << i)
				fall[i] = -1
			u = until[i]
			if u == 0:
				mask |= 1 << i
				a = acc[i] + r[i]
				u = q[i]
				if a >= d[i]:
					a -= d[i]
					u += 1
				acc[i] = a
				until[i] = u
				fall[i] = width if width * 2 <= u else u >> 1
			if u < nxt:
				nxt = u
			f = fall[i]
			if f >= 0 and f < nxt:
				nxt = f

		for i in range(n):
			until[i] -= nxt
			if fall[i] > 0:
				fall[i] -= nxt

		self.mask = mask

		return mask | ((nxt * CPT - 4) << 8)
