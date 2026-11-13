"""Start, pause and stop: what each one does, whatever the clock is doing.

The page's buttons and the hardware inputs both go through this, so a press
means the same thing wherever it comes from. Each command has one meaning:
start never pauses, and pause never starts.

| | Stopped | Running | Paused |
| --- | --- | --- | --- |
| start | begins at zero | nothing | carries on |
| pause | nothing | freezes | nothing |
| stop | nothing | back to zero | back to zero |
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

COMMANDS = (START, PAUSE, STOP)

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
}


class Transport:

	"""Remembers whether the clock is stopped, running or paused, and turns commands into actions."""

	def __init__ (self) -> None:

		"""Begin stopped."""

		self.state = STOPPED

	def press (self, command: str) -> str:

		"""Take a command and say what the engine should carry out.

		The answer is BEGIN, CARRY_ON, FREEZE, END or NOTHING. The state moves
		on at once, so the engine should carry the action out straight away.
		"""

		if command not in COMMANDS:
			raise ValueError("The clock can be told to start, pause or stop")

		action, state = _TABLE[(self.state, command)]
		self.state = state

		return action
