"""The README's wiring diagrams show the pins the code uses, and the pictures in the repo are the ones the script draws."""

import pathlib
import xml.dom.minidom

import picoclock.device.board
import tools.diagrams

# About how wide a letter is, as a part of the height of the writing, for each kind of writing. Bold is wider.
LETTER = {"label": (14, 0.5), "name": (14, 0.58), "title": (17, 0.58), "note": (13, 0.5)}


def test_the_diagrams_show_the_pins_the_board_uses () -> None:

	"""A pin drawn in the README is the pin PicoClock drives or reads, and every output it can have has a place on the header."""

	board = picoclock.device.board

	assert tools.diagrams.FIRST_OUTPUT == board.FIRST_OUTPUT_PIN
	assert tools.diagrams.PLAY_PAUSE == board.PLAY_PAUSE_PIN
	assert tools.diagrams.STOP == board.STOP_PIN
	assert tools.diagrams.TEMPO_LED == board.LED_PIN

	for pin in range(board.FIRST_OUTPUT_PIN, board.FIRST_OUTPUT_PIN + board.MOST_OUTPUTS):
		assert pin in tools.diagrams.HEADER_PIN, pin


def test_every_diagram_is_a_whole_picture_with_words_for_a_screen_reader () -> None:

	"""Each one reads as an SVG file, and says what it is and what it shows."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		picture = xml.dom.minidom.parseString(draw().svg())
		root = picture.documentElement

		assert root is not None and root.tagName == "svg", name

		for tag in ("title", "desc"):

			said = root.getElementsByTagName(tag)

			assert len(said) == 1 and said[0].firstChild is not None and len(said[0].firstChild.nodeValue or "") > 10, (name, tag)


def test_nothing_written_on_a_diagram_runs_off_its_card () -> None:

	"""Every piece of writing begins and ends inside the picture, going by about how wide its letters are."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		sheet = draw()

		for x, anchor, text, kind in sheet.written:

			height, part = LETTER[kind]
			width = len(text) * height * part
			left = x if anchor == "start" else x - width if anchor == "end" else x - width / 2

			assert left >= 12 and left + width <= sheet.width - 12, (name, text)


def test_the_pictures_in_the_repo_are_the_ones_the_script_draws (tmp_path: pathlib.Path) -> None:

	"""Changing a value in the script without drawing the pictures again would leave the README showing the old one."""

	assert tools.diagrams.main([str(tmp_path)]) == 0

	for name in tools.diagrams.DIAGRAMS:

		drawn = (tmp_path / (name + ".svg")).read_text(encoding="utf-8")
		kept = tools.diagrams.IMAGES / (name + ".svg")

		assert kept.exists(), "run `python -m tools.diagrams`: %s is missing" % kept.name
		assert kept.read_text(encoding="utf-8") == drawn, "run `python -m tools.diagrams`: %s is out of date" % kept.name
