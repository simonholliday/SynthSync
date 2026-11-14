"""Which pin does what.

This is the one place the wiring is written down. To change the number of
outputs, change OUTPUTS: the page draws however many there are.
"""

# The outputs are on consecutive pins, starting here, because the player sets
# them all at once from the low bits of one word. Output 1 is the first.
FIRST_OUTPUT_PIN = 2
OUTPUTS = 4

# The player can drive eight outputs. The four pins after the outputs are kept
# free for that, and the one after those for MIDI out.
MOST_OUTPUTS = 8
MIDI_PIN = 10

# The inputs: a button held for three seconds to wipe every setting, and one
# each to start, pause and stop the clock.
RESET_PIN = 16
START_PIN = 17
PAUSE_PIN = 18
STOP_PIN = 19

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
