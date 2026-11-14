"""The traffic generator uses the page as it says it does, tried here against the laptop stand-in over real sockets.

Against a real Pico it is run by hand and by the timing rig's `air` test.
"""

import threading
import time
import typing

import pytest

import tests.stand
import tools.hammer


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

	# Two pages opened, the page's six files twice over, and a change by POST for each whole second.
	assert totals["requests"] >= 2 + 12 + 4
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
