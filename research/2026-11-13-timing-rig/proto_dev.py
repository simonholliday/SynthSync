# Device-side trial of the candidate architecture (appended after rig.py + timeline_proto.py):
# exact timeline on core 1 -> PIO player (4 outputs on GP2..GP5) -> PIO+DMA capture.

TXF0 = PIO0 + 0x010
CMD = array.array('i', [0])
PRE = array.array('i', [0])
MINLVL = array.array('i', [8])
PBUF = array.array('I', [0] * 4096)
PRE_TICKS = [0]
PDMA = [None]


@rp2.asm_pio(out_init=(rp2.PIO.OUT_LOW,) * 4, out_shiftdir=rp2.PIO.SHIFT_RIGHT, fifo_join=rp2.PIO.JOIN_TX)
def player4():
	# Word = 8 bits of pin state + 24 bits of hold time; lasts x + 4 ticks.
	wrap_target()
	pull(block)
	out(pins, 8)
	out(x, 24)
	label("d")
	jmp(x_dec, "d")
	wrap()


def producer(sm, tl):
	put = sm.put
	lvl = sm.tx_fifo
	nw = tl.next_word
	while RUN[0]:
		c = CMD[0]
		if c:
			CMD[0] = 0
			tl.set_bpm(c)
		if PRE[0] == 1:
			# Pre-roll: render ahead into a buffer and let DMA feed the player through a flash write.
			n = 0
			acc = 0
			lim = PRE_TICKS[0]
			while acc < lim and n < 4096:
				m, d = nw()
				if d < 4:
					d = 4
				PBUF[n] = m | ((d - 4) << 8)
				n += 1
				acc += d
			dma = PDMA[0]
			dma.config(read=PBUF, write=TXF0, count=n, ctrl=dma.pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0), trigger=True)
			STAT[1] = n
			PRE[0] = 2
			while dma.count > 0:
				pass
			PRE[0] = 0
			continue
		m, d = nw()
		if d < 4:
			d = 4
		l = lvl()
		if l < MINLVL[0]:
			MINLVL[0] = l
		put(m | ((d - 4) << 8))


def word_cost(ratios, bpm, n=3000):
	tl = Timeline(SYS_HZ, ratios, bpm, 1500000, 1875000)
	gc.collect()
	t = time.ticks_us()
	for _ in range(n):
		tl.next_word()
	dt = time.ticks_diff(time.ticks_us(), t)
	span = tl.now / SYS_HZ
	print('word cost: ratios=%s bpm=%.2f -> %.0f us/word, %d words cover %.2fs of music => producer uses %.1f%% of one core' % (
		ratios, bpm / 100, dt / n, n, span, dt / 1e4 / span))


def trial(ratios, bpm, cap_out, n, load, bpm_sweep=False, preroll=False, pause=False):
	tl = Timeline(SYS_HZ, ratios, bpm, 1500000, 1875000)
	sm_cap, d, buf = cap_start(n, 2 + cap_out)
	sm = rp2.StateMachine(0, player4, freq=SYS_HZ, out_base=machine.Pin(2))
	num, den = ratios[cap_out]
	hz = bpm * num / (6000.0 * den)
	t_end = time.ticks_add(time.ticks_ms(), int(n * 1000 / hz) + 8000)
	done0 = lambda: d.count == 0 or time.ticks_diff(t_end, time.ticks_ms()) < 0
	PDMA[0] = rp2.DMA()
	PRE_TICKS[0] = SYS_HZ * 6 // 10
	sm.active(1)
	_thread.start_new_thread(producer, (sm, tl))
	time.sleep_ms(50)
	machine.mem32[FDEBUG] = 1 << 24
	MINLVL[0] = 8
	state = [0, 0, 0]

	def done():
		# Side jobs run from the load's polling loop on core 0.
		state[0] += 1
		if bpm_sweep and state[0] % 3 == 0:
			CMD[0] = 6000 + (state[0] * 37) % 18000
		if pause and state[0] % 100 == 50:
			# Freeze the player mid-flight, hold, and release: position must carry on exactly.
			sm.active(0)
			time.sleep_ms(150)
			sm.active(1)
			state[1] += 1
		return done0()

	if preroll:
		# Realistic save: ask core 1 to pre-roll, then write flash while DMA feeds the player.
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
		res = 'preroll flash writes=%d worst_write=%.1fms words_per_preroll=%d' % (writes, worst / 1000, STAT[1])
	else:
		res = LOADS[load](done)

	got = n - d.count
	RUN[0] = False
	stall = (machine.mem32[FDEBUG] >> 24) & 1

	# Expected intervals from exact maths (constant tempo only).
	tag = 'timeline out%d x%d/%d @%.2f %s' % (cap_out, num, den, bpm / 100, 'preroll' if preroll else load)
	if bpm_sweep:
		vals = [2 * buf[i] + 4 for i in range(2, got)]
		print('%s (tempo swept live): n=%d min=%.2fms max=%.2fms underrun=%d min_fifo=%d' % (
			tag, len(vals), min(vals) / 150000, max(vals) / 150000, stall, MINLVL[0]))
	else:
		k = 6000 * SYS_HZ
		period = lambda j: (j * den * k) // (num * bpm)
		exp = set()
		for j in range(0, 64):
			exp.add(period(j + 1) - period(j))
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
	print('    load: %s%s' % (res, (' pauses=%d' % state[1]) if pause else ''))
