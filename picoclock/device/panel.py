"""The buttons and the lights on the Pico itself.

Play/pause and stop each have a pin with a push button on it. Play/pause
starts a stopped clock, pauses a running one and carries a paused one on, and
stop does what the page's stop does. They are read by the player on the second
core, which works even while the first core is held up, so this only tells the
player which pins they are on.

Stop held for five seconds also puts every setting back as new. It has
stopped the clock the moment it was pressed, so anyone who only meant to stop
lets go long before. From two seconds both lights flash fast to warn, and
once the settings are back they stay lit until the button is let go.
Otherwise the Pico's own light comes on once a beat while the clock runs, and
the panel LED flashes with output 1 by itself, and is only told here when the
tempo changes, so its flash stays a quarter of a beat long.

The hold and the lights are the first core's work, and none of it is urgent.
The stop pin is looked at every few milliseconds from the main loop. It also
notes, through an interrupt, when it was pressed and when it was let go, so a
hold made while the Pico was busy saving or scanning for networks isn't lost,
and a spike on the wire is never taken for one. The interrupt only notes the
time: the hold is acted on from the main loop, never from the interrupt.
"""

import asyncio
import machine
import micropython
import time

import picoclock.core.buttons
import picoclock.core.transport
import picoclock.device.board
import picoclock.device.chip
import picoclock.device.led

# How often the buttons are looked at and the light is set, in milliseconds.
LOOK_EVERY = 5

def wiring () -> dict:

	"""Give the pin each button is on, and whether it reads low when pressed, as `board` has them."""

	board = picoclock.device.board

	pins = {
		picoclock.core.transport.PLAY_PAUSE: board.PLAY_PAUSE_PIN,
		picoclock.core.transport.STOP: board.STOP_PIN,
	}

	return {name: (pins[name], board.PRESSED_IS_LOW[pins[name]]) for name in pins}


class Panel:

	"""Hands the play/pause and stop buttons to the player, reads stop held down to put the settings back, and works the lights."""

	def __init__ (self, clock, player, inputs: "dict | None" = None, light=None, led=None) -> None:

		"""Set the pins up as inputs, the light as off, and the panel LED flashing.

		`inputs` gives a (pin, pressed is low) for each of "play/pause" and
		"stop" that has a button, and is the wiring in `board` unless given.
		`light` is anything with a `value` to set, and is the Pico's own light
		unless given. `led` is anything with a `follow` to give the tempo to
		and a `show` to warn of a reset with, and is the panel LED unless given.
		"""

		self.clock = clock
		self.player = player
		self._led = led if led is not None else picoclock.device.led.Led(clock.settings.tempo)

		if inputs is None:
			inputs = wiring()

		# Play/pause and stop are read by the player on the second core, where the first core being held up can't keep them waiting.
		self._handed = sorted(inputs)

		for name in self._handed:
			number, low = inputs[name]
			player.listen(name, number, low)

		# An interrupt can't ask for memory, even to report what went wrong in it.
		micropython.alloc_emergency_exception_buf(100)

		# Stop held down puts the settings back, which is the first core's work, so it reads that pin for itself as well.
		self._names = [name for name in inputs if name == picoclock.core.transport.STOP]
		self._pins = []
		self._levels = []
		self._buttons = []
		self._hold = None

		# When each pin first went to pressed and last let go since it was looked at, or -1.
		self._downs = [-1] * len(self._names)
		self._ups = [-1] * len(self._names)

		for index in range(len(self._names)):

			name = self._names[index]
			number, low = inputs[name]

			# A button to ground works against the Pico's own pull-up. The Pico 2's
			# own pull-down can't bring a pin low again once it has been high, so an
			# input pressed high needs a resistor of 8.2 kΩ or less from the pin to ground.
			pin = machine.Pin(number, machine.Pin.IN, machine.Pin.PULL_UP if low else machine.Pin.PULL_DOWN)
			pin.irq(self._remember(index, 0 if low else 1), machine.Pin.IRQ_FALLING | machine.Pin.IRQ_RISING, hard=True)

			self._pins.append(pin)
			self._levels.append(0 if low else 1)

			self._hold = picoclock.core.buttons.Hold()
			self._buttons.append(self._hold)

		# The Pico 2 W's light is worked through its radio chip, which is slow, so it is set only when it changes.
		# The first use of it starts the chip, which an interrupt mustn't cut short, so every use goes through `unbroken`.
		self._light = light
		self._lit = False

		# Whether a held stop has taken the panel LED over, and how, as `buttons.warning` says.
		self._warned = None

		if light is None:
			picoclock.device.chip.unbroken(self._find_light)

		picoclock.device.chip.unbroken(self._light.value, 0)

		self._looked = time.ticks_ms()

	def _find_light (self) -> None:

		"""Take hold of the Pico's own light, which starts the radio chip if nothing else has."""

		self._light = machine.Pin("LED", machine.Pin.OUT)

	def _remember (self, index: int, level: int):

		"""Make what a pin's interrupt runs: it notes when the pin went to pressed or let go, and nothing more.

		It runs between any two instructions of the main loop, so it asks for
		no memory: the times are small whole numbers, kept in lists made in
		advance.
		"""

		downs = self._downs
		ups = self._ups

		def changed (pin) -> None:

			if pin.value() != level:
				ups[index] = time.ticks_ms()
			elif downs[index] < 0:
				downs[index] = time.ticks_ms()

		return changed

	def look (self) -> None:

		"""Read the stop button, put the settings back once it has been held long enough, and set the lights."""

		now = time.ticks_ms()
		elapsed = time.ticks_diff(now, self._looked)

		self._looked = now

		for index in range(len(self._names)):

			# What the pin noted is taken before the pin is read, so that a press
			# arriving between the two is kept for the next look.
			down = self._downs[index]
			up = self._ups[index]

			self._downs[index] = -1
			self._ups[index] = -1

			pressed = self._pins[index].value() == self._levels[index]
			unseen = 0

			# How long it was pressed for since the last look: until now, or until it was let go.
			if down >= 0 and pressed:
				unseen = time.ticks_diff(now, down)
			elif down >= 0 and up >= 0:
				unseen = time.ticks_diff(up, down)

			if self._buttons[index].look(pressed, max(unseen, 0), elapsed):
				self.clock.reset()

		self._led.follow(self.clock.settings.tempo)

		warned = picoclock.core.buttons.warning(self._hold)

		if warned != self._warned:
			self._warned = warned
			self._led.show(warned)

		running = self.clock.transport.state == picoclock.core.transport.RUNNING
		lit = picoclock.core.buttons.light(running, self.player.position() if running else -1, self._hold)

		if lit != self._lit:
			self._lit = lit
			picoclock.device.chip.unbroken(self._light.value, 1 if lit else 0)

	async def run (self) -> None:

		"""Look at the stop button and set the lights for as long as the Pico is on."""

		while True:
			self.look()
			await asyncio.sleep_ms(LOOK_EVERY)

	def close (self) -> None:

		"""Stop listening to the buttons and put the light out.

		The panel LED goes on flashing with output 1, even if it was warning
		of a reset: the clock plays on.
		"""

		for name in self._handed:
			self.player.listen(name, -1)

		for pin in self._pins:
			pin.irq(None)

		self._led.show(None)

		picoclock.device.chip.unbroken(self._light.value, 0)
