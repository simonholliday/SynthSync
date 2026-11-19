"""The second core's word maker: `Timeline.next_word` written for viper, on the timeline's table.

`synthsync.core.timeline` is the definition of what each word is. This copy
does the same arithmetic in the same order, but MicroPython's viper compiles it
to machine instructions working on plain machine words, with no names to look
up and no objects made, so a word takes about a tenth of the time. That is what
lets eight busy outputs keep time.

Each number is read through `shift`. On the Pico a machine word is 32 bits,
the table's own size, so a -1, which the timeline uses for "not running" and
"low", reads as -1 and the shift is none. Where a machine word is 64 bits, as on
the laptop the tests run viper on, the same read would give 4,294,967,295 and
pass every `> 0` below, so there the shift brings the sign back.

It reads and writes nothing but the table a `Timeline` keeps, in the places
`synthsync.core.timeline` gives as `CELL_` and `ROW_` numbers, and it imports
nothing of SynthSync's. Every number it uses is written out here
as a constant: `tests/test_word.py` holds each one to the timeline's, and runs
this beside `next_word`, word for word, on the laptop's MicroPython, as the
rig's `word` test does on the Pico.
"""

import micropython
import sys

# Where the timeline keeps each number, as synthsync.core.timeline numbers them.
# A constant's name starting with an underscore is written into the code where
# it is used, so none of them is looked up as a word is made.
_SLOTS = const(0)
_MIDI_SLOT = const(1)
_CYCLES_PER_TICK = const(2)
_OVERHEAD_CYCLES = const(3)
_PIN_BITS = const(4)
_MASK = const(5)
_LED_FALL = const(6)
_LED_WIDTH = const(7)
_BEAT = const(8)
_MERGED = const(9)
_MIDI_ON = const(10)
_MIDI_MODE = const(11)
_MIDI_RUN = const(12)
_MIDI_PATTERN = const(13)
_MIDI_FREE = const(14)
_MIDI_QUEUED = const(15)
_MIDI_DUE = const(16)
_MIDI_IDLE = const(17)
_MIDI_IDLE_PART = const(18)
_MIDI_WAITING = const(19)
_HEAD = const(24)

_ROW_UNTIL = const(0)
_ROW_UNTIL_PART = const(1)
_ROW_FALL = const(2)
_ROW_PERIOD = const(3)
_ROW_PERIOD_PART = const(4)
_ROW_DIVISOR = const(5)
_ROW_WIDTH = const(6)
_ROW_MUTED = const(7)
_ROW_BIT = const(8)

# The timeline's own numbers.
_HOLD_LIMIT = const(2500)
_LED_BIT = const(256)
_MIDI_BIT = const(512)
_MIDI_BIT_TICKS = const(32)
_MIDI_BYTE_TICKS = const(320)
_MIDI_IDLE_HOLD = const(400)
_MIDI_CLOCK = const(4)

# How far to shift a number read from the table to bring its sign back: none
# where a machine word is the table's 32 bits.
_SIGN_SHIFT = 0 if sys.maxsize < 2 ** 32 else 32


@micropython.viper
def next_word (table: ptr32) -> int:

	"""Give the next word for the player from a timeline's table, and move the table on, exactly as `Timeline.next_word` does."""

	shift = int(_SIGN_SHIFT)
	slots = ((int(table[_SLOTS]) << shift) >> shift)
	midi_slot = ((int(table[_MIDI_SLOT]) << shift) >> shift)
	last = slots - 1

	# Where each row begins.
	until = _HEAD + _ROW_UNTIL * slots
	until_part = _HEAD + _ROW_UNTIL_PART * slots
	fall = _HEAD + _ROW_FALL * slots
	period = _HEAD + _ROW_PERIOD * slots
	period_part = _HEAD + _ROW_PERIOD_PART * slots
	divisor = _HEAD + _ROW_DIVISOR * slots
	width = _HEAD + _ROW_WIDTH * slots
	muted = _HEAD + _ROW_MUTED * slots
	bit = _HEAD + _ROW_BIT * slots

	hold = _HOLD_LIMIT
	mask = ((int(table[_MASK]) << shift) >> shift)
	led_fall = ((int(table[_LED_FALL]) << shift) >> shift)
	due = ((int(table[_MIDI_DUE]) << shift) >> shift)

	# The tempo LED goes out before anything rises, as each slot's pin does.
	if led_fall == 0:
		mask &= ~_LED_BIT
		led_fall = -1

	slot = 0

	while slot < slots:

		drop = ((int(table[fall + slot]) << shift) >> shift)

		if drop == 0:
			mask &= ~((int(table[bit + slot]) << shift) >> shift)
			drop = -1
			table[fall + slot] = -1

		wait = ((int(table[until + slot]) << shift) >> shift)

		if wait == 0:

			# This slot's pulse is due. Its next one is one gap later, and the
			# part carries over so the fraction is never lost.
			part = ((int(table[until_part + slot]) << shift) >> shift) + ((int(table[period_part + slot]) << shift) >> shift)
			wait = ((int(table[period + slot]) << shift) >> shift)
			whole = ((int(table[divisor + slot]) << shift) >> shift)

			if part >= whole:
				part -= whole
				wait += 1

			table[until_part + slot] = part
			table[until + slot] = wait

			if slot == last:
				table[_BEAT] += 1

				# Every beat lights the tempo LED, whatever the outputs are doing.
				mask |= _LED_BIT
				led_fall = ((int(table[_LED_WIDTH]) << shift) >> shift)

			elif slot == midi_slot:
				# The grid's next place wants a clock byte of its own.
				due += 1

			elif not ((int(table[muted + slot]) << shift) >> shift):

				pin = ((int(table[bit + slot]) << shift) >> shift)

				if mask & pin:
					table[_MERGED] += 1

				mask |= pin
				drop = ((int(table[width + slot]) << shift) >> shift)

				# Half the gap when asked for, or when the pulse wouldn't fit.
				if drop == 0 or drop * 2 > wait:
					drop = wait >> 1

				if drop < 1:
					drop = 1

				table[fall + slot] = drop

		if wait > 0 and wait < hold:
			hold = wait

		if drop > 0 and drop < hold:
			hold = drop

		slot += 1

	if led_fall > 0 and led_fall < hold:
		hold = led_fall

	if ((int(table[_MIDI_ON]) << shift) >> shift):

		run = ((int(table[_MIDI_RUN]) << shift) >> shift)
		free = ((int(table[_MIDI_FREE]) << shift) >> shift)
		idle = ((int(table[_MIDI_IDLE]) << shift) >> shift)
		queued = ((int(table[_MIDI_QUEUED]) << shift) >> shift)
		wait = ((int(table[until + midi_slot]) << shift) >> shift)

		# The pin's next change within the byte going out.
		if run == 0:

			mask ^= _MIDI_BIT
			pattern = ((int(table[_MIDI_PATTERN]) << shift) >> shift)

			if pattern:
				run = (pattern & 15) * _MIDI_BIT_TICKS
				table[_MIDI_PATTERN] = pattern >> 4
			else:
				run = -1

		# With the line free, the next byte: whatever is waiting comes first.
		if free == 0:

			byte = 0

			if queued:

				byte = ((int(table[_MIDI_WAITING]) << shift) >> shift)
				queued -= 1
				place = 0

				while place < queued:
					table[_MIDI_WAITING + place] = ((int(table[_MIDI_WAITING + place + 1]) << shift) >> shift)
					place += 1

			elif due > 1 or (due == 1 and wait >= 0 and wait <= _MIDI_BYTE_TICKS):
				byte = _MIDI_CLOCK
				due -= 1

			elif idle == 0:

				byte = _MIDI_CLOCK
				part = ((int(table[_MIDI_IDLE_PART]) << shift) >> shift) + ((int(table[period_part + midi_slot]) << shift) >> shift)
				idle = ((int(table[period + midi_slot]) << shift) >> shift)

				if part >= ((int(table[divisor + midi_slot]) << shift) >> shift):
					part -= ((int(table[divisor + midi_slot]) << shift) >> shift)
					idle += 1

				table[_MIDI_IDLE_PART] = part

			if byte:
				mask |= _MIDI_BIT
				run = (byte & 15) * _MIDI_BIT_TICKS
				table[_MIDI_PATTERN] = byte >> 4
				free = _MIDI_BYTE_TICKS

		if run > 0 and run < hold:
			hold = run

		if free > 0 and free < hold:
			hold = free

		if due == 1 and wait > _MIDI_BYTE_TICKS and wait - _MIDI_BYTE_TICKS < hold:
			hold = wait - _MIDI_BYTE_TICKS

		if idle >= 0:

			if idle > 0 and idle < hold:
				hold = idle

			if hold > _MIDI_IDLE_HOLD:
				hold = _MIDI_IDLE_HOLD

			if idle > 0:
				idle -= hold

		if run > 0:
			run -= hold

		if free > 0:
			free -= hold

		table[_MIDI_RUN] = run
		table[_MIDI_FREE] = free
		table[_MIDI_IDLE] = idle
		table[_MIDI_QUEUED] = queued

		if not ((int(table[_MIDI_MODE]) << shift) >> shift) and run < 0 and not queued:
			table[_MIDI_ON] = 0
			table[_MIDI_FREE] = 0

	table[_MIDI_DUE] = due

	slot = 0

	while slot < slots:

		wait = ((int(table[until + slot]) << shift) >> shift)

		if wait > 0:
			table[until + slot] = wait - hold

		drop = ((int(table[fall + slot]) << shift) >> shift)

		if drop > 0:
			table[fall + slot] = drop - hold

		slot += 1

	if led_fall > 0:
		led_fall -= hold

	table[_LED_FALL] = led_fall
	table[_MASK] = mask

	return mask | ((hold * ((int(table[_CYCLES_PER_TICK]) << shift) >> shift) - ((int(table[_OVERHEAD_CYCLES]) << shift) >> shift)) << ((int(table[_PIN_BITS]) << shift) >> shift))
