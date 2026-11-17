"""The package imports on the Pico's Python as well as a laptop's."""

import sys

import synthsync
import synthsync.core
import synthsync.web


def check_version_is_known_where_it_can_be () -> None:

	"""On a laptop the version is the installed package's, which starts with a number. MicroPython, running the tree as it stands, has no package to ask and says so.

	A Pico's copy has the version written in, which `tests/test_package.py` checks.
	"""

	assert synthsync.LAID_OUT == ""

	if sys.implementation.name == "micropython":
		assert synthsync.__version__ == "unknown"
	else:
		assert synthsync.__version__[0].isdigit()
		assert " " not in synthsync.__version__
