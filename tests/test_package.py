"""The package imports, and says which version it is."""

import picoclock
import picoclock.core
import picoclock.sim
import picoclock.web


def test_package_has_a_version () -> None:

	"""The page's About view shows the version, so it has to be there and read as one."""

	parts = picoclock.__version__.split(".")

	assert len(parts) == 3
	assert all(part.isdigit() for part in parts)


def test_host_side_packages_import () -> None:

	"""Everything except the Pico-only code imports on a laptop."""

	for module in (picoclock.core, picoclock.sim, picoclock.web):
		assert module.__doc__
