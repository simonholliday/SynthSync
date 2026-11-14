/*
The page's one custom control: a knob, turned by dragging or with the arrow keys.

A knob holds no value of its own. It says how far it has been turned, in steps,
and points wherever it is told to. What a step means is up to whoever uses it:
one BPM for the tempo, the next musical value for a ratio.

Dragging is relative on purpose. Touching a knob never moves it, so a stray
finger can't jump the tempo. Only the distance dragged counts: up or to the
right for more, down or to the left for less.

A screen reader hears it as a slider, and the arrow keys move it one step,
Page Up and Page Down several. Home and End are left out deliberately: nobody
wants one key to send a running clock to its slowest.
*/

const SVG = "http://www.w3.org/2000/svg";

// The pointer travels three quarters of a turn, from lower left round to lower right.
const LOWEST_ANGLE = -135;
const SWEEP = 270;

// How far out the arc and the pointer sit, in a drawing 100 units across.
const ARC_RADIUS = 44;
const BODY_RADIUS = 34;


/* Give the point at an angle on a circle about the middle. Angles are in degrees, clockwise from the top. */
function pointAt (angle, radius) {

	const turn = angle * Math.PI / 180;

	return [50 + radius * Math.sin(turn), 50 - radius * Math.cos(turn)];
}


/* Describe an arc between two angles as an SVG path. */
function arc (from, to, radius) {

	const [startX, startY] = pointAt(from, radius);
	const [endX, endY] = pointAt(to, radius);
	const large = Math.abs(to - from) > 180 ? 1 : 0;
	const clockwise = to > from ? 1 : 0;

	return `M ${startX.toFixed(2)} ${startY.toFixed(2)} A ${radius} ${radius} 0 ${large} ${clockwise} ${endX.toFixed(2)} ${endY.toFixed(2)}`;
}


/* Make one SVG element with these attributes. */
function shape (name, attributes) {

	const element = document.createElementNS(SVG, name);

	for (const [key, value] of Object.entries(attributes)) {
		element.setAttribute(key, value);
	}

	return element;
}


export class Knob {

	/*
	Draw a knob inside an element, which becomes the thing a finger or the keyboard works.

	Options:
		label          what a screen reader calls it
		pixelsPerStep  how far a drag goes for one step: 4 unless given
		bigStep        how many steps Page Up and Page Down move: 10 unless given
		origin         where along its travel the lit arc begins, from 0 to 1: 0 unless given
		onStep(steps)  called as it turns, with how many steps, negative for less
		onHold(held)   called when a drag begins, with true, and ends, with false
	*/
	constructor (element, options) {

		this.element = element;
		this.pixelsPerStep = options.pixelsPerStep || 4;
		this.bigStep = options.bigStep || 10;
		this.origin = options.origin || 0;
		this.onStep = options.onStep || (() => {});
		this.onHold = options.onHold || (() => {});

		// Whether a finger or the mouse is on it, and what the drag has added up to.
		this.held = false;
		this.pointer = null;
		this.carried = 0;
		this.lastX = 0;
		this.lastY = 0;

		this.draw();

		element.classList.add("knob");
		element.setAttribute("role", "slider");
		element.setAttribute("tabindex", "0");
		element.setAttribute("aria-label", options.label || "Knob");

		// Without this a drag on a phone scrolls the page and the knob never hears of it.
		element.style.touchAction = "none";
		element.style.userSelect = "none";
		element.style.webkitUserSelect = "none";

		element.addEventListener("pointerdown", (event) => this.pressed(event));
		element.addEventListener("pointermove", (event) => this.moved(event));
		element.addEventListener("pointerup", (event) => this.released(event));
		element.addEventListener("pointercancel", (event) => this.released(event));
		element.addEventListener("keydown", (event) => this.keyed(event));

		// A long press on a phone would otherwise bring up a menu mid-drag.
		element.addEventListener("contextmenu", (event) => event.preventDefault());

		this.point(0);
	}

	/* Build the drawing: a dim arc for the travel, a lit arc for the setting, the body and its pointer. */
	draw () {

		const drawing = shape("svg", {viewBox: "0 0 100 100", "aria-hidden": "true", focusable: "false"});

		this.lit = shape("path", {class: "knob-lit", fill: "none", "stroke-width": 6, "stroke-linecap": "round"});
		this.hand = shape("g", {class: "knob-hand"});

		this.hand.append(
			shape("circle", {class: "knob-body", cx: 50, cy: 50, r: BODY_RADIUS}),
			shape("line", {class: "knob-pointer", x1: 50, y1: 50 - BODY_RADIUS + 5, x2: 50, y2: 50 - BODY_RADIUS + 19, "stroke-width": 5, "stroke-linecap": "round"}),
		);

		drawing.append(
			shape("path", {class: "knob-track", d: arc(LOWEST_ANGLE, LOWEST_ANGLE + SWEEP, ARC_RADIUS), fill: "none", "stroke-width": 6, "stroke-linecap": "round"}),
			this.lit,
			this.hand,
		);

		this.element.replaceChildren(drawing);
	}

	/* Point the knob somewhere along its travel, from 0 at its lowest to 1 at its highest. */
	point (fraction) {

		const along = Math.min(Math.max(fraction, 0), 1);
		const angle = LOWEST_ANGLE + along * SWEEP;
		const from = LOWEST_ANGLE + this.origin * SWEEP;

		this.fraction = along;
		this.hand.setAttribute("transform", `rotate(${angle.toFixed(2)} 50 50)`);

		// An arc with no length draws nothing, which is right when the knob sits on its origin.
		this.lit.setAttribute("d", Math.abs(angle - from) < 0.5 ? "" : arc(from, angle, ARC_RADIUS));
	}

	/* Say what the knob is set to, for a screen reader: the words, and the number with its limits. */
	say (text, now, lowest, highest) {

		this.element.setAttribute("aria-valuetext", text);
		this.element.setAttribute("aria-valuenow", now);
		this.element.setAttribute("aria-valuemin", lowest);
		this.element.setAttribute("aria-valuemax", highest);
	}

	/* Stop the knob being turned, or let it turn again. */
	disable (disabled) {

		this.disabled = disabled;
		this.element.setAttribute("aria-disabled", disabled ? "true" : "false");
		this.element.setAttribute("tabindex", disabled ? "-1" : "0");

		if (disabled && this.held) {
			this.letGo();
		}
	}

	/* A finger or the mouse has come down on the knob. Nothing moves yet. */
	pressed (event) {

		if (this.disabled || this.held || (event.pointerType === "mouse" && event.button !== 0)) {
			return;
		}

		this.held = true;
		this.pointer = event.pointerId;
		this.carried = 0;
		this.lastX = event.clientX;
		this.lastY = event.clientY;

		this.element.setPointerCapture(event.pointerId);
		this.element.classList.add("knob-held");
		this.element.focus({preventScroll: true});
		this.onHold(true);

		event.preventDefault();
	}

	/* The finger has moved: turn by however many whole steps the drag has added up to. */
	moved (event) {

		if (!this.held || event.pointerId !== this.pointer) {
			return;
		}

		// Up and right are both "more", so the drag can go whichever way is comfortable.
		this.carried += (event.clientX - this.lastX) - (event.clientY - this.lastY);
		this.lastX = event.clientX;
		this.lastY = event.clientY;

		const steps = Math.trunc(this.carried / this.pixelsPerStep);

		if (steps !== 0) {
			this.carried -= steps * this.pixelsPerStep;
			this.onStep(steps);
		}

		event.preventDefault();
	}

	/* The finger has lifted, or the phone has taken the touch away. */
	released (event) {

		if (this.held && event.pointerId === this.pointer) {
			this.letGo();
		}
	}

	/* End a drag. */
	letGo () {

		this.held = false;
		this.pointer = null;
		this.element.classList.remove("knob-held");
		this.onHold(false);
	}

	/* An arrow key moves one step, and Page Up or Page Down several. */
	keyed (event) {

		if (this.disabled) {
			return;
		}

		const steps = {
			ArrowUp: 1,
			ArrowRight: 1,
			ArrowDown: -1,
			ArrowLeft: -1,
			PageUp: this.bigStep,
			PageDown: -this.bigStep,
		}[event.key];

		if (steps === undefined) {
			return;
		}

		this.onStep(steps);
		event.preventDefault();
	}
}
