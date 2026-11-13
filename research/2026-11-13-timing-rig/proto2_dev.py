# Device trial of prototype 2 (appended after rig.py + timeline2_proto.py + proto_dev.py).

import uctypes

PCTRL = [0]
PADDR = [0]


def producer2(sm, tl):
	put = sm.put
	lvl = sm.tx_fifo
	nw = tl.next_word
	while RUN[0]:
		c = CMD[0]
		if c:
			CMD[0] = 0
			tl.set_bpm(c)
		if PRE[0] == 1:
			# Build a backlog while still feeding the player, then hand the backlog to DMA.
			head = 0
			tail = 0
			span = 0
			lim = PRE_TICKS[0]
			while span < lim and tail < 4096:
				if head < tail and lvl() < 8:
					w = PBUF[head]
					head += 1
					put(w)
					span -= ((w >> 8) + 4) // 150
				else:
					w = nw()
					PBUF[tail] = w
					tail += 1
					span += ((w >> 8) + 4) // 150
			dma = PDMA[0]
			dma.config(read=PADDR[0] + 4 * head, write=TXF0, count=tail - head, ctrl=PCTRL[0], trigger=True)
			STAT[1] = tail - head
			PRE[0] = 2
			while dma.count > 0:
				pass
			PRE[0] = 0
			continue
		w = nw()
		l = lvl()
		if l < MINLVL[0]:
			MINLVL[0] = l
		put(w)


def word_cost2(ratios, bpm, n=5000):
	tl = Timeline2(ratios, bpm, 10000, 12500)
	gc.collect()
	m0 = gc.mem_alloc()
	t = time.ticks_us()
	span = 0
	for _ in range(n):
		w = tl.next_word()
		span += ((w >> 8) + 4) // 150
	dt = time.ticks_diff(time.ticks_us(), t)
	m1 = gc.mem_alloc()
	print('word cost v2: ratios=%s bpm=%.2f -> %.0f us/word, heap growth over %d words: %d bytes, %.1f%% of one core' % (
		ratios, bpm / 100, dt / n, n, m1 - m0, dt / 100.0 / span * 100))
	t = time.ticks_us()
	for _ in range(200):
		tl.set_bpm(bpm + 1)
		tl.set_bpm(bpm)
	dt = time.ticks_diff(time.ticks_us(), t)
	print('    tempo change: %.0f us each for %d outputs' % (dt / 400, len(ratios)))


def trial2(ratios, bpm, cap_out, n, load, bpm_sweep=False, preroll=False, wifi=False):
	tl = Timeline2(ratios, bpm, 10000, 12500)
	sm_cap, d, buf = cap_start(n, 2 + cap_out)
	sm = rp2.StateMachine(0, player4, freq=SYS_HZ, out_base=machine.Pin(2))
	num, den = ratios[cap_out]
	hz = bpm * num / (6000.0 * den)
	t_end = time.ticks_add(time.ticks_ms(), int(n * 1000 / hz) + 8000)
	done0 = lambda: d.count == 0 or time.ticks_diff(t_end, time.ticks_ms()) < 0
	PDMA[0] = rp2.DMA()
	PCTRL[0] = PDMA[0].pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0)
	PADDR[0] = uctypes.addressof(PBUF)
	PRE_TICKS[0] = 300000
	if wifi:
		wifi_up()
	sm.active(1)
	_thread.start_new_thread(producer2, (sm, tl))
	time.sleep_ms(50)
	machine.mem32[FDEBUG] = 1 << 24
	MINLVL[0] = 8
	state = [0]

	def done():
		state[0] += 1
		if bpm_sweep and state[0] % 3 == 0:
			CMD[0] = 6000 + (state[0] * 37) % 24000
		return done0()

	if preroll:
		data = bytes(300)
		writes = 0
		worst = 0
		while not done0():
			PRE[0] = 1
			while PRE[0] == 1:
				pass
			t = time.ticks_us()
			with open('/_rigtest.bin', 'wb') as f:
				f.write(data)
			dt = time.ticks_diff(time.ticks_us(), t)
			worst = max(worst, dt)
			writes += 1
			while PRE[0] != 0:
				time.sleep_ms(1)
			time.sleep_ms(100)
		os.remove('/_rigtest.bin')
		res = 'preroll flash writes=%d worst_write=%.1fms words_per_preroll=%d wifi=%s' % (writes, worst / 1000, STAT[1], wifi)
	else:
		res = LOADS[load](done)

	got = n - d.count
	RUN[0] = False
	stall = (machine.mem32[FDEBUG] >> 24) & 1
	tag = 'timeline2 out%d x%d/%d @%.2f %s' % (cap_out, num, den, bpm / 100, 'preroll' if preroll else load)

	if bpm_sweep:
		vals = [2 * buf[i] + 4 for i in range(2, got)]
		print('%s (tempo swept live): n=%d min=%.2fms max=%.2fms underrun=%d min_fifo=%d' % (
			tag, len(vals), min(vals) / 150000, max(vals) / 150000, stall, MINLVL[0]))
	else:
		period = lambda j: (j * den * K) // (num * bpm)
		exp = set()
		for j in range(0, 200):
			exp.add((period(j + 1) - period(j)) * 150)
		worst = 0
		bad = 0
		for i in range(2, got):
			v = 2 * buf[i] + 4
			e = min(abs(v - x) for x in exp)
			if e > worst:
				worst = e
			if e > 3:
				bad += 1
		print('%s: n=%d worst deviation from exact=%d cycles (%.1f ns), samples off by >3 cycles: %d, underrun=%d min_fifo=%d' % (
			tag, got - 2, worst, worst * 1e9 / SYS_HZ, bad, stall, MINLVL[0]))
	print('    load: %s' % res)
