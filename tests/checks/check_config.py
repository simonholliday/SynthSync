"""config.txt is read the same on the Pico's Python as on a laptop's: what can be used is, and the rest is named in words fit to show."""

import synthsync.core.config
import synthsync.core.timeline

# The file SynthSync comes with, at the top of the project, found from this file so it is found from any folder.
COMES_WITH = __file__.rsplit("/", 1)[0] + "/../../config.txt"


def parsed (text: str) -> synthsync.core.config.Config:

	"""Read a file holding this text."""

	return synthsync.core.config.parse(text.encode())


def check_a_missing_file_is_every_default_with_nothing_to_say () -> None:

	"""A new Pico may have no config.txt at all, and that is no problem."""

	config = synthsync.core.config.read("/no/such/folder/config.txt")

	assert config.outputs == synthsync.core.config.DEFAULT_OUTPUTS == 4
	assert config.problems == []


def check_the_file_synthsync_comes_with_reads_cleanly_as_the_defaults () -> None:

	"""The config.txt an install puts on the Pico is read with nothing to say, holds the defaults, and is well short of the longest a file may be."""

	config = synthsync.core.config.read(COMES_WITH)

	assert config.problems == []
	assert config.outputs == synthsync.core.config.DEFAULT_OUTPUTS

	with open(COMES_WITH, "rb") as handle:
		data = handle.read()

	assert len(data) < synthsync.core.config.LONGEST // 2
	assert b"\noutputs = 4\n" in data


def check_every_number_of_outputs_from_one_to_eight_is_taken () -> None:

	"""Anything from 1 output to as many as the player can drive."""

	assert synthsync.core.timeline.MOST_OUTPUTS == 8

	for outputs in range(1, 9):

		config = parsed("outputs = %d\n" % outputs)

		assert config.outputs == outputs
		assert config.problems == []


def check_notes_blank_lines_capitals_and_spaces_are_all_taken () -> None:

	"""A line may have a note after it, any capitals and any spaces, and a note or a blank line on its own is skipped."""

	config = parsed("# How many are wired.\n\n   \n  OUTPUTS=  6  # six jacks\n\t# outputs = 2\n")

	assert config.outputs == 6
	assert config.problems == []

	# A note that is the whole file says nothing either.
	assert parsed("# nothing yet\n").problems == []
	assert parsed("").problems == []


def check_a_file_saved_by_windows_is_read_as_any_other () -> None:

	"""Its mark saying it is UTF-8 is skipped, and so are the carriage returns at the ends of its lines, or on their own."""

	for data in (b"\xef\xbb\xbfoutputs = 2\r\n", b"# notes\r\noutputs = 2\r\n", b"# notes\routputs = 2\r"):

		config = synthsync.core.config.parse(data)

		assert config.outputs == 2, data
		assert config.problems == [], (data, config.problems)


def check_a_number_of_outputs_that_cannot_be_used_keeps_the_default_and_names_its_line () -> None:

	"""None, too few, too many, a fraction, words, signs, and digits that aren't the ones on a keyboard."""

	for value in ("", "0", "9", "10", "-1", "+4", "4.0", "4.5", "four", "4 4", "١", "999999999999"):

		config = parsed("# How many.\noutputs = %s\n" % value)

		assert config.outputs == 4, value
		assert config.problems == ["config.txt, line 2: outputs has to be a whole number from 1 to 8"], (value, config.problems)


def check_a_line_that_is_not_a_setting_is_named () -> None:

	"""A line with no equals sign, or nothing before it."""

	for line in ("outputs 6", "= 6", "6"):

		config = parsed(line)

		assert config.outputs == 4, line
		assert config.problems == ["config.txt, line 1: a setting is a name, an equals sign and a value, such as outputs = 4"], (line, config.problems)


def check_a_name_that_is_not_a_setting_is_named_as_it_was_typed () -> None:

	"""A misspelt name is shown back, so it can be found, but never at length, and never with anything in it that can't be seen."""

	config = parsed("outputs = 2\noutptus = 3\n")

	assert config.outputs == 2
	assert config.problems == ['config.txt, line 2: SynthSync has no setting called "outptus"']

	long = parsed("a" * 40 + " = 1")

	assert long.problems == ['config.txt, line 1: SynthSync has no setting called "%s..."' % ("a" * synthsync.core.config.SHOWN)]

	hidden = parsed("out\x07puts\x1b = 1")

	assert hidden.problems == ['config.txt, line 1: SynthSync has no setting called "out?puts?"']


def check_the_first_good_line_for_a_setting_is_the_one_used () -> None:

	"""A second line for the same setting is skipped and named, and a first that can't be used doesn't count."""

	twice = parsed("outputs = 2\n\noutputs = 3\n")

	assert twice.outputs == 2
	assert twice.problems == ["config.txt, line 3: outputs is set already, on line 1"]

	mended = parsed("outputs = 9\noutputs = 3\n")

	assert mended.outputs == 3
	assert mended.problems == ["config.txt, line 1: outputs has to be a whole number from 1 to 8"]


def check_a_file_that_is_not_text_is_not_read_at_all () -> None:

	"""Anything that isn't UTF-8, or holds a nought byte as a picture or a program would, keeps every default and is said to be what it is."""

	for data in (b"\xff\xfe\x00o\x00u\x00t", b"\x89PNG\r\n\x1a\n", b"outputs = 6\x00", b"outputs = 6 \xe9\n"):

		config = synthsync.core.config.parse(data)

		assert config.outputs == 4, data
		assert config.problems == ["config.txt isn't plain text, so none of it was used"], (data, config.problems)


def check_a_file_far_too_long_to_be_this_one_is_not_read_at_all () -> None:

	"""Up to the longest a file may be it is read, and past that none of it is."""

	setting = b"outputs = 6\n"
	longest = setting + b"#" * (synthsync.core.config.LONGEST - len(setting))

	assert len(longest) == synthsync.core.config.LONGEST
	assert synthsync.core.config.parse(longest).outputs == 6

	config = synthsync.core.config.parse(longest + b"#")

	assert config.outputs == 4
	assert config.problems == ["config.txt is too long to be SynthSync's settings, so none of it was used"]


def check_only_so_many_lines_are_named_and_the_rest_are_counted () -> None:

	"""Each named line goes in every page's greeting, so a file of nonsense can't swell it."""

	assert synthsync.core.config.MOST_TOLD == 4

	many = parsed("\n".join("line %d" % number for number in range(1, 11)))

	assert len(many.problems) == 5
	assert many.problems[0].startswith("config.txt, line 1: ")
	assert many.problems[3].startswith("config.txt, line 4: ")
	assert many.problems[4] == "config.txt has 6 more lines that couldn't be used"

	one_more = parsed("\n".join("line %d" % number for number in range(1, 6)))

	assert one_more.problems[4] == "config.txt has 1 more line that couldn't be used"


def check_whatever_is_said_about_a_file_fits_in_a_greeting () -> None:

	"""The longest a file's problems can be, with the longest names shown back and on its last lines, is a few hundred bytes."""

	worst = ("#" * 60 + "\n") * 55 + ("%s = 1\n" % ("x" * 100)) * 4 + "nonsense\n"
	config = parsed(worst)

	assert len(worst.encode()) <= synthsync.core.config.LONGEST
	assert len(config.problems) == 5

	for problem in config.problems:
		assert len(problem) <= 100, problem

	assert sum(len(problem) for problem in config.problems) <= 450


def check_a_folder_where_the_file_should_be_is_said_to_be_unreadable () -> None:

	"""Something there that can't be opened as a file is worth saying, unlike nothing there at all."""

	config = synthsync.core.config.read(__file__.rsplit("/", 1)[0])

	assert config.outputs == 4
	assert config.problems == ["config.txt couldn't be read, so none of it was used"]
