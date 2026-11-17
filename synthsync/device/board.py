"""Which pin does what.

This is the one place the wiring is written down. How many outputs are wired
is the musician's to say, in `config.txt` at the top of the Pico's files, which
`synthsync.core.config` reads. The page draws however many there are.

The outputs take pins from the bottom, starting at GP2, and everything the
player sets comes straight after them. Everything else takes pins from the
top, starting at GP22. A new pin for anything that isn't the player's is the
highest one still free, so the two ends grow towards each other and only the
room between them limits what fits. GP23 to GP25 and GP29 belong to the Pico 2
W's radio, and GP26 to GP28 are kept free for analogue inputs. The buttons step
out of that order for the wiring's sake, either side of a ground pin, so GP18
to GP20 are free.
"""

# The outputs are on consecutive pins, starting here, because the player sets
# them all at once from the low bits of one word. Output 1 is the first. GP0 and
# GP1 are left free: they are the Pico's own serial port.
FIRST_OUTPUT_PIN = 2

# Where the file is that says how many outputs are wired.
CONFIG = "/config.txt"

# The player can drive eight outputs. The four pins after the outputs are kept
# free for that. The player also sets the two after those, from the same words:
# the tempo LED, which it lights on every beat, beside a ground pin, and then
# MIDI out, which it is to send as one more pin.
MOST_OUTPUTS = 8
LED_PIN = 10
MIDI_PIN = 11

# From the top: the stop button, which also wipes every setting when held for
# five seconds, and play/pause. They are on either side of a ground pin, which
# both can share. GP20 is the next one free, then GP19 and GP18.
STOP_PIN = 22
PLAY_PAUSE_PIN = 21

# Whether an input reads as pressed when its pin is pulled low. A bare push
# button between the pin and ground does that, against the Pico's own pull-up,
# and needs no other part.
#
# Set one to False for an input that goes high when pressed. That one also
# needs a resistor of 8.2 kΩ or less from its pin to ground: the Pico 2's own
# pull-down can't bring a pin low again once it has been high, so a bare
# button to 3V3 reads as pressed for ever after its first press.
PRESSED_IS_LOW = {
	STOP_PIN: True,
	PLAY_PAUSE_PIN: True,
}
