/*
The page's second view, for the things that are set once: how many pulses the
clock sends a beat, how the page looks on this phone, which WiFi SynthSync is
on, the password of its own network, starting again as new, and how it is
doing. At its top, and only when
there is something to say, it says what in config.txt couldn't be used.

As everywhere on the page, nothing here decides anything. It asks the Pico and
shows the answer. A form stays open until the Pico has taken what it holds, so
a refusal appears beside what was typed.
*/

// How strong a network has to be, in dBm, to be called each of these. Anything weaker is weak.
const STRENGTHS = [[-55, "strong"], [-67, "good"], [-75, "fair"]];

// Where the page is on SynthSync's own network, which MicroPython chooses.
const OWN_ADDRESS = "192.168.4.1";

// The note each of the clock's pulses a beat gives, where it is one a musician would name, or what uses it.
const RATE_WORDS = {1: "quarters", 2: "8ths", 4: "16ths", 8: "32nds", 24: "DIN sync"};


// The looks a phone can choose, each by the name page.css knows it by and the name shown, Dark first among them
// because it is what a phone that hasn't chosen gets. A test holds this list to the looks page.css draws.
const LOOKS = [
	["system", "Match phone"], ["dark", "Dark"], ["light", "Light"],
	["modular", "Modular"], ["phosphor", "Phosphor"], ["phaedra", "Phaedra"], ["constructor", "Constructor"],
	["aluminium", "Aluminium"], ["airports", "Airports"], ["oxygene", "Oxygène"], ["workbench", "Workbench"],
];

// The look a phone has until it chooses one, and where this phone keeps its choice. index.html reads it too,
// before the page is first drawn.
const FIRST_LOOK = "dark";
const LOOK_KEY = "synthsync.look";


/* Find one of the page's elements by its id. */
function find (id) {

	return document.getElementById(id);
}


/* Make a link to an address, showing the address itself. */
function addressLink (address) {

	const link = document.createElement("a");

	link.href = address;
	link.textContent = address;

	return link;
}


/* Make a password box show what's typed, or hide it, as its switch says. */
function showAs (box, shown) {

	box.type = shown ? "text" : "password";
}


export class Settings {

	/* Wire the view up to the line it sends its changes on. */
	constructor (link) {

		this.link = link;

		// What the Pico last said about the network, and the network picked to join, if one is.
		this.network = null;
		this.picked = null;

		// What has been asked of the Pico and not yet answered: "join", "own_password", "host_name" or "reset".
		this.asked = null;

		// Where SynthSync is heading when it changes networks, which takes this page's line with it, or null:
		// {to: "trial", name} while it tries a network, {to: "switch", name, at} once it is going to one it found,
		// and {to: "own"} when it is going back to its own. `from` is the mode it was in when this was last decided.
		this.going = null;

		// Whether this page asked to try a network and has yet to hear how it went. It survives the line
		// going and coming back, which a trial from SynthSync's own network may or may not take with it.
		this.trying = false;

		this.showLooks();

		find("scan").addEventListener("click", () => this.scan());
		find("join-cancel").addEventListener("click", () => this.pick(null));
		find("join-show").addEventListener("change", (event) => showAs(find("join-password"), event.target.checked));
		find("own-show").addEventListener("change", (event) => showAs(find("own-password"), event.target.checked));
		find("forget").addEventListener("click", () => this.forget());
		find("switch").addEventListener("click", () => this.switchNow());

		find("join").addEventListener("submit", (event) => {

			event.preventDefault();
			this.join();
		});

		find("host").addEventListener("submit", (event) => {

			event.preventDefault();
			this.ask("host_name", {do: "host_name", value: find("host-name").value});
		});

		find("own-form").addEventListener("submit", (event) => {

			event.preventDefault();
			this.ask("own_password", {do: "own_password", value: find("own-password").value});
		});

		find("reset-ask").addEventListener("click", () => this.confirmReset(true));
		find("reset-cancel").addEventListener("click", () => this.confirmReset(false));

		find("reset-go").addEventListener("click", () => {

			this.confirmReset(false);
			this.ask("reset", {do: "reset"});
		});
	}

	/* Offer every look, with this phone's choice ticked, and wear it. Nothing about it is sent to the Pico. */
	showLooks () {

		let chosen = FIRST_LOOK;

		// A browser that won't keep anything still shows every look; it just forgets the choice.
		try {
			chosen = localStorage.getItem(LOOK_KEY) || FIRST_LOOK;
		} catch (problem) {}

		find("looks").replaceChildren(...LOOKS.map(([key, name]) => {

			const choice = document.createElement("label");
			const box = document.createElement("input");
			const swatch = document.createElement("i");

			box.type = "radio";
			box.name = "look";
			box.value = key;
			box.checked = key === chosen;
			swatch.dataset.theme = key;

			box.addEventListener("change", () => {

				try {
					localStorage.setItem(LOOK_KEY, key);
				} catch (problem) {}

				this.wear(key);
			});

			choice.className = "look";
			choice.append(box, swatch, name);

			return choice;
		}));

		this.wear(chosen);
	}

	/* Put a look on the page, and colour the phone's own bar to match it. */
	wear (key) {

		document.documentElement.dataset.theme = key;
		document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue("--ground").trim();
	}

	/* Ask the Pico for something, remembering what, so its answer can be told from other news. */
	ask (what, message) {

		this.asked = what;
		this.trying = message.do === "join";

		for (const id of ["join-problem", "host-problem", "own-problem", "own-done", "reset-done"]) {
			find(id).textContent = "";
		}

		// Never held back: these are pressed once, and each one matters.
		this.link.send(message, what, null);
	}

	/* The Pico has said hello: show what never changes, and clear anything half-done from before. */
	hello (fixed) {

		find("version").textContent = "SynthSync " + fixed.version;
		find("rates").replaceChildren(...fixed.ppqns.map((rate) => this.rate(rate)));
		find("config").hidden = fixed.config_problems.length === 0;

		find("config-problems").replaceChildren(...fixed.config_problems.map((problem) => {

			const item = document.createElement("li");

			item.textContent = problem;

			return item;
		}));

		find("ppqn-problem").textContent = "";
		find("networks").replaceChildren();
		find("scan-status").textContent = "";

		this.asked = null;
		this.going = null;
		this.pick(null);
		this.confirmReset(false);
	}

	/* Make the choice of one of the clock's pulses a beat, which asks for it as soon as it is chosen. */
	rate (rate) {

		const choice = document.createElement("label");
		const box = document.createElement("input");
		const words = document.createElement("span");
		const number = document.createElement("b");

		box.type = "radio";
		box.name = "ppqn";
		box.value = String(rate);
		box.setAttribute("aria-label", rate + " PPQN" + (RATE_WORDS[rate] ? ", " + RATE_WORDS[rate] : ""));

		box.addEventListener("change", () => {

			find("ppqn-problem").textContent = "";
			this.link.send({do: "ppqn", value: rate}, "ppqn", null);
		});

		number.textContent = String(rate);
		words.append(number);

		if (RATE_WORDS[rate]) {
			words.append(" · " + RATE_WORDS[rate]);
		}

		choice.className = "rate";
		choice.append(box, words);

		return choice;
	}

	/* Take one message from the Pico that is about this view. */
	hear (message) {

		if (message.type === "ppqn") {

			for (const box of find("rates").querySelectorAll("input")) {
				box.checked = Number(box.value) === message.ppqn;
			}

		} else if (message.type === "network") {

			const mine = message.by === this.link.me;
			const yes = mine && this.asked === "join";

			this.network = message;
			this.follow(message);
			this.showNetwork();

			// How a trial went, found or back on the own network with why, is news after the Pico's yes to the
			// asking, which carries this page's number too. It is shown at the top of the WiFi section, out of
			// sight of the form that asked.
			if (this.trying && !yes && (message.mode === "found" || message.mode === "own")) {
				this.trying = false;
				find("wifi").scrollIntoView({block: "start"});
			}

			// News of the network that this page caused is the Pico's yes to what was asked.
			if (mine && this.asked === "join") {
				this.pick(null);

			} else if (mine && this.asked === "own_password") {
				find("own-password").value = "";
				find("own-done").textContent = "The password is changed.";

			} else if (mine && this.asked === "reset") {
				find("reset-done").textContent = "Every setting is as new.";
			}

			if (mine) {
				this.asked = null;
			}

		} else if (message.type === "networks") {

			this.showFound(message.found);

		} else if (message.type === "clock") {

			this.showFaults(message.faults);
		}
	}

	/* Show why the Pico refused something this view asked for. */
	problem (shown, words) {

		const place = {
			ppqn: "ppqn-problem",
			join: "join-problem",
			host_name: "host-problem",
			own_password: "own-problem",
			scan: "scan-status",
		}[shown];

		if (place) {
			find(place).textContent = words;
		}

		this.asked = null;
		this.trying = false;
	}

	/* Say how SynthSync is reached just now, and offer what can be done about it. */
	showNetwork () {

		const network = this.network;
		const none = network.mode === "none";

		// With no WiFi of its own, as when the page is served by something else, there is nothing to set.
		find("wifi").hidden = none;
		find("own").hidden = none;

		if (none) {
			return;
		}

		let status = `SynthSync is running its own network, ${network.own}. This page is at http://${network.address}.`;
		let own = `It is called ${network.own}, and it is on now.`;

		if (network.mode === "joined") {
			status = `SynthSync is on ${network.name}. This page is at http://${network.address}, or http://${network.host_name}.local.`;
			own = `It is called ${network.own}. It is off while SynthSync is on ${network.name}.`;

		} else if (network.mode === "joining") {
			status = `SynthSync is joining ${network.saved}…`;
			own = `It is called ${network.own}. It is off while SynthSync tries to join ${network.saved}.`;

		} else if (network.mode === "found") {
			status = `SynthSync can join ${network.name}. Once it has, this page will be at http://${network.found_at}.`;
			own = `It is called ${network.own}, and it is on until SynthSync switches to ${network.name}.`;

		} else if (network.saved) {
			// Back on its own network with one still saved, which it listens for by itself.
			status += ` When ${network.saved} is back in range, SynthSync goes back to it by itself, as soon as no phone is on ${network.own}.`;
		}

		find("trial").hidden = network.mode !== "found";
		find("trial-address").textContent = `http://${network.found_at}`;
		find("trial-words").textContent =
			`Note that address down. Then switch, join this phone to ${network.name}, and open the address there. `
			+ "If nobody switches, SynthSync does by itself two minutes after it found the network.";
		find("switch").textContent = `Switch to ${network.name} now`;

		find("wifi-status").textContent = status;
		find("wifi-problem").textContent = network.problem;
		find("own-status").textContent = own;

		const forget = find("forget");

		forget.hidden = !network.saved;
		forget.textContent = network.mode === "joined"
			? `Leave ${network.saved} and go back to SynthSync's own network`
			: `Forget ${network.saved}`;

		if (document.activeElement !== find("host-name")) {
			find("host-name").value = network.host_name;
		}
	}

	/* Ask the Pico which networks are in range. */
	scan () {

		find("networks").replaceChildren();
		find("scan-status").textContent = "Looking for networks…";

		this.pick(null);
		this.link.send({do: "scan"}, "scan", null);
	}

	/* List the networks the Pico found, each one a button that picks it. */
	showFound (found) {

		const list = find("networks");

		list.replaceChildren();
		find("scan-status").textContent = found.length ? "" : "No networks were found. Others can still be joined by name.";

		const choices = found.map((network) => {

			const strength = (STRENGTHS.find(([least]) => network.strength >= least) || [0, "weak"])[1];

			return [network, network.name, strength + (network.secure ? ", needs a password" : ", open")];
		});

		// One that doesn't announce itself can still be joined by typing its name.
		choices.push([{name: "", secure: true, unlisted: true}, "Another network…", "one that isn't listed"]);

		for (const [network, name, about] of choices) {

			const item = document.createElement("li");
			const button = document.createElement("button");
			const title = document.createElement("b");
			const detail = document.createElement("span");

			button.type = "button";
			button.className = "found";
			title.textContent = name;
			detail.textContent = about;

			button.append(title, detail);
			button.addEventListener("click", () => this.pick(network));
			item.append(button);
			list.append(item);
		}
	}

	/* Open the form for joining one network, or close it with null. */
	pick (network) {

		const form = find("join");

		this.picked = network;
		form.hidden = network === null;

		find("join-password").value = "";
		find("join-problem").textContent = "";

		if (network === null) {
			return;
		}

		const name = network.unlisted ? "that network" : network.name;
		const joined = this.network !== null && this.network.mode === "joined";
		const own = this.network ? this.network.own : "its own network";

		find("join-name").value = "";
		find("join-name-field").hidden = !network.unlisted;
		find("join-password-field").hidden = !network.secure;
		find("join-show-switch").hidden = !network.secure;
		find("join-go").textContent = network.unlisted ? "Join" : "Join " + network.name;

		// SynthSync always says how a join went on its own network, which a page on another network can't see.
		find("join-warning").textContent = joined
			? `SynthSync will leave ${this.network.name} to try ${name}, and this page will stop responding. `
				+ `To see how it went, join this phone to SynthSync's own network, ${own}, and open http://${OWN_ADDRESS}. `
				+ `It says there where SynthSync will be on ${name}, or why it couldn't join.`
			: `SynthSync will switch its own network off to try ${name}, and this page will stop responding for a few seconds. `
				+ `Once SynthSync has joined, its own network comes back, and this page says where SynthSync will be on ${name}. `
				+ "If it can't join within about 20 seconds, its own network comes back and this page says why.";

		(network.unlisted ? find("join-name") : find("join-password")).focus({preventScroll: true});
		form.scrollIntoView({block: "nearest"});
	}

	/* Ask the Pico to join the network that was picked. */
	join () {

		if (this.picked === null) {
			return;
		}

		const name = this.picked.unlisted ? find("join-name").value : this.picked.name;

		this.going = {to: "trial", name, from: this.mode()};

		this.ask("join", {
			do: "join",
			name,
			password: this.picked.secure ? find("join-password").value : "",
		});
	}

	/* Ask the Pico to forget its network, which takes it back to its own. */
	forget () {

		// Forgotten from a page on that network, SynthSync takes the line with it.
		if (this.mode() === "joined") {
			this.going = {to: "own", from: "joined"};
		}

		this.ask("join", {do: "forget"});
	}

	/* Ask the Pico to join now the network it found, without waiting. */
	switchNow () {

		const network = this.network;

		this.going = {to: "switch", name: network.name, at: network.found_at, from: "found"};
		this.ask("switch", {do: "switch"});
	}

	/* Say what the network was last heard to be doing, or "none". */
	mode () {

		return this.network ? this.network.mode : "none";
	}

	/*
	Keep up with where SynthSync is heading from what the Pico says of its network.

	News in the mode SynthSync was in when that was last decided says nothing new: it is
	the answer to what this page asked, sent before the Pico has acted on it.
	*/
	follow (network) {

		if (this.going !== null && network.mode === this.going.from) {
			return;
		}

		if (network.mode === "joining") {

			// The join that ends a stay on the own network is as much a switch as the one asked for.
			if (this.going === null || this.going.to !== "switch") {
				this.going = {to: "trial", name: network.name, from: "joining"};
			}

		} else if (network.mode === "found") {
			// From here SynthSync only ever goes to the network it found, when asked or two minutes on.
			this.going = {to: "switch", name: network.name, at: network.found_at, from: "found"};

		} else {
			this.going = null;
		}
	}

	/* Say where to find SynthSync, as things to show, when the line has gone because SynthSync changed networks, or nothing. */
	whereNext () {

		const going = this.going;
		const own = this.network ? this.network.own : "its own network";

		if (going === null) {
			return [];
		}

		if (going.to === "trial") {
			return [
				`SynthSync is trying ${going.name}. In a few seconds its own network, ${own}, comes back to say where SynthSync will be. `
				+ `If this phone has gone to another network meanwhile, join it to ${own} again.`,
			];
		}

		if (going.to === "switch") {
			return [`SynthSync is switching to ${going.name}. Join this phone to ${going.name} too, then open `, addressLink(`http://${going.at}`), "."];
		}

		return [`SynthSync is going back to its own network, ${own}. Join this phone to it, then open `, addressLink(`http://${OWN_ADDRESS}`), "."];
	}

	/* Show the question that comes before a reset, or put it away. */
	confirmReset (asking) {

		find("reset-confirm").hidden = !asking;
		find("reset-ask").hidden = asking;
	}

	/* Say how many times the timing has slipped, which should be never. */
	showFaults (faults) {

		const place = find("faults");

		place.classList.toggle("problem", faults > 0);
		place.textContent = faults === 0
			? "Timing faults: 0, as it should be."
			: `Timing faults: ${faults}. The clock has slipped that many times since it was switched on, which it never should.`;
	}
}
