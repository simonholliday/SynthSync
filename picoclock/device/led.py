"""The panel LED, which flashes with every pulse of output 1.

It is worked by a small PIO program of its own, not by the processor. The
program watches output 1's pin, lights the LED the moment that pin rises,
keeps it lit for the time the first core last gave it, and puts it out once
that time is up and the pulse has ended. So each flash starts within a few
billionths of a second of the clock's pulse, nothing the Pico is busy with
can hold it up, and the second core does nothing for it.

Because it follows the pin, it is dark while output 1 is off, paused or
stopped, and it goes on flashing with the clock when everything else on the
Pico has been stopped from its prompt.

Wiring: the LED's pin, then a resistor of 330 Ω to 1 kΩ, then the LED, then ground.
"""

import machine
import rp2

import picoclock.core.buttons
import picoclock.device.board

# The state machine the program runs on: the first of PIO block 1. The player
# and the timing rig use block 0, and the radio uses block 2.
MACHINE = 4

# The programs made so far, by the pin each watches. A program is put into the
# PIO's small memory the first time it is used, so making the LED again with
# the same one doesn't use up more.
_programs: dict = {}


def _program (watched: int):

	"""Give the LED's PIO program, watching this pin."""

	if watched in _programs:
		return _programs[watched]

	def led ():

		# The first time to stay lit for, in clock cycles, given before the program starts.
		pull(block)
		mov(x, osr)

		wrap_target()
		wait(1, gpio, watched)
		set(pins, 1)

		# Lit for the time, and then for as long as the pulse is still high.
		mov(y, x)
		label("lit")
		jmp(y_dec, "lit")
		wait(0, gpio, watched)
		set(pins, 0)

		# A new time if the first core has left one, or the same one again.
		pull(noblock)
		mov(x, osr)
		wrap()

	_programs[watched] = rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)(led)

	return _programs[watched]


class Led:

	"""The panel LED, flashing with every pulse of output 1."""

	def __init__ (self, tempo: int, pin: int = picoclock.device.board.LED_PIN, watched: int = picoclock.device.board.FIRST_OUTPUT_PIN) -> None:

		"""Start the LED's program, lit for as long as suits this tempo, in hundredths of a BPM.

		It watches output 1's pin by its number alone, so it neither takes
		that pin from the player nor sets it.
		"""

		self._cycles = machine.freq() // 1000000
		self.machine = rp2.StateMachine(MACHINE, _program(watched), freq=machine.freq(), set_base=machine.Pin(pin))

		self.machine.put(self._time(tempo))
		self._tempo = tempo
		self.machine.active(1)

	def _time (self, tempo: int) -> int:

		"""Give how long the LED stays lit at a tempo, in clock cycles, which is what the program counts."""

		return picoclock.core.buttons.led_time(tempo) * self._cycles

	def follow (self, tempo: int) -> None:

		"""Keep each flash as long as suits the tempo, as the tempo changes.

		The program takes a new time after each flash, and has room for only
		a few to wait, so a new one is given only once the last has been taken.
		Called again with the same tempo, it gives whatever is still to give.
		"""

		if tempo == self._tempo or self.machine.tx_fifo():
			return

		self.machine.put(self._time(tempo))
		self._tempo = tempo

	def holds (self, tempo: int) -> bool:

		"""Say whether the program has taken the time for this tempo, so that every flash from the next one on is that long."""

		return tempo == self._tempo and not self.machine.tx_fifo()

	def close (self) -> None:

		"""Stop the program and put the LED out."""

		self.machine.active(0)
		self.machine.exec("set(pins, 0)")
