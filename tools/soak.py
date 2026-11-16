"""Soak PicoClock for hours over WiFi, with its page in use throughout, and time a steady output to the cycle all the while.

    python -m tools.soak 192.168.1.23 --hours 12 --log soak.log

It runs the whole of PicoClock from the timing rig, on the WiFi network the
Pico has saved, as the rig's `air` test does, but for hours. The Pico times
output 2 in windows of a minute, holding every gap to the cycle against the
tempo and ratio output 2 had when the window began, and prints a line for
each. The clock runs at 4 PPQN, as PicoClock starts and as most people will
run it. Meanwhile this computer uses the page as people would, and harder:

- a page stays open throughout, as a phone left on the page does;
- every half a minute the page is used hard for a few seconds, as
  `tools/hammer.py` does, changing outputs 3 and 4, and each round ends with
  a change that causes a save;
- after most windows, output 2, the tempo or the transport is changed: a new
  tempo or ratio, a pause, a stop, a mute, a new pulse length, or snap
  turned off for a free ratio and on again;
- every minute, PicoClock is asked how it has kept up.

A window that one of those changes lands in is not judged, and the next one
begins once things have been left alone for a few seconds.

Everything goes to the log as it happens, with the time, so a soak cut short
still says how far it got. It is meant to be left alone: start it so that it
outlives the terminal it was started from. When it ends, the Pico is
restarted, so PicoClock comes back as installed. Stopped early by Ctrl-C or
a kill, it interrupts the Pico's half, which puts the settings back, and
restarts the Pico too.

It exits with 0 when the Pico's half passed: every window exact, none short,
nothing broken, and the clock's own counts clean. How often the page's side
was held up is in the log and the summary, for the question of whether
PicoClock needs a MicroPython of its own.
"""

import argparse
import asyncio
import datetime
import pathlib
import random
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import typing

import picoclock.core.ratio
import tools.hammer
import tools.install
import tools.page
import tools.rig
import tools.stage

# How often the page is used hard, in seconds: one round of hammer.py's, about
# five seconds long, each time.
HAMMER_EVERY = 30.0

# How often PicoClock is asked how it has kept up, in seconds.
HEALTH_EVERY = 60.0

# The chance that a window is followed by a change to the tempo, the transport or output 2.
CHANGE_CHANCE = 0.7

# The clock's PPQN while the soak runs, which the Pico's half sets: the one
# PicoClock starts at.
PPQN = picoclock.core.ratio.DEFAULT_PPQN

# What those changes choose from. Any of these tempos with any of these ratios
# keeps output 2 between 1 and 120 pulses a second, which is what the Pico's
# windows are made for. The ratios are of a clock of 4 pulses a beat, so "1/4"
# is a pulse a beat and "6" is 24: the same pulses a beat as earlier soaks,
# which ran at 1 PPQN. The ratios are all snap values, and the free ones, set
# only with snap off, are not.
TEMPOS = (6000, 8250, 9000, 10000, 12000, 13333, 14050, 17475, 24000, 30000)
RATIOS = ("1/4", "1/2", "3/4", "1", "3/2", "2", "3", "4", "6", "3/8")
FREE_RATIOS = ("7/12", "5/16", "11/8", "5/4")
PULSES = (5, 10, 20)
CHANGES = ("tempo", "ratio", "pause", "stop", "mute", "pulse", "snap")

# Output 4's pulse after each round of hammering, in turn. A round always ends on
# the same settings, which would leave nothing new to save, so this makes each
# round end in a save: none of these is a pulse hammer.py sets.
SAVED_PULSES = (15, 25, 40)

# How long the transport is left paused or stopped, or output 2 muted, before
# it is put back, in seconds.
HOLD_OFF = (2.0, 8.0)

# How long to wait before opening the lasting page again once it is lost, in seconds.
REOPEN_AFTER = 5.0

# Output 2, counted from 0 as the page's messages count.
TIMED = 1

Change = dict[str, typing.Any]


def fields (line: str) -> dict[str, str]:

	"""Pick the name=value pairs out of a line the Pico printed."""

	found: dict[str, str] = {}

	for word in line.split():

		name, equals, value = word.partition("=")

		if equals:
			found[name] = value

	return found


class Log:

	"""Writes each line with the time it happened, to the log as it goes and to the screen."""

	def __init__ (self, path: pathlib.Path) -> None:

		"""Open the log to add to it."""

		self.path = path
		self.file = path.open("a", buffering=1)
		self.lock = threading.Lock()

	def write (self, source: str, words: str) -> None:

		"""Write one line, saying where it came from."""

		line = "%s  %-6s %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), source, words)

		with self.lock:
			self.file.write(line + "\n")
			print(line, flush=True)

	def close (self) -> None:

		"""Close the log."""

		self.file.close()


class Soak:

	"""What the Pico's half and the page's half share while the soak runs."""

	def __init__ (self, address: str, port: int, log: Log, seed: int) -> None:

		"""Get ready to soak the PicoClock at this address."""

		self.address = address
		self.port = port
		self.log = log
		self.random = random.Random(seed)

		# Set once the Pico has joined the network, which the page waits for, and once its half has ended, which ends everything else.
		self.joined = threading.Event()
		self.over = threading.Event()

		# What the Pico has said that the changes wait on, handed over to the page's half as it comes.
		self.loop: asyncio.AbstractEventLoop | None = None
		self.said: asyncio.Queue[str] | None = None

		# The Pico's half, its result line, and whether output 2 was last left with snap off.
		self.process: subprocess.Popen[str] | None = None
		self.result = ""
		self.snap_off = False

		self.counts = {"windows": 0, "spoiled": 0, "changes": 0, "refused": 0, "unanswered": 0, "phone_lost": 0, "rounds": 0, "round_failures": 0, "out_of_step": 0, "asked": 0, "not_told": 0}
		self.first_health: dict[str, int] | None = None
		self.last_health: dict[str, int] | None = None

	def origin (self) -> str:

		"""Say where a browser would say the page came from."""

		return "http://%s" % (self.address if self.port == 80 else "%s:%d" % (self.address, self.port))

	def tell (self, what: str) -> None:

		"""Hand something the Pico said to the page's half, from whichever thread heard it."""

		if self.loop is not None and self.said is not None:
			self.loop.call_soon_threadsafe(self.said.put_nowait, what)

	def heard (self, line: str) -> None:

		"""Take in one line the Pico printed: log it, follow the Pico if its address moves, and pass on what the changes wait for."""

		self.log.write("pico", line)

		if line.startswith("PICOCLOCK NETWORK") and "mode=joined" in line:
			self.joined.set()

		if line.startswith("RIG soak "):
			self.result = line
			return

		if line.startswith("SOAK spoiled"):
			self.counts["spoiled"] += 1
			self.tell("window")
			return

		if line.startswith("SOAK waiting") and "playing=0" in line:
			self.tell("waiting")
			return

		if not line.startswith("SOAK window"):
			return

		self.counts["windows"] += 1
		self.joined.set()
		address = fields(line).get("address", "none")

		if address not in ("none", self.address):
			self.log.write("note", "the Pico's address moved to %s, and the page is now used there" % address)
			self.address = address

		self.tell("window")

	async def send (self, change: Change) -> bool:

		"""Make one change as a page without a socket does, and say whether PicoClock took it."""

		try:
			status, answer = await asyncio.wait_for(tools.page.fetch(self.address, self.port, "/api/change", change, {"Origin": self.origin()}), tools.hammer.PATIENCE)

		except (OSError, asyncio.TimeoutError, ValueError, IndexError) as problem:
			self.counts["unanswered"] += 1
			self.log.write("change", "unanswered: %r (%s)" % (change, type(problem).__name__))
			return False

		if status != 200:
			self.counts["refused"] += 1
			self.log.write("change", "refused with %d: %r %r" % (status, change, answer))
			return False

		return True

	async def rest (self, seconds: float) -> None:

		"""Wait this long, or until the soak is over."""

		until = time.monotonic() + seconds

		while not self.over.is_set() and time.monotonic() < until:
			await asyncio.sleep(min(0.5, until - time.monotonic()))


def plan (soak: Soak) -> tuple[str, list[tuple[Change, float]]]:

	"""Choose the next change to the tempo, the transport or output 2, as the messages to send and how long to wait after each.

	Snap turned off for a free ratio is turned on again at the next change,
	which moves the ratio to the nearest snap value.
	"""

	choose = soak.random

	def output (name: str, value: object) -> Change:

		"""A change to output 2."""

		return {"do": "output", "index": TIMED, "name": name, "value": value}

	if soak.snap_off:
		soak.snap_off = False
		return "snap back on", [(output("snap", True), 0.0)]

	kind = choose.choice(CHANGES)
	held = choose.uniform(*HOLD_OFF)

	if kind == "tempo":
		tempo = choose.choice(TEMPOS)
		return "tempo %d" % tempo, [({"do": "tempo", "value": tempo}, 0.0)]

	if kind == "ratio":
		ratio = choose.choice(RATIOS)
		return "ratio %s" % ratio, [(output("ratio", ratio), 0.0)]

	if kind in ("pause", "stop"):
		return "%s for %.1f s" % (kind, held), [({"do": kind}, held), ({"do": "start"}, 0.0)]

	if kind == "mute":
		return "mute for %.1f s" % held, [(output("muted", True), held), (output("muted", False), 0.0)]

	if kind == "pulse":
		pulse = choose.choice(PULSES)
		return "pulse %d ms" % pulse, [(output("pulse", pulse), 0.0)]

	soak.snap_off = True
	ratio = choose.choice(FREE_RATIOS)

	return "snap off, ratio %s" % ratio, [(output("snap", False), 0.0), (output("ratio", ratio), 0.0)]


async def change_something (soak: Soak) -> None:

	"""Make the next change, sending every part of it even if one goes unanswered, so nothing is left paused or muted."""

	what, steps = plan(soak)
	took = True

	for change, wait in steps:

		took = await soak.send(change) and took

		if wait:
			await asyncio.sleep(wait)

	soak.counts["changes"] += 1
	soak.log.write("change", what if took else what + ", not all of it taken")


async def put_back (soak: Soak) -> None:

	"""Start the clock and unmute output 2, for a Pico that says output 2 has been silent a while."""

	soak.log.write("change", "output 2 has been silent a while, so the clock is started and output 2 unmuted")

	await soak.send({"do": "start"})
	await soak.send({"do": "output", "index": TIMED, "name": "muted", "value": False})


async def changing (soak: Soak) -> None:

	"""After most windows, change the tempo, the transport or output 2, in the pause the Pico leaves for it."""

	assert soak.said is not None

	while not soak.over.is_set():

		try:
			what = await asyncio.wait_for(soak.said.get(), 1.0)
		except asyncio.TimeoutError:
			continue

		if what == "waiting":
			await put_back(soak)

		elif soak.random.random() < CHANGE_CHANCE:
			await change_something(soak)

		# Whatever the Pico said while that was being done is out of date now.
		while not soak.said.empty():
			soak.said.get_nowait()


async def phone (soak: Soak) -> None:

	"""Keep a page open throughout, as a phone left on the page does, hearing everything PicoClock sends it."""

	while not soak.over.is_set():

		page = tools.page.Page(soak.address, soak.port)

		try:

			await asyncio.wait_for(page.open(origin=soak.origin()), tools.hammer.PATIENCE)

			while not soak.over.is_set():
				await page.hear(1.0)

		except (OSError, EOFError, asyncio.TimeoutError, ValueError, tools.page.Refused) as problem:
			soak.counts["phone_lost"] += 1
			soak.log.write("page", "the page left open was lost: %s" % type(problem).__name__)

		finally:
			await page.close()

		await soak.rest(REOPEN_AFTER)


async def hammering (soak: Soak) -> None:

	"""Every half a minute, use the page hard for a round, as hammer.py does, log how the round went, and leave something new to save."""

	totals = tools.hammer.new_totals()
	turn = 0

	while not soak.over.is_set():

		await soak.rest(HAMMER_EVERY)

		if soak.over.is_set():
			break

		turn += 1
		before = dict(totals)

		try:
			await tools.hammer.one_round(soak.address, soak.port, totals, turn, False)

		except Exception as problem:
			soak.log.write("page", "round %d broke: %r" % (turn, problem))

		failed = int(totals["failed"] - before["failed"])
		out_of_step = int(totals["out_of_step"] - before["out_of_step"])

		soak.counts["rounds"] += 1
		soak.counts["round_failures"] += failed
		soak.counts["out_of_step"] += out_of_step
		soak.log.write("page", "round %d: changes=%d failed=%d out_of_step=%d worst_echo_ms=%d" % (turn, totals["changes"] - before["changes"], failed, out_of_step, totals["worst_echo_ms"]))

		for words in tools.hammer.notes:
			soak.log.write("page", "  " + words)

		tools.hammer.notes.clear()

		await soak.send({"do": "output", "index": 3, "name": "pulse", "value": SAVED_PULSES[turn % len(SAVED_PULSES)]})


async def asking (soak: Soak) -> None:

	"""Every minute, ask PicoClock how its side has kept up, and log what it says."""

	while not soak.over.is_set():

		soak.counts["asked"] += 1
		answer = await tools.hammer.health(soak.address, soak.port)

		if answer is None:
			soak.counts["not_told"] += 1
			soak.log.write("health", "PicoClock didn't say how it has kept up")

		else:
			soak.first_health = soak.first_health or answer
			soak.last_health = answer
			soak.log.write("health", " ".join("%s=%s" % (name, answer.get(name, -1)) for name in ("pages", "given_up", "waiting", "holds", "held", "longest_hold", "faults")))

		await soak.rest(HEALTH_EVERY)


async def use_page (soak: Soak) -> None:

	"""Use the page in every way above, from when the Pico has joined the network until its half has ended.

	Before it has joined there is no page to use, and failing to reach it
	then would only be noise in the counts.
	"""

	soak.loop = asyncio.get_running_loop()
	soak.said = asyncio.Queue()

	while not soak.joined.is_set() and not soak.over.is_set():
		await asyncio.sleep(0.2)

	await asyncio.gather(phone(soak), hammering(soak), changing(soak), asking(soak))


def run_board (soak: Soak, device: str, folder: pathlib.Path, minutes: int) -> None:

	"""Run the Pico's half, logging each line it prints as it comes, and end the soak when it ends."""

	command = [tools.rig.find_mpremote(), "connect", device, "soft-reset", "mount", str(folder), "exec", "import rig; rig.soak(%d)" % minutes]

	try:

		soak.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
		assert soak.process.stdout is not None

		for line in soak.process.stdout:
			soak.heard(line.rstrip())

		soak.process.wait()
		soak.log.write("note", "the Pico's half ended, with code %d" % soak.process.returncode)

	finally:
		soak.over.set()


def summary (soak: Soak) -> list[str]:

	"""Say how the soak went, in a few lines."""

	counts = soak.counts
	lines = [
		"result: %s" % (soak.result or "none came back"),
		"windows=%d spoiled=%d changes=%d refused=%d unanswered=%d" % (counts["windows"], counts["spoiled"], counts["changes"], counts["refused"], counts["unanswered"]),
		"page: rounds=%d failures=%d out_of_step=%d lasting_page_lost=%d" % (counts["rounds"], counts["round_failures"], counts["out_of_step"], counts["phone_lost"]),
		"asked how it kept up %d times, not told %d" % (counts["asked"], counts["not_told"]),
	]

	if soak.first_health is not None and soak.last_health is not None:

		first = soak.first_health
		last = soak.last_health

		lines.append("while it ran: holds=%d held_ms=%d longest_hold_ms=%d given_up=%d" % (last["holds"] - first["holds"], last["held"] - first["held"], last.get("longest_hold", -1), last["given_up"] - first["given_up"]))

	return lines


def stop_early (soak: Soak) -> None:

	"""End the soak before its time: let the Pico's half go, which leaves the Pico running it alone until the restart interrupts it."""

	soak.log.write("note", "stopped early")

	if soak.process is not None and soak.process.poll() is None:
		soak.process.terminate()

	soak.over.set()


def restart (device: str, log: Log) -> None:

	"""Restart the Pico, which interrupts anything still running there first, so PicoClock starts again as installed."""

	worked, said = tools.install.ask([tools.rig.find_mpremote(), "connect", device, "reset"], None, 60)

	log.write("note", "the Pico was restarted" if worked else "the Pico couldn't be restarted: %s" % said.strip())


def main (arguments: list[str] | None = None) -> int:

	"""Soak PicoClock for as long as asked, and say how it went."""

	parser = argparse.ArgumentParser(description="Soak PicoClock for hours over WiFi with its page in use, timing a steady output to the cycle throughout.")
	parser.add_argument("address", help="where the Pico is on the WiFi network it has saved")
	parser.add_argument("--hours", type=float, default=12.0, help="how long to soak it for")
	parser.add_argument("--log", required=True, help="the file to log to, on local disk, which is added to")
	parser.add_argument("--device", help="the Pico's serial port; found automatically if left out")
	parser.add_argument("--seed", type=int, help="the seed for the changes, to repeat a soak's; chosen and logged if left out")
	options = parser.parse_args(arguments)

	device = options.device or tools.rig.find_device()

	if device is None:
		print("No Pico running MicroPython was found on a USB port.")
		return 1

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is None:
		print(tools.stage.NO_COMPILER)
		return 1

	minutes = max(1, round(options.hours * 60))
	seed = options.seed if options.seed is not None else int(time.time())
	log = Log(pathlib.Path(options.log))
	soak = Soak(options.address, 80, log, seed)

	def stopped (number: int, frame: types.FrameType | None) -> None:

		"""Stop early when asked to."""

		stop_early(soak)

	signal.signal(signal.SIGTERM, stopped)
	signal.signal(signal.SIGINT, stopped)

	log.write("note", "a soak of %d minutes begins, with seed %d" % (minutes, seed))

	with tempfile.TemporaryDirectory(prefix="picoclock-soak-") as scratch:

		folder = pathlib.Path(scratch)
		tools.rig.stage(folder, compiler)

		# The installed PicoClock is stopped first, with the install tool's patience for a Pico busy starting its radio.
		tools.install.on_pico(device, ["exec", "pass"], None, 60)

		board = threading.Thread(target=run_board, args=(soak, device, folder, minutes))
		board.start()

		asyncio.run(use_page(soak))
		board.join()

	restart(device, log)

	for line in summary(soak):
		log.write("result", line)

	log.close()

	passed = bool(tools.rig.results(soak.result)) and tools.rig.results(soak.result)[0][1]

	return 0 if passed else 1


if __name__ == "__main__":
	sys.exit(main())
