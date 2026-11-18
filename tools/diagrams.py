"""Draw the wiring diagrams that the README shows.

    python -m tools.diagrams
    python -m tools.diagrams FOLDER

Each diagram is an SVG file, drawn here from the same few symbols: a resistor,
a transistor, an LED, a push button, a jack socket, ground and the 5 V supply.
The pins and the part values are written once, below, so a changed value is
changed in one place and every picture is drawn again. Nothing beyond Python
is needed to run it.

Every diagram sits on a rounded card of its own, and is drawn twice: once for
light pages, and once, with `-dark` in its name, for dark ones. With no folder
given, the pictures go where the README looks for them.
"""

import html
import math
import os
import pathlib
import sys

# Where the README looks for its pictures.
IMAGES = pathlib.Path(__file__).parent.parent / "docs" / "images"

# The Pico's header pin for each GPIO that SynthSync uses, as the official pinout numbers them.
HEADER_PIN = {2: 4, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14, 11: 15, 21: 27, 22: 29}

# The header pin of the Pico's own 3.3 V supply.
SUPPLY_PIN = 36

# Where each thing is wired, which must match `synthsync/device/board.py`.
FIRST_OUTPUT = 2
PLAY_PAUSE = 21
STOP = 22
TEMPO_LED = 10
MIDI_OUT = 11

# The part values, as the wiring guide gives them.
INTO_BASE = "10 kΩ"
PULL_UP = "10 kΩ"
TO_JACK = "1 kΩ"
FOR_LED = "1 kΩ"

# MIDI out's, which are the MIDI standard's for a 3.3 V supply.
MIDI_SUPPLY = "33 Ω, 0.5 W"
MIDI_SINK = "10 Ω"
MIDI_BASE = "1 kΩ"
MIDI_BASE_DOWN = "4.7 kΩ"

# Which contact of each kind of MIDI socket takes what: the supply's resistor,
# which is MIDI's pin 4, the transistor's resistor, which is its pin 5, and
# ground, its pin 2. Type B is Type A with the first two swapped.
MIDI_SOCKETS = (
	("3.5 mm TRS jack, Type A", "Ring", "Tip", "Sleeve"),
	("3.5 mm TRS jack, Type B", "Tip", "Ring", "Sleeve"),
	("5-pin DIN socket", "Pin 4", "Pin 5", "Pin 2"),
)

# How far either side of a crossing a wire leaves the straight, to hop over the other.
HOP = 6

# The colours: ink for the circuit, a quieter grey for notes, and the Pico's pins in the green its pinout uses.
INK = "#1f2328"
QUIET = "#59636e"
CARD = "#ffffff"
EDGE = "#d0d7de"
BOARD = "#f6f8fa"
PIN_FILL = "#dafbe1"
PIN_EDGE = "#1a7f37"

# What each of those becomes in a diagram for a dark page.
DARK = {
	INK: "#e6edf3",
	QUIET: "#9198a1",
	CARD: "#161b22",
	EDGE: "#3d444d",
	BOARD: "#21262d",
	PIN_FILL: "#12351f",
	PIN_EDGE: "#3fb950",
}

STYLE = """
	.wire { fill: none; stroke: %s; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
	.shape { fill: %s; stroke: %s; stroke-width: 2; stroke-linejoin: round; }
	.solid { fill: %s; stroke: none; }
	.pin { fill: %s; stroke: %s; stroke-width: 1.5; }
	.maybe { fill: none; stroke: %s; stroke-width: 1.5; stroke-dasharray: 6 5; }
	text { font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; fill: %s; }
	.label { font-size: 14px; }
	.name { font-size: 14px; font-weight: 600; }
	.title { font-size: 17px; font-weight: 600; }
	.note { font-size: 13px; fill: %s; }
""" % (INK, CARD, INK, INK, PIN_FILL, PIN_EDGE, QUIET, INK, QUIET)

Point = tuple[float, float]


class Sheet:

	"""One diagram: wires, symbols and words on a light card."""

	def __init__ (self, width: int, height: int, title: str, about: str) -> None:

		"""Begin an empty diagram of this size. `title` is written at its top, and `about` is what a screen reader says of it."""

		self.width = width
		self.height = height
		self.title = title
		self.about = about
		self.parts: list[str] = []

		# Everything written, as (where it starts or ends or is centred, how, the words, their kind), for checking that none of it runs off the card.
		self.written: list[tuple[float, str, str, str]] = []

		self.words(24, 36, title, kind="title")

	def wire (self, *points: Point) -> None:

		"""Draw a wire through these points."""

		self.parts.append('<path class="wire" d="M%s"/>' % " L".join("%g %g" % point for point in points))

	def over (self, start: Point, end: Point, at: Point, size: float = HOP) -> None:

		"""Draw a straight wire that hops over another where it crosses it at `at`, so the two can't be taken for joined."""

		length = math.hypot(end[0] - start[0], end[1] - start[1])
		along = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
		before = (at[0] - along[0] * size, at[1] - along[1] * size)
		after = (at[0] + along[0] * size, at[1] + along[1] * size)

		self.parts.append('<path class="wire" d="M%g %g L%.1f %.1f A%g %g 0 0 1 %.1f %.1f L%g %g"/>' % (start + before + (size, size) + after + end))

	def terminal (self, at: Point) -> None:

		"""Draw one of a switch's tags."""

		self.parts.append('<circle class="shape" cx="%g" cy="%g" r="5"/>' % at)

	def joint (self, at: Point) -> None:

		"""Draw the dot that says wires meeting here are joined."""

		self.parts.append('<circle class="solid" cx="%g" cy="%g" r="3.5"/>' % at)

	def words (self, x: float, y: float, text: str, anchor: str = "start", kind: str = "label") -> None:

		"""Write something, starting at, ending at or centred on this point."""

		self.written.append((x, anchor, text, kind))
		self.parts.append('<text class="%s" x="%g" y="%g" text-anchor="%s">%s</text>' % (kind, x, y, anchor, html.escape(text)))

	def arrow (self, tail: Point, tip: Point, size: float = 9) -> None:

		"""Draw an arrowhead whose point is at `tip`, pointing away from `tail`."""

		length = math.hypot(tip[0] - tail[0], tip[1] - tail[1])
		along = ((tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length)
		back = (tip[0] - along[0] * size, tip[1] - along[1] * size)
		side = (-along[1] * size / 2, along[0] * size / 2)

		corners = (tip, (back[0] + side[0], back[1] + side[1]), (back[0] - side[0], back[1] - side[1]))

		self.parts.append('<path class="solid" d="M%s Z"/>' % " L".join("%.1f %.1f" % corner for corner in corners))

	def tag (self, at: Point, name: str, number: int) -> Point:

		"""Draw one of the Pico's pins as a tag ending at this point, with its name and its number on the header, and give the point back."""

		x, y = at

		self.parts.append('<rect class="pin" x="%g" y="%g" width="124" height="28" rx="6"/>' % (x - 124, y - 14))
		self.words(x - 62, y + 5, "%s · pin %d" % (name, number), anchor="middle", kind="name")

		return at

	def pin (self, at: Point, gpio: int) -> Point:

		"""Draw one of the Pico's GPIO pins as a tag ending at this point, named as the pinout names it, and give the point back."""

		return self.tag(at, "GP%d" % gpio, HEADER_PIN[gpio])

	def board (self, corner: Point, width: float, height: float) -> None:

		"""Draw the Pico itself as a plain board, for its pins' tags to sit along the edge of."""

		x, y = corner

		self.parts.append('<rect x="%g" y="%g" width="%g" height="%g" rx="10" fill="%s" stroke="%s" stroke-width="2"/>' % (x, y, width, height, BOARD, EDGE))
		self.words(x + width / 2, y + 28, "Pico 2 W", anchor="middle", kind="name")

	def resistor (self, start: Point, value: str, down: bool = False, beside: int = -1) -> Point:

		"""Draw a resistor from here, to the right or downwards, with its value beside it, and give where it ends.

		`beside` is -1 to write the value above it or to its left, and 1 for below or to its right.
		"""

		x, y = start
		shape = [(0, 0), (16, 0)] + [(20 + 8 * step, -7 if step % 2 == 0 else 7) for step in range(6)] + [(64, 0), (80, 0)]

		if down:
			self.wire(*[(x + off, y + along) for along, off in shape])
			self.words(x + 16 * beside, y + 45, value, anchor="end" if beside < 0 else "start")

			return (x, y + 80)

		self.wire(*[(x + along, y + off) for along, off in shape])
		self.words(x + 40, y - 14 if beside < 0 else y + 26, value, anchor="middle")

		return (x + 80, y)

	def transistor (self, base: Point, pnp: bool = False) -> tuple[Point, Point]:

		"""Draw a transistor whose base is at this point, and give where its upper and lower legs end.

		An NPN has its collector above and its emitter below. A PNP is drawn
		the other way up, with its emitter above, as it sits in a circuit that
		switches the supply.
		"""

		x, y = base
		upper = ((x + 24.0, y - 6.0), (x + 46.0, y - 20.0))
		lower = ((x + 24.0, y + 6.0), (x + 46.0, y + 20.0))

		self.parts.append('<circle class="shape" cx="%g" cy="%g" r="25"/>' % (x + 36, y))
		self.wire((x, y), (x + 24, y))
		self.parts.append('<path class="wire" style="stroke-width: 3.5" d="M%g %g L%g %g"/>' % (x + 24, y - 15, x + 24, y + 15))
		self.wire(upper[0], upper[1], (x + 46, y - 40))
		self.wire(lower[0], lower[1], (x + 46, y + 40))

		# The arrow is on the emitter: leaving an NPN's, and arriving at a PNP's.
		if pnp:
			self.arrow(upper[1], (upper[0][0] + 6, upper[0][1] - 3.8))
		else:
			self.arrow(lower[0], (lower[1][0] - 2, lower[1][1] - 1.3))

		return ((x + 46, y - 40), (x + 46, y + 40))

	def led (self, start: Point) -> Point:

		"""Draw an LED from here downwards, lit by current flowing down through it, and give where it ends."""

		x, y = start

		self.wire((x, y), (x, y + 20))
		self.parts.append('<path class="shape" d="M%g %g L%g %g L%g %g Z"/>' % (x - 11, y + 20, x + 11, y + 20, x, y + 40))
		self.wire((x - 11, y + 40), (x + 11, y + 40))
		self.wire((x, y + 40), (x, y + 60))

		# Two small arrows for the light leaving it.
		for drop in (20, 31):
			self.wire((x + 17, y + drop + 8), (x + 28, y + drop))
			self.arrow((x + 17, y + drop + 8), (x + 30, y + drop - 1.5), size=6)

		return (x, y + 60)

	def button (self, start: Point) -> Point:

		"""Draw a push button from here to the right, which joins its two sides while pressed, and give where it ends."""

		x, y = start

		self.wire((x, y), (x + 26, y))
		self.wire((x + 54, y), (x + 80, y))

		for contact in (29, 51):
			self.parts.append('<circle class="shape" cx="%g" cy="%g" r="3.5"/>' % (x + contact, y))

		self.wire((x + 23, y - 10), (x + 57, y - 10))
		self.wire((x + 40, y - 10), (x + 40, y - 21))
		self.wire((x + 33, y - 21), (x + 47, y - 21))

		return (x + 80, y)

	def jack (self, tip: Point) -> Point:

		"""Draw a jack socket whose tip contact is wired at this point, and give where its sleeve contact ends, below it."""

		x, y = tip

		self.wire((x, y), (x + 18, y), (x + 28, y + 9), (x + 38, y))
		self.parts.append('<rect class="shape" x="%g" y="%g" width="9" height="40" rx="2"/>' % (x + 52, y - 24))
		self.wire((x + 56.5, y + 16), (x + 56.5, y + 40))

		self.words(x + 28, y - 12, "tip", anchor="middle", kind="note")
		self.words(x + 70, y + 2, "sleeve", kind="note")

		return (x + 56.5, y + 40)

	def ground (self, at: Point) -> None:

		"""Draw the ground symbol hanging from this point."""

		x, y = at

		for step in range(3):
			self.wire((x - 14 + 5 * step, y + 6 * step), (x + 14 - 5 * step, y + 6 * step))

	def supply (self, at: Point, name: str = "+5 V") -> None:

		"""Draw the supply symbol standing on this point, with its name above it."""

		x, y = at

		self.wire((x, y), (x, y - 16))
		self.wire((x - 12, y - 16), (x + 12, y - 16))
		self.words(x, y - 24, name, anchor="middle", kind="name")

	def maybe (self, corner: Point, width: float, height: float, caption: str) -> None:

		"""Draw a dashed box around parts that can be left out, with a few words saying so beneath its top edge on the right."""

		x, y = corner

		self.parts.append('<rect class="maybe" x="%g" y="%g" width="%g" height="%g" rx="8"/>' % (x, y, width, height))
		self.words(x + width - 10, y + 20, caption, anchor="end", kind="note")

	def svg (self) -> str:

		"""Give the whole diagram as the text of an SVG file."""

		head = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-labelledby="title about">' % (self.width, self.height, self.width, self.height)
		named = '<title id="title">%s</title><desc id="about">%s</desc>' % (html.escape(self.title), html.escape(self.about))
		card = '<rect x="1" y="1" width="%d" height="%d" rx="12" fill="%s" stroke="%s" stroke-width="2"/>' % (self.width - 2, self.height - 2, CARD, EDGE)

		return "\n".join([head, named, "<style>%s</style>" % STYLE, card] + self.parts + ["</svg>", ""])


def stage (sheet: Sheet, pin: Point, gpio: int, led: bool, name: str = "") -> None:

	"""Draw one clock output, from the Pico's pin at this point to its jack, with a name over it if one is given.

	The pin turns the first transistor on, which turns the second on, which
	joins the jack to 5 V. So the jack follows the pin, at 5 V in place of 3.3.
	"""

	x, y = sheet.pin(pin, gpio)

	if name:
		sheet.words(x + 16, y - 150, name, kind="name")

	base = sheet.resistor((x, y), INTO_BASE)
	collector, emitter = sheet.transistor(base)
	sheet.words(x + 150, y + 26, "NPN", kind="name")

	sheet.wire(emitter, (emitter[0], y + 56))
	sheet.ground((emitter[0], y + 56))

	# Up from the first transistor and across to the second, with a resistor to 5 V that keeps the second off until then.
	sheet.wire(collector, (collector[0], y - 80))
	across = sheet.resistor((collector[0], y - 80), INTO_BASE, beside=1)
	meet = (across[0] + 20, y - 80)

	sheet.wire(across, meet, (meet[0] + 20, y - 80))
	sheet.joint(meet)
	sheet.resistor((meet[0], y - 160), PULL_UP, down=True)

	high, low = sheet.transistor((meet[0] + 20, y - 80), pnp=True)
	sheet.words(high[0] + 24, y - 75, "PNP", kind="name")

	rail = (high[0], y - 160)

	sheet.wire((meet[0], y - 160), rail)
	sheet.wire(high, rail)
	sheet.joint(rail)
	sheet.supply(rail)

	# Down to the height of the pin, and out to the jack.
	out = (low[0], y)

	sheet.wire(low, out, (out[0] + 20, y))

	tip = sheet.resistor((out[0] + 20, y), TO_JACK)
	sleeve = sheet.jack((tip[0] + 20, y))

	sheet.wire(tip, (tip[0] + 20, y))
	sheet.ground(sleeve)
	sheet.words(tip[0] + 48, y - 40, "3.5 mm jack", anchor="middle", kind="name")

	if led:

		sheet.joint(out)

		lit = sheet.led(sheet.resistor(out, FOR_LED, down=True, beside=1))

		sheet.ground(lit)
		sheet.words(out[0] + 44, y + 118, "LED")
		sheet.maybe((out[0] - 34, y + 12), 150, 158, "optional")


def output (led: bool) -> Sheet:

	"""Draw one clock output by itself, with or without the LED that shows its pulses."""

	if led:
		sheet = Sheet(720, 500, "A clock output with an LED", "The same circuit as a clock output, with a 1 kilohm resistor and an LED from the PNP transistor's collector to ground, which light with each pulse.")
	else:
		sheet = Sheet(720, 380, "A clock output", "A Pico pin drives an NPN transistor through 10 kilohms. The NPN switches a PNP transistor, which joins the jack's tip to 5 volts through 1 kilohm. The jack's sleeve goes to ground.")

	stage(sheet, (150, 262), FIRST_OUTPUT, led)
	sheet.words(24, sheet.height - 22, "+5 V is the Pico's VSYS, pin 39. Every ground symbol is one of the Pico's GND pins.", kind="note")

	return sheet


def press (sheet: Sheet, play_pause: Point, stop: Point) -> None:

	"""Draw the two buttons, each from its own pin at these points to ground.

	A button needs nothing else: the Pico holds each pin at 3.3 V by itself,
	and pressing the button joins the pin to ground.
	"""

	x = play_pause[0]
	shared = (x + 180, stop[1])

	sheet.pin(play_pause, PLAY_PAUSE)
	sheet.wire(play_pause, (x + 40, play_pause[1]))
	sheet.wire(sheet.button((x + 40, play_pause[1])), (shared[0], play_pause[1]), shared)
	sheet.words(x + 80, play_pause[1] - 32, "Play/pause", anchor="middle")

	sheet.pin(stop, STOP)
	sheet.wire(stop, (x + 40, stop[1]))
	sheet.wire(sheet.button((x + 40, stop[1])), shared, (shared[0], stop[1] + 30))
	sheet.joint(shared)
	sheet.ground((shared[0], stop[1] + 30))
	sheet.words(x + 80, stop[1] - 32, "Stop", anchor="middle")
	sheet.words(shared[0] + 24, stop[1] + 5, "hold for 5 s to reset", kind="note")


def buttons () -> Sheet:

	"""Draw the play/pause and stop buttons by themselves."""

	sheet = Sheet(620, 300, "The play/pause and stop buttons", "Two push buttons. One joins GP21 to ground while pressed, and the other joins GP22 to ground.")

	press(sheet, (150, 110), (150, 190))
	sheet.words(24, 278, "No resistors are needed. Pin 28, between these two pins, is a GND pin that both can use.", kind="note")

	return sheet


def tempo_led () -> Sheet:

	"""Draw the tempo LED, which flashes on every beat."""

	sheet = Sheet(620, 250, "The tempo LED", "GP10 goes through a 1 kilohm resistor and an LED to ground.")

	at = sheet.pin((150, 96), TEMPO_LED)
	turn = (at[0] + 120, at[1])

	sheet.wire(sheet.resistor(at, FOR_LED), turn)
	sheet.ground(sheet.led(turn))
	sheet.words(turn[0] + 44, at[1] + 38, "LED")
	sheet.words(24, 228, "It flashes on every beat. Pin 13, next to this pin, is a GND pin.", kind="note")

	return sheet


def midi_stage (sheet: Sheet, y: float) -> Point:

	"""Draw GP11 switching MIDI's transistor, level with this height, and give where its collector ends."""

	at = sheet.pin((150, y), MIDI_OUT)
	base = sheet.resistor(at, MIDI_BASE)
	meet = (base[0] + 30, y)

	sheet.wire(base, meet)
	sheet.joint(meet)

	collector, emitter = sheet.transistor(meet)
	floor = (emitter[0], y + 90)

	sheet.words(meet[0] + 38, y - 31, "NPN", anchor="end", kind="name")
	sheet.wire(sheet.resistor(meet, MIDI_BASE_DOWN, down=True), (meet[0], floor[1]), floor)
	sheet.wire(emitter, floor)
	sheet.joint(floor)
	sheet.ground(floor)

	return collector


def midi () -> Sheet:

	"""Draw MIDI out with a socket of each kind: one transistor, and two resistors of their own for each socket."""

	sheet = Sheet(780, 526, "MIDI out, with a socket of each kind", "The 3.3 volt pin feeds a 33 ohm resistor for each socket. GP11 goes through 1 kilohm to the base of an NPN transistor, with 4.7 kilohms from base to emitter and the emitter to ground. The collector feeds a 10 ohm resistor for each socket. On a Type A jack the 33 ohms goes to the ring and the 10 ohms to the tip. On a Type B jack they are the other way round. On a 5-pin DIN socket they go to pins 4 and 5. The sleeve, or pin 2, goes to ground.")
	socket = 500
	sink_bus = 330
	feed_bus = 356
	resistors = 380
	tops = [56 + 140 * index for index in range(len(MIDI_SOCKETS))]
	feeds = [top + 46 for top in tops]
	sinks = [top + 84 for top in tops]

	# The supply runs along the top and down past every socket's resistor.
	supply = sheet.tag((150, feeds[0]), "3V3", SUPPLY_PIN)

	sheet.wire(supply, (resistors, feeds[0]))
	sheet.wire((feed_bus, feeds[0]), (feed_bus, feeds[-1]))
	sheet.joint((feed_bus, feeds[0]))

	# The collector joins a line running down past every socket's other resistor.
	collector = midi_stage(sheet, 300)

	sheet.wire(collector, (sink_bus, collector[1]))
	sheet.wire((sink_bus, sinks[0]), (sink_bus, sinks[-1]))
	sheet.joint((sink_bus, collector[1]))

	for index, (name, feed, sink, ground) in enumerate(MIDI_SOCKETS):

		top = tops[index]
		across = feeds[index]
		below = sinks[index]
		earth = top + 112

		if index:
			sheet.wire((feed_bus, across), (resistors, across))

		if 0 < index < len(MIDI_SOCKETS) - 1:
			sheet.joint((feed_bus, across))
			sheet.joint((sink_bus, below))

		# Every line from the collector but the last hops over the supply's on its way.
		if index < len(MIDI_SOCKETS) - 1:
			sheet.over((sink_bus, below), (resistors, below), (feed_bus, below))
		else:
			sheet.wire((sink_bus, below), (resistors, below))

		sheet.wire(sheet.resistor((resistors, across), MIDI_SUPPLY), (socket, across))
		sheet.wire(sheet.resistor((resistors, below), MIDI_SINK), (socket, below))

		sheet.parts.append('<rect class="shape" x="%g" y="%g" width="256" height="128" rx="8"/>' % (socket, top))
		sheet.words(socket + 128, top + 24, name, anchor="middle", kind="name")

		for at, contact, number in ((across, feed, 4), (below, sink, 5), (earth, ground, 2)):
			sheet.words(socket + 14, at + 5, contact if contact.startswith("Pin") else "%s: MIDI pin %d" % (contact, number))

		sheet.wire((socket, earth), (socket - 24, earth), (socket - 24, earth + 10))
		sheet.ground((socket - 24, earth + 10))

	sheet.words(24, 490, "Fit any of them, or all. One transistor serves every socket, and each socket has its own two resistors.", kind="note")
	sheet.words(24, 510, "On a 5-pin DIN socket, go by the numbers moulded on it: they don't run in order.", kind="note")

	return sheet


def midi_switch () -> Sheet:

	"""Draw one TRS jack behind a two-pole, two-way switch, which swaps its tip and ring for Type A or Type B."""

	sheet = Sheet(780, 436, "One jack, with a switch for Type A or Type B", "The transistor's 10 ohm resistor goes to the switch's Type A tag on its upper row and its Type B tag on its lower row. The 33 ohm resistor from the 3.3 volt pin goes to the Type A tag on the lower row and the Type B tag on the upper row. The upper middle tag goes to the jack's tip, and the lower middle tag to its ring. The sleeve goes to ground.")
	left = 400
	middle = 470
	right = 540
	upper = 150
	lower = 250
	centre = (middle, (upper + lower) / 2)
	jack = 600
	tip = 104
	ring = 296
	sleeve = 340

	sheet.parts.append('<rect class="shape" x="356" y="118" width="228" height="164" rx="8"/>')
	sheet.words(left, 136, "Type A", anchor="middle", kind="name")
	sheet.words(right, 136, "Type B", anchor="middle", kind="name")

	# The transistor's resistor along the top row, and the supply's along the bottom.
	sheet.words(24, upper + 5, "Transistor's collector", kind="name")
	sheet.wire((206, upper), (215, upper))
	sheet.wire(sheet.resistor((215, upper), MIDI_SINK), (left, upper))

	supply = sheet.tag((150, lower), "3V3", SUPPLY_PIN)

	sheet.wire(supply, (215, lower))
	sheet.wire(sheet.resistor((215, lower), MIDI_SUPPLY), (left, lower))

	# Each also goes across to the far tag of the other row, which is what swaps them.
	sheet.wire((left, upper), (right, lower))
	sheet.over((left, lower), (right, upper), centre, 11)

	# The two middle tags go to the jack.
	sheet.wire((middle, upper), (middle, tip), (jack, tip))
	sheet.wire((middle, lower), (middle, ring), (jack, ring))

	for x in (left, middle, right):
		for y in (upper, lower):
			sheet.terminal((x, y))

	sheet.parts.append('<rect class="shape" x="%g" y="64" width="156" height="300" rx="8"/>' % jack)
	sheet.words(jack + 78, 84, "3.5 mm TRS jack", anchor="middle", kind="name")
	sheet.words(jack + 14, tip + 5, "Tip")
	sheet.words(jack + 14, ring + 5, "Ring")
	sheet.words(jack + 14, sleeve + 5, "Sleeve")
	sheet.wire((jack, sleeve), (jack - 24, sleeve), (jack - 24, sleeve + 10))
	sheet.ground((jack - 24, sleeve + 10))

	sheet.words(24, 396, "Any two-pole, two-way (DPDT) switch will do, with the jack on its two middle tags.", kind="note")
	sheet.words(24, 416, "Check which way is which with a meter: a toggle joins the tags on the side away from its lever.", kind="note")

	return sheet


def direct () -> Sheet:

	"""Draw the quick way to try a module: a jack straight from a pin, with one resistor and nothing to protect the Pico."""

	sheet = Sheet(620, 250, "The quick way: straight from a pin", "GP2 goes through a 1 kilohm resistor to the jack's tip. The jack's sleeve goes to ground.")

	at = sheet.pin((150, 110), FIRST_OUTPUT)
	tip = sheet.resistor(at, TO_JACK)

	sheet.wire(tip, (tip[0] + 20, at[1]))
	sheet.ground(sheet.jack((tip[0] + 20, at[1])))
	sheet.words(tip[0] + 48, at[1] - 40, "3.5 mm jack", anchor="middle", kind="name")
	sheet.words(24, 228, "Pulses of 3.3 V, and nothing to protect the Pico. For trying things out only.", kind="note")

	return sheet


def example () -> Sheet:

	"""Draw a whole SynthSync: the main clock, two multividers and both buttons."""

	sheet = Sheet(720, 1290, "An example: a main clock, two multividers and both buttons", "A Pico 2 W with three clock outputs on GP2, GP3 and GP4, each as the clock output circuit, a play/pause button on GP21, a stop button on GP22, 5 volts taken from VSYS and ground from a GND pin.")

	sheet.board((22, 56), 128, 1180)

	for index, name in enumerate(("Output 1: the main clock", "Output 2: a multivider", "Output 3: a multivider")):
		stage(sheet, (160, 280 + 280 * index), FIRST_OUTPUT + index, False, name)

	press(sheet, (160, 990), (160, 1070))

	# Where the 5 V and the ground in every circuit above come from.
	power = sheet.tag((160, 1150), "VSYS", 39)
	earth = sheet.tag((160, 1200), "GND", 38)

	sheet.wire(power, (power[0] + 50, power[1]))
	sheet.supply((power[0] + 50, power[1]))
	sheet.wire(earth, (earth[0] + 50, earth[1]), (earth[0] + 50, earth[1] + 12))
	sheet.ground((earth[0] + 50, earth[1] + 12))
	sheet.words(power[0] + 90, power[1] + 5, "every +5 V above", kind="note")
	sheet.words(earth[0] + 90, earth[1] + 5, "every ground above (any GND pin will do)", kind="note")
	sheet.words(24, sheet.height - 22, "Power comes in through the Pico's USB socket.", kind="note")

	return sheet


DIAGRAMS = {
	"output": lambda: output(False),
	"output-led": lambda: output(True),
	"buttons": buttons,
	"tempo-led": tempo_led,
	"midi": midi,
	"midi-switch": midi_switch,
	"direct": direct,
	"example": example,
}


def shaded (svg: str, dark: bool) -> str:

	"""Give a diagram's SVG in the colours for a light page or a dark one."""

	if dark:
		for light, instead in DARK.items():
			svg = svg.replace(light, instead)

	return svg


def svg_path (folder: pathlib.Path, name: str, dark: bool) -> pathlib.Path:

	"""Say where one diagram goes, for a light page or a dark one."""

	return folder / (name + ("-dark" if dark else "") + ".svg")


def main (arguments: list[str] | None = None) -> int:

	"""Draw every diagram, light and dark, into the README's folder of pictures, or into a folder that is named, and say what was drawn."""

	arguments = sys.argv[1:] if arguments is None else arguments
	folder = pathlib.Path(arguments[0]) if arguments else IMAGES

	folder.mkdir(parents=True, exist_ok=True)

	for name, draw in DIAGRAMS.items():

		svg = draw().svg()

		for dark in (False, True):

			path = svg_path(folder, name, dark)
			write(path, shaded(svg, dark))

			print("drew", path)

	return 0


def write (path: pathlib.Path, text: str) -> None:

	"""Put a file in place whole, by writing it under a new name and then renaming it.

	The folder may be on a network share, where opening a file to write over it
	can hang the share for good on some machines. A new file and a rename
	never do that, and a picture is never left half written.
	"""

	new = path.with_name(path.name + ".new")

	# Left by a run that was cut short.
	new.unlink(missing_ok=True)

	with open(new, "x", encoding="utf-8") as handle:
		handle.write(text)

	os.replace(new, path)


if __name__ == "__main__":
	sys.exit(main())
