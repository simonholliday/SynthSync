"""Every change goes through the clock the same way, on the Pico's Python as on a laptop's."""

import asyncio
import sys

import picoclock.core.clock
import picoclock.core.settings
import picoclock.core.transport

# mypy treats this name as true, and nothing else does, so `typing` is never
# imported where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing


class Engine:

	"""Stands in for whatever plays the clock, and notes every call it is given."""

	def __init__ (self) -> None:

		"""Begin with nothing noted, and nothing wrong."""

		self.calls: "list[tuple[typing.Any, ...]]" = []
		self.position = -1
		self.faults = 0

		# What the engine says the clock is really doing, which a check sets as a
		# button on the Pico would, or None while a press is on its way.
		self.state: "str | None" = None

		# How many calls to refuse, as an engine that's too busy does, and how
		# many to let through before that.
		self.refuse = 0
		self.after = 0

	def _note (self, call: "tuple[typing.Any, ...]") -> None:

		"""Note one call, or refuse it."""

		if self.after:
			self.after -= 1

		elif self.refuse:
			self.refuse -= 1
			raise OSError("The clock isn't taking commands")

		self.calls.append(call)

	def set_tempo (self, tempo: int) -> None:

		"""Note a tempo."""

		self._note(("set_tempo", tempo))

	def set_ratio (self, output: int, pulses: int, beats: int) -> None:

		"""Note a ratio."""

		self._note(("set_ratio", output, pulses, beats))

	def set_mute (self, output: int, muted: bool) -> None:

		"""Note a mute."""

		self._note(("set_mute", output, muted))

	def set_width (self, output: int, ticks: int) -> None:

		"""Note a pulse length."""

		self._note(("set_width", output, ticks))

	def set_role (self, output: int, role: int) -> None:

		"""Note a role."""

		self._note(("set_role", output, role))

	def press (self, command: str) -> None:

		"""Note a press."""

		self._note(("press", command))

	def transport (self) -> "str | None":

		"""Say whatever the check has set."""

		return self.state

	def status (self) -> "dict[str, typing.Any]":

		"""Say whatever the check has set."""

		return {"running": False, "paused": False, "position": self.position, "faults": self.faults}

	def named (self, name: str) -> "list[tuple[typing.Any, ...]]":

		"""Give the calls of one kind, in order."""

		return [call for call in self.calls if call[0] == name]


class Storage:

	"""Stands in for whatever saves the settings, and counts what it's asked."""

	def __init__ (self) -> None:

		"""Begin with nothing asked."""

		self.changes = 0
		self.forgets = 0

	def changed (self) -> None:

		"""Count a change to be saved."""

		self.changes += 1

	def forget (self) -> None:

		"""Count a request to remove what was saved."""

		self.forgets += 1


class Watcher:

	"""Notes what a clock tells whoever is watching."""

	def __init__ (self, clock: picoclock.core.clock.Clock) -> None:

		"""Begin watching a clock."""

		self.told: "list[tuple[typing.Any, typing.Any]]" = []

		clock.watch(self.hear)

	def hear (self, what: "str | int", by: object) -> None:

		"""Note one thing told."""

		self.told.append((what, by))


def make (outputs: int = 4) -> "tuple[picoclock.core.clock.Clock, Engine, Storage]":

	"""Make a clock on a stand-in engine, with stand-in storage."""

	engine = Engine()
	storage = Storage()
	clock = picoclock.core.clock.Clock(picoclock.core.settings.Settings(outputs), engine, storage)

	return clock, engine, storage


def run (work: "typing.Coroutine[typing.Any, typing.Any, None]") -> None:

	"""Run one check's coroutine to its end, with nothing left over from the check before."""

	# MicroPython keeps one loop for good, tasks and all, unless a new one is asked for.
	if sys.implementation.name == "micropython":
		asyncio.new_event_loop()

	asyncio.run(work)


def check_loading_gives_the_engine_every_setting () -> None:

	"""At power-up the engine is given the tempo and all four parts of every output."""

	clock, engine, storage = make(3)

	clock.settings.set_tempo(9050)
	clock.settings.set_output(1, "ratio", "3/2")
	clock.settings.set_output(1, "pulse", "half")
	clock.settings.set_output(2, "role", "run")
	clock.settings.set_output(2, "muted", True)

	clock.load()

	assert engine.named("set_tempo") == [("set_tempo", 9050)]
	assert engine.named("set_ratio") == [("set_ratio", 0, 1, 1), ("set_ratio", 1, 3, 2), ("set_ratio", 2, 1, 1)]
	assert engine.named("set_width") == [("set_width", 0, 10000), ("set_width", 1, 0), ("set_width", 2, 10000)]
	assert engine.named("set_role") == [("set_role", 0, 0), ("set_role", 1, 0), ("set_role", 2, 3)]
	assert engine.named("set_mute") == [("set_mute", 0, False), ("set_mute", 1, False), ("set_mute", 2, True)]
	assert len(engine.calls) == 13

	# Loading is not a change, so nothing is saved and nothing more is owed.
	assert storage.changes == 0

	clock.flush()

	assert len(engine.calls) == 13


def check_a_change_is_checked_saved_told_and_then_given_to_the_engine () -> None:

	"""A change takes hold in the settings at once, and reaches the engine at the next flush."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	clock.set_tempo(13000)

	assert clock.settings.tempo == 13000
	assert storage.changes == 1
	assert watcher.told == [(picoclock.core.clock.TEMPO, None)]
	assert engine.calls == []

	clock.flush()

	assert engine.calls == [("set_tempo", 13000)]

	clock.flush()

	assert engine.calls == [("set_tempo", 13000)]


def check_an_output_that_is_off_can_still_be_changed () -> None:

	"""Switched off, an output's ratio, snap, pulse and role can all still be changed, and each reaches the engine while it stays off."""

	clock, engine, storage = make()

	clock.set_output(1, "muted", True)
	clock.flush()

	engine.calls = []

	clock.set_output(1, "ratio", "3/2")
	clock.set_output(1, "snap", False)
	clock.set_output(1, "pulse", 20)
	clock.set_output(1, "role", "run")
	clock.flush()

	assert clock.settings.outputs[1].muted
	assert not clock.settings.outputs[1].snap
	assert ("set_ratio", 1, 3, 2) in engine.calls
	assert ("set_width", 1, 20000) in engine.calls
	assert ("set_role", 1, picoclock.core.settings.ROLES.index("run")) in engine.calls
	assert engine.named("set_mute") == []


def check_only_the_latest_of_a_burst_reaches_the_engine () -> None:

	"""However many times a setting changes between flushes, the engine is given the last value once."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	for step in range(50):
		clock.set_tempo(10000 + step * 100)
		clock.set_output(2, "ratio", "%d/7" % (step + 1))

	clock.flush()

	assert engine.calls == [("set_tempo", 14900), ("set_ratio", 2, 50, 7)]

	# Every one of them was still told and noted to be saved.
	assert len(watcher.told) == 100
	assert storage.changes == 100


def check_each_setting_reaches_the_engine_in_the_form_it_takes () -> None:

	"""A ratio goes as two whole numbers, a pulse in microseconds, a role as its number and a mute as yes or no."""

	clock, engine, storage = make()

	clock.set_output(1, "ratio", "4/3")
	clock.flush()
	clock.set_output(1, "pulse", 25)
	clock.flush()
	clock.set_output(1, "pulse", "half")
	clock.flush()
	clock.set_output(1, "role", "stop")
	clock.flush()
	clock.set_output(1, "muted", True)
	clock.flush()

	assert engine.calls == [
		("set_ratio", 1, 4, 3),
		("set_width", 1, 25000),
		("set_width", 1, 0),
		("set_role", 1, 2),
		("set_mute", 1, True),
	]


def check_snap_is_saved_and_told_but_never_reaches_the_engine () -> None:

	"""Snap only changes how the page's knob moves."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	clock.set_output(3, "snap", False)
	clock.flush()

	assert clock.settings.outputs[3].snap is False
	assert storage.changes == 1
	assert watcher.told == [(3, None)]
	assert engine.calls == []


def check_turning_snap_on_takes_a_ratio_set_freely_to_the_nearest_snap_value () -> None:

	"""With snap off any ratio can be set. Turned back on, snap moves it to the nearest snap value, judged as it sounds, and the engine is given that ratio."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	clock.set_output(2, "snap", False)
	clock.set_output(2, "ratio", "2.9")
	clock.flush()

	engine.calls = []
	watcher.told = []

	clock.set_output(2, "snap", True)

	assert clock.settings.outputs[2].snap is True
	assert clock.settings.outputs[2].ratio == (3, 1)
	assert watcher.told == [(2, None)]

	clock.flush()

	assert engine.calls == [("set_ratio", 2, 3, 1)]

	# By proportion, 2.8 is nearer ×8/3 than ×3.
	clock.set_output(2, "snap", False)
	clock.set_output(2, "ratio", "2.8")
	clock.set_output(2, "snap", True)

	assert clock.settings.outputs[2].ratio == (8, 3)


def check_turning_snap_on_leaves_a_snap_value_where_it_is () -> None:

	"""A ratio that is already a snap value doesn't move, and the engine hears nothing. Nor does saying snap is on when it already is."""

	clock, engine, storage = make()

	clock.set_output(2, "snap", False)
	clock.set_output(2, "ratio", "1.5")
	clock.flush()

	engine.calls = []

	clock.set_output(2, "snap", True)
	clock.flush()

	assert clock.settings.outputs[2].ratio == (3, 2)
	assert engine.calls == []

	# Typed with snap on, a ratio stays as it was typed, even when snap is said to be on again.
	clock.set_output(2, "ratio", "2.9")
	clock.set_output(2, "snap", True)

	assert clock.settings.outputs[2].ratio == (29, 10)


def check_a_bad_value_changes_nothing_and_tells_nobody () -> None:

	"""A value the settings refuse is refused in their words, and nothing else happens."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	said = []

	changes: "list[typing.Callable[[], None]]" = [
		lambda: clock.set_tempo(1999),
		lambda: clock.set_tempo("120"),
		lambda: clock.set_output(0, "ratio", "2"),
		lambda: clock.set_output(4, "ratio", "2"),
		lambda: clock.set_output(1, "ratio", "200"),
		lambda: clock.set_output(1, "colour", "red"),
		lambda: clock.set_own_password("short"),
		lambda: clock.set_network("", ""),
		lambda: clock.set_host_name("-pico"),
	]

	for change in changes:

		try:
			change()
		except ValueError as error:
			said.append(str(error))

	assert len(said) == 9
	assert said[0] == "The tempo has to be between 20 and 300 BPM"
	assert said[2] == "Output 1 is the main clock, and always pulses once a beat"
	assert said[3] == "There is no such output"

	clock.flush()

	assert clock.settings.to_dict() == picoclock.core.settings.Settings(4).to_dict()
	assert storage.changes == 0
	assert watcher.told == []
	assert engine.calls == []


def check_every_press_reaches_the_engine_and_those_that_change_something_are_told () -> None:

	"""Each press says what the transport's table says, the engine is given every one to carry out by the same table, and one that does nothing tells nobody."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	assert clock.press("pause") == picoclock.core.transport.NOTHING
	assert clock.press("stop") == picoclock.core.transport.NOTHING
	assert watcher.told == []

	assert clock.press("start") == picoclock.core.transport.BEGIN
	assert clock.press("start") == picoclock.core.transport.NOTHING
	assert clock.press("pause") == picoclock.core.transport.FREEZE
	assert clock.press("start") == picoclock.core.transport.CARRY_ON
	assert clock.press("stop") == picoclock.core.transport.END

	assert engine.calls == [("press", command) for command in ("pause", "stop", "start", "start", "pause", "start", "stop")]
	assert watcher.told == [(picoclock.core.clock.TRANSPORT, None)] * 4
	assert clock.transport.state == picoclock.core.transport.STOPPED

	# Whether the clock is running is never saved: it always starts at power-up.
	assert storage.changes == 0

	try:
		clock.press("rewind")
		assert False, "a command the clock doesn't have was taken"
	except ValueError:
		pass


def check_a_change_made_just_before_a_start_reaches_the_engine_first () -> None:

	"""A start doesn't overtake a tempo set a moment earlier."""

	clock, engine, storage = make()

	clock.set_tempo(8000)
	clock.set_output(1, "ratio", "2")
	clock.press("start")

	assert engine.calls == [("set_tempo", 8000), ("set_ratio", 1, 2, 1), ("press", "start")]


def check_a_reset_brings_back_the_defaults_and_forgets_what_was_saved () -> None:

	"""After a reset every setting is as new, the saved file is gone, and the clock carries on."""

	clock, engine, storage = make(2)

	clock.set_tempo(15000)
	clock.set_output(1, "ratio", "3")
	clock.set_network("Studio", "a password")
	clock.press("start")
	clock.flush()

	watcher = Watcher(clock)
	before = len(engine.calls)
	changes = storage.changes

	clock.reset("someone")

	assert clock.settings.to_dict() == picoclock.core.settings.Settings(2).to_dict()
	assert storage.forgets == 1
	assert storage.changes == changes
	assert watcher.told == [(picoclock.core.clock.EVERYTHING, "someone")]
	assert clock.transport.state == picoclock.core.transport.RUNNING

	clock.flush()

	given = engine.calls[before:]

	assert ("set_tempo", 12000) in given
	assert ("set_ratio", 1, 1, 1) in given
	assert len(given) == 9
	assert engine.named("stop") == []


def check_network_settings_are_saved_and_told () -> None:

	"""The network to join, the Pico's own password and its name each change the settings and tell the watchers."""

	clock, engine, storage = make()
	watcher = Watcher(clock)

	clock.set_network("Studio", "a password", 1)
	clock.set_own_password("another one", 2)
	clock.set_host_name("Clock-Two", 3)

	assert clock.settings.network_name == "Studio"
	assert clock.settings.network_password == "a password"
	assert clock.settings.own_password == "another one"
	assert clock.settings.host_name == "clock-two"

	clock.forget_network(4)

	assert clock.settings.network_name == ""
	assert clock.settings.network_password == ""

	assert watcher.told == [(picoclock.core.clock.NETWORK, by) for by in (1, 2, 3, 4)]
	assert storage.changes == 4

	clock.flush()

	assert engine.calls == []


def check_whoever_made_a_change_is_passed_to_the_watchers_untouched () -> None:

	"""The `by` given with a change is what each watcher is handed, whatever it is."""

	clock, engine, storage = make()
	first = Watcher(clock)
	second = Watcher(clock)
	who = (3, 17)

	clock.set_tempo(11000, who)
	clock.set_output(2, "muted", True, who)
	clock.press("start", who)

	assert first.told == [(picoclock.core.clock.TEMPO, who), (2, who), (picoclock.core.clock.TRANSPORT, who)]
	assert second.told == first.told
	assert first.told[0][1] is who


def check_a_value_the_engine_could_not_take_is_given_again () -> None:

	"""If the engine is too busy to take a value, the next flush gives it again, and nothing twice."""

	clock, engine, storage = make()

	clock.set_output(1, "ratio", "5")
	clock.set_output(1, "muted", True)
	clock.set_tempo(7000)
	clock.flush()

	engine.calls = []
	clock.set_tempo(7100)
	clock.set_output(1, "ratio", "6")
	clock.set_output(1, "muted", False)

	# The tempo gets through, and the first of the output's two parts doesn't.
	engine.after = 1
	engine.refuse = 1

	try:
		clock.flush()
		assert False, "the engine's refusal was swallowed"
	except OSError:
		pass

	assert engine.calls == [("set_tempo", 7100)]

	clock.flush()

	assert engine.calls == [("set_tempo", 7100), ("set_ratio", 1, 6, 1), ("set_mute", 1, False)]

	clock.flush()

	assert len(engine.calls) == 3


def check_running_the_clock_keeps_the_engine_up_to_date () -> None:

	"""With `run` going, a change reaches the engine by itself, and a busy engine doesn't end it."""

	async def work () -> None:

		clock, engine, storage = make()
		task = asyncio.create_task(clock.run())

		clock.set_tempo(9900)
		await asyncio.sleep(0.1)

		assert engine.calls == [("set_tempo", 9900)]

		engine.refuse = 2
		clock.set_tempo(9800)
		await asyncio.sleep(0.2)

		assert engine.calls == [("set_tempo", 9900), ("set_tempo", 9800)]

		task.cancel()
		await asyncio.sleep(0)

	run(work())


def check_the_clock_catches_up_with_a_button_pressed_on_the_pico () -> None:

	"""A button on the Pico changes what the clock is doing where it is played, and the clock hears of it afterwards, once."""

	transport = picoclock.core.transport
	clock, engine, storage = make()
	watcher = Watcher(clock)

	clock.press("start")
	watcher.told.clear()

	# While a press is on its way the engine can't say, and nothing changes.
	clock.catch_up()

	assert clock.transport.state == transport.RUNNING
	assert watcher.told == []

	# Stop pressed on the Pico: told once, and only once.
	engine.state = transport.STOPPED
	clock.catch_up()
	clock.catch_up()

	assert clock.transport.state == transport.STOPPED
	assert watcher.told == [(picoclock.core.clock.TRANSPORT, None)]

	# The engine agreeing tells nobody anything.
	clock.catch_up()

	assert len(watcher.told) == 1

	# A start from the page right after a stop button the clock hadn't yet
	# heard of begins again from zero, as it should from stopped.
	engine.state = transport.RUNNING
	clock.catch_up()
	engine.state = transport.STOPPED
	watcher.told.clear()

	assert clock.press("start", "page") == transport.BEGIN
	assert watcher.told == [(picoclock.core.clock.TRANSPORT, None), (picoclock.core.clock.TRANSPORT, "page")]

	# Whether the clock is running is never saved, however it changed.
	assert storage.changes == 0


def check_running_the_clock_catches_up_with_the_buttons_by_itself () -> None:

	"""With `run` going, a button pressed on the Pico reaches every watcher within a round or two."""

	async def work () -> None:

		clock, engine, storage = make()
		watcher = Watcher(clock)
		task = asyncio.create_task(clock.run())

		engine.state = picoclock.core.transport.RUNNING
		await asyncio.sleep(0.1)

		assert clock.transport.state == picoclock.core.transport.RUNNING
		assert watcher.told == [(picoclock.core.clock.TRANSPORT, None)]

		# A busy engine still lets the buttons through.
		engine.refuse = 5
		engine.state = picoclock.core.transport.PAUSED
		clock.set_tempo(9800)
		await asyncio.sleep(0.1)

		assert clock.transport.state == picoclock.core.transport.PAUSED

		task.cancel()
		await asyncio.sleep(0)

	run(work())
