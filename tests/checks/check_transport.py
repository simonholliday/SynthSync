"""Start, pause and stop mean the same on the Pico's Python as on a laptop's."""

import picoclock.core.timeline
import picoclock.core.transport


def after (commands: tuple[str, ...]) -> picoclock.core.transport.Transport:

	"""Make a transport and press these commands in turn."""

	transport = picoclock.core.transport.Transport()

	for command in commands:
		transport.press(command)

	return transport


def check_a_new_transport_is_stopped () -> None:

	"""Nothing runs until something says start."""

	assert picoclock.core.transport.Transport().state == picoclock.core.transport.STOPPED


def check_every_command_from_every_state () -> None:

	"""All nine cases: what each command carries out, and what the clock is doing afterwards."""

	transport = picoclock.core.transport

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
	)

	for before, command, action, state in cases:

		machine = after(before)

		assert machine.press(command) == action, (before, command)
		assert machine.state == state, (before, command)


def check_a_whole_session_reads_as_expected () -> None:

	"""A run of presses, as someone might make them, goes through the right actions."""

	transport = picoclock.core.transport
	machine = transport.Transport()
	actions = []

	for command in (transport.START, transport.PAUSE, transport.PAUSE, transport.START, transport.START, transport.STOP, transport.STOP, transport.START):
		actions.append(machine.press(command))

	assert actions == [transport.BEGIN, transport.FREEZE, transport.NOTHING, transport.CARRY_ON, transport.NOTHING, transport.END, transport.NOTHING, transport.BEGIN]
	assert machine.state == transport.RUNNING


def check_an_unknown_command_is_refused () -> None:

	"""Anything but start, pause and stop is refused, and changes nothing."""

	machine = after((picoclock.core.transport.START,))

	for command in ("", "resume", "START", "toggle"):

		try:
			machine.press(command)
		except ValueError:
			continue

		assert False, command

	assert machine.state == picoclock.core.transport.RUNNING


def check_the_names_are_listed () -> None:

	"""The states and commands are each listed once, for the page to offer."""

	transport = picoclock.core.transport

	assert transport.STATES == ("stopped", "running", "paused")
	assert transport.COMMANDS == ("start", "pause", "stop")


def check_the_scheduler_carries_out_every_press_as_the_table_says () -> None:

	"""A press from a button is carried out by the scheduler on the second core, and must mean what the page's press means."""

	transport = picoclock.core.transport
	timeline = picoclock.core.timeline

	assert transport.NUMBERS == {transport.START: timeline.PRESS_START, transport.PAUSE: timeline.PRESS_PAUSE, transport.STOP: timeline.PRESS_STOP}

	def scheduler_in (state: str) -> picoclock.core.timeline.Timeline:

		"""Make a scheduler that is stopped, running or paused."""

		made = timeline.Timeline(2)

		if state != transport.STOPPED:
			made.start()

		if state == transport.PAUSED:
			made.pause()

		assert transport.state_of(made.running, made.paused) == state

		return made

	for state in transport.STATES:
		for command in transport.COMMANDS:

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
	held = (scheduler.beat, scheduler.position())
	scheduler.press(timeline.PRESS_START)

	assert held[0] > 0
	assert (scheduler.beat, scheduler.position()) == held

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
