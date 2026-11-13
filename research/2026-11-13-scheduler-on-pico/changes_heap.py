import gc
import picoclock.core.timeline
timeline = picoclock.core.timeline.Timeline(8)
timeline.set_tempo(30000)
for output in range(8):
	timeline.set_ratio(output, 3 + output, 2)
timeline.start()
next_word = timeline.next_word
for _ in range(100):
	next_word()
gc.collect()
before = gc.mem_alloc()
for step in range(300):
	timeline.set_tempo(2000 + step * 93)
	timeline.set_ratio(1 + step % 7, 1 + step % 96, 1 + step % 3)
	next_word()
	next_word()
after = gc.mem_alloc()
print('heap growth over 300 tempo changes, 300 ratio changes and 600 words on the Pico:', after - before, 'bytes; merged', timeline.merged)
timeline.stop()
gc.collect()
before = gc.mem_alloc()
timeline.start()
for _ in range(50):
	next_word()
timeline.stop()
print('heap growth over a start, 50 words and a stop:', gc.mem_alloc() - before, 'bytes')
