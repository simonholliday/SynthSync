"""The Pico's tempo knob finds out whether it is there, follows the hand and lets a lost wire go, against a pretend pin and a pretend clock."""

import asyncio
import importlib
import sys
import time
import types
import typing

import pytest

import synthsync.core.settings

import tests.checks.check_clock

# The pad's bits, as the chip has them.
PULL_DOWN = 1 << 2
PULL_UP = 1 << 3
INPUT = 1 << 6

# What a pin with nothing on it reads, by pull, as measured on a Pico 2 W.
OPEN = {"up": 4042, "down": 48, "none": 951}

# How far a knob at mid-travel lets the pulls move the pin, each way, as measured.
HELD = 165


class Bench:

	"""A pretend Pico: a pad, an ADC and a clock that only moves when something waits."""

	def __init__ (self) -> None:

		"""Begin with a knob fitted, at the middle of its travel."""

		self.now = 0
		self.fitted = True
		self.position = 2048
		self.pad = 0x12
		self.reads = 0
		self.input_seen = False
		self.pulled_at: list[int] = []

	def level (self) -> int:

		"""Give what the pin reads just now."""

		up = bool(self.pad & PULL_UP)
		down = bool(self.pad & PULL_DOWN)
		pull = "up" if up else "down" if down else "none"

		if not self.fitted:
			return OPEN[pull]

		return self.position + (HELD if up else -HELD if down else 0)

	def read_u16 (self) -> int:

		"""Read the pin as MicroPython gives it, in sixteen bits."""

		self.reads += 1

		return max(0, min(4095, self.level())) << 4

	def __getitem__ (self, address: int) -> int:

		"""Read the pad's register."""

		return self.pad

	def __setitem__ (self, address: int, value: int) -> None:

		"""Write the pad's register, noting anything that matters."""

		assert address == 0x40038000 + 4 + 4 * 26

		self.pad = value
		self.input_seen = self.input_seen or bool(value & INPUT)

		if value & (PULL_UP | PULL_DOWN):
			self.pulled_at.append(self.now)


@pytest.fixture()
def bench (monkeypatch: pytest.MonkeyPatch) -> typing.Iterator[Bench]:

	"""Stand in for the Pico's own modules, and load the knob's code against them."""

	pretend = Bench()

	async def sleep_ms (milliseconds: int) -> None:

		"""Let this much pretend time pass, and everything else have a turn."""

		pretend.now += milliseconds
		await asyncio.sleep(0)

	machine = types.SimpleNamespace(
		ADC = lambda pin: types.SimpleNamespace(read_u16=pretend.read_u16),
		Pin = lambda number: number,
		mem32 = pretend,
	)

	monkeypatch.setitem(sys.modules, "machine", machine)
	monkeypatch.setattr(asyncio, "sleep_ms", sleep_ms, raising=False)
	monkeypatch.setattr(time, "ticks_ms", lambda: pretend.now, raising=False)
	monkeypatch.setattr(time, "ticks_diff", lambda later, earlier: later - earlier, raising=False)
	monkeypatch.delitem(sys.modules, "synthsync.device.knob", raising=False)

	yield pretend

	sys.modules.pop("synthsync.device.knob", None)


def run_for (bench: Bench, clock: typing.Any, milliseconds: int, during: typing.Callable[[int], None] | None = None) -> typing.Any:

	"""Run the knob's job until this much pretend time has passed, calling `during` with the time as it goes, and give the knob."""

	module = importlib.import_module("synthsync.device.knob")
	knob = module.TempoKnob(clock)

	async def work () -> None:

		"""Run the job beside a watcher that ends it."""

		job = asyncio.ensure_future(knob.run())

		for _ in range(200000):

			await asyncio.sleep(0)

			if during is not None:
				during(bench.now)

			if bench.now >= milliseconds or job.done():
				break

		if job.done():
			job.result()

		job.cancel()

		try:
			await job

		except asyncio.CancelledError:
			pass

	asyncio.run(work())

	return knob


def make_clock (tempo: int = 12000, noted: int = synthsync.core.settings.NO_KNOB) -> typing.Any:

	"""Make a clock on a stand-in engine, with this tempo and this place noted for the knob."""

	clock = tests.checks.check_clock.make(4)[0]

	clock.settings.tempo = tempo
	clock.settings.knob_at = noted

	return clock


def test_with_no_knob_the_pin_is_left_alone_and_the_tempo_with_it (bench: Bench) -> None:

	"""The pulls show nothing there, and after that nothing is read at all."""

	bench.fitted = False
	clock = make_clock()

	knob = run_for(bench, clock, 500)
	reads = bench.reads

	assert not knob.fitted
	assert clock.settings.tempo == 12000
	assert clock.settings.knob_at == synthsync.core.settings.NO_KNOB
	assert reads <= 32
	assert bench.pad & (PULL_UP | PULL_DOWN) == 0


def test_the_pulls_leave_the_digital_input_off (bench: Bench) -> None:

	"""With it on, this chip's pull-down couldn't bring the pin low."""

	run_for(bench, make_clock(), 300)

	assert bench.pulled_at
	assert not bench.input_seen
	assert bench.pad & (PULL_UP | PULL_DOWN) == 0


def test_a_knob_never_noted_sets_the_tempo_at_power_up (bench: Bench) -> None:

	"""The middle of the travel is about 116 to 120 BPM, and where it is gets noted to be saved."""

	clock = make_clock(tempo=20000)
	knob = run_for(bench, clock, 300)

	assert knob.fitted
	assert knob.rule.following
	assert clock.settings.tempo == knob.rule.tempo_at(2048) == 12000
	assert clock.settings.knob_at == 2048


def test_a_knob_where_it_was_leaves_the_saved_tempo_playing (bench: Bench) -> None:

	"""The page's tempo survives a restart."""

	clock = make_clock(tempo=14000, noted=2050)
	knob = run_for(bench, clock, 300)

	assert knob.fitted
	assert not knob.rule.following
	assert clock.settings.tempo == 14000
	assert clock.settings.knob_at == 2050


def test_turning_the_knob_moves_the_tempo_and_notes_where_it_went (bench: Bench) -> None:

	"""Once it has the tempo, it follows the hand, through the clock."""

	clock = make_clock(tempo=12000, noted=2048)

	def turn (now: int) -> None:

		"""Turn the knob up, a little at a time, after a moment."""

		if now > 300:
			bench.position = min(3000, 2048 + (now - 300))

	knob = run_for(bench, clock, 2000, turn)

	assert clock.settings.tempo == knob.rule.tempo_at(3000)
	assert abs(clock.settings.knob_at - 3000) <= 41


def test_a_wire_coming_off_stops_the_knob_before_it_moves_the_tempo (bench: Bench) -> None:

	"""The pin drifts as if the knob were turning: the check made as it starts moving sees nothing there."""

	clock = make_clock(tempo=12000, noted=2048)

	def lose (now: int) -> None:

		"""Let the wire go after a good rest, with the pin creeping away."""

		if now > 2000:
			bench.fitted = False

	knob = run_for(bench, clock, 3000, lose)
	reads = bench.reads

	assert not knob.fitted
	assert clock.settings.tempo == 12000
	assert bench.now >= 3000 or reads > 0
