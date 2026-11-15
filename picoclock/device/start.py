"""What happens when the Pico is switched on.

The clock comes first. The saved settings are read and the clock is started
before anything else is even loaded, so a PicoClock with no phone in the room
is pulsing a fraction of a second after it gets power. The buttons, the light
and the saving of settings come after, and then the Pico's own WiFi network
and the page. Each is kept going on its own: one of them going wrong never
stops the others, and nothing here can stop the clock.

`main.py` calls `run`. The install tool calls it with a time to stop after, to
see that a new copy comes up before it is left to start by itself.
"""

import asyncio
import gc
import sys
import time

import picoclock.core.clock
import picoclock.core.settings
import picoclock.core.transport
import picoclock.device.board
import picoclock.device.chip
import picoclock.device.player
import picoclock.device.storage

# How long after the Pico was switched on the clock was started, in
# milliseconds, or -1 if it hasn't been, and the parts of the running clock.
# Both are kept so that they can be looked at from the Pico's own prompt.
STARTED = [-1]
RUNNING = [None]

# How long to wait before starting again a job that has failed, in milliseconds.
AGAIN_AFTER = 1000

# Where the page is served: the port a browser tries when it is given a bare address.
PAGE_PORT = 80


class Parts:

	"""The clock and what it is made of, once it is running."""

	def __init__ (self, clock, player, storage, problems: list) -> None:

		"""Hold the parts, and what in the saved settings couldn't be used."""

		self.clock = clock
		self.player = player
		self.storage = storage
		self.problems = problems

		# What is made after the clock has started: the buttons and the light,
		# the network, the page's server, and the jobs that keep them all going.
		self.panel = None
		self.wifi = None
		self.app = None
		self.jobs: list = []

		# How the page, the memory and the network stood when everything but
		# the clock was last stopped, for the trial's report and for anyone
		# who stops PicoClock from its prompt to look.
		self.stood = ""


def begin () -> Parts:

	"""Read the saved settings and start the clock, and give back its parts.

	Settings that can't be read are left as a new PicoClock has them, so a
	damaged file never stops the clock from starting.
	"""

	board = picoclock.device.board
	settings = picoclock.core.settings.Settings(board.OUTPUTS)

	# The player is made before anything else touches its pins. Setting one up afterwards would take it away.
	player = picoclock.device.player.Player(board.FIRST_OUTPUT_PIN, board.OUTPUTS)
	storage = picoclock.device.storage.Storage(settings, player)

	try:
		problems = storage.load()

	except Exception as problem:
		settings.reset()
		problems = ["The saved settings couldn't be read: %r" % (problem,)]

	clock = picoclock.core.clock.Clock(settings, player, storage)

	clock.load()
	clock.press(picoclock.core.transport.START)

	# Once the player has taken the start, the first pulse is a few milliseconds away at most.
	player.settle()
	STARTED[0] = time.ticks_ms()
	RUNNING[0] = Parts(clock, player, storage, problems)

	return RUNNING[0]


async def keep (job) -> None:

	"""Run one of the Pico's jobs for good, starting it again if it fails.

	What went wrong is printed, for anyone watching over USB. Being
	interrupted from there is not a failure, and stops everything as it
	should.
	"""

	while True:

		try:
			await job()

		except Exception as problem:
			sys.print_exception(problem)

		await asyncio.sleep_ms(AGAIN_AFTER)


def reach (parts: Parts, static: "str | None" = None) -> list:

	"""Make the Pico's network and the page's server, and give back the jobs that run them, with the one that sends a phone on the Pico's own network to the page.

	Their code is loaded only here, with the clock long since running.
	`static` is the folder the page's files are in, which is beside the
	app's own code unless a test says otherwise.
	"""

	import picoclock.device.radio
	import picoclock.web.app
	import picoclock.web.portal

	parts.wifi = picoclock.device.radio.make(parts.clock)

	if static is None:
		static = picoclock.web.app.__file__.rsplit("/", 1)[0] + "/static"

	parts.app = picoclock.web.app.App(parts.clock, parts.wifi, static)

	async def network () -> None:

		"""Keep the radio doing what the settings ask."""

		await picoclock.device.radio.run(parts.wifi)

	async def serve () -> None:

		"""Answer whatever asks for the page."""

		await parts.app.web.start_server(host="0.0.0.0", port=PAGE_PORT)

	async def names () -> None:

		"""Answer the names a phone looks up on PicoClock's own network, so that it opens the page by itself."""

		await picoclock.web.portal.run(lambda: picoclock.web.portal.sent_to(parts.wifi.status()))

	return [network, parts.app.run, serve, names]


def reached (parts: Parts) -> str:

	"""Say whether the page is being served, how much memory is left, what the radio is doing, which of its sides are on, where the page is, what the Pico answers to and what its own network is called.

	A network the Pico has joined, or is joining, is never named.
	"""

	serving = parts.app is not None and parts.app.web.server is not None
	status = parts.wifi.status() if parts.wifi is not None else {}
	sides = parts.wifi.radio.on() if parts.wifi is not None else "none"

	gc.collect()

	# The own network's name has a space in it, so it goes last.
	return "page=%d free=%d mode=%s radio=%s address=%s host=%s own=%s" % (1 if serving else 0, gc.mem_free(), status.get("mode") or "none", sides, status.get("address") or "none", parts.clock.settings.host_name, status.get("own") or "none")


async def carry_on (parts: Parts, trial: int, static: "str | None" = None) -> None:

	"""Run everything that isn't the clock: saving, the buttons and the light, the network and the page.

	With a `trial` it comes back after that many milliseconds, with all of
	it still going. `static` is where the page's files are, if not beside
	the app's code.
	"""

	# Loaded only now, with the clock already running, so that none of it holds up the first pulse.
	import picoclock.device.panel

	parts.panel = picoclock.device.panel.Panel(parts.clock, parts.player)
	parts.jobs = [asyncio.create_task(keep(job)) for job in (parts.storage.run, parts.clock.run, parts.panel.run)]

	# The network and the page come last, and nothing above waits for them. If
	# they can't be made, the clock, the buttons and the saving carry on without.
	try:
		parts.jobs += [asyncio.create_task(keep(job)) for job in reach(parts, static)]

	except Exception as problem:
		sys.print_exception(problem)

	if trial:
		await asyncio.sleep_ms(trial)
	else:
		await asyncio.gather(*parts.jobs)


async def end (jobs: list) -> None:

	"""End every job, and give each one more turn, which is when a job that has been ended finds out.

	This has to be a job itself. MicroPython won't end the job it ran last
	from outside, and when PicoClock is interrupted that is one of these.
	"""

	for job in jobs:
		job.cancel()

	await asyncio.sleep_ms(50)


def stop (parts: Parts) -> None:

	"""Stop everything but the clock, leaving a note of how things stood.

	The jobs are ended, the page is no longer answered for, the buttons are
	let go of and the network is switched off. The clock plays on.
	"""

	# This is mostly reached by being interrupted from the Pico's prompt, and
	# whatever interrupts tends to do it twice. A second one is held off until
	# everything is stopped, or the network would be left on with nothing behind it.
	picoclock.device.chip.hold()

	try:

		# Noted before anything is stopped, while it is all still going, and so only the first time.
		if not parts.stood:
			parts.stood = reached(parts)

		# Ending the job that answers for the page ends the server it was waiting
		# on, which lets go of its port when it is next given a turn.
		asyncio.run(end(parts.jobs))

		if parts.panel is not None:
			parts.panel.close()

		if parts.wifi is not None:
			parts.wifi.close()

	finally:
		picoclock.device.chip.release()


def run (trial: int = 0) -> None:

	"""Start the clock, then everything else, and keep going for as long as the Pico is on.

	With a `trial` it stops after that many milliseconds, puts every output
	low, switches the network off and says how it went. Stopped from the
	Pico's prompt, it leaves the clock playing and switches the rest off.
	"""

	asked = time.ticks_ms()
	parts = begin()
	took = time.ticks_diff(time.ticks_ms(), asked)

	for problem in parts.problems:
		print(problem)

	by_hand = False

	try:
		asyncio.run(carry_on(parts, trial))

	except KeyboardInterrupt:
		by_hand = True
		raise

	finally:

		# A second interrupt can land before `stop` has shut it out, which sends `stop` round again.
		while True:

			try:
				stop(parts)
				break

			except KeyboardInterrupt:
				pass

		if trial:
			status = parts.player.status()
			parts.player.close()

		elif by_hand:
			print("PicoClock was stopped from the Pico's prompt. The clock plays on. The page, the buttons and the WiFi network are off until the Pico is restarted.")

	if trial:
		print("PICOCLOCK RAN started_in_ms=%d beat=%d faults=%d refused=%d %s" % (took, status["beat"], status["faults"], status["refused"], parts.stood))
