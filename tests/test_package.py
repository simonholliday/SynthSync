"""The package imports, and says which version it is."""

import synthsync
import synthsync.core
import synthsync.sim
import synthsync.web


def test_package_has_a_version () -> None:

	"""The page's About view shows the version, so it has to be there and read as one."""

	parts = synthsync.__version__.split(".")

	assert len(parts) == 3
	assert all(part.isdigit() for part in parts)


def test_host_side_packages_import () -> None:

	"""Everything except the Pico-only code imports on a laptop."""

	for module in (synthsync.core, synthsync.sim, synthsync.web):
		assert module.__doc__
