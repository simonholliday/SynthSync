/*
The page: it shows what the Pico says, and tells the Pico what was touched.

The settings live in one place, on the Pico. The page asks for a change and
shows the answer. It never decides for itself that a value is allowed. That is
what keeps two phones open at once showing the same thing, and what puts a
control back when the Pico refuses what it was set to.

The one thing the page works out for itself is the lights. The Pico says where
the clock has got to four times a second, and the page keeps time in between.

The Pico sends its messages in numbered batches, and the page answers each one
with its number. That is how the Pico knows what has arrived, and it sends no
more than a little beyond that, so a phone on a poor signal is never sent more
than it can take.
*/

import {Knob} from "./knob.js";
import * as ratio from "./ratio.js";
import {Settings} from "./settings.js";

// How far a finger drags for one step of each knob, in pixels.
const TEMPO_PIXELS = 3;
const SNAP_PIXELS = 12;
const FREE_PIXELS = 5;

// The least time between two messages about the same thing, in milliseconds.
// A dragged knob changes far more often than that, and only the latest is sent.
const SEND_EVERY = 40;

// How long a refusal stays on show, in milliseconds.
const PROBLEM_FOR = 6000;

// How long a light stays on for a pulse too short to see, and for a start or
// a stop pulse, in milliseconds.
const SHORTEST_FLASH = 50;
const SINGLE_FLASH = 120;

// A pulse that comes round faster than this, in milliseconds, is a glow, not a blink.
const FASTEST_BLINK = 70;

// What an output does, as said under its number. A multivider pulses at a
// multiple or a division of the tempo, which is what an output's role "clock" means.
const ROLE_WORDS = {clock: "multivider", start: "start pulse", stop: "stop pulse", run: "run"};

// How far the page's idea of where the clock is may be from the Pico's, in
// thousandths of a beat, before it stops easing towards it and jumps.
const FAR_OUT = 60;


// ---------------------------------------------------------------------------
// The line to the Pico.

class Link {

	/*
	Keep a socket open to the Pico, opening it again whenever it drops.

	`hear` is called with every message the Pico sends, and `connected` with
	true when the Pico has said hello and false when the line is lost.
	*/
	constructor (hear, connected) {

		this.hear = hear;
		this.connected = connected;
		this.me = 0;
		this.socket = null;

		// Messages held back because one about the same thing has just gone.
		this.waiting = new Map();
		this.timer = null;

		// The number of the last message sent about each thing on the page,
		// and which thing each recent message was about.
		this.count = 0;
		this.latest = new Map();
		this.about = new Map();

		// How long to wait before trying again after the line drops.
		this.wait = 500;
	}

	/* Open the socket. */
	open () {

		const scheme = location.protocol === "https:" ? "wss" : "ws";
		const socket = new WebSocket(`${scheme}://${location.host}/api/socket`);

		socket.onmessage = (event) => {

			const batch = JSON.parse(event.data);

			// Said first, so the Pico isn't kept waiting while the page redraws.
			socket.send(JSON.stringify({do: "heard", batch: batch.batch}));

			for (const message of batch.messages) {

				if (message.type === "hello") {
					this.me = message.you;
					this.wait = 500;
					this.connected(true);
				}

				this.hear(message);
			}
		};

		socket.onclose = () => this.lost(socket);

		this.socket = socket;
	}

	/* The line has dropped: forget what was waiting to go, and try again shortly. */
	lost (socket) {

		if (socket !== this.socket) {
			return;
		}

		clearTimeout(this.timer);

		this.socket = null;
		this.timer = null;

		// A change made before the line dropped must not arrive out of the blue later.
		this.waiting.clear();
		this.latest.clear();
		this.about.clear();

		this.connected(false);

		setTimeout(() => this.open(), this.wait);
		this.wait = Math.min(this.wait * 2, 4000);
	}

	/* Say whether a message can be sent just now. */
	get ready () {

		return this.socket !== null && this.socket.readyState === WebSocket.OPEN;
	}

	/*
	Send the Pico a message.

	`shown` names the thing on the page it is about, such as "tempo" or
	"output2". `key` names what it replaces if that hasn't gone yet, which
	is `shown` unless given. With a `key` of null it is never held back,
	and whatever was waiting goes first, so the order is kept.
	*/
	send (message, shown, key = shown) {

		if (!this.ready) {
			return;
		}

		this.count += 1;
		message.id = this.count;

		this.latest.set(shown, this.count);
		this.about.set(this.count, shown);
		this.about.delete(this.count - 200);

		if (key === null) {
			this.pass();
			this.socket.send(JSON.stringify(message));
			return;
		}

		this.waiting.set(key, message);

		if (this.timer === null) {
			this.flush();
		}
	}

	/* Send whatever is waiting, and hold back anything new for a moment. */
	flush () {

		this.timer = null;

		if (!this.ready || this.waiting.size === 0) {
			return;
		}

		this.pass();
		this.timer = setTimeout(() => this.flush(), SEND_EVERY);
	}

	/* Send everything that is waiting. */
	pass () {

		for (const message of this.waiting.values()) {
			this.socket.send(JSON.stringify(message));
		}

		this.waiting.clear();
	}

	/*
	Say whether a message is this page's own change coming back after a newer one has gone.

	A dragged knob has moved on by the time its earlier settings come back,
	and showing them would make it jump backwards under the finger.
	*/
	stale (message, shown) {

		return message.by === this.me && message.id < (this.latest.get(shown) || 0);
	}

	/*
	Give the thing on the page that a refused message was about, and take the next news of it whoever it is from.
	*/
	refused (id) {

		const shown = this.about.get(id);

		if (shown !== undefined) {
			this.latest.delete(shown);
		}

		return shown;
	}
}


// ---------------------------------------------------------------------------
// What the page knows, as the Pico last said it.

const now = {
	fixed: null,
	tempo: 12000,
	tempoText: "120",
	transport: "stopped",
	outputs: [],
};

// The musical values, as the Pico lists them: their texts in order, and each one's label.
let snaps = [];

// Each output's row: its element and the controls in it.
const rows = [];

// The timer that clears each refusal on show, by the element it is shown in.
const problemTimers = new Map();

const link = new Link(hear, connected);
const settings = new Settings(link);

const tempoKnob = new Knob(document.getElementById("tempo-knob"), {
	label: "Tempo",
	pixelsPerStep: TEMPO_PIXELS,
	bigStep: 10,
	onStep: turnTempo,
});

const tempoText = document.getElementById("tempo-text");
const tempoProblem = document.getElementById("tempo-problem");

const playPause = document.getElementById("play-pause");
const stopButton = document.getElementById("stop");


/* The line to the Pico has come up or gone down. */
function connected (up) {

	lights.up = up;
	document.body.classList.toggle("lost", !up);

	// When PicoClock has changed networks, trying again here won't find it, so the page says where it has gone instead.
	const where = up ? [] : settings.whereNext();
	const panel = document.getElementById("where");
	const news = where.length > 0 && panel.childNodes.length === 0;

	document.getElementById("link").textContent = up ? "" : where.length > 0 ? "Not connected." : "Not connected. Trying again…";
	panel.replaceChildren(...where);

	// The panel is at the top of the page, out of sight of whatever was pressed lower down to send PicoClock away.
	if (news) {
		window.scrollTo(0, 0);
	}

	// While it's down nothing can be changed, so nothing should look as if it can.
	document.getElementById("main-view").inert = !up;
	document.getElementById("settings-view").inert = !up;
}


/* Show the main view, or the settings when the address ends in #settings. */
function showView () {

	const inSettings = location.hash === "#settings";

	document.getElementById("main-view").hidden = inSettings;
	document.getElementById("settings-view").hidden = !inSettings;
	document.getElementById("to-settings").hidden = inSettings;
	document.getElementById("to-main").hidden = !inSettings;

	window.scrollTo(0, 0);
}


/* Take one message from the Pico. */
function hear (message) {

	if (message.type === "hello") {

		const transport = message.now.find((each) => each.type === "transport");

		now.fixed = message.fixed;
		snaps = message.fixed.snaps.map((snap) => snap.ratio);

		// This is how things already stand, not a start or a stop that has just happened.
		now.transport = transport ? transport.state : "stopped";
		lights.forget();

		build(message.now.filter((each) => each.type === "output").length);
		settings.hello(message.fixed);
		message.now.forEach((each) => hear(each));

	} else if (message.type === "tempo") {

		if (link.stale(message, "tempo")) {
			return;
		}

		// The lights are placed with the tempo as it was up to this moment.
		lights.hold();

		now.tempo = message.tempo;
		now.tempoText = message.text;
		showTempo();

	} else if (message.type === "output") {

		if (link.stale(message, "output" + message.index)) {
			return;
		}

		now.outputs[message.index] = message;
		showOutput(message.index);

	} else if (message.type === "transport") {

		lights.turned(now.transport, message.state);

		now.transport = message.state;
		showTransport();

	} else if (message.type === "clock") {

		lights.heard(message.position);
		settings.hear(message);

	} else if (message.type === "network" || message.type === "networks") {

		settings.hear(message);

	} else if (message.type === "error") {

		const shown = link.refused(message.id);

		// A refusal belongs beside whatever it was about, in whichever view that is.
		if (!showProblem(shown, message.message)) {
			settings.problem(shown, message.message);
		}
	}
}


// ---------------------------------------------------------------------------
// The tempo.

/* Show the tempo on its knob and, unless it's being typed into, in its box. */
function showTempo () {

	const slowest = now.fixed.slowest_tempo;
	const fastest = now.fixed.fastest_tempo;

	tempoKnob.point((now.tempo - slowest) / (fastest - slowest));
	tempoKnob.say(now.tempoText + " BPM", now.tempo / 100, slowest / 100, fastest / 100);

	if (document.activeElement !== tempoText) {
		tempoText.value = now.tempoText;
	}
}


/* The tempo knob has turned: move to the next whole BPM that way, and on from there. */
function turnTempo (steps) {

	const whole = steps > 0 ? Math.floor(now.tempo / 100) : Math.ceil(now.tempo / 100);
	const slowest = Math.ceil(now.fixed.slowest_tempo / 100);
	const fastest = Math.floor(now.fixed.fastest_tempo / 100);
	const tempo = Math.min(Math.max(whole + steps, slowest), fastest) * 100;

	if (tempo === now.tempo) {
		return;
	}

	// Shown at once, so the knob follows the finger. The Pico's answer confirms it.
	lights.hold();
	now.tempo = tempo;
	now.tempoText = String(tempo / 100);
	showTempo();
	showProblem("tempo", "");

	link.send({do: "tempo", value: tempo}, "tempo");
}


tempoText.addEventListener("focus", () => tempoText.select());

tempoText.addEventListener("keydown", (event) => {

	// Enter finishes typing. Leaving the box is what sends it.
	if (event.key === "Enter") {
		tempoText.blur();
	}
});

tempoText.addEventListener("change", () => {

	showProblem("tempo", "");
	link.send({do: "tempo", text: tempoText.value}, "tempo");
});

tempoText.addEventListener("blur", () => {

	// If nothing was typed, or the Pico has yet to answer, show what is known meanwhile.
	if (tempoText.value.trim() === "") {
		tempoText.value = now.tempoText;
	}
});


// ---------------------------------------------------------------------------
// Play/pause and stop, as on the Pico's own buttons.

/* Say what the clock is doing, as a media player does.

Exactly one of the two is lit: play/pause while the clock is playing or
paused, and stop while it is stopped. Play/pause shows what a tap on it will
do next: pause while the clock runs, and play otherwise. */
function showTransport () {

	const running = now.transport === "running";
	const stopped = now.transport === "stopped";

	playPause.classList.toggle("running", running);
	playPause.classList.toggle("lit", !stopped);
	playPause.setAttribute("aria-label", running ? "Pause" : "Play");

	stopButton.classList.toggle("lit", stopped);
	stopButton.setAttribute("aria-pressed", stopped ? "true" : "false");
}


// A tap sends what the button meant as it was tapped, start or pause, never
// "the other one", so two phones tapping at once, or one message sent twice,
// can't undo each other. These are never held back or dropped: a stop then a
// start is not the same as a start.
playPause.addEventListener("click", () => link.send({do: now.transport === "running" ? "pause" : "start"}, "transport", null));
stopButton.addEventListener("click", () => link.send({do: "stop"}, "transport", null));


// ---------------------------------------------------------------------------
// The outputs.

/* Make a row for each output, replacing any that were there. */
function build (count) {

	const holder = document.getElementById("outputs");
	const template = document.getElementById("output-row");

	holder.replaceChildren();
	rows.length = 0;
	now.outputs.length = 0;

	for (let index = 0; index < count; index++) {

		const element = template.content.firstElementChild.cloneNode(true);
		const number = index + 1;

		const row = {
			element,
			light: element.querySelector(".light"),
			text: element.querySelector(".ratio-text"),
			snap: element.querySelector(".snap"),
			more: element.querySelector(".more"),
			extra: element.querySelector(".extra"),
			problem: element.querySelector(".problem"),
			on: element.querySelector(".on"),
			role: element.querySelector(".role"),
			pulseKind: element.querySelector(".pulse-kind"),
			pulse: element.querySelector(".pulse"),
			pulseField: element.querySelector(".pulse-field"),
			kind: element.querySelector(".ratio-kind"),
			knob: null,
		};

		element.setAttribute("aria-label", "Output " + number);
		element.querySelector(".name").textContent = number;

		row.problem.id = "problem-" + number;
		row.more.setAttribute("aria-label", "More settings for output " + number);
		row.on.setAttribute("aria-label", "Output " + number);
		row.pulse.min = now.fixed.shortest_pulse;
		row.pulse.max = now.fixed.longest_pulse;

		if (index === 0) {

			// Output 1 is the main clock: always a clock, always once a beat.
			element.querySelector(".ratio-knob").remove();
			element.querySelector(".snap-switch").remove();
			element.querySelector(".role-field").remove();
			element.querySelector(".ratio-number").remove();

		} else {

			element.querySelector(".fixed-ratio").remove();

			row.text.setAttribute("aria-label", "Output " + number + " multivider");
			row.text.setAttribute("aria-describedby", row.problem.id);
			row.snap.setAttribute("aria-label", "Snap output " + number + " to musical values");

			row.knob = new Knob(element.querySelector(".ratio-knob"), {
				label: "Output " + number + " multivider",
				origin: ratio.along("1", now.fixed.slowest_ratio, now.fixed.fastest_ratio),
				onStep: (steps) => turnRatio(index, steps),
			});

			row.text.addEventListener("focus", () => row.text.select());

			row.text.addEventListener("keydown", (event) => {

				if (event.key === "Enter") {
					row.text.blur();
				}
			});

			row.text.addEventListener("change", () => tell(index, "ratio", row.text.value));

			row.text.addEventListener("blur", () => {

				if (row.text.value.trim() === "" && now.outputs[index]) {
					row.text.value = now.outputs[index].label;
				}
			});

			row.snap.addEventListener("change", () => choose(index, "snap", row.snap.checked));
			row.role.addEventListener("change", () => choose(index, "role", row.role.value));
		}

		row.more.addEventListener("click", () => {

			row.extra.hidden = !row.extra.hidden;
			row.more.setAttribute("aria-expanded", row.extra.hidden ? "false" : "true");
		});

		// The Pico calls an output that is off "muted", so what is sent is the opposite of what the switch shows.
		row.on.addEventListener("change", () => choose(index, "muted", !row.on.checked));

		row.pulseKind.addEventListener("change", () => {

			if (row.pulseKind.value === "half") {
				choose(index, "pulse", "half");
			} else {
				tell(index, "pulse", Number(row.pulse.value) || 10);
			}
		});

		row.pulse.addEventListener("change", () => tell(index, "pulse", Number(row.pulse.value)));

		row.pulse.addEventListener("keydown", (event) => {

			// Enter finishes typing, as it does in the other boxes, so the Pico's answer can be shown.
			if (event.key === "Enter") {
				row.pulse.blur();
			}
		});

		holder.append(element);
		rows.push(row);
	}
}


/* Ask the Pico to change one setting of one output. */
function tell (index, name, value) {

	showProblem("output" + index, "");
	link.send({do: "output", index, name, value}, "output" + index, "output" + index + "." + name);
}


/*
Set something that is picked, not typed: a switch, or one of a list.

Whatever is picked is allowed, so the page takes it as set at once and the
next touch already goes by it. A knob turned straight after its snap switch
steps the new way, without waiting to hear back.
*/
function choose (index, name, value) {

	if (now.outputs[index]) {
		now.outputs[index][name] = value;
		showOutput(index);
	}

	tell(index, name, value);
}


/* Say a ratio's label in words, for a screen reader. */
function inWords (label) {

	return label.replace("×", "times ").replace("÷", "divided by ");
}


/* Show one output's settings in its row. */
function showOutput (index) {

	const output = now.outputs[index];
	const row = rows[index];

	if (!output || !row) {
		return;
	}

	const half = output.pulse === "half";
	const on = !output.muted;

	// Off, an output is silent and its row dimmed, but every setting can still be changed, so a
	// change can be set up quietly and brought in with the switch.
	row.element.classList.toggle("off", !on);
	row.on.checked = on;

	row.pulseKind.value = half ? "half" : "length";
	row.pulseField.hidden = half;

	if (!half && document.activeElement !== row.pulse) {
		row.pulse.value = output.pulse;
	}

	if (index === 0) {
		return;
	}

	// Only a clock has a ratio to set.
	const clock = output.role === "clock";

	row.snap.checked = output.snap;
	row.snap.disabled = !clock;
	row.role.value = output.role;
	row.kind.textContent = ROLE_WORDS[output.role] || "";
	row.text.disabled = !clock;

	row.knob.disable(!clock);
	row.knob.pixelsPerStep = output.snap ? SNAP_PIXELS : FREE_PIXELS;
	row.knob.bigStep = output.snap ? 3 : 12;
	row.knob.point(ratio.along(output.ratio, now.fixed.slowest_ratio, now.fixed.fastest_ratio));

	row.knob.say(
		inWords(output.label),
		Number(ratio.toNumber(output.ratio).toFixed(4)),
		Number(ratio.toNumber(now.fixed.slowest_ratio).toFixed(4)),
		ratio.toNumber(now.fixed.fastest_ratio),
	);

	if (document.activeElement !== row.text) {
		row.text.value = output.label;
	}
}


/* A ratio knob has turned: step to the next musical value, or freely, as its snap switch says. */
function turnRatio (index, steps) {

	const output = now.outputs[index];

	if (!output) {
		return;
	}

	const next = output.snap
		? ratio.stepSnap(output.ratio, snaps, steps)
		: ratio.stepFree(output.ratio, steps, now.fixed.fastest_ratio);

	if (next === output.ratio) {
		return;
	}

	// Shown at once, so the knob follows the finger. The Pico's answer confirms it.
	output.ratio = next;
	output.label = ratio.label(next);
	showOutput(index);

	tell(index, "ratio", next);
}


// ---------------------------------------------------------------------------
// Refusals.

/*
Show why the Pico refused something beside the control it was about, or clear what was shown there.

Says whether the control was one of the main view's.
*/
function showProblem (shown, words) {

	let element = null;

	if (shown === "tempo") {
		element = tempoProblem;

	} else if (shown && shown.startsWith("output") && rows[Number(shown.slice(6))]) {
		element = rows[Number(shown.slice(6))].problem;
	}

	if (element === null) {
		return false;
	}

	clearTimeout(problemTimers.get(element));
	element.textContent = words;

	if (words) {
		problemTimers.set(element, setTimeout(() => { element.textContent = ""; }, PROBLEM_FOR));
	}

	return true;
}


// ---------------------------------------------------------------------------
// The lights.

const lights = {

	// Whether the Pico can be heard. While it can't, the page doesn't know what
	// the clock is doing, so no light is lit, though the clock may well play on.
	up: false,

	// Where the clock was, in thousandths of a beat, at a moment on the page's
	// own clock. Before the first beat there is no position, which is -1.
	position: -1,
	at: 0,

	// When the clock last started from zero and last stopped, for the outputs
	// that pulse once on each.
	started: -Infinity,
	stopped: -Infinity,

	/* Forget where the clock was, and any start or stop, as when the page has only just heard from the Pico. */
	forget () {

		this.position = -1;
		this.started = -Infinity;
		this.stopped = -Infinity;
	},

	/* Give where the clock is at a moment, keeping time from where it was last known to be. */
	where (moment) {

		if (this.position < 0 || now.transport !== "running") {
			return this.position;
		}

		// The tempo is in hundredths of a BPM, so this many thousandths of a beat pass each millisecond.
		return this.position + (moment - this.at) * now.tempo / 6000;
	},

	/* Fix the position as it is right now, before something that changes how fast it moves. */
	hold () {

		const moment = performance.now();

		this.position = this.where(moment);
		this.at = moment;
	},

	/* The Pico has said where the clock is. Ease towards it if it's close, and jump if it isn't. */
	heard (position) {

		if (position < 0) {

			// It can't say just now. Unless the clock is running, that means it's at zero.
			if (now.transport !== "running") {
				this.position = -1;
			}

			return;
		}

		const moment = performance.now();
		const expected = this.where(moment);
		const out = position - expected;

		this.position = expected < 0 || Math.abs(out) > FAR_OUT ? position : expected + out / 5;
		this.at = moment;
	},

	/* The clock has been started, paused or stopped. */
	turned (before, after) {

		const moment = performance.now();

		this.hold();

		if (after === "stopped") {

			this.position = -1;

			if (before !== "stopped") {
				this.stopped = moment;
			}

		} else if (before === "stopped") {

			// Starting from zero. The Pico's next word on where it is will correct this.
			this.position = 0;
			this.started = moment;
		}
	},

	/* Say whether one output's light is on at a moment: true, false, or "fast" for a glow. */
	lit (output, position, moment) {

		if (output.muted) {
			return false;
		}

		if (output.role === "run") {
			return now.transport === "running";
		}

		if (output.role === "start") {
			return moment - this.started < SINGLE_FLASH;
		}

		if (output.role === "stop") {
			return moment - this.stopped < SINGLE_FLASH;
		}

		if (now.transport !== "running" || position < 0) {
			return false;
		}

		// How long one pulse and the gap after it last, and how far into that this moment is.
		const each = ratio.toNumber(output.ratio);
		const gap = 6000000 / now.tempo / each;
		const into = ((position / 1000 * each) % 1) * gap;

		if (gap < FASTEST_BLINK) {
			return "fast";
		}

		const length = output.pulse === "half" ? gap / 2 : Math.min(output.pulse, gap / 2);

		return into < Math.max(length, Math.min(SHORTEST_FLASH, gap / 2));
	},

	/* Set every light for this moment, and come back for the next. */
	draw (moment) {

		const position = this.where(moment);

		for (let index = 0; index < rows.length; index++) {

			const output = now.outputs[index];
			const state = output && this.up ? this.lit(output, position, moment) : false;

			rows[index].light.classList.toggle("on", state === true);
			rows[index].light.classList.toggle("fast", state === "fast");
		}

		requestAnimationFrame((next) => this.draw(next));
	},
};


// ---------------------------------------------------------------------------

window.addEventListener("hashchange", showView);

showView();
link.open();
requestAnimationFrame((moment) => lights.draw(moment));
