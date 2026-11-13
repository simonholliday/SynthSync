"""The timing rig's laptop half reads results and lays out files correctly.

The rig itself needs a real Pico and is run by hand. These cover the parts that
don't: picking results out of what the Pico prints, and what gets copied.
"""

import pathlib

import pytest

import tools.rig


def test_result_lines_are_picked_out_of_the_output () -> None:

	"""Only lines that begin with RIG are results, and each gives a test, a verdict and details."""

	output = "\n".join((
		"Local directory /tmp/x is mounted at /remote",
		"RIG freerun PASS pulses=1998 worst_cycles=0 load=idle",
		"some other line",
		"RIG player FAIL pulses=12 worst_cycles=5621 load=all: own network, bluetooth",
	))

	assert tools.rig.results(output) == [
		("freerun", True, "pulses=1998 worst_cycles=0 load=idle"),
		("player", False, "pulses=12 worst_cycles=5621 load=all: own network, bluetooth"),
	]
	assert tools.rig.results("Traceback (most recent call last):\n  ...") == []


def test_listing_needs_no_pico (capsys: pytest.CaptureFixture[str]) -> None:

	"""Asking what there is to run prints the tests and loads and succeeds."""

	assert tools.rig.main(["--list"]) == 0

	printed = capsys.readouterr().out

	assert "freerun" in printed
	assert "all" in printed


def test_no_pico_is_reported_as_a_failure (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""With no board plugged in, the rig says so and fails rather than passing by default."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: None)

	assert tools.rig.main(["freerun"]) == 1
	assert "No Pico" in capsys.readouterr().out


def test_a_run_with_no_result_line_fails (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""If the Pico prints an error and no result, that is a failure, and the error is shown."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: "/dev/null")
	monkeypatch.setattr(tools.rig, "run_on_board", lambda device, folder, code, timeout: "Traceback: boom")

	assert tools.rig.main(["freerun"]) == 1
	assert "boom" in capsys.readouterr().out


def test_a_failed_test_fails_the_run (monkeypatch: pytest.MonkeyPatch) -> None:

	"""One FAIL line is enough to make the whole run fail."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: "/dev/null")
	monkeypatch.setattr(tools.rig, "run_on_board", lambda device, folder, code, timeout: "RIG freerun FAIL pulses=3 worst_cycles=9 load=idle")

	assert tools.rig.main(["freerun"]) == 1


def test_staging_copies_picoclock_and_the_board_half (tmp_path: pathlib.Path) -> None:

	"""The folder the Pico runs from holds the package and the rig, and no caches."""

	tools.rig.stage(tmp_path)

	core = tmp_path / "picoclock" / "core"

	assert (tmp_path / "rig.py").exists()
	assert (tmp_path / "picoclock" / "device" / "player.py").exists()
	assert not list(tmp_path.rglob("__pycache__"))

	# The scheduler is there once: as source, or compiled if mpy-cross was to hand.
	assert (core / "timeline.py").exists() != (core / "timeline.mpy").exists()
