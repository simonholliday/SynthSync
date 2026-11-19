/*
Working out a tempo from taps.

Each tap is timed where it lands, so neither the WiFi nor how busy the Pico is
comes into it. What the tempo may be is the page's to keep within the clock's
range, and the Pico's to accept.
*/

// How many taps it takes before they say anything: fewer swing too far to send a running clock after.
const FIRST_TEMPO_AT = 4;

// How many of the latest taps are averaged, so that the tempo settles and can still be moved.
const MOST_COUNTED = 8;

// A longer gap than this between taps starts the count again, in milliseconds:
// a little over the slowest beat, which is 3 seconds in the usual tempo range.
const STARTS_AGAIN_AFTER = 3200;
const LONGEST_BEATS_MORE = 16 / 15;


export class TapTempo {

	/* Count taps from nothing. */
	constructor () {

		this.startsAgainAfter = STARTS_AGAIN_AFTER;
		this.reset();
	}

	/* Allow for beats as slow as this, in BPM, by waiting that much longer before starting again. */
	slowest (bpm) {

		this.startsAgainAfter = Math.max(STARTS_AGAIN_AFTER, 60000 / bpm * LONGEST_BEATS_MORE);
	}

	/* Forget every tap. */
	reset () {

		this.times = [];
		this.count = 0;
	}

	/* Say how many more taps it takes before there is a tempo. */
	left () {

		return Math.max(FIRST_TEMPO_AT - this.count, 0);
	}

	/* Take a tap at a moment in milliseconds, and give the tempo so far in whole BPM, or null while there are too few taps. */
	tap (moment) {

		if (this.times.length > 0 && moment - this.times[this.times.length - 1] > this.startsAgainAfter) {
			this.reset();
		}

		this.times.push(moment);
		this.count += 1;

		if (this.times.length > MOST_COUNTED) {
			this.times.shift();
		}

		if (this.left() > 0) {
			return null;
		}

		return Math.round(60000 * (this.times.length - 1) / (moment - this.times[0]));
	}
}
