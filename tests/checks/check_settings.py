"""Settings hold, check, save and load the same on the Pico's Python as on a laptop's."""

import json

import synthsync.core.ratio
import synthsync.core.settings
import synthsync.core.timeline


def refusal (settings: synthsync.core.settings.Settings, what: str, first: object, second: object = None, third: object = None) -> str:

	"""Try one change and give the message it was refused with, or nothing if it was taken."""

	try:
		if what == "tempo":
			settings.set_tempo(first)
		elif what == "output":
			settings.set_output(first, str(second), third)
		elif what == "password":
			settings.set_own_password(first)
		elif what == "host":
			settings.set_host_name(first)
		else:
			settings.set_network(first, second)

	except ValueError as error:
		return str(error)

	return ""


def tempo_refused (text: str) -> bool:

	"""Say whether a typed tempo is refused."""

	try:
		synthsync.core.settings.parse_tempo(text)
	except ValueError:
		return True

	return False


def check_the_defaults () -> None:

	"""A new set of settings is 120 BPM, every output a clock at one pulse a beat, and the Pico's own network."""

	settings = synthsync.core.settings.Settings(4)

	assert settings.tempo == 12000
	assert len(settings.outputs) == 4

	for output in settings.outputs:
		assert output.ratio == (1, 1)
		assert output.snap is True
		assert output.muted is False
		assert output.pulse == 10
		assert output.role == "clock"

	assert settings.own_password == "synthsync"
	assert settings.host_name == "synthsync"
	assert settings.network_name == ""
	assert settings.network_password == ""


def check_a_tempo_is_typed_in_bpm_and_held_in_hundredths () -> None:

	"""A typed tempo becomes exact hundredths of a BPM, and writes back the way it was typed."""

	parse = synthsync.core.settings.parse_tempo
	text = synthsync.core.settings.tempo_text

	assert parse("120") == 12000
	assert parse(" 120.5 ") == 12050
	assert parse("123.45") == 12345
	assert parse("99.") == 9900
	assert parse("0.07") == 7

	assert text(12000) == "120"
	assert text(12050) == "120.5"
	assert text(12345) == "123.45"
	assert text(2007) == "20.07"

	for bad in ("", ".", "abc", "1.2.3", "120.123", "-120", "12e1", "12345"):
		assert tempo_refused(bad), bad


def check_tempo_limits () -> None:

	"""The tempo runs from 20 to 300 BPM and nothing else is taken."""

	settings = synthsync.core.settings.Settings(2)

	settings.set_tempo(2000)
	settings.set_tempo(30000)

	assert settings.tempo == 30000
	assert "between 20 and 300" in refusal(settings, "tempo", 1999)
	assert "between 20 and 300" in refusal(settings, "tempo", 30001)
	assert refusal(settings, "tempo", "12000")
	assert refusal(settings, "tempo", True)
	assert settings.tempo == 30000


def check_each_output_setting_can_be_changed () -> None:

	"""Ratio, snap, mute, pulse and role each take a good value and keep it."""

	settings = synthsync.core.settings.Settings(3)

	settings.set_output(1, "ratio", "4/3")
	settings.set_output(1, "snap", False)
	settings.set_output(1, "muted", True)
	settings.set_output(1, "pulse", 25)
	settings.set_output(1, "role", "run")
	settings.set_output(2, "pulse", "half")
	settings.set_output(2, "ratio", "÷1.5")

	assert settings.outputs[1].ratio == (4, 3)
	assert settings.outputs[1].snap is False
	assert settings.outputs[1].muted is True
	assert settings.outputs[1].pulse == 25
	assert settings.outputs[1].role == "run"
	assert settings.outputs[2].pulse == synthsync.core.settings.HALF
	assert settings.outputs[2].ratio == (2, 3)

	# The other outputs are untouched.
	assert settings.outputs[0].ratio == (1, 1)
	assert settings.outputs[0].muted is False


def check_bad_output_settings_are_refused_with_a_reason () -> None:

	"""A value that makes no sense is refused, says why, and changes nothing."""

	settings = synthsync.core.settings.Settings(2)

	assert "fastest" in refusal(settings, "output", 1, "ratio", "97")
	assert refusal(settings, "output", 1, "ratio", 2)
	assert "on or off" in refusal(settings, "output", 1, "snap", "yes")
	assert "on or off" in refusal(settings, "output", 1, "muted", 1)
	assert "milliseconds" in refusal(settings, "output", 1, "pulse", 0)
	assert "milliseconds" in refusal(settings, "output", 1, "pulse", 101)
	assert "milliseconds" in refusal(settings, "output", 1, "pulse", "long")
	assert refusal(settings, "output", 1, "pulse", True)
	assert "clock" in refusal(settings, "output", 1, "role", "gate")
	assert refusal(settings, "output", 1, "colour", "red")
	assert "no such output" in refusal(settings, "output", 2, "muted", True)
	assert "no such output" in refusal(settings, "output", -1, "muted", True)
	assert refusal(settings, "output", "1", "muted", True)

	assert settings.outputs[1].ratio == (1, 1)
	assert settings.outputs[1].pulse == 10
	assert settings.outputs[1].role == "clock"


def check_output_one_is_always_the_main_clock () -> None:

	"""The first output keeps its ratio and its role, and can still be muted or given another pulse."""

	settings = synthsync.core.settings.Settings(2)

	assert "main clock" in refusal(settings, "output", 0, "ratio", "2")
	assert "main clock" in refusal(settings, "output", 0, "role", "run")
	assert not refusal(settings, "output", 0, "ratio", "1")
	assert not refusal(settings, "output", 0, "role", "clock")
	assert not refusal(settings, "output", 0, "muted", True)
	assert not refusal(settings, "output", 0, "pulse", "half")
	assert not refusal(settings, "output", 0, "snap", False)

	assert settings.outputs[0].ratio == (1, 1)
	assert settings.outputs[0].role == "clock"
	assert settings.outputs[0].muted is True


def check_an_output_gives_the_scheduler_what_it_takes () -> None:

	"""A pulse in milliseconds becomes ticks, half stays as nought, and a role becomes its number."""

	settings = synthsync.core.settings.Settings(2)

	assert settings.outputs[1].width_ticks() == 10000
	assert settings.outputs[1].role_number() == synthsync.core.timeline.CLOCK

	settings.set_output(1, "pulse", "half")
	settings.set_output(1, "role", "stop")

	assert settings.outputs[1].width_ticks() == 0
	assert settings.outputs[1].role_number() == synthsync.core.timeline.STOP_PULSE

	settings.set_output(1, "role", "start")
	assert settings.outputs[1].role_number() == synthsync.core.timeline.START_PULSE

	settings.set_output(1, "role", "run")
	assert settings.outputs[1].role_number() == synthsync.core.timeline.RUN


def check_network_settings_are_checked () -> None:

	"""Passwords and names that a WiFi network couldn't use are refused."""

	settings = synthsync.core.settings.Settings(1)

	settings.set_own_password("a better one")
	settings.set_host_name(" Studio-Clock ")
	settings.set_network("Home WiFi", "correct horse")

	assert settings.own_password == "a better one"
	assert settings.host_name == "studio-clock"
	assert settings.network_name == "Home WiFi"
	assert settings.network_password == "correct horse"

	assert "between 8 and 63" in refusal(settings, "password", "short")
	assert refusal(settings, "password", "x" * 64)
	assert refusal(settings, "password", "pass\tword")
	assert refusal(settings, "password", 12345678)
	assert refusal(settings, "host", "")
	assert refusal(settings, "host", "-clock")
	assert refusal(settings, "host", "clock-")
	assert refusal(settings, "host", "my clock")
	assert refusal(settings, "host", "x" * 33)
	assert refusal(settings, "network", "", "password1")
	assert refusal(settings, "network", "x" * 33, "password1")
	assert refusal(settings, "network", "Home", "short")
	assert not refusal(settings, "network", "Open Cafe", "")

	settings.forget_network()

	assert settings.network_name == ""
	assert settings.network_password == ""
	assert settings.own_password == "a better one"


def check_saving_and_loading_gives_back_the_same_settings () -> None:

	"""Everything set survives being written to text and read back, with nothing reported."""

	settings = synthsync.core.settings.Settings(4)
	settings.set_tempo(12345)
	settings.set_output(0, "muted", True)
	settings.set_output(1, "ratio", "137/100")
	settings.set_output(1, "snap", False)
	settings.set_output(2, "pulse", "half")
	settings.set_output(2, "role", "start")
	settings.set_output(3, "pulse", 3)
	settings.set_output(3, "role", "run")
	settings.set_own_password("another password")
	settings.set_host_name("studio")
	settings.set_network("Home WiFi", "correct horse")

	text = settings.to_text()
	loaded = synthsync.core.settings.Settings(4)
	problems = loaded.load_text(text)

	assert problems == []
	assert loaded.tempo == 12345
	assert loaded.outputs[0].muted is True
	assert loaded.outputs[1].ratio == (137, 100)
	assert loaded.outputs[1].snap is False
	assert loaded.outputs[2].pulse == synthsync.core.settings.HALF
	assert loaded.outputs[2].role == "start"
	assert loaded.outputs[3].pulse == 3
	assert loaded.outputs[3].role == "run"
	assert loaded.own_password == "another password"
	assert loaded.host_name == "studio"
	assert loaded.network_name == "Home WiFi"
	assert loaded.network_password == "correct horse"

	# What's written has no decimals in it, so it reads back exactly on any Python.
	assert json.loads(text)["tempo"] == 12345
	assert "." not in text.replace("137/100", "")


def check_a_missing_or_damaged_file_gives_the_defaults () -> None:

	"""Text that isn't settings at all leaves the defaults in place, and says so."""

	for text in ("", "not json", "{", "[1, 2, 3]", "null", "42", "\"text\""):

		settings = synthsync.core.settings.Settings(2)
		settings.set_tempo(9000)
		problems = settings.load_text(text)

		assert len(problems) == 1, text
		assert settings.tempo == 12000
		assert settings.outputs[1].ratio == (1, 1)
		assert settings.own_password == "synthsync"


def check_a_partly_damaged_file_keeps_what_it_can () -> None:

	"""One bad value doesn't cost the good ones: each is taken or defaulted alone."""

	text = json.dumps({
		"version": 1,
		"tempo": 99999,
		"outputs": [
			{"ratio": "2", "muted": True},
			{"ratio": "3/2", "snap": "sometimes", "pulse": 500, "role": "run"},
			"nonsense",
			{"ratio": "÷4", "role": "stop"},
		],
		"own_password": "x",
		"host_name": "good-name",
		"network_name": "Home WiFi",
		"network_password": "correct horse",
		"something_new": [1, 2, 3],
	})

	settings = synthsync.core.settings.Settings(4)
	problems = settings.load_text(text)

	# The tempo, output 1's ratio, output 2's snap and pulse, output 3 and the password.
	assert len(problems) == 6, problems

	assert settings.tempo == 12000
	assert settings.outputs[0].ratio == (1, 1)
	assert settings.outputs[0].muted is True
	assert settings.outputs[1].ratio == (3, 2)
	assert settings.outputs[1].snap is True
	assert settings.outputs[1].pulse == 10
	assert settings.outputs[1].role == "run"
	assert settings.outputs[2].ratio == (1, 1)
	assert settings.outputs[3].ratio == (1, 4)
	assert settings.outputs[3].role == "stop"
	assert settings.own_password == "synthsync"
	assert settings.host_name == "good-name"
	assert settings.network_name == "Home WiFi"


def check_a_ratio_saved_with_snap_on_loads_as_it_was_saved () -> None:

	"""A ratio typed with snap on is saved as typed, and loads as typed, even into settings whose snap was off: loading starts afresh, with snap on, so nothing turns it on."""

	settings = synthsync.core.settings.Settings(4)
	settings.set_output(1, "ratio", "29/10")

	text = settings.to_text()
	loaded = synthsync.core.settings.Settings(4)

	loaded.set_output(1, "snap", False)

	assert loaded.load_text(text) == []
	assert loaded.outputs[1].snap is True
	assert loaded.outputs[1].ratio == (29, 10)


def check_turning_snap_on_says_the_ratio_changed_only_when_it_did () -> None:

	"""The settings give the names of what a change changed: the ratio too when turning snap on moved it."""

	settings = synthsync.core.settings.Settings(4)

	assert settings.set_output(1, "snap", False) == ("snap",)
	assert settings.set_output(1, "ratio", "2.9") == ("ratio",)
	assert settings.set_output(1, "snap", True) == ("snap", "ratio")
	assert settings.outputs[1].ratio == (3, 1)

	settings.set_output(1, "snap", False)

	assert settings.set_output(1, "snap", True) == ("snap",)


def check_a_file_for_more_or_fewer_outputs_still_loads () -> None:

	"""Outputs the file doesn't mention keep their defaults, and ones this Pico lacks are ignored."""

	saved = synthsync.core.settings.Settings(4)
	saved.set_output(1, "ratio", "2")
	saved.set_output(3, "ratio", "8")

	fewer = synthsync.core.settings.Settings(2)

	assert fewer.load_text(saved.to_text()) == []
	assert fewer.outputs[1].ratio == (2, 1)

	more = synthsync.core.settings.Settings(6)

	assert more.load_text(saved.to_text()) == []
	assert more.outputs[3].ratio == (8, 1)
	assert more.outputs[5].ratio == (1, 1)


def check_an_older_file_with_fields_missing_loads () -> None:

	"""A file that lacks whole fields takes the defaults for them without complaint."""

	settings = synthsync.core.settings.Settings(2)

	assert settings.load_text("{\"tempo\": 9000}") == []
	assert settings.tempo == 9000
	assert settings.outputs[1].ratio == (1, 1)
	assert settings.own_password == "synthsync"


def check_a_reset_goes_back_to_the_defaults () -> None:

	"""A factory reset forgets everything, the network included."""

	settings = synthsync.core.settings.Settings(2)
	settings.set_tempo(9000)
	settings.set_output(1, "ratio", "3")
	settings.set_own_password("another password")
	settings.set_network("Home WiFi", "correct horse")

	settings.reset()

	assert settings.tempo == 12000
	assert settings.outputs[1].ratio == (1, 1)
	assert settings.own_password == "synthsync"
	assert settings.network_name == ""
	assert len(settings.outputs) == 2


def check_the_clock_sends_four_pulses_a_beat_until_told_otherwise () -> None:

	"""A new SynthSync, a reset and a file saved before there was a PPQN all give the default of four."""

	settings = synthsync.core.settings.Settings(2)

	assert settings.ppqn == 4

	settings.set_ppqn(24)
	settings.reset()

	assert settings.ppqn == 4

	settings.set_ppqn(24)

	assert settings.load_text("{\"tempo\": 9000, \"outputs\": [{\"ratio\": \"1\"}, {\"ratio\": \"1/4\"}]}") == []
	assert settings.ppqn == 4
	assert settings.outputs[1].ratio == (1, 4)


def check_the_ppqn_is_saved_and_loaded_before_the_ratios_it_allows () -> None:

	"""A ratio only allowed at the saved PPQN still loads, because the PPQN is loaded first."""

	settings = synthsync.core.settings.Settings(2)
	settings.set_ppqn(1)
	settings.set_output(1, "ratio", "96")

	saved = settings.to_text()

	assert json.loads(saved)["ppqn"] == 1
	assert json.loads(saved)["version"] == 2

	again = synthsync.core.settings.Settings(2)

	assert again.load_text(saved) == []
	assert again.ppqn == 1
	assert again.outputs[1].ratio == (96, 1)


def check_a_ratio_is_checked_against_the_ppqn () -> None:

	"""At four pulses a beat the fastest ratio is ×24, and a refusal says why."""

	settings = synthsync.core.settings.Settings(2)

	assert refusal(settings, "output", 1, "ratio", "24") == ""
	assert refusal(settings, "output", 1, "ratio", "25") == "At 4 PPQN the fastest ratio is ×24"
	assert settings.outputs[1].ratio == (24, 1)

	# At one pulse a beat, a ratio past ×24 is allowed, and snap takes it to the nearest value past ×24 too.
	settings.set_output(1, "snap", False)
	settings.set_ppqn(1)
	settings.set_output(1, "ratio", "30")

	assert settings.set_output(1, "snap", True) == ("snap", "ratio")
	assert settings.outputs[1].ratio == (32, 1)


def check_a_change_of_ppqn_moves_only_the_outputs_that_would_be_too_fast () -> None:

	"""Each output keeps its ratio of the clock, unless that would pass 96 pulses a beat."""

	settings = synthsync.core.settings.Settings(4)
	settings.set_ppqn(1)
	settings.set_output(1, "ratio", "96")
	settings.set_output(2, "ratio", "4")
	settings.set_output(3, "ratio", "3/2")

	assert settings.set_ppqn(24) == [1]
	assert settings.outputs[1].ratio == (4, 1)
	assert settings.outputs[2].ratio == (4, 1)
	assert settings.outputs[3].ratio == (3, 2)
	assert settings.set_ppqn(48) == [1, 2]
	assert settings.outputs[0].ratio == (1, 1)
	assert settings.set_ppqn(1) == []

	for wrong in (3, 0, 96, "4", True, None, 4.0):
		assert refusal_of_ppqn(settings, wrong) == "The clock sends 1, 2, 4, 8, 12, 16, 24 or 48 pulses a beat", wrong

	assert settings.ppqn == 1


def check_a_ratio_too_fine_for_a_higher_ppqn_takes_its_nearest_snap_value () -> None:

	"""A fraction fine enough to type at a low PPQN, whose pulses a beat would then need numbers too large to play exactly, moves as turning snap on would."""

	settings = synthsync.core.settings.Settings(4)
	settings.set_ppqn(1)

	for index in (1, 2):
		settings.set_output(index, "snap", False)

	settings.set_output(1, "ratio", "9599/9598")
	settings.set_output(2, "ratio", "3/2")

	assert settings.set_ppqn(48) == [1]
	assert settings.outputs[1].ratio == synthsync.core.ratio.nearest_snap((9599, 9598), 48)
	assert settings.outputs[2].ratio == (3, 2)

	settings.set_ppqn(4)
	settings.set_output(3, "snap", False)
	settings.set_output(3, "ratio", "2399/4799")

	assert settings.set_ppqn(8) == [3]
	assert settings.outputs[3].ratio == synthsync.core.ratio.nearest_snap((2399, 4799), 8)


def check_every_ratio_held_after_any_change_of_ppqn_is_one_the_scheduler_takes () -> None:

	"""Whatever ratio the settings took at one PPQN, every PPQN after it leaves something the scheduler will play."""

	fine = ("9599/9598", "9600/9599", "4799/4800", "2399/4799", "1199/2399", "599/1199", "75/9599", "96", "1/128", "95.99", "0.01", "47.99")
	timeline = synthsync.core.timeline.Timeline(2)

	for first in synthsync.core.ratio.PPQNS:

		for typed in fine:

			settings = synthsync.core.settings.Settings(2)
			settings.set_ppqn(first)
			settings.set_output(1, "snap", False)

			if refusal(settings, "output", 1, "ratio", typed):
				continue

			for then in synthsync.core.ratio.PPQNS:

				settings.set_ppqn(then)
				pulses, beats = synthsync.core.ratio.effective(settings.outputs[1].ratio, then)

				# The scheduler refuses by raising, so taking it is all there is to see.
				timeline.set_ratio(1, pulses, beats)


def refusal_of_ppqn (settings: synthsync.core.settings.Settings, ppqn: object) -> str:

	"""Try to set the PPQN and give the message it was refused with, or nothing if it was taken."""

	try:
		settings.set_ppqn(ppqn)
	except ValueError as error:
		return str(error)

	return ""


def check_saved_outputs_past_those_wired_are_kept_and_saved_again () -> None:

	"""Wiring fewer outputs in config.txt and then more again gets back the settings the extra ones had."""

	six = synthsync.core.settings.Settings(6)
	six.set_output(4, "ratio", "3")
	six.set_output(5, "muted", True)

	four = synthsync.core.settings.Settings(4)

	assert four.load_text(six.to_text()) == []
	assert len(four.outputs) == 4

	saved = four.to_dict()["outputs"]
	original = six.to_dict()["outputs"]

	assert isinstance(saved, list) and len(saved) == 6
	assert isinstance(original, list) and saved[4:] == original[4:]

	# A change to an output that is wired leaves the others as they were.
	four.set_output(1, "ratio", "2")

	again = synthsync.core.settings.Settings(6)

	assert again.load_text(four.to_text()) == []
	assert again.outputs[1].ratio == (2, 1)
	assert again.outputs[4].ratio == (3, 1)
	assert again.outputs[5].muted


def check_saved_outputs_past_those_wired_are_checked_only_when_wired_again () -> None:

	"""Kept as they were saved, however they were saved, and never more than the player can drive."""

	data = json.loads(synthsync.core.settings.Settings(2).to_text())
	data["outputs"] += [{"ratio": "not a ratio"}, "not an output"] + [{}] * 10

	two = synthsync.core.settings.Settings(2)

	assert two.load_dict(data) == []

	saved = two.to_dict()["outputs"]

	assert isinstance(saved, list) and len(saved) == synthsync.core.timeline.MOST_OUTPUTS
	assert saved[2] == {"ratio": "not a ratio"} and saved[3] == "not an output"

	four = synthsync.core.settings.Settings(4)

	assert four.load_dict(two.to_dict()) == ["output 3 ratio: Type a ratio as a number such as 1.5, or as a fraction such as 4/3", "output 4: not readable"]


def check_starting_again_forgets_outputs_not_wired_too () -> None:

	"""A reset forgets every output's settings, wired or not."""

	six = synthsync.core.settings.Settings(6)
	six.set_output(5, "ratio", "3")

	four = synthsync.core.settings.Settings(4)
	four.load_text(six.to_text())
	four.reset()

	saved = four.to_dict()["outputs"]

	assert isinstance(saved, list) and len(saved) == 4
