"""The zip a release is published with holds what an install puts on a Pico, and is only made from a tagged commit."""

import pathlib
import subprocess
import tempfile
import zipfile

import pytest

import synthsync
import tests.test_checks
import tools.release
import tools.stage


@pytest.fixture()
def compiler () -> str:

	"""Give the compiler for the Pico, or skip the test on a machine that has none."""

	found = tools.stage.find_tool("mpy-cross")

	if found is None:
		pytest.skip("this machine has no mpy-cross to compile for the Pico with")

	return found


@pytest.fixture()
def tagged (monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:

	"""Make this a clean checkout of the commit tagged v1.2.3, with that version installed, and give what git says so a test can change it."""

	said: dict[str, str | None] = {"describe": "v1.2.3", "status": ""}

	monkeypatch.setattr(synthsync, "__version__", "1.2.3")
	monkeypatch.setattr(tools.release, "git", lambda *arguments: said[arguments[0]])

	return said


def held (path: pathlib.Path) -> dict[str, bytes]:

	"""Give every file in a zip, by its name."""

	with zipfile.ZipFile(path) as archive:
		return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.usefixtures("tagged")
def test_the_zip_holds_exactly_what_an_install_lays_out (tmp_path: pathlib.Path, compiler: str) -> None:

	"""Every file an install would copy is in it, byte for byte, and nothing else is."""

	path, count = tools.release.make(tmp_path / "out", compiler)
	laid_out = tmp_path / "laid-out"
	laid_out.mkdir()
	tools.stage.stage_for_pico(laid_out, compiler)

	expected = {file.relative_to(laid_out).as_posix(): file.read_bytes() for file in laid_out.rglob("*") if file.is_file()}

	assert path.name == "SynthSync-1.2.3.zip"
	assert held(path) == expected
	assert count == len(expected)
	assert not list((tmp_path / "out").glob("*.new"))


@pytest.mark.usefixtures("tagged")
def test_copying_the_zip_over_an_old_copy_can_replace_nothing_of_the_musicians (tmp_path: pathlib.Path, compiler: str) -> None:

	"""It unzips straight into what goes on the Pico, carries both licences, and holds no config.txt or saved settings."""

	path, _ = tools.release.make(tmp_path, compiler)
	names = set(held(path))

	assert {name.split("/")[0] for name in names} == {"main.py", "synthsync", "microdot"}
	assert {"synthsync/LICENSE", "microdot/LICENSE", "synthsync/core/default_config.mpy", "synthsync/web/static/index.html.gz"} <= names
	assert not [name for name in names if name.endswith((".txt", ".json"))]


@pytest.mark.usefixtures("tagged")
def test_the_same_code_makes_the_same_zip (tmp_path: pathlib.Path, compiler: str) -> None:

	"""Anyone can check a published zip by making it again, so nothing in it depends on when or where it was made."""

	first, _ = tools.release.make(tmp_path / "one", compiler)

	with tempfile.TemporaryDirectory(prefix="another-") as scratch:
		second, _ = tools.release.make(pathlib.Path(scratch), compiler)
		assert second.read_bytes() == first.read_bytes()


@pytest.mark.usefixtures("tagged")
def test_the_version_the_pico_will_show_is_the_release (tmp_path: pathlib.Path, compiler: str) -> None:

	"""The zip's own copy of SynthSync says which release it is."""

	path, _ = tools.release.make(tmp_path, compiler)

	assert b"1.2.3" in held(path)["synthsync/__init__.mpy"]


def test_a_release_is_compiled_for_the_micropython_the_project_is_held_to () -> None:

	"""The tool's version is the one the tests and the README are held to."""

	assert tools.release.MICROPYTHON == tests.test_checks.PINNED


@pytest.mark.parametrize("version, describe, status, words", [
	("0.1.dev40+g3c9edd7dd", "v0.1.0", "", "isn't a release"),
	("1.2.3", None, "", "isn't a commit with a tag"),
	("1.2.3", "v1.2.2", "", "tagged v1.2.2"),
	("1.2.3", "v1.2.3", " M README.md", "aren't committed"),
	("1.2.3", "v1.2.3", None, "aren't committed"),
], ids=["a version with .dev", "no tag", "the package older than the tag", "changes not committed", "git can't say"])
def test_anything_but_a_clean_tagged_commit_is_refused (tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tagged: dict[str, str | None], version: str, describe: str | None, status: str | None, words: str) -> None:

	"""The tool says why and makes nothing."""

	monkeypatch.setattr(synthsync, "__version__", version)
	tagged.update(describe=describe, status=status)

	assert tools.release.main([str(tmp_path / "out")]) == 1
	assert words in capsys.readouterr().out
	assert not (tmp_path / "out").exists()


@pytest.mark.usefixtures("tagged", "compiler")
def test_a_compiler_for_another_micropython_is_refused (tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:

	"""Files compiled for another release might not load on the MicroPython the README says to use."""

	monkeypatch.setattr(tools.release, "MICROPYTHON", "9.9.9")

	assert tools.release.main([str(tmp_path / "out")]) == 1
	assert "compiled for MicroPython 9.9.9" in capsys.readouterr().out
	assert not (tmp_path / "out").exists()


@pytest.mark.usefixtures("tagged")
def test_no_compiler_is_refused (tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:

	"""Without one the scheduler can't be made safe to run."""

	monkeypatch.setattr(tools.stage, "find_tool", lambda name: None)

	assert tools.release.main([str(tmp_path / "out")]) == 1
	assert tools.stage.NO_COMPILER in capsys.readouterr().out


@pytest.mark.usefixtures("tagged")
def test_it_says_what_it_made (tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], compiler: str) -> None:

	"""The path and a checksum are what a release's notes want."""

	assert tools.release.main([str(tmp_path)]) == 0

	said = capsys.readouterr().out

	assert str(tmp_path / "SynthSync-1.2.3.zip") in said and "SHA-256: " in said


def test_it_reads_a_real_checkout (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Against real git: a tagged clean commit passes, and an uncommitted file or a later commit doesn't."""

	def run (*arguments: str) -> None:

		"""Run git in the made-up checkout."""

		subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *arguments], cwd=tmp_path, check=True, capture_output=True)

	monkeypatch.setattr(tools.stage, "ROOT", tmp_path)
	monkeypatch.setattr(synthsync, "__version__", "1.2.3")

	run("init")
	(tmp_path / "a").write_text("a")
	run("add", "a")
	run("commit", "-m", "first")
	run("tag", "-a", "v1.2.3", "-m", "first")

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is not None:
		assert tools.release.ready(compiler) == compiler

	(tmp_path / "b").write_text("b")

	with pytest.raises(tools.release.NotReady, match="aren't committed"):
		tools.release.ready(compiler)

	run("add", "b")
	run("commit", "-m", "second")

	with pytest.raises(tools.release.NotReady, match="isn't a commit with a tag"):
		tools.release.ready(compiler)
