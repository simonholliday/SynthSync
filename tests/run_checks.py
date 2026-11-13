"""Run every shared check, on whichever Python this is.

The shared checks are the ones that have to pass on the Pico's MicroPython as
well as on a laptop's Python, because the code they test runs on both. Each is a
plain function named `check_...` in a module named `check_...` under
`tests/checks`, and uses nothing but `assert`.

Run it from the project folder:

    python -m tests.run_checks
    micropython -m tests.run_checks

It prints one line for each failure and a summary, and exits with 1 if anything
failed.
"""

import os
import sys

CHECKS_FOLDER = "tests/checks"
CHECKS_PACKAGE = "tests.checks"


def find_modules () -> list[str]:

	"""Name every check module, in a fixed order."""

	names = []

	for entry in os.listdir(CHECKS_FOLDER):
		if entry.startswith("check_") and entry.endswith(".py"):
			names.append(entry[:-3])

	return sorted(names)


def find_checks (module: object) -> list[str]:

	"""Name every check in one module, in a fixed order."""

	return sorted(name for name in dir(module) if name.startswith("check_"))


def load (name: str) -> object:

	"""Import one check module and hand it back."""

	full_name = CHECKS_PACKAGE + "." + name

	# __import__ hands back the top package, so fetch the module that was asked for.
	__import__(full_name)

	return sys.modules[full_name]


def describe (error: BaseException) -> None:

	"""Print where a failure happened, in whatever way this Python can."""

	# MicroPython has its own call for this, and no traceback module.
	print_exception = getattr(sys, "print_exception", None)

	if print_exception is not None:
		print_exception(error)


def run () -> int:

	"""Run every check, print what failed, and say how many did."""

	count = 0
	failures = 0

	for name in find_modules():

		try:
			module = load(name)
		except Exception as error:
			print("FAIL importing %s: %r" % (name, error))
			describe(error)
			failures += 1
			continue

		for check in find_checks(module):

			count += 1

			try:
				getattr(module, check)()
			except Exception as error:
				print("FAIL %s.%s: %r" % (name, check, error))
				describe(error)
				failures += 1

	version = ".".join(str(part) for part in sys.implementation.version[:3])
	print("%d checks run, %d failed, on %s %s" % (count, failures, sys.implementation.name, version))

	return failures


if __name__ == "__main__":
	sys.exit(1 if run() else 0)
