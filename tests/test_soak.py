"""The soak's laptop half reads the Pico's lines and uses the page as it says it does, tried here against the laptop stand-in.

The soak itself needs a real Pico, for hours, and is run by hand.
"""

import asyncio
import pathlib
import threading
import time
import typing

import pytest

import picoclock.core.ratio
import tests.stand
import tools.hammer
import tools.soak


@pytest.fixture()
def log (tmp_path: pathlib.Path) -> typing.Iterator[tools.soak.Log]:

	"""A log in a folder of its own."""

	log = tools.soak.Log(tmp_path / "soak.log")

	yield log

	log.close()


def test_name_value_pairs_are_picked_out_of_a_line () -> None:

	"""Each word with an equals sign gives a name and a value, and the rest are passed over."""

	assert tools.soak.fields("SOAK window n=3 tempo=12000 ratio=24/1 mode=joined") == {"n": "3", "tempo": "12000", "ratio": "24/1", "mode": "joined"}
	assert tools.soak.fields("SOAK spoiled") == {}


def test_every_tempo_with_every_ratio_keeps_output_2_within_what_a_window_holds () -> None:

	"""Between 1 and 120 pulses a second, whichever tempo and ratio the changes have chosen, snapped or free, at the soak's PPQN."""

	ppqn = tools.soak.PPQN

	for tempo in tools.soak.TEMPOS:
		for text in tools.soak.RATIOS + tools.soak.FREE_RATIOS:

			ratio = picoclock.core.ratio.parse(text, ppqn)
			pulses, beats = picoclock.core.ratio.effective(ratio, ppqn)
			per_second = tempo * pulses / (6000 * beats)

			assert 1 <= per_second <= 120, (tempo, text)

	# The ratios set with snap on are snap values, so setting them never moves them.
	for text in tools.soak.RATIOS:
		assert picoclock.core.ratio.is_snap(picoclock.core.ratio.parse(text, ppqn), ppqn), text

	# The free ones aren't, so turning snap on again always moves the ratio.
	for text in tools.soak.FREE_RATIOS:
		assert not picoclock.core.ratio.is_snap(picoclock.core.ratio.parse(text, ppqn), ppqn), text


def test_snap_turned_off_is_turned_on_again_at_the_next_change (log: tools.soak.Log) -> None:

	"""A free ratio lasts one window, and every pause, stop and mute ends by putting things back."""

	soak = tools.soak.Soak("127.0.0.1", 80, log, 1)
	seen = set()

	for _ in range(300):

		what, steps = tools.soak.plan(soak)
		seen.add(what.split()[0])

		if what.startswith("snap off"):
			assert tools.soak.plan(soak)[0] == "snap back on"

		if what.startswith(("pause", "stop")):
			assert steps[-1][0] == {"do": "start"}

		if what.startswith("mute"):
			assert steps[-1][0]["value"] is False

	assert seen == {"tempo", "ratio", "pause", "stop", "mute", "pulse", "snap"}


def test_the_picos_lines_are_counted_and_its_address_followed (log: tools.soak.Log) -> None:

	"""The page waits for the Pico to have joined, windows and spoiled windows are counted, a new address is used from then on, and the result line is kept."""

	soak = tools.soak.Soak("192.168.1.23", 80, log, 1)

	soak.heard("PICOCLOCK NETWORK mode=joining address=none reason=none")
	assert not soak.joined.is_set()

	soak.heard("PICOCLOCK NETWORK mode=joined address=192.168.1.23 reason=none")
	assert soak.joined.is_set()

	soak.heard("SOAK window n=1 tempo=12000 ratio=24/1 pulses=2880 worst_cycles=0 address=192.168.1.23")
	soak.heard("SOAK spoiled after_ms=1200")
	soak.heard("SOAK window n=2 tempo=12000 ratio=24/1 pulses=2880 worst_cycles=0 address=192.168.1.40")
	soak.heard("SOAK window n=3 tempo=12000 ratio=24/1 pulses=2880 worst_cycles=0 address=none")
	soak.heard("RIG soak PASS windows=3")

	assert soak.counts["windows"] == 3
	assert soak.counts["spoiled"] == 1
	assert soak.address == "192.168.1.40"
	assert soak.result == "RIG soak PASS windows=3"


def test_the_page_is_used_and_output_2_changed_after_windows (log: tools.soak.Log, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Against the stand-in: a page stays open, a round of hammering runs, PicoClock is asked how it kept up, and a change follows each window, leaving the clock running and output 2 sounding."""

	monkeypatch.setattr(tools.soak, "HAMMER_EVERY", 0.5)
	monkeypatch.setattr(tools.soak, "HEALTH_EVERY", 1.0)
	monkeypatch.setattr(tools.soak, "HOLD_OFF", (0.1, 0.2))
	monkeypatch.setattr(tools.soak, "CHANGE_CHANCE", 1.0)
	tools.hammer.notes.clear()

	stand = tests.stand.Stand()

	try:

		soak = tools.soak.Soak("127.0.0.1", stand.port, log, 7)

		def pico () -> None:

			"""Say a window has ended every half a second, as the Pico would every minute, and then end."""

			while soak.loop is None:
				time.sleep(0.05)

			for number in range(1, 13):
				time.sleep(0.5)
				soak.heard("SOAK window n=%d address=none" % number)

			time.sleep(1.0)
			soak.over.set()

		windows = threading.Thread(target=pico)
		windows.start()

		asyncio.run(tools.soak.use_page(soak))
		windows.join()

		counts = soak.counts

		assert counts["windows"] == 12
		assert counts["changes"] >= 6
		assert counts["refused"] == 0 and counts["unanswered"] == 0
		assert counts["phone_lost"] == 0
		assert counts["rounds"] >= 1 and counts["round_failures"] == 0 and counts["out_of_step"] == 0
		assert counts["asked"] >= 2 and counts["not_told"] == 0

		assert stand.now("transport")["state"] == "running"
		assert stand.now("output", 1)["muted"] is False

		# Each round of hammering ends with a pulse no round sets, so that there is something new to save.
		assert stand.now("output", 3)["pulse"] in tools.soak.SAVED_PULSES

		written = log.path.read_text()

		assert "change " in written and "health pages=" in written and "page   round 1:" in written

	finally:
		stand.stop()
