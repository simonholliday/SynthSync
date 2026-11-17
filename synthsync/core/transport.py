"""Start, pause and stop: what each one does, whatever the clock is doing.

Start, pause and stop each have one meaning: start never pauses, and pause
never starts. The page sends only those three, so two phones tapping at once,
or one message sent twice, can't undo each other. Play/pause is the Pico's own
button, which starts a stopped clock, pauses a running one and carries a paused
one on. The scheduler carries a press out by this table, on the Pico's second
core, where the page's presses and the Pico's own buttons both arrive, so a
press means the same thing wherever it comes from. The first core keeps its own
account here, to answer at once, and takes the scheduler's whenever the two
differ.

| | Stopped | Running | Paused |
| --- | --- | --- | --- |
| start | begins at zero | nothing | carries on |
| pause | nothing | freezes | nothing |
| stop | nothing | back to zero | back to zero |
| play/pause | begins at zero | freezes | carries on |
"""

# What the clock can be doing.
STOPPED = "stopped"
RUNNING = "running"
PAUSED = "paused"

STATES = (STOPPED, RUNNING, PAUSED)

# What it can be told to do.
START = "start"
PAUSE = "pause"
STOP = "stop"
PLAY_PAUSE = "play/pause"

# What the page can send.
COMMANDS = (START, PAUSE, STOP)

# Everything a press can be, and the Pico's own two buttons, of which stop,
# held for long enough, also puts every setting back as new.
PRESSES = (START, PAUSE, STOP, PLAY_PAUSE)
BUTTONS = (PLAY_PAUSE, STOP)

# Each press as the scheduler takes it, which is
# `synthsync.core.timeline.PRESS_START` and so on.
NUMBERS = {START: 1, PAUSE: 2, STOP: 3, PLAY_PAUSE: 4}

# What a command asks the clock's engine to carry out.
NOTHING = "nothing"
BEGIN = "begin"
CARRY_ON = "carry on"
FREEZE = "freeze"
END = "end"

# For each state and command: what to carry out, and the state that leaves the clock in.
_TABLE = {
	(STOPPED, START): (BEGIN, RUNNING),
	(STOPPED, PAUSE): (NOTHING, STOPPED),
	(STOPPED, STOP): (NOTHING, STOPPED),
	(RUNNING, START): (NOTHING, RUNNING),
	(RUNNING, PAUSE): (FREEZE, PAUSED),
	(RUNNING, STOP): (END, STOPPED),
	(PAUSED, START): (CARRY_ON, RUNNING),
	(PAUSED, PAUSE): (NOTHING, PAUSED),
	(PAUSED, STOP): (END, STOPPED),
	(STOPPED, PLAY_PAUSE): (BEGIN, RUNNING),
	(RUNNING, PLAY_PAUSE): (FREEZE, PAUSED),
	(PAUSED, PLAY_PAUSE): (CARRY_ON, RUNNING),
}


class Transport:

	"""Remembers whether the clock is stopped, running or paused, and turns commands into actions."""

	def __init__ (self) -> None:

		"""Begin stopped."""

		self.state = STOPPED

	def press (self, command: str) -> str:

		"""Take a press and say what the engine should carry out.

		The answer is BEGIN, CARRY_ON, FREEZE, END or NOTHING. The state moves
		on at once, so the engine should carry the action out straight away.
		"""

		if command not in PRESSES:
			raise ValueError("The clock can be told to start, pause or stop")

		action, state = _TABLE[(self.state, command)]
		self.state = state

		return action


def state_of (running: bool, paused: bool) -> str:

	"""Say what the clock is doing, from what the scheduler says of itself."""

	if not running:
		return STOPPED

	return PAUSED if paused else RUNNING
