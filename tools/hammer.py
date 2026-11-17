"""Use a SynthSync's page hard over the network, the way several phones at once would, and count what happened.

    python -m tools.hammer 192.168.4.1
    python -m tools.hammer 192.168.1.23 --seconds 60 --tempo

Every few seconds it opens two pages, as two phones would. One changes
settings thirty times a second while the other listens, and at the end of the
round the listener must end up with what the first one last set. It is given
a few seconds to, because a packet lost on the air can hold a page up for a
second or two, and how long it took is counted.
Meanwhile it opens the page as two phones do at once, and sends changes the
way a page without a socket does.

It changes outputs 3 and 4 only, so that outputs 1 and 2 pulse steadily and
can be timed while it runs. With `--tempo` it drags the tempo too. What it
found is put back when it finishes.

After each round it asks SynthSync how well its side has kept up, and counts
how often and for how long everything there but the clock was held up for over
a second while this ran, and how many pages SynthSync gave up on. Each reads
-1 if SynthSync never said, and it is asked until it does.

This is traffic for the timing rig's `air` test and for a long soak. It exits
with 0 when every request was answered, the two pages stayed in step and
SynthSync said nothing held its side up.
"""

import argparse
import asyncio
import sys
import time
import typing

import tools.page

# What a phone asks for to open the page. On the Pico the page is one file, with
# everything inside it, and a laptop's stand-in sends the rest when asked.
PAGE = "/"

# How long one round lasts, and how many changes a second the busy page makes.
ROUND = 5.0
RATE = 30

# What the busy page sets output 3's ratio and output 4's pulse to, turn by turn.
RATIOS = ("3/2", "2", "1/4", "5", "4/3", "1/2", "3", "7")
PULSES = (5, 8, 12, 20, 35, 50)

# How long to wait for any one answer, in seconds.
PATIENCE = 8.0

# How long the listening page is given, at the end of a round, to hear the last of what was set, in seconds.
CATCH_UP = 6.0

Totals = dict[str, float]

# What went wrong, in words, for whoever is running this to read: at most this many.
MOST_NOTES = 12
notes: list[str] = []


def note (words: str) -> None:

	"""Keep a line about something that went wrong, up to a point."""

	if len(notes) < MOST_NOTES:
		notes.append(words)


def new_totals () -> Totals:

	"""Begin counting from nothing."""

	return {
		"rounds": 0,
		"out_of_step": 0,
		"requests": 0,
		"failed": 0,
		"changes": 0,
		"heard": 0,
		"worst_file_ms": 0,
		"worst_echo_ms": 0,
		"worst_catch_up_ms": 0,
		"faults": 0,
		"holds": -1,
		"held_ms": -1,
		"given_up": -1,
	}


async def get (host: str, port: int, path: str) -> float:

	"""Fetch one of the page's files, and give back how long it took in milliseconds. Raises OSError if it didn't come whole."""

	began = time.monotonic()
	reader, writer = await asyncio.open_connection(host, port)

	try:

		writer.write(("GET %s HTTP/1.1\r\nHost: %s\r\nAccept-Encoding: gzip\r\nConnection: close\r\n\r\n" % (path, host)).encode())
		await writer.drain()

		answer = await reader.read()

	finally:
		writer.close()

	head, _, body = answer.partition(b"\r\n\r\n")

	if not head.startswith(b"HTTP/1.") or head.split(b" ", 2)[1] != b"200" or not body:
		raise OSError("%s didn't come whole" % path)

	return (time.monotonic() - began) * 1000


async def count (totals: Totals, work: typing.Awaitable[typing.Any], what: str) -> typing.Any:

	"""Do one thing that asks SynthSync for an answer, and count whether it got one in time."""

	totals["requests"] += 1
	began = time.monotonic()

	try:
		return await asyncio.wait_for(work, PATIENCE)

	# A socket closed part way through, or one SynthSync wouldn't open, is a failure like any other.
	except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, tools.page.Refused, ValueError, IndexError) as problem:

		totals["failed"] += 1
		note("%s failed after %.1f s: %s" % (what, time.monotonic() - began, type(problem).__name__))

		return None


async def browse (host: str, port: int, totals: Totals) -> None:

	"""Open the page as two phones do at the same moment, which the Pico sends one after the other."""

	for took in await asyncio.gather(*(count(totals, get(host, port, PAGE), "fetching the page") for _ in range(2))):

		if took is not None and took > totals["worst_file_ms"]:
			totals["worst_file_ms"] = took


async def one_round (host: str, port: int, totals: Totals, turn: int, tempo: bool) -> None:

	"""Use the page hard for a few seconds: two pages open, one busy and one listening, with a browser and plain requests beside them. Both pages are closed whatever happens."""

	origin = "http://%s" % (host if port == 80 else "%s:%d" % (host, port))
	busy = tools.page.Page(host, port)
	quiet = tools.page.Page(host, port)

	try:

		if await count(totals, busy.open(origin=origin), "opening a page") is None or await count(totals, quiet.open(origin=origin), "opening a second page") is None:
			return

		await use_pages(host, port, totals, turn, tempo, origin, busy, quiet)

	finally:
		await busy.close()
		await quiet.close()


async def use_pages (host: str, port: int, totals: Totals, turn: int, tempo: bool, origin: str, busy: tools.page.Page, quiet: tools.page.Page) -> None:

	"""Change things from the busy page for a round while the quiet one listens, with a browser and plain requests beside them, and count whether the two ended in step."""

	# What the listening page last heard of each thing the busy page changes, and of the clock.
	heard: dict[typing.Any, typing.Any] = {}
	sent: dict[typing.Any, typing.Any] = {}
	echoes: dict[int, float] = {}
	stop = asyncio.Event()

	async def listen (page: tools.page.Page, mine: bool) -> None:

		"""Keep what one page hears until the round is over."""

		while not stop.is_set():

			try:
				message = await page.hear(0.25)

			except (OSError, asyncio.IncompleteReadError) as problem:

				# SynthSync closed the page, or went away.
				totals["failed"] += 1
				note("a page's socket was lost: %s" % type(problem).__name__)

				return

			if message is None:
				continue

			if mine:

				began = echoes.pop(message.get("id", 0), None)

				if began is not None and (time.monotonic() - began) * 1000 > totals["worst_echo_ms"]:
					totals["worst_echo_ms"] = (time.monotonic() - began) * 1000

				continue

			totals["heard"] += 1

			if message["type"] == "output":
				heard[("ratio", message["index"])] = message["ratio"]
				heard[("pulse", message["index"])] = message["pulse"]

			elif message["type"] == "tempo":
				heard["tempo"] = message["tempo"]

			elif message["type"] == "clock":
				totals["faults"] = max(totals["faults"], message["faults"])

	listeners = [asyncio.create_task(listen(busy, True)), asyncio.create_task(listen(quiet, False))]
	began = time.monotonic()
	browsed = 0
	posted = 0
	step = 0

	try:

		while time.monotonic() - began < ROUND:

			number = turn * 100000 + step + 1

			if tempo and step % 3 == 2:
				sent["tempo"] = 10000 + (number * 137) % 4001
				change = {"do": "tempo", "value": sent["tempo"], "id": number}

			elif step % 2:
				sent[("pulse", 3)] = PULSES[(step // 2) % len(PULSES)]
				change = {"do": "output", "index": 3, "name": "pulse", "value": sent[("pulse", 3)], "id": number}

			else:
				sent[("ratio", 2)] = RATIOS[(step // 2) % len(RATIOS)]
				change = {"do": "output", "index": 2, "name": "ratio", "value": sent[("ratio", 2)], "id": number}

			echoes[number] = time.monotonic()
			await busy.say(change)
			totals["changes"] += 1
			step += 1

			elapsed = time.monotonic() - began

			# A browser opens the page twice a round, and a page without a socket sends a change every second.
			if browsed < 2 and elapsed > 0.5 + 2.5 * browsed:
				browsed += 1
				listeners.append(asyncio.create_task(browse(host, port, totals)))

			if posted < int(elapsed):
				posted += 1
				listeners.append(asyncio.create_task(count(totals, tools.page.fetch(host, port, "/api/change", {"do": "output", "index": 3, "name": "snap", "value": posted % 2 == 0}, {"Origin": origin}), "a change by POST")))

			await asyncio.sleep(max(0.0, began + step / RATE - time.monotonic()))

		# The listening page is given time to hear the last of it.
		finished = time.monotonic()

		while any(heard.get(key) != value for key, value in sent.items()) and time.monotonic() - finished < CATCH_UP:
			await asyncio.sleep(0.05)

		if (time.monotonic() - finished) * 1000 > totals["worst_catch_up_ms"]:
			totals["worst_catch_up_ms"] = (time.monotonic() - finished) * 1000

	except (OSError, asyncio.IncompleteReadError) as problem:
		totals["failed"] += 1
		note("the busy page's socket failed: %s" % type(problem).__name__)

	finally:

		stop.set()
		await asyncio.gather(*listeners, return_exceptions=True)

	totals["rounds"] += 1

	for key, value in sent.items():

		if heard.get(key) != value:
			totals["out_of_step"] += 1
			note("round %d: the listening page ended with %r for %r, where %r was set last" % (turn, heard.get(key), key, value))
			break


async def settings (host: str, port: int) -> dict[typing.Any, typing.Any] | None:

	"""Ask SynthSync how the things this changes stand, or give None if it doesn't answer."""

	try:
		status, state = await asyncio.wait_for(tools.page.fetch(host, port, "/api/state"), PATIENCE)

	except (OSError, asyncio.TimeoutError, ValueError, IndexError):
		return None

	if status != 200 or not isinstance(state, dict):
		return None

	found: dict[typing.Any, typing.Any] = {}

	for message in state["now"]:

		if message["type"] == "tempo":
			found["tempo"] = message["tempo"]

		elif message["type"] == "output" and message["index"] in (2, 3):
			found[message["index"]] = message

	return found


async def health (host: str, port: int) -> dict[str, int] | None:

	"""Ask SynthSync how well its side of things has kept up since it was switched on, or give None if it doesn't say."""

	try:
		status, answer = await asyncio.wait_for(tools.page.fetch(host, port, "/api/health"), PATIENCE)

	except (OSError, asyncio.TimeoutError, ValueError, IndexError):
		return None

	if status != 200 or not isinstance(answer, dict) or not all(isinstance(answer.get(name), int) for name in ("holds", "held", "given_up")):
		return None

	return answer


async def put_back (host: str, port: int, found: dict[typing.Any, typing.Any]) -> None:

	"""Set what this changed back to how it was found, as far as SynthSync still answers."""

	origin = {"Origin": "http://%s" % (host if port == 80 else "%s:%d" % (host, port))}
	changes: list[dict[str, typing.Any]] = [{"do": "tempo", "value": found["tempo"]}] if "tempo" in found else []

	for index in (2, 3):
		for name in ("ratio", "pulse", "snap"):
			if index in found:
				changes.append({"do": "output", "index": index, "name": name, "value": found[index][name]})

	for change in changes:

		try:
			await asyncio.wait_for(tools.page.fetch(host, port, "/api/change", change, origin), PATIENCE)

		except (OSError, asyncio.TimeoutError, ValueError, IndexError):
			return


def keep_at_it (host: str, port: int, still_going: typing.Callable[[], bool], tempo: bool = False, wait: float = 60.0, grace: float = 0.0) -> Totals:

	"""Wait for a SynthSync to answer, then use its page hard for as long as `still_going` says, and give back the counts.

	A round under way when `still_going` turns false is the last. A test
	that ends by stopping SynthSync makes the round it stops under fail, a
	moment before the test is seen to have ended. With a `grace`, a round
	that fails is not counted if `still_going` turns false within that many
	seconds of it.
	"""

	totals = new_totals()
	began = time.monotonic()
	found = None

	while found is None and still_going() and time.monotonic() - began < wait:

		found = asyncio.run(settings(host, port))

		if found is None:
			time.sleep(0.5)

	if found is None:
		return totals

	# How things stood before any of this, to count only what happens while it runs. Without it nothing
	# can be said of what held SynthSync up, which counts against the run, so it is asked for until it comes.
	at_first = None

	while at_first is None and still_going() and time.monotonic() - began < wait:

		at_first = asyncio.run(health(host, port))

		if at_first is None:
			time.sleep(0.5)

	turn = 0

	while still_going():

		turn += 1
		before = dict(totals)

		asyncio.run(one_round(host, port, totals, turn, tempo))

		# Asked after every round, because a test that ends by stopping SynthSync leaves nothing to ask at the end.
		lately = asyncio.run(health(host, port)) if at_first is not None else None

		if at_first is not None and lately is not None:
			totals["holds"] = lately["holds"] - at_first["holds"]
			totals["held_ms"] = lately["held"] - at_first["held"]
			totals["given_up"] = lately["given_up"] - at_first["given_up"]

		if totals["failed"] == before["failed"] and totals["out_of_step"] == before["out_of_step"]:
			continue

		waited = time.monotonic()

		while still_going() and time.monotonic() - waited < grace:
			time.sleep(0.1)

		# SynthSync was stopped under this round, so what failed in it failed for that.
		if not still_going() and grace:
			totals.update(before)
			break

	asyncio.run(put_back(host, port, found))

	return totals


def describe (totals: Totals) -> str:

	"""Write the counts as one line."""

	return "rounds=%d out_of_step=%d requests=%d failed=%d changes=%d heard=%d worst_file_ms=%d worst_echo_ms=%d worst_catch_up_ms=%d faults=%d holds=%d held_ms=%d given_up=%d" % (totals["rounds"], totals["out_of_step"], totals["requests"], totals["failed"], totals["changes"], totals["heard"], totals["worst_file_ms"], totals["worst_echo_ms"], totals["worst_catch_up_ms"], totals["faults"], totals["holds"], totals["held_ms"], totals["given_up"])


def went_well (totals: Totals) -> bool:

	"""Say whether the page was used, every request was answered, the two pages stayed in step, SynthSync said nothing held its side up and the clock reported no timing fault.

	Holds of -1 mean SynthSync never said, which isn't the same as nothing holding it up.
	"""

	return totals["rounds"] >= 1 and totals["failed"] == 0 and totals["out_of_step"] == 0 and totals["faults"] == 0 and totals["holds"] == 0


def main (arguments: list[str] | None = None) -> int:

	"""Use the page hard for a while, and say how it went."""

	parser = argparse.ArgumentParser(description="Use a SynthSync's page hard over the network, and count what happened.")
	parser.add_argument("address", help="where SynthSync's page is, such as 192.168.4.1")
	parser.add_argument("--port", type=int, default=80, help="the port the page is served on")
	parser.add_argument("--seconds", type=float, default=30.0, help="how long to keep at it")
	parser.add_argument("--tempo", action="store_true", help="drag the tempo as well, which changes the timing of every output")
	options = parser.parse_args(arguments)

	# A round is only begun if there is time to finish it.
	until = time.monotonic() + options.seconds - ROUND
	totals = keep_at_it(options.address, options.port, lambda: time.monotonic() < until, options.tempo, min(options.seconds, 60.0))

	if not totals["rounds"]:
		print("SynthSync didn't answer at %s." % options.address)
		return 1

	print(describe(totals))

	for words in notes:
		print("  " + words)

	return 0 if went_well(totals) else 1


if __name__ == "__main__":
	sys.exit(main())
