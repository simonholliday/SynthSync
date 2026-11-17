"""The page's script and the API agree on every message, by reading the script.

The API lists what a page may send and what it is sent. This reads the page's
own files and holds them to those lists, so neither can change alone.
"""

import pathlib
import re

import synthsync.web.app

STATIC = pathlib.Path(__file__).parent.parent / "synthsync" / "web" / "static"


def scripts () -> str:

	"""Give all of the page's JavaScript as one text."""

	return "\n".join(path.read_text() for path in sorted(STATIC.glob("*.js")))


def sent () -> dict[str, set[str]]:

	"""Find every message the page builds: what it does, and what else it carries."""

	found: dict[str, set[str]] = {}

	for literal in re.findall(r"\{\s*do: ([^{}]*)\}", scripts()):

		parts = [part.strip() for part in literal.split(",") if part.strip()]
		do = parts[0]
		carried = {part.split(":")[0].strip() for part in parts[1:]}

		if "?" in do:
			# Play/pause sends one of two, chosen as it is tapped: each is a message of its own.
			for name in re.findall(r'"(\w+)"', do.split("?", 1)[1]):
				found.setdefault(name, set()).update(carried)

		else:
			assert do.startswith('"') and do.endswith('"'), do
			found.setdefault(do.strip('"'), set()).update(carried)

	return found


def test_the_page_sends_only_what_the_api_takes_and_all_of_it () -> None:

	"""Every message the page builds is in the API's list with nothing extra in it, and no listed message is left unused."""

	found = sent()

	assert sorted(found) == sorted(synthsync.web.app.FROM_PAGE)

	for do, carried in found.items():
		assert carried <= set(synthsync.web.app.FROM_PAGE[do]), (do, carried)


def test_the_page_takes_every_kind_of_message_the_api_sends () -> None:

	"""Each type the API can send is looked for somewhere in the page, and the page looks for no type that doesn't exist."""

	looked_for = set(re.findall(r'\.type === "(\w+)"', scripts()))

	assert looked_for == set(synthsync.web.app.TO_PAGE)


def test_the_page_reads_only_what_the_api_puts_in_each_message () -> None:

	"""Whatever the page reads off a message is something the API's list says that message carries."""

	carried = {"type"}

	for names in synthsync.web.app.TO_PAGE.values():
		carried.update(names)

	read = set(re.findall(r"\bmessage\.(\w+)", scripts()))

	assert read <= carried, read - carried

	# And what it reads off the things that never change is in that list too.
	fixed = set(re.findall(r"\bfixed\.(\w+)", scripts()))

	assert fixed <= set(synthsync.web.app.FIXED), fixed - set(synthsync.web.app.FIXED)


def test_the_page_takes_messages_in_batches_and_answers_each_with_its_number () -> None:

	"""The page reads off a batch exactly what the API puts in one, and says it has heard it."""

	read = set(re.findall(r"\bbatch\.(\w+)", scripts()))

	assert read == set(synthsync.web.app.BATCH)

	# The answer carries the number the batch came with.
	assert re.search(r'\{do: "heard", batch: batch\.batch\}', scripts())


def test_every_file_the_page_asks_for_is_one_the_app_sends () -> None:

	"""The page's files are exactly the ones the app is willing to send."""

	assert sorted(path.name for path in STATIC.iterdir() if path.is_file()) == sorted(synthsync.web.app.PAGE_FILES)

	index = (STATIC / "index.html").read_text()
	asked = set(re.findall(r'(?:href|src)="([\w.]+)"', index)) | set(re.findall(r'from "\./([\w.]+)"', scripts()))

	assert asked <= set(synthsync.web.app.PAGE_FILES), asked
