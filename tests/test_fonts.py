"""The page's fonts: each holds every character SynthSync shows, keeps its licence and the tempo's tabular figures, and is what the tool cuts.

A character the page uses that a font lacks is drawn in the phone's own font,
which nobody would notice until a musician did. These say which character, and
that `python -m tools.fonts` mends it.
"""

import pathlib

import fontTools.ttLib
import pytest

import synthsync.web.app
import tools.fonts

FONTS = sorted(tools.fonts.CUTS)


def characters (path: pathlib.Path) -> set[str]:

	"""Give every character a font file can draw."""

	return {chr(code) for code in fontTools.ttLib.TTFont(path).getBestCmap()}


@pytest.mark.parametrize("name", FONTS)
def test_each_font_holds_every_character_it_is_cut_for (name: str) -> None:

	"""Lexend draws the title, and Inter printable ASCII and everything else the page and SynthSync's messages say."""

	missing = tools.fonts.needs(name) - characters(tools.fonts.STATIC / name)

	assert missing == set(), "%s lacks %r: run python -m tools.fonts" % (name, "".join(sorted(missing)))


def test_the_title_is_all_lexend_is_cut_for () -> None:

	"""Lexend holds the title's letters and nothing more, which is what keeps it to a kilobyte."""

	assert tools.fonts.title() == "SynthSync"
	assert characters(tools.fonts.STATIC / "lexend-600.woff2") == set("SynthSync")


def test_inter_keeps_its_tabular_figures () -> None:

	"""The page asks for figures all one width, so the tempo doesn't shift as it changes, and a cut keeps that only when told to."""

	for name in ("inter-400.woff2", "inter-700.woff2"):

		gsub = fontTools.ttLib.TTFont(tools.fonts.STATIC / name)["GSUB"].table

		assert "tnum" in {record.FeatureTag for record in gsub.FeatureList.FeatureRecord}, name


@pytest.mark.parametrize("name", FONTS)
def test_each_font_keeps_its_copyright_and_its_licence_sits_beside_the_whole_font (name: str) -> None:

	"""The SIL Open Font Licence asks for the copyright and the licence to go with the font: the cut keeps both in its names, and the whole font has the licence's text beside it."""

	source = tools.fonts.SOURCES / tools.fonts.CUTS[name][0]
	cut = fontTools.ttLib.TTFont(tools.fonts.STATIC / name)["name"]
	whole = fontTools.ttLib.TTFont(source)["name"]

	for number in (0, 14):
		assert cut.getDebugName(number) == whole.getDebugName(number) is not None, (name, number)

	assert "SIL Open Font License" in (source.parent / "OFL.txt").read_text()


def test_the_fonts_beside_the_page_are_the_ones_the_tool_cuts (tmp_path: pathlib.Path) -> None:

	"""Cut again, each font draws the same characters with the same glyphs, so none was changed by hand or left behind by a change of words."""

	assert tools.fonts.main([str(tmp_path)]) == 0

	for name in FONTS:

		made = fontTools.ttLib.TTFont(tmp_path / name)
		kept = fontTools.ttLib.TTFont(tools.fonts.STATIC / name)

		assert made.getBestCmap() == kept.getBestCmap(), "%s: run python -m tools.fonts" % name
		assert made.getGlyphOrder() == kept.getGlyphOrder(), "%s: run python -m tools.fonts" % name


def test_the_style_sheet_names_each_font_and_the_app_sends_each () -> None:

	"""The page's own files name each font by file, which the app sends from a laptop and the Pico's page carries inside it."""

	style = (tools.fonts.STATIC / "page.css").read_text()

	for name in FONTS:
		assert style.count('url("%s")' % name) == 1, name
		assert synthsync.web.app.PAGE_FILES[name] == "font/woff2"


def test_what_inter_holds_is_what_the_page_can_say_and_not_what_a_comment_says (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""A character written as itself, as an HTML entity or as a script's escape is kept, and so is one in a string SynthSync sends. One in a comment or a docstring isn't."""

	static = tmp_path / "synthsync" / "web" / "static"
	static.mkdir(parents=True)

	for name in tools.fonts.WORDS:
		(static / name).write_text("")

	(static / "index.html").write_text("<h1>Synth</h1><p>café &times; 2</p>")
	(static / "page.js").write_text('const arrow = "\\u2192"; // a comment\'s ≈ is still in the file\n')
	(tmp_path / "synthsync" / "said.py").write_text('"""A docstring\'s Ω."""\n\n# A comment\'s ≤.\nTOLD = "20 °C"\n')

	monkeypatch.setattr(tools.fonts, "ROOT", tmp_path)
	monkeypatch.setattr(tools.fonts, "STATIC", static)

	shown = tools.fonts.shown()

	# The page's own files are taken whole, comments and all, so a character there is kept wherever it is.
	assert {"é", "×", "→", "≈", "°", "A", "~", " "} <= shown
	assert {"Ω", "≤", "\n"}.isdisjoint(shown)
	assert tools.fonts.title() == "Synth"
