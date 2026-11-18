"""The README's wiring diagrams show the pins the code uses, and the pictures in the repo are the ones the script draws."""

import pathlib
import re
import shutil
import struct
import typing
import xml.dom.minidom
import zlib

import pytest

import synthsync.core.timeline
import synthsync.device.board
import tools.diagrams

# About how wide a letter is, as a part of the height of the writing, for each kind of writing. Bold is wider.
LETTER = {"label": (14, 0.5), "name": (14, 0.58), "title": (17, 0.58), "note": (13, 0.5)}

README = pathlib.Path(tools.diagrams.__file__).parent.parent / "README.md"

# How the README shows a diagram, in the one form both GitHub and the documentation site take, with a line clear on each side.
PICTURE = re.compile(r'(?<=\n\n)<picture>\n<source media="\(prefers-color-scheme: dark\)" srcset="docs/images/([a-z-]+)-dark\.png">\n<img src="docs/images/([a-z-]+)\.png" alt="[^"]+">\n</picture>(?=\n\n)')


def png_facts (png: bytes) -> tuple[int, int, int, int]:

	"""Give a PNG's width, height and colour type, and how see-through its top left pixel is, 0 being wholly."""

	width, height = struct.unpack(">II", png[16:24])
	place = 8
	pixels = b""

	while place < len(png):

		length = struct.unpack(">I", png[place:place + 4])[0]

		if png[place + 4:place + 8] == b"IDAT":
			pixels += png[place + 8:place + 8 + length]

		place += length + 12

	# The first pixel of the first row has nothing before it to be stored against, so it is there as it is, after the row's filter byte.
	return width, height, png[25], zlib.decompress(pixels)[4]


def test_the_diagrams_show_the_pins_the_board_uses () -> None:

	"""A pin drawn in the README is the pin SynthSync drives or reads, and every output it can have has a place on the header."""

	board = synthsync.device.board

	assert tools.diagrams.FIRST_OUTPUT == board.FIRST_OUTPUT_PIN
	assert tools.diagrams.PLAY_PAUSE == board.PLAY_PAUSE_PIN
	assert tools.diagrams.STOP == board.STOP_PIN
	assert tools.diagrams.TEMPO_LED == board.LED_PIN
	assert tools.diagrams.MIDI_OUT == board.MIDI_PIN

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

	assert 'src="docs/images/midi.png"' in readme
	assert 'src="docs/images/midi-switch.png"' in readme


def test_the_readme_shows_every_diagram_for_light_pages_and_dark () -> None:

	"""Each diagram appears once, in the form that follows the reader's theme, naming the same picture twice."""

	shown = PICTURE.findall(README.read_text(encoding="utf-8"))

	assert all(dark == light for dark, light in shown), shown
	assert sorted(light for _, light in shown) == sorted(tools.diagrams.DIAGRAMS)


def test_every_png_is_made_from_the_diagram_as_it_is_drawn_now () -> None:

	"""Changing a diagram without making its PNGs again would leave the README showing the old one."""

	assert [path.name for path, _, _, _ in tools.diagrams.out_of_date(tools.diagrams.IMAGES)] == [], "run `python -m tools.diagrams`"


def test_every_png_is_twice_its_diagram_with_see_through_corners () -> None:

	"""Sharp at the width of the column, and with nothing square behind the card's rounded corners on any page."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		sheet = draw()

		for dark in (False, True):

			facts = png_facts(tools.diagrams.png_path(tools.diagrams.IMAGES, name, dark).read_bytes())

			assert facts == (sheet.width * 2, sheet.height * 2, 6, 0), (name, dark)


def test_a_diagram_for_a_dark_page_keeps_none_of_the_light_colours () -> None:

	"""Every colour in the drawing has its dark stand-in, and there is one for each colour the script names."""

	for name, draw in tools.diagrams.DIAGRAMS.items():

		light = draw().svg()
		dark = tools.diagrams.shaded(light, True)

		assert set(re.findall(r"#[0-9a-f]{6}\b", light)) <= set(tools.diagrams.DARK), name
		assert not set(re.findall(r"#[0-9a-f]{6}\b", dark)) & set(tools.diagrams.DARK), name
		assert tools.diagrams.shaded(light, False) == light


def test_a_png_says_which_svg_it_was_made_from () -> None:

	"""The note reads back from a real PNG, which stays one, and a PNG without a note says nothing."""

	plain = tools.diagrams.png_path(tools.diagrams.IMAGES, "direct", False).read_bytes()
	bare = plain[:33] + plain[33 + 12 + struct.unpack(">I", plain[33:37])[0]:]

	assert tools.diagrams.note_in(bare) == ""
	assert tools.diagrams.note_in(tools.diagrams.noted(bare, "abc")) == "abc"
	assert png_facts(tools.diagrams.noted(bare, "abc")) == png_facts(bare)


def test_a_png_made_from_another_svg_is_out_of_date (tmp_path: pathlib.Path) -> None:

	"""One whose note doesn't match its diagram is made again, and only that one."""

	for path in tools.diagrams.IMAGES.glob("*.png"):
		shutil.copy(path, tmp_path / path.name)

	png = (tmp_path / "midi.png").read_bytes()

	(tmp_path / "midi.png").write_bytes(tools.diagrams.noted(png[:33] + png[33 + 12 + struct.unpack(">I", png[33:37])[0]:], "another"))

	assert [path.name for path, _, _, _ in tools.diagrams.out_of_date(tmp_path)] == ["midi.png"]


def test_pictures_up_to_date_are_left_alone (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""With every PNG current no browser is wanted, and drawing the SVGs again is all there is to do."""

	for path in tools.diagrams.IMAGES.glob("*.png"):
		shutil.copy(path, tmp_path / path.name)

	def refuse (wanted: list[typing.Any]) -> None:

		"""Stand in for making PNGs, which shouldn't happen."""

		raise AssertionError(wanted)

	monkeypatch.setattr(tools.diagrams, "make_pngs", refuse)

	assert tools.diagrams.main([str(tmp_path)]) == 0
	assert (tmp_path / "midi.svg").exists()


def test_with_no_browser_to_make_pngs_it_says_so (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""The SVGs are still drawn, and the run doesn't claim to have finished."""

	def missing (wanted: list[typing.Any]) -> None:

		"""Stand in for making PNGs without Playwright."""

		raise ImportError("playwright")

	monkeypatch.setattr(tools.diagrams, "make_pngs", missing)

	assert tools.diagrams.main([str(tmp_path)]) == 1
	assert (tmp_path / "midi.svg").exists()
	assert "16 PNGs need making" in capsys.readouterr().out


def test_making_a_png_gives_a_picture_on_nothing_that_is_then_up_to_date (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Through a real browser, a diagram comes out at twice its size, see-through at the corner, and isn't made again."""

	pytest.importorskip("playwright")

	monkeypatch.setattr(tools.diagrams, "DIAGRAMS", {"direct": tools.diagrams.direct})

	tools.diagrams.make_pngs(tools.diagrams.out_of_date(tmp_path))

	sheet = tools.diagrams.direct()

	assert png_facts((tmp_path / "direct-dark.png").read_bytes()) == (sheet.width * 2, sheet.height * 2, 6, 0)
	assert tools.diagrams.out_of_date(tmp_path) == []


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

	tools.diagrams.draw_svgs(tmp_path)

	for name in tools.diagrams.DIAGRAMS:

		drawn = (tmp_path / (name + ".svg")).read_text(encoding="utf-8")
		kept = tools.diagrams.IMAGES / (name + ".svg")

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
	tools.diagrams.write(path, b"<svg/>")
	monkeypatch.undo()

	assert opened == ["xb"]
	assert path.read_text(encoding="utf-8") == "<svg/>"
	assert sorted(each.name for each in tmp_path.iterdir()) == ["output.svg"]
