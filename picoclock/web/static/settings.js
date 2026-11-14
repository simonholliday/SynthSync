/*
The page's second view, for the things that are set once: which WiFi PicoClock
is on, the password of its own network, starting again as new, and how it is
doing.

As everywhere on the page, nothing here decides anything. It asks the Pico and
shows the answer. A form stays open until the Pico has taken what it holds, so
a refusal appears beside what was typed.
*/

// How strong a network has to be, in dBm, to be called each of these. Anything weaker is weak.
const STRENGTHS = [[-55, "strong"], [-67, "good"], [-75, "fair"]];


/* Find one of the page's elements by its id. */
function find (id) {

	return document.getElementById(id);
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

		find("scan").addEventListener("click", () => this.scan());
		find("join-cancel").addEventListener("click", () => this.pick(null));
		find("join-show").addEventListener("change", (event) => showAs(find("join-password"), event.target.checked));
		find("own-show").addEventListener("change", (event) => showAs(find("own-password"), event.target.checked));
		find("forget").addEventListener("click", () => this.ask("join", {do: "forget"}));

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

	/* Ask the Pico for something, remembering what, so its answer can be told from other news. */
	ask (what, message) {

		this.asked = what;

		for (const id of ["join-problem", "host-problem", "own-problem", "own-done", "reset-done"]) {
			find(id).textContent = "";
		}

		// Never held back: these are pressed once, and each one matters.
		this.link.send(message, what, null);
	}

	/* The Pico has said hello: show what never changes, and clear anything half-done from before. */
	hello (fixed) {

		find("version").textContent = "PicoClock " + fixed.version;
		find("networks").replaceChildren();
		find("scan-status").textContent = "";

		this.asked = null;
		this.pick(null);
		this.confirmReset(false);
	}

	/* Take one message from the Pico that is about this view. */
	hear (message) {

		if (message.type === "network") {

			const mine = message.by === this.link.me;

			this.network = message;
			this.showNetwork();

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
			join: "join-problem",
			host_name: "host-problem",
			own_password: "own-problem",
			scan: "scan-status",
		}[shown];

		if (place) {
			find(place).textContent = words;
		}

		this.asked = null;
	}

	/* Say how PicoClock is reached just now, and offer what can be done about it. */
	showNetwork () {

		const network = this.network;
		const none = network.mode === "none";

		// With no WiFi of its own, as when the page is served by something else, there is nothing to set.
		find("wifi").hidden = none;
		find("own").hidden = none;

		if (none) {
			return;
		}

		let status = `PicoClock is running its own network, ${network.own}. This page is at http://${network.address}.`;
		let own = `It is called ${network.own}, and it is on now.`;

		if (network.mode === "joined") {
			status = `PicoClock is on ${network.name}. This page is at http://${network.host_name}.local, or http://${network.address}.`;
			own = `It is called ${network.own}. It is off while PicoClock is on ${network.name}.`;

		} else if (network.mode === "joining") {
			status = `PicoClock is joining ${network.saved}…`;
			own = `It is called ${network.own}. It is off while PicoClock tries to join ${network.saved}.`;
		}

		find("wifi-status").textContent = status;
		find("wifi-problem").textContent = network.problem;
		find("own-status").textContent = own;

		const forget = find("forget");

		forget.hidden = !network.saved;
		forget.textContent = network.mode === "joined"
			? `Leave ${network.saved} and go back to PicoClock's own network`
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
		const leaving = this.network && this.network.mode === "joined" ? `leave ${this.network.name}` : "switch its own network off";
		const host = this.network ? this.network.host_name : "picoclock";

		find("join-name").value = "";
		find("join-name-field").hidden = !network.unlisted;
		find("join-password-field").hidden = !network.secure;
		find("join-show-switch").hidden = !network.secure;
		find("join-go").textContent = network.unlisted ? "Join" : "Join " + network.name;

		find("join-warning").textContent =
			`PicoClock will ${leaving} and join ${name}, and this page will stop responding. `
			+ `Join this phone to ${name} too, then open http://${host}.local. `
			+ "If PicoClock can't join within about 20 seconds, its own network comes back and this page says why.";

		(network.unlisted ? find("join-name") : find("join-password")).focus({preventScroll: true});
		form.scrollIntoView({block: "nearest"});
	}

	/* Ask the Pico to join the network that was picked. */
	join () {

		if (this.picked === null) {
			return;
		}

		this.ask("join", {
			do: "join",
			name: this.picked.unlisted ? find("join-name").value : this.picked.name,
			password: this.picked.secure ? find("join-password").value : "",
		});
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
