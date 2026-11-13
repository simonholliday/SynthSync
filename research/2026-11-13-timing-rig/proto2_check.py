# Host check of prototype 2 against exact fractions, including live tempo changes.

import fractions
import random

import timeline2_proto

K = timeline2_proto.K
CPT = timeline2_proto.CPT


def decode (word: int) -> tuple[int, int]:

	"""Split a player word into (mask, ticks)."""

	return word & 0xFF, ((word >> 8) + 4) // CPT


def check_scale () -> None:

	"""The exact rescale helper must agree with plain integer arithmetic."""

	rng = random.Random(1)

	for _ in range(200000):
		d_old = rng.randrange(1, 1 << 28)
		d_new = rng.randrange(max(1, d_old // 16), min((1 << 28), d_old * 16) + 1)
		u = rng.randrange(0, 1 << 28)
		a = rng.randrange(0, d_old)
		total = u * d_old + a
		if total // d_new >= (1 << 30):
			continue
		q, r = timeline2_proto.scale(u, a, d_old, d_new)
		assert (q, r) == divmod(total, d_new), (u, a, d_old, d_new)

	print("scale helper: 200000 random cases match exact integer arithmetic")


def check_timeline () -> None:

	"""Every pulse must sit on its own grid from transport zero, through tempo changes."""

	rng = random.Random(3)
	ratios = [(1, 1), (2, 1), (3, 1), (1, 4), (3, 2), (137, 100), (24, 1), (4, 3)]
	bpm = 12345
	tl = timeline2_proto.Timeline2(ratios, bpm, 10000, 12500)
	pos = fractions.Fraction(0)   # exact position in beats at the current tick
	prev = 0
	count = [0] * len(ratios)
	pulses = 0
	changes = 0
	ticks = 0
	small_ok = True

	for step in range(600000):

		if step % 211 == 0:
			bpm = rng.randrange(2000, 30001)
			tl.set_bpm(bpm)
			changes += 1

		word = tl.next_word()
		small_ok = small_ok and word < (1 << 30)
		mask, dur = decode(word)
		rising = mask & ~prev
		per_tick = fractions.Fraction(bpm, K)

		for i in range(len(ratios)):
			if (rising >> i) & 1:
				grid = count[i] * fractions.Fraction(ratios[i][1], ratios[i][0])
				off = grid - pos
				assert 0 <= off < per_tick, (step, i, count[i], float(off / per_tick))
				count[i] += 1
				pulses += 1

		prev = mask
		pos += dur * per_tick
		ticks += dur

	print("timeline: %d pulses over %.1f min with %d tempo changes; every pulse within one tick (1 us) of its exact grid position, no accumulated error" % (
		pulses, ticks / 60e6, changes))
	print("all player words fit in 30 bits (no big integers on the fast path): %s" % small_ok)


if __name__ == "__main__":
	check_scale()
	check_timeline()
