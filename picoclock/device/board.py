"""Which pin does what.

This is the one place the wiring is written down. To change the number of
outputs, change OUTPUTS: the page draws however many there are.

The outputs take pins from the bottom, starting at GP2, and everything else
takes pins from the top, starting at GP22. A new pin for anything that isn't
an output is the highest one still free, so the two ends grow towards each
other and only the room between them limits how many outputs there can be.
GP23 to GP25 and GP29 belong to the Pico 2 W's radio, and GP26 to GP28 are
kept free for analogue inputs.
"""

# The outputs are on consecutive pins, starting here, because the player sets
# them all at once from the low bits of one word. Output 1 is the first. GP0 and
# GP1 are left free: they are the Pico's own serial port.
FIRST_OUTPUT_PIN = 2
OUTPUTS = 4

# The player can drive eight outputs. The four pins after the outputs are kept
# free for that, and the one after those for MIDI out, which the player is to
# send as one more pin.
MOST_OUTPUTS = 8
MIDI_PIN = 10

# From the top: a button held for three seconds to wipe every setting, one
# each to start, pause and stop the clock, and the panel LED, which flashes
# with output 1. GP17 is the next one free.
RESET_PIN = 22
START_PIN = 21
PAUSE_PIN = 20
STOP_PIN = 19
LED_PIN = 18

# Whether an input reads as pressed when its pin is pulled low. A bare push
# button between the pin and ground does that, against the Pico's own pull-up,
# and needs no other part.
#
# Set one to False for an input that goes high when pressed. That one also
# needs a resistor of 8.2 kΩ or less from its pin to ground: the Pico 2's own
# pull-down can't bring a pin low again once it has been high, so a bare
# button to 3V3 reads as pressed for ever after its first press.
PRESSED_IS_LOW = {
	RESET_PIN: True,
	START_PIN: True,
	PAUSE_PIN: True,
	STOP_PIN: True,
}
