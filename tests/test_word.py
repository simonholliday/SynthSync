"""The second core's word maker gives every word `Timeline.next_word` gives, and keeps the timeline's numbers as it does.

`synthsync/device/word.py` is viper, which only MicroPython runs, so the two are
compared under the pinned MicroPython, where viper becomes this machine's code.
The rig's `word` test compares them on the Pico too, where it becomes Arm code.
"""

import ast
import pathlib
import subprocess

import pytest

import synthsync.core.timeline
import tests.test_checks

ROOT = pathlib.Path(__file__).parent.parent
WORD = ROOT / "synthsync" / "device" / "word.py"

# The word maker's own constants that aren't places in the table, and the timeline's names for them.
TWINS = {
	"_HEAD": "TABLE_HEAD",
	"_HOLD_LIMIT": "HOLD_LIMIT",
	"_LED_BIT": "LED_BIT",
	"_MIDI_BIT": "MIDI_BIT",
	"_MIDI_BIT_TICKS": "MIDI_BIT_TICKS",
	"_MIDI_BYTE_TICKS": "MIDI_BYTE_TICKS",
	"_MIDI_IDLE_HOLD": "MIDI_IDLE_HOLD",
	"_MIDI_CLOCK": "MIDI_CLOCK",
}

# What the comparison must have got through, and on which MicroPython.
SEEDS = 300
STEPS = 500


def constants () -> dict[str, int]:

	"""Give every constant the word maker writes out, by name."""

	found = {}

	for node in ast.parse(WORD.read_text()).body:

		if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "const":

			value = node.value.args[0]
			assert isinstance(value, ast.Constant) and isinstance(value.value, int)
			found[node.targets[0].id] = value.value  # type: ignore[attr-defined]

	return found


def maker () -> ast.FunctionDef:

	"""Give the word maker's function as written."""

	found = [node for node in ast.parse(WORD.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "next_word"]

	assert len(found) == 1

	return found[0]


def test_every_number_the_word_maker_uses_is_the_timelines () -> None:

	"""Each place in the table and each of the timeline's numbers that the word maker writes out is the timeline's own, and none is missing."""

	timeline = synthsync.core.timeline
	wanted = {}

	for name in dir(timeline):

		if name.startswith("CELL_"):
			wanted["_" + name[len("CELL_"):]] = getattr(timeline, name)

		elif name.startswith("ROW_"):
			wanted["_" + name] = getattr(timeline, name)

	for name, twin in TWINS.items():
		wanted[name] = getattr(timeline, twin)

	assert constants() == wanted


def test_the_table_has_room_for_what_comes_before_its_rows () -> None:

	"""MIDI's waiting bytes end before the first row begins."""

	timeline = synthsync.core.timeline

	assert timeline.CELL_MIDI_WAITING + 4 <= timeline.TABLE_HEAD
	assert max(getattr(timeline, name) for name in dir(timeline) if name.startswith("CELL_")) < timeline.TABLE_HEAD


def test_the_word_maker_reads_nothing_but_its_table () -> None:

	"""It is viper, takes the table and nothing else, and looks up no name but its constants, `int` and the sign's shift, so it asks for no memory and no lookup can slow it."""

	function = maker()

	assert [ast.unparse(decorator) for decorator in function.decorator_list] == ["micropython.viper"]
	assert [(argument.arg, ast.unparse(argument.annotation)) for argument in function.args.args if argument.annotation is not None] == [("table", "ptr32")]
	assert len(function.args.args) == 1

	body = [node for statement in function.body for node in ast.walk(statement)]
	assigned = {node.id for node in body if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)}
	read = {node.id for node in body if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}

	assert read - assigned - set(constants()) == {"table", "int", "_SIGN_SHIFT"}
	assert not [node for node in body if isinstance(node, ast.Attribute)]


def test_the_word_maker_imports_nothing_of_synthsyncs () -> None:

	"""Loading it brings in nothing that could move the names the second core uses."""

	imported: set[str] = set()

	for node in ast.walk(ast.parse(WORD.read_text())):

		if isinstance(node, ast.Import):
			imported.update(alias.name for alias in node.names)

		elif isinstance(node, ast.ImportFrom):
			imported.add(node.module or "")

	assert imported == {"micropython", "sys"}


def test_the_word_maker_makes_every_word_next_word_makes () -> None:

	"""Through 150,000 random changes on one to eight outputs, a third of them in the widest tempo range, the word maker gives every word `next_word` gives and leaves every number the same. A skip here is worth noticing."""

	micropython = tests.test_checks.find_micropython()

	if micropython is None:
		pytest.skip("this machine has no MicroPython to run viper on")

	code = "import sys; sys.path.append('tools/board'); import rig_word; rig_word.compare(%d, %d)" % (SEEDS, STEPS)
	result = subprocess.run([micropython, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=600)

	assert result.returncode == 0, result.stdout + result.stderr
	assert "WORD the same through %d changes" % (SEEDS * STEPS) in result.stdout, result.stdout
	assert result.stdout.strip().endswith("on micropython %s" % tests.test_checks.PINNED), result.stdout
