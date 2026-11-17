"""The laptop stand-in plays the real scheduler, and the page's API can be driven on it over real sockets."""

import ast
import asyncio
import contextlib
import pathlib
import socket
import subprocess
import sys

import pytest

import synthsync.core.clock
import synthsync.core.settings
import synthsync.sim.__main__
import synthsync.sim.engine
import synthsync.sim.network
import synthsync.web.app
import tools.page

ROOT = pathlib.Path(__file__).parent.parent


class Hands:

	"""A clock that only moves when a test moves it."""

	def __init__ (self) -> None:

		"""Begin at some time that isn't zero."""

		self.time = 100.0

	def now (self) -> float:

		"""Give the time."""

		return self.time


def play (engine: synthsync.sim.engine.Engine, hands: Hands, seconds: float) -> None:

	"""Move the clock on for this long, in the steps the engine takes when it runs."""

	for _ in range(round(seconds / synthsync.sim.engine.STEP_EVERY)):
		hands.time += synthsync.sim.engine.STEP_EVERY
		engine.step()


def free_port () -> int:

	"""Find a port nothing is listening on."""

	with socket.socket() as probe:
		probe.bind(("127.0.0.1", 0))
		return int(probe.getsockname()[1])


async def wait_until_served (port: int) -> None:

	"""Wait until something answers on a port."""

	for _ in range(100):

		try:
			await tools.page.fetch("127.0.0.1", port, "/api/state")
			return
		except OSError:
			await asyncio.sleep(0.05)

	raise OSError("nothing began serving on port %d" % port)


def test_pulses_keep_to_the_tempo_and_each_outputs_ratio () -> None:

	"""Over ten seconds at 120 BPM, each output begins as many pulses as its ratio says."""

	hands = Hands()
	engine = synthsync.sim.engine.Engine(3, hands.now)

	engine.set_ratio(1, 4, 1)
	engine.set_ratio(2, 1, 2)
	engine.start()
	engine.step()

	play(engine, hands, 10)

	# The first pulse of each is at the very start, and the last on the ten-second mark.
	assert engine.pulses == [21, 81, 11]
	assert engine.status()["beat"] == 21


def test_a_tempo_change_shows_in_the_pulses () -> None:

	"""Doubling the tempo doubles how many pulses the next ten seconds hold."""

	hands = Hands()
	engine = synthsync.sim.engine.Engine(2, hands.now)

	engine.start()
	engine.step()
	play(engine, hands, 10)

	before = engine.pulses[0]

	engine.set_tempo(24000)
	play(engine, hands, 10)

	assert before == 21
	assert 39 <= engine.pulses[0] - before <= 41


def test_a_pause_holds_everything_and_carrying_on_picks_it_up () -> None:

	"""While paused no pulse begins and the position stands still, and afterwards both move again."""

	hands = Hands()
	engine = synthsync.sim.engine.Engine(2, hands.now)

	engine.set_ratio(1, 8, 1)
	engine.start()
	engine.step()
	play(engine, hands, 2.2)

	engine.pause()
	play(engine, hands, 0.1)

	held = list(engine.pulses)
	position = engine.status()["position"]

	play(engine, hands, 5)

	assert engine.pulses == held
	assert engine.status()["position"] == position
	assert engine.status()["paused"] is True
	assert engine.pins == 0
	assert 4000 < position < 4500

	engine.resume()
	play(engine, hands, 1)

	assert engine.pulses[0] == held[0] + 2
	assert engine.status()["position"] > position + 1900


def test_stopping_goes_quiet_and_starting_begins_at_zero_again () -> None:

	"""A stopped stand-in has every pin low and no position, and a start pulses at once."""

	hands = Hands()
	engine = synthsync.sim.engine.Engine(2, hands.now)

	engine.start()
	engine.step()
	play(engine, hands, 0.74)
	engine.stop()
	play(engine, hands, 1)

	assert engine.pins == 0
	assert engine.pulses == [2, 2]
	assert engine.status()["running"] is False
	assert engine.status()["position"] == -1

	engine.start()
	engine.step()

	assert engine.pulses == [3, 3]
	assert engine.status()["running"] is True
	assert engine.status()["beat"] == 1


def test_time_the_laptop_was_asleep_for_is_not_played_back () -> None:

	"""After a long gap the stand-in carries on from now, and doesn't race through what it missed."""

	hands = Hands()
	engine = synthsync.sim.engine.Engine(1, hands.now)

	engine.start()
	engine.step()
	play(engine, hands, 1)

	before = engine.status()["beat"]

	hands.time += 3600
	engine.step()

	assert engine.status()["beat"] - before <= 1

	play(engine, hands, 1)

	assert engine.status()["beat"] - before <= 3


def test_the_stand_in_reports_in_the_players_terms () -> None:

	"""Its status carries what the player's does on the Pico, so the page's code can't tell them apart."""

	engine = synthsync.sim.engine.Engine(4)

	assert sorted(engine.status().keys()) == players_status_names()
	assert engine.status()["faults"] == 0


def players_status_names () -> list[str]:

	"""Read the names `synthsync.device.player.Player.status` gives from the player's source, since it can't be imported off the Pico."""

	tree = ast.parse((ROOT / "synthsync" / "device" / "player.py").read_text(encoding="utf-8"))
	player = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Player")
	status = next(node for node in player.body if isinstance(node, ast.FunctionDef) and node.name == "status")
	given = next(node.value for node in ast.walk(status) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict))

	names = sorted(key.value for key in given.keys if isinstance(key, ast.Constant) and isinstance(key.value, str))

	# A dozen or more, so that a status written some other way can't make this pass with none.
	assert len(names) >= 12

	return names


def test_the_pretend_network_joins_as_a_pico_does_only_sooner () -> None:

	"""With the right password it finds the network and switches to it, with the wrong one or an unknown name it says why and keeps the network that worked, and a reset goes back."""

	engine = synthsync.sim.engine.Engine(2)
	clock = synthsync.core.clock.Clock(synthsync.core.settings.Settings(2), engine)
	network = synthsync.sim.network.make(clock)

	def spend (milliseconds: int) -> None:

		"""Let time pass, with a look as often as the stand-in looks."""

		for _ in range(milliseconds // synthsync.sim.network.LOOK_EVERY):
			network.look(synthsync.sim.network.LOOK_EVERY)

	assert network.status() == {"mode": "own", "name": synthsync.sim.network.OWN_NAME, "address": "localhost", "problem": "", "own": synthsync.sim.network.OWN_NAME, "found_at": ""}
	assert [found["name"] for found in network.scan()] == ["Studio", "Upstairs", "Cafe"]
	assert [found["secure"] for found in network.scan()] == [True, True, False]

	clock.set_network("Studio", "studio wifi")
	spend(700)

	assert network.status()["mode"] == "found"
	assert network.status()["found_at"] == "localhost"

	clock.switch_network()
	spend(700)

	assert network.status()["mode"] == "joined"
	assert network.status()["name"] == "Studio"
	assert network.status()["address"] == "localhost"

	clock.set_network("Studio", "something else")
	spend(1200)

	assert network.status()["mode"] == "own"
	assert network.status()["problem"] == "Studio didn't accept that password"

	# The password that worked is kept, not the one that didn't.
	assert clock.settings.network_name == "Studio"
	assert clock.settings.network_password == "studio wifi"

	# A network that isn't there is given up within two seconds here, where a Pico takes twenty.
	clock.set_network("Garden", "garden wifi")
	spend(1000)

	assert network.status()["mode"] == "joining"

	spend(800)

	assert network.status()["mode"] == "own"
	assert network.status()["problem"] == "There was no network called Garden in range"
	assert clock.settings.network_name == "Studio"

	clock.set_network("Cafe", "")
	spend(700)

	assert network.status()["mode"] == "found"

	# Left alone, it switches by itself, five seconds on here where a Pico waits two minutes.
	spend(synthsync.sim.network.STAY_FOR)

	assert network.status()["mode"] == "joined"

	clock.reset()
	spend(700)

	assert network.status()["mode"] == "own"
	assert network.status()["problem"] == ""


def test_the_page_is_driven_over_real_sockets () -> None:

	"""Two pages on the stand-in stay in step, hear where the clock is, and another site's page is turned away."""

	async def scenario () -> None:

		"""The test itself, which needs an event loop to run in."""

		port = free_port()
		stand = synthsync.sim.__main__.Stand()
		serving = asyncio.create_task(stand.serve("127.0.0.1", port))

		await wait_until_served(port)

		status, state = await tools.page.fetch("127.0.0.1", port, "/api/state")

		assert status == 200
		assert len(state["now"]) == 9

		first = tools.page.Page("127.0.0.1", port)
		second = tools.page.Page("127.0.0.1", port)
		hello = await first.open(origin="http://127.0.0.1:%d" % port)
		await second.open()

		assert first.number != second.number

		# It starts running, as a Pico does when it is switched on.
		assert [message["state"] for message in hello["now"] if message["type"] == "transport"] == ["running"]

		await first.say({"do": "tempo", "value": 14000, "id": 1})

		for page in (first, second):
			tempo = await page.wait_for("tempo")
			assert tempo == {"type": "tempo", "tempo": 14000, "text": "140", "by": first.number, "id": 1}

		positions = [message["position"] for message in await second.hear_for(0.9) if message["type"] == "clock"]

		assert len(positions) >= 2
		assert positions == sorted(positions)
		assert positions[-1] > positions[0] >= 0

		# A change by POST reaches the pages too, and a bad one is refused in words.
		status, answer = await tools.page.fetch("127.0.0.1", port, "/api/change", {"do": "output", "index": 1, "name": "ratio", "value": "1.5"})

		assert (status, answer) == (200, {"ok": True})
		assert (await first.wait_for("output", index=1))["label"] == "×1.5"

		status, answer = await tools.page.fetch("127.0.0.1", port, "/api/change", {"do": "tempo", "value": 1})

		assert (status, answer) == (400, {"error": "The tempo has to be between 20 and 300 BPM"})

		await second.say({"do": "stop"})

		assert (await first.wait_for("transport"))["state"] == "stopped"
		assert (await first.wait_for("clock", position=-1))["faults"] == 0

		stranger = tools.page.Page("127.0.0.1", port)

		with pytest.raises(tools.page.Refused) as refusal:
			await stranger.open(origin="http://elsewhere.example")

		assert refusal.value.status == 403

		status, answer = await tools.page.fetch("127.0.0.1", port, "/api/change", {"do": "start"}, {"Origin": "http://elsewhere.example"})

		assert status == 403
		assert stand.clock.transport.state == "stopped"

		await first.close()
		await second.close()
		await asyncio.sleep(0.1)

		assert stand.app.pages == []

		serving.cancel()

		with contextlib.suppress(asyncio.CancelledError):
			await serving

	asyncio.run(scenario())


def test_a_page_that_stops_answering_is_sent_little_more_and_then_let_go (monkeypatch: pytest.MonkeyPatch) -> None:

	"""Over real sockets, a page that stops saying what it has heard is sent two batches more and no others, and its socket is closed without a word."""

	# The few seconds a page is given are cut short, so the test needn't wait them out.
	monkeypatch.setattr(synthsync.web.app, "DEAF_FOR", 1500)

	async def scenario () -> None:

		"""The test itself, which needs an event loop to run in."""

		port = free_port()
		stand = synthsync.sim.__main__.Stand()
		serving = asyncio.create_task(stand.serve("127.0.0.1", port))

		await wait_until_served(port)

		steady = tools.page.Page("127.0.0.1", port)
		silent = tools.page.Page("127.0.0.1", port)

		await steady.open()
		await silent.open()

		# With the clock stopped, nothing is sent that a page didn't cause.
		await steady.say({"do": "stop"})
		await silent.wait_for("clock", position=-1)
		await silent.hear_for(0.4)

		silent.answering = False
		before = silent.batch

		for step in range(6):

			await steady.say({"do": "tempo", "value": 10000 + step * 100, "id": step + 1})

			assert (await steady.wait_for("tempo", id=step + 1))["tempo"] == 10000 + step * 100

		heard = [message["tempo"] for message in await silent.hear_for(0.4) if message["type"] == "tempo"]

		assert silent.batch == before + synthsync.web.app.AHEAD
		assert len(heard) == synthsync.web.app.AHEAD
		assert 10500 not in heard

		status, health = await tools.page.fetch("127.0.0.1", port, "/api/health")

		assert status == 200
		assert sorted(health) == sorted(synthsync.web.app.HEALTH)
		assert health["pages"] == 2 and health["given_up"] == 0 and health["waiting"] > 0

		# Its socket is closed with nothing more sent over it, not even a goodbye.
		with pytest.raises(asyncio.IncompleteReadError):
			await silent.hear(4.0)

		status, health = await tools.page.fetch("127.0.0.1", port, "/api/health")

		assert health["pages"] == 1 and health["given_up"] == 1 and health["waiting"] == 0

		# The page that answers is none the worse, and a page that opens now begins from how things stand.
		await steady.say({"do": "tempo", "value": 11100, "id": 7})

		assert (await steady.wait_for("tempo", id=7))["tempo"] == 11100

		again = tools.page.Page("127.0.0.1", port)
		hello = await again.open()

		assert [message["tempo"] for message in hello["now"] if message["type"] == "tempo"] == [11100]

		await steady.close()
		await silent.close()
		await again.close()
		await asyncio.sleep(0.1)

		assert stand.app.pages == []

		serving.cancel()

		with contextlib.suppress(asyncio.CancelledError):
			await serving

	asyncio.run(scenario())


def test_the_stand_in_starts_from_the_command_line () -> None:

	"""`python -m synthsync.sim` says where it is and answers there."""

	port = free_port()
	process = subprocess.Popen(
		[sys.executable, "-B", "-m", "synthsync.sim", "--port", str(port)],
		cwd = ROOT,
		stdout = subprocess.PIPE,
		stderr = subprocess.STDOUT,
		text = True,
	)

	try:

		assert process.stdout is not None

		line = process.stdout.readline()

		assert line.strip() == "SynthSync's stand-in is at http://localhost:%d" % port

		async def ask () -> tuple[int, object]:

			"""Wait for the stand-in to be serving, then ask it for everything."""

			await wait_until_served(port)

			return await tools.page.fetch("127.0.0.1", port, "/api/state")

		status, state = asyncio.run(ask())

		assert status == 200
		assert isinstance(state, dict) and sorted(state.keys()) == ["fixed", "now"]

	finally:
		process.terminate()
		process.wait(timeout=10)


def test_the_stand_in_refuses_a_number_of_outputs_it_cannot_have () -> None:

	"""Asking for nine outputs is turned down before anything starts."""

	with pytest.raises(SystemExit):
		synthsync.sim.__main__.main(["--outputs", "9"])
