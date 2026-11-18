"""The scheduler: which pins change, and when.

Every output is locked to one timeline that starts at zero when the clock
starts. An output with a ratio of n pulses to d beats pulses whenever the beat
count is a whole multiple of d / n, so outputs that should line up do, on the
very same instant, and changing a ratio puts an output straight onto its new
grid.

The scheduler doesn't touch any pins. It hands out "words", one at a time: each
says how the pins should be set and how long to hold them before the next word.
On the Pico a small hardware player turns words into edges, so nothing the rest
of the program does can move a pulse.

The beat has a slot of its own, after the outputs', which keeps the beat count
and lights the tempo LED. Its pin comes after the eighth output's in every word,
so the LED lights on the very cycle a beat's pulses begin, whatever the outputs
are set to and whether or not any of them is switched on.

MIDI clock comes from the same timeline, on one more pin. On the wire a MIDI
byte is a few edges, and its 32 microsecond bit is a whole number of ticks, so
the words carry it as they carry a pulse. A slot of its own keeps 24 clocks to
the beat. Each clock byte starts 320 ticks before its place on that grid, so
that it has finished arriving as the outputs' pulses begin. The pin is
inverted, for the transistor that drives the socket: it rests low, and is high
where the MIDI line is low.

Time is counted in ticks of one microsecond. For each output the scheduler keeps
the time to its next pulse as a whole number of ticks plus an exact part, a
fraction that is never rounded away, so the timing can't drift however long it
runs or however often the tempo changes.

Everything here uses whole numbers below 2**30 and arrays made in advance. That
matters on the Pico: it means working out a word never asks for memory, so the
other half of the program collecting its garbage can't hold a pulse up.
"""

import array

# Tempo is given in hundredths of a BPM: 12000 is 120 BPM.
SLOWEST_TEMPO = 2000
FASTEST_TEMPO = 30000

# The longest a single word may hold the pins, in ticks. The player is always
# eight words ahead and playing a ninth, and a change waits too for the few
# words put by to cover it, so this bounds how long a change takes to be
# heard: about 25 ms at most, and less when the outputs are busy.
HOLD_LIMIT = 2500

# The most outputs a timeline drives. A word's low bits carry their pins first,
# then the tempo LED's, then MIDI out's.
MOST_OUTPUTS = 8
LED_BIT = 1 << MOST_OUTPUTS
MIDI_BIT = 1 << (MOST_OUTPUTS + 1)
PIN_BITS = MOST_OUTPUTS + 2

# The tempo LED is lit for a quarter of each beat, so it looks the same at every
# tempo, but never longer than this many ticks: a tenth of a second, which is a
# quarter beat at 150 BPM. At 300 BPM it is 50 ms.
LED_LONGEST = 100000

# A pulse lasts this many ticks unless an output asks for something else.
PULSE_TICKS = 10000

# The longest pulse an output can be given, in ticks.
LONGEST_PULSE = 1000000

# What an output is for. A clock pulses at its ratio. A start pulse fires once
# when the clock starts and a stop pulse once when it stops, for resetting
# whatever follows the clock. A run output is high for as long as the clock runs.
CLOCK = 0
START_PULSE = 1
STOP_PULSE = 2
RUN = 3

# When a start pulse fires or a run output goes high, the clocks wait this many
# ticks before their first pulse, so whatever is being reset or started has
# seen that signal before its first clock arrives.
LEAD_TICKS = 10000

# Start, pause, stop and play/pause, as `press` takes them. They are numbers
# because the second core reads the buttons and can't look words up without
# asking for memory. `synthsync.core.transport.NUMBERS` gives each one's number.
PRESS_START = 1
PRESS_PAUSE = 2
PRESS_STOP = 3
PRESS_PLAY_PAUSE = 4

# What MIDI out does: nothing, clock only while the clock plays, or clock all the
# time, so that what follows it knows the tempo before it is started.
MIDI_OFF = 0
MIDI_PLAYING = 1
MIDI_ALWAYS = 2

# MIDI clock runs at 24 to the beat. A byte is ten bits of 32 ticks. At least
# MIDI_GAP_TICKS must pass between the end of a Start or Continue and the next
# clock, which is what the follower moves on.
MIDI_PER_BEAT = 24
MIDI_BIT_TICKS = 32
MIDI_BYTE_TICKS = 320
MIDI_GAP_TICKS = 1000

# Each byte MIDI out sends, as how long its pin is high, then low, then high,
# counted in bits, four bits to a count with the first at the bottom. After the
# last count the pin goes low and stays there until the byte's ten bits are up.
MIDI_CLOCK = 4
MIDI_START = 2 | 1 << 4 | 1 << 8
MIDI_CONTINUE = 1 | 2 << 4 | 1 << 8
MIDI_STOP = 3

# How many Start, Continue and Stop bytes can wait for the line.
_MIDI_WAITING = 4

# The longest a word lasts while only MIDI's clock is playing. The player is
# eight words ahead, so this keeps a press of start as quick as it is with
# nothing playing at all.
MIDI_IDLE_HOLD = 400

# The role of the MIDI slot while MIDI is off, which is no role at all.
_NOT_SENT = -1

# One beat lasts 6000 x 1,000,000 / tempo ticks, with the tempo in hundredths of
# a BPM. The two factors are kept apart so no product reaches 2**30.
_BEAT_FACTOR = 6000
_MILLION = 1000000

# The count of changes wraps here, well inside a small whole number.
_CHANGES_WRAP = 0xFFFFFF

# The limits a ratio must keep, as synthsync.core.ratio enforces them.
_LARGEST_PART = 9600
_SLOWEST_BEATS = 128
_FASTEST_PULSES = 96


def gap_ticks (tempo: int, pulses: int, beats: int) -> "tuple[int, int]":

	"""Give the two lengths, in ticks, the gaps of a steady output can take: its exact gap rounded down, and rounded up.

	An output playing `pulses` to `beats` beats at `tempo` hundredths of a BPM
	pulses every beats x 6,000,000,000 / (pulses x tempo) ticks, and each pulse
	is on the tick at or just before its exact time. So from whatever moment it
	is watched, each gap is the exact one rounded down or up, the two being the
	same when it is whole. This is for the tools that time the pins: the
	player never calls it.
	"""

	whole, part = divmod(beats * _BEAT_FACTOR * _MILLION, pulses * tempo)

	return (whole, whole + 1 if part else whole)


class Timeline:

	"""Works out, word by word, how the output pins should be set.

	Outputs are numbered from 0. After `start()`, each call to `next_word()`
	gives the next word to play. Tempo and ratios can be changed between words
	at any time, running or not.
	"""

	def __init__ (self, outputs: int, cycles_per_tick: int = 150, overhead_cycles: int = 4, pin_bits: int = PIN_BITS) -> None:

		"""Make a stopped timeline for this many outputs, at 120 BPM with every ratio at one pulse a beat.

		The other three numbers describe the player's words: how many of its
		clock cycles make one tick, how many cycles it spends fetching a word,
		and how many of a word's low bits carry the pins. Those have room for
		the eight outputs and the tempo LED at least.
		"""

		if outputs < 1 or outputs > MOST_OUTPUTS:
			raise ValueError("A timeline drives between 1 and %d outputs" % MOST_OUTPUTS)

		if pin_bits <= MOST_OUTPUTS:
			raise ValueError("A word needs a pin bit for each of %d outputs and the tempo LED" % MOST_OUTPUTS)

		# One slot for each output, one for MIDI's clock, and a last one that
		# follows the beat itself.
		slots = outputs + 2
		blank = [0] * slots

		self.outputs = outputs
		self.slots = slots
		self.midi_slot = outputs
		self.master = outputs + 1
		self.cycles_per_tick = cycles_per_tick
		self.overhead_cycles = overhead_cycles
		self.pin_bits = pin_bits

		# Each slot's ratio: this many pulses to this many beats.
		self.pulses = array.array("i", [1] * slots)
		self.beats = array.array("i", [1] * slots)

		# The gap between a slot's pulses: `period` whole ticks plus `period_part`
		# out of `divisor`.
		self.period = array.array("i", blank)
		self.period_part = array.array("i", blank)
		self.divisor = array.array("i", blank)

		# The time to a slot's next pulse: `until` whole ticks plus `until_part`
		# out of `divisor`. An `until` of -1 means the slot isn't running.
		self.until = array.array("i", [-1] * slots)
		self.until_part = array.array("i", blank)

		# Where the waits are kept while the clock is paused, so `until` can read
		# as "not running" and nothing new begins.
		self.held = array.array("i", [-1] * slots)

		# The time until a slot's pin goes low again, or -1 when it's already low.
		self.fall = array.array("i", [-1] * slots)

		# How long a slot's pulse lasts in ticks, or 0 for half the gap.
		self.width = array.array("i", [PULSE_TICKS] * slots)

		# What each slot is for, and whether it is silenced. A muted clock keeps
		# its place and simply doesn't raise its pin.
		self.role = array.array("i", [CLOCK] * slots)
		self.muted = array.array("i", blank)

		# The bit each slot drives in a word. The beat slot drives none of its own
		# here: its `fall` times the lead, so the tempo LED it lights is timed in
		# `led_fall` instead, the time until the LED goes out, or -1 when it is out.
		self.bit = array.array("i", [1 << index for index in range(outputs)] + [0, 0])
		self.led_fall = -1
		self.led_width = LED_LONGEST

		# MIDI out. `midi_on` says there is anything for it to do, which stays true
		# after it is switched off until the last byte has gone.
		self.midi_mode = MIDI_OFF
		self.midi_on = 0

		# The byte going out: the time until its pin next changes, or -1 when no
		# change is left, what is left of its counts, and the time until the line
		# is free for another.
		self.midi_run = -1
		self.midi_pattern = 0
		self.midi_free = 0

		# Start, Continue and Stop bytes waiting for the line, oldest first.
		self.midi_waiting = array.array("i", [0] * _MIDI_WAITING)
		self.midi_queued = 0

		# How many clock bytes the grid is owed: 1 for the next place on it, and
		# more only if one could not be sent in time.
		self.midi_due = 0

		# With the clock not playing, the time until the next clock byte, or -1
		# for none, with the exact part left over out of the slot's divisor.
		self.midi_idle = -1
		self.midi_idle_part = 0

		# Where _muldiv leaves its answer: the whole part, then the remainder.
		self._answer = array.array("i", [0, 0])

		self.tempo = 12000
		self.mask = 0
		self.beat = 0
		self.running = False
		self.paused = False

		# What was left of a lead when the clock was paused, to be waited out when it carries on.
		self.owed = 0

		# How many pulses began while the one before was still high, and so made
		# no edge. It should stay at zero: see _trim.
		self.merged = 0

		# Counts every change to where the clock is, once as it begins and once
		# as it ends, so it is odd while a change is half-made. `position`
		# looks at it to know whether what it has just read hangs together.
		self.changes = 0

		self.pulses[self.midi_slot] = MIDI_PER_BEAT
		self.role[self.midi_slot] = _NOT_SENT

		for slot in range(slots):
			self._load(slot)

		self._size_led()

	def _size_led (self) -> None:

		"""Work out how long the tempo LED stays lit at the present tempo: a quarter of a beat, but no longer than LED_LONGEST."""

		width = self.period[self.master] >> 2

		self.led_width = width if width < LED_LONGEST else LED_LONGEST

	def _muldiv (self, count: int, each: int, extra: int, divisor: int) -> None:

		"""Work out (count x each + extra) / divisor exactly, as a whole part and a remainder.

		The product can be far too large for the Pico to hold without asking for
		memory, so it is built up one bit of `count` at a time, keeping every
		number below 2**30. The answer is left in `_answer`.

		It needs 2 x divisor + each, and divisor + extra, to stay below 2**30.
		"""

		whole = 0
		rest = 0
		bit = 1 << 29

		while bit and not count & bit:
			bit >>= 1

		while bit:

			whole <<= 1
			rest <<= 1

			if count & bit:
				rest += each

			if rest >= divisor:
				whole += rest // divisor
				rest %= divisor

			bit >>= 1

		rest += extra

		if rest >= divisor:
			whole += rest // divisor
			rest %= divisor

		answer = self._answer
		answer[0] = whole
		answer[1] = rest

	def _load (self, slot: int) -> None:

		"""Work out the gap between one slot's pulses at the present tempo."""

		divisor = self.pulses[slot] * self.tempo

		self._muldiv(self.beats[slot] * _BEAT_FACTOR, _MILLION, 0, divisor)

		self.period[slot] = self._answer[0]
		self.period_part[slot] = self._answer[1]
		self.divisor[slot] = divisor

	def _lead (self) -> int:

		"""Give how many ticks of a lead are still to run.

		A lead is plain time, not musical time: the beat stands still while it
		runs. It is counted down in the beat slot's `fall`, which nothing else
		uses, so the word-by-word path keeps it without any extra work.
		"""

		left = self.fall[self.master]

		return left if left > 0 else 0

	def _place (self, slot: int) -> None:

		"""Put a running slot's next pulse on its grid, worked out from the beat position.

		The slot's pulses fall wherever the beat count is a whole multiple of
		beats / pulses, counted from zero. This finds the first such moment that
		hasn't passed yet.
		"""

		waits = self.held if self.paused else self.until
		master = self.master
		pulses = self.pulses[slot]
		beats = self.beats[slot]
		divisor = self.divisor[slot]

		# The time to the next beat once any lead is over, as whole ticks and a
		# part out of this slot's divisor.
		lead = self._lead()
		whole = waits[master] - lead
		part = self.until_part[master] * pulses

		if self.beat == 0:
			# Nothing has played yet, so the first pulse is on the first beat.
			waits[slot] = whole + lead
			self.until_part[slot] = part
			return

		# How far the next beat is past this slot's last grid line, in units of
		# 1 / pulses of a beat. Reduced first so the product stays small.
		offset = ((self.beat % beats) * (pulses % beats)) % beats

		# Step back from the next beat to that grid line.
		self._muldiv(offset * _BEAT_FACTOR, _MILLION, 0, divisor)

		whole -= self._answer[0]
		part -= self._answer[1]

		if part < 0:
			part += divisor
			whole -= 1

		gap = self.period[slot]
		gap_part = self.period_part[slot]

		if whole < 0:
			# That grid line has passed, and the one after it hasn't.
			whole += gap
			part += gap_part

			if part >= divisor:
				part -= divisor
				whole += 1

		else:
			# Step back over any earlier grid lines that are still to come.
			while whole > gap or (whole == gap and part >= gap_part):

				whole -= gap
				part -= gap_part

				if part < 0:
					part += divisor
					whole -= 1

		waits[slot] = whole + lead
		self.until_part[slot] = part

	def _trim (self, slot: int) -> None:

		"""Cut a pulse short if a change has brought the next one forward into it.

		A pulse's length is chosen when it begins, to fit the gap as it was then.
		If a change shortens the gap while the pin is still high, the pulse is
		cut to half of what's left, so the pin goes low before the next pulse
		and that pulse still has an edge to be counted by.

		The one case it can't help is a next pulse due this very instant: there
		is no time left to go low in, so the two run together and are counted
		in `merged`.
		"""

		wait = self.until[slot]

		if wait > 0 and self.fall[slot] * 2 > wait:
			self.fall[slot] = wait >> 1

	def set_tempo (self, tempo: int) -> None:

		"""Change the tempo, given in hundredths of a BPM.

		Every output keeps its place: the time to each next pulse is stretched
		or shrunk exactly, so nothing is dropped, doubled or shifted against
		anything else.
		"""

		if tempo < SLOWEST_TEMPO or tempo > FASTEST_TEMPO:
			raise ValueError("The tempo has to be between 20 and 300 BPM")

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		# Any lead still running is plain time, so it is set aside and only the
		# musical part of each wait is stretched or shrunk.
		waits = self.held if self.paused else self.until
		lead = self._lead()

		for slot in range(self.slots):

			if waits[slot] >= 0:
				self._muldiv(waits[slot] - lead, self.divisor[slot], self.until_part[slot], self.pulses[slot] * tempo)
				waits[slot] = self._answer[0] + lead
				self.until_part[slot] = self._answer[1]

		self.tempo = tempo

		for slot in range(self.slots):
			self._load(slot)
			self._trim(slot)

		# A flash already lit keeps its length, which is never more than a quarter of any beat.
		self._size_led()

		self.changes = (self.changes + 1) & _CHANGES_WRAP

	def set_ratio (self, output: int, pulses: int, beats: int) -> None:

		"""Give one output a new ratio: this many pulses to this many beats.

		If the clock is running, the output moves straight onto its new grid,
		counted from where the clock started.
		"""

		self._check_output(output)

		if pulses < 1 or beats < 1 or pulses > _LARGEST_PART or beats > _LARGEST_PART:
			raise ValueError("A ratio is made of whole numbers from 1 to %d" % _LARGEST_PART)

		if pulses * _SLOWEST_BEATS < beats or pulses > beats * _FASTEST_PULSES:
			raise ValueError("A ratio has to be between ÷%d and ×%d" % (_SLOWEST_BEATS, _FASTEST_PULSES))

		self.pulses[output] = pulses
		self.beats[output] = beats
		self._load(output)

		waits = self.held if self.paused else self.until

		if waits[output] >= 0:
			self._place(output)
			self._trim(output)

	def _check_output (self, output: int) -> None:

		"""Refuse an output number this timeline doesn't have."""

		if output < 0 or output >= self.outputs:
			raise ValueError("There is no output %d" % output)

	def _lower (self, slot: int) -> None:

		"""Set one slot's pin low at once and forget any pulse it was in."""

		self.mask &= ~self.bit[slot]
		self.fall[slot] = -1

	def _pulse (self, slot: int) -> None:

		"""Raise one slot's pin for a single pulse, for a start or a stop."""

		length = self.width[slot]

		# "Half the gap" means nothing for a pulse that happens once.
		if length == 0:
			length = PULSE_TICKS

		self.mask |= self.bit[slot]
		self.fall[slot] = length

	def set_mute (self, output: int, muted: bool) -> None:

		"""Silence an output, or let it sound again.

		A muted output goes low at once. A muted clock keeps its place, so it
		comes back in step, and a run output comes back high if the clock is
		running.
		"""

		self._check_output(output)

		self.muted[output] = 1 if muted else 0

		if muted:
			self._lower(output)

		elif self.running and not self.paused and self.role[output] == RUN:
			self.mask |= self.bit[output]

	def set_width (self, output: int, ticks: int) -> None:

		"""Set how long an output's pulses last, in ticks, or 0 for half the gap between them.

		It takes hold at the output's next pulse. A length that won't fit the
		gap is cut to half of it.
		"""

		self._check_output(output)

		if ticks < 0 or ticks > LONGEST_PULSE:
			raise ValueError("A pulse lasts at most one second")

		self.width[output] = ticks

	def set_role (self, output: int, role: int) -> None:

		"""Say what an output is for: CLOCK, START_PULSE, STOP_PULSE or RUN.

		The output goes low first, so nothing is left high by the change. If
		the clock is running, a new clock joins its grid and a new run output
		goes high.
		"""

		self._check_output(output)

		if role < CLOCK or role > RUN:
			raise ValueError("An output is a clock, a start pulse, a stop pulse or a run output")

		waits = self.held if self.paused else self.until

		self._lower(output)
		self.role[output] = role
		waits[output] = -1
		self.until_part[output] = 0

		if not self.running:
			return

		if role == CLOCK:
			waits[output] = 0
			self._place(output)

		elif role == RUN and not self.muted[output] and not self.paused:
			self.mask |= self.bit[output]

	def set_midi (self, mode: int) -> None:

		"""Say what MIDI out does: MIDI_OFF, MIDI_PLAYING or MIDI_ALWAYS.

		Switched on while the clock runs, its clock joins the grid at once, and
		what follows it starts at the next Start. Switched off while the clock
		plays, it says Stop first, so nothing is left waiting for clocks that
		won't come. A byte already going out always finishes.
		"""

		if mode < MIDI_OFF or mode > MIDI_ALWAYS:
			raise ValueError("MIDI out is off, on while playing, or always on")

		if self.pin_bits <= MOST_OUTPUTS + 1:
			raise ValueError("These words have no pin bit for MIDI out")

		was = self.midi_mode

		if mode == was:
			return

		slot = self.midi_slot
		playing = self.running and not self.paused
		waits = self.held if self.paused else self.until

		self.midi_mode = mode

		if mode == MIDI_OFF:

			self.role[slot] = _NOT_SENT
			self.until[slot] = -1
			self.held[slot] = -1
			self.until_part[slot] = 0
			self.midi_due = 0
			self.midi_idle = -1

			if playing:
				self._midi_queue(MIDI_STOP)

			return

		self.midi_on = 1

		if was == MIDI_OFF:

			self.role[slot] = CLOCK

			if self.running:
				waits[slot] = 0
				self._place(slot)
				self.midi_due = 1

		if playing:
			self.midi_idle = -1
		else:
			self._midi_idle_from(waits[slot])

	def _midi_queue (self, byte: int) -> None:

		"""Leave a byte to go out as soon as the line is free. If too many are waiting, it takes the last one's place."""

		count = self.midi_queued

		if count == _MIDI_WAITING:
			count -= 1

		self.midi_waiting[count] = byte
		self.midi_queued = count + 1
		self.midi_on = 1

	def _midi_clear (self) -> int:

		"""Give how long it is until everything now waiting has gone out, in ticks."""

		return self.midi_free + self.midi_queued * MIDI_BYTE_TICKS

	def _midi_idle_from (self, wait: int) -> None:

		"""With the clock no longer playing, carry MIDI's clock on from where it was, `wait` ticks from its next place on the grid."""

		self.midi_idle_part = 0

		if self.midi_mode != MIDI_ALWAYS:
			self.midi_idle = -1

		elif wait < 0:
			self.midi_idle = 0

		elif not self.midi_due:
			# The clock for that place has gone, so this is the one after it.
			self.midi_idle = wait + self.period[self.midi_slot] - MIDI_BYTE_TICKS

		else:
			self.midi_idle = wait - MIDI_BYTE_TICKS if wait > MIDI_BYTE_TICKS else 0

	def run_pins (self) -> int:

		"""Give the pins of the run outputs that aren't muted, as bits.

		The Pico uses this when the clock carries on from a pause: those pins
		go high again before the clocks continue.
		"""

		pins = 0

		for slot in range(self.outputs):
			if self.role[slot] == RUN and not self.muted[slot]:
				pins |= self.bit[slot]

		return pins

	def start (self) -> int:

		"""Begin at zero, and say how many ticks it is until the first beat.

		Start pulses fire and run outputs go high at once. Every clock pulses
		together on the first beat, which is straight away, or one lead later
		if there is a start pulse or a run output for it to follow. With MIDI
		on, it is long enough at least for MIDI's Start, the gap that must
		follow it, and the first clock byte.
		"""

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		self.mask &= MIDI_BIT
		self.led_fall = -1
		self.beat = 0
		self.running = True
		self.paused = False

		lead = 0

		for slot in range(self.outputs):

			self.fall[slot] = -1

			if self.muted[slot]:
				continue

			if self.role[slot] == START_PULSE:
				self._pulse(slot)
				lead = LEAD_TICKS

			elif self.role[slot] == RUN:
				self.mask |= self.bit[slot]
				lead = LEAD_TICKS

		if self.midi_mode:

			self._midi_queue(MIDI_START)
			self.midi_due = 1
			self.midi_idle = -1

			needed = self._midi_clear() + MIDI_GAP_TICKS + MIDI_BYTE_TICKS

			if needed > lead:
				lead = needed

		for slot in range(self.slots):

			self.until_part[slot] = 0

			if self.role[slot] == CLOCK:
				self.until[slot] = lead
			else:
				self.until[slot] = -1

		self.owed = 0

		# Set even when there is no lead, so none is left from a start just before.
		self.fall[self.master] = lead if lead else -1

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		return lead

	def pause (self) -> None:

		"""Hold the clock where it is.

		No new pulse begins. A pulse already begun finishes as it would have,
		and run outputs go low at once. Every output keeps its place, to the
		exact fraction of a tick, until `resume`. MIDI says Stop.
		"""

		if not self.running or self.paused:
			return

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		# A lead cut short by the pause is set aside, and waited out afterwards.
		lead = self._lead()

		for slot in range(self.slots):

			wait = self.until[slot]

			self.held[slot] = wait - lead if wait >= 0 else -1
			self.until[slot] = -1

			if self.role[slot] == RUN:
				self._lower(slot)

		self.owed = lead
		self.fall[self.master] = -1
		self.paused = True

		if self.midi_mode:
			self._midi_queue(MIDI_STOP)
			self._midi_idle_from(self.held[self.midi_slot])

		self.changes = (self.changes + 1) & _CHANGES_WRAP

	def resume (self) -> int:

		"""Carry on from a pause, and say how many ticks it is until the clocks continue.

		Every output picks up exactly where it was held, so nothing has moved
		against anything else. Run outputs go high at once, and if there are
		any, the clocks follow one lead later, as they do at a start. MIDI says
		Continue, and the clocks wait for it only if its next clock byte would
		otherwise come too soon after.
		"""

		if not self.paused:
			return 0

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		lead = self.owed

		for slot in range(self.outputs):
			if self.role[slot] == RUN and not self.muted[slot]:
				self.mask |= self.bit[slot]
				lead = LEAD_TICKS

		if self.midi_mode:

			self._midi_queue(MIDI_CONTINUE)
			self.midi_idle = -1

			if self.midi_due:

				needed = self._midi_clear() + MIDI_GAP_TICKS + MIDI_BYTE_TICKS - self.held[self.midi_slot]

				if needed > lead:
					lead = needed

		for slot in range(self.slots):

			wait = self.held[slot]

			self.until[slot] = wait + lead if wait >= 0 else -1
			self.held[slot] = -1

		self.owed = 0
		self.paused = False

		if lead:
			self.fall[self.master] = lead

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		return lead

	def press (self, command: int) -> bool:

		"""Start, pause or stop, from whatever the clock is doing now, and say whether anything changed.

		It follows `synthsync.core.transport`'s table: start begins a stopped
		clock at zero and carries a paused one on, pause freezes a running one,
		and stop sends a running or paused one back to zero. Play/pause is
		pause while the clock runs and start otherwise. Anything else does
		nothing. The page's presses and the Pico's own buttons both come here,
		on the second core, so they always mean the same thing and one can
		never overtake the other.
		"""

		# Decided here, as it is carried out, so it goes by what the clock is doing at this moment.
		if command == PRESS_PLAY_PAUSE:
			command = PRESS_PAUSE if self.running and not self.paused else PRESS_START

		if command == PRESS_START:

			if not self.running:
				self.start()
				return True

			if self.paused:
				self.resume()
				return True

		elif command == PRESS_PAUSE:

			if self.running and not self.paused:
				self.pause()
				return True

		elif command == PRESS_STOP:

			if self.running:
				self.stop()
				return True

		return False

	def stop (self) -> None:

		"""Set every output and the tempo LED low and forget the position. Stop pulses then fire.

		A MIDI byte already going out finishes, and MIDI says Stop unless it has
		already, at a pause.
		"""

		self.changes = (self.changes + 1) & _CHANGES_WRAP

		if self.midi_mode and self.running and not self.paused:
			self._midi_queue(MIDI_STOP)
			self._midi_idle_from(self.until[self.midi_slot])

		self.midi_due = 0
		self.mask &= MIDI_BIT
		self.led_fall = -1
		self.beat = 0
		self.running = False
		self.paused = False
		self.owed = 0

		for slot in range(self.slots):
			self.until[slot] = -1
			self.held[slot] = -1
			self.until_part[slot] = 0
			self.fall[slot] = -1

		for slot in range(self.outputs):
			if self.role[slot] == STOP_PULSE and not self.muted[slot]:
				self._pulse(slot)

		self.changes = (self.changes + 1) & _CHANGES_WRAP

	def quiet (self) -> bool:

		"""Say whether the clock is stopped or paused with nothing left to play, MIDI included.

		While this is true every word would be the same, with every pin low,
		so there is nothing worth feeding the player.
		"""

		if self.midi_idle >= 0 or self.midi_run >= 0 or self.midi_queued:
			return False

		return self.still()

	def midi_resting (self) -> bool:

		"""Say whether MIDI's pin is low with no byte going out or waiting, so that the words can stop for a while."""

		return self.midi_run < 0 and not self.midi_queued

	def still (self) -> bool:

		"""Say whether the clock is stopped or paused with no pulse or flash left to finish, whatever MIDI is doing."""

		if self.running and not self.paused:
			return False

		# A flash of the tempo LED begun before a pause finishes, as a pulse does.
		if self.led_fall >= 0:
			return False

		for slot in range(self.slots):
			if self.fall[slot] >= 0:
				return False

		return True

	def position (self) -> int:

		"""Say how far the clock has played, in thousandths of a beat, or -1 before its first beat.

		This is for showing where the clock is, such as a light on the page,
		and not for timing anything. It may ask for memory, so on the Pico it
		is for the first core only.
		"""

		master = self.master

		# The second core may be half-way through a change or a word while this
		# reads. So it reads, then looks whether anything moved meanwhile, and
		# reads again if it did.
		for _ in range(4):

			mark = self.changes
			beat = self.beat
			answer = -1

			if self.running and beat > 0:

				gap = self.period[master]

				# The time left of the present beat. A pause has already set any lead aside.
				if self.paused:
					left = self.held[master]
				else:
					left = self.until[master] - self._lead()

				if 0 <= left <= gap:
					answer = (beat - 1) * 1000 + (gap - left) * 1000 // gap

			if not mark & 1 and self.changes == mark and self.beat == beat:
				return answer

		return -1

	def next_word (self) -> int:

		"""Give the next word for the player: how to set the pins, and how long to hold them.

		The pins are in the word's low bits and the hold, in the player's clock
		cycles, is above them. This is the part that runs for every word, so it
		uses nothing but small whole numbers and the arrays made at the start.
		"""

		until = self.until
		until_part = self.until_part
		fall = self.fall
		period = self.period
		period_part = self.period_part
		divisor = self.divisor
		width = self.width
		muted = self.muted
		bit = self.bit
		slots = self.slots
		last = slots - 1
		mask = self.mask
		hold = HOLD_LIMIT
		led_fall = self.led_fall
		midi_slot = self.midi_slot
		due = self.midi_due

		# The tempo LED goes out before anything rises, as each slot's pin does.
		if led_fall == 0:
			mask &= ~LED_BIT
			led_fall = -1

		for slot in range(slots):

			drop = fall[slot]

			if drop == 0:
				mask &= ~bit[slot]
				drop = -1
				fall[slot] = -1

			wait = until[slot]

			if wait == 0:

				# This slot's pulse is due. Its next one is one gap later, and the
				# part carries over so the fraction is never lost.
				part = until_part[slot] + period_part[slot]
				wait = period[slot]

				if part >= divisor[slot]:
					part -= divisor[slot]
					wait += 1

				until_part[slot] = part
				until[slot] = wait

				if slot == last:
					self.beat += 1

					# Every beat lights the tempo LED, whatever the outputs are doing.
					mask |= LED_BIT
					led_fall = self.led_width

				elif slot == midi_slot:
					# The grid's next place wants a clock byte of its own.
					due += 1

				elif not muted[slot]:
					if mask & bit[slot]:
						self.merged += 1

					mask |= bit[slot]
					drop = width[slot]

					# Half the gap when asked for, or when the pulse wouldn't fit.
					if drop == 0 or drop * 2 > wait:
						drop = wait >> 1

					if drop < 1:
						drop = 1

					fall[slot] = drop

			if wait > 0 and wait < hold:
				hold = wait

			if drop > 0 and drop < hold:
				hold = drop

		if led_fall > 0 and led_fall < hold:
			hold = led_fall

		if self.midi_on:

			run = self.midi_run
			free = self.midi_free
			idle = self.midi_idle
			queued = self.midi_queued
			wait = until[midi_slot]

			# The pin's next change within the byte going out.
			if run == 0:

				mask ^= MIDI_BIT
				pattern = self.midi_pattern

				if pattern:
					run = (pattern & 15) * MIDI_BIT_TICKS
					self.midi_pattern = pattern >> 4
				else:
					run = -1

			# With the line free, the next byte: whatever is waiting comes first.
			if free == 0:

				byte = 0

				if queued:

					waiting = self.midi_waiting
					byte = waiting[0]
					queued -= 1

					for place in range(queued):
						waiting[place] = waiting[place + 1]

				elif due > 1 or (due == 1 and 0 <= wait <= MIDI_BYTE_TICKS):
					byte = MIDI_CLOCK
					due -= 1

				elif idle == 0:

					byte = MIDI_CLOCK
					part = self.midi_idle_part + period_part[midi_slot]
					idle = period[midi_slot]

					if part >= divisor[midi_slot]:
						part -= divisor[midi_slot]
						idle += 1

					self.midi_idle_part = part

				if byte:
					mask |= MIDI_BIT
					run = (byte & 15) * MIDI_BIT_TICKS
					self.midi_pattern = byte >> 4
					free = MIDI_BYTE_TICKS

			if run > 0 and run < hold:
				hold = run

			if free > 0 and free < hold:
				hold = free

			if due == 1 and wait > MIDI_BYTE_TICKS and wait - MIDI_BYTE_TICKS < hold:
				hold = wait - MIDI_BYTE_TICKS

			if idle >= 0:

				if idle > 0 and idle < hold:
					hold = idle

				if hold > MIDI_IDLE_HOLD:
					hold = MIDI_IDLE_HOLD

				if idle > 0:
					idle -= hold

			if run > 0:
				run -= hold

			if free > 0:
				free -= hold

			self.midi_run = run
			self.midi_free = free
			self.midi_idle = idle
			self.midi_queued = queued

			if not self.midi_mode and run < 0 and not queued:
				self.midi_on = 0
				self.midi_free = 0

		self.midi_due = due

		for slot in range(slots):

			if until[slot] > 0:
				until[slot] -= hold

			if fall[slot] > 0:
				fall[slot] -= hold

		if led_fall > 0:
			led_fall -= hold

		self.led_fall = led_fall
		self.mask = mask

		return mask | ((hold * self.cycles_per_tick - self.overhead_cycles) << self.pin_bits)

	def unpack (self, word: int) -> tuple[int, int]:

		"""Split a word into its pins and how many ticks it holds them for."""

		pins = word & ((1 << self.pin_bits) - 1)
		ticks = ((word >> self.pin_bits) + self.overhead_cycles) // self.cycles_per_tick

		return pins, ticks
