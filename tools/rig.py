"""Time real pulses on a Pico and say pass or fail.

Every timing claim SynthSync makes rests on this. It copies SynthSync and the
rig's board half to a temporary folder, lets the Pico run them from there, and
reads back one result line for each test. Tests that save write files of their
own, named from `/_rig`, and remove them. `powerup` and `air` write over
SynthSync's own settings, keeping a copy aside meanwhile, and put them back
whatever happens. The next test puts back a copy one cut short left.

    python -m tools.rig freerun
    python -m tools.rig player sweep pause startstop --load all
    python -m tools.rig air --address 192.168.1.23
    python -m tools.rig --list

`freerun` proves the rig itself, and `word` holds the second core's word
maker to the scheduler's `next_word`, word for word, on the Pico. The rest test
the real player: `player` times
its fastest output in the busiest pattern, `cluster` times one whose edges
crowd together with three others', `clustermidi` does that with MIDI's clock
going, as SynthSync plays by default, `eight` times output 1 with all eight
outputs as busy as the page can make them, so it drives GP2 to GP9, `sweep`
changes the tempo many times a
second, `burst` sends every setting at once over and over, `collect` changes
outputs while the first core collects a large heap straight after each change,
`pause` pauses and carries on a dozen times, `startstop` starts and stops over
and over, `position` asks where the clock has got to
and checks the answers against the Pico's own clock, `inputs` presses each
button through the jumper and watches both lights, `led` times every flash of
the tempo LED against the beat, `powerup` starts the clock
as switching on does, from good, damaged and missing settings, `network`
brings the Pico's own WiFi network up, changes its password and looks for
other networks, `page` asks for the page and its API over and over, and `save`
saves the settings about a hundred times while the clock runs. `save` leaves its file
on the Pico for `saved` to read back after a reset and remove, so run the two
together. `stopsave` saves with the clock stopped and paused, starts it the
moment a save ends, and taps play/pause through the jumper while a save is
being made, stopped and playing: no start may wait and no tap may be lost. `shortfall` gives one flash write far too little cover on purpose, and
passes when the player's counts of an empty queue and of writes that outlast
their cover notice it: it is the one test where a timing fault is wanted, so
it isn't in the usual list.

Only `air` goes over the air. It runs the whole of SynthSync on the WiFi
network the Pico has saved, and for as long as it runs this computer uses the
page hard over that network, with `tools/hammer.py`, while a fast output is
timed. It needs `--address`, which is where the Pico is on that network, and
this computer has to be able to reach it there. It passes when every pulse was
exact and the page really was being used throughout. A request that went
unanswered is counted in the line as `failed`, and doesn't fail the test:
this tool is for the timing. `network` and `page` test everything on the
Pico's side of the radio without it.

It needs a Pico 2 W running MicroPython on a USB port, with GP0 jumpered to
GP1. It exits with 0 when every test passed and 1 when any failed.
"""

import argparse
import glob
import pathlib
import shutil
import sys
import tempfile
import threading

import tools.hammer
import tools.install
import tools.stage

ROOT = pathlib.Path(__file__).parent.parent

TESTS = ("freerun", "word", "player", "cluster", "clustermidi", "eight", "sweep", "burst", "collect", "pause", "startstop", "position", "inputs", "led", "midi", "extremes", "powerup", "network", "page", "air", "save", "saved", "stopsave", "shortfall")

# The test that needs the Pico's address on a network, and how long to give a round of
# requests that failed to be seen as the end of the test, in seconds.
AIR = "air"
AIR_GRACE = 6.0
LOADS = ("idle", "gc", "flash", "wifi", "ble", "all")


def find_device () -> str | None:

	"""Find the first Pico running MicroPython on a USB port, or None if there isn't one."""

	found = sorted(glob.glob("/dev/serial/by-id/usb-MicroPython*"))

	return found[0] if found else None


def find_mpremote () -> str:

	"""Find mpremote, which talks to the Pico."""

	return tools.stage.find_tool("mpremote") or "mpremote"


def stage (folder: pathlib.Path, compiler: str) -> None:

	"""Lay out what the Pico needs in a folder on local disk: SynthSync, Microdot, the page and the rig's board half.

	Everything is compiled, as it is when SynthSync is installed for real,
	with the scheduler as native code for the Pico 2's processor. Left as
	source, the Pico would have to compile it all as it loads, which leaves
	its memory too broken up for the larger tests. Microdot's test client
	goes too: the `page` test makes its requests with it.
	"""

	tools.stage.copy_synthsync(folder)
	tools.stage.copy_microdot(folder, test_client=True)
	tools.stage.pack_page(folder)
	shutil.copy(ROOT / "tools" / "board" / "rig.py", folder / "rig.py")
	shutil.copy(ROOT / "tools" / "board" / "rig_extremes.py", folder / "rig_extremes.py")
	shutil.copy(ROOT / "tools" / "board" / "rig_busiest.py", folder / "rig_busiest.py")
	shutil.copy(ROOT / "tools" / "board" / "rig_word.py", folder / "rig_word.py")

	tools.stage.compile_all(folder, compiler)


def run_on_board (device: str, folder: pathlib.Path, code: str, timeout: int) -> str:

	"""Run a line of Python on the Pico, with the folder mounted, and give back what it printed.

	A Pico that has just been restarted is waited for.
	"""

	# A soft reset first clears whatever an earlier run left on the second core.
	worked, said = tools.install.ask([find_mpremote(), "connect", device, "soft-reset", "mount", str(folder), "exec", code], None, timeout)

	return said


def run_on_air (device: str, folder: pathlib.Path, address: str) -> tuple[str, tools.hammer.Totals]:

	"""Run the board's `air` test, use its page hard over the network for as long as it runs, and give back what the Pico printed and what the asking came to."""

	said: list[str] = []

	def board () -> None:

		"""Run the board's half, which comes back when it is over."""

		said.append(run_on_board(device, folder, "import rig; rig.run(%r)" % AIR, 300))

	running = threading.Thread(target=board)
	running.start()

	totals = tools.hammer.keep_at_it(address, 80, running.is_alive, grace=AIR_GRACE)

	running.join()

	return said[0] if said else "", totals


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
	parser.add_argument("--address", help="where the Pico is on the WiFi network it has joined, which the air test needs")
	parser.add_argument("--list", action="store_true", help="list the tests and loads, and stop")
	options = parser.parse_args(arguments)

	if options.list or not options.tests:
		print("tests: " + ", ".join(TESTS))
		print("loads: " + ", ".join(LOADS))
		return 0

	for test in options.tests:
		if test not in TESTS:
			parser.error("there is no test called %s" % test)

	if AIR in options.tests and not options.address:
		parser.error("the air test needs --address, which is where the Pico is on the WiFi network it has joined")

	device = options.device or find_device()

	if device is None:
		print("No Pico running MicroPython was found on a USB port.")
		return 1

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is None:
		print(tools.stage.NO_COMPILER)
		return 1

	failed = False

	with tempfile.TemporaryDirectory(prefix="synthsync-rig-") as scratch:

		folder = pathlib.Path(scratch)
		stage(folder, compiler)

		for test in options.tests:

			asked = None

			if test == AIR:
				output, asked = run_on_air(device, folder, options.address)
			else:
				output = run_on_board(device, folder, "import rig; rig.run(%r, %r)" % (test, options.load), 300)

			found = results(output)

			if not found:
				print("%s: no result came back. The Pico said:" % test)
				print(output)
				failed = True
				continue

			for name, passed, details in found:

				# Over the air, the timing only counts if the page really was being used while it was measured.
				if asked is not None:
					passed = passed and asked["rounds"] >= 3 and asked["changes"] >= 300 and asked["faults"] == 0
					details += " asked: " + tools.hammer.describe(asked)

				print("%s  %s  %s" % ("PASS" if passed else "FAIL", name, details))
				failed = failed or not passed

			if asked is not None:
				for words in tools.hammer.notes:
					print("      " + words)

	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
