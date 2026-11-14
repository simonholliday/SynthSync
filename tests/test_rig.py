"""The timing rig's laptop half reads results and lays out files correctly.

The rig itself needs a real Pico and is run by hand. These cover the parts that
don't: picking results out of what the Pico prints, and what gets copied.
"""

import ast
import pathlib

import pytest

import picoclock.web.app
import tools.rig
import tools.stage


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


@pytest.fixture()
def laid_out (monkeypatch: pytest.MonkeyPatch) -> None:

	"""Stand in for the compiler and the laying out of files, which a run with a pretend Pico doesn't need."""

	monkeypatch.setattr(tools.stage, "find_tool", lambda name: name)
	monkeypatch.setattr(tools.rig, "stage", lambda folder, compiler: None)


def test_no_compiler_is_reported_as_a_failure (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""Without mpy-cross the scheduler would run as bytecode, which isn't PicoClock as installed, so the rig refuses."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: "/dev/null")
	monkeypatch.setattr(tools.stage, "find_tool", lambda name: None)
	monkeypatch.setattr(tools.rig, "run_on_board", lambda device, folder, code, timeout: "RIG freerun PASS pulses=3 worst_cycles=0 load=idle")

	assert tools.rig.main(["freerun"]) == 1
	assert "mpy-cross" in capsys.readouterr().out


def test_no_pico_is_reported_as_a_failure (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""With no board plugged in, the rig says so and fails rather than passing by default."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: None)

	assert tools.rig.main(["freerun"]) == 1
	assert "No Pico" in capsys.readouterr().out


@pytest.mark.usefixtures("laid_out")
def test_a_run_with_no_result_line_fails (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""If the Pico prints an error and no result, that is a failure, and the error is shown."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: "/dev/null")
	monkeypatch.setattr(tools.rig, "run_on_board", lambda device, folder, code, timeout: "Traceback: boom")

	assert tools.rig.main(["freerun"]) == 1
	assert "boom" in capsys.readouterr().out


@pytest.mark.usefixtures("laid_out")
def test_a_failed_test_fails_the_run (monkeypatch: pytest.MonkeyPatch) -> None:

	"""One FAIL line is enough to make the whole run fail."""

	monkeypatch.setattr(tools.rig, "find_device", lambda: "/dev/null")
	monkeypatch.setattr(tools.rig, "run_on_board", lambda device, folder, code, timeout: "RIG freerun FAIL pulses=3 worst_cycles=9 load=idle")

	assert tools.rig.main(["freerun"]) == 1


def test_every_test_the_laptop_offers_is_one_the_board_has () -> None:

	"""The two halves of the rig each list the tests, and the lists have to be the same."""

	tree = ast.parse((tools.rig.ROOT / "tools" / "board" / "rig.py").read_text())
	tables = [node for node in tree.body if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "TESTS"]

	assert len(tables) == 1
	assert isinstance(tables[0].value, ast.Dict)
	assert sorted(ast.literal_eval(key) for key in tables[0].value.keys if key is not None) == sorted(tools.rig.TESTS)


def test_staging_copies_picoclock_and_the_board_half (tmp_path: pathlib.Path) -> None:

	"""The folder the Pico runs from holds the package, Microdot, the page and the rig, and no caches."""

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is None:
		pytest.skip("this machine has no mpy-cross to compile for the Pico with")

	tools.rig.stage(tmp_path, compiler)

	core = tmp_path / "picoclock" / "core"

	assert (tmp_path / "rig.mpy").exists()
	assert (tmp_path / "picoclock" / "device" / "player.mpy").exists()
	assert not list(tmp_path.rglob("__pycache__"))

	# Nothing is left as source for the Pico to compile, which would leave its memory too broken up for the larger tests.
	assert not list(tmp_path.rglob("*.py"))

	# The scheduler is compiled for the Pico 2's processor, which a file says in its third byte. The rest is bytecode.
	assert (core / "timeline.mpy").read_bytes()[2] >> 2 != 0
	assert (core / "ratio.mpy").read_bytes()[2] >> 2 == 0

	# Microdot is there compiled, with the test client the page test makes its requests with.
	assert sorted(path.name for path in (tmp_path / "microdot").iterdir()) == sorted(pathlib.Path(part).stem + ".mpy" for part in tools.stage.MICRODOT_PARTS + (tools.stage.MICRODOT_TEST_CLIENT,))

	# And the page is there gzipped, as the Pico serves it.
	assert sorted(path.name for path in (tmp_path / "picoclock" / "web" / "static").iterdir()) == ["index.html.gz"]
