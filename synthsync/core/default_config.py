"""The `config.txt` SynthSync makes on a Pico that has none, for the musician to edit.

It is here, and not a file at the top of a release, so that copying a new
release over an old one can never replace the musician's own.
"""

TEXT = b"""# How your SynthSync is wired.
#
# SynthSync reads this file when the Pico is switched on. After changing it,
# save it, then unplug the Pico and plug it in again.
#
# A line that starts with # is a note for you, and SynthSync skips it. If
# SynthSync can't make sense of a line, it uses its own setting in its place,
# and the page says which line under Settings. Getting this file wrong can't
# stop the clock.
#
# Resetting SynthSync, from the page or by holding stop, leaves this file as
# it is.

# How many clock outputs are wired, from 1 to 8. Output 1 is on GP2, output 2
# on GP3, and so on, up to output 8 on GP9.
outputs = 4

# The tempo range, for the page and for a tempo knob alike, from 1 to 1000 BPM.
# It is 20 to 300 unless you remove the # from these lines and change them. A
# range below 20 or above 300 BPM narrows the ratios the outputs can have.
# min_bpm = 20
# max_bpm = 300
"""
