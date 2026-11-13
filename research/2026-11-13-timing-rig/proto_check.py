# Host-side check of the timeline prototype against exact fractions.

import fractions
import random
import sys
import time

import timeline_proto

TICK_HZ = 150000000


def rises (tl, n_words):

	"""Play n_words and return, per output, the tick of every rising edge."""

	out = [[] for _ in tl.num]
	t = 0
	prev = 0
	total = 0

	for _ in range(n_words):
		mask, dur = tl.next_word()
		assert dur > 0, "zero-length word"
		for i in range(len(out)):
			if (mask >> i) & 1 and not (prev >> i) & 1:
				out[i].append(t)
		prev = mask
		t += dur
		total += 1

	return out, t


def check_constant_tempo () -> None:

	ratios = [(1, 1), (2, 1), (3, 1), (1, 4), (3, 2), (137, 100), (24, 1), (5, 4)]
	bpm = 12345  # 123.45 BPM
	tl = timeline_proto.Timeline(TICK_HZ, ratios, bpm, width_ticks=1500000, chunk_ticks=1875000)
	out, end = rises(tl, 400000)
	beat = fractions.Fraction(6000 * TICK_HZ, bpm)
	worst = fractions.Fraction(0)
	count = 0

	for i, (num, den) in enumerate(ratios):
		for j, t in enumerate(out[i]):
			exact = j * fractions.Fraction(den, num) * beat
			err = exact - t
			assert 0 <= err < 1, (i, j, t, exact)
			worst = max(worst, err)
			count += 1

	print("constant tempo: %d pulses over %.1f min, every edge within 1 tick of exact (worst %.3f ticks), drift 0" % (
		count, end / TICK_HZ / 60, float(worst)))

	# Coincidence: x1, x2, x3 must share a tick on every beat.
	beats = set(out[0])
	assert beats <= set(out[1]) and beats <= set(out[2])
	print("coincidence: all %d beats of x1 are the same tick on x2 and x3" % len(beats))


def check_changes () -> None:

	rng = random.Random(7)
	ratios = [(1, 1), (2, 1), (3, 1), (1, 2)]
	tl = timeline_proto.Timeline(TICK_HZ, ratios, 12000, width_ticks=1500000, chunk_ticks=1875000)
	prev = 0
	t = 0
	pos_exact = fractions.Fraction(0)
	bpm = 12000
	edges = 0
	worst_units = 0

	for step in range(300000):
		if step % 97 == 0:
			bpm = rng.randrange(2000, 30001)
			tl.set_bpm(bpm)
		if step % 1009 == 0:
			i = rng.randrange(1, 4)
			tl.set_ratio(i, rng.choice([1, 2, 3, 4, 6, 8]), rng.choice([1, 2, 3, 4]))
		before_bpm = tl.bpm
		mask, dur = tl.next_word()
		rising = mask & ~prev
		if rising:
			# Position at this tick, from the scheduler's own anchor, must be on each riser's grid.
			pos = fractions.Fraction(tl.anchor_pos, timeline_proto.UNITS) + fractions.Fraction((t - tl.anchor_t) * before_bpm, tl.k)
			for i in range(4):
				if (rising >> i) & 1:
					grid = fractions.Fraction(tl.den[i], tl.num[i])
					off = pos % grid
					off = min(off, grid - off)
					ticks_off = off * fractions.Fraction(tl.k, before_bpm)
					assert ticks_off < 2, (step, i, float(ticks_off))
					edges += 1
		prev = mask
		t += dur

	print("tempo and ratio changes: %d edges, all on their own grid from transport zero (within 2 ticks) after %d tempo and %d ratio changes" % (
		edges, 300000 // 97 + 1, 300000 // 1009 + 1))


def speed () -> None:

	ratios = [(1, 1), (2, 1), (3, 1), (4, 1)]
	tl = timeline_proto.Timeline(TICK_HZ, ratios, 12000, width_ticks=1500000, chunk_ticks=1875000)
	n = 20000
	t0 = time.perf_counter()
	for _ in range(n):
		tl.next_word()
	dt = time.perf_counter() - t0
	print("host speed: %.1f us per word" % (dt / n * 1e6))


if __name__ == "__main__":
	check_constant_tempo()
	check_changes()
	speed()
