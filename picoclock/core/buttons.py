"""When a button has been pressed, and when the lights are on.

A push button doesn't close cleanly. Its contacts chatter for a few
milliseconds, and a knock on the wire can look like a touch. These classes
take what a pin reads, looked at every few milliseconds, and say when a press
was meant.

Nothing here touches a pin, so it runs anywhere and can be checked anywhere.
`picoclock.device.panel` reads the real pins, and `picoclock.device.led`
works the panel LED.
"""

# How long a button must read the same way before that is believed, in milliseconds.
STEADY = 20

# How long the reset button must be held down, in milliseconds.
HOLD = 3000

# How much of each beat the light is on for, in thousandths of a beat.
LIT = 250

# How long the light is on, and then off, while the reset button is held, in milliseconds.
FLASH = 60

# How much of each beat the panel LED is lit for, in thousandths of a beat, and
# the longest it is ever lit for, in microseconds, so it still reads as a flash
# at a slow tempo.
LED_LIT = 250
LED_LONGEST = 100000


class Button:

	"""One push button, which says when each press of it begins."""

	def __init__ (self) -> None:

		"""Begin not pressed."""

		# Whether the button is believed to be down, and how long the pin has read otherwise.
		self.down = False
		self._changing = 0

	def look (self, pressed: bool, unseen: int, elapsed: int) -> bool:

		"""Take one reading of the button, and say whether a press has just begun.

		`pressed` is whether the pin reads as pressed now, and `elapsed` is
		how long ago the last look was, in milliseconds.

		`unseen` is what the pin itself remembers of the time since the last
		look: how long it was pressed for, from when it first went to pressed
		until it last let go, or until now if it is still pressed. It is 0 if
		the pin never went to pressed.

		A press counts once the pin has been pressed for STEADY milliseconds,
		whether that was seen over several looks or happened between two of
		them while the Pico was busy.
		"""

		if pressed == self.down:

			self._changing = 0

			# The pin reads as it did, but was pressed for long enough in between.
			# Still up, that was a whole press. Still down, it was let go and pressed again.
			return unseen >= STEADY

		# How long it has read this way: by the pin's own account if it only just
		# went to pressed, and otherwise since the last look.
		self._changing += unseen if pressed and 0 < unseen < elapsed else elapsed

		if self._changing < STEADY:
			return False

		self._changing = 0
		self.down = pressed

		return pressed


class Hold:

	"""A button that only counts once it has been held down for a while, so a knock can't set it off."""

	def __init__ (self, needed: int = HOLD) -> None:

		"""Begin not pressed. `needed` is how long it must be held, in milliseconds."""

		self.needed = needed
		self.button = Button()

		# How long it has been held, whether that is still being counted, and
		# whether it has been held long enough and not yet let go.
		self.held = 0
		self.holding = False
		self.done = False

	def look (self, pressed: bool, unseen: int, elapsed: int) -> bool:

		"""Take one reading of the button, and say whether it has just been held for long enough.

		The arguments are `Button.look`'s. It says yes once for each hold, and
		letting go for a moment starts the count again.
		"""

		began = self.button.look(pressed, unseen, elapsed)

		if not self.button.down:
			self.held = 0
			self.holding = False
			self.done = False
			return False

		if began:

			# Counted from when it went down, which is before it was believed.
			self.held = unseen if unseen > STEADY else STEADY
			self.holding = True
			self.done = False

			return False

		if self.done:
			return False

		self.held += elapsed

		if self.held < self.needed:
			return False

		self.holding = False
		self.done = True

		return True


def light (running: bool, position: int, hold: "Hold | None" = None) -> bool:

	"""Say whether the Pico's light is on.

	While the clock runs it is on for the first quarter of every beat.
	`position` is how far the clock has played, in thousandths of a beat, or
	-1 when that can't be said, and then the light is off.

	While the reset button is held it flashes fast instead, and once the
	settings have been put back it stays on until the button is let go.
	"""

	if hold is not None and hold.done:
		return True

	if hold is not None and hold.holding:
		return (hold.held // FLASH) % 2 == 0

	return running and position >= 0 and position % 1000 < LIT


def led_time (tempo: int) -> int:

	"""Say how long the panel LED stays lit for each pulse of output 1, in microseconds, at a tempo in hundredths of a BPM.

	It is a quarter of a beat, so the flash looks the same at every tempo,
	but never more than a tenth of a second: 100 ms at 150 BPM and below, 50
	ms at 300. The LED also stays lit for as long as output 1's pulse does,
	when that is longer, which the LED's own program sees to.
	"""

	# A beat is 6,000,000,000 / tempo microseconds. Dividing first keeps the
	# numbers small enough for the Pico, at a cost of under a quarter of a millisecond.
	return min(LED_LONGEST, 6000000 // tempo * LED_LIT)
