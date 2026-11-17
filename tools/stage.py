"""Lay out what a Pico needs to run SynthSync, in a folder on local disk.

The install tool, the timing rig and the tests each need SynthSync and
Microdot in a shape MicroPython can import. This is the one place that shape
is written down, so the three can't drift apart.

Nothing here writes anywhere but the folder it is given.
"""

import gzip
import pathlib
import re
import shutil
import subprocess
import sys

import microdot

import synthsync.web.app

ROOT = pathlib.Path(__file__).parent.parent

# The parts of Microdot SynthSync uses, which are all the Pico gets, and the
# part only the tests use.
MICRODOT_PARTS = ("__init__.py", "microdot.py", "websocket.py", "helpers.py")
MICRODOT_TEST_CLIENT = "test_client.py"

# The Pico 2's processor, as the compiler names it.
PROCESSOR = "armv7emsp"

# What is compiled to the processor's own instructions and not to bytecode.
# The scheduler works out every pulse on the Pico's second core, and the
# second core reads the buttons by the rule in `buttons`. Compiled this way the
# scheduler runs about half as fast again, and, which matters more, none of
# their functions takes memory to be called. One that did would wait whenever
# the first core was tidying its memory, for longer than the player's words last.
NATIVE = ("synthsync/core/timeline.py", "synthsync/core/buttons.py")

# What to say when there is no compiler, since SynthSync can't safely run without one.
NO_COMPILER = "mpy-cross wasn't found. SynthSync's scheduler has to be compiled for the Pico's processor: left for the Pico to compile, the clock would stumble every time the Pico tidied its memory."

# The folders of SynthSync a Pico has no use for: the laptop stand-in, and the
# page's files, which go gzipped.
NOT_FOR_THE_PICO = ("__pycache__", "sim", "static")

# The program a Pico runs when it is switched on, once there is one.
MAIN = "main.py"

# The file that says how many outputs are wired, as SynthSync comes with it. It
# is the musician's to change, so it is never copied over one already there.
CONFIG = "config.txt"


def find_tool (name: str) -> str | None:

	"""Find a program beside the Python this is running on, or on the PATH."""

	beside = pathlib.Path(sys.executable).parent / name

	if beside.exists():
		return str(beside)

	return shutil.which(name)


def copy_synthsync (folder: pathlib.Path) -> None:

	"""Copy SynthSync's own code into a folder, without what only a laptop uses."""

	shutil.copytree(ROOT / "synthsync", folder / "synthsync", ignore=shutil.ignore_patterns(*NOT_FOR_THE_PICO))


def copy_microdot (folder: pathlib.Path, test_client: bool = False) -> None:

	"""Copy the parts of Microdot SynthSync uses into a folder MicroPython can import them from.

	MicroPython can't be pointed at this Python's own packages: it would find
	the laptop's versions of things it has its own of.
	"""

	source = pathlib.Path(microdot.__file__).parent
	(folder / "microdot").mkdir()

	for part in MICRODOT_PARTS + ((MICRODOT_TEST_CLIENT,) if test_client else ()):
		shutil.copy(source / part, folder / "microdot" / part)


def compile_one (compiler: str, folder: pathlib.Path, name: str) -> None:

	"""Compile one file in a folder for the Pico, and remove its source.

	`name` is the file's path within the folder. It is also the name an error
	on the Pico gives for the file, so it reads the same there as here.
	"""

	source = folder / name
	command = [compiler, "-march=" + PROCESSOR, "-s", name, "-o", str(source.with_suffix(".mpy"))]

	if name in NATIVE:
		command += ["-X", "emit=native"]

	subprocess.run(command + [str(source)], check=True)
	source.unlink()


def compile_scheduler (folder: pathlib.Path, compiler: str) -> None:

	"""Compile only the scheduler, leaving the rest as source for the Pico to compile as it loads."""

	for name in NATIVE:
		compile_one(compiler, folder, name)


def compile_all (folder: pathlib.Path, compiler: str) -> None:

	"""Compile every Python file in a folder but the one the Pico runs at power-up.

	A compiled file loads faster and costs the Pico far less memory than one
	it has to compile itself.
	"""

	for source in sorted(folder.rglob("*.py")):

		name = str(source.relative_to(folder))

		if name != MAIN:
			compile_one(compiler, folder, name)


# How the page's own files are named inside `index.html` and `page.js`, which
# is how `one_page` finds what to put in their place.
_STYLE_SHEET = '<link rel="stylesheet" href="page.css">'
_SCRIPT = '<script type="module" src="page.js"></script>'
_IMPORT_NAMES = re.compile(r'^import \{([A-Za-z, ]+)\} from "\./([a-z]+)\.js";$', re.MULTILINE)
_IMPORT_ALL = re.compile(r'^import \* as ([A-Za-z]+) from "\./([a-z]+)\.js";$', re.MULTILINE)
_EXPORT = re.compile(r'^export (?:class|function|const|let) ([A-Za-z]+)', re.MULTILINE)


def one_page () -> str:

	"""Write the page as one file: `index.html`, with the style sheet and every script inside it.

	A phone then opens the page with one request over one connection, where
	the six files take six. The scripts are modules: each one `page.js`
	imports is wrapped in a function of its own, so its names stay its own,
	and hands over what it exports. The page's source stays as six plain
	files, which the laptop stand-in serves as they are.
	"""

	source = ROOT / "synthsync" / "web" / "static"
	page = (source / "index.html").read_text()
	style = (source / "page.css").read_text()
	main = (source / "page.js").read_text()
	parts = []

	imports = [(names, module, False) for names, module in _IMPORT_NAMES.findall(main)]
	imports += [(name, module, True) for name, module in _IMPORT_ALL.findall(main)]

	for names, module, whole in sorted(imports, key=lambda each: main.index('"./%s.js"' % each[1])):

		script = (source / (module + ".js")).read_text()
		exported = _EXPORT.findall(script)
		inside = _EXPORT.sub(lambda found: found.group(0)[len("export "):], script)

		# What is put inside the page has to stand alone, and mustn't end the script it is put in.
		if "import " in inside or re.search(r"^export ", inside, re.MULTILINE) or "</script" in inside:
			raise ValueError("%s.js can't be put inside the page as it is" % module)

		given = ("const %s = " % names) if whole else ("const {%s} = " % names)
		parts.append("%s(() => {\n%s\nreturn {%s};\n})();\n" % (given, inside, ", ".join(exported)))

	rest = _IMPORT_ALL.sub("", _IMPORT_NAMES.sub("", main))

	if "import " in rest or "</script" in rest or "</style" in style:
		raise ValueError("page.js or page.css can't be put inside the page as it is")

	parts.append(rest)

	if page.count(_STYLE_SHEET) != 1 or page.count(_SCRIPT) != 1:
		raise ValueError("index.html doesn't name page.css and page.js as one_page expects")

	page = page.replace(_STYLE_SHEET, "<style>\n%s</style>" % style)

	return page.replace(_SCRIPT, '<script type="module">\n%s</script>' % "\n".join(parts))


def pack_page (folder: pathlib.Path) -> None:

	"""Put the page into a folder as the Pico holds it: one gzipped file, where the app looks for it.

	The app sends a gzipped file as it is, so the Pico never holds the plain
	one. The time stamp is left out so the same page always packs to the
	same bytes, and a page that hasn't changed isn't copied again.
	"""

	target = folder / "synthsync" / "web" / "static"

	target.mkdir(parents=True)

	(target / "index.html.gz").write_bytes(gzip.compress(one_page().encode(), 9, mtime=0))


def stage_for_pico (folder: pathlib.Path, compiler: str) -> list[str]:

	"""Lay out everything a Pico needs to run SynthSync, and name what is at the top of it.

	That is SynthSync's code and Microdot, compiled, the page gzipped, and
	the program that runs at power-up.
	"""

	copy_synthsync(folder)
	copy_microdot(folder)

	top = ["synthsync", "microdot"]

	if (ROOT / MAIN).exists():
		shutil.copy(ROOT / MAIN, folder / MAIN)
		top.append(MAIN)

	compile_all(folder, compiler)
	pack_page(folder)

	return top
