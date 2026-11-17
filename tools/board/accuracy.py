"""The Pico's half of measuring SynthSync's accuracy: play output 1 through a list of tempos, and time every one of its pulses at the pin.

`tools/accuracy.py` sends this to a Pico with SynthSync installed and runs it,
leaving nothing behind. It plays with SynthSync's own player, from the Pico's
own copy, but starts nothing else: the network and the page have no part in
the timing, which runs on the second core whatever the first is doing.

A state machine times the gap between each rising edge of output 1 and the
next, to a cycle of the 150 MHz clock, and DMA puts every gap in a ring that
the first core empties several times a second. So every pulse of the run is
timed, and the gaps add up to how long output 1 has been playing, by the
Pico's own crystal. The other outputs play too, so the player is as busy as in
use.

Every few seconds this waits for a pulse, and says how many pulses have come,
what their gaps add up to and how long ago that pulse was. The computer notes
when each report arrives, against network time, and the difference between
the two is how far the Pico's tempo is from true.

At the end of each tempo's stretch it says how that tempo's pulses kept time:
the gap furthest from what the tempo asks for, and how far all the stretch's
gaps together came from the stretch's ideal length.
"""

import array
import machine
import rp2
import time
import uctypes

import synthsync.device.board
import synthsync.device.player

# As many outputs play as config.txt says are wired. An install from before
# config.txt said so in its own code.
try:
	import synthsync.core.config
	_OUTPUTS = synthsync.core.config.read(synthsync.device.board.CONFIG).outputs

except ImportError:
	_OUTPUTS = synthsync.device.board.OUTPUTS

# The system clock, which the gaps are timed in, and how many of its cycles
# make one of the scheduler's ticks of a microsecond.
_HZ = machine.freq()
_CYCLES_PER_TICK = _HZ // 1000000

# PIO block 0, where the player is state machine 0 and the timing runs on 1.
_PIO0 = 0x50200000
_RXF0 = _PIO0 + 0x020
_TIMING_MACHINE = 1
_TIMING_REQUEST = 4 + _TIMING_MACHINE

# The ring the gaps go into: how many it holds, and the size in bytes as a
# power of two, which DMA wraps its writing at. At 300 BPM and 4 PPQN it holds
# fifty seconds of pulses, and it is emptied every tenth of a second.
_RING_WORDS = 1024
_RING_BITS = 12

# As many gaps as DMA can be asked for at once: weeks of them at the fastest tempo.
_ENDLESS = 0x0FFFFFFF

# The first two gaps are from before the timing had settled, and the first two
# after a change of tempo span the change, so they count towards the total
# but not towards judging a tempo.
_SKIP = 2

# One beat is this many of the scheduler's units, and one tick at a tempo of
# t hundredths of a BPM moves t of them.
_UNITS = 6000 * 1000000

# How long to wait for output 1 to pulse before a report, in milliseconds. Its
# longest gap, at 20 BPM and 4 PPQN, is 750 ms, so a pulse not seen in this
# long means the output has stopped, and the run ends saying so.
_PULSE_PATIENCE = 3000

# The pulses a beat of every output after the first, which play to keep the
# player as busy as in use.
_OTHERS = ((24, 1), (3, 2), (1, 4), (6, 1), (2, 1), (1, 2), (3, 1))


@rp2.asm_pio()
def _timing_program ():

	"""Count between rising edges on the jump pin, two cycles to a count. The rig's capture does the same."""

	wrap_target()
	mov(x, invert(null))
	label("high")
	jmp(x_dec, "still_high")
	label("still_high")
	jmp(pin, "high")
	label("low")
	jmp(pin, "risen")
	jmp(x_dec, "low")
	label("risen")
	mov(isr, invert(x))
	push(noblock)
	wrap()


class Ring:

	"""Times every gap between rising edges on one pin, for as long as it runs."""

	def __init__ (self, pin: int) -> None:

		"""Begin timing a pin. Make this before the player, which would otherwise lose the pin to it."""

		# DMA wraps at a boundary of the ring's own size, so the ring is the
		# part of a store twice as big that begins on one.
		size = _RING_WORDS * 4
		self.store = array.array("I", [0] * (2 * _RING_WORDS))
		address = uctypes.addressof(self.store)
		aligned = (address + size - 1) & ~(size - 1)
		self.base = (aligned - address) // 4

		self.machine = rp2.StateMachine(_TIMING_MACHINE, _timing_program, freq=_HZ, jmp_pin=machine.Pin(pin, machine.Pin.IN))
		self.dma = rp2.DMA()
		control = self.dma.pack_ctrl(size=2, inc_read=False, inc_write=True, ring_size=_RING_BITS, ring_sel=True, treq_sel=_TIMING_REQUEST)

		self.dma.config(read=_RXF0 + 4 * _TIMING_MACHINE, write=aligned, count=_ENDLESS, ctrl=control, trigger=True)
		self.machine.active(1)

	def done (self) -> int:

		"""Say how many gaps have been timed so far."""

		return _ENDLESS - self.dma.count

	def gap (self, number: int) -> int:

		"""Give one gap, by its number from the start, in cycles. It must be one of the last the ring holds."""

		# Each count is two cycles, and four more pass between one count and the next.
		return 2 * self.store[self.base + number % _RING_WORDS] + 4

	def close (self) -> None:

		"""Stop timing and give the hardware back."""

		self.machine.active(0)
		self.dma.close()


def gap_lengths (tempo: int, pulses: int, beats: int) -> tuple:

	"""Give the lengths, in cycles, an output's gaps take at a steady tempo, from wherever they are watched: the exact gap in ticks, rounded down and up.

	It is the scheduler's `gap_ticks`, worked out here so that this runs
	against an installed SynthSync older than that. A test holds the two to
	the same answers.
	"""

	whole, part = divmod(beats * _UNITS, pulses * tempo)

	return (whole * _CYCLES_PER_TICK, (whole + 1 if part else whole) * _CYCLES_PER_TICK)


class Stretch:

	"""One tempo's stretch of the run, and how its pulses kept time."""

	def __init__ (self, number: int, tempo: int, pulses: int, beats: int, first: int) -> None:

		"""Begin judging gaps from this one on, by its number."""

		self.number = number
		self.tempo = tempo
		self.pulses = pulses
		self.beats = beats
		self.first = first
		self.lengths = gap_lengths(tempo, pulses, beats)
		self.timed = 0
		self.cycles = 0
		self.worst = 0

	def take (self, number: int, cycles: int) -> None:

		"""Judge one gap, if it is one of this stretch's."""

		if number < self.first:
			return

		self.timed += 1
		self.cycles += cycles

		lengths = self.lengths
		error = abs(cycles - lengths[0])

		for length in lengths:
			if abs(cycles - length) < error:
				error = abs(cycles - length)

		if error > self.worst:
			self.worst = error

	def drift (self) -> int:

		"""Say how far the stretch's gaps together came from its ideal length, in cycles, rounded down."""

		scale = self.pulses * self.tempo

		return (self.cycles * scale - self.timed * self.beats * _UNITS * _CYCLES_PER_TICK) // scale

	def say (self) -> None:

		"""Print how the stretch went."""

		print("ACCURACY timed stretch=%d tempo=%d pulses=%d worst_cycles=%d drift_cycles=%d" % (self.number, self.tempo, self.timed, self.worst, self.drift()))


def measure (tempos: tuple, stretch_s: int, every_s: int, total_s: int, pulses: int = 4, beats: int = 1) -> None:

	"""Play output 1 at `pulses` to `beats` beats, through these tempos in turn, for `total_s` seconds, a stretch of `stretch_s` each, and report every `every_s` seconds.

	Tempos are in hundredths of a BPM. Output 1 is timed; the others play
	ratios of their own.
	"""

	first_pin = synthsync.device.board.FIRST_OUTPUT_PIN
	outputs = _OUTPUTS
	ring = Ring(first_pin)
	player = synthsync.device.player.Player(first_pin, outputs)

	player.set_tempo(tempos[0])
	player.set_ratio(0, pulses, beats)

	for output in range(1, outputs):
		player.set_ratio(output, _OTHERS[output - 1][0], _OTHERS[output - 1][1])

	player.settle()
	player.start()

	print("ACCURACY begin hz=%d pulses=%d beats=%d outputs=%d every_s=%d stretch_s=%d total_s=%d tempos=%s" % (_HZ, pulses, beats, outputs, every_s, stretch_s, total_s, ",".join(str(tempo) for tempo in tempos)))

	# How many gaps have been read from the ring, how many of them count
	# towards the total and what they add up to in cycles, and whether the
	# ring was ever left so long that it wrapped over gaps not yet read.
	read = 0
	edges = 0
	total = 0
	lost = 0
	stretch = Stretch(0, tempos[0], pulses, beats, _SKIP)
	reports = 0

	began = time.ticks_ms()
	report_due = 0
	change_due = stretch_s * 1000
	print("ACCURACY stretch n=0 tempo=%d" % tempos[0])

	while True:

		elapsed = time.ticks_diff(time.ticks_ms(), began)

		if elapsed >= total_s * 1000:
			break

		upto = ring.done()

		if elapsed >= report_due:

			# Wait for the next pulse, and note when it came by the Pico's own clock.
			waited = upto
			asked = time.ticks_ms()

			while upto == waited and time.ticks_diff(time.ticks_ms(), asked) < _PULSE_PATIENCE:
				upto = ring.done()

			pulsed = time.ticks_us()

			if upto == waited:
				print("ACCURACY stalled after_ms=%d" % elapsed)
				break

		# Gaps the ring wrapped over before they were read can't be added up, so
		# what output 1 has played can't be known from here on, and the run ends.
		if upto - read > _RING_WORDS:
			lost += upto - read - _RING_WORDS
			print("ACCURACY overrun lost=%d" % lost)
			break

		while read < upto:

			cycles = ring.gap(read)

			if read >= _SKIP:
				edges += 1
				total += cycles

			stretch.take(read, cycles)
			read += 1

		if elapsed >= report_due:

			# The total counts from the end of the first gaps skipped, so a pulse
			# before that has no place in it.
			if edges:
				reports += 1
				print("ACCURACY at n=%d edges=%d cycles=%d since_us=%d" % (reports, edges, total, time.ticks_diff(time.ticks_us(), pulsed)))

			report_due += every_s * 1000

		if elapsed >= change_due:

			stretch.say()

			number = stretch.number + 1
			tempo = tempos[number % len(tempos)]
			player.set_tempo(tempo)

			# The gap under way spans the change, and so may the one after it.
			stretch = Stretch(number, tempo, pulses, beats, ring.done() + _SKIP)
			print("ACCURACY stretch n=%d tempo=%d" % (number, tempo))
			change_due += stretch_s * 1000

		time.sleep_ms(20)

	stretch.say()

	status = player.status()
	player.close()
	ring.close()

	print("ACCURACY end edges=%d lost=%d faults=%d refused=%d fewest_waiting=%d slowest_command_us=%d empty=%d one_left=%d" % (edges, lost, status["faults"], status["refused"], status["fewest_waiting"], status["slowest_command"], status["empty"], status["one_left"]))
