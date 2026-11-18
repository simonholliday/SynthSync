"""Cut the page's fonts down to the characters SynthSync shows, and put them beside the page.

    python -m tools.fonts

The page sets its title in Lexend and everything else in Inter. The whole
fonts are in `fonts/`, each beside its licence: Fontsource's Latin cuts.

A phone downloads the page each time it opens it, fonts and all, so they are
cut down. Lexend keeps only the letters of the title. Inter keeps printable
ASCII and every other character the page's files and SynthSync's own messages
use, with its tabular figures, which keep the tempo from shifting sideways as
it changes. A character a cut lacks, such as an accent in a WiFi network's
name, is drawn in the phone's own font instead.

`tests/test_fonts.py` fails once the page uses a character a cut lacks, and
running this again mends it. Each file is written under a new name and renamed
into place, so it is safe to run on a network share. It needs fontTools with
brotli, which `pip install -e '.[dev]'` brings.
"""

import ast
import html
import io
import os
import pathlib
import re
import sys

import fontTools.subset
import fontTools.ttLib

ROOT = pathlib.Path(__file__).parent.parent
SOURCES = ROOT / "fonts"
STATIC = ROOT / "synthsync" / "web" / "static"

# Each cut: the file made beside the page, the whole font it is cut from, and
# whether it holds the title's letters or the text's.
CUTS = {
	"lexend-600.woff2": ("lexend/lexend-latin-600-normal.woff2", "title"),
	"inter-400.woff2": ("inter/inter-latin-400-normal.woff2", "text"),
	"inter-700.woff2": ("inter/inter-latin-700-normal.woff2", "text"),
}

# The features kept beyond fontTools' own defaults: the page's figures are
# tabular, and fontTools drops them unless asked.
FEATURES = ["tnum"]

# The page's files that hold words, as against the fonts.
WORDS = ("index.html", "page.css", "page.js", "settings.js", "knob.js", "ratio.js")


def title () -> str:

	"""Give the page's title, which is all Lexend draws."""

	found = re.findall(r"<h1>([^<]+)</h1>", (STATIC / "index.html").read_text())

	if len(found) != 1:
		raise ValueError("index.html should have one <h1>, with nothing but words in it.")

	return str(found[0])


def said (path: pathlib.Path) -> set[str]:

	"""Give every character of the strings a Python file can say, leaving out its comments and docstrings."""

	tree = ast.parse(path.read_text())
	docstrings = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr)}

	return {character for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings for character in node.value}


def shown () -> set[str]:

	"""Give every character Inter has to hold: printable ASCII, and anything else the page or SynthSync says."""

	found = {chr(code) for code in range(0x20, 0x7f)}

	for name in WORDS:

		# A character may be written as itself, as an HTML entity or as a script's escape.
		text = html.unescape((STATIC / name).read_text())
		found |= set(re.sub(r"\\u\{?([0-9a-fA-F]{4,6})\}?", lambda escape: chr(int(escape.group(1), 16)), text))

	for path in sorted((ROOT / "synthsync").rglob("*.py")):
		found |= said(path)

	return {character for character in found if character.isprintable()}


def needs (name: str) -> set[str]:

	"""Give the characters one cut has to hold."""

	return set(title()) if CUTS[name][1] == "title" else shown()


def cut (name: str) -> bytes:

	"""Cut one font down to what it has to hold, keeping its names, the licence among them, and give the file."""

	options = fontTools.subset.Options()
	options.flavor = "woff2"
	options.layout_features = options.layout_features + FEATURES
	options.name_IDs = ["*"]

	font = fontTools.ttLib.TTFont(SOURCES / CUTS[name][0])
	subsetter = fontTools.subset.Subsetter(options)
	subsetter.populate(text="".join(sorted(needs(name))))
	subsetter.subset(font)

	made = io.BytesIO()
	font.save(made)

	return made.getvalue()


def write (path: pathlib.Path, data: bytes) -> None:

	"""Write a file under a new name, then rename it into place, so nothing is ever truncated where it lies."""

	spare = path.with_name(path.name + ".new")

	with open(spare, "xb") as file:
		file.write(data)

	os.replace(spare, path)


def main (arguments: list[str] | None = None) -> int:

	"""Cut every font and put it beside the page, or in a folder that is named, and say how big each came out."""

	arguments = sys.argv[1:] if arguments is None else arguments
	folder = pathlib.Path(arguments[0]) if arguments else STATIC

	for name, (source, _) in CUTS.items():

		data = cut(name)
		write(folder / name, data)

		print("%s: %d characters, %.1f KB, from %.1f KB." % (name, len(needs(name)), len(data) / 1024, (SOURCES / source).stat().st_size / 1024))

	return 0


if __name__ == "__main__":
	sys.exit(main())
