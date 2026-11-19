"""The tempo knob on the Pico: a potentiometer on GP26, if one is fitted.

Its two ends go to AGND and ADC_VREF, its wiper to GP26, with 100 nF from GP26
to AGND. What its readings mean is `synthsync.core.knob`'s. This reads the pin
on the first core, so it has no part in the timing, and hands any new tempo to
the clock, as the page does.

Nothing says whether a knob is fitted. The Pico finds out by pulling the pin up
and then down, with its own weak pulls. A knob holds the pin nearly still, and
a pin with nothing on it follows the pull almost all the way. Just reading the
pin couldn't tell: with nothing on it, it sits as still as a knob would. With
no knob, nothing is read again. The same test is made when the knob starts
moving after a rest, so a wire coming loose can't drag the tempo about.

The pulls are set in the pad's own register, which leaves the pin's digital
input off, as the ADC has it. Setting them through `machine.Pin` would turn it
back on, and with it on this chip's pull-down can't bring a pin low.
"""

import asyncio
import machine
import time

import synthsync.core.knob
import synthsync.device.board

# Where the pad of a pin is set, and the bits for its input and its two pulls.
_PADS = 0x40038000
_PULL_DOWN = 1 << 2
_PULL_UP = 1 << 3
_INPUT = 1 << 6

# How often the knob is read, in milliseconds, and how many quick reads are averaged each time.
LOOK_EVERY = 20
READS = 16

# How long a pull is given to settle, in milliseconds. The capacitor takes about
# 20 through this Pico's pulls, and another Pico's can be weaker.
PULL_SETTLES = 60

# How long the pin is given to recover after a pull, or after being set up.
RECOVERS = 5

# How far the pin must follow the pulls to have nothing on it. A knob lets it
# move about 330 at most, and a pin with nothing on it moved 3,900 or more.
NOTHING_THERE = 1500

# The knob counts as resting after this long, in milliseconds, within this many readings.
AT_REST = 1000
STILL = 12


class TempoKnob:

	"""Reads the knob, if there is one, and keeps the clock's tempo up to date with it."""

	def __init__ (self, clock) -> None:

		"""Make a knob for this clock. Nothing is touched until it runs."""

		self.clock = clock
		self.fitted = False
		self.rule = synthsync.core.knob.Knob(clock.settings.limits)
		self._pad = _PADS + 4 + 4 * synthsync.device.board.KNOB_PIN
		self._read = None

	def _pulls (self, up: bool, down: bool) -> None:

		"""Set the pin's weak pulls, leaving its digital input off."""

		value = machine.mem32[self._pad] & ~(_PULL_UP | _PULL_DOWN | _INPUT)
		machine.mem32[self._pad] = value | (_PULL_UP if up else 0) | (_PULL_DOWN if down else 0)

	def reading (self) -> int:

		"""Read the knob, averaging a few quick reads, from 0 to 4095."""

		total = 0
		read = self._read

		for _ in range(READS):
			total += read() >> 4

		return total // READS

	async def _there (self) -> bool:

		"""Pull the pin each way and say whether something holds it: whether there is a knob."""

		try:
			self._pulls(True, False)
			await asyncio.sleep_ms(PULL_SETTLES)
			high = self.reading()

			self._pulls(False, True)
			await asyncio.sleep_ms(PULL_SETTLES)
			low = self.reading()

		finally:
			self._pulls(False, False)

		await asyncio.sleep_ms(RECOVERS)

		return high - low < NOTHING_THERE

	def _act (self, reading: int, tempo: "int | None") -> None:

		"""Carry out what the knob's rule decided, and note where the knob is if that is due."""

		clock = self.clock

		if tempo is not None:
			clock.set_tempo(tempo)

		if self.rule.to_note(reading, clock.settings.knob_at):
			clock.set_knob_at(reading)

	async def _rest (self) -> None:

		"""Do nothing from now on: there is no knob to read."""

		self.fitted = False

		while True:
			await asyncio.sleep(3600)

	async def run (self) -> None:

		"""Find out whether there is a knob and, if so, follow it. Runs for as long as the clock does."""

		self._read = machine.ADC(machine.Pin(synthsync.device.board.KNOB_PIN)).read_u16
		await asyncio.sleep_ms(RECOVERS)

		if not await self._there():
			await self._rest()

		self.fitted = True
		settings = self.clock.settings
		rule = self.rule
		reading = self.reading()

		self._act(reading, rule.start(reading, settings.tempo, settings.knob_at))

		rested_at = reading
		rested_since = time.ticks_ms()

		while True:

			await asyncio.sleep_ms(LOOK_EVERY)

			reading = self.reading()

			if abs(reading - rested_at) > STILL:

				# Moving after a rest: first make sure it is the knob that moved.
				if time.ticks_diff(time.ticks_ms(), rested_since) >= AT_REST:

					if not await self._there():
						await self._rest()

					reading = self.reading()

				rested_at = reading
				rested_since = time.ticks_ms()

			self._act(reading, rule.look(reading, settings.tempo))
