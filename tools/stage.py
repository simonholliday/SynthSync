"""Lay out what a Pico needs to run PicoClock, in a folder on local disk.

The install tool, the timing rig and the tests each need PicoClock and
Microdot in a shape MicroPython can import. This is the one place that shape
is written down, so the three can't drift apart.

Nothing here writes anywhere but the folder it is given.
"""

import gzip
import pathlib
import shutil
import subprocess
import sys

import microdot

import picoclock.web.app

ROOT = pathlib.Path(__file__).parent.parent

# The parts of Microdot PicoClock uses, which are all the Pico gets, and the
# part only the tests use.
MICRODOT_PARTS = ("__init__.py", "microdot.py", "websocket.py", "helpers.py")
MICRODOT_TEST_CLIENT = "test_client.py"

# The Pico 2's processor, as the compiler names it.
PROCESSOR = "armv7emsp"

# What is compiled to the processor's own instructions and not to bytecode.
# The scheduler works out every pulse on the Pico's second core. Compiled this
# way it runs about half as fast again, and, which matters more, none of its
# functions takes memory to be called. One that did would wait whenever the
# first core was tidying its memory, for longer than the player's words last.
NATIVE = ("picoclock/core/timeline.py",)

# What to say when there is no compiler, since PicoClock can't safely run without one.
NO_COMPILER = "mpy-cross wasn't found. PicoClock's scheduler has to be compiled for the Pico's processor: left for the Pico to compile, the clock would stumble every time the Pico tidied its memory."

# The folders of PicoClock a Pico has no use for: the laptop stand-in, and the
# page's files, which go gzipped.
NOT_FOR_THE_PICO = ("__pycache__", "sim", "static")

# The program a Pico runs when it is switched on, once there is one.
MAIN = "main.py"


def find_tool (name: str) -> str | None:

	"""Find a program beside the Python this is running on, or on the PATH."""

	beside = pathlib.Path(sys.executable).parent / name

	if beside.exists():
		return str(beside)

	return shutil.which(name)


def copy_picoclock (folder: pathlib.Path) -> None:

	"""Copy PicoClock's own code into a folder, without what only a laptop uses."""

	shutil.copytree(ROOT / "picoclock", folder / "picoclock", ignore=shutil.ignore_patterns(*NOT_FOR_THE_PICO))


def copy_microdot (folder: pathlib.Path, test_client: bool = False) -> None:

	"""Copy the parts of Microdot PicoClock uses into a folder MicroPython can import them from.

	MicroPython can't be pointed at this Python's own packages: it would find
	the laptop's versions of things it has its own of.
	"""

	source = pathlib.Path(microdot.__file__).parent
	(folder / "microdot").mkdir()

	for part in MICRODOT_PARTS + ((MICRODOT_TEST_CLIENT,) if test_client else ()):
		shutil.copy(source / part, folder / "microdot" / part)


def compile_one (compiler: str, folder: pathlib.Path, name: str) -> None:

	"""Compile one file in a folder for the Pico, and remove its source.

	`name` is the file's path within the folder. It is also the name an error
	on the Pico gives for the file, so it reads the same there as here.
	"""

	source = folder / name
	command = [compiler, "-march=" + PROCESSOR, "-s", name, "-o", str(source.with_suffix(".mpy"))]

	if name in NATIVE:
		command += ["-X", "emit=native"]

	subprocess.run(command + [str(source)], check=True)
	source.unlink()


def compile_scheduler (folder: pathlib.Path, compiler: str) -> None:

	"""Compile only the scheduler, leaving the rest as source for the Pico to compile as it loads."""

	for name in NATIVE:
		compile_one(compiler, folder, name)


def compile_all (folder: pathlib.Path, compiler: str) -> None:

	"""Compile every Python file in a folder but the one the Pico runs at power-up.

	A compiled file loads faster and costs the Pico far less memory than one
	it has to compile itself.
	"""

	for source in sorted(folder.rglob("*.py")):

		name = str(source.relative_to(folder))

		if name != MAIN:
			compile_one(compiler, folder, name)


def pack_page (folder: pathlib.Path) -> None:

	"""Gzip each of the page's files into a folder, where the app looks for them.

	The app sends a gzipped file as it is, so the Pico never holds the plain
	one. The time stamp is left out so the same page always packs to the
	same bytes, and a file that hasn't changed isn't copied again.
	"""

	source = ROOT / "picoclock" / "web" / "static"
	target = folder / "picoclock" / "web" / "static"

	target.mkdir(parents=True)

	for name in sorted(picoclock.web.app.PAGE_FILES):
		(target / (name + ".gz")).write_bytes(gzip.compress((source / name).read_bytes(), 9, mtime=0))


def stage_for_pico (folder: pathlib.Path, compiler: str) -> list[str]:

	"""Lay out everything a Pico needs to run PicoClock, and name what is at the top of it.

	That is PicoClock's code and Microdot, compiled, the page gzipped, and
	the program that runs at power-up.
	"""

	copy_picoclock(folder)
	copy_microdot(folder)

	top = ["picoclock", "microdot"]

	if (ROOT / MAIN).exists():
		shutil.copy(ROOT / MAIN, folder / MAIN)
		top.append(MAIN)

	compile_all(folder, compiler)
	pack_page(folder)

	return top
