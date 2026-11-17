"""A stand-in for the Pico's player, so the clock can be used on a laptop.

It runs the real scheduler against the laptop's clock. There are no pins: it
takes the scheduler's words a little ahead of time, as the hardware player is
given them, and counts each pulse an output begins. Its timing is only good
enough to watch, which is all the page needs of it.

It takes the same calls as the player on the Pico, so nothing that uses one
can tell it from the other.
"""

import asyncio
import time
import typing

import picoclock.core.timeline
import picoclock.core.transport

# How far ahead of the laptop's clock words are taken, in seconds. The Pico's
# player is kept about this far ahead too, so a change takes as long to show.
AHEAD = 0.02

# How often more words are taken, in seconds.
STEP_EVERY = 0.005

# When the laptop's clock has got this far ahead of the words taken, in
# seconds, as it does after the laptop sleeps, the engine carries on from now
# and doesn't play back what was missed.
MOST_TO_CATCH_UP = 0.5


class Engine:

	"""Plays the clock with no pins, in step with the laptop's own clock.

	`pulses` counts the pulses each output has begun, and `pins` is how the
	pins would be set just now, as bits.
	"""

	def __init__ (self, outputs: int, now: typing.Callable[[], float] = time.monotonic) -> None:

		"""Make a stopped engine for this many outputs. `now` gives the time in seconds, for tests to stand in for."""

		self.timeline = picoclock.core.timeline.Timeline(outputs)
		self.pulses = [0] * outputs
		self.pins = 0

		self._now = now

		# The time on the laptop's clock that the words taken so far reach, or
		# None while there is nothing to play.
		self._taken_to: float | None = None

	def set_tempo (self, tempo: int) -> None:

		"""Change the tempo, in hundredths of a BPM."""

		self.timeline.set_tempo(tempo)

	def set_ratio (self, output: int, pulses: int, beats: int) -> None:

		"""Give an output a new ratio: this many pulses to this many beats."""

		self.timeline.set_ratio(output, pulses, beats)

	def set_mute (self, output: int, muted: bool) -> None:

		"""Silence an output, or let it sound again."""

		self.timeline.set_mute(output, muted)

	def set_width (self, output: int, ticks: int) -> None:

		"""Set how long an output's pulses last, in microseconds, or 0 for half the gap."""

		self.timeline.set_width(output, ticks)

	def set_role (self, output: int, role: int) -> None:

		"""Say what an output is for, as one of the scheduler's role numbers."""

		self.timeline.set_role(output, role)

	def press (self, command: str) -> None:

		"""Start, pause or stop, as the transport's table says for whatever the clock is doing."""

		self.timeline.press(picoclock.core.transport.NUMBERS[command])

	def transport (self) -> str:

		"""Say whether the clock is stopped, running or paused. A press here is carried out at once, so this can always say."""

		return picoclock.core.transport.state_of(self.timeline.running, self.timeline.paused)

	def start (self) -> None:

		"""Begin at zero."""

		self.timeline.start()

	def pause (self) -> None:

		"""Hold the clock where it is."""

		self.timeline.pause()

	def resume (self) -> None:

		"""Carry on from a pause."""

		self.timeline.resume()

	def stop (self) -> None:

		"""Set every output low and go back to zero."""

		self.timeline.stop()

	def status (self) -> dict[str, typing.Any]:

		"""Say how the clock is doing, in the same terms as the player on the Pico.

		There is no hardware to run short of words here, so the counts that
		tell of trouble on the Pico never move, and there are no buttons to
		press.
		"""

		timeline = self.timeline

		return {
			"running": timeline.running,
			"paused": timeline.paused,
			"beat": timeline.beat,
			"position": timeline.position(),
			"faults": 0,
			"refused": 0,
			"fewest_waiting": 8,
			"slowest_command": 0,
			"merged": timeline.merged,
			"presses": 0,
			"pressed_at": 0,
			"empty": 0,
			"one_left": 0,
			"empty_where": 0,
			"empty_after": 0,
			"longest_write": 0,
			"writes_past_backlog": 0,
		}

	def step (self) -> None:

		"""Take the scheduler's words up to a little ahead of now, counting the pulses that begin in them."""

		timeline = self.timeline
		now = self._now()

		if timeline.quiet():
			# Nothing is playing, so there is no time to keep.
			self._taken_to = None
			return

		if self._taken_to is None or now - self._taken_to > MOST_TO_CATCH_UP:
			self._taken_to = now

		horizon = now + AHEAD

		while self._taken_to < horizon and not timeline.quiet():

			pins, ticks = timeline.unpack(timeline.next_word())
			rose = pins & ~self.pins

			for output in range(timeline.outputs):
				if (rose >> output) & 1:
					self.pulses[output] += 1

			self.pins = pins
			self._taken_to += ticks / 1000000

	async def run (self) -> None:

		"""Keep the clock playing. Runs for as long as the stand-in does."""

		while True:

			self.step()
			await asyncio.sleep(STEP_EVERY)
