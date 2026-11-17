"""The tool that gives a Pico a WiFi network does it in the right order, and never shows what is in the file.

The Pico is stood in for here. The real thing is tried by hand, against a
network that doesn't exist and, where there is one to join, one that does.
"""

import ast
import pathlib

import pytest

import tools.install
import tools.join
import tools.stage

# What the file holds. Neither may ever be printed or put on a command line.
NAME = "Hidden Valley Studio"
PASSWORD = "s3cret-pass-phrase"


class Pico:

	"""Stands in for the Pico: it notes what it was asked, and answers the join as told to."""

	def __init__ (self, says: str = "JOINED 192.168.1.23 picoclock 4200\n", fail: str | None = None) -> None:

		"""Answer the join with `says`, and fail any command containing `fail`."""

		self.says = says
		self.fail = fail
		self.asked: list[list[str]] = []

	def __call__ (self, device: str, arguments: list[str], folder: pathlib.Path | None = None, timeout: int = 180) -> tuple[bool, str]:

		"""Take one set of commands, as `tools.install.on_pico` does."""

		self.asked.append(arguments)

		if self.fail is not None and self.fail in " ".join(arguments):
			return False, "boom"

		if arguments[0] == "exec" and "FORGOTTEN" in arguments[1]:
			return True, "FORGOTTEN\n"

		if arguments[0] == "exec" and "REMOVED" in arguments[1]:
			return True, "REMOVED\n"

		if arguments[0] == "exec":
			return True, self.says

		return True, ""


@pytest.fixture()
def network (tmp_path: pathlib.Path) -> pathlib.Path:

	"""A file with a network's name and password in it."""

	file = tmp_path / "wifi.txt"
	file.write_text(NAME + "\n" + PASSWORD + "\n")

	return file


def use (monkeypatch: pytest.MonkeyPatch, pico: Pico) -> None:

	"""Put a stand-in Pico on the only USB port."""

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one"])
	monkeypatch.setattr(tools.install, "on_pico", pico)


def never_shown (pico: Pico, said: str, file: pathlib.Path) -> None:

	"""See that what is in the file was neither printed nor given to the Pico on a command line."""

	assert NAME not in said and PASSWORD not in said

	for arguments in pico.asked:
		for argument in arguments:
			assert NAME not in argument and PASSWORD not in argument


def test_a_network_is_copied_tried_and_the_pico_restarted (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""The file goes to the Pico by itself, the Pico is asked to try it, the file is removed, and then the Pico is restarted."""

	pico = Pico()
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 0

	copying, trying, removing, restarting = pico.asked

	assert copying == ["fs", "cp", str(network), ":/_join.txt"]
	assert trying[0] == "exec" and "/_join.txt" in trying[1]
	assert removing[0] == "exec" and "['_join.txt']" in removing[1] and "REMOVED" in removing[1]
	assert restarting == ["reset"]

	said = capsys.readouterr().out

	assert "http://192.168.1.23" in said and "http://picoclock.local" in said
	assert "in 4.2 seconds" in said
	never_shown(pico, said, network)


@pytest.mark.parametrize("reason, words", sorted(tools.join.WHY.items()))
def test_a_join_that_fails_says_why_and_still_restarts_the_pico (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path, reason: str, words: str) -> None:

	"""Each reason the Pico gives has words of its own, none of which names the network."""

	pico = Pico(says="FAILED %s 20600\n" % reason)
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 1
	assert pico.asked[-1] == ["reset"]

	said = capsys.readouterr().out

	assert words in said and "Nothing was saved" in said and "20.6 seconds" in said
	never_shown(pico, said, network)


def test_trying_a_wrong_password_succeeds_when_the_network_refuses_it (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""`--wrong-password` is a test of the refusal: refused is right, and anything else is wrong."""

	pico = Pico(says="FAILED wrong_password 7300\n")
	use(monkeypatch, pico)

	assert tools.join.main([str(network), "--wrong-password"]) == 0
	assert pico.asked[-1] == ["reset"]

	said = capsys.readouterr().out

	assert "refused the password after 7.3 seconds" in said
	never_shown(pico, said, network)

	# The Pico is told to use a password of its own making, and still never sent the file's on a command line.
	assert "if True:" in pico.asked[1][1]

	for says in ("FAILED not_found 20600\n", "JOINED 192.168.1.23 picoclock 4200\n"):

		pico = Pico(says=says)
		use(monkeypatch, pico)

		assert tools.join.main([str(network), "--wrong-password"]) == 1

	# Asked for the usual way, the Pico uses the file's own password.
	pico = Pico()
	use(monkeypatch, pico)
	tools.join.main([str(network)])

	assert "if False:" in pico.asked[1][1]


def test_every_reason_the_pico_can_give_has_words () -> None:

	"""The reasons are the ones the WiFi code gives for a join that failed."""

	wifi = ast.parse((tools.stage.ROOT / "picoclock" / "core" / "wifi.py").read_text())
	names = {}

	for node in wifi.body:
		if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
			names[ast.unparse(node.targets[0])] = node.value.value

	for reason in ("NOT_FOUND", "WRONG_PASSWORD", "FAILED", "NO_ADDRESS", "TRYING"):
		assert names[reason] in tools.join.WHY, reason


def test_what_the_pico_prints_is_not_shown_when_it_makes_no_sense (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""If the Pico fails in some way nobody thought of, its output might hold the file's contents, so it stays unseen."""

	pico = Pico(says="Traceback (most recent call last):\nValueError: %s %s\n" % (NAME, PASSWORD))
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 1
	assert pico.asked[-1] == ["reset"]

	said = capsys.readouterr().out

	assert "didn't say how the join went" in said
	never_shown(pico, said, network)


def test_a_name_or_password_the_pico_will_not_take_is_said_in_its_words (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""A password too short to be one is refused before anything is tried."""

	pico = Pico(says="REFUSED A network's password has between 8 and 63 characters, or none at all\n")
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 1
	assert "between 8 and 63 characters" in capsys.readouterr().out
	assert pico.asked[-1] == ["reset"]


def test_a_pico_without_picoclock_is_told_apart (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""With nothing installed the Pico can't try a network, and the tool says what to do first."""

	pico = Pico(says="Traceback (most recent call last):\nImportError: no module named 'picoclock'\n")
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 1
	assert "tools.install" in capsys.readouterr().out


def test_a_file_that_cannot_be_copied_stops_there_and_the_pico_is_restarted (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""Nothing is tried without the file, whatever of it did get across is removed, and the Pico isn't left stopped."""

	pico = Pico(fail="fs cp")
	use(monkeypatch, pico)

	assert tools.join.main([str(network)]) == 1
	assert [asked[0] for asked in pico.asked] == ["fs", "exec", "reset"]
	assert "['_join.txt']" in pico.asked[1][1]
	never_shown(pico, capsys.readouterr().out, network)


@pytest.mark.parametrize("says", ["JOINED 192.168.1.23 picoclock 4200\n", "FAILED not_found 20600\n", "REFUSED A network's name has between 1 and 32 characters\n", "Traceback (most recent call last):\nImportError: no module named 'picoclock'\n", ""])
def test_the_file_is_removed_from_the_pico_however_the_join_went (monkeypatch: pytest.MonkeyPatch, network: pathlib.Path, says: str) -> None:

	"""The Pico removes the file as it reads it, but it may never have run: so the file is removed again before every restart."""

	pico = Pico(says=says)
	use(monkeypatch, pico)

	tools.join.main([str(network)])

	assert [asked[0] for asked in pico.asked] == ["fs", "exec", "exec", "reset"]
	assert "['_join.txt']" in pico.asked[2][1]


def test_a_file_that_cannot_be_removed_is_said_to_be_there_still (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""A Pico that won't remove the file is a Pico with a password on it in plain text, and the tool says how to remove it, without showing what the Pico said."""

	pico = Pico(fail="REMOVED")
	use(monkeypatch, pico)

	tools.join.main([str(network)])
	said = capsys.readouterr().out

	assert "may still be on the Pico" in said and "Installing PicoClock again removes it" in said
	assert "boom" not in said
	assert pico.asked[-1] == ["reset"]


def test_a_file_with_no_name_in_it_never_reaches_the_pico (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:

	"""An empty file, or one that isn't there, is said so before the Pico is touched."""

	pico = Pico()
	use(monkeypatch, pico)

	empty = tmp_path / "empty.txt"
	empty.write_text("\n")

	assert tools.join.main([str(empty)]) == 1
	assert tools.join.main([str(tmp_path / "missing.txt")]) == 1
	assert pico.asked == []

	said = capsys.readouterr().out

	assert "no network's name" in said and "couldn't be read" in said


def test_forgetting_asks_the_pico_to_forget_and_restarts_it (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:

	"""`--forget` needs no file."""

	pico = Pico()
	use(monkeypatch, pico)

	assert tools.join.main(["--forget"]) == 0

	forgetting, restarting = pico.asked

	assert forgetting[0] == "exec" and "forget_network" in forgetting[1]
	assert restarting == ["reset"]
	assert "its own network" in capsys.readouterr().out


def test_a_file_or_forgetting_must_be_asked_for_and_not_both (monkeypatch: pytest.MonkeyPatch, network: pathlib.Path) -> None:

	"""With neither, or with both, nothing is done."""

	pico = Pico()
	use(monkeypatch, pico)

	with pytest.raises(SystemExit):
		tools.join.main([])

	with pytest.raises(SystemExit):
		tools.join.main([str(network), "--forget"])

	assert pico.asked == []


def test_no_pico_or_more_than_one_stops_before_anything_is_changed (monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], network: pathlib.Path) -> None:

	"""With no Pico, or two and none named, nothing is touched."""

	pico = Pico()

	monkeypatch.setattr(tools.install, "on_pico", pico)
	monkeypatch.setattr(tools.install, "find_devices", lambda: [])

	assert tools.join.main([str(network)]) == 1

	monkeypatch.setattr(tools.install, "find_devices", lambda: ["/dev/one", "/dev/two"])

	assert tools.join.main([str(network)]) == 1
	assert pico.asked == []

	assert tools.join.main([str(network), "--device", "/dev/two"]) == 0


def test_what_runs_on_the_pico_is_python_that_prints_nothing_from_the_file () -> None:

	"""Both scripts read as Python, the file is removed whatever happens, and nothing read from it is printed."""

	script = tools.join.JOIN % (tools.join.HANDOVER, tools.join.HANDOVER, False)
	tree = ast.parse(script)

	ast.parse(tools.join.FORGET)

	for node in ast.walk(tree):

		if isinstance(node, ast.Call) and ast.unparse(node.func) == "print":

			printed = {name.id for argument in node.args for name in ast.walk(argument) if isinstance(name, ast.Name)}

			assert "lines" not in printed and "handle" not in printed

	# The file is removed in a `finally`, straight after it is read.
	removing = [node for node in ast.walk(tree) if isinstance(node, ast.Try) and any("os.remove" in ast.unparse(each) for each in node.finalbody)]

	assert len(removing) == 1
	assert "open(" in ast.unparse(removing[0].body[0])

	# And before anything else is done, so that nothing failing first, such as PicoClock not being there to import, can leave it behind.
	assert ast.unparse(tree.body[0]) == "import os"
	assert tree.body[1] is removing[0]


def test_the_pico_saves_its_settings_only_when_the_network_was_joined () -> None:

	"""A join that fails leaves what the Pico had saved exactly as it was, the network it was on among it."""

	tree = ast.parse(tools.join.JOIN % (tools.join.HANDOVER, tools.join.HANDOVER, False))
	saving = [node for node in ast.walk(tree) if isinstance(node, ast.If) and any("save_now" in ast.unparse(each) for each in node.body)]

	assert "save_now" in tools.join.JOIN
	assert len(saving) == 1 and ast.unparse(saving[0].test) == "joined"
	assert tools.join.JOIN.count("save_now") == 1
	assert "joined = wifi.mode == picoclock.core.wifi.FOUND" in tools.join.JOIN
