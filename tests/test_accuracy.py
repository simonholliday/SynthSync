"""The accuracy tool's sums and its questions to time servers are right, tried on runs made up to a known error.

The measuring itself needs a Pico and a night, and is done by hand.
"""

import ast
import json
import math
import pathlib
import random
import re
import socket
import struct
import subprocess
import sys
import threading

import pytest

import picoclock.core.timeline
import tools.accuracy
import tools.stage

ROOT = pathlib.Path(__file__).parent.parent

# When a made-up run begins, in seconds since 1970, and where this computer's hardware clock stands then.
BEGAN = 1794900000.0
RAW_BEGAN = 5000.0


def stamp (seconds: float) -> bytes:

	"""Turn seconds since 1970 into network time's eight bytes."""

	since = seconds + tools.accuracy.NTP_EPOCH
	whole = int(since)

	return struct.pack("!II", whole, int((since - whole) * 2 ** 32))


def reply (identity: int, reached: float, left: float, first: int = 0x24, stratum: int = 1) -> bytes:

	"""Make a time server's answer to a question carrying this number."""

	return bytes([first, stratum]) + bytes(22) + struct.pack("!Q", identity) + stamp(reached) + stamp(left)


def test_an_answer_gives_how_far_the_clock_is_out_and_how_long_it_took () -> None:

	"""A server half way through a 20 ms round trip, 5 s ahead of the hardware clock, is read as just that."""

	sample = tools.accuracy.answer(reply(77, BEGAN + 5.010, BEGAN + 5.0101), 77, "192.0.2.1", BEGAN, BEGAN + 0.0201)

	assert sample is not None
	assert sample.offset == pytest.approx(5.0, abs=1e-6)
	assert sample.delay == pytest.approx(0.020, abs=1e-6)
	assert sample.middle == pytest.approx(BEGAN + 0.01005, abs=1e-9)


@pytest.mark.parametrize("data", [
	reply(78, BEGAN, BEGAN),
	reply(77, BEGAN, BEGAN, stratum=0),
	reply(77, BEGAN, BEGAN, first=0xE4),
	reply(77, BEGAN, BEGAN, first=0x23),
	reply(77, BEGAN, BEGAN)[:40],
], ids=["another question's answer", "asked to ask less often", "server doesn't know the time", "not from a server", "cut short"])
def test_a_bad_answer_is_left_out (data: bytes) -> None:

	"""An answer that isn't a server's good answer to this very question is no answer."""

	assert tools.accuracy.answer(data, 77, "192.0.2.1", BEGAN, BEGAN + 0.02) is None


def test_a_question_carries_its_number_where_the_server_copies_it_from () -> None:

	"""The question is network time's version 4 from a client, with the number as its time sent."""

	asked = tools.accuracy.question(0x0123456789ABCDEF)

	assert len(asked) == 48
	assert asked[0] == 0x23
	assert struct.unpack("!Q", asked[40:48])[0] == 0x0123456789ABCDEF


def test_a_server_on_this_computer_is_asked_and_answers () -> None:

	"""Asking goes over a real socket, to a stand-in server that is a second ahead."""

	server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	server.bind(("127.0.0.1", 0))
	server.settimeout(5)
	port = server.getsockname()[1]

	def serve () -> None:

		"""Answer one question."""

		data, sender = server.recvfrom(512)
		identity = struct.unpack("!Q", data[40:48])[0]
		now = tools.accuracy.raw_clock() + 1.0
		server.sendto(reply(identity, now, now), sender)

	answering = threading.Thread(target=serve)
	answering.start()

	sample = tools.accuracy.ask("127.0.0.1", 5, port)
	answering.join()
	server.close()

	assert sample is not None
	assert sample.offset == pytest.approx(1.0, abs=0.01)
	assert 0 <= sample.delay < 0.05


def test_a_server_that_never_answers_gives_none () -> None:

	"""Nobody listening is no answer, soon."""

	quiet = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	quiet.bind(("127.0.0.1", 0))

	assert tools.accuracy.ask("127.0.0.1", 0.2, quiet.getsockname()[1]) is None

	quiet.close()


def made_up (server: str, middle: float, delay: float, offset: float = 0.0) -> tuple[str, tools.accuracy.Sample]:

	"""Make an answer from a server that stands for this moment, took this long and was this far out."""

	sent = middle - delay / 2
	received = middle + delay / 2

	return ("name-" + server, tools.accuracy.Sample(server, sent, middle + offset, middle + offset, received))


def test_only_the_quickest_answer_in_each_window_is_kept_for_each_server () -> None:

	"""Each server's slowest answers are dropped, window by window, and the servers kept apart."""

	samples = [
		made_up("a", 10.0, 0.030),
		made_up("a", 20.0, 0.012),
		made_up("b", 30.0, 0.050),
		made_up("a", 250.0, 0.040),
		made_up("a", 260.0, 0.041),
	]

	kept = tools.accuracy.quickest(samples, 240.0)

	assert [(sample.server, round(sample.delay, 3)) for _, sample in kept] == [("a", 0.012), ("b", 0.05), ("a", 0.04)]


def test_how_long_output_1_had_played_is_read_through_the_usb_delay () -> None:

	"""Reports that arrive a few milliseconds late, give or take, still say to well under a millisecond how far output 1 had got."""

	shake = random.Random(3)
	arrivals = []
	played = []

	for number in range(200):
		seconds = number * 2.0
		played.append(seconds)
		arrivals.append(100.0 + seconds * (1 + 20e-6) + 0.003 + shake.uniform(-0.002, 0.002))

	moment = 100.0 + 201.0 * (1 + 20e-6) + 0.003
	found = tools.accuracy.played_at(arrivals, played, moment)

	assert found is not None
	assert found == pytest.approx(201.0, abs=0.0005)


def test_a_moment_without_reports_on_both_sides_has_no_answer () -> None:

	"""Before the first report or after the last, nothing is guessed."""

	arrivals = [float(number) for number in range(10)]

	assert tools.accuracy.played_at(arrivals, arrivals, -5.0) is None
	assert tools.accuracy.played_at(arrivals, arrivals, 50.0) is None


def test_a_line_is_fitted_with_a_starting_point_for_each_group () -> None:

	"""Two servers out by different amounts still give one slope, the true one."""

	points: list[tuple[object, float, float]] = []

	for number in range(100):
		true = number * 60.0
		points.append(("a", true, true * (1 - 3e-6) + 0.004))
		points.append(("b", true, true * (1 - 3e-6) - 0.002))

	fitted = tools.accuracy.fit(points)

	assert fitted.ppm == pytest.approx(-3.0, abs=1e-6)
	assert fitted.error < 1e-6
	assert fitted.points == 200


def test_too_few_answers_fit_no_line () -> None:

	"""Two points and two groups leave nothing to measure with."""

	with pytest.raises(ValueError):
		tools.accuracy.fit([("a", 0.0, 0.0), ("b", 1.0, 1.0)])


def make_run (path: pathlib.Path, hours: float, pico_ppm: float, stretch: float = 1200.0, worst: int = 0, lost: int = 0, drift: int = -3) -> pathlib.Path:

	"""Write the log of a made-up run, with the Pico's tempo out by `pico_ppm`.

	This computer's hardware clock runs 15 ppm fast and wanders by two
	milliseconds either way. Three time servers are each out by a millisecond
	or two of their own, and every question and answer spends 10 ms on the way
	plus a random wait each way. Every report arrives between one and four
	milliseconds late.
	"""

	shake = random.Random(11)
	log = tools.accuracy.Log(path)
	pico = 1 + pico_ppm / 1000000
	tempos = tools.accuracy.TEMPOS
	servers = (("time.one", "192.0.2.1", 0.001), ("time.two", "192.0.2.2", -0.0015), ("time.three", "192.0.2.3", 0.0005))
	seconds = hours * 3600

	def raw (true: float) -> float:

		"""Give the hardware clock at a true time, in seconds into the run."""

		return RAW_BEGAN + true * (1 + 15e-6) + 0.002 * math.sin(true / 800)

	events: list[tuple[float, str, object]] = []

	# The Pico's reports: one every two seconds by its crystal, each printed
	# 150 microseconds after the pulse it waited for.
	edges = 0

	while edges * 2.0 / pico < seconds:
		true = edges * 2.0 / pico
		said = "ACCURACY at n=%d edges=%d cycles=%d since_us=150" % (edges + 1, edges + 1, edges * 2 * 150000000)
		events.append((true + 0.00015 + shake.uniform(0.001, 0.004), "pico", said))
		edges += 1

	# The stretches, one tempo after another, each judged as it ends.
	number = 0

	while number * stretch < seconds:
		tempo = tempos[number % len(tempos)]
		events.append((number * stretch + 0.002, "pico", "ACCURACY stretch n=%d tempo=%d" % (number, tempo)))
		events.append((min((number + 1) * stretch, seconds) + 0.001, "pico", "ACCURACY timed stretch=%d tempo=%d pulses=1000 worst_cycles=%d drift_cycles=%d" % (number, tempo, worst if number == 1 else 0, drift if number == 1 else -3)))
		number += 1

	# The time servers, one every eleven seconds or so, in turn.
	turn = 0

	while turn * 32.0 / 3 < seconds:
		name, address, off = servers[turn % 3]
		true = turn * 32.0 / 3 + 1.0
		going = 0.010 + shake.expovariate(1 / 0.003)
		coming = 0.010 + shake.expovariate(1 / 0.003)
		reached = BEGAN + true + going + off
		events.append((true + going + coming, "ntp", (name, tools.accuracy.Sample(address, raw(true), reached, reached + 0.0001, raw(true + going + 0.0001 + coming)))))
		turn += 1

	events.sort(key=lambda event: event[0])
	log.pico(raw(0.0), "ACCURACY begin hz=150000000 pulses=4 beats=1 outputs=4 every_s=2 stretch_s=%d total_s=%d tempos=%s" % (stretch, seconds, ",".join(str(tempo) for tempo in tempos)))
	log.pico(raw(0.5), "Traceback (most recent call last): a line that isn't a report")

	for true, kind, what in events:

		if kind == "pico":
			assert isinstance(what, str)
			log.pico(raw(true), what)
		else:
			assert isinstance(what, tuple)
			log.ntp(what[0], what[1])

	log.pico(raw(seconds + 1), "ACCURACY end edges=%d lost=%d faults=0 refused=0 fewest_waiting=6 slowest_command_us=1800 empty=0 one_left=0" % (edges, lost))
	log.close()

	return path


@pytest.mark.parametrize("pico_ppm", [-2.4, 0.0, 17.5])
def test_a_run_finds_the_picos_own_error_through_everything_elses (tmp_path: pathlib.Path, pico_ppm: float) -> None:

	"""Through a fast and wandering computer clock, servers that disagree and delays on every path, the Pico's error is found to a few hundredths of a part per million, and within the give or take."""

	path = make_run(tmp_path / "run.log", 4.0, pico_ppm)

	with open(path, encoding="utf-8") as handle:
		result = tools.accuracy.analyse(tools.accuracy.read_log(handle))

	assert result["ppm"] == pytest.approx(pico_ppm, abs=0.05)
	assert abs(result["ppm"] - pico_ppm) <= 2 * result["give_or_take_ppm"]
	assert result["every_pulse_on_time"]
	assert result["hours"] == pytest.approx(4.0, abs=0.01)
	assert sorted(result["servers"]) == ["time.one", "time.three", "time.two"]
	assert len(result["hour_by_hour"]) == 4

	for hour in result["hour_by_hour"]:
		assert hour["ppm"] == pytest.approx(pico_ppm, abs=0.5)

	assert [tempo["bpm"] for tempo in result["tempos"]] == [tempo / 100 for tempo in sorted(tools.accuracy.TEMPOS)]

	for tempo in result["tempos"]:
		assert tempo["against_network_time"]["ppm"] == pytest.approx(pico_ppm, abs=0.5)
		assert tempo["furthest_drift_cycles"] == 3

	assert result["other_lines"] == ["Traceback (most recent call last): a line that isn't a report"]


@pytest.mark.parametrize("worst, lost, drift", [(5, 0, -3), (0, 3, -3), (0, 0, 155), (0, 0, -155)], ids=["a gap off by five cycles", "gaps lost from the ring", "a stretch a tick and more long", "a stretch a tick and more short"])
def test_a_run_with_a_pulse_out_of_place_says_so (tmp_path: pathlib.Path, worst: int, lost: int, drift: int) -> None:

	"""A gap further out than the timing can see, gaps that went untimed, or a stretch that drifted past the microsecond every pulse is rounded to, mean not every pulse was shown to be on time."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4, worst=worst, lost=lost, drift=drift)

	with open(path, encoding="utf-8") as handle:
		result = tools.accuracy.analyse(tools.accuracy.read_log(handle))

	assert not result["every_pulse_on_time"]
	assert "NOT every pulse was on time" in tools.accuracy.describe(result)


def test_a_stretch_drifting_by_less_than_a_microsecond_is_on_time (tmp_path: pathlib.Path) -> None:

	"""At a tempo whose gaps aren't whole microseconds, a stretch can come out up to a tick from its ideal, as the Pico's own run at 174 BPM did, and that is on time."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4, drift=140)

	with open(path, encoding="utf-8") as handle:
		assert tools.accuracy.analyse(tools.accuracy.read_log(handle))["every_pulse_on_time"]


def test_a_report_counts_the_time_since_its_pulse () -> None:

	"""How long output 1 had played when a report was printed is its gaps added up and the time since the last of them: a report held up by the first core says so."""

	run = tools.accuracy.read_log([
		"pico 10.0 ACCURACY begin hz=150000000",
		"pico 12.5 ACCURACY at n=1 edges=4 cycles=300000000 since_us=250",
		"pico 15.0 ACCURACY at n=2 edges=8 cycles=600000000 since_us=500000",
	])

	assert [arrived for arrived, _ in run.reports] == [12.5, 15.0]
	assert [played for _, played in run.reports] == pytest.approx([2.00025, 4.5])


def test_a_run_cut_short_before_its_end_is_not_called_on_time (tmp_path: pathlib.Path) -> None:

	"""Without the Pico's last word, the player's own counts are missing, so nothing is promised."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4)
	lines = [line for line in path.read_text(encoding="utf-8").splitlines() if "ACCURACY end" not in line]

	result = tools.accuracy.analyse(tools.accuracy.read_log(lines))
	said = tools.accuracy.describe(result)

	assert not result["every_pulse_on_time"]
	assert result["cut_short"] and result["timed_well"]
	assert "The run was cut short" in said and "NOT every pulse" not in said
	assert result["ppm"] == pytest.approx(-2.4, abs=0.1)


def test_the_result_says_slow_and_fast_in_words_and_in_bpm (tmp_path: pathlib.Path) -> None:

	"""A slow Pico is called slow, with its BPM at 120 and its time an hour."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4)

	with open(path, encoding="utf-8") as handle:
		said = tools.accuracy.describe(tools.accuracy.analyse(tools.accuracy.read_log(handle)))

	slow = re.search(r"ran slow by ([0-9.]+) parts per million", said)
	hour = re.search(r"BPM: ([0-9.]+) ms in an hour", said)

	assert slow is not None and float(slow.group(1)) == pytest.approx(2.4, abs=0.05)
	assert "At 120 BPM that is 119.9997" in said
	assert hour is not None and float(hour.group(1)) == pytest.approx(8.64, abs=0.2)


def test_the_result_is_kept_beside_the_log_and_replaced_by_a_later_working_out (tmp_path: pathlib.Path) -> None:

	"""Working a log out twice leaves one result file, the later one."""

	log = tmp_path / "run.log"
	log.write_text("", encoding="utf-8")

	tools.accuracy.keep_result(log, {"ppm": 1.0})
	kept = tools.accuracy.keep_result(log, {"ppm": 2.0})

	assert kept == tmp_path / "run.json"
	assert json.loads(kept.read_text(encoding="utf-8")) == {"ppm": 2.0}
	assert sorted(entry.name for entry in tmp_path.iterdir()) == ["run.json", "run.log"]


def test_a_log_from_before_can_be_worked_out_by_itself (tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:

	"""--analyse reads a log, says what it shows and keeps the result, with no Pico."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4)

	assert tools.accuracy.main(["--analyse", str(path)]) == 0
	assert "ran slow by 2." in capsys.readouterr().out
	assert (tmp_path / "run.json").exists()


@pytest.mark.parametrize("seconds, expected", [(43200, 1800), (3600, 360), (300, 60)])
def test_every_tempo_gets_at_least_two_stretches (seconds: int, expected: int) -> None:

	"""Stretches are as long as half an hour, and as short as a minute, so that every tempo comes round twice."""

	assert tools.accuracy.stretch_for(seconds, len(tools.accuracy.TEMPOS)) == expected


def test_the_picos_half_is_sent_with_the_run_asked_for () -> None:

	"""The Pico's half ends by measuring at the tempos and lengths asked for, at PicoClock's own PPQN."""

	script = tools.accuracy.board_script((3000, 12000), 600, 7200)

	assert script.endswith("\nmeasure((3000, 12000), 600, 2, 7200, 4, 1)\n")


def test_the_picos_half_compiles_for_micropython (tmp_path: pathlib.Path) -> None:

	"""MicroPython's own compiler takes the Pico's half."""

	compiler = tools.stage.find_tool("mpy-cross")

	if compiler is None:
		pytest.skip("mpy-cross isn't here")

	result = subprocess.run([compiler, "-o", str(tmp_path / "accuracy.mpy"), str(ROOT / "tools" / "board" / "accuracy.py")], capture_output=True, text=True)

	assert result.returncode == 0, result.stderr


def test_the_tool_needs_nothing_but_python_and_mpremote () -> None:

	"""Anyone measuring their own board has Python and mpremote, and nothing of PicoClock's development, so the tool imports only the standard library."""

	tree = ast.parse((ROOT / "tools" / "accuracy.py").read_text(encoding="utf-8"))
	imported: set[str] = set()

	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			imported.update(alias.name.split(".")[0] for alias in node.names)
		elif isinstance(node, ast.ImportFrom):
			imported.add((node.module or "").split(".")[0])

	assert imported and imported <= sys.stdlib_module_names, imported - sys.stdlib_module_names


def test_the_picos_half_times_pulses_as_the_rig_does () -> None:

	"""The state machine program that times the gaps is the rig's, instruction for instruction, so the rig's proof of it holds."""

	def body (path: pathlib.Path, name: str) -> list[str]:

		"""Give a function's statements, without its docstring."""

		tree = ast.parse(path.read_text(encoding="utf-8"))
		found = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]

		return [ast.unparse(statement) for statement in found[0].body[1:]]

	assert body(ROOT / "tools" / "board" / "accuracy.py", "_timing_program") == body(ROOT / "tools" / "board" / "rig.py", "_capture_program")


@pytest.mark.parametrize("freedom, factor", [(1, 12.706), (2, 4.303), (11, 2.228), (40, 2.021), (59, 2.021), (120, 1.98), (500, 1.96)])
def test_give_or_take_widens_for_a_line_with_few_answers_to_spare (freedom: int, factor: float) -> None:

	"""Student's t, taken from the row at or below the degrees of freedom, so never narrower than the truth."""

	assert tools.accuracy.student(freedom) == factor


def test_a_fit_with_few_answers_gives_a_wide_give_or_take () -> None:

	"""Four points in one group leave two degrees of freedom, so the give or take is 4.3 standard errors, not 2."""

	points: list[tuple[object, float, float]] = [("a", 0.0, 0.0), ("a", 100.0, 100.001), ("a", 200.0, 199.999), ("a", 300.0, 300.0005)]
	fitted = tools.accuracy.fit(points)
	mean_true = sum(true for _, true, _ in points) / 4
	mean_played = sum(played for _, _, played in points) / 4
	centred = [(true - mean_true, played - mean_played) for _, true, played in points]
	along = sum(true ** 2 for true, _ in centred)
	slope = sum(true * played for true, played in centred) / along
	squares = sum((played - slope * true) ** 2 for true, played in centred)

	assert fitted.error == pytest.approx(4.303 * (squares / 2 / along) ** 0.5 * 1000000, rel=1e-6)


@pytest.mark.parametrize("typed, expected", [("30,60,120,174,300", (3000, 6000, 12000, 17400, 30000)), ("100.01", (10001,)), ("20, 300", (2000, 30000))])
def test_tempos_are_read_in_hundredths (typed: str, expected: tuple[int, ...]) -> None:

	"""BPMs with up to two decimal places, from 20 to 300."""

	assert tools.accuracy.tempos_from(typed) == expected


def refuse_to_go_on (monkeypatch: pytest.MonkeyPatch) -> None:

	"""Make anything past the tool's checks fail at once, so that a check that let something through can't reach the time servers or the Pico."""

	def went_on (*anything: object) -> None:

		"""Stand in for everything after the checks."""

		raise AssertionError("the tool went on past its checks")

	monkeypatch.setattr(tools.accuracy, "find_servers", went_on)
	monkeypatch.setattr(tools.accuracy, "measure", went_on)
	monkeypatch.setattr(tools.accuracy, "MPREMOTE", [sys.executable, "-c", "raise SystemExit(3)"])


@pytest.mark.parametrize("typed", ["0", "400", "19.99", "120.001", "fast", ""])
def test_a_tempo_picoclock_cant_play_is_refused_before_anything_starts (typed: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:

	"""A tempo out of range, too finely given, or not a number stops the tool before it touches the Pico."""

	refuse_to_go_on(monkeypatch)

	with pytest.raises(SystemExit):
		tools.accuracy.main(["--tempos", typed])

	assert "error" in capsys.readouterr().err


def test_a_run_longer_than_the_pico_can_time_is_refused (capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:

	"""Past six days the Pico's count of milliseconds would wrap, so a longer run is refused."""

	refuse_to_go_on(monkeypatch)

	with pytest.raises(SystemExit):
		tools.accuracy.main(["--hours", "200"])

	assert "at most 144 hours" in capsys.readouterr().err


def test_a_server_that_asks_to_be_asked_less_often_says_so_over_a_real_socket () -> None:

	"""A stand-in server on this computer answers with a kiss of death, and asking raises rather than giving no answer."""

	server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
	server.bind(("127.0.0.1", 0))
	server.settimeout(5)

	def serve () -> None:

		"""Answer one question with a stratum of 0."""

		data, sender = server.recvfrom(512)
		identity = struct.unpack("!Q", data[40:48])[0]
		server.sendto(reply(identity, BEGAN, BEGAN, stratum=0), sender)

	answering = threading.Thread(target=serve)
	answering.start()

	with pytest.raises(tools.accuracy.TooOften):
		tools.accuracy.ask("127.0.0.1", 5, server.getsockname()[1])

	answering.join()
	server.close()


def test_a_kiss_of_death_is_told_from_a_good_answer () -> None:

	"""A stratum of 0 from a server, to this question, asks to be asked less often."""

	assert tools.accuracy.kissed(reply(77, BEGAN, BEGAN, stratum=0), 77)
	assert not tools.accuracy.kissed(reply(77, BEGAN, BEGAN), 77)
	assert not tools.accuracy.kissed(reply(78, BEGAN, BEGAN, stratum=0), 77)


def test_a_server_that_asks_to_be_asked_less_is_left_out_for_a_while (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""After a kiss of death, the server's next turns are passed over, and the other servers carry on."""

	asked: list[str] = []

	def ask (address: str) -> tools.accuracy.Sample | None:

		"""Stand in for asking: one server always says to ask less often, and the other always answers."""

		asked.append(address)

		if address == "192.0.2.1":
			raise tools.accuracy.TooOften(address)

		return made_up(address, 100.0, 0.02)[1]

	class Soon:

		"""Stands in for the stop event, letting a set number of turns pass at once."""

		def __init__ (self) -> None:

			"""Begin with forty turns to go."""

			self.left = 40

		def wait (self, seconds: float) -> bool:

			"""Say whether to stop, after the turns have run out."""

			self.left -= 1

			return self.left < 0

	monkeypatch.setattr(tools.accuracy, "ask", ask)
	log = tools.accuracy.Log(tmp_path / "run.log")
	tools.accuracy.keep_asking({"kisser": "192.0.2.1", "other": "192.0.2.2"}, log, Soon())  # type: ignore[arg-type]
	log.close()

	# Twenty turns each: the kisser is asked on the first, rests for sixteen, and is asked once more.
	assert asked.count("192.0.2.1") == 2
	assert asked.count("192.0.2.2") == 20
	assert "asked to be asked less often" in (tmp_path / "run.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("broke", ["ACCURACY stalled after_ms=36000", "ACCURACY overrun lost=12"])
def test_a_run_the_pico_stopped_timing_part_way_is_not_on_time (tmp_path: pathlib.Path, broke: str) -> None:

	"""A dead output or a ring overrun ends the Pico's timing, and the result says so."""

	path = make_run(tmp_path / "run.log", 2.0, -2.4)
	lines = path.read_text(encoding="utf-8").splitlines()
	lines.insert(len(lines) - 1, "pico 99999.0 " + broke)

	result = tools.accuracy.analyse(tools.accuracy.read_log(lines))

	assert not result["timed_well"] and not result["every_pulse_on_time"]
	assert "stopped timing output 1 part way" in tools.accuracy.describe(result)


def test_an_hour_with_too_few_answers_keeps_the_others_in_their_places (tmp_path: pathlib.Path) -> None:

	"""With the second hour's answers gone, the third hour is still the third."""

	path = make_run(tmp_path / "run.log", 3.0, -2.4)
	run = tools.accuracy.read_log(path.read_text(encoding="utf-8").splitlines())
	first = min(sample.middle for _, sample in run.samples)
	kept = [(name, sample) for name, sample in run.samples if not 3550 <= sample.middle - first < 7250]
	result = tools.accuracy.analyse(run._replace(samples=kept))

	assert [hour["hour"] for hour in result["hour_by_hour"]] == [0, 1, 2]
	assert result["hour_by_hour"][1]["ppm"] is None
	assert result["hour_by_hour"][2]["ppm"] == pytest.approx(-2.4, abs=0.5)
	assert "too few answers" in tools.accuracy.describe(result)


def test_a_run_too_short_for_any_one_server_says_so_rather_than_naming_none (tmp_path: pathlib.Path) -> None:

	"""Six minutes leaves no server with answers enough for a fit of its own, and the summary says that in words."""

	path = make_run(tmp_path / "run.log", 3.0, -2.4)
	result = tools.accuracy.analyse(tools.accuracy.read_log(path.read_text(encoding="utf-8").splitlines()))
	said = tools.accuracy.describe(dict(result, servers={}))

	assert "Against each time server: too few answers from any one of them" in said


def test_a_short_stretch_settles_for_a_quarter_of_its_length (tmp_path: pathlib.Path) -> None:

	"""A stretch of two minutes counts from 30 s in, so a short run has settled moments in every stretch, where a minute's settling would leave only one."""

	path = make_run(tmp_path / "run.log", 1.0, -2.4, stretch=120.0)
	run = tools.accuracy.read_log(path.read_text(encoding="utf-8").splitlines())
	second = run.stretches[1]

	assert run.stretch_s == 120
	assert tools.accuracy.stretch_at(run, second.began + 29) is None
	assert tools.accuracy.stretch_at(run, second.began + 31) == second
	assert tools.accuracy.stretch_at(run._replace(stretch_s=0), second.began + 31) is None


def test_the_picos_half_works_out_gaps_as_the_scheduler_does () -> None:

	"""The board half's own copy of the gap lengths, for older installs, gives what `gap_ticks` gives, in cycles."""

	tree = ast.parse((ROOT / "tools" / "board" / "accuracy.py").read_text(encoding="utf-8"))
	found: list[ast.stmt] = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "gap_lengths"]
	space: dict[str, object] = {"_UNITS": 6000 * 1000000, "_CYCLES_PER_TICK": 150}
	exec(compile(ast.Module(body=found, type_ignores=[]), "gap_lengths", "exec"), space)
	gap_lengths = space["gap_lengths"]

	for tempo in (2000, 3000, 10001, 12000, 17400, 29999, 30000):
		for pulses, beats in ((4, 1), (96, 1), (1, 128), (3, 2), (7, 12)):
			assert gap_lengths(tempo, pulses, beats) == tuple(150 * ticks for ticks in picoclock.core.timeline.gap_ticks(tempo, pulses, beats)), (tempo, pulses, beats)  # type: ignore[operator]


def test_a_pico_that_goes_quiet_ends_the_run_and_a_spoiled_byte_does_not (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""A stand-in for mpremote prints two lines, one with a byte that isn't UTF-8, then says nothing: the run ends after the quiet allowed, with both lines logged."""

	stand_in = tmp_path / "quiet.py"
	stand_in.write_text("import sys, time\nsys.stdout.buffer.write(b'ACCURACY begin hz=150000000\\n\\xff spoiled\\n')\nsys.stdout.flush()\ntime.sleep(60)\n", encoding="utf-8")

	monkeypatch.setattr(tools.accuracy, "MPREMOTE", [sys.executable, str(stand_in)])
	monkeypatch.setattr(tools.accuracy, "QUIET_FOR", 1.0)
	monkeypatch.setattr(tools.accuracy, "keep_asking", lambda found, log, stop: None)

	log = tools.accuracy.Log(tmp_path / "run.log")
	began = tools.accuracy.raw_clock()
	tools.accuracy.measure("auto", log, (12000,), 60, 60, {})
	log.close()

	said = (tmp_path / "run.log").read_text(encoding="utf-8")

	assert tools.accuracy.raw_clock() - began < 10
	assert "ACCURACY begin" in said and "spoiled" in said
	assert "said nothing for 1 s" in said
