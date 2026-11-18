"""Make the zip a release of SynthSync is published with.

    python -m tools.release FOLDER

The zip holds what an install puts on a Pico, compiled the same way, for a
musician to unzip and copy across with Thonny. It has no folder of its own
inside it, so unzipping gives a folder whose contents go straight onto the
Pico. It holds no `config.txt` and no saved settings, so copying a new release
over an old one leaves both alone: SynthSync makes `config.txt` itself the first
time it starts.

It is made from the code alone, and the same code always makes the same zip. It
is only made from a commit with a release's tag and nothing changed since, by
the MicroPython compiler the release is for, with SynthSync installed at that
version. After tagging, that means running `pip install -e '.[dev]'` again.
"""

import argparse
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import zipfile

import synthsync
import tools.stage

# The MicroPython a release is compiled for, which the README tells people to use.
MICROPYTHON = "1.29.0"

# What a release's version looks like: no `.dev`, and nothing after it.
RELEASE = re.compile(r"\d+\.\d+\.\d+")

# The date every file in the zip is given, the earliest a zip can hold, so that
# the same code makes the same zip whenever it is made.
WHEN = (1980, 1, 1, 0, 0, 0)


class NotReady (Exception):

	"""Why a release can't be made yet, in words to show."""


def git (*arguments: str) -> str | None:

	"""Give what a git command says about this checkout, or None if it failed."""

	try:
		done = subprocess.run(["git", *arguments], cwd=tools.stage.ROOT, capture_output=True, text=True, check=False)

	except OSError:
		return None

	return done.stdout.strip() if done.returncode == 0 else None


def ready (compiler: str | None) -> str:

	"""Check that a release can be made from what is here, and give the compiler to make it with."""

	version = synthsync.__version__

	if not RELEASE.fullmatch(version):
		raise NotReady("SynthSync is installed as version %s, which isn't a release. Tag the commit, then run pip install -e '.[dev]' again." % version)

	tag = git("describe", "--tags", "--exact-match", "HEAD")

	if tag is None:
		raise NotReady("This isn't a commit with a tag, in a git checkout, so it can't be a release.")

	if tag != "v" + version:
		raise NotReady("This commit is tagged %s, but SynthSync is installed as version %s. Run pip install -e '.[dev]' again." % (tag, version))

	changes = git("status", "--porcelain")

	if changes is None or changes:
		raise NotReady("This checkout has changes that aren't committed, so the zip wouldn't be what %s holds." % tag)

	if compiler is None:
		raise NotReady(tools.stage.NO_COMPILER)

	said = subprocess.run([compiler, "--version"], capture_output=True, text=True, check=False).stdout

	if "MicroPython v%s " % MICROPYTHON not in said:
		raise NotReady("A release is compiled for MicroPython %s, and this mpy-cross says: %s" % (MICROPYTHON, said.strip()))

	return compiler


def write_zip (source: pathlib.Path, path: pathlib.Path) -> int:

	"""Put every file under a folder into a zip, the same way every time, and say how many.

	It is written under another name first, so a zip that is there is whole.
	"""

	names = sorted(file.relative_to(source).as_posix() for file in source.rglob("*") if file.is_file())
	fresh = path.with_name(path.name + ".new")
	fresh.unlink(missing_ok=True)

	with open(fresh, "xb") as handle, zipfile.ZipFile(handle, "w") as archive:

		for name in names:

			entry = zipfile.ZipInfo(name, WHEN)
			entry.compress_type = zipfile.ZIP_DEFLATED
			entry.create_system = 3
			entry.external_attr = 0o644 << 16
			archive.writestr(entry, (source / name).read_bytes(), compresslevel=9)

	os.replace(fresh, path)

	return len(names)


def make (folder: pathlib.Path, compiler: str) -> tuple[pathlib.Path, int]:

	"""Lay out what a Pico needs, zip it into a folder for this version, and give the zip's path and how many files it holds."""

	folder.mkdir(parents=True, exist_ok=True)
	path = folder / ("SynthSync-%s.zip" % synthsync.__version__)

	with tempfile.TemporaryDirectory(prefix="synthsync-release-") as scratch:
		tools.stage.stage_for_pico(pathlib.Path(scratch), compiler)
		count = write_zip(pathlib.Path(scratch), path)

	return path, count


def main (arguments: list[str] | None = None) -> int:

	"""Make the zip, or say why it can't be made, and exit with 0 or 1."""

	parser = argparse.ArgumentParser(description="Make the zip a release of SynthSync is published with.")
	parser.add_argument("folder", type=pathlib.Path, help="where to put the zip")
	options = parser.parse_args(arguments)

	try:
		compiler = ready(tools.stage.find_tool("mpy-cross"))

	except NotReady as problem:
		print(problem)
		return 1

	path, count = make(options.folder, compiler)
	data = path.read_bytes()

	print("Made %s: %d files, %d KB." % (path, count, len(data) // 1024))
	print("SHA-256: %s" % hashlib.sha256(data).hexdigest())

	return 0


if __name__ == "__main__":
	sys.exit(main())
