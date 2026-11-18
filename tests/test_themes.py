"""The page's looks: each sets every colour, each can be read, and Settings offers exactly the looks the style sheet draws.

The values are Superconductor's. These tests read them out of `page.css`, so a
look can't be added, changed or left half written without one of them saying so.
"""

import pathlib
import re

import pytest

STATIC = pathlib.Path(__file__).parent.parent / "synthsync" / "web" / "static"

# Each pairing the page draws, and the contrast it needs: 4.5:1 for words and
# 3:1 for a mark, such as a lit light or the outline of a control. Every control
# and every word but the header's sits on a panel, so the page's own ground
# carries only the name, in --text, and the button beside it, whose word names it.
WORDS = 4.5
MARKS = 3.0
PAIRS = [
	("text", "ground", WORDS),
	("text", "panel", WORDS),
	("text", "raised", WORDS),
	("quiet", "panel", WORDS),
	("quiet", "raised", WORDS),
	("wrong", "panel", WORDS),
	("on-lit", "lit", WORDS),
	("lit", "panel", MARKS),
	("lit", "raised", MARKS),
	("edge-strong", "panel", MARKS),
]


def looks () -> list[tuple[str, str, dict[str, str]]]:

	"""Read every look's block out of the style sheet: its key, its colour scheme and its tokens.

	Match device has two, its dark half and its light half.
	"""

	css = (STATIC / "page.css").read_text()
	found = []

	for selector, body in re.findall(r'^((?::root,\n)?\s*\[data-theme="[a-z]+"\])\s*\{([^}]*)\}', css, re.MULTILINE):

		key = re.search(r'data-theme="([a-z]+)"', selector)
		scheme = re.search(r"color-scheme:\s*(light|dark);", body)

		assert key is not None and scheme is not None, selector

		found.append((key.group(1), scheme.group(1), dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-f]{6});", body))))

	return found


def offered () -> list[str]:

	"""Read the keys of the looks Settings offers, in the order it offers them."""

	script = (STATIC / "settings.js").read_text()
	listed = re.search(r"const LOOKS = \[(.*?)\];", script, re.DOTALL)

	assert listed is not None

	return re.findall(r'\["([a-z]+)", "[^"]+"\]', listed.group(1))


def rgb (colour: str) -> str:

	"""Write a colour as a browser gives back one it has worked out: "#0f1114" is "rgb(15, 17, 20)"."""

	return "rgb(%d, %d, %d)" % tuple(int(colour[place:place + 2], 16) for place in (1, 3, 5))


def token (key: str, name: str, scheme: str = "") -> str:

	"""Give one token's value in one look, or in the given half of Match device."""

	return [values for found, kind, values in looks() if found == key and scheme in ("", kind)][0][name]


def luminance (colour: str) -> float:

	"""Give a colour's relative luminance, as WCAG 2 defines it."""

	channels = [int(colour[place:place + 2], 16) / 255 for place in (1, 3, 5)]
	linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]

	return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast (first: str, second: str) -> float:

	"""Give the contrast between two colours, from 1 to 21."""

	lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)

	return (lighter + 0.05) / (darker + 0.05)


def test_the_contrast_of_two_colours_is_measured_as_wcag_measures_it () -> None:

	"""Black on white is 21, a colour on itself is 1, and a known pair gives its known figure."""

	assert contrast("#000000", "#ffffff") == pytest.approx(21)
	assert contrast("#777777", "#777777") == pytest.approx(1)
	assert contrast("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


def test_every_look_sets_every_colour_the_page_uses () -> None:

	"""A look that left one out would quietly take Dark's, and show one wrong colour nobody notices."""

	found = looks()
	tokens = set(found[0][2])

	assert tokens == {"ground", "panel", "raised", "edge", "edge-strong", "text", "quiet", "lit", "on-lit", "wrong"}

	for key, scheme, values in found:
		assert set(values) == tokens, (key, scheme, tokens ^ set(values))


def test_settings_offers_exactly_the_looks_the_style_sheet_draws () -> None:

	"""Every look offered is drawn, every look drawn is offered, Match device has a dark half and a light half, and the rest one of either."""

	found = looks()
	keys = [key for key, _, _ in found]

	assert sorted(set(keys)) == sorted(offered())
	assert len(offered()) == len(set(offered())) == 11
	assert sorted(scheme for key, scheme, _ in found if key == "system") == ["dark", "light"]

	for key in set(keys) - {"system"}:
		assert keys.count(key) == 1, key


def test_dark_is_the_look_until_a_phone_chooses_another () -> None:

	"""The style sheet's first block is Dark's, for the page itself; Settings ticks Dark when nothing is kept; and the phone's bar starts in Dark's ground."""

	css = (STATIC / "page.css").read_text()
	script = (STATIC / "settings.js").read_text()
	page = (STATIC / "index.html").read_text()
	dark = [values for key, scheme, values in looks() if key == "dark"][0]

	assert re.search(r'^:root,\n\[data-theme="dark"\] \{', css, re.MULTILINE)
	assert css.index(':root,\n[data-theme="dark"]') < css.index('[data-theme="system"]')
	assert 'const FIRST_LOOK = "dark";' in script
	assert '<meta name="theme-color" content="%s">' % dark["ground"] in page


def test_the_page_puts_a_kept_look_on_before_it_is_first_drawn () -> None:

	"""The page's head reads the same key Settings keeps the choice under, before the style sheet, in a script of its own that the page's packing leaves alone."""

	script = (STATIC / "settings.js").read_text()
	page = (STATIC / "index.html").read_text()
	key = re.search(r'const LOOK_KEY = "([a-z.]+)";', script)

	assert key is not None
	assert 'localStorage.getItem("%s")' % key.group(1) in page
	assert page.index('localStorage.getItem("%s")' % key.group(1)) < page.index('<link rel="stylesheet" href="page.css">') < page.index("</head>")


@pytest.mark.parametrize("first, second, floor", PAIRS)
def test_every_look_can_be_read (first: str, second: str, floor: float) -> None:

	"""Each pairing the page draws clears its floor in every look, Match device's light half included."""

	short = []

	for key, scheme, values in looks():

		measured = contrast(values[first], values[second])

		if measured < floor:
			short.append("%s (%s) %.2f" % (key, scheme, measured))

	assert short == [], "--%s on --%s needs %.1f:1" % (first, second, floor)


def test_no_colour_is_written_anywhere_but_in_a_look () -> None:

	"""Outside the looks' blocks every colour is a token, so a look changes all of it. The phone's bar is set from --ground once the page is up."""

	css = re.sub(r"/\*.*?\*/", "", (STATIC / "page.css").read_text(), flags=re.DOTALL)
	outside = re.sub(r'^(?::root,\n)?\s*\[data-theme="[a-z]+"\]\s*\{[^}]*\}', "", css, flags=re.MULTILINE)
	written = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")

	assert written.findall(outside) == []

	for name in ("page.js", "settings.js", "knob.js", "ratio.js", "tap.js"):
		assert written.findall((STATIC / name).read_text()) == [], name

	page = (STATIC / "index.html").read_text()
	bar = re.search(r'<meta name="theme-color" content="#[0-9a-f]{6}">', page)

	assert bar is not None
	assert written.findall(page.replace(bar.group(0), "")) == []
