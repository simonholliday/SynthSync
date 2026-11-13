"""The package imports on the Pico's Python as well as a laptop's."""

import picoclock
import picoclock.core
import picoclock.web


def check_version_reads_as_three_numbers () -> None:

	"""The version is three numbers with dots between, on either Python."""

	parts = picoclock.__version__.split(".")

	assert len(parts) == 3

	for part in parts:
		assert part.isdigit()
