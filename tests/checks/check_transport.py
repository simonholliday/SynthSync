"""Start, pause, stop and play/pause mean the same on the Pico's Python as on a laptop's."""

import synthsync.core.timeline
import synthsync.core.transport


def after (commands: tuple[str, ...]) -> synthsync.core.transport.Transport:

	"""Make a transport and press these commands in turn."""

	transport = synthsync.core.transport.Transport()

	for command in commands:
		transport.press(command)

	return transport


def check_a_new_transport_is_stopped () -> None:

	"""Nothing runs until something says start."""

	assert synthsync.core.transport.Transport().state == synthsync.core.transport.STOPPED


def check_every_command_from_every_state () -> None:

	"""All twelve cases: what each press carries out, and what the clock is doing afterwards."""

	transport = synthsync.core.transport

	cases = (
		((), transport.START, transport.BEGIN, transport.RUNNING),
		((), transport.PAUSE, transport.NOTHING, transport.STOPPED),
		((), transport.STOP, transport.NOTHING, transport.STOPPED),
		((transport.START,), transport.START, transport.NOTHING, transport.RUNNING),
		((transport.START,), transport.PAUSE, transport.FREEZE, transport.PAUSED),
		((transport.START,), transport.STOP, transport.END, transport.STOPPED),
		((transport.START, transport.PAUSE), transport.START, transport.CARRY_ON, transport.RUNNING),
		((transport.START, transport.PAUSE), transport.PAUSE, transport.NOTHING, transport.PAUSED),
		((transport.START, transport.PAUSE), transport.STOP, transport.END, transport.STOPPED),
		((), transport.PLAY_PAUSE, transport.BEGIN, transport.RUNNING),
		((transport.START,), transport.PLAY_PAUSE, transport.FREEZE, transport.PAUSED),
		((transport.START, transport.PAUSE), transport.PLAY_PAUSE, transport.CARRY_ON, transport.RUNNING),
	)

	for before, command, action, state in cases:

		machine = after(before)

		assert machine.press(command) == action, (before, command)
		assert machine.state == state, (before, command)


def check_a_whole_session_reads_as_expected () -> None:

	"""A run of presses, as someone might make them, goes through the right actions."""

	transport = synthsync.core.transport
	machine = transport.Transport()
	actions = []

	for command in (transport.START, transport.PAUSE, transport.PAUSE, transport.START, transport.START, transport.STOP, transport.STOP, transport.START):
		actions.append(machine.press(command))

	assert actions == [transport.BEGIN, transport.FREEZE, transport.NOTHING, transport.CARRY_ON, transport.NOTHING, transport.END, transport.NOTHING, transport.BEGIN]
	assert machine.state == transport.RUNNING


def check_an_unknown_command_is_refused () -> None:

	"""Anything but start, pause, stop and play/pause is refused, and changes nothing."""

	machine = after((synthsync.core.transport.START,))

	for command in ("", "resume", "START", "toggle"):

		try:
			machine.press(command)
		except ValueError:
			continue

		assert False, command

	assert machine.state == synthsync.core.transport.RUNNING


def check_the_names_are_listed () -> None:

	"""The states and commands are each listed once, and the page can't send play/pause, so two phones can't undo each other."""

	transport = synthsync.core.transport

	assert transport.STATES == ("stopped", "running", "paused")
	assert transport.COMMANDS == ("start", "pause", "stop")
	assert transport.PRESSES == ("start", "pause", "stop", "play/pause")
	assert transport.BUTTONS == ("play/pause", "stop")


def check_the_scheduler_carries_out_every_press_as_the_table_says () -> None:

	"""A press from a button is carried out by the scheduler on the second core, and must mean what the page's press means."""

	transport = synthsync.core.transport
	timeline = synthsync.core.timeline

	assert transport.NUMBERS == {transport.START: timeline.PRESS_START, transport.PAUSE: timeline.PRESS_PAUSE, transport.STOP: timeline.PRESS_STOP, transport.PLAY_PAUSE: timeline.PRESS_PLAY_PAUSE}

	def scheduler_in (state: str) -> synthsync.core.timeline.Timeline:

		"""Make a scheduler that is stopped, running or paused."""

		made = timeline.Timeline(2)

		if state != transport.STOPPED:
			made.start()

		if state == transport.PAUSED:
			made.pause()

		assert transport.state_of(made.running, made.paused) == state

		return made

	for state in transport.STATES:
		for command in transport.PRESSES:

			expected = transport.Transport()
			expected.state = state
			action = expected.press(command)

			scheduler = scheduler_in(state)
			changed = scheduler.press(transport.NUMBERS[command])

			assert transport.state_of(scheduler.running, scheduler.paused) == expected.state, (state, command)
			assert changed == (action != transport.NOTHING), (state, command)

	# Start from paused carries on from where the clock was held, and doesn't begin again at zero.
	scheduler = scheduler_in(transport.RUNNING)

	for _ in range(300):
		scheduler.next_word()

	scheduler.press(timeline.PRESS_PAUSE)
	held = (scheduler.table[synthsync.core.timeline.CELL_BEAT], scheduler.position())
	scheduler.press(timeline.PRESS_START)

	assert held[0] > 0
	assert (scheduler.table[synthsync.core.timeline.CELL_BEAT], scheduler.position()) == held

	# A stop from stopped does nothing at all: its stop pulses fire only when the clock really stops.
	scheduler = scheduler_in(transport.STOPPED)
	changes = scheduler.changes

	assert not scheduler.press(timeline.PRESS_STOP)
	assert scheduler.changes == changes

	# Anything that isn't a press is ignored.
	scheduler = scheduler_in(transport.RUNNING)

	assert not scheduler.press(0)
	assert not scheduler.press(9)
	assert scheduler.running and not scheduler.paused

	# Play/pause, pressed again and again, goes running, paused, running, and never back to zero.
	scheduler = scheduler_in(transport.STOPPED)
	states = []
	places = []

	for _ in range(5):

		scheduler.press(timeline.PRESS_PLAY_PAUSE)
		states.append(transport.state_of(scheduler.running, scheduler.paused))

		for _ in range(50):
			scheduler.next_word()

		places.append((scheduler.table[synthsync.core.timeline.CELL_BEAT], scheduler.position()))

	assert states == [transport.RUNNING, transport.PAUSED, transport.RUNNING, transport.PAUSED, transport.RUNNING], states
	assert places[0] > (0, 0) and places[1] == places[0] and places[2] > places[1] and places[3] == places[2] and places[4] > places[3], places
	assert scheduler.running and not scheduler.paused
