# Timing rig for PicoClock research. Runs from RAM via `mpremote run`; writes nothing
# to flash except the explicit flash-load test file, which it removes.
#
# Pins: GP0 = generated pulse, GP1 = capture input (jumpered to GP0).
# GP13-15 carry an RTC chip and are never touched.

import array
import gc
import machine
import os
import rp2
import time
import _thread

PIO0 = 0x50200000
FDEBUG = PIO0 + 0x008
RXF0 = PIO0 + 0x020
SYS_HZ = machine.freq()

RUN = [True]
STAT = array.array('i', [0, 0, 0, 0])


@rp2.asm_pio()
def capture():
	# Counts 2 system cycles per decrement between rising edges on the jmp pin.
	wrap_target()
	mov(x, invert(null))
	label("hi")
	jmp(x_dec, "hi2")
	label("hi2")
	jmp(pin, "hi")
	label("lo")
	jmp(pin, "got")
	jmp(x_dec, "lo")
	label("got")
	mov(isr, invert(x))
	push(noblock)
	wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)
def freerun():
	# Free-running pulse generator: period = OSR value + fixed overhead, reloaded each cycle.
	wrap_target()
	pull(noblock)
	mov(x, osr)
	mov(y, x)
	set(pins, 1) [31]
	set(pins, 0)
	label("d")
	jmp(y_dec, "d")
	wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_RIGHT, fifo_join=rp2.PIO.JOIN_TX)
def player():
	# Timeline player: each word = pin state (1 bit) + hold time (31 bits). Stalls on empty FIFO.
	wrap_target()
	pull(block)
	out(pins, 1)
	out(x, 31)
	label("d")
	jmp(x_dec, "d")
	wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, out_shiftdir=rp2.PIO.SHIFT_RIGHT, fifo_join=rp2.PIO.JOIN_TX)
def player_irq():
	# As player, but raises an interrupt each time it takes a word, so a handler can refill.
	wrap_target()
	pull(block)
	irq(rel(0))
	out(pins, 1)
	out(x, 31)
	label("d")
	jmp(x_dec, "d")
	wrap()


def cap_start(n, jmp_gpio):
	sm = rp2.StateMachine(1, capture, freq=SYS_HZ, jmp_pin=machine.Pin(jmp_gpio, machine.Pin.IN))
	buf = array.array('I', [0] * n)
	d = rp2.DMA()
	c = d.pack_ctrl(size=2, inc_read=False, inc_write=True, treq_sel=4 + 1)
	d.config(read=RXF0 + 4, write=buf, count=n, ctrl=c, trigger=True)
	sm.active(1)
	return sm, d, buf


def report(tag, buf, got, nominal_cycles):
	# Skip the first two samples (start-up partials).
	vals = [2 * buf[i] + 4 for i in range(2, got)]
	if not vals:
		print(tag, 'NO SAMPLES')
		return
	errs = [v - nominal_cycles for v in vals]
	mn = min(errs)
	mx = max(errs)
	mean = sum(errs) / len(errs)
	absmax = max(abs(mn), abs(mx))
	over_100us = sum(1 for e in errs if abs(e) > 15000)
	over_1ms = sum(1 for e in errs if abs(e) > 150000)
	srt = sorted(abs(e - mean) for e in errs)
	p50 = srt[len(srt) // 2]
	p99 = srt[int(len(srt) * 0.99)]
	us = lambda c: c / 150.0
	print('%s n=%d mean_err=%+.3fus min=%+.2fus max=%+.2fus |dev| p50=%.3fus p99=%.2fus  >100us:%d >1ms:%d' % (
		tag, len(vals), us(mean), us(mn), us(mx), us(p50), us(p99), over_100us, over_1ms))


# ---------------------------------------------------------------- loads (core 0)

def load_idle(done):
	while not done():
		time.sleep_ms(10)
	return 'idle'


def load_gc(done):
	keep = [bytearray(64) for _ in range(1800)]
	n = 0
	worst = 0
	while not done():
		junk = [str(i) for i in range(300)]
		t = time.ticks_us()
		gc.collect()
		dt = time.ticks_diff(time.ticks_us(), t)
		if dt > worst:
			worst = dt
		n += 1
	return 'gc runs=%d worst_collect=%.1fms heap_used=%dK' % (n, worst / 1000, gc.mem_alloc() // 1024)


def load_flash(done):
	data = bytes(4096)
	n = 0
	worst = 0
	while not done():
		t = time.ticks_us()
		with open('/_rigtest.bin', 'wb') as f:
			f.write(data)
		dt = time.ticks_diff(time.ticks_us(), t)
		if dt > worst:
			worst = dt
		n += 1
		time.sleep_ms(150)
	os.remove('/_rigtest.bin')
	return 'flash writes=%d worst_write=%.1fms' % (n, worst / 1000)


def load_flash_small(done):
	# A realistic settings save: rewrite a ~300 byte JSON-sized file.
	data = bytes(300)
	n = 0
	worst = 0
	while not done():
		t = time.ticks_us()
		with open('/_rigtest.bin', 'wb') as f:
			f.write(data)
		dt = time.ticks_diff(time.ticks_us(), t)
		if dt > worst:
			worst = dt
		n += 1
		time.sleep_ms(150)
	os.remove('/_rigtest.bin')
	return 'small flash writes=%d worst_write=%.1fms' % (n, worst / 1000)


def wifi_up():
	import network
	ap = network.WLAN(network.AP_IF)
	ap.config(ssid='PicoClockRig', password='rigtest123')
	ap.active(True)
	sta = network.WLAN(network.STA_IF)
	sta.active(True)
	return ap, sta


def load_wifi(done):
	ap, sta = wifi_up()
	n = 0
	worst = 0
	seen = 0
	while not done():
		t = time.ticks_ms()
		r = sta.scan()
		dt = time.ticks_diff(time.ticks_ms(), t)
		seen = len(r)
		if dt > worst:
			worst = dt
		n += 1
	return 'wifi ap=%s scans=%d worst_scan=%dms networks=%d' % (ap.active(), n, worst, seen)


def load_ble(done):
	import bluetooth
	ble = bluetooth.BLE()
	ble.active(True)
	ble.gap_advertise(100000, adv_data=b'\x02\x01\x06\x0d\x09PicoClockRig')
	while not done():
		time.sleep_ms(10)
	ble.gap_advertise(None)
	return 'ble advertising'


def load_all(done):
	# Everything at once: AP up, BLE advertising, GC churn, WiFi scans.
	import bluetooth
	ap, sta = wifi_up()
	ble = bluetooth.BLE()
	ble.active(True)
	ble.gap_advertise(100000, adv_data=b'\x02\x01\x06\x0d\x09PicoClockRig')
	keep = [bytearray(64) for _ in range(1200)]
	n = 0
	while not done():
		junk = [str(i) for i in range(300)]
		gc.collect()
		if n % 20 == 0:
			sta.scan()
		n += 1
	return 'all: ap+ble+gc+scan loops=%d heap_used=%dK' % (n, gc.mem_alloc() // 1024)


LOADS = {
	'idle': load_idle, 'gc': load_gc, 'flash': load_flash, 'flashsmall': load_flash_small,
	'wifi': load_wifi, 'ble': load_ble, 'all': load_all,
}


# ---------------------------------------------------------------- generators

def spin_thread(period_us):
	# Reference-algorithm port: absolute deadlines, busy-wait, on core 1.
	p = machine.Pin(0, machine.Pin.OUT, value=0)
	tu = time.ticks_us
	td = time.ticks_diff
	ta = time.ticks_add
	nxt = ta(tu(), period_us)
	last = tu()
	mg = 0
	while RUN[0]:
		now = tu()
		g = td(now, last)
		last = now
		if g > mg:
			mg = g
			STAT[0] = mg
		if td(nxt, now) <= 0:
			p(1)
			p(0)
			nxt = ta(nxt, period_us)


def feed_thread(sm, w_hi, w_lo):
	put = sm.put
	tu = time.ticks_us
	td = time.ticks_diff
	last = tu()
	mg = 0
	while RUN[0]:
		put(w_hi)
		put(w_lo)
		now = tu()
		g = td(now, last)
		last = now
		if g > mg:
			mg = g
			STAT[0] = mg


P0 = None
WORDS = array.array('I', [0, 0])
IDX = array.array('i', [0])


def timer_cb(t):
	P0(1)
	P0(0)


def irq_feed(sm):
	i = IDX[0]
	while sm.tx_fifo() < 8:
		sm.put(WORDS[i])
		i ^= 1
	IDX[0] = i


def run(mode, load, hz=200, n=2000, cap_gpio=1):
	global P0
	period_cycles = SYS_HZ // hz
	sm_cap, d, buf = cap_start(n, cap_gpio)
	t_end = time.ticks_add(time.ticks_ms(), int(n * 1000 / hz) + 6000)
	done = lambda: d.count == 0 or time.ticks_diff(t_end, time.ticks_ms()) < 0
	nominal = period_cycles
	extra = ''
	hi_cycles = 1500

	if mode == 'freerun':
		sm = rp2.StateMachine(0, freerun, freq=SYS_HZ, set_base=machine.Pin(0))
		# cycle = pull, mov, mov, set[31] (32), set, then y+1 jumps = 36 + y + 1
		sm.put(period_cycles - 37)
		sm.active(1)

	elif mode == 'spin':
		_thread.start_new_thread(spin_thread, (1000000 // hz,))

	elif mode in ('timer_hard', 'timer_soft'):
		P0 = machine.Pin(0, machine.Pin.OUT, value=0)
		tim = machine.Timer(freq=hz, mode=machine.Timer.PERIODIC, callback=timer_cb, hard=(mode == 'timer_hard'))

	elif mode in ('pio_core1', 'pio_core0', 'pio_irq'):
		prog = player_irq if mode == 'pio_irq' else player
		overhead = 5 if mode == 'pio_irq' else 4
		sm = rp2.StateMachine(0, prog, freq=SYS_HZ, out_base=machine.Pin(0))
		w_hi = 1 | ((hi_cycles - overhead) << 1)
		w_lo = 0 | ((period_cycles - hi_cycles - overhead) << 1)
		WORDS[0] = w_hi
		WORDS[1] = w_lo
		if mode == 'pio_core1':
			sm.active(1)
			_thread.start_new_thread(feed_thread, (sm, w_hi, w_lo))
		elif mode == 'pio_irq':
			sm.irq(irq_feed, hard=True)
			irq_feed(sm)
			sm.active(1)
		else:
			sm.active(1)
		time.sleep_ms(30)
		machine.mem32[FDEBUG] = 1 << 24

	if mode == 'pio_core0':
		# Feeder shares core 0 with the load: interleave by running the load in a thread instead.
		_thread.start_new_thread(feed_thread, (sm, w_hi, w_lo))

	res = LOADS[load](done)
	got = n - d.count
	RUN[0] = False

	if mode.startswith('pio'):
		stall = (machine.mem32[FDEBUG] >> 24) & 1
		extra = ' underrun=%d' % stall
	if mode in ('spin', 'pio_core1'):
		extra += ' core1_max_gap=%dus' % STAT[0]

	report('%-10s %-10s' % (mode, load), buf, got, nominal)
	print('    load: %s%s' % (res, extra))
