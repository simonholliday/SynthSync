"""SynthSync: a master clock for modular synths on a Raspberry Pi Pico 2 W.

You set a tempo from a phone, and each output pin pulses at that tempo, or at a
multiple or a division of it.

The version comes from the git tag: `v1.2.0` makes it 1.2.0. On a laptop it is
the installed package's, which the build worked out from git. A Pico can't ask
that, so its copy has the version written into `LAID_OUT` as it is laid out
for one (`tools/stage.py`).
"""

# The version, in a copy laid out for a Pico, and empty everywhere else.
LAID_OUT = ""


def _installed () -> str:

	"""Give the installed package's version, or say it isn't known."""

	try:
		import importlib.metadata

		return importlib.metadata.version("synthsync")

	except ImportError:
		# MicroPython has no importlib, and a tree that was never installed has
		# no package to ask: PackageNotFoundError is an ImportError.
		return "unknown"


__version__ = LAID_OUT or _installed()
