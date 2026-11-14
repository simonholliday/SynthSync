"""Keeping an interrupt from cutting the radio chip short.

The Pico 2 W's radio chip also works the Pico's own light. Anything asked of
it, even switching the light, waits on the chip, and the first thing asked
after power-up has to start the chip, which takes a few tenths of a second.

Ctrl-C from the Pico's prompt can land in the middle of that. MicroPython
then drops what it was doing on the spot and leaves the chip half started:
it says "core not in reset", and nothing on the radio works again until the
Pico is switched off and on. An editor that interrupts the Pico as it is
plugged in does exactly this, a second or so after power-up.

So everything asked of the chip goes through `unbroken`, which holds an
interrupt off until the chip has finished and then lets it happen.

Stopping PicoClock asks several things of the chip, and has work of its own
that mustn't be cut short either. It calls `hold` first and `release` last,
and nothing in between looks for an interrupt: by then one has happened, and
whatever is typed after it is for the Pico's prompt to read.
"""

import micropython
import select
import sys

# Says whether anything has arrived from the Pico's prompt.
_arrived = select.poll()
_arrived.register(sys.stdin, select.POLLIN)

# Whether interrupts are being held off for longer than one thing asked of the chip.
_held = [False]


def hold () -> None:

	"""Hold every interrupt off until `release`, for work that must be seen through."""

	micropython.kbd_intr(-1)
	_held[0] = True


def release () -> None:

	"""Let interrupts happen again."""

	_held[0] = False
	micropython.kbd_intr(3)


def unbroken (work, *details) -> None:

	"""Ask one thing of the radio chip, and let an interrupt that arrives meanwhile happen only afterwards."""

	if _held[0]:
		work(*details)
		return

	micropython.kbd_intr(-1)

	try:
		work(*details)

	finally:
		micropython.kbd_intr(3)

	# Held off, an interrupt is left waiting as an ordinary character. What is waiting is read
	# as far as an interrupt, which is raised now. Anything typed after it is left for the prompt.
	while _arrived.poll(0):

		if sys.stdin.buffer.read(1) == b"\x03":
			raise KeyboardInterrupt()
