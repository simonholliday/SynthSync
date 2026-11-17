"""The traffic generator uses the page as it says it does, tried here against the laptop stand-in over real sockets.

Against a real Pico it is run by hand and by the timing rig's `air` test.
"""

import asyncio
import threading
import time
import typing

import pytest

import tests.stand
import tools.hammer
import tools.page


@pytest.fixture()
def stand () -> typing.Iterator[tests.stand.Stand]:

	"""A stand-in PicoClock of its own, since this changes its settings hard."""

	stand = tests.stand.Stand()

	tools.hammer.notes.clear()

	yield stand

	stand.stop()


def test_a_round_uses_two_pages_a_browser_and_plain_requests_and_leaves_things_as_it_found_them (stand: tests.stand.Stand) -> None:

	"""One round: the two pages stay in step, every request is answered, and what it changed is put back."""

	stand.change({"do": "output", "index": 2, "name": "ratio", "value": "3"})
	stand.change({"do": "output", "index": 3, "name": "pulse", "value": 42})

	until = time.monotonic() + 1.0
	totals = tools.hammer.keep_at_it("127.0.0.1", stand.port, lambda: time.monotonic() < until)

	assert tools.hammer.went_well(totals), tools.hammer.notes
	assert totals["rounds"] == 1

	# Two pages opened, the page opened twice at once twice over, and a change by POST for each whole second.
	assert totals["requests"] >= 2 + 4 + 4
	assert totals["failed"] == 0

	# Thirty changes a second for the five seconds a round lasts, and the listening page heard of them.
	assert 120 <= totals["changes"] <= 160
	assert totals["heard"] >= 20

	# Outputs 1 and 2 are never touched, so that they can be timed, and the tempo is left alone unless asked.
	assert stand.now("tempo")["tempo"] == 12000
	assert stand.now("output", 0)["ratio"] == "1" and stand.now("output", 1)["ratio"] == "1"

	# What it changed is back as it was found.
	assert stand.now("output", 2)["ratio"] == "3"
	assert stand.now("output", 3)["pulse"] == 42

	assert "rounds=1 out_of_step=0" in tools.hammer.describe(totals)

	# The stand-in said how well it kept up: nothing held it, and it gave up on neither page.
	assert totals["holds"] == 0 and totals["held_ms"] == 0 and totals["given_up"] == 0
	assert tools.hammer.describe(totals).endswith("holds=0 held_ms=0 given_up=0")


def test_the_tempo_is_dragged_only_when_asked_and_put_back_after (stand: tests.stand.Stand) -> None:

	"""With the tempo dragged too, the listening page must end on the last tempo set, and the tempo it found is restored."""

	stand.change({"do": "tempo", "value": 9900})

	until = time.monotonic() + 1.0
	totals = tools.hammer.keep_at_it("127.0.0.1", stand.port, lambda: time.monotonic() < until, tempo=True)

	assert tools.hammer.went_well(totals), tools.hammer.notes
	assert stand.now("tempo")["tempo"] == 9900


def test_a_picoclock_that_never_answers_counts_no_rounds () -> None:

	"""With nothing at the address it gives up waiting, and that is not counted as having gone well."""

	until = time.monotonic() + 1.0
	totals = tools.hammer.keep_at_it("127.0.0.1", tests.stand.free_port(), lambda: time.monotonic() < until, wait=1.0)

	assert totals["rounds"] == 0
	assert not tools.hammer.went_well(totals)
	assert totals["holds"] == -1 and totals["given_up"] == -1
	assert tools.hammer.main(["127.0.0.1", "--port", str(tests.stand.free_port()), "--seconds", "1"]) == 1


def test_a_round_that_fails_as_a_test_ends_is_not_counted_against_it (stand: tests.stand.Stand) -> None:

	"""The rig stops PicoClock at the end of its test, which fails the round under way. Within the grace allowed, that round is left out."""

	began = time.monotonic()

	# The stand-in is stopped a second and a half in, under the first round, and the test is over half a second later.
	stopping = threading.Timer(1.5, stand.process.terminate)
	stopping.start()

	totals = tools.hammer.keep_at_it("127.0.0.1", stand.port, lambda: time.monotonic() - began < 2.0, grace=4.0)

	stopping.join()
	stand.process.wait(timeout=10)

	assert totals["failed"] == 0 and totals["out_of_step"] == 0
	assert totals["rounds"] == 0

	# Something did go wrong in that round. It just isn't held against PicoClock.
	assert tools.hammer.notes


def test_a_round_that_fails_while_the_test_goes_on_is_counted (stand: tests.stand.Stand) -> None:

	"""With the test still going after a round has failed, the failure stands."""

	began = time.monotonic()

	stopping = threading.Timer(1.5, stand.process.terminate)
	stopping.start()

	# The test goes on for another twelve seconds, far past the grace allowed.
	totals = tools.hammer.keep_at_it("127.0.0.1", stand.port, lambda: time.monotonic() - began < 14.0, grace=2.0)

	stopping.join()
	stand.process.wait(timeout=10)

	assert not tools.hammer.went_well(totals)
	assert totals["failed"] > 0 or totals["out_of_step"] > 0


@pytest.mark.parametrize("problem", [asyncio.IncompleteReadError(b"", 2), tools.page.Refused(403)])
def test_a_page_that_fails_to_open_fails_the_round_and_neither_page_is_left_open (monkeypatch: pytest.MonkeyPatch, problem: BaseException) -> None:

	"""A socket closed part way through opening, or one PicoClock refuses, is counted as a failure rather than ending the run, and both pages are closed."""

	opened: list[object] = []
	closed: list[object] = []

	class Page:

		"""Stands in for a page: the second one opened fails as told."""

		def __init__ (self, host: str, port: int = 80) -> None:

			"""Be ready to open."""

		async def open (self, origin: str | None = None) -> dict[str, object]:

			"""Open, or fail as told if this is the second."""

			opened.append(self)

			if len(opened) == 2:
				raise problem

			return {}

		async def close (self) -> None:

			"""Note being closed."""

			closed.append(self)

	monkeypatch.setattr(tools.page, "Page", Page)
	tools.hammer.notes.clear()
	totals = tools.hammer.new_totals()

	asyncio.run(tools.hammer.one_round("127.0.0.1", 80, totals, 1, False))

	assert totals["failed"] == 1 and totals["rounds"] == 0
	assert len(closed) == 2 and sorted(map(id, closed)) == sorted(map(id, opened))
	assert tools.hammer.notes[0].startswith("opening a second page failed")


def test_how_picoclock_has_kept_up_is_asked_until_it_answers (monkeypatch: pytest.MonkeyPatch) -> None:

	"""A first ask of how PicoClock has kept up that goes unanswered is asked again, so what held it up is counted rather than left unknown, and unknown never counts as having gone well."""

	answers: list[dict[str, int] | None] = [None, None, {"holds": 2, "held": 2500, "given_up": 0}, {"holds": 2, "held": 2500, "given_up": 0}]
	rounds: list[int] = []

	async def settings (host: str, port: int) -> dict[typing.Any, typing.Any]:

		"""Answer at once, with nothing to put back."""

		return {}

	async def health (host: str, port: int) -> dict[str, int] | None:

		"""Answer as the list says, nothing at first."""

		return answers.pop(0)

	async def one_round (host: str, port: int, totals: tools.hammer.Totals, turn: int, tempo: bool) -> None:

		"""Count a round that went well."""

		rounds.append(turn)
		totals["rounds"] += 1

	async def put_back (host: str, port: int, found: dict[typing.Any, typing.Any]) -> None:

		"""Put nothing back."""

	monkeypatch.setattr(tools.hammer, "settings", settings)
	monkeypatch.setattr(tools.hammer, "health", health)
	monkeypatch.setattr(tools.hammer, "one_round", one_round)
	monkeypatch.setattr(tools.hammer, "put_back", put_back)

	totals = tools.hammer.keep_at_it("127.0.0.1", 80, lambda: not rounds)

	assert answers == []
	assert totals["holds"] == 0 and totals["held_ms"] == 0 and totals["given_up"] == 0
	assert tools.hammer.went_well(totals)

	# Never told, it can't be said that nothing held PicoClock up.
	assert not tools.hammer.went_well(dict(totals, holds=-1))
