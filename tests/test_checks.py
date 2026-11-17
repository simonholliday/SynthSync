"""The shared checks pass on this Python and on MicroPython.

`synthsync/core` and `synthsync/web` run on the Pico as well as on a laptop, and
most of the faults that code can have show only on MicroPython. So every shared
check runs twice: once here as an ordinary test, and once in a MicroPython
started from here.
"""

import importlib
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

import tests.run_checks
import tools.stage

ROOT = pathlib.Path(__file__).parent.parent

# The MicroPython release the Pico runs, which the README tells people to install.
# Another one on this machine, such as one a Linux distribution ships, would
# test the shared code against the wrong release.
PINNED = "1.29.0"


def all_checks () -> list[tuple[str, str]]:

	"""List every shared check as (module, function), in a fixed order."""

	found = []

	for module in tests.run_checks.find_modules():
		for check in tests.run_checks.find_checks(tests.run_checks.load(module)):
			found.append((module, check))

	return found


def find_micropython () -> str | None:

	"""Find a MicroPython to run the checks under, or None if this machine has none.

	Looks at the MICROPYTHON variable first, then beside this Python, which is
	where a virtualenv can hold a link to the release the Pico runs, then on
	the PATH.
	"""

	named = os.environ.get("MICROPYTHON")

	if named:
		return named

	beside = pathlib.Path(sys.executable).parent / "micropython"

	if beside.exists():
		return str(beside)

	return shutil.which("micropython")


def run_under (interpreter: list[str], folder: pathlib.Path, library: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:

	"""Run the shared checks found under one folder, with one interpreter.

	`library` is a folder of extra packages for MicroPython to import from.
	"""

	environment = dict(os.environ)

	if library is not None:
		# This replaces MicroPython's whole search path, so its built-in modules go first.
		environment["MICROPYPATH"] = ".frozen:%s" % library

	return subprocess.run(
		interpreter + ["-m", "tests.run_checks"],
		cwd = folder,
		env = environment,
		capture_output = True,
		text = True,
		timeout = 300,
	)


def make_tree (folder: pathlib.Path, check_source: str) -> None:

	"""Build a tiny project in a folder, holding the runner and one check module."""

	checks = folder / "tests" / "checks"
	checks.mkdir(parents=True)

	(folder / "tests" / "__init__.py").write_text("")
	(checks / "__init__.py").write_text("")
	(checks / "check_planted.py").write_text(check_source)

	shutil.copy(ROOT / "tests" / "run_checks.py", folder / "tests" / "run_checks.py")


@pytest.mark.parametrize(("module", "check"), all_checks())
def test_check_passes_here (module: str, check: str) -> None:

	"""Each shared check passes on the Python the tests run on."""

	getattr(importlib.import_module("tests.checks." + module), check)()


def test_checks_pass_under_micropython (tmp_path: pathlib.Path) -> None:

	"""Every shared check passes on MicroPython too. A skip here is worth noticing."""

	micropython = find_micropython()

	if micropython is None:
		pytest.skip("this machine has no MicroPython to run the shared checks under")

	# The same parts of Microdot the Pico gets, and the test client the checks use.
	tools.stage.copy_microdot(tmp_path, test_client=True)

	result = run_under([micropython], ROOT, tmp_path)

	assert result.returncode == 0, result.stdout + result.stderr

	# Every one of them, on the release the Pico runs, so that a run of none, or on the wrong MicroPython, can't pass.
	assert "on micropython %s" % PINNED in result.stdout, "%s isn't MicroPython %s: %s" % (micropython, PINNED, result.stdout)
	assert "%d checks run, 0 failed, on micropython %s" % (len(all_checks()), PINNED) in result.stdout, result.stdout


def test_the_pinned_micropython_is_the_one_the_readme_names () -> None:

	"""The release the checks insist on is the one people are told to put on their Pico."""

	assert "**version %s**" % PINNED in (ROOT / "README.md").read_text(encoding="utf-8")


def test_runner_reports_a_check_that_fails (tmp_path: pathlib.Path) -> None:

	"""A failing check is named and turns the run's result to a failure."""

	make_tree(tmp_path, "def check_never_true () -> None:\n\tassert 1 + 1 == 3\n")

	result = run_under([sys.executable, "-B"], tmp_path)

	assert result.returncode == 1
	assert "FAIL check_planted.check_never_true" in result.stdout


def test_runner_catches_a_fault_only_micropython_has (tmp_path: pathlib.Path) -> None:

	"""Code that leans on something MicroPython lacks passes here and fails there."""

	micropython = find_micropython()

	if micropython is None:
		pytest.skip("this machine has no MicroPython to run the shared checks under")

	make_tree(tmp_path, "import typing\n\n\ndef check_nothing () -> None:\n\tassert typing is not None\n")

	here = run_under([sys.executable, "-B"], tmp_path)
	there = run_under([micropython], tmp_path)

	assert here.returncode == 0, here.stdout + here.stderr
	assert there.returncode == 1, there.stdout + there.stderr
	assert "FAIL importing check_planted" in there.stdout
