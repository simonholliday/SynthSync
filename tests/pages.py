"""Helpers for the tests that open the page in a real browser."""

import pathlib
import time
import typing

if typing.TYPE_CHECKING:
	import playwright.sync_api

# Where the page's own files are.
STATIC = pathlib.Path(__file__).parent.parent / "picoclock" / "web" / "static"

# A phone held upright: the size the page is laid out for.
PHONE = {"width": 390, "height": 844}

_TYPES = {
	".html": "text/html",
	".css": "text/css",
	".js": "text/javascript",
}


def eventually (check: typing.Callable[[], bool], within: float = 3.0) -> bool:

	"""Wait for something to come true, and say whether it did in time."""

	until = time.monotonic() + within

	while time.monotonic() < until:

		if check():
			return True

		time.sleep(0.02)

	return check()


def phone (browser: "playwright.sync_api.Browser", width: int = PHONE["width"]) -> "playwright.sync_api.Page":

	"""Open a tab that behaves as a phone does: narrow, and worked by touch."""

	context = browser.new_context(viewport={"width": width, "height": PHONE["height"]}, device_scale_factor=3, is_mobile=True, has_touch=True)

	return context.new_page()


def desktop (browser: "playwright.sync_api.Browser") -> "playwright.sync_api.Page":

	"""Open a tab that behaves as a laptop's browser does, worked by mouse and keyboard."""

	context = browser.new_context(viewport={"width": 1000, "height": 800})

	return context.new_page()


def serve_static (page: "playwright.sync_api.Page", origin: str, extra: dict[str, str]) -> None:

	"""Answer a made-up address from the page's own files, with no server.

	`extra` gives pages that exist only for a test, by path. Anything else
	is looked for among the page's files.
	"""

	def answer (route: "playwright.sync_api.Route") -> None:

		"""Fulfil one request."""

		path = route.request.url[len(origin):].split("?")[0]

		if path in extra:
			route.fulfill(status=200, content_type="text/html", body=extra[path])
			return

		file = STATIC / path.lstrip("/")

		if not file.is_file() or STATIC not in file.resolve().parents:
			route.fulfill(status=404, body="")
			return

		route.fulfill(status=200, content_type=_TYPES.get(file.suffix, "application/octet-stream"), body=file.read_bytes())

	page.route(origin + "/**", answer)


def centre (page: "playwright.sync_api.Page", selector: str) -> tuple[float, float]:

	"""Give the middle of an element, in the tab's own coordinates."""

	box = page.locator(selector).bounding_box()

	assert box is not None

	return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def drag_by_mouse (page: "playwright.sync_api.Page", selector: str, across: float, down: float) -> None:

	"""Press the mouse on the middle of an element, drag this far, and let go."""

	x, y = centre(page, selector)

	page.mouse.move(x, y)
	page.mouse.down()
	page.mouse.move(x + across, y + down, steps=10)
	page.mouse.up()


def drag_by_touch (page: "playwright.sync_api.Page", x: float, y: float, across: float, down: float, moves: int = 10, pause: float = 0) -> None:

	"""Put a finger down at a point, drag this far in so many moves, and lift it.

	With a pause, the finger waits that many seconds after each move, as a
	slow drag does.
	"""

	session = page.context.new_cdp_session(page)

	session.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})

	for move in range(1, moves + 1):

		session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + across * move / moves, "y": y + down * move / moves}]})

		if pause:
			time.sleep(pause)

	session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
	session.detach()
