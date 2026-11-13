"""The player: turns the scheduler's words into edges on the pins.

A small program runs in the Pico's PIO hardware. It takes a word, sets the
output pins from it, holds them for as many clock cycles as the word says, and
takes the next. Every pin changes on the same cycle, and nothing the rest of
the program does can make an edge early or late.

The words come from the scheduler, which runs here on the Pico's second core
and does nothing else. It keeps the player eight words ahead. The first core,
which runs the web page, the radio and everything else, never touches the
scheduler: it leaves short commands in a queue for the second core to take
between words.

Everything the second core touches is made in advance and held by this object,
and none of it asks for memory, so the first core collecting its garbage can't
hold a pulse up.

A timing fault is the player running out of words, which would make everything
after it late. The hardware flags it and this counts it. It should never happen.

One thing stops both cores: writing to the Pico's flash, which a save does. It
lasts about 40 ms, far longer than eight words. So before a write the second
core works out a third of a second of words in advance and hands them to DMA,
which is hardware too, and feeds the player by itself while the cores are held.
"""

import array
import machine
import rp2
import time
import uctypes
import _thread

import picoclock.core.timeline

# PIO block 0, where the player is state machine 0. The radio owns a state
# machine in block 2, so block 0 is clear.
_FDEBUG = 0x50200000 + 0x008

# The bit in FDEBUG that says state machine 0 wanted a word and found none.
_RAN_DRY = 1 << 24

# Where a word is written to reach state machine 0, for DMA to write to.
_TXF0 = 0x50200000 + 0x010

# How many words the player's queue holds.
_DEPTH = 8

# How many words a backlog can hold, and how long one lasts unless asked otherwise, in ticks.
_BACKLOG_WORDS = 2048
_BACKLOG_TICKS = 300000

# The commands the first core can leave for the second.
_TEMPO = 1
_RATIO = 2
_MUTE = 3
_WIDTH = 4
_ROLE = 5
_START = 6
_PAUSE = 7
_RESUME = 8
_STOP = 9
_BACKLOG = 10

# How many commands can wait at once. It must be a power of two.
_WAITING = 32

# The two counters that mark the command queue wrap here, well inside a small integer.
_WRAP = 0x3FFFFFFF


def _program (outputs: int):

	"""Build the player's PIO program for this many output pins."""

	def player ():

		# A word is the pins in its low 9 bits and the hold above them. One turn
		# takes the hold plus 4 cycles, which the scheduler allows for.
		wrap_target()
		pull(block)
		out(pins, 9)
		out(x, 23)
		label("hold")
		jmp(x_dec, "hold")
		wrap()

	return rp2.asm_pio(
		out_init = (rp2.PIO.OUT_LOW,) * outputs,
		out_shiftdir = rp2.PIO.SHIFT_RIGHT,
		fifo_join = rp2.PIO.JOIN_TX,
	)(player)


class Player:

	"""Plays the clock on the output pins.

	Make one, and it starts the second core feeding the hardware. It begins
	stopped. The methods here are for the first core: each leaves a command for
	the second core and returns at once. Values must already have been checked,
	as `picoclock.core.settings` does.
	"""

	def __init__ (self, first_pin: int, outputs: int) -> None:

		"""Set up the pins, the hardware player and the second core."""

		cycles = machine.freq() // 1000000

		if cycles * 1000000 != machine.freq():
			raise ValueError("The player needs a system clock that is a whole number of megahertz")

		self.timeline = picoclock.core.timeline.Timeline(outputs, cycles_per_tick=cycles)
		self.machine = rp2.StateMachine(0, _program(outputs), freq=machine.freq(), out_base=machine.Pin(first_pin))

		# Commands waiting for the second core: four numbers each.
		self._queue = array.array("i", [0] * (_WAITING * 4))

		# How many commands have been left, and how many taken.
		self._marks = array.array("i", [0, 0])

		# Timing faults, commands the scheduler refused, the fewest words the
		# player has had waiting while the clock ran, and how many backlogs have
		# begun playing.
		self._counts = array.array("i", [0, 0, _DEPTH, 0])

		# Words worked out ahead of a flash write, and the DMA channel that plays them.
		self._backlog = array.array("I", [0] * _BACKLOG_WORDS)
		self._backlog_at = uctypes.addressof(self._backlog)
		self._dma = rp2.DMA()
		self._dma_control = self._dma.pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0)

		self._alive = True

		self.machine.active(1)

		_thread.stack_size(8 * 1024)
		_thread.start_new_thread(self._feed, ())

	# -----------------------------------------------------------------------
	# The second core.

	def _feed (self) -> None:

		"""Keep the player in words, and take commands between them. Runs on the second core."""

		timeline = self.timeline
		player = self.machine
		put = player.put
		waiting = player.tx_fifo
		next_word = timeline.next_word
		quiet = timeline.quiet
		marks = self._marks
		counts = self._counts
		registers = machine.mem32
		playing = False

		while self._alive:

			if marks[0] != marks[1]:
				self._take()

			if playing:

				if not (timeline.running and not timeline.paused) and quiet():
					# Nothing left to play. One last word leaves every pin low, and
					# then the player is allowed to run dry.
					put(next_word())
					playing = False
					continue

				level = waiting()

				if level < _DEPTH:

					put(next_word())

					if level < counts[2]:
						counts[2] = level

				if registers[_FDEBUG] & _RAN_DRY:
					counts[0] += 1
					registers[_FDEBUG] = _RAN_DRY

			elif (timeline.running and not timeline.paused) or not quiet():

				# Something to play again. Fill the queue with the player held, so it
				# sets off with a full queue rather than one word.
				player.active(0)

				while waiting() < _DEPTH:
					put(next_word())

				registers[_FDEBUG] = _RAN_DRY
				player.active(1)
				playing = True

	def _take (self) -> None:

		"""Carry out the oldest waiting command. Runs on the second core."""

		marks = self._marks
		queue = self._queue
		timeline = self.timeline
		at = (marks[1] % _WAITING) * 4
		what = queue[at]
		first = queue[at + 1]
		second = queue[at + 2]
		third = queue[at + 3]

		try:

			if what == _TEMPO:
				timeline.set_tempo(first)
			elif what == _RATIO:
				timeline.set_ratio(first, second, third)
			elif what == _MUTE:
				timeline.set_mute(first, second)
			elif what == _WIDTH:
				timeline.set_width(first, second)
			elif what == _ROLE:
				timeline.set_role(first, second)
			elif what == _START:
				timeline.start()
			elif what == _PAUSE:
				timeline.pause()
			elif what == _RESUME:
				timeline.resume()
			elif what == _STOP:
				timeline.stop()
			elif what == _BACKLOG:
				self._ahead(first)

		except Exception:
			# A value the scheduler won't take. Count it rather than end the thread.
			self._counts[1] += 1

		marks[1] = (marks[1] + 1) & _WRAP

	def _ahead (self, ticks: int) -> None:

		"""Work out this many ticks of words in advance and let DMA play them. Runs on the second core.

		The player has to be kept fed while the backlog is built: working out a
		third of a second of a busy pattern takes longer than its eight words
		last. So each word made goes on the end of the backlog, and the oldest
		go to the player whenever it has room. What's left when the backlog is
		long enough is handed to DMA.
		"""

		timeline = self.timeline
		backlog = self._backlog
		put = self.machine.put
		waiting = self.machine.tx_fifo
		next_word = timeline.next_word
		bits = timeline.pin_bits
		cycles = timeline.cycles_per_tick
		overhead = timeline.overhead_cycles
		head = 0
		tail = 0
		span = 0

		while span < ticks and tail < _BACKLOG_WORDS:

			if head < tail and waiting() < _DEPTH:
				word = backlog[head]
				head += 1
				put(word)
				span -= ((word >> bits) + overhead) // cycles

			else:
				word = next_word()
				backlog[tail] = word
				tail += 1
				span += ((word >> bits) + overhead) // cycles

		dma = self._dma
		dma.config(read=self._backlog_at + 4 * head, write=_TXF0, count=tail - head, ctrl=self._dma_control, trigger=True)

		# The first core may write now: it is waiting for this count to move.
		# Nothing more is fed until DMA has finished.
		self._counts[3] = (self._counts[3] + 1) & _WRAP

		while dma.count > 0:
			pass

	# -----------------------------------------------------------------------
	# The first core.

	def _send (self, what: int, first: int = 0, second: int = 0, third: int = 0) -> None:

		"""Leave a command for the second core."""

		marks = self._marks
		queue = self._queue
		tries = 0

		# The second core takes a command between every two words, so a full
		# queue clears within milliseconds.
		while ((marks[0] - marks[1]) & _WRAP) >= _WAITING - 1:

			tries += 1

			if tries > 500:
				raise OSError("The clock isn't taking commands")

			time.sleep_ms(1)

		at = (marks[0] % _WAITING) * 4
		queue[at] = what
		queue[at + 1] = first
		queue[at + 2] = second
		queue[at + 3] = third
		marks[0] = (marks[0] + 1) & _WRAP

	def set_tempo (self, tempo: int) -> None:

		"""Change the tempo, in hundredths of a BPM."""

		self._send(_TEMPO, tempo)

	def set_ratio (self, output: int, pulses: int, beats: int) -> None:

		"""Give an output a new ratio: this many pulses to this many beats."""

		self._send(_RATIO, output, pulses, beats)

	def set_mute (self, output: int, muted: bool) -> None:

		"""Silence an output, or let it sound again."""

		self._send(_MUTE, output, 1 if muted else 0)

	def set_width (self, output: int, ticks: int) -> None:

		"""Set how long an output's pulses last, in microseconds, or 0 for half the gap."""

		self._send(_WIDTH, output, ticks)

	def set_role (self, output: int, role: int) -> None:

		"""Say what an output is for, as one of the scheduler's role numbers."""

		self._send(_ROLE, output, role)

	def start (self) -> None:

		"""Begin at zero."""

		self._send(_START)

	def pause (self) -> None:

		"""Hold the clock where it is."""

		self._send(_PAUSE)

	def resume (self) -> None:

		"""Carry on from a pause."""

		self._send(_RESUME)

	def stop (self) -> None:

		"""Set every output low and go back to zero."""

		self._send(_STOP)

	def protect (self, write, ticks: int = _BACKLOG_TICKS):

		"""Do something that writes to flash without disturbing the clock, and give back its answer.

		`write` is called once the player has a backlog to live on. It should
		finish well inside the backlog's length: a write measured here takes
		about 45 ms, and up to twice that when the filesystem has more to do.
		Changes made meanwhile wait until the backlog has played out.
		"""

		# Wait for a backlog made for this write to begin playing. One still
		# playing from an earlier write won't do: it may be nearly over.
		begun = self._counts[3]

		self._send(_BACKLOG, ticks)

		deadline = time.ticks_add(time.ticks_ms(), 1000)

		while self._counts[3] == begun:

			# If the second core never answers, the clock is already in trouble,
			# and the write goes ahead anyway.
			if time.ticks_diff(deadline, time.ticks_ms()) < 0:
				break

			time.sleep_ms(1)

		return write()

	def settle (self, milliseconds: int = 200) -> bool:

		"""Wait until every command left so far has been taken, and say whether it was in time."""

		marks = self._marks
		deadline = time.ticks_add(time.ticks_ms(), milliseconds)

		while marks[0] != marks[1]:

			if time.ticks_diff(deadline, time.ticks_ms()) < 0:
				return False

			time.sleep_ms(1)

		return True

	def status (self) -> dict:

		"""Say how the clock is doing.

		`faults` is how many times the player ran out of words, and should stay
		at zero. `refused` is how many commands the scheduler wouldn't take.
		`fewest_waiting` is the least the player has had in hand, out of eight.
		"""

		timeline = self.timeline

		return {
			"running": timeline.running,
			"paused": timeline.paused,
			"beat": timeline.beat,
			"faults": self._counts[0],
			"refused": self._counts[1],
			"fewest_waiting": self._counts[2],
			"merged": timeline.merged,
		}

	def close (self) -> None:

		"""Stop the second core and the hardware player, leaving every pin low."""

		self.stop()
		self.settle()
		time.sleep_ms(120)

		self._alive = False
		time.sleep_ms(5)
		self.machine.active(0)
		self._dma.close()
