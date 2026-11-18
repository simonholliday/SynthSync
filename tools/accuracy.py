"""Measure how close SynthSync's tempo is to true, at its output, against network time.

    python -m tools.accuracy
    python -m tools.accuracy --hours 1
    python -m tools.accuracy --analyse accuracy-2026-11-17-2000.log

The Pico plays output 1 through several tempos, a stretch of each in turn, and
times every one of its pulses at the pin, to a cycle of its 150 MHz clock. That
shows whether each pulse is exactly where its tempo puts it. But the Pico can
only time its pulses by its own crystal, which may run a little fast or slow.
So every couple of seconds it says how long output 1 has been playing, by that
crystal, and this computer notes when each report arrives. Over a long run the
difference between the two is how far the tempo is from true, in parts per
million. One part per million at 120 BPM is 119.99988 BPM, or 3.6 ms in an
hour.

This computer's own clock isn't trusted either, since it may be set from the
network only now and then, or nudged faster and slower to catch up. The
reports are noted by its hardware clock, which nothing adjusts, and it asks
time servers on the internet itself, every ten seconds or so, how far that
clock is out. Each answer is only as good as the time it took to come back,
so for each server only the quickest answer in every four minutes is kept, and
not even that one if it still took far longer than the server's best.

It needs SynthSync installed on a Pico on a USB port, and a computer with
Python 3.10 or newer, mpremote and the internet: nothing else. It stops
SynthSync while it runs and restarts the Pico at the end. Meanwhile the
outputs play the tempos being measured, so anything patched to them will hear
it. Keep the computer awake throughout.

Everything is written to a log as it happens, so a run that was cut short can
still be worked out afterwards with --analyse. The results go in a file beside
the log, with the same name ending .json.
"""

import argparse
import bisect
import glob
import importlib.util
import json
import os
import pathlib
import random
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import typing

# Only the standard library is used here, and mpremote, which is run as a
# program, so that anyone with Python and mpremote can measure their own board.
ROOT = pathlib.Path(__file__).parent.parent

# mpremote, run by the Python running this, wherever pip put it.
MPREMOTE = [sys.executable, "-m", "mpremote"]

# How many times to try restarting the Pico, which may be slow to answer after a long run.
RESTART_TRIES = 3

# The tempos output 1 plays, in hundredths of a BPM, one stretch of each in turn,
# and how many pulses a beat: SynthSync's own PPQN, which most people use.
# 174 BPM is there because its gaps aren't a whole number of microseconds.
TEMPOS = (3000, 6000, 12000, 17400, 30000)
PPQN = 4

# How long each stretch lasts at most and at least, in seconds. A run goes
# through every tempo at least twice.
LONGEST_STRETCH = 1800
SHORTEST_STRETCH = 60

# How often the Pico reports, in seconds.
EVERY = 2

# The time servers asked, how often each one is asked, and how long the
# window is in which only the quickest answer from each is kept, in seconds.
SERVERS = ("time.google.com", "time.cloudflare.com", "pool.ntp.org")
NTP_EVERY = 32.0
WINDOW = 240.0

# How much longer than its server's quickest answer of the run an answer may
# take and still count, in seconds. All the extra could be on one leg of the
# trip, which would put the answer out by half of it. A window near the end of
# a run can hold a single answer, so being its quickest says nothing.
SLOWEST_EXTRA = 0.020

# How long to wait for a time server's answer, in seconds, and after how many
# unanswered questions in a row its name is looked up afresh.
NTP_PATIENCE = 2.0
NTP_LOOK_AGAIN = 10

# Seconds between 1900, when network time begins, and 1970, when this computer's does.
NTP_EPOCH = 2208988800

# How far either side of an answer from a time server the Pico's reports are
# taken, to say how far output 1 had got at that moment, in seconds, and how
# many reports that needs at least.
REACH = 60.0
FEWEST_REPORTS = 5

# How long after a change of tempo before that tempo's stretch is judged
# against network time, in seconds.
SETTLE = 60.0

# The furthest a gap may be from what its tempo asks for, in cycles: the
# timing state machine sees an edge to within two. And how far a stretch's
# gaps together may come from its ideal, beyond the one tick of a microsecond
# the scheduler rounds every pulse to.
WORST_ALLOWED = 2
DRIFT_SLACK = 4

# "Give or take" is how far either side the true figure is, 95 times in 100:
# so many standard errors, which depends on how many answers the line had to
# spare. Student's t, by those degrees of freedom, and beyond the last, about 2.
STUDENT = ((1, 12.706), (2, 4.303), (3, 3.182), (4, 2.776), (5, 2.571), (6, 2.447), (7, 2.365), (8, 2.306), (9, 2.262), (10, 2.228), (12, 2.179), (15, 2.131), (20, 2.086), (25, 2.060), (30, 2.042), (40, 2.021), (60, 2.000), (120, 1.980))
CONFIDENCE_IS = "95 in 100"

# How often a run in progress says how it is going, and how long the Pico may
# say nothing before the run is ended as stalled, in seconds. It reports
# every two seconds.
PROGRESS_EVERY = 600.0
QUIET_FOR = 30.0

# The longest a run can be, in hours: the Pico's clock of milliseconds can
# measure no longer a stretch of time than about six days.
LONGEST_RUN = 144.0

# How many turns a time server that asks to be asked less often is left out for.
KISSED_SKIP = 16

# The Pico's half, which is sent to it and run.
BOARD_HALF = ROOT / "tools" / "board" / "accuracy.py"


def raw_clock () -> float:

	"""Give this computer's hardware clock, in seconds, which nothing nudges to keep it right."""

	# Linux's ordinary clocks are slewed by its own time keeping. Elsewhere the
	# performance counter is the raw one.
	if hasattr(time, "CLOCK_MONOTONIC_RAW"):
		return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)

	return time.perf_counter()


# ---------------------------------------------------------------------------
# Asking time servers.


class Sample (typing.NamedTuple):

	"""One answer from a time server: when the question left and the answer came back, by this computer's hardware clock, and when the server had them, in seconds since 1970."""

	server: str
	sent: float
	reached: float
	left: float
	received: float

	@property
	def offset (self) -> float:

		"""How far true time is ahead of the hardware clock, in seconds."""

		return ((self.reached - self.sent) + (self.left - self.received)) / 2

	@property
	def delay (self) -> float:

		"""How long the question and answer spent on the way, in seconds."""

		return (self.received - self.sent) - (self.left - self.reached)

	@property
	def middle (self) -> float:

		"""When, by the hardware clock, the answer stands for."""

		return (self.sent + self.received) / 2


def to_seconds (stamp: bytes) -> float:

	"""Turn network time's eight bytes into seconds since 1970."""

	whole, part = struct.unpack("!II", stamp)

	return float(whole - NTP_EPOCH) + float(part) / 2 ** 32


def question (identity: int) -> bytes:

	"""Make a question for a time server, carrying a number its answer must give back."""

	# Version 4, from a client. The number goes where the time sent would, and
	# the server copies it into its answer.
	return bytes([0x23]) + bytes(39) + struct.pack("!Q", identity)


def answer (data: bytes, identity: int, server: str, sent: float, received: float) -> Sample | None:

	"""Read a time server's answer, or give None if it isn't a good answer to this question."""

	if len(data) < 48:
		return None

	leap = data[0] >> 6
	mode = data[0] & 7
	stratum = data[1]

	# A server that doesn't know the time says so with a leap of 3, and one
	# asking to be asked less often says so with a stratum of 0.
	if mode != 4 or leap == 3 or not 1 <= stratum <= 15:
		return None

	if struct.unpack("!Q", data[24:32])[0] != identity:
		return None

	return Sample(server, sent, to_seconds(data[32:40]), to_seconds(data[40:48]), received)


class TooOften (Exception):

	"""A time server has asked to be asked less often."""


def kissed (data: bytes, identity: int) -> bool:

	"""Say whether an answer to this question asks to be asked less often: a stratum of 0, which network time calls a kiss of death."""

	return len(data) >= 48 and data[0] & 7 == 4 and data[1] == 0 and struct.unpack("!Q", data[24:32])[0] == identity


def ask (server: str, patience: float = NTP_PATIENCE, port: int = 123) -> Sample | None:

	"""Ask one time server, by its address, how far this computer's hardware clock is out, or give None if it doesn't answer well.

	A server that asks to be asked less often raises `TooOften`.
	"""

	identity = random.getrandbits(64)

	with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as asking:

		asking.settimeout(patience)

		try:
			sent = raw_clock()
			asking.sendto(question(identity), (server, port))
			data = asking.recv(512)
			received = raw_clock()

		except OSError:
			return None

	if kissed(data, identity):
		raise TooOften(server)

	return answer(data, identity, server, sent, received)


def look_up (name: str) -> str | None:

	"""Find a time server's address, or give None if its name can't be found."""

	try:
		return str(socket.getaddrinfo(name, 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0])
	except (OSError, IndexError):
		return None


# ---------------------------------------------------------------------------
# The log.


class Log:

	"""Everything a run hears, written down as it happens, and kept to be worked out at the end."""

	def __init__ (self, path: pathlib.Path) -> None:

		"""Begin a new log, which mustn't exist already."""

		self.lines: list[str] = []
		self._lock = threading.Lock()
		self._file = open(path, "x", encoding="utf-8")

	def write (self, line: str) -> None:

		"""Write one line."""

		with self._lock:
			self.lines.append(line)
			self._file.write(line + "\n")
			self._file.flush()

	def note (self, words: str) -> None:

		"""Write something said about the run, which the working out passes over."""

		self.write("# " + words)

	def pico (self, arrived: float, said: str) -> None:

		"""Write one line the Pico printed, with when it arrived by the hardware clock."""

		self.write("pico %.6f %s" % (arrived, said))

	def ntp (self, name: str, sample: Sample) -> None:

		"""Write one answer from a time server."""

		self.write("ntp %s %s %.6f %.9f %.9f %.6f" % (name, sample.server, sample.sent, sample.reached, sample.left, sample.received))

	def close (self) -> None:

		"""Finish the log."""

		self._file.close()


def find_servers (names: list[str]) -> dict[str, str | None]:

	"""Look up each time server's address, and say which, if any, answers now."""

	found = {name: look_up(name) for name in names}

	return {name: address for name, address in found.items() if address is not None and answers(address)}


def answers (address: str) -> bool:

	"""Say whether a time server answers well, asking it up to three times."""

	for _ in range(3):

		try:
			if ask(address) is not None:
				return True

		except TooOften:
			return False

	return False


def keep_asking (found: dict[str, str | None], log: Log, stop: threading.Event) -> None:

	"""Ask each time server in turn, each one every NTP_EVERY seconds, until told to stop, and log the answers."""

	names = sorted(found)
	unanswered = {name: 0 for name in names}
	resting = {name: 0 for name in names}
	turn = 0

	if not names:
		return

	while not stop.wait(NTP_EVERY / len(names)):

		name = names[turn % len(names)]
		turn += 1
		address = found[name]

		# A server that asked to be asked less often is left out for a while.
		if resting[name]:
			resting[name] -= 1
			continue

		try:
			sample = ask(address) if address is not None else None

		except TooOften:
			resting[name] = KISSED_SKIP
			log.note("time server %s asked to be asked less often, so it is left out for %d turns" % (name, KISSED_SKIP))
			continue

		if sample is not None:
			log.ntp(name, sample)
			unanswered[name] = 0
			continue

		unanswered[name] += 1

		# A server that stops answering may have moved, as pool servers do.
		if unanswered[name] >= NTP_LOOK_AGAIN:
			found[name] = look_up(name)
			unanswered[name] = 0
			log.note("time server %s stopped answering, and is now looked for at %s" % (name, found[name]))


# ---------------------------------------------------------------------------
# Working it out.


class Stretch (typing.NamedTuple):

	"""One tempo's stretch: its number, its tempo in hundredths of a BPM, and when the Pico said it began, by the hardware clock."""

	number: int
	tempo: int
	began: float


class Run (typing.NamedTuple):

	"""What a log holds, read out of it."""

	hz: int
	stretch_s: int
	reports: list[tuple[float, float]]
	stretches: list[Stretch]
	timed: list[dict[str, int]]
	samples: list[tuple[str, Sample]]
	end: dict[str, int] | None
	broke: list[str]
	other: list[str]


class Fit (typing.NamedTuple):

	"""How fast or slow output 1 ran against true time, from a straight line through the points."""

	ppm: float
	error: float
	spread_ms: float
	points: int


def fields (words: list[str]) -> dict[str, int]:

	"""Read words like `edges=12` into numbers by name, leaving out any that aren't."""

	found = {}

	for word in words:

		name, equals, value = word.partition("=")

		if equals and value.lstrip("-").isdigit():
			found[name] = int(value)

	return found


def read_log (lines: typing.Iterable[str]) -> Run:

	"""Read a run's log, line by line.

	Each report becomes when it arrived by the hardware clock, and how long
	output 1 had played when the Pico printed it, by the Pico's crystal, in
	seconds: its gaps added up, and the time since the last of them.
	"""

	hz = 0
	stretch_s = 0
	reports: list[tuple[float, float]] = []
	stretches: list[Stretch] = []
	timed: list[dict[str, int]] = []
	samples: list[tuple[str, Sample]] = []
	end: dict[str, int] | None = None
	broke: list[str] = []
	other: list[str] = []

	for line in lines:

		words = line.split()

		if len(words) == 7 and words[0] == "ntp":
			samples.append((words[1], Sample(words[2], float(words[3]), float(words[4]), float(words[5]), float(words[6]))))
			continue

		if len(words) < 2 or words[0] != "pico":
			continue

		arrived = float(words[1])
		said = words[2:]

		if len(said) < 2 or said[0] != "ACCURACY":
			if said:
				other.append(" ".join(said))
			continue

		kind = said[1]
		found = fields(said[2:])

		if kind == "begin":
			hz = found.get("hz", 0)
			stretch_s = found.get("stretch_s", 0)
		elif kind == "stalled" or kind == "overrun":
			broke.append(" ".join(said[1:]))
		elif kind == "at" and hz and {"edges", "cycles", "since_us"} <= found.keys():
			reports.append((arrived, found["cycles"] / hz + found["since_us"] / 1000000))
		elif kind == "stretch" and {"n", "tempo"} <= found.keys():
			stretches.append(Stretch(found["n"], found["tempo"], arrived))
		elif kind == "timed":
			timed.append(found)
		elif kind == "end":
			end = found

	return Run(hz, stretch_s, reports, stretches, timed, samples, end, broke, other)


def quickest (samples: list[tuple[str, Sample]], window: float = WINDOW) -> list[tuple[str, Sample]]:

	"""Keep, for each server, the answer that came back quickest in each window of time."""

	kept: dict[tuple[str, int], tuple[str, Sample]] = {}

	for name, sample in samples:

		key = (sample.server, int(sample.middle // window))

		if key not in kept or sample.delay < kept[key][1].delay:
			kept[key] = (name, sample)

	return sorted(kept.values(), key=lambda each: each[1].middle)


def prompt_enough (kept: list[tuple[str, Sample]], extra: float = SLOWEST_EXTRA) -> list[tuple[str, Sample]]:

	"""Leave out any answer that took far longer than its server's quickest."""

	least: dict[str, float] = {}

	for _, sample in kept:
		least[sample.server] = min(sample.delay, least.get(sample.server, sample.delay))

	return [each for each in kept if each[1].delay - least[each[1].server] <= extra]


def played_at (arrivals: list[float], played: list[float], moment: float, reach: float = REACH) -> float | None:

	"""Say how long output 1 had played at a moment by the hardware clock, from the reports either side of it, or None if there are too few.

	A report's arrival is late by however long USB took, which varies, so a
	straight line through the reports nearby says it better than any one.
	"""

	low = bisect.bisect_left(arrivals, moment - reach)
	high = bisect.bisect_right(arrivals, moment + reach)

	if high - low < FEWEST_REPORTS or arrivals[low] > moment or arrivals[high - 1] < moment:
		return None

	# How long output 1 had played is exact, and the arrival is what varies, so
	# the line gives arrival from that.
	xs = played[low:high]
	ys = arrivals[low:high]
	mean_x = sum(xs) / len(xs)
	mean_y = sum(ys) / len(ys)
	spread = sum((x - mean_x) ** 2 for x in xs)

	if not spread:
		return None

	slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread

	return mean_x + (moment - mean_y) / slope


def fit (points: list[tuple[object, float, float]]) -> Fit:

	"""Fit how long output 1 had played against true time, as one straight line with its own starting point for each group.

	Each point is its group, the true time in seconds and how long output 1
	had played. A group is one time server, whose answers may all be out by
	the same small amount, or one server over one stretch.
	"""

	groups: dict[object, list[tuple[float, float]]] = {}

	for group, true, played in points:
		groups.setdefault(group, []).append((true, played))

	along = 0.0
	across = 0.0
	centred = []

	for members in groups.values():

		mean_true = sum(true for true, _ in members) / len(members)
		mean_played = sum(played for _, played in members) / len(members)

		for true, played in members:
			centred.append((true - mean_true, played - mean_played))
			along += (true - mean_true) ** 2
			across += (true - mean_true) * (played - mean_played)

	left = len(points) - len(groups) - 1

	if left < 1 or not along:
		raise ValueError("Too few answers from the time servers to fit a line")

	slope = across / along
	squares = sum((played - slope * true) ** 2 for true, played in centred)
	error = (squares / left / along) ** 0.5

	return Fit((slope - 1) * 1000000, student(left) * error * 1000000, (squares / len(points)) ** 0.5 * 1000, len(points))


def student (freedom: int) -> float:

	"""Give how many standard errors either side hold the true figure 95 times in 100, for a line with this many degrees of freedom.

	Between the table's rows it takes the row before, which is a little wider
	than the truth, never narrower. Far past the last row it is 1.96.
	"""

	if freedom > 2 * STUDENT[-1][0]:
		return 1.96

	factor = STUDENT[0][1]

	for least, each in STUDENT:
		if freedom >= least:
			factor = each

	return factor


def points_of (run: Run) -> list[tuple[str, Sample, float, float]]:

	"""Pair each quickest answer from a time server with how long output 1 had played at that moment: its server's name, the answer, the true time and how long output 1 had played."""

	arrivals = [arrived for arrived, _ in run.reports]
	played = [seconds for _, seconds in run.reports]
	found = []

	for name, sample in prompt_enough(quickest(run.samples)):

		seconds = played_at(arrivals, played, sample.middle)

		if seconds is not None:
			found.append((name, sample, sample.middle + sample.offset, seconds))

	return found


def strayed (points: list[tuple[str, Sample, float, float]], window: float = WINDOW) -> float:

	"""Say how far output 1 strayed from network time, either way from its average over the run, in milliseconds.

	Each server is taken from its own average, since it may be out by a steady
	amount of its own, and the servers' answers in each window are averaged,
	which quiets the network's noise.
	"""

	ahead: dict[str, list[float]] = {}

	for _, sample, true, played in points:
		ahead.setdefault(sample.server, []).append(played - true)

	middle = {server: sum(each) / len(each) for server, each in ahead.items()}
	windows: dict[int, list[float]] = {}

	for _, sample, true, played in points:
		windows.setdefault(int(sample.middle // window), []).append(played - true - middle[sample.server])

	return max(abs(sum(each) / len(each)) for each in windows.values()) * 1000


def stretch_at (run: Run, moment: float) -> Stretch | None:

	"""Say which stretch a moment by the hardware clock fell in, once it had settled, or None if in none.

	A stretch settles for a minute, or a quarter of its length if that is
	shorter, so that a short run's stretches count too.
	"""

	settle = min(SETTLE, run.stretch_s / 4) if run.stretch_s else SETTLE

	for index, stretch in enumerate(run.stretches):

		ends = run.stretches[index + 1].began if index + 1 < len(run.stretches) else float("inf")

		if stretch.began + settle <= moment < ends:
			return stretch

	return None


def analyse (run: Run) -> dict[str, typing.Any]:

	"""Work out everything a run shows, as a result the README can quote."""

	if not run.reports:
		raise ValueError("The Pico made no reports")

	points = points_of(run)
	overall = fit([(sample.server, true, played) for _, sample, true, played in points])
	first_true = min(true for _, _, true, _ in points)

	servers: dict[str, dict[str, typing.Any]] = {}

	for name in sorted({name for name, _, _, _ in points}):

		try:
			servers[name] = fit([(sample.server, true, played) for each, sample, true, played in points if each == name])._asdict()
		except ValueError:
			continue

	# Each hour by its number from the start, with nothing measured for one
	# whose answers were too few, so that the hours after it keep their places.
	hours: list[dict[str, typing.Any]] = []

	for hour in range(int((max(true for _, _, true, _ in points) - first_true) // 3600) + 1):

		try:
			hours.append(dict(fit([(sample.server, true, played) for _, sample, true, played in points if int((true - first_true) // 3600) == hour])._asdict(), hour=hour))
		except ValueError:
			hours.append({"hour": hour, "ppm": None, "error": None, "spread_ms": None, "points": 0})

	tempos: list[dict[str, typing.Any]] = []
	cycles_per_tick = run.hz // 1000000

	for tempo in sorted({stretch.tempo for stretch in run.stretches}):

		judged = [each for each in run.timed if each.get("tempo") == tempo]
		mine: list[tuple[object, float, float]] = []

		for _, sample, true, played in points:

			stretch = stretch_at(run, sample.middle)

			if stretch is not None and stretch.tempo == tempo:
				mine.append(((sample.server, stretch.number), true, played))

		against: dict[str, typing.Any] | None

		try:
			against = fit(mine)._asdict()
		except ValueError:
			against = None

		tempos.append({
			"bpm": tempo / 100,
			"stretches": len(judged),
			"pulses": sum(each.get("pulses", 0) for each in judged),
			"worst_cycles": max((each.get("worst_cycles", 0) for each in judged), default=0),
			"furthest_drift_cycles": max((abs(each.get("drift_cycles", 0)) for each in judged), default=0),
			"against_network_time": against,
		})

	worst = max((each["worst_cycles"] for each in tempos), default=0)
	drift = max((each["furthest_drift_cycles"] for each in tempos), default=0)
	end = run.end or {}
	seconds = run.reports[-1][0] - run.reports[0][0]
	timed_well = not run.broke and worst <= WORST_ALLOWED and drift <= cycles_per_tick + DRIFT_SLACK and end.get("lost", 0) == 0 and end.get("faults", 0) == 0

	return {
		"hours": seconds / 3600,
		"pulses": sum(each["pulses"] for each in tempos),
		"every_pulse_on_time": timed_well and run.end is not None,
		"cut_short": run.end is None,
		"timed_well": timed_well,
		"worst_cycles": worst,
		"furthest_drift_cycles": drift,
		"cycles_per_microsecond": cycles_per_tick,
		"ppm": overall.ppm,
		"give_or_take_ppm": overall.error,
		"give_or_take_is": CONFIDENCE_IS,
		"broke": run.broke,
		"spread_ms": overall.spread_ms,
		"answers": overall.points,
		"bpm_at_120": 120 * (1 + overall.ppm / 1000000),
		"seconds_an_hour": 3600 * overall.ppm / 1000000,
		"seconds_a_day": 86400 * overall.ppm / 1000000,
		"strayed_ms": strayed(points),
		"gained_ms": seconds * overall.ppm / 1000,
		"gained_give_or_take_ms": seconds * overall.error / 1000,
		"servers": servers,
		"hour_by_hour": hours,
		"tempos": tempos,
		"player": end,
		"other_lines": run.other[:20],
	}


def describe (result: dict[str, typing.Any]) -> str:

	"""Say what a result shows, for someone reading it at the end of a run."""

	ppm = result["ppm"]
	lines = [
		"SynthSync's accuracy, measured at output 1 over %.1f hours against network time." % result["hours"],
		"",
	]

	player = result["player"]

	if result["broke"]:
		lines.append("The Pico stopped timing output 1 part way through (%s), so the pulses after that went unseen." % "; ".join(result["broke"]))

	if not result["timed_well"]:
		lines.append("NOT every pulse was on time: the furthest gap was %d cycles out, a stretch drifted by %d cycles, %d pulses went untimed and the player ran out of words %d times. See the table." % (result["worst_cycles"], result["furthest_drift_cycles"], player.get("lost", 0), player.get("faults", 0)))
	elif result["cut_short"]:
		lines.append("The run was cut short, before the Pico gave the player's own counts. Of the %d pulses timed, every one came exactly where its tempo puts it, to the microsecond, and no tempo drifted." % result["pulses"])
	else:
		lines.append("Every pulse was on time: all %d of them came exactly where their tempo puts them, to the microsecond, and no tempo drifted." % result["pulses"])

	lines.append("")
	lines.append("    BPM    stretches     pulses   furthest gap   drift   against network time")

	for tempo in result["tempos"]:

		against = tempo["against_network_time"]
		measured = "%+.2f ppm, give or take %.2f" % (against["ppm"], against["error"]) if against else "too few answers"
		lines.append("  %6.2f  %10d  %9d  %8d cyc  %4d cyc   %s" % (tempo["bpm"], tempo["stretches"], tempo["pulses"], tempo["worst_cycles"], tempo["furthest_drift_cycles"], measured))

	lines.append("")
	lines.append("Against network time it stayed within %.1f ms either way, and over the %.1f hours it %s %.1f ms, give or take %.1f." % (result["strayed_ms"], result["hours"], "gained" if result["gained_ms"] >= 0 else "lost", abs(result["gained_ms"]), result["gained_give_or_take_ms"]))
	lines.append("The tempo ran %s by %.2f parts per million, give or take %.2f, %s times (from %d answers)." % ("fast" if ppm >= 0 else "slow", abs(ppm), result["give_or_take_ppm"], result["give_or_take_is"], result["answers"]))
	lines.append("At 120 BPM that is %.5f BPM: %.1f ms in an hour, or %.2f s in a day." % (result["bpm_at_120"], abs(result["seconds_an_hour"]) * 1000, abs(result["seconds_a_day"])))
	lines.append("")
	# A short run can leave no one server with enough answers to say anything by itself.
	servers = "; ".join("%s %+.2f give or take %.2f" % (name, fitted["ppm"], fitted["error"]) for name, fitted in result["servers"].items())
	lines.append("Against each time server: " + (servers or "too few answers from any one of them"))
	lines.append("Hour by hour: " + ", ".join("too few answers" if hour["ppm"] is None else "%+.2f" % hour["ppm"] for hour in result["hour_by_hour"]))

	if player:
		lines.append("The player: " + " ".join("%s=%d" % (name, player[name]) for name in sorted(player)))

	if result["other_lines"]:
		lines.append("The Pico also said: " + " | ".join(result["other_lines"]))

	return "\n".join(lines)


def keep_result (log_path: pathlib.Path, result: dict[str, typing.Any]) -> pathlib.Path:

	"""Write the result beside the log, replacing any earlier one, and say where."""

	path = log_path.with_suffix(".json")
	fresh = path.with_name(path.name + ".new")

	with open(fresh, "x", encoding="utf-8") as handle:
		json.dump(result, handle, indent=1)

	os.replace(fresh, path)

	return path


# ---------------------------------------------------------------------------
# Running it.


def stretch_for (seconds: float, tempos: int) -> int:

	"""Choose each stretch's length, so that a run goes through every tempo at least twice."""

	return int(max(SHORTEST_STRETCH, min(LONGEST_STRETCH, seconds / (2 * tempos))))


def board_script (tempos: tuple[int, ...], stretch: int, total: int) -> str:

	"""Give the Pico's half, ready to run for this long."""

	return BOARD_HALF.read_text(encoding="utf-8") + "\nmeasure(%r, %d, %d, %d, %d, 1)\n" % (tuple(tempos), stretch, EVERY, total, PPQN)


def find_device () -> str:

	"""Find the Pico's serial port when only one is plugged in, or leave mpremote to find it."""

	found = sorted(glob.glob("/dev/serial/by-id/usb-MicroPython*"))

	return found[0] if len(found) == 1 else "auto"


def restart (device: str) -> bool:

	"""Restart the Pico, so that SynthSync starts again, and say whether it worked."""

	for _ in range(RESTART_TRIES):

		try:
			if subprocess.run(MPREMOTE + ["connect", device, "reset"], capture_output=True, timeout=60).returncode == 0:
				return True

		except subprocess.TimeoutExpired:
			pass

		time.sleep(1)

	return False


def say_how_it_goes (log: Log, began: float) -> None:

	"""Print how the run is going so far."""

	run = read_log(list(log.lines))
	hours = (raw_clock() - began) / 3600

	try:
		result = analyse(run)
	except ValueError:
		print("  after %.1f hours: %d reports, too few answers from the time servers yet." % (hours, len(run.reports)), flush=True)
		return

	print("  after %.1f hours: %d pulses timed%s, and the tempo %+.2f ppm, give or take %.2f, so far." % (hours, result["pulses"], "" if result["worst_cycles"] <= WORST_ALLOWED else " (SOME OFF TIME)", result["ppm"], result["give_or_take_ppm"]), flush=True)


def measure (device: str, log: Log, tempos: tuple[int, ...], total: int, stretch: int, servers: dict[str, str | None]) -> int:

	"""Run the Pico's half and ask the time servers meanwhile, logging everything, and give mpremote's exit code.

	Each line the Pico prints is noted the moment it arrives, by a thread that
	does nothing else, so that working out how the run is going never makes
	an arrival look late. A Pico that says nothing for `QUIET_FOR` seconds
	has stalled, and the run is ended.
	"""

	stop = threading.Event()
	asking = threading.Thread(target=keep_asking, args=(servers, log, stop), daemon=True)
	asking.start()

	environment = dict(os.environ, PYTHONUNBUFFERED="1")
	began = raw_clock()
	said_at = began
	heard = [began]

	with tempfile.TemporaryDirectory(prefix="synthsync-accuracy-") as scratch:

		script = pathlib.Path(scratch) / "accuracy.py"
		script.write_text(board_script(tempos, stretch, total), encoding="utf-8")
		command = MPREMOTE + ["connect", device, "soft-reset", "run", str(script)]

		# A byte spoiled on the way is read as a mark rather than ending the run.
		with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, env=environment) as running:

			assert running.stdout is not None
			said = running.stdout

			def listen () -> None:

				"""Note each line the Pico prints as it arrives, until it has finished."""

				for line in said:
					heard[0] = raw_clock()
					log.pico(heard[0], line.rstrip("\r\n"))

			listening = threading.Thread(target=listen, daemon=True)
			listening.start()

			try:

				while listening.is_alive():

					listening.join(1.0)
					now = raw_clock()

					if now - heard[0] > QUIET_FOR:
						log.note("the Pico said nothing for %d s, so the run was ended" % QUIET_FOR)
						print("The Pico has said nothing for %d seconds, so the run is ended." % QUIET_FOR, flush=True)
						break

					if now - said_at >= PROGRESS_EVERY:
						say_how_it_goes(log, began)
						said_at = raw_clock()

			finally:

				if running.poll() is None:
					running.terminate()

				stop.set()

	return running.returncode


def tempos_from (text: str) -> tuple[int, ...]:

	"""Read the BPMs asked for, with commas between, into hundredths of a BPM, refusing any SynthSync can't play."""

	tempos = []

	for typed in text.split(","):

		try:
			bpm = float(typed)
		except ValueError:
			raise ValueError("%r isn't a BPM" % typed.strip()) from None

		hundredths = round(bpm * 100)

		if abs(bpm * 100 - hundredths) > 1e-6:
			raise ValueError("%s BPM has more than two decimal places" % typed.strip())

		if not 2000 <= hundredths <= 30000:
			raise ValueError("%s BPM is outside 20 to 300 BPM, which is all SynthSync plays" % typed.strip())

		tempos.append(hundredths)

	return tuple(tempos)


def main (arguments: list[str] | None = None) -> int:

	"""Measure, or work out a log from before, and say what it shows."""

	parser = argparse.ArgumentParser(description="Measure how close SynthSync's tempo is to true, at its output, against network time.")
	parser.add_argument("--hours", type=float, default=12.0, help="how long to measure for; twelve hours gives the tempo to a few hundredths of a part per million")
	parser.add_argument("--log", type=pathlib.Path, help="where to write the log; a new file named for the time, here, if left out")
	parser.add_argument("--device", help="the Pico's serial port; found automatically when only one is plugged in")
	parser.add_argument("--tempos", default=",".join("%g" % (tempo / 100) for tempo in TEMPOS), help="the BPMs to measure at, with commas between")
	parser.add_argument("--servers", default=",".join(SERVERS), help="the time servers to ask, with commas between")
	parser.add_argument("--analyse", type=pathlib.Path, metavar="LOG", help="work out a log from an earlier run instead of measuring")
	options = parser.parse_args(arguments)

	if options.analyse is not None:

		with open(options.analyse, encoding="utf-8") as handle:
			result = analyse(read_log(handle))

		print(describe(result))
		print("The result is in %s." % keep_result(options.analyse, result))

		return 0 if result["every_pulse_on_time"] else 1

	if importlib.util.find_spec("mpremote") is None:
		print("This needs mpremote, which talks to the Pico over USB. Install it with: %s -m pip install mpremote" % pathlib.Path(sys.executable).name)
		return 1

	try:
		tempos = tempos_from(options.tempos)
	except ValueError as problem:
		parser.error(str(problem))

	if not 0 < options.hours <= LONGEST_RUN:
		parser.error("a run lasts more than nothing and at most %g hours" % LONGEST_RUN)

	names = [name.strip() for name in options.servers.split(",") if name.strip()]
	total = int(options.hours * 3600)
	stretch = stretch_for(total, len(tempos))
	device = options.device or find_device()
	path = options.log or pathlib.Path(time.strftime("accuracy-%Y-%m-%d-%H%M.log"))

	# Before SynthSync is stopped, a time server must be answering, or the night would be wasted.
	print("Asking the time servers...", flush=True)
	servers = find_servers(names)

	if not servers:
		print("None of the time servers answered. Check that this computer is on the internet, and try again.")
		return 1

	log = Log(path)
	log.note("SynthSync accuracy, begun %s UTC, for %g hours, stretches of %d s, on %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), options.hours, stretch, device))

	for name in names:
		log.note("time server %s is at %s" % (name, servers[name]) if name in servers else "time server %s didn't answer, so it isn't asked" % name)

	print("Measuring SynthSync's accuracy at output 1 for %g hours, at %s BPM in turn, %d minutes each." % (options.hours, options.tempos, stretch // 60), flush=True)
	print("SynthSync is stopped meanwhile, and the outputs play the tempos being measured. The log is %s." % path, flush=True)

	# A stop asked for from outside ends the run as one that finished does, with
	# the Pico restarted, and what it measured so far is worked out.
	signal.signal(signal.SIGTERM, lambda number, frame: sys.exit(1))

	ended = 0

	try:
		ended = measure(device, log, tempos, total, stretch, servers)

	except (KeyboardInterrupt, SystemExit):
		log.note("stopped early")
		print("Stopped early.", flush=True)

	finally:
		restarted = restart(device)
		log.note("ended %s UTC, and the Pico was %s" % (time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()), "restarted" if restarted else "NOT restarted"))
		log.close()
		print("Restarted the Pico, which starts SynthSync again." if restarted else "The Pico didn't answer when asked to restart. Unplug it and plug it in again to start SynthSync.", flush=True)

	run = read_log(log.lines)

	if any("synthsync" in line and "Error" in line for line in run.other):
		print("The Pico couldn't find SynthSync. Copy SynthSync onto it first, as the README's steps say, then try again.")
		return 1

	try:
		result = analyse(run)

	except ValueError as problem:
		print("Nothing could be worked out: %s." % problem)

		# mpremote says what kept it from the Pico, such as the port being in use.
		if not run.reports and run.other:
			print("mpremote ended with code %s, saying: %s" % (ended, " | ".join(run.other[-5:])))

		return 1

	print(describe(result))
	print("The result is in %s." % keep_result(path, result))

	return 0 if result["every_pulse_on_time"] else 1


if __name__ == "__main__":
	sys.exit(main())
