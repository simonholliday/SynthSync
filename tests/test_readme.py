"""The README stays something the project's documentation site can publish as it is, as well as GitHub."""

import pathlib
import re
import struct
import zlib

ROOT = pathlib.Path(__file__).parent.parent
README = ROOT / "README.md"

# The raw tags the site lets through, and the three that make up a picture for light and dark pages.
ALLOWED_TAGS = {"abbr", "br", "details", "kbd", "sub", "summary", "sup", "picture", "source", "img"}

# The README's two pictures that aren't drawings: the page, and a photograph of the board built.
PAGE_PICTURE = "docs/images/page.png"
PHOTO = "docs/images/breadboard.jpg"

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


def test_every_image_is_a_drawing_the_picture_of_the_page_or_the_photograph_and_is_there () -> None:

	"""The diagrams are SVG, and the only other pictures are the page's and the photograph of the board."""

	text = prose()
	images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text) + re.findall(r'(?:src|srcset)="([^"]+)"', text)

	assert PAGE_PICTURE in images and PHOTO in images

	for image in images:
		assert image.startswith("docs/images/") and (image.endswith(".svg") or image in (PAGE_PICTURE, PHOTO)), image
		assert (ROOT / image).is_file(), image


def test_the_picture_of_the_page_is_the_only_png () -> None:

	"""The diagrams' files are the SVGs alone, so there is nothing to fall out of step with them."""

	assert [path.name for path in (ROOT / "docs" / "images").glob("*.png")] == ["page.png"]


def test_the_page_picture_is_wide_enough_to_be_sharp_with_clear_sides () -> None:

	"""Twice the site's column across, so each phone in it is sharp, on nothing at its edges."""

	png = (ROOT / PAGE_PICTURE).read_bytes()
	place = 8
	pixels = b""

	while place < len(png):

		length = struct.unpack(">I", png[place:place + 4])[0]

		if png[place + 4:place + 8] == b"IDAT":
			pixels += png[place + 8:place + 8 + length]

		place += length + 12

	# The first pixel of the first row has nothing before it to be stored against, so it is there as it is, after the row's filter byte.
	assert struct.unpack(">I", png[16:20])[0] == 1600
	assert png[25] == 6
	assert zlib.decompress(pixels)[4] == 0


def test_the_photograph_is_the_only_jpeg_sharp_light_and_with_nothing_hidden_in_it () -> None:

	"""Every reader downloads it and the repository keeps it for good, so it is small, and carries no camera's notes, such as where it was taken."""

	assert [path.name for path in (ROOT / "docs" / "images").iterdir() if path.suffix.lower() in (".jpg", ".jpeg")] == ["breadboard.jpg"]

	jpeg = (ROOT / PHOTO).read_bytes()

	assert jpeg[:2] == b"\xff\xd8"
	assert len(jpeg) < 500_000

	# Each part before the picture itself says what it holds: a camera's notes, Exif or XMP, are APP1 (E1), a comment is COM (FE), and the size is in the frame (C0).
	place = 2
	kinds = []
	width = 0

	while jpeg[place] == 0xFF and jpeg[place + 1] != 0xDA:

		kind = jpeg[place + 1]
		kinds.append(kind)

		if kind == 0xC0:
			width = struct.unpack(">H", jpeg[place + 7:place + 9])[0]

		place += 2 + struct.unpack(">H", jpeg[place + 2:place + 4])[0]

	assert 0xE1 not in kinds and 0xFE not in kinds

	# Twice the site's column across, as the page's picture is, so it is sharp on any screen.
	assert width >= 1600


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
