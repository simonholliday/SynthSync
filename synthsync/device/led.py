"""The tempo LED, which flashes on every beat.

The player lights it. Its pin comes straight after the eighth output's, so the
words that set the outputs set it too, on the very cycle each beat's pulses
begin. It is lit for a quarter of each beat, and never longer than a tenth of
a second, whatever the clock's pulses a beat and whether or not any output is
switched on. It is dark while the clock is paused or stopped. Neither core can
hold a flash up, and it goes on flashing with the clock when everything else
on the Pico has been stopped from its prompt. `synthsync.core.timeline` times it.

The one time the first core works it is while the stop button is held to put
the settings back: `show` takes the pin from the player, lights the LED or puts
it out, and gives the pin back afterwards. The clock is stopped by then, so
the player has the LED out too.

Wiring: GP10, then a resistor of 330 Ω to 1 kΩ, then the LED, then ground.
"""

import machine

import synthsync.core.timeline
import synthsync.device.board

# The player lights the pin after the eighth output's, so the LED has to be there.
if synthsync.device.board.LED_PIN != synthsync.device.board.FIRST_OUTPUT_PIN + synthsync.core.timeline.MOST_OUTPUTS:
	raise ValueError("The tempo LED's pin has to be the one after the eighth output's")


class Led:

	"""The tempo LED's pin, for the first core to take from the player while the stop button is held."""

	def __init__ (self, pin: int = synthsync.device.board.LED_PIN) -> None:

		"""Hold the LED's pin without touching it: the player drives it until `show` is asked to."""

		self._pin = machine.Pin(pin)
		self._shown = False

	def show (self, lit: "bool | None") -> None:

		"""Light the LED or put it out from here, taking the pin from the player, or with None give the pin back to the player."""

		if lit is None:

			if self._shown:
				self._pin.init(machine.Pin.ALT, alt=machine.Pin.ALT_PIO0)
				self._shown = False

			return

		self._pin.init(machine.Pin.OUT, value=1 if lit else 0)
		self._shown = True

	def close (self) -> None:

		"""Give the pin back to the player."""

		self.show(None)
