# Probe for the scheduler on the real board: heap use and the cost of a word.
import gc
import time

import picoclock.core.timeline


def run (label, ratios, tempo):
	timeline = picoclock.core.timeline.Timeline(len(ratios))
	timeline.set_tempo(tempo)
	for output in range(len(ratios)):
		timeline.set_ratio(output, ratios[output][0], ratios[output][1])
	timeline.start()
	next_word = timeline.next_word
	for _ in range(50):
		next_word()
	gc.collect()
	before = gc.mem_alloc()
	count = 4000
	ticks = 0
	started = time.ticks_us()
	for _ in range(count):
		next_word()
	took = time.ticks_diff(time.ticks_us(), started)
	grew_words = gc.mem_alloc() - before
	# how much music those words covered, to turn cost into a share of one core
	cover = picoclock.core.timeline.Timeline(len(ratios))
	cover.set_tempo(tempo)
	for output in range(len(ratios)):
		cover.set_ratio(output, ratios[output][0], ratios[output][1])
	cover.start()
	for _ in range(count):
		ticks += cover.unpack(cover.next_word())[1]
	gc.collect()
	before = gc.mem_alloc()
	started = time.ticks_us()
	for step in range(100):
		timeline.set_tempo(tempo - 1 - step)
	tempo_took = time.ticks_diff(time.ticks_us(), started) / 100
	started = time.ticks_us()
	for step in range(100):
		timeline.set_ratio(len(ratios) - 1, 3 + step % 5, 2)
	ratio_took = time.ticks_diff(time.ticks_us(), started) / 100
	for _ in range(200):
		next_word()
	grew_changes = gc.mem_alloc() - before
	print('%s: %.0f us a word, %.1f%% of one core, heap growth %d bytes over %d words and %d bytes over 200 changes; tempo change %.0f us, ratio change %.0f us' % (
		label, took / count, took / ticks * 100, grew_words, count, grew_changes, tempo_took, ratio_took))


run('4 outputs x1 x2 x3 x4 @120', [(1, 1), (2, 1), (3, 1), (4, 1)], 12000)
run('4 outputs x24 x16 x12 x32 @300', [(24, 1), (16, 1), (12, 1), (32, 1)], 30000)
run('8 outputs @120', [(1, 1), (2, 1), (3, 1), (4, 1), (6, 1), (8, 1), (1, 2), (1, 4)], 12000)
run('8 outputs fastest @300', [(96, 1), (64, 1), (48, 1), (32, 1), (24, 1), (16, 1), (12, 1), (8, 1)], 30000)
