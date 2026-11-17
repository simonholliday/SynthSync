"""Copy SynthSync onto a Pico, or take it off again.

    python -m tools.install
    python -m tools.install --wipe
    python -m tools.install --remove

The first puts SynthSync on the Pico in place of any copy already there, and
leaves the settings the Pico has saved alone. `--wipe` empties the Pico's
storage first, which also forgets its settings and its WiFi. `--remove` takes
SynthSync off and stops, and with `--wipe` leaves the Pico empty.

Everything is laid out in a temporary folder on local disk first: SynthSync
and Microdot compiled for the Pico, and the page gzipped. Once it is copied,
the Pico is asked to load every part and then to run SynthSync for a few
seconds, in which the clock has to keep time and the page has to be served.
Only when that has worked is `main.py` copied, which is what makes SynthSync
start by itself, and the Pico restarted. It ends by saying which WiFi network
to join to reach the page.

That order keeps the Pico reachable. A copy that hangs is found while nothing
yet starts it at power-up, so unplugging the Pico and plugging it in again
gets it back. Were `main.py` already there, it would hang again every time.

It needs a Pico 2 W running MicroPython on a USB port, and says which one it
is about to change. With more than one plugged in, name one with `--device`.
It exits with 0 when everything worked and 1 when anything didn't.
"""

import argparse
import glob
import pathlib
import subprocess
import sys
import tempfile
import time

import synthsync.core.wifi
import tools.stage

# The file the join tool puts on the Pico for a moment, with a network's name
# and password in it. The Pico removes it as it reads it, and so does the join
# tool afterwards. An install, or taking SynthSync off, removes it too, in case
# anything ever left it behind.
JOIN_FILE = "_join.txt"

# What SynthSync's folder on the Pico was called before it had that name. An
# install, or taking SynthSync off, removes it, so an old copy is never left
# beside the new one. The settings it saved are kept.
OLD_NAMES = ["picoclock"]

# Run on the Pico to take files and folders off it. Given no names, it takes
# off everything there is.
REMOVE = """
import os

def remove (path):
	try:
		kind = os.stat(path)[0]
	except OSError:
		return
	if kind & 0x4000:
		for name in os.listdir(path):
			remove(path + "/" + name)
		os.rmdir(path)
	else:
		os.remove(path)

for name in %r or os.listdir("/"):
	remove("/" + name)

print("REMOVED")
"""

# Run on the Pico to load every part of SynthSync and say how much memory is left.
LOAD = """
import gc

for name in %r:
	__import__(name)

gc.collect()
print("LOADED", gc.mem_free())
"""

# Run on the Pico to start SynthSync as power-up does, and stop it after a few seconds.
TRY = """
import synthsync.device.start

synthsync.device.start.run(%d)
"""

# How long the Pico runs SynthSync for when a new copy is tried, in milliseconds.
TRY_FOR = 3000

# Said when an install fails after the old copy has been taken off.
NOTHING_STARTS = "The old copy is already off the Pico, so nothing starts when it is switched on until an install works. Put right what went wrong and run this again."

# A Pico that has just been restarted is off its USB port for a second or two.
# This is how many times to ask for it, how long to leave between, in seconds,
# and what mpremote says when it isn't there yet.
ASK_TIMES = 5
ASK_AGAIN_AFTER = 1.0
NOT_THERE_YET = ("failed to access", "no device found", "could not open", "could not enter raw repl")


def find_devices () -> list[str]:

	"""Find every Pico running MicroPython on a USB port."""

	return sorted(glob.glob("/dev/serial/by-id/usb-MicroPython*"))


def ask (command: list[str], folder: pathlib.Path | None = None, timeout: int = 180) -> tuple[bool, str]:

	"""Run one mpremote command, and say whether it worked and what came back.

	A Pico that isn't on its USB port yet, having just been restarted, is
	waited for, as is one that is too busy starting its radio to be
	interrupted.
	"""

	for asked in range(ASK_TIMES):

		try:
			result = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=timeout)
		except subprocess.TimeoutExpired:
			return False, "The Pico didn't answer within %d seconds." % timeout

		said = result.stdout + result.stderr

		if result.returncode == 0 or not any(words in said for words in NOT_THERE_YET):
			break

		time.sleep(ASK_AGAIN_AFTER)

	return result.returncode == 0, said


def on_pico (device: str, arguments: list[str], folder: pathlib.Path | None = None, timeout: int = 180) -> tuple[bool, str]:

	"""Give the Pico some mpremote commands, and say whether they worked and what came back.

	A soft reset goes first. It stops whatever the Pico was running, the clock
	on its second core included, and doesn't start it again.
	"""

	return ask([tools.stage.find_tool("mpremote") or "mpremote", "connect", device, "soft-reset"] + arguments, folder, timeout)


def modules (folder: pathlib.Path) -> list[str]:

	"""Name every module of SynthSync laid out in a folder, as Python imports them."""

	found = []

	for path in sorted((folder / "synthsync").rglob("*")):

		if path.suffix in (".py", ".mpy") and path.stem != "__init__":
			found.append(".".join(path.relative_to(folder).with_suffix("").parts))

	return found


def size (folder: pathlib.Path) -> tuple[int, int]:

	"""Count the files laid out in a folder, and the bytes in them."""

	files = [path for path in folder.rglob("*") if path.is_file()]

	return len(files), sum(path.stat().st_size for path in files)


def trial (output: str) -> dict[str, str]:

	"""Read what the Pico said of its trial run into names and values, or nothing if it didn't say.

	The line it prints ends with the name of the Pico's own network, which
	has a space in it.
	"""

	for line in output.splitlines():

		if line.startswith("SYNTHSYNC RAN "):

			rest, _, own = line.partition(" own=")
			found = dict(part.split("=", 1) for part in rest.split()[2:] if "=" in part)
			found["own"] = own.strip()

			return found

	return {}


def remove (device: str, names: list[str]) -> bool:

	"""Take the named files and folders off the Pico, or everything with no names, and say whether it worked."""

	worked, output = on_pico(device, ["exec", REMOVE % (names,)])

	if not worked or "REMOVED" not in output:
		print("Nothing could be taken off the Pico. It said:")
		print(output)
		return False

	return True


def install (device: str, wipe: bool, start: bool) -> bool:

	"""Lay SynthSync out, copy it to the Pico, check that it loads, and say whether all of that worked."""

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is None:
		print(tools.stage.NO_COMPILER)
		return False

	with tempfile.TemporaryDirectory(prefix="synthsync-install-") as scratch:

		folder = pathlib.Path(scratch)
		top = tools.stage.stage_for_pico(folder, compiler)

		count, total = size(folder)

		print("Laid out %d files, %d KB in all." % (count, total // 1024))

		# The old copy goes first, so that nothing it had and this one hasn't is left behind.
		if not remove(device, [] if wipe else top + [JOIN_FILE] + OLD_NAMES):
			return False

		print("Emptied the Pico's storage." if wipe else "Took off any copy that was there.")

		# Everything but what starts it at power-up, which waits until the rest has been seen to run.
		worked, output = on_pico(device, ["fs", "cp", "-r"] + [name for name in top if name != tools.stage.MAIN] + [":"], folder)

		if not worked:
			print("The files couldn't be copied. The Pico said:")
			print(output)
			print(NOTHING_STARTS)
			return False

		print("Copied.")

		worked, output = on_pico(device, ["exec", LOAD % (modules(folder),)])
		loaded = [line for line in output.splitlines() if line.startswith("LOADED ")]

		if not worked or not loaded:
			print("SynthSync was copied, but the Pico couldn't load it. It said:")
			print(output)
			print("If it names a .mpy file as incompatible, the Pico's MicroPython and this machine's mpy-cross are different releases.")
			print(NOTHING_STARTS)
			return False

		print("Every part loads on the Pico, with %d KB of its memory to spare." % (int(loaded[0].split()[1]) // 1024))

		if tools.stage.MAIN not in top:
			print("There is no %s yet, so nothing starts when the Pico is switched on." % tools.stage.MAIN)
			return True

		worked, output = on_pico(device, ["exec", TRY % TRY_FOR], timeout=60)
		ran = trial(output)

		# The clock has to have got somewhere without a slip, and the page has to have been there to ask for.
		# A Pico with a WiFi network saved is still joining it after three seconds, and has no address yet.
		kept_time = ran.get("faults") == "0" and ran.get("beat", "0") != "0"
		served = ran.get("page") == "1" and (ran.get("address", "none") != "none" or ran.get("mode") == "joining")

		if not worked or not kept_time or not served:
			print("SynthSync was copied, but didn't run as it should when tried. The Pico said:")
			print(output)
			print("%s has not been copied, so SynthSync won't start by itself. If the Pico has stopped answering, unplug it and plug it in again." % tools.stage.MAIN)
			return False

		print("Tried it for %d seconds: the clock was running %s ms after it was asked for and kept time, and the page was served with %d KB of memory to spare." % (TRY_FOR // 1000, ran.get("started_in_ms", "?"), int(ran.get("free", "0")) // 1024))

		worked, output = on_pico(device, ["fs", "cp", tools.stage.MAIN, ":"], folder)

		if not worked:
			print("%s couldn't be copied. The Pico said:" % tools.stage.MAIN)
			print(output)
			print(NOTHING_STARTS)
			return False

	# The password isn't said: it may have been changed, and the Pico keeps its settings through an install.
	if ran.get("mode") == "own":
		reach = "To use it, join the WiFi network \"%s\" from a phone and open http://%s. Its password is synthsync unless you have changed it." % (ran["own"], ran["address"])

	else:
		reach = "It has a WiFi network saved, and joins it when it starts: the page is then at http://%s.local. If it can't join within twenty seconds its own network, \"%s\", comes back, with the page at http://%s." % (ran.get("host", "synthsync"), ran["own"], synthsync.core.wifi.OWN_ADDRESS)

	if not start:
		print("Not started. SynthSync starts the next time the Pico is switched on.")
		print(reach)
		return True

	worked, output = on_pico(device, ["reset"])

	if not worked:
		print("The Pico couldn't be restarted. It said:")
		print(output)
		return False

	print("Restarted the Pico, which starts SynthSync.")
	print(reach)

	return True


def main (arguments: list[str] | None = None) -> int:

	"""Do what the command line asks, and say how it went."""

	parser = argparse.ArgumentParser(description="Copy SynthSync onto a Pico, or take it off again.")
	parser.add_argument("--device", help="the Pico's serial port; found automatically when only one is plugged in")
	parser.add_argument("--wipe", action="store_true", help="empty the Pico's storage first, which forgets its saved settings and WiFi too")
	parser.add_argument("--remove", action="store_true", help="take SynthSync off the Pico and stop")
	parser.add_argument("--no-start", action="store_true", help="don't restart the Pico afterwards")
	options = parser.parse_args(arguments)

	found = find_devices()

	if options.device is None and len(found) != 1:

		if not found:
			print("No Pico running MicroPython was found on a USB port.")
		else:
			print("More than one Pico is plugged in. Name one with --device:")
			print("\n".join("  " + device for device in found))

		return 1

	device = options.device or found[0]

	if options.remove:

		print("Taking %s off %s" % ("everything" if options.wipe else "SynthSync", device))

		if not remove(device, [] if options.wipe else ["synthsync", "microdot", tools.stage.MAIN, JOIN_FILE] + OLD_NAMES):
			return 1

		print("Done. The Pico's storage is empty." if options.wipe else "Done. Any settings the Pico had saved are still there.")

		return 0

	print("Installing SynthSync on %s" % device)

	return 0 if install(device, options.wipe, not options.no_start) else 1


if __name__ == "__main__":
	sys.exit(main())
