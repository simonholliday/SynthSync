"""What gets laid out for a Pico, and what the install tool does with it.

No Pico is needed for these. The tool's talk with the Pico is stood in for,
and what it lays out is checked as files on this machine.
"""

import ast
import base64
import gzip
import importlib.metadata
import pathlib
import re
import subprocess
import time
import typing

import pytest

import synthsync.web.app
import tools.install
import tools.stage

ROOT = pathlib.Path(__file__).parent.parent


def names (folder: pathlib.Path) -> set[str]:

	"""Name every file under a folder, by its path within it."""

	return {str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()}


def sources () -> set[str]:

	"""Name every Python file of SynthSync that a Pico needs, without its ending."""

	found = set()

	for path in (ROOT / "synthsync").rglob("*.py"):

		parts = path.relative_to(ROOT).parts

		if "sim" not in parts and "static" not in parts:
			found.add(str(path.relative_to(ROOT).with_suffix("")))

	return found


@pytest.fixture()
def compiler () -> str:

	"""Give the compiler for the Pico, or skip the test on a machine that has none."""

	found = tools.stage.find_tool("mpy-cross")

	if found is None:
		pytest.skip("this machine has no mpy-cross to compile for the Pico with")

	return found


def test_the_page_is_packed_as_one_file_that_unpacks_to_exactly_the_page (tmp_path: pathlib.Path) -> None:

	"""The Pico holds the page as one gzipped file, the same every time, so a phone opens it with one request."""

	tools.stage.pack_page(tmp_path)
	tools.stage.pack_page(tmp_path / "again")

	folder = tmp_path / "synthsync" / "web" / "static"
	packed = (folder / "index.html.gz").read_bytes()

	assert sorted(path.name for path in folder.iterdir()) == ["index.html.gz"]
	assert gzip.decompress(packed).decode() == tools.stage.one_page()
	assert packed == (tmp_path / "again" / "synthsync" / "web" / "static" / "index.html.gz").read_bytes()


def test_the_packed_page_holds_every_part_of_the_page_and_asks_for_nothing () -> None:

	"""The style sheet and every script are inside the one file, each script's own names kept to itself, and nothing outside it is asked for."""

	page = tools.stage.one_page()
	source = ROOT / "synthsync" / "web" / "static"

	# Nothing left that a browser would fetch: the icon is written into the page itself.
	# A link is only followed if it is tapped, so the licence's don't count.
	assert re.findall(r'(?:src|href)="([^"#][^"]*)"', re.sub(r"<a [^>]*>", "<a>", page)) == ["data:,"]
	assert "import " not in page.split("<script", 1)[1]
	assert page.count("<style>") == 1

	# Two scripts: a few lines in the head that put this device's look on before the page is first drawn,
	# ahead of the style sheet, and the module that is everything else.
	assert page.count("<script") == 2 and page.count("<script>") == 1 and page.count('<script type="module">') == 1
	assert page.index("<script>") < page.index("<style>") < page.index('<script type="module">')

	# Every line of every part is there, apart from how the scripts named one another, and with the fonts inside the style sheet.
	for name in ("page.css", "page.js", "settings.js", "knob.js", "ratio.js"):
		for line in tools.stage.inline_fonts((source / name).read_text(), source).splitlines():
			if line.startswith("import "):
				continue
			assert line.removeprefix("export ") in page, (name, line)

	# Each font is there whole, and the style sheet names no file.
	fonts = [name for name, kind in synthsync.web.app.PAGE_FILES.items() if kind == "font/woff2"]

	assert len(fonts) == 3
	assert re.findall(r"url\((?!\"data:)", page) == []

	for name in fonts:
		assert page.count('url("data:font/woff2;base64,%s")' % base64.b64encode((source / name).read_bytes()).decode()) == 1, name

	# Each module's names stay inside a function of its own, and only what it exports is handed over.
	assert "const {Knob} = (() => {" in page
	assert "const ratio = (() => {" in page
	assert "const {Settings} = (() => {" in page


def test_a_style_sheet_naming_a_file_the_page_cant_carry_is_refused () -> None:

	"""A font that isn't one of the page's files, or any other file, would be asked for by a phone and never come, so the packing says so."""

	source = ROOT / "synthsync" / "web" / "static"

	with pytest.raises(ValueError, match="other.woff2"):
		tools.stage.inline_fonts('src: url("other.woff2");', source)

	with pytest.raises(ValueError, match="can't put inside"):
		tools.stage.inline_fonts("background: url(ground.png);", source)


def test_what_is_laid_out_is_compiled_code_and_a_gzipped_page_and_nothing_else (tmp_path: pathlib.Path, compiler: str) -> None:

	"""The Pico gets SynthSync and Microdot compiled with their licences, the page gzipped, the one file it runs at power-up, and nothing a laptop alone uses."""

	top = tools.stage.stage_for_pico(tmp_path, compiler)
	laid_out = names(tmp_path)

	expected = {name + ".mpy" for name in sources()}
	expected |= {"microdot/" + pathlib.Path(part).stem + ".mpy" for part in tools.stage.MICRODOT_PARTS}
	expected.add("synthsync/web/static/index.html.gz")
	expected.add(tools.stage.MAIN)
	expected |= {"synthsync/" + tools.stage.LICENCE, "microdot/" + tools.stage.LICENCE}

	assert laid_out == expected
	assert sorted(top) == sorted({name.split("/")[0] for name in expected})

	assert not [name for name in laid_out if "test_client" in name or "/sim/" in name or "__pycache__" in name]
	assert [name for name in laid_out if name.endswith(".py")] == [tools.stage.MAIN]

	for name in laid_out:
		if name.endswith(".mpy"):
			assert (tmp_path / name).read_bytes()[:1] == b"M", name

	# A file compiled for one processor says which in its third byte. Bytecode runs on any.
	# The scheduler has to be the first kind: as bytecode it would stumble whenever the Pico tidied its memory.
	scheduler = (tmp_path / "synthsync" / "core" / "timeline.mpy").read_bytes()
	plain = (tmp_path / "synthsync" / "core" / "ratio.mpy").read_bytes()

	assert scheduler[2] >> 2 != 0
	assert plain[2] >> 2 == 0


def test_each_licence_goes_to_the_pico_beside_the_code_it_covers (tmp_path: pathlib.Path) -> None:

	"""SynthSync's own licence goes whole, and so does the MIT notice Microdot has to be copied with."""

	tools.stage.copy_synthsync(tmp_path)
	tools.stage.copy_microdot(tmp_path)
	tools.stage.copy_licences(tmp_path)

	assert (tmp_path / "synthsync" / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()

	microdot = (tmp_path / "microdot" / "LICENSE").read_text()

	assert "Miguel Grinberg" in microdot and "Permission is hereby granted" in microdot


def test_a_copy_is_refused_when_microdots_licence_cant_be_told_apart (tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:

	"""Without its licence Microdot may not be passed on, so finding none stops the layout instead of leaving it out."""

	class Nothing:

		"""A package that lists no files."""

		files: list[pathlib.PurePath] = []

	tools.stage.copy_synthsync(tmp_path)
	tools.stage.copy_microdot(tmp_path)
	monkeypatch.setattr(importlib.metadata, "distribution", lambda name: Nothing())

	with pytest.raises(ValueError, match="Microdot's licence"):
		tools.stage.copy_licences(tmp_path)


def test_power_up_makes_config_txt_before_there_is_a_player_to_protect () -> None:

	"""Making the file writes to flash, so `run` does it before `begin`, which starts the player."""

	source = (ROOT / "synthsync" / "device" / "start.py").read_text()
	run = source[source.index("def run (trial"):]

	assert run.index("make_config()") < run.index("begin()")


def test_every_function_the_second_core_runs_is_native () -> None:

	"""An ordinary function called on the second core waits for the first core's garbage collector, so none may be.

	This reads the player's source. Starting from the function the second
	core runs, it follows every call to one of the player's own methods, and
	each one reached has to be marked native.
	"""

	tree = ast.parse((ROOT / "synthsync" / "device" / "player.py").read_text())
	player = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Player"][0]
	methods = {node.name: node for node in player.body if isinstance(node, ast.FunctionDef)}

	reached = set()
	waiting = ["_feed"]

	while waiting:

		name = waiting.pop()

		if name in reached:
			continue

		reached.add(name)

		for node in ast.walk(methods[name]):

			called = node.func if isinstance(node, ast.Call) else None

			if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name) and called.value.id == "self" and called.attr in methods:
				waiting.append(called.attr)

	# The walk has to have found the ones that matter, or it is checking nothing.
	assert {"_feed", "_take_under_cover", "_take", "_ahead", "_build", "_play_out", "_look", "_act", "_note_state"} <= reached

	for name in sorted(reached):
		assert [ast.unparse(decorator) for decorator in methods[name].decorator_list] == ["micropython.native"], name

	# The pins are read by a function of the module's own, which reads the register itself.
	functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}

	assert [ast.unparse(decorator) for decorator in functions["_levels"].decorator_list] == ["micropython.viper"]

	# And the scheduler and the buttons' rule it calls into are compiled native as a whole.
	assert "synthsync/core/timeline.py" in tools.stage.NATIVE
	assert "synthsync/core/buttons.py" in tools.stage.NATIVE


# The code the second core runs: the player, and the scheduler and the buttons' rule it calls into.
SECOND_CORE = ("synthsync/device/player.py", "synthsync/core/timeline.py", "synthsync/core/buttons.py")


def methods_taken_out (source: str) -> list[str]:

	"""Find every name given an attribute and then called, outside `__init__`, unless the attribute belongs to a module or is one an object keeps.

	`put = self.machine.put` makes a new object, and so does
	`next_word = timeline.next_word` or `take = self._take`. Reading a
	method an object kept when it was made, `put = self._put`, doesn't, and
	nor does reading a module's own, `ticks_ms = time.ticks_ms`.
	"""

	tree = ast.parse(source)
	modules = {alias.asname or alias.name.split(".")[0] for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
	methods = set()

	for node in ast.walk(tree):
		if isinstance(node, ast.ClassDef):
			methods |= {member.name for member in node.body if isinstance(member, ast.FunctionDef)}

	found = []

	for function in ast.walk(tree):

		if not isinstance(function, ast.FunctionDef) or function.name == "__init__":
			continue

		called = {node.func.id for node in ast.walk(function) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}

		for node in ast.walk(function):

			if not (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Attribute)):
				continue

			if node.targets[0].id not in called:
				continue

			root: ast.expr = node.value

			while isinstance(root, ast.Attribute):
				root = root.value

			from_module = isinstance(root, ast.Name) and root.id in modules
			kept = isinstance(node.value.value, ast.Name) and node.value.value.id == "self" and node.value.attr not in methods

			if not (from_module or kept):
				found.append("%s: %s" % (function.name, ast.unparse(node)))

	return found


def test_nothing_the_second_core_runs_takes_a_method_out_under_a_name () -> None:

	"""Taking a method out under a name of its own makes an object, which asks for memory, so the second core's code never does.

	The second core then waits for any collection the first core is part way
	through, and with the page and the network loaded, one can last longer
	than the player's eight words. That ran the player dry, rarely, when
	`_build` did it for every command. The player now takes out what the
	second core calls once, when it is made.
	"""

	for path in SECOND_CORE:
		assert methods_taken_out((ROOT / path).read_text()) == [], path

	# The search finds each way of doing it, and passes over the ways that are safe.
	found = methods_taken_out("import time\n\nclass Player:\n\n\tdef _take (self):\n\t\tpass\n\n\tdef _build (self, timeline):\n\t\tput = self.machine.put\n\t\tnext_word = timeline.next_word\n\t\ttake = self._take\n\t\tkept = self._put\n\t\tticks = time.ticks_us\n\t\tcount = self.machine.count\n\t\tput(next_word())\n\t\ttake()\n\t\tkept(ticks())\n")

	assert found == ["_build: put = self.machine.put", "_build: next_word = timeline.next_word", "_build: take = self._take"]


def own_nodes (function: ast.FunctionDef | ast.AsyncFunctionDef) -> typing.Iterator[ast.AST]:

	"""Give every node of a function's own body, leaving out the functions, classes and lambdas inside it, which have names of their own."""

	nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
	waiting: list[ast.AST] = [node for node in function.body if not isinstance(node, nested)]

	while waiting:

		node = waiting.pop()

		yield node

		waiting.extend(child for child in ast.iter_child_nodes(node) if not isinstance(child, nested))


def used_before_imported (source: str) -> list[str]:

	"""Say where a function uses a name before importing it there.

	An import inside a function makes the name the function's own from its
	first line, so a use above the import fails, on either Python, and only
	when that function runs.
	"""

	found = []

	for function in ast.walk(ast.parse(source)):

		if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
			continue

		imported: dict[str, int] = {}
		used: dict[str, int] = {}

		for node in own_nodes(function):

			if isinstance(node, (ast.Import, ast.ImportFrom)):

				for alias in node.names:
					name = alias.asname or alias.name.split(".")[0]
					imported[name] = min(imported.get(name, node.lineno), node.lineno)

			elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
				used[node.id] = min(used.get(node.id, node.lineno), node.lineno)

		for name, line in imported.items():
			if name in used and used[name] < line:
				found.append("%s uses %s on line %d and imports it on line %d" % (function.name, name, used[name], line))

	return found


def test_no_function_uses_a_name_before_importing_it_there () -> None:

	"""Nothing uses a package and then imports part of it further down the same function, which fails only when the function runs: on the board, that may be hours into a run."""

	assert used_before_imported("def measure ():\n\tpin = synthsync.board.PIN\n\timport synthsync.config\n") == ["measure uses synthsync on line 2 and imports it on line 3"]
	assert used_before_imported("def measure ():\n\timport synthsync.config\n\tpin = synthsync.board.PIN\n") == []

	found = []

	for path in sorted([ROOT / "main.py"] + list((ROOT / "synthsync").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))):
		found += ["%s: %s" % (path.relative_to(ROOT), place) for place in used_before_imported(path.read_text())]

	assert found == []


def test_everything_asked_of_the_radio_chip_goes_through_the_guard () -> None:

	"""An interrupt that lands while the radio chip is being worked leaves it half started, so nothing may work it unguarded.

	This reads the source of the two files that use the chip. Whatever
	switches a network, joins, leaves, listens or sets the Pico's own light
	has to be handed to `synthsync.device.chip.unbroken`, never called
	straight.
	"""

	def guarded (tree: ast.Module) -> set[str]:

		"""Name what is handed to the guard: the first thing in each call to it."""

		return {ast.unparse(node.args[0]) for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "synthsync.device.chip.unbroken"}

	def called_in (tree: ast.Module, working: set[str], asking: set[str] = set()) -> dict[str, set[str]]:

		"""Give, for each function, the calls it makes that work the chip.

		A call named in `asking` only works the chip when it is given
		something: with nothing, it asks how things stand.
		"""

		found: dict[str, set[str]] = {}

		for function in ast.walk(tree):

			if not isinstance(function, ast.FunctionDef):
				continue

			for node in ast.walk(function):

				if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in working):
					continue

				if node.func.attr not in asking or node.args or node.keywords:
					found.setdefault(function.name, set()).add(ast.unparse(node.func))

		return found

	# The radio: switching a side on or off, naming a network, joining, leaving and listening.
	radio = ast.parse((ROOT / "synthsync" / "device" / "radio.py").read_text())
	handed = guarded(radio)
	working = called_in(radio, {"active", "config", "connect", "disconnect", "scan"}, {"active"})

	assert sorted(working) == ["_listen", "_start_join", "_start_own", "_stop_join"]
	assert {"self." + name for name in working} <= handed
	assert "self._own.active" in handed

	# None of those four is ever called straight.
	for node in ast.walk(radio):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
			assert node.func.attr not in working, ast.unparse(node)

	# The panel: taking hold of the Pico's own light, and setting it.
	panel = ast.parse((ROOT / "synthsync" / "device" / "panel.py").read_text())
	handed = guarded(panel)

	assert handed == {"self._find_light", "self._light.value"}
	assert called_in(panel, {"value"}, {"value"}) == {}
	assert [function.name for function in ast.walk(panel) if isinstance(function, ast.FunctionDef) and "'LED'" in ast.unparse(function)] == ["_find_light"]

	for node in ast.walk(panel):
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
			assert node.func.attr != "_find_light", ast.unparse(node)


def test_nothing_but_the_clock_is_loaded_before_the_first_pulse () -> None:

	"""The buttons, the network and the page are loaded after the clock has started, so none of them can hold it up.

	This reads the source of what runs at power-up. Loading Microdot and the
	page's code takes the Pico a good part of a second, which would otherwise
	come before the first pulse. What reads config.txt comes first, because it
	says how many outputs the clock has.
	"""

	tree = ast.parse((ROOT / "synthsync" / "device" / "start.py").read_text())
	at_once = {alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
	later = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} - at_once

	assert at_once == {
		"asyncio", "gc", "sys", "time",
		"synthsync.core.clock", "synthsync.core.config", "synthsync.core.settings", "synthsync.core.transport",
		"synthsync.device.board", "synthsync.device.chip", "synthsync.device.player", "synthsync.device.storage",
	}
	assert later == {"synthsync.device.panel", "synthsync.device.radio", "synthsync.web.app", "synthsync.web.portal"}

	# And the file the Pico runs at power-up loads nothing else.
	main = ast.parse((ROOT / "main.py").read_text())

	assert {alias.name for node in ast.walk(main) if isinstance(node, ast.Import) for alias in node.names} == {"synthsync.device.start"}


def test_the_modules_the_pico_is_asked_to_load_are_all_of_synthsync (tmp_path: pathlib.Path) -> None:

	"""Every part of SynthSync is named for the Pico to load, compiled or not, and nothing else is."""

	tools.stage.copy_synthsync(tmp_path)

	found = tools.install.modules(tmp_path)

	assert "synthsync.core.timeline" in found
	assert "synthsync.device.player" in found
	assert "synthsync.web.app" in found
	assert sorted(found) == sorted(name.replace("/", ".") for name in sources() if not name.endswith("__init__"))


def test_a_pico_that_has_just_been_restarted_is_waited_for (monkeypatch: pytest.MonkeyPatch) -> None:

	"""For a second or two after a restart the Pico isn't on its USB port, and a tool run straight after another must not fail for that."""

	answers = [
		subprocess.CompletedProcess([], 1, "", "mpremote: failed to access /dev/one (it may be in use by another program)\n"),
		subprocess.CompletedProcess([], 1, "", "mpremote: no device found\n"),
		subprocess.CompletedProcess([], 0, "LOADED 1\n", ""),
	]
	asked: list[list[str]] = []
	waits: list[float] = []

	def run (command: list[str], **options: object) -> "subprocess.CompletedProcess[str]":

		"""Answer each command with the next answer in the list, and note what was asked."""

		asked.append(command)

		return answers[len(asked) - 1]

	monkeypatch.setattr(subprocess, "run", run)
	monkeypatch.setattr(time, "sleep", waits.append)

	assert tools.install.on_pico("/dev/one", ["exec", "pass"]) == (True, "LOADED 1\n")
	assert len(asked) == 3 and len(waits) == 2
	assert asked[0][-4:] == ["/dev/one", "soft-reset", "exec", "pass"]

	# Anything else that goes wrong is said at once, and a Pico that never comes back is given up on.
	answers[:] = [subprocess.CompletedProcess([], 1, "", "Traceback: boom\n")]
	asked.clear()

	assert tools.install.on_pico("/dev/one", ["exec", "pass"]) == (False, "Traceback: boom\n")
	assert len(asked) == 1

	answers[:] = [subprocess.CompletedProcess([], 1, "", "mpremote: no device found\n")] * tools.install.ASK_TIMES
	asked.clear()

	assert tools.install.on_pico("/dev/one", ["exec", "pass"])[0] is False
	assert len(asked) == tools.install.ASK_TIMES


class Pico:

	"""Stands in for the Pico: it notes what it was asked, and answers as told to."""

	def __init__ (self, fail: str | None = None, loads: bool = True, ran: str = "SYNTHSYNC RAN started_in_ms=41 beat=7 faults=0 refused=0 page=1 free=312000 mode=own address=192.168.4.1 host=synthsync own=SynthSync AB78D9\n") -> None:

		"""Answer well to everything, bar a command containing `fail`, or the loading if `loads` is false.

		`ran` is what comes back when SynthSync is tried.
		"""

		self.fail = fail
		self.loads = loads
		self.ran = ran
		self.asked: list[list[str]] = []
		self.copied: set[str] = set()

	def __call__ (self, device: str, arguments: list[str], folder: pathlib.Path | None = None, timeout: int = 180) -> tuple[bool, str]:

		"""Take one set of commands, as `tools.install.on_pico` does."""

		self.asked.append(arguments)

		if self.fail is not None and self.fail in " ".join(arguments):
			return False, "boom"

		if arguments[0] == "fs" and folder is not None:
			self.copied = names(folder)
			return True, ""

		if arguments[0] == "exec" and "REMOVED" in arguments[1]:
			return True, "REMOVED\n"

		if arguments[0] == "exec" and "start.run" in arguments[1]:
			return True, self.ran

		if arguments[0] == "exec":
			return True, "LOADED 400000\n" if self.loads else "ImportError: incompatible .mpy file\n"

		return True, ""


@pytest.mark.usefixtures("compiler")
def test_an_install_takes_the_old_copy_off_copies_the_new_tries_it_and_only_then_lets_it_start_by_itself (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""The Pico's saved settings are left alone, and what starts SynthSync at power-up goes on last, once the rest has run."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 0

	removing, copying, loading, trying, starting, restarting = pico.asked

	# The join tool's file goes too, in case anything ever left it behind with a network's password in it,
	# and so does the folder a copy from before the name SynthSync used.
	assert removing[0] == "exec" and "['synthsync', 'microdot', 'main.py', '_join.txt', 'picoclock']" in removing[1]
	assert copying[:3] == ["fs", "cp", "-r"] and copying[-1] == ":"
	assert sorted(copying[3:-1]) == ["microdot", "synthsync"]
	assert "synthsync.core.timeline" in loading[1] and "synthsync.device.start" in loading[1]
	assert "synthsync.device.start.run(3000)" in trying[1]
	assert starting == ["fs", "cp", "main.py", ":"]
	assert restarting == ["reset"]

	assert "synthsync/web/static/index.html.gz" in pico.copied

	said = capsys.readouterr().out

	assert "/dev/one" in said
	assert "Every part loads" in said
	assert "41 ms" in said
	assert "304 KB" in said

	# It ends by saying how to reach the page, and never says a password that may have been changed.
	assert said.strip().splitlines()[-1].startswith('To use it, join the WiFi network "SynthSync AB78D9" from a phone and open http://192.168.4.1.')


@pytest.mark.usefixtures("compiler")
@pytest.mark.parametrize("ran", [
	"SYNTHSYNC RAN started_in_ms=41 beat=7 faults=3 refused=0 page=1 free=312000 mode=own address=192.168.4.1 host=synthsync own=SynthSync AB78D9\n",
	"SYNTHSYNC RAN started_in_ms=41 beat=0 faults=0 refused=0 page=1 free=312000 mode=own address=192.168.4.1 host=synthsync own=SynthSync AB78D9\n",
	"SYNTHSYNC RAN started_in_ms=41 beat=7 faults=0 refused=0 page=0 free=312000 mode=own address=192.168.4.1 host=synthsync own=SynthSync AB78D9\n",
	"SYNTHSYNC RAN started_in_ms=41 beat=7 faults=0 refused=0 page=1 free=312000 mode=own address=none host=synthsync own=SynthSync AB78D9\n",
	"SYNTHSYNC RAN started_in_ms=41 beat=7 faults=0 refused=0\n",
	"Traceback (most recent call last):\nValueError\n",
	"",
])
def test_a_copy_that_does_not_run_when_tried_is_never_left_to_start_by_itself (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ran: str) -> None:

	"""If the trial faults, never reaches a beat, has no page or no network, fails or says nothing, `main.py` stays off the Pico, so it can still be reached."""

	pico = Pico(ran=ran)

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 1
	assert not [asked for asked in pico.asked if "main.py" in asked or asked == ["reset"]]
	assert "unplug" in capsys.readouterr().out


@pytest.mark.usefixtures("compiler")
def test_a_pico_with_a_network_saved_passes_its_trial_while_still_joining (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""Three seconds isn't long enough to join a network, so having no address yet isn't held against it, and the network is never named."""

	pico = Pico(ran="SYNTHSYNC RAN started_in_ms=41 beat=7 faults=0 refused=0 page=1 free=312000 mode=joining address=none host=studio-clock own=SynthSync AB78D9\n")

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 0
	assert ["fs", "cp", "main.py", ":"] in pico.asked

	last = capsys.readouterr().out.strip().splitlines()[-1]

	assert "http://studio-clock.local" in last
	assert '"SynthSync AB78D9"' in last and "http://192.168.4.1" in last


@pytest.mark.usefixtures("compiler")
def test_an_install_told_not_to_start_leaves_the_pico_as_it_is (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""`--no-start` copies everything and doesn't restart the Pico."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main(["--no-start"]) == 0
	assert ["fs", "cp", "main.py", ":"] in pico.asked
	assert ["reset"] not in pico.asked

	said = capsys.readouterr().out

	assert "next time" in said
	assert "SynthSync AB78D9" in said


@pytest.mark.usefixtures("compiler")
def test_wiping_empties_the_pico_first (monkeypatch: pytest.MonkeyPatch) -> None:

	"""`--wipe` names nothing to take off, which takes off everything, saved settings included."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main(["--wipe"]) == 0
	assert "for name in [] or os.listdir" in pico.asked[0][1]


@pytest.mark.usefixtures("compiler")
def test_an_install_never_touches_config_txt (monkeypatch: pytest.MonkeyPatch) -> None:

	"""config.txt is the musician's, so nothing an install asks of the Pico names it, and the top of the copy holds none."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 0
	assert not [asked for asked in pico.asked if "config.txt" in " ".join(asked)]
	assert "config.txt" not in pico.copied


@pytest.mark.usefixtures("compiler")
def test_an_install_says_what_in_config_txt_the_pico_could_not_use (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""What the trial printed about config.txt is passed on, as the page would say it."""

	pico = Pico(ran="config.txt, line 2: outputs has to be a whole number from 1 to 8\n" + Pico().ran)

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 0
	assert "config.txt, line 2: outputs has to be a whole number from 1 to 8" in capsys.readouterr().out


def test_removing_takes_synthsync_off_and_copies_nothing (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""`--remove` takes off SynthSync's own files, any file the join tool left, and a copy from before the name SynthSync, and stops there. The saved settings and config.txt stay."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main(["--remove"]) == 0
	assert len(pico.asked) == 1
	assert "['synthsync', 'microdot', 'main.py', '_join.txt', 'picoclock']" in pico.asked[0][1]

	said = capsys.readouterr().out

	assert "settings" in said and "config.txt" in said


@pytest.mark.usefixtures("compiler")
@pytest.mark.parametrize("fail, old_copy_gone", [("REMOVED", False), ("cp", True)])
def test_a_step_the_pico_refuses_stops_the_install_and_says_so (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fail: str, old_copy_gone: bool) -> None:

	"""If the old copy can't be taken off or the new one can't be copied, the tool fails and shows what the Pico said, and says when nothing will start at power-up now."""

	pico = Pico(fail=fail)

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)

	assert tools.install.main([]) == 1

	said = capsys.readouterr().out

	assert "boom" in said
	assert (tools.install.NOTHING_STARTS in said) == old_copy_gone


@pytest.mark.usefixtures("compiler")
def test_a_copy_the_pico_cannot_load_fails_the_install (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""A copy that went across but won't load is a failure, found now and not at the next power-up, and the old copy is gone, so nothing starts."""

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", Pico(loads=False))

	assert tools.install.main([]) == 1

	said = capsys.readouterr().out

	assert "incompatible" in said
	assert tools.install.NOTHING_STARTS in said


def test_without_a_compiler_nothing_is_installed (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""SynthSync left for the Pico to compile would stumble whenever the Pico tidied its memory, so the tool stops before touching it."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)
	monkeypatch.setattr(tools.stage, "find_tool", lambda name: None)

	assert tools.install.main([]) == 1
	assert pico.asked == []
	assert "mpy-cross" in capsys.readouterr().out


@pytest.mark.usefixtures("compiler")
def test_no_pico_or_more_than_one_stops_before_anything_is_changed (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""With nothing plugged in, or two and neither named, nothing is touched. Naming one goes ahead."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "on_pico", pico)
	monkeypatch.setattr(tools.install, "find_devices", lambda: [])

	assert tools.install.main([]) == 1
	assert "No Pico" in capsys.readouterr().out

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one", "/dev/two"])

	assert tools.install.main([]) == 1
	assert "/dev/two" in capsys.readouterr().out
	assert pico.asked == []

	assert tools.install.main(["--device", "/dev/two"]) == 0
	assert "/dev/two" in capsys.readouterr().out
