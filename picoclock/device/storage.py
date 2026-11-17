"""Saving the settings to the Pico's flash, and reading them back at power-up.

A save waits until two seconds after the last change, so turning a knob is one
save rather than fifty. It goes through the player's `protect`, because writing
to flash stops both cores for about 40 ms and the clock must not notice.

The file is written under another name and then renamed, so a power cut in the
middle of a save leaves the old settings whole.
"""

import asyncio
import os
import time

import picoclock.core.settings

# Where the settings are kept, and the name a save is written under first.
PATH = "/settings.json"

# How long to wait after the last change before saving, in milliseconds.
QUIET_FOR = 2000


class Storage:

	"""Keeps one set of settings on flash."""

	def __init__ (self, settings: picoclock.core.settings.Settings, player=None, path: str = PATH) -> None:

		"""Look after these settings. With a player, saves are made without disturbing the clock."""

		self.settings = settings
		self.player = player
		self.path = path

		# What the file holds, as far as this knows, so an unchanged save is skipped.
		self._saved = ""

		# When the next save is due, or None if nothing has changed.
		self._due = None

		# How many saves have been made, for the page's About view and for tests.
		self.saves = 0

	def load (self) -> list:

		"""Read the settings from flash, and say what couldn't be used.

		A missing file is not a problem: it means a new or freshly reset Pico,
		and the defaults stand.
		"""

		try:
			with open(self.path) as handle:
				text = handle.read()

		except OSError:
			self.settings.reset()
			return []

		problems = self.settings.load_text(text)
		self._saved = text

		return problems

	def changed (self) -> None:

		"""Note that a setting has changed, so it is saved once things have been quiet for two seconds."""

		self._due = time.ticks_add(time.ticks_ms(), QUIET_FOR)

	def _write (self, text: str) -> None:

		"""Write the text under another name, then put it in place."""

		temporary = self.path + ".new"

		with open(temporary, "w") as handle:
			handle.write(text)

		os.rename(temporary, self.path)

	def save_now (self) -> bool:

		"""Save the settings at once if they differ from what's on flash, and say whether a save was made.

		A save that fails is due again once things have been quiet for as long
		again, and what went wrong is passed on, to be printed.
		"""

		self._due = None
		text = self.settings.to_text()

		if text == self._saved:
			return False

		try:

			if self.player is None:
				self._write(text)
			else:
				self.player.protect(lambda: self._write(text))

		except Exception:
			self.changed()
			raise

		self._saved = text
		self.saves += 1

		return True

	def forget (self) -> None:

		"""Remove the saved settings, as a factory reset does."""

		self._due = None
		self._saved = ""

		def remove () -> None:

			try:
				os.remove(self.path)
			except OSError:
				pass

		if self.player is None:
			remove()
		else:
			self.player.protect(remove)

	async def run (self) -> None:

		"""Save whenever a change has been left alone for two seconds. Runs for as long as the Pico does."""

		while True:

			await asyncio.sleep_ms(100)

			if self._due is not None and time.ticks_diff(time.ticks_ms(), self._due) >= 0:
				self.save_now()
