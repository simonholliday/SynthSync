"""The README stays something the project's documentation site can publish as it is, as well as GitHub."""

import pathlib
import re

import tests.test_diagrams

ROOT = pathlib.Path(__file__).parent.parent
README = ROOT / "README.md"

# The raw tags the site lets through, and the three that make up a picture for light and dark pages.
ALLOWED_TAGS = {"abbr", "br", "details", "kbd", "sub", "summary", "sup", "picture", "source", "img"}

# The one release number the README may name: the MicroPython it is built on.
ALLOWED_VERSIONS = {"1.29.0"}


def prose () -> str:

	"""Give the README without its code blocks and code spans, whose contents are shown and not read as markup."""

	text = re.sub(r"^```.*?^```$", "", README.read_text(encoding="utf-8"), flags=re.DOTALL | re.MULTILINE)

	return re.sub(r"`[^`\n]*`", "", text)


def test_only_the_raw_html_the_site_accepts_is_used () -> None:

	"""Anything else is refused, and so is any attribute on a picture's parts but the ones its form has."""

	text = prose()

	assert set(tag.lower() for tag in re.findall(r"</?([A-Za-z][A-Za-z0-9]*)[\s/>]", text)) <= ALLOWED_TAGS
	assert "&nbsp;" not in text

	for attributes in re.findall(r"<source ([^>]*)>", text):
		assert re.findall(r"([a-z-]+)=", attributes) == ["media", "srcset"], attributes

	for attributes in re.findall(r"<img ([^>]*)>", text):
		assert re.findall(r'(?:^|" )([a-z-]+)=', attributes) == ["src", "alt"], attributes[:40]


def test_every_image_is_a_png_that_is_there () -> None:

	"""The site publishes an app's images only as PNG."""

	text = prose()
	images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text) + re.findall(r'(?:src|srcset)="([^"]+)"', text)

	assert images

	for image in images:
		assert image.startswith("docs/images/") and image.endswith(".png"), image
		assert (ROOT / image).is_file(), image


def test_the_page_picture_is_wide_enough_to_be_sharp_with_clear_sides () -> None:

	"""Twice the site's column across, so each phone in it is sharp, on nothing at its edges."""

	width, _, colours, corner = tests.test_diagrams.png_facts((ROOT / "docs" / "images" / "page.png").read_bytes())

	assert (width, colours, corner) == (1600, 6, 0)


def test_no_link_climbs_out_of_the_repository () -> None:

	"""A relative link with `..` in it means nothing on the site."""

	assert re.findall(r"\]\((\.\.[^)]*)\)", prose()) == []


def test_the_step_headings_are_in_sentence_case_and_contents_match () -> None:

	"""After the colon a step goes on in small letters, and the contents list says the same."""

	text = README.read_text(encoding="utf-8")
	steps = re.findall(r"^## (Step \d: .+)$", text, flags=re.MULTILINE)

	assert len(steps) == 4

	for step in steps:
		assert re.match(r"Step \d: [a-z]", step), step
		assert "- [%s](#" % step in text, step


def test_no_version_is_named_but_micropythons () -> None:

	"""The site blocks a release number it can't account for, examples included. Addresses have four parts and aren't versions."""

	assert set(re.findall(r"(?<![\d.])v?(\d+\.\d+\.\d+)(?![\d.])", prose())) <= ALLOWED_VERSIONS
