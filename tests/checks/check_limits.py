"""A tempo range from `config.txt` reaches everything that cares about it, on either Python."""

import json

import synthsync.core.clock
import synthsync.core.config
import synthsync.core.default_config
import synthsync.core.limits
import synthsync.core.ratio
import synthsync.core.settings
import synthsync.core.timeline
import synthsync.web.app

import tests.checks.check_app
import tests.checks.check_clock
import tests.checks.check_timeline

WIDE = synthsync.core.limits.Limits(100, 100000)
NARROW = synthsync.core.limits.Limits(10000, 11000)
UNITS = tests.checks.check_timeline.UNITS


def refused (change: "object", words: str) -> bool:

	"""Say whether a change is refused with a message holding these words."""

	try:
		change()  # type: ignore[operator]

	except ValueError as problem:
		return words in str(problem)

	return False


def check_the_usual_range_allows_what_it_always_has () -> None:

	"""Nothing changes for anyone who leaves the range alone."""

	limits = synthsync.core.limits.DEFAULT

	assert (limits.slowest_tempo, limits.fastest_tempo) == (2000, 30000)
	assert (limits.most_beats, limits.most_a_beat, limits.largest_part) == (128, 96, 9600)
	assert limits.tempo_step == 100
	assert synthsync.core.ratio.ppqns(limits) == synthsync.core.ratio.PPQNS
	assert synthsync.core.ratio.slowest(4) == (1, 128)
	assert synthsync.core.ratio.fastest(4) == (24, 1)


def check_a_narrow_range_keeps_every_ratio_and_gets_finer_steps () -> None:

	"""100 to 110 BPM asks nothing more of the timing, and its controls move in tenths of a BPM."""

	assert (NARROW.most_beats, NARROW.most_a_beat, NARROW.largest_part) == (128, 96, 9600)
	assert NARROW.tempo_step == 10
	assert synthsync.core.limits.Limits(10000, 10010).tempo_step == 1
	assert NARROW.tempo_in(9000) == 10000
	assert NARROW.tempo_in(12000) == 11000
	assert NARROW.tempo_in(10500) == 10500


def check_a_wide_range_narrows_the_ratios_to_todays_extremes () -> None:

	"""At 1 BPM no gap passes 384 seconds, and at 1000 BPM no output passes 480 pulses a second."""

	assert WIDE.most_beats == 6
	assert WIDE.most_a_beat == 28
	assert WIDE.tempo_step == 100
	assert 3 * WIDE.largest_part * WIDE.fastest_tempo < 2 ** 30
	assert WIDE.most_beats * UNITS // WIDE.slowest_tempo <= 384000000
	assert WIDE.most_a_beat * WIDE.fastest_tempo // 6000 <= 480

	assert synthsync.core.ratio.ppqns(WIDE) == (1, 2, 4, 8, 12, 16, 24)
	assert synthsync.core.ratio.fastest(4, WIDE) == (7, 1)
	assert synthsync.core.ratio.slowest(4, WIDE) == (1, 24)
	assert synthsync.core.ratio.slowest(1, WIDE) == (1, 6)
	assert synthsync.core.ratio.slowest(48, synthsync.core.limits.Limits(100, 30000)) == (1, 128)


def check_a_range_has_to_be_one_the_clock_can_play () -> None:

	"""Backwards, empty or too wide is refused."""

	for slowest, fastest in ((5000, 5000), (6000, 5000), (99, 30000), (2000, 100001)):
		assert refused(lambda: synthsync.core.limits.Limits(slowest, fastest), "tempo range")


def check_ratios_are_held_to_the_range () -> None:

	"""What can be typed, and what a knob stops on, keep to the range."""

	assert synthsync.core.ratio.parse("7", 4, WIDE) == (7, 1)
	assert refused(lambda: synthsync.core.ratio.parse("8", 4, WIDE), "fastest ratio is ×7")
	assert synthsync.core.ratio.parse("÷24", 4, WIDE) == (1, 24)
	assert refused(lambda: synthsync.core.ratio.parse("÷25", 4, WIDE), "slowest ratio is ÷24")

	found = synthsync.core.ratio.snaps(4, WIDE)

	assert found[-1] == (7, 1)
	assert synthsync.core.ratio.compare(found[0], (1, 24)) >= 0
	assert found == sorted(found, key=lambda each: each[0] / each[1])


def check_a_ratio_is_fitted_to_the_range () -> None:

	"""One out of reach goes to the nearest there is, and one within it stays."""

	assert synthsync.core.ratio.fit((24, 1), 4, WIDE) == (7, 1)
	assert synthsync.core.ratio.fit((1, 128), 4, WIDE) == (1, 24)
	assert synthsync.core.ratio.fit((3, 2), 4, WIDE) == (3, 2)
	assert synthsync.core.ratio.fit((3, 2), 4) == (3, 2)
	assert synthsync.core.ratio.compare(synthsync.core.ratio.fit((4001, 4000), 1, WIDE), (1, 1)) == 0


def check_the_scheduler_keeps_to_its_range () -> None:

	"""It refuses what the range doesn't allow, in its own right."""

	usual = synthsync.core.timeline.Timeline(1)

	assert refused(lambda: usual.set_tempo(1999), "range")
	assert refused(lambda: usual.set_tempo(30001), "range")

	timeline = synthsync.core.timeline.Timeline(1, limits=WIDE)

	timeline.set_tempo(100)
	timeline.set_tempo(100000)
	timeline.set_ratio(0, 28, 1)
	timeline.set_ratio(0, 1, 6)

	assert refused(lambda: timeline.set_ratio(0, 29, 1), "between")
	assert refused(lambda: timeline.set_ratio(0, 1, 7), "between")
	assert refused(lambda: timeline.set_ratio(0, 3580, 3579), "whole numbers")


def timeline_at (ratios: "list[tuple[int, int]]", tempo: int) -> synthsync.core.timeline.Timeline:

	"""Make a timeline with the widest range, at this tempo with these ratios, started."""

	timeline = synthsync.core.timeline.Timeline(len(ratios), limits=WIDE)
	timeline.set_tempo(tempo)

	for output in range(len(ratios)):
		timeline.set_ratio(output, ratios[output][0], ratios[output][1])

	timeline.start()

	return timeline


def check_every_pulse_is_exact_at_both_ends_of_the_widest_range () -> None:

	"""At 1 and at 1000 BPM, each pulse rises on exactly the tick the maths gives."""

	for tempo, ratios in ((100000, [(28, 1), (1, 6), (7, 3)]), (100, [(28, 1), (7, 3), (1, 1)])):

		timeline = timeline_at(ratios, tempo)
		player = tests.checks.check_timeline.Player(timeline)

		# Long enough for the slowest of them to pulse a second time.
		player.play_until(max(beats * UNITS // (pulses * tempo) for pulses, beats in ratios) + 1)

		for output in range(len(ratios)):

			pulses, beats = ratios[output]
			rises = player.rises[output]

			assert len(rises) > 1, (tempo, output)

			for number in range(len(rises)):
				assert rises[number] == number * beats * UNITS // (pulses * tempo), (tempo, output, number)


def check_every_output_keeps_its_place_across_the_whole_range () -> None:

	"""A jump from 1000 BPM to 1 and back moves no output off its place, the longest gap there is included."""

	timeline = timeline_at([(28, 1), (1, 6), (7, 3)], 100000)
	player = tests.checks.check_timeline.Player(timeline)

	player.play(400)
	timeline.set_tempo(100)
	player.play(40)

	assert max(timeline.period) == 6 * UNITS // 100
	assert max(timeline.until) < 2 ** 30

	timeline.set_tempo(100000)
	player.play(400)

	for output, (pulses, beats) in enumerate(((28, 1), (1, 6), (7, 3))):

		for position in player.rise_positions[output]:

			# Each rise is within a tick of a place on the output's grid, either side of it.
			over = (position * pulses) % (beats * UNITS)
			assert min(over, beats * UNITS - over) // pulses <= 100000, (output, position)


def check_settings_keep_to_the_range () -> None:

	"""The tempo is held to the range, with the range in the message."""

	settings = synthsync.core.settings.Settings(2, NARROW)

	assert settings.tempo == 11000
	assert refused(lambda: settings.set_tempo(9999), "between 100 and 110 BPM")
	assert refused(lambda: synthsync.core.settings.Settings(2).set_tempo(1999), "between 20 and 300 BPM")

	settings.set_tempo(10525)

	assert settings.tempo == 10525

	wide = synthsync.core.settings.Settings(2, WIDE)

	wide.set_tempo(100)
	wide.set_tempo(100000)

	assert refused(lambda: wide.set_ppqn(48), "24 pulses a beat")
	assert refused(lambda: wide.set_output(1, "ratio", "8"), "fastest ratio is ×7")


def check_a_new_clock_starts_inside_the_range () -> None:

	"""120 BPM may be outside it, and so may 4 PPQN be too many pulses."""

	assert synthsync.core.settings.Settings(1, synthsync.core.limits.Limits(13000, 14000)).tempo == 13000
	assert synthsync.core.settings.Settings(1, synthsync.core.limits.Limits(2000, 3000)).tempo == 3000

	settings = synthsync.core.settings.Settings(1, NARROW)

	settings.set_tempo(10000)
	settings.reset()

	assert settings.tempo == 11000


def check_saved_settings_outside_the_range_go_to_the_nearest () -> None:

	"""Changing the range never counts as damage: what was saved plays as near as it can."""

	before = synthsync.core.settings.Settings(3)

	before.set_tempo(29000)
	before.set_ppqn(48)
	before.set_output(1, "ratio", "2")
	before.set_output(2, "ratio", "1/128")

	narrow = synthsync.core.settings.Settings(3, NARROW)

	assert narrow.load_text(before.to_text()) == []
	assert narrow.tempo == 11000

	wide = synthsync.core.settings.Settings(3, WIDE)

	assert wide.load_text(before.to_text()) == []
	assert wide.tempo == 29000
	assert wide.ppqn == 24
	assert wide.outputs[1].ratio == (1, 1)
	assert wide.outputs[2].ratio == (1, 128)

	# A tempo no range could hold is still damage.
	again = synthsync.core.settings.Settings(1)
	saved = json.loads(before.to_text())

	saved["tempo"] = 100001

	assert len(again.load_text(json.dumps(saved))) > 0
	assert again.tempo == 12000


def check_changing_the_ppqn_keeps_outputs_inside_the_range () -> None:

	"""An output left waiting too long by a lower PPQN goes to the slowest there is, and is named."""

	settings = synthsync.core.settings.Settings(2, WIDE)

	settings.set_output(1, "ratio", "1/24")

	assert settings.set_ppqn(1) == [1]
	assert settings.outputs[1].ratio == (1, 6)


def check_where_the_knob_was_is_saved () -> None:

	"""It comes back from the file, a reset forgets it, and an older file has none."""

	settings = synthsync.core.settings.Settings(1)

	assert settings.knob_at == synthsync.core.settings.NO_KNOB

	settings.set_knob_at(2048)

	again = synthsync.core.settings.Settings(1)

	assert again.load_text(settings.to_text()) == []
	assert again.knob_at == 2048

	again.reset()

	assert again.knob_at == synthsync.core.settings.NO_KNOB

	saved = json.loads(settings.to_text())

	del saved["knob_at"]

	assert again.load_text(json.dumps(saved)) == []
	assert again.knob_at == synthsync.core.settings.NO_KNOB

	for wrong in (-2, 4096, "middle", True):
		assert refused(lambda: settings.set_knob_at(wrong), "knob")


def check_the_page_is_told_the_range () -> None:

	"""What a page draws its controls from comes from the range."""

	settings = synthsync.core.settings.Settings(2, WIDE)
	clock = synthsync.core.clock.Clock(settings, tests.checks.check_clock.Engine(), tests.checks.check_clock.Storage())
	app = synthsync.web.app.App(clock, tests.checks.check_app.Network())
	fixed = app._describe()

	assert (fixed["slowest_tempo"], fixed["fastest_tempo"], fixed["tempo_step"]) == (100, 100000, 100)
	assert fixed["ppqns"] == [1, 2, 4, 8, 12, 16, 24]

	message = app._message(synthsync.web.app.PPQN)

	assert (message["fastest_ratio"], message["slowest_ratio"]) == ("7", "1/24")

	narrow = synthsync.core.clock.Clock(synthsync.core.settings.Settings(2, NARROW), tests.checks.check_clock.Engine(), tests.checks.check_clock.Storage())

	assert synthsync.web.app.App(narrow, tests.checks.check_app.Network())._describe()["tempo_step"] == 10


def check_the_clock_notes_where_the_knob_is_and_tells_nobody () -> None:

	"""It is saved with the settings, and nothing that plays changes."""

	clock, engine, storage = tests.checks.check_clock.make(1)
	told = []

	clock.watch(lambda what, by: told.append(what))
	before = storage.changes

	clock.set_knob_at(1234)

	assert clock.settings.knob_at == 1234
	assert storage.changes == before + 1
	assert told == []


def check_config_txt_gives_the_range () -> None:

	"""Both limits are read, with decimals, and each can be given alone."""

	config = synthsync.core.config.parse(b"min_bpm = 1\nmax_bpm = 1000\n")

	assert config.problems == []
	assert (config.limits().slowest_tempo, config.limits().fastest_tempo) == (100, 100000)

	config = synthsync.core.config.parse(b"MIN_BPM = 100.5\nmax_bpm=110.25 # close\n")

	assert config.problems == []
	assert (config.slowest_tempo, config.fastest_tempo) == (10050, 11025)

	config = synthsync.core.config.parse(b"max_bpm = 500\n")

	assert config.problems == []
	assert (config.slowest_tempo, config.fastest_tempo) == (2000, 50000)


def check_config_txt_says_what_is_wrong_with_a_range () -> None:

	"""A limit that can't be used is named, and the usual one stands."""

	for line in (b"min_bpm = 0.5", b"max_bpm = 1001", b"min_bpm = fast", b"max_bpm = ", b"min_bpm = 20.123"):

		config = synthsync.core.config.parse(line + b"\n")

		assert (config.slowest_tempo, config.fastest_tempo) == (2000, 30000), line
		assert len(config.problems) == 1, line
		assert "from 1 to 1000" in config.problems[0], line

	for text in (b"min_bpm = 200\nmax_bpm = 100\n", b"min_bpm = 120\nmax_bpm = 120\n", b"min_bpm = 400\n", b"max_bpm = 10\n"):

		config = synthsync.core.config.parse(text)

		assert (config.slowest_tempo, config.fastest_tempo) == (2000, 30000), text
		assert config.problems == ["config.txt: min_bpm has to be below max_bpm, so the range is 20 to 300 BPM"], text


def check_the_file_synthsync_makes_leaves_the_usual_range () -> None:

	"""It shows how to set a range, and doesn't set one."""

	config = synthsync.core.config.parse(synthsync.core.default_config.TEXT)

	assert config.problems == []
	assert (config.slowest_tempo, config.fastest_tempo) == (2000, 30000)
	assert b"# min_bpm = 20\n" in synthsync.core.default_config.TEXT
	assert b"# max_bpm = 300\n" in synthsync.core.default_config.TEXT
