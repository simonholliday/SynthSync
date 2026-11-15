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

That includes calling a function. MicroPython takes memory for an ordinary
function's working space each time it is called, unless the function is very
small, and gives it back afterwards. If the first core is collecting garbage
at that moment the call waits until it has finished, which was measured at 18
to 24 ms: ten times longer than eight words last when the outputs are busy.
A native function keeps its working space on the stack and never waits. So
every function the second core calls is native: the ones marked so here, and
the whole scheduler, which is compiled that way when it is installed.

A timing fault is the player running out of words, which would make everything
after it late. The hardware flags it and this counts it. It should never happen.

The start, pause and stop buttons are read here too, on the second core. The
first core can be held up for seconds at a time when the WiFi is poor, and a
stop pressed then would otherwise wait for it. They are looked at about once a
millisecond in time the second core has to spare: while the player is full,
or while nothing plays. Looking every millisecond regardless ran the player
dry with the outputs at their busiest. A press is told from a knock by the
same rule as everywhere else, and carried out by the scheduler as one from the
page is. The first core finds out afterwards, from `transport`, and tells the
pages.

One thing stops both cores: writing to the Pico's flash, which a save does. It
lasts about 40 ms, far longer than eight words. So before a write the second
core works out a third of a second of words in advance and hands them to DMA,
which is hardware too, and feeds the player by itself while the cores are held.

Commands get the same treatment on a smaller scale. A tempo change keeps the
second core busy for a millisecond or two, and a power-up or a reset sends a
dozen commands back to back, which is longer than eight words last when the
outputs are busy. So while the clock is playing, the second core first works
out enough words to last through the commands that are waiting, and DMA plays
those while it carries them out.
"""

import array
import machine
import micropython
import rp2
import time
import uctypes
import _thread

import picoclock.core.buttons
import picoclock.core.timeline
import picoclock.core.transport

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

# How much playing to put by before each kind of command is carried out while
# the clock is playing, in ticks. Each is about twice what the command was
# measured to take with the first core at its busiest: a tempo change 1.9 ms
# with four outputs, a ratio 0.6 ms, a role 0.4 ms, the rest 0.15 ms or less.
# A tempo change works through every output, so it is so much for each.
_COVER_TEMPO = 1000
_COVER_TEMPO_EACH = 600
_COVER_RATIO = 1200
_COVER_ROLE = 800
_COVER_SWITCH = 300
_COVER_TRANSPORT = 500

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
_PRESS = 11
_COMMANDS = 12

# The buttons the second core reads: start, pause and stop, in that order.
_BUTTONS = (picoclock.core.transport.START, picoclock.core.transport.PAUSE, picoclock.core.transport.STOP)

# The buttons are looked at in time the second core has to spare. When the
# outputs keep it so busy that the player is never full, they are looked at
# after this many words all the same. The time since the last look is counted
# as no more than this many milliseconds, so a knock caught by one late look
# can't pass for a press: a press has to be seen on four looks or more.
_LOOK_AFTER = 256
_LOOK_MOST = 5

# How many commands can wait at once. It must be a power of two.
_WAITING = 32

# The two counters that mark the command queue wrap here, well inside a small integer.
_WRAP = 0x3FFFFFFF


@micropython.viper
def _levels () -> int:

	"""Read every pin at once, GP0 to GP29, as the bits of one number. Runs on the second core.

	It reads the register itself, so nothing is asked of the memory. The bits
	above GP29 are left out, so the number stays small enough not to need any.
	"""

	return int(ptr32(0xD0000004)[0]) & 0x3FFFFFFF


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
		# player has had waiting while the clock ran, how many backlogs have
		# begun playing, and the longest one command has taken while the clock
		# was playing, in microseconds.
		self._counts = array.array("i", [0, 0, _DEPTH, 0, 0])

		# How much playing to put by for each command, by its number.
		self._cover = array.array("i", [0] * _COMMANDS)
		self._cover[_TEMPO] = _COVER_TEMPO + _COVER_TEMPO_EACH * outputs
		self._cover[_RATIO] = _COVER_RATIO
		self._cover[_ROLE] = _COVER_ROLE
		self._cover[_MUTE] = _COVER_SWITCH
		self._cover[_WIDTH] = _COVER_SWITCH

		for command in (_START, _PAUSE, _RESUME, _STOP, _PRESS):
			self._cover[command] = _COVER_TRANSPORT

		# The buttons the second core reads, each with the pin it is on or -1 for
		# none, the level that means pressed, the press it makes, and what tells a
		# press from a knock.
		self._button_pins = array.array("i", [-1] * len(_BUTTONS))
		self._button_levels = array.array("i", [0] * len(_BUTTONS))
		self._button_presses = array.array("i", [picoclock.core.transport.NUMBERS[name] for name in _BUTTONS])
		self._buttons = [picoclock.core.buttons.Button() for _ in _BUTTONS]

		# What the clock is doing, as the second core last left it: 1 if running,
		# plus 2 if paused. Then how many presses of the buttons it has carried
		# out, and the time in milliseconds when it carried out the last.
		self._state = array.array("i", [0])
		self._pressed = array.array("i", [0, 0])

		# Words worked out ahead of a flash write, and the DMA channel that plays them.
		self._backlog = array.array("I", [0] * _BACKLOG_WORDS)
		self._backlog_at = uctypes.addressof(self._backlog)
		self._dma = rp2.DMA()
		self._dma_control = self._dma.pack_ctrl(size=2, inc_read=True, inc_write=False, treq_sel=0)

		# What the second core calls to feed the player, each taken out once here.
		# Taking a method out under a name of its own makes an object, which asks
		# for memory, and the second core must never ask: it would wait for any
		# collection the first core was part way through. With the page and the
		# network loaded, one can last longer than the player's eight words.
		self._put = self.machine.put
		self._waiting = self.machine.tx_fifo
		self._next_word = self.timeline.next_word
		self._quiet = self.timeline.quiet

		self._alive = True

		self.machine.active(1)

		# Native functions keep their working space on the stack, so the second core is given plenty.
		_thread.stack_size(16 * 1024)
		_thread.start_new_thread(self._feed, ())

	# -----------------------------------------------------------------------
	# The second core.

	@micropython.native
	def _feed (self) -> None:

		"""Keep the player in words, and take commands between them. Runs on the second core."""

		timeline = self.timeline
		player = self.machine
		put = self._put
		waiting = self._waiting
		next_word = self._next_word
		quiet = self._quiet
		marks = self._marks
		counts = self._counts
		registers = machine.mem32
		ticks_ms = time.ticks_ms
		ticks_diff = time.ticks_diff
		looked = ticks_ms()
		since = 0
		level = _DEPTH
		playing = False

		while self._alive:

			# The buttons, in time to spare, or after so many words if there is none.
			if not playing or level >= _DEPTH or since >= _LOOK_AFTER:

				now = ticks_ms()

				if now != looked:
					self._look(min(ticks_diff(now, looked), _LOOK_MOST), playing)
					looked = now
					since = 0

			if marks[0] != marks[1]:

				if playing:
					self._take_under_cover()
				else:
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
					since += 1

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

	@micropython.native
	def _take_under_cover (self) -> None:

		"""Carry out the waiting commands while the clock is playing. Runs on the second core.

		Commands can take longer than the player's eight words last, so enough
		words to last through them are worked out first, and DMA plays those
		while the commands are carried out. Without it, a burst of commands
		among busy outputs ran the player dry hundreds of times.
		"""

		marks = self._marks
		queue = self._queue
		cover = self._cover
		waiting = (marks[0] - marks[1]) & _WRAP
		count = 0
		ticks = 0

		# A backlog for a flash write is its own cover, so stop short of one.
		while count < waiting:

			what = queue[((marks[1] + count) % _WAITING) * 4]

			if what == _BACKLOG:
				break

			if 0 < what < _COMMANDS:
				ticks += cover[what]

			count += 1

		if count == 0:
			self._take()
			return

		self._build(ticks)

		counts = self._counts
		ticks_us = time.ticks_us
		ticks_diff = time.ticks_diff

		while count:

			began = ticks_us()
			self._take()
			took = ticks_diff(ticks_us(), began)

			# Kept so that a command held up by something unforeseen shows, even when the words put by covered for it.
			if took > counts[4]:
				counts[4] = took

			count -= 1

		self._play_out()

	@micropython.native
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
			elif what == _PRESS:
				timeline.press(first)

		except Exception:
			# A value the scheduler won't take. Count it rather than end the thread.
			self._counts[1] += 1

		# Said before the command is struck off, so the first core never sees
		# the queue empty and the clock as it was before.
		self._note_state()
		marks[1] = (marks[1] + 1) & _WRAP

	@micropython.native
	def _note_state (self) -> None:

		"""Leave word for the first core of what the clock is now doing. Runs on the second core."""

		timeline = self.timeline
		self._state[0] = (1 if timeline.running else 0) + (2 if timeline.paused else 0)

	@micropython.native
	def _look (self, elapsed: int, playing: bool) -> None:

		"""Read the buttons, and carry out any press that has just begun. Runs on the second core.

		`elapsed` is how long since the last look, in milliseconds, and
		`playing` whether the player is being fed.
		"""

		pins = self._button_pins
		levels = self._button_levels
		presses = self._button_presses
		buttons = self._buttons
		read = _levels()

		for index in range(len(buttons)):

			pin = pins[index]

			if pin < 0:
				continue

			if buttons[index].look(((read >> pin) & 1) == levels[index], 0, elapsed):
				self._act(presses[index], playing)

	@micropython.native
	def _act (self, press: int, playing: bool) -> None:

		"""Carry out a press of one of the buttons, as one from the page is. Runs on the second core."""

		if playing:
			self._build(self._cover[_PRESS])

		self.timeline.press(press)

		if playing:
			self._play_out()

		self._note_state()

		pressed = self._pressed
		pressed[0] = (pressed[0] + 1) & _WRAP
		pressed[1] = time.ticks_ms()

	@micropython.native
	def _ahead (self, ticks: int) -> None:

		"""Work out this many ticks of words in advance and let DMA play them all. Runs on the second core.

		This is what a flash write waits for: the first core may write as soon
		as the backlog has begun to play.
		"""

		self._build(ticks)

		# The first core is waiting for this count to move.
		self._counts[3] = (self._counts[3] + 1) & _WRAP

		self._play_out()

	@micropython.native
	def _build (self, ticks: int) -> None:

		"""Work out this many ticks of words in advance and start DMA playing them. Runs on the second core.

		The player has to be kept fed while the backlog is built: working out a
		third of a second of a busy pattern takes longer than its eight words
		last. So the player comes first. Whenever it has room it is given the
		oldest word of the backlog, or a new one straight from the scheduler
		if the backlog is empty, which is exactly what simply playing does.
		Only while the player is full does a new word go on the end of the
		backlog. What's left when the backlog is long enough is handed to DMA.
		Nothing more may be fed by hand until `_play_out` has seen DMA finish.

		When the outputs are so busy that the scheduler can only just keep up,
		the backlog grows slowly or not at all. The building stops after as
		many words as a backlog can hold, and goes ahead with what it has.
		"""

		timeline = self.timeline
		backlog = self._backlog
		put = self._put
		waiting = self._waiting
		next_word = self._next_word
		counts = self._counts
		bits = timeline.pin_bits
		cycles = timeline.cycles_per_tick
		overhead = timeline.overhead_cycles
		head = 0
		tail = 0
		span = 0
		made = 0

		while span < ticks and made < _BACKLOG_WORDS:

			level = waiting()

			if level < _DEPTH:

				if level < counts[2]:
					counts[2] = level

				if head < tail:
					word = backlog[head]
					head += 1
					span -= ((word >> bits) + overhead) // cycles
				else:
					word = next_word()
					made += 1

				put(word)

			else:
				word = next_word()
				made += 1
				backlog[tail] = word
				tail += 1
				span += ((word >> bits) + overhead) // cycles

		if head < tail:
			self._dma.config(read=self._backlog_at + 4 * head, write=_TXF0, count=tail - head, ctrl=self._dma_control, trigger=True)

	@micropython.native
	def _play_out (self) -> None:

		"""Wait until DMA has given the player the whole backlog. Runs on the second core."""

		dma = self._dma

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

	def press (self, command: str) -> None:

		"""Start, pause or stop, as the transport's table says for whatever the clock is doing when the second core takes it."""

		self._send(_PRESS, picoclock.core.transport.NUMBERS[command])

	def transport (self) -> "str | None":

		"""Say whether the clock is stopped, running or paused, or None while a command is still waiting for the second core."""

		if self._marks[0] != self._marks[1]:
			return None

		state = self._state[0]

		return picoclock.core.transport.state_of(state & 1 != 0, state & 2 != 0)

	def listen (self, command: str, pin: int, low: bool = True) -> None:

		"""Have the second core read the button for start, pause or stop on this pin, or no button at all with a pin of -1.

		`low` is whether the pin reads low when pressed. A button to ground
		works against the Pico's own pull-up. The Pico 2's own pull-down can't
		bring a pin low again once it has been high, so an input pressed high
		needs a resistor of 8.2 kΩ or less from the pin to ground.
		"""

		index = _BUTTONS.index(command)

		# The second core leaves the button alone while it is changed, and then
		# begins again with it not pressed. A look caught halfway sees at most a
		# millisecond of a change, and a press takes twenty.
		self._button_pins[index] = -1

		if pin < 0:
			return

		machine.Pin(pin, machine.Pin.IN, machine.Pin.PULL_UP if low else machine.Pin.PULL_DOWN)

		self._buttons[index] = picoclock.core.buttons.Button()
		self._button_levels[index] = 0 if low else 1
		self._button_pins[index] = pin

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

	def position (self) -> int:

		"""Say how far the clock has played, in thousandths of a beat, or -1 when that can't be said just now."""

		# While a backlog plays, the scheduler is as much as a third of a second
		# ahead of the pins, so where it has got to isn't where the clock is.
		return -1 if self._dma.count else self.timeline.position()

	def status (self) -> dict:

		"""Say how the clock is doing.

		`position` is how far the clock has played, in thousandths of a beat,
		or -1 when that can't be said just now. `faults` is how many times the
		player ran out of words, and should stay at zero. `refused` is how many
		commands the scheduler wouldn't take. `fewest_waiting` is the least the
		player has had in hand, out of eight. `slowest_command` is the longest
		one command has kept the second core while the clock was playing, in
		microseconds: a few thousand at most, and ten times that if a call on
		the second core has waited for the first to collect its garbage.
		`presses` is how many presses of the buttons the second core has
		carried out, and `pressed_at` the time of the last, by `time.ticks_ms`.
		"""

		timeline = self.timeline

		return {
			"running": timeline.running,
			"paused": timeline.paused,
			"beat": timeline.beat,
			"position": self.position(),
			"faults": self._counts[0],
			"refused": self._counts[1],
			"fewest_waiting": self._counts[2],
			"slowest_command": self._counts[4],
			"merged": timeline.merged,
			"presses": self._pressed[0],
			"pressed_at": self._pressed[1],
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
