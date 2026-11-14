"""What the tests share: one real browser for the tests of the page."""

import typing

import pytest

if typing.TYPE_CHECKING:
	import playwright.sync_api


@pytest.fixture(scope="module")
def chromium () -> "typing.Iterator[playwright.sync_api.Browser]":

	"""Start one Chromium for each module of tests that wants a real browser.

	The page's tests need Playwright and its Chromium. Where either is
	missing they are skipped, and a skip there is worth noticing.

	It is started afresh for each module because Playwright keeps an event
	loop running for as long as it is open, and no other test can run one of
	its own meanwhile. So a test that uses this can't call `asyncio.run`.
	"""

	sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright isn't installed, so the page can't be tried in a real browser")

	with sync_api.sync_playwright() as driver:

		try:
			browser = driver.chromium.launch()
		except sync_api.Error:
			pytest.skip("Playwright has no Chromium to start: `playwright install chromium` fetches one")

		yield browser

		browser.close()
