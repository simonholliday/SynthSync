"""The package imports, and says which version it is, on a laptop and on a Pico."""

import importlib.metadata
import pathlib
import re
import subprocess

import pytest

import synthsync
import synthsync.core
import synthsync.sim
import synthsync.web
import tests.test_checks
import tools.stage

# A version as the build makes one from a git tag: a release's numbers, then for
# a commit after it, which commit and whether anything had changed since.
VERSION = re.compile(r"\d+(\.\d+)*(\.dev\d+)?(\+[0-9a-z]+(\.[0-9a-z]+)*)?")


def test_the_version_is_the_one_the_build_took_from_git () -> None:

	"""The page's About view shows the version, which on a laptop is the installed package's."""

	assert synthsync.LAID_OUT == ""
	assert synthsync.__version__ == importlib.metadata.version("synthsync")
	assert VERSION.fullmatch(synthsync.__version__), synthsync.__version__


def test_a_picos_copy_has_the_version_written_in (tmp_path: pathlib.Path) -> None:

	"""MicroPython can't ask which version is installed, so the copy laid out for a Pico says it, and nothing else in the copy is changed."""

	tools.stage.copy_synthsync(tmp_path)

	source = (tools.stage.ROOT / "synthsync" / "__init__.py").read_text()
	copy = (tmp_path / "synthsync" / "__init__.py").read_text()

	assert copy != source
	assert copy == source.replace('LAID_OUT = ""', "LAID_OUT = %r" % synthsync.__version__)


def test_micropython_reads_the_version_written_into_a_picos_copy (tmp_path: pathlib.Path) -> None:

	"""The pinned MicroPython, given the copy laid out for a Pico, gives the version the laptop has, never asking for a package."""

	micropython = tests.test_checks.find_micropython()

	if micropython is None:
		pytest.skip("no MicroPython here")

	tools.stage.copy_synthsync(tmp_path)

	ran = subprocess.run([micropython, "-c", "import synthsync, sys; print(synthsync.__version__, 'importlib' in sys.modules)"], cwd=tmp_path, capture_output=True, text=True, timeout=30)

	assert ran.stdout.split() == [synthsync.__version__, "False"], ran.stdout + ran.stderr


def test_a_copy_with_nowhere_to_write_the_version_is_refused (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Were the line the version goes in changed or gone, laying SynthSync out says so, rather than putting a Pico's copy on with no version."""

	tree = tmp_path / "tree"
	(tree / "synthsync").mkdir(parents=True)
	(tree / "synthsync" / "__init__.py").write_text('LAID_OUT = "1.0.0"\n')

	monkeypatch.setattr(tools.stage, "ROOT", tree)

	with pytest.raises(ValueError, match="LAID_OUT"):
		tools.stage.copy_synthsync(tmp_path / "copy")


def test_host_side_packages_import () -> None:

	"""Everything except the Pico-only code imports on a laptop."""

	for module in (synthsync.core, synthsync.sim, synthsync.web):
		assert module.__doc__
