"""What the Pico runs when it is switched on: SynthSync.

If anything here fails, MicroPython says what went wrong over USB and waits
there for commands, so the Pico can always be reached to put a new copy on.
"""

import synthsync.device.start

synthsync.device.start.run()
