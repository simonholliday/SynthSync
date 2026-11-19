"""The README's wiring diagrams show the pins the code uses, and the pictures in the repo are the ones the script draws."""

import pathlib
import re
import typing
import xml.dom.minidom

import pytest

import synthsync.core.timeline
import synthsync.device.board
import tools.diagrams

# About how wide a letter is, as a part of the height of the writing, for each kind of writing. Bold is wider.
LETTER = {"label": (14, 0.5), "name": (14, 0.58), "title": (17, 0.58), "note": (13, 0.5)}

README = pathlib.Path(tools.diagrams.__file__).parent.parent / "README.md"

# How the README shows a diagram, in the one form both GitHub and the documentation site take, with a line clear on each side.
PICTURE = re.compile(r'(?<=\n\n)<picture>\n<source media="\(prefers-color-scheme: dark\)" srcset="docs/images/([a-z-]+)-dark\.svg">\n<img src="docs/images/([a-z-]+)\.svg" alt="[^"]+">\n</picture>(?=\n\n)')

# What the documentation site refuses in an SVG, because it could run something or fetch something.
REFUSED_ELEMENTS = {"script", "foreignObject", "image", "use"}


def test_the_diagrams_show_the_pins_the_board_uses () -> None:

	"""A pin drawn in the README is the pin SynthSync drives or reads, and every output it can have has a place on the header."""

	board = synthsync.device.board

	assert tools.diagrams.FIRST_OUTPUT == board.FIRST_OUTPUT_PIN
	assert tools.diagrams.PLAY_PAUSE == board.PLAY_PAUSE_PIN
	assert tools.diagrams.STOP == board.STOP_PIN
	assert tools.diagrams.TEMPO_LED == board.LED_PIN
	assert tools.diagrams.MIDI_OUT == board.MIDI_PIN
	assert tools.diagrams.KNOB == board.KNOB_PIN

	# The player lights the tempo LED from the bit after the eighth output's, and MIDI out is to have the one after that.
	assert board.LED_PIN == board.FIRST_OUTPUT_PIN + synthsync.core.timeline.MOST_OUTPUTS
	assert board.MIDI_PIN == board.LED_PIN + 1
	assert board.MIDI_PIN in tools.diagrams.HEADER_PIN
	assert board.MOST_OUTPUTS == synthsync.core.timeline.MOST_OUTPUTS
	assert board.MIDI_PIN - board.FIRST_OUTPUT_PIN < synthsync.core.timeline.PIN_BITS

	for pin in range(board.FIRST_OUTPUT_PIN, board.FIRST_OUTPUT_PIN + board.MOST_OUTPUTS):
		assert pin in tools.diagrams.HEADER_PIN, pin


def test_each_midi_socket_takes_the_contacts_the_standard_gives_it () -> None:

	"""The supply's resistor is MIDI's pin 4 and the transistor's its pin 5, and Type B is Type A with those two swapped."""

	assert [each[1:] for each in tools.diagrams.MIDI_SOCKETS] == [
		("Ring", "Tip", "Sleeve"),
		("Tip", "Ring", "Sleeve"),
		("Pin 4", "Pin 5", "Pin 2"),
	]


def test_the_readme_wires_midi_as_the_diagrams_do () -> None:

	"""The guide's table has a row for each socket with the diagram's contacts, and it shows both ways of wiring one."""

	readme = README.read_text(encoding="utf-8")

	for name, feed, sink, ground in tools.diagrams.MIDI_SOCKETS:
		assert "| %s | %s | %s | %s |" % (name, feed, sink, ground) in readme, name

	assert 'src="docs/images/midi.svg"' in readme
	assert 'src="docs/images/midi-switch.svg"' in readme


def test_the_readme_shows_every_diagram_for_light_pages_and_dark () -> None:

	"""Each diagram appears once, in the form that follows the reader's theme, naming the same drawing twice."""

	shown = PICTURE.findall(README.read_text(encoding="utf-8"))

	assert all(dark == light for dark, light in shown), shown
	assert sorted(light for _, light in shown) == sorted(tools.diagrams.DIAGRAMS)


def test_a_diagram_for_a_dark_page_keeps_none_of_the_light_colours () -> None:

	"""Every colour in a drawing has its dark stand-in, and a drawing for a light page is left as it is."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		light = draw().svg()
		dark = tools.diagrams.shaded(light, True)

		assert set(re.findall(r"#[0-9a-f]{6}\b", light)) <= set(tools.diagrams.DARK), name
		assert not set(re.findall(r"#[0-9a-f]{6}\b", dark)) & set(tools.diagrams.DARK), name
		assert tools.diagrams.shaded(light, False) == light


@pytest.mark.parametrize("dark", (False, True))
def test_every_diagram_holds_nothing_the_documentation_site_refuses (dark: bool) -> None:

	"""It keeps its size on its root, and has nothing that could run or fetch anything, and no words the site would stop at."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		svg = tools.diagrams.shaded(draw().svg(), dark)
		root = xml.dom.minidom.parseString(svg).documentElement

		assert root is not None
		assert all(root.hasAttribute(each) for each in ("width", "height", "viewBox")), name

		for element in root.getElementsByTagName("*"):

			assert element.tagName not in REFUSED_ELEMENTS, (name, element.tagName)

			for attribute in element.attributes.keys():
				assert not attribute.startswith("on") and "href" not in attribute, (name, attribute)

		assert "url(" not in svg and "@import" not in svg, name
		assert "\u2014" not in svg, name
		assert re.findall(r"(?<![\d.#])v?\d+\.\d+\.\d+", " ".join(each[2] for each in draw().written)) == [], name


def test_every_diagram_is_a_whole_picture_with_words_for_a_screen_reader () -> None:

	"""Each one reads as an SVG file, and says what it is and what it shows."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		picture = xml.dom.minidom.parseString(draw().svg())
		root = picture.documentElement

		assert root is not None and root.tagName == "svg", name

		for tag in ("title", "desc"):

			said = root.getElementsByTagName(tag)

			assert len(said) == 1 and said[0].firstChild is not None and len(said[0].firstChild.nodeValue or "") > 10, (name, tag)


def test_the_words_on_every_pin_fit_its_tag () -> None:

	"""A long pin name gets a wider tag."""

	height, part = LETTER["name"]

	for name, draw in tools.diagrams.DIAGRAMS.items():
		for words, width in draw().tags:
			assert len(words) * height * part <= width - 8, (name, words)


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

		for dark in (False, True):

			drawn = tools.diagrams.svg_path(tmp_path, name, dark).read_text(encoding="utf-8")
			kept = tools.diagrams.svg_path(tools.diagrams.IMAGES, name, dark)

			assert kept.exists(), "run `python -m tools.diagrams`: %s is missing" % kept.name
			assert kept.read_text(encoding="utf-8") == drawn, "run `python -m tools.diagrams`: %s is out of date" % kept.name


def test_a_picture_is_put_in_place_whole_by_a_rename_and_nothing_is_left_beside_it (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Drawn again over an old picture, and over what a run cut short left, the picture is replaced by renaming a new file, never by writing over it."""

	path = tmp_path / "output.svg"
	path.write_text("old", encoding="utf-8")
	(tmp_path / "output.svg.new").write_text("half", encoding="utf-8")
	opened: list[str] = []
	really_open = open

	def watching (file: typing.Any, mode: str = "r", *rest: typing.Any, **named: typing.Any) -> typing.Any:

		"""Note how each file is opened."""

		opened.append(mode)

		return really_open(file, mode, *rest, **named)

	monkeypatch.setattr("builtins.open", watching)
	tools.diagrams.write(path, "<svg/>")
	monkeypatch.undo()

	assert opened == ["x"]
	assert path.read_text(encoding="utf-8") == "<svg/>"
	assert sorted(each.name for each in tmp_path.iterdir()) == ["output.svg"]
