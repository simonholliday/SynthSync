"""Give a PicoClock a WiFi network to join, from a file, without showing its name or its password.

    python -m tools.join wifi.txt
    python -m tools.join --forget
    python -m tools.join wifi.txt --wrong-password

The file holds the network's name on its first line and its password on its
second. An open network has no second line. The file is copied to the Pico,
which reads it, removes it and tries to join, just as it does when asked from
the page: for up to twenty seconds, keeping the network only if it gets in.
The Pico is then restarted. If it joined, it joins again as it starts, and
this says where its page is. If it didn't, this says why, and the Pico comes
back up on its own network.

`--forget` has the Pico forget the network it has saved and go back to its
own. `--wrong-password` is for testing: it tries the file's network with a
password that isn't its own, to see PicoClock refused and back on its own
network, and it succeeds when that is what happens.

Nothing in the file is printed, here or by the Pico, and nothing in it is
ever put on a command line. This is for setting a PicoClock up over USB when
its page can't be reached, and for testing.

It needs a Pico 2 W with PicoClock installed, on a USB port. With more than
one plugged in, name one with `--device`. It exits with 0 when the Pico
joined, or forgot, and 1 when it didn't.
"""

import argparse
import pathlib
import sys

import tools.install

# Where the file is put on the Pico, for as long as it takes to read it.
HANDOVER = "/_join.txt"

# Run on the Pico to read the file, remove it, and try the network as a join
# from the page is tried. It says how that went and never what was in the file.
JOIN = """
import os
import time

import picoclock.core.clock
import picoclock.core.settings
import picoclock.core.wifi
import picoclock.device.board
import picoclock.device.radio
import picoclock.device.storage

settings = picoclock.core.settings.Settings(picoclock.device.board.OUTPUTS)
storage = picoclock.device.storage.Storage(settings)
storage.load()

try:
	with open(%r) as handle:
		lines = handle.read().split("\\n")
finally:
	os.remove(%r)

clock = picoclock.core.clock.Clock(settings, None, storage)
wifi = picoclock.device.radio.make(clock)
wifi.look(0)

password = lines[1].strip("\\r") if len(lines) > 1 else ""

if %r:
	password = "wrong-on-purpose"

began = time.ticks_ms()

try:
	clock.set_network(lines[0].strip("\\r"), password)

except ValueError as problem:
	print("REFUSED", problem)

else:

	waited = 0

	while waited < 30000 and wifi.mode != picoclock.core.wifi.JOINED and not wifi.reason:
		time.sleep_ms(100)
		wifi.look(100)
		waited += 100

	took = time.ticks_diff(time.ticks_ms(), began)

	if wifi.mode == picoclock.core.wifi.JOINED:
		print("JOINED", wifi.status()["address"], settings.host_name, took)
	else:
		print("FAILED", wifi.reason or picoclock.core.wifi.TRYING, took)

finally:
	wifi.close()
	storage.save_now()
"""

# Run on the Pico to forget the network it has saved.
FORGET = """
import picoclock.core.settings
import picoclock.device.board
import picoclock.device.storage

settings = picoclock.core.settings.Settings(picoclock.device.board.OUTPUTS)
storage = picoclock.device.storage.Storage(settings)
storage.load()
settings.forget_network()
storage.save_now()
print("FORGOTTEN")
"""

# Why a join failed, by what the Pico calls each reason. None of them names the network.
WHY = {
	"not_found": "no network of that name was in range",
	"wrong_password": "the network didn't accept the password",
	"failed": "the network wouldn't let it join",
	"no_address": "the network let it join but gave it no address",
	"trying": "the network didn't answer in time",
}


def said (output: str, word: str) -> list[str] | None:

	"""Find the line the Pico printed that begins with a word, and give the words after it, or None if there is no such line."""

	for line in output.splitlines():

		if line.split(" ", 1)[0] == word:
			return line.split()[1:]

	return None


def restart (device: str) -> bool:

	"""Restart the Pico, which starts PicoClock, and say whether that worked."""

	worked, output = tools.install.on_pico(device, ["reset"])

	if not worked:
		print("The Pico couldn't be restarted. Unplug it and plug it in again. It said:")
		print(output)

	return worked


def not_installed (output: str) -> bool:

	"""Say whether what the Pico printed means PicoClock isn't on it."""

	return "ImportError" in output and "picoclock" in output


def seconds (milliseconds: str) -> str:

	"""Write a time the Pico gave in milliseconds as seconds, to one place."""

	return "%.1f" % (int(milliseconds) / 1000) if milliseconds.isdigit() else "?"


def join (device: str, file: pathlib.Path, wrong: bool = False) -> bool:

	"""Give the Pico the network in a file, have it try to join, restart it, and say whether it went as it should.

	With `wrong` the Pico tries a password that isn't the file's, and it has
	gone as it should when the network refuses it.
	"""

	try:
		lines = file.read_text().split("\n")

	except (OSError, UnicodeError):
		print("%s couldn't be read." % file)
		return False

	if not lines[0].strip():
		print("%s has no network's name on its first line." % file)
		return False

	print("Giving the Pico the network in %s. Neither its name nor its password is shown." % file)

	if wrong:
		print("It will try a password that isn't the one in the file, to see the network refuse it.")

	worked, output = tools.install.on_pico(device, ["fs", "cp", str(file), ":" + HANDOVER])

	if not worked:
		print("The file couldn't be copied to the Pico.")
		restart(device)
		return False

	print("PicoClock is trying to join it, which can take twenty seconds.")

	# Whatever the Pico prints here could only hold the file's contents if it failed
	# in some way nobody has thought of, so on failure it is not shown.
	worked, output = tools.install.on_pico(device, ["exec", JOIN % (HANDOVER, HANDOVER, wrong)], timeout=90)

	joined = said(output, "JOINED")
	failed = said(output, "FAILED")
	refused = said(output, "REFUSED")

	if not restart(device):
		return False

	if wrong:

		if failed is not None and failed[:1] == ["wrong_password"]:
			print("The network refused the password after %s seconds, as it should, and nothing was saved." % seconds(failed[-1]))
			print("Restarted the Pico, which comes up on its own network.")
			return True

		if joined is not None:
			print("PicoClock joined with a password that isn't the network's own, so the network takes any. It is saved with that password.")
			return False

	if joined is not None and len(joined) == 3:
		print("PicoClock joined it in %s seconds and was given the address %s." % (seconds(joined[2]), joined[0]))
		print("Restarted the Pico, which joins it again as it starts. In about ten seconds its page is at http://%s, or http://%s.local." % (joined[0], joined[1]))
		return True

	if failed is not None:
		print("PicoClock couldn't join it: %s. It gave up after %s seconds. Nothing was saved." % (WHY.get(failed[0] if failed else "", "it failed"), seconds(failed[-1]) if failed else "?"))

	elif refused is not None:
		print("PicoClock wouldn't take what is in the file: %s." % " ".join(refused))

	elif not_installed(output):
		print("PicoClock isn't installed on this Pico. Install it first with python -m tools.install.")

	else:
		print("The Pico didn't say how the join went, so it may have failed part way. What it printed isn't shown, in case it holds something from the file.")

	print("Restarted the Pico, which comes up as it was before.")

	return False


def forget (device: str) -> bool:

	"""Have the Pico forget its saved network, restart it, and say whether that worked."""

	worked, output = tools.install.on_pico(device, ["exec", FORGET])

	if not worked or said(output, "FORGOTTEN") is None:

		if not_installed(output):
			print("PicoClock isn't installed on this Pico. Install it first with python -m tools.install.")
		else:
			print("The Pico couldn't forget its network. It said:")
			print(output)

		return False

	if not restart(device):
		return False

	print("PicoClock has forgotten its WiFi network. Restarted the Pico, which comes up on its own network.")

	return True


def main (arguments: list[str] | None = None) -> int:

	"""Do what the command line asks, and say how it went."""

	parser = argparse.ArgumentParser(description="Give a PicoClock a WiFi network to join, from a file, without showing its name or its password.")
	parser.add_argument("file", nargs="?", type=pathlib.Path, help="a file with the network's name on its first line and its password on its second")
	parser.add_argument("--forget", action="store_true", help="have the Pico forget its saved network and go back to its own")
	parser.add_argument("--wrong-password", action="store_true", help="for testing: try the file's network with a password that isn't its own, and succeed if it is refused")
	parser.add_argument("--device", help="the Pico's serial port; found automatically when only one is plugged in")
	options = parser.parse_args(arguments)

	if (options.file is None) == (not options.forget):
		parser.error("give a file with a network in it, or --forget, and not both")

	found = tools.install.find_devices()

	if options.device is None and len(found) != 1:

		if not found:
			print("No Pico running MicroPython was found on a USB port.")
		else:
			print("More than one Pico is plugged in. Name one with --device:")
			print("\n".join("  " + device for device in found))

		return 1

	device = options.device or found[0]

	if options.forget:
		return 0 if forget(device) else 1

	return 0 if join(device, options.file, options.wrong_password) else 1


if __name__ == "__main__":
	sys.exit(main())
