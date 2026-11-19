/*
What the page needs to know about ratios: where one sits along a knob, and
what a turn of the knob makes of it.

A ratio travels as text, the way the Pico sends it: "3/2" is three pulses to
two beats, and "2" is two pulses a beat. The Pico does all the checking. This
only places a ratio and steps from it.
*/

// How many steps of a freely moving knob double a ratio.
const STEPS_PER_DOUBLING = 24;


/* Read a ratio as a plain number: "3/2" is 1.5. */
export function toNumber (text) {

	const [pulses, beats] = String(text).split("/");

	return Number(pulses) / Number(beats || 1);
}


/* Give a ratio's two whole numbers in lowest terms, from "3/2" or from a decimal such as "1.5". */
function parts (text) {

	let [pulses, beats] = String(text).split("/").map(Number);

	if (beats === undefined) {
		// A decimal has at most two places, so it is so many hundredths.
		pulses = Math.round(pulses * 100);
		beats = 100;
	}

	let first = pulses;
	let second = beats;

	while (second) {
		[first, second] = [second, first % second];
	}

	return [pulses / first, beats / first];
}


/* Write one number over another as a decimal if it comes out even within two places, and as nothing if not. */
function shortDecimal (top, bottom) {

	return (top * 100) % bottom ? "" : String(top / bottom);
}


/*
Write a ratio the way the page shows it: ×2, ÷3, ×1.5, ÷1.5, ×4/3.

The Pico sends this with every ratio. The page works it out too, by the same
rules, only so that a knob can show it while it is still being turned.
*/
export function label (text) {

	const [pulses, beats] = parts(text);

	if (beats === 1) {
		return "×" + pulses;
	}

	if (pulses === 1) {
		return "÷" + beats;
	}

	// Slower than the beat reads best as a division when that comes out even.
	if (pulses < beats && shortDecimal(beats, pulses)) {
		return "÷" + shortDecimal(beats, pulses);
	}

	return "×" + (shortDecimal(pulses, beats) || pulses + "/" + beats);
}


/*
Give where a ratio sits along a knob's travel, from 0 at the slowest to 1 at the fastest.

It is spread the way it sounds: halving and doubling are the same distance
apart all the way along, so ÷2 is as far below ×1 as ×2 is above it.
*/
export function along (text, slowest, fastest) {

	const low = Math.log(toNumber(slowest));
	const high = Math.log(toNumber(fastest));

	return (Math.log(toNumber(text)) - low) / (high - low);
}


/*
Give the musical values as ratios of the clock, slowest first, when it sends `ppqn` pulses a beat.

The Pico lists them once, as pulses a beat, since they are the same notes at
every PPQN. Divided by the PPQN, they are what a knob stops on: at 4, a
quarter note is "1/4". Those slower than `slowest` or faster than `fastest`
are left out, as the Pico would refuse them, and `fastest` is always the last,
so that a knob can reach it.
*/
export function ofClock (beatSnaps, ppqn, slowest, fastest) {

	const low = toNumber(slowest) - 1e-9;
	const high = toNumber(fastest) + 1e-9;
	const found = [];

	for (const snap of beatSnaps) {

		const [pulses, beats] = parts(snap);
		let first = pulses;
		let second = beats * ppqn;

		while (second) {
			[first, second] = [second, first % second];
		}

		const top = pulses / first;
		const bottom = beats * ppqn / first;
		const text = bottom === 1 ? String(top) : top + "/" + bottom;

		if (toNumber(text) >= low && toNumber(text) <= high) {
			found.push(text);
		}
	}

	if (found.length === 0 || toNumber(found[found.length - 1]) < toNumber(fastest) - 1e-9) {
		found.push(String(fastest));
	}

	return found;
}


/*
Step through the musical values: give the one `steps` on from a ratio.

`snaps` lists them from slowest to fastest. The ratio needn't be one of them:
the first step then goes to its neighbour in that direction. Stepping past
either end stays on the end.
*/
export function stepSnap (text, snaps, steps) {

	const value = toNumber(text);
	let index = snaps.indexOf(text);

	if (index < 0) {
		index = snaps.findIndex((snap) => Math.abs(toNumber(snap) - value) < 1e-9);
	}

	if (index < 0) {

		// Between two of them. Stand on the far neighbour, so one step lands on the near one.
		const above = snaps.findIndex((snap) => toNumber(snap) > value);
		const firstFaster = above < 0 ? snaps.length : above;

		index = steps > 0 ? firstFaster - 1 : firstFaster;
	}

	return snaps[Math.min(Math.max(index + steps, 0), snaps.length - 1)];
}


/*
Step freely: give the ratio `steps` on from this one, as a decimal with at most two places.

The knob moves along a fixed set of lines, 24 to each doubling, so it feels
the same at ×2 as at ×16, and ×2, ×4 and the rest can be landed on exactly. A
step always moves by at least a hundredth, which is all the steps there are
below about a third. The answer stays between a hundredth and the fastest
ratio there is.
*/
export function stepFree (text, steps, fastest) {

	const highest = Math.round(toNumber(fastest) * 100);
	const direction = steps > 0 ? 1 : -1;
	const end = direction > 0 ? highest : 1;
	let hundredths = Math.min(Math.max(Math.round(toNumber(text) * 100), 1), highest);

	for (let step = 0; step < Math.abs(steps) && hundredths !== end; step++) {

		// Begin from the nearest line, and go on until the ratio has really moved.
		let line = Math.round(Math.log2(hundredths / 100) * STEPS_PER_DOUBLING);
		let next = hundredths;

		while ((next - hundredths) * direction <= 0) {
			line += direction;
			next = Math.min(Math.max(Math.round(2 ** (line / STEPS_PER_DOUBLING) * 100), 1), highest);
		}

		hundredths = next;
	}

	return String(hundredths / 100);
}
