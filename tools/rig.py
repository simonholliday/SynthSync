"""Time real pulses on a Pico and say pass or fail.

Every timing claim PicoClock makes rests on this. It copies PicoClock and the
rig's board half to a temporary folder, lets the Pico run them from there, and
reads back one result line for each test. Nothing is written to the Pico's own
storage, apart from a small file the flash load writes and removes.

    python -m tools.rig freerun
    python -m tools.rig player sweep pause startstop --load all
    python -m tools.rig --list

`freerun` proves the rig itself. The rest test the real player: `player` times
its fastest output in the busiest pattern, `sweep` changes the tempo many
times a second, `pause` pauses and carries on a dozen times, `startstop`
starts and stops over and over, and `save` saves the settings about a hundred
times while the clock runs. `save` leaves its file on the Pico for `saved` to
read back after a reset and remove, so run the two together.

It needs a Pico 2 W running MicroPython on a USB port, with GP0 jumpered to
GP1. It exits with 0 when every test passed and 1 when any failed.
"""

import argparse
import glob
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).parent.parent

TESTS = ("freerun", "player", "sweep", "pause", "startstop", "save", "saved")
LOADS = ("idle", "gc", "flash", "wifi", "ble", "all")


def find_device () -> str | None:

	"""Find the first Pico running MicroPython on a USB port, or None if there isn't one."""

	found = sorted(glob.glob("/dev/serial/by-id/usb-MicroPython*"))

	return found[0] if found else None


def find_tool (name: str) -> str | None:

	"""Find a program beside the Python this is running on, or on the PATH."""

	beside = pathlib.Path(sys.executable).parent / name

	if beside.exists():
		return str(beside)

	return shutil.which(name)


def find_mpremote () -> str:

	"""Find mpremote, which talks to the Pico."""

	return find_tool("mpremote") or "mpremote"


def stage (folder: pathlib.Path) -> None:

	"""Lay out what the Pico needs in a folder on local disk: PicoClock and the rig's board half.

	If mpy-cross is to hand, the scheduler is compiled to native code for the
	Pico 2's processor, as it is when PicoClock is installed for real. Without
	it the Pico compiles the scheduler itself, to slower bytecode.
	"""

	shutil.copytree(ROOT / "picoclock", folder / "picoclock", ignore=shutil.ignore_patterns("__pycache__", "static"))
	shutil.copy(ROOT / "tools" / "board" / "rig.py", folder / "rig.py")

	compiler = find_tool("mpy-cross")

	if compiler is not None:

		source = folder / "picoclock" / "core" / "timeline.py"

		subprocess.run([compiler, "-march=armv7emsp", "-X", "emit=native", "-o", str(source.with_suffix(".mpy")), str(source)], check=True)
		source.unlink()


def run_on_board (device: str, folder: pathlib.Path, code: str, timeout: int) -> str:

	"""Run a line of Python on the Pico, with the folder mounted, and give back what it printed."""

	# A soft reset first clears whatever an earlier run left on the second core.
	command = [find_mpremote(), "connect", device, "soft-reset", "mount", str(folder), "exec", code]
	result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)

	return result.stdout + result.stderr


def results (output: str) -> list[tuple[str, bool, str]]:

	"""Pick the result lines out of what the Pico printed, as (test, passed, details)."""

	found = []

	for line in output.splitlines():

		if line.startswith("RIG "):
			_, name, verdict, details = line.split(" ", 3)
			found.append((name, verdict == "PASS", details))

	return found


def main (arguments: list[str] | None = None) -> int:

	"""Run the tests asked for and say how they went."""

	parser = argparse.ArgumentParser(description="Time real pulses on a Pico and say pass or fail.")
	parser.add_argument("tests", nargs="*", help="which tests to run: " + ", ".join(TESTS))
	parser.add_argument("--load", default="idle", choices=LOADS, help="what to keep the Pico busy with meanwhile")
	parser.add_argument("--device", help="the Pico's serial port; found automatically if left out")
	parser.add_argument("--list", action="store_true", help="list the tests and loads, and stop")
	options = parser.parse_args(arguments)

	if options.list or not options.tests:
		print("tests: " + ", ".join(TESTS))
		print("loads: " + ", ".join(LOADS))
		return 0

	for test in options.tests:
		if test not in TESTS:
			parser.error("there is no test called %s" % test)

	device = options.device or find_device()

	if device is None:
		print("No Pico running MicroPython was found on a USB port.")
		return 1

	failed = False

	with tempfile.TemporaryDirectory(prefix="picoclock-rig-") as scratch:

		folder = pathlib.Path(scratch)
		stage(folder)

		for test in options.tests:

			output = run_on_board(device, folder, "import rig; rig.run(%r, %r)" % (test, options.load), 300)
			found = results(output)

			if not found:
				print("%s: no result came back. The Pico said:" % test)
				print(output)
				failed = True
				continue

			for name, passed, details in found:
				print("%s  %s  %s" % ("PASS" if passed else "FAIL", name, details))
				failed = failed or not passed

	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
