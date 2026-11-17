"""SynthSync's WiFi, apart from the radio itself.

A SynthSync is always doing one of four things with its radio. Out of the box
it runs a network of its own, which a phone joins to reach the page. Told of
a WiFi network, it switches its own off and tries to join that one. Once it
has, it is on that network, and its own stays off: the two are never up
together.

A join asked for from the page is a trial first. Once the network has given
the Pico an address, the Pico notes it, leaves, and puts its own network back
up, so the page can say where SynthSync will be. That is the fourth thing,
"found". It joins for good when the page says so, or two minutes later. A
phone can't always find the Pico by its name on a network, so without this
nobody would know its address there.

`Wifi` decides which of the four it should be doing, and when to change:

- **A join is tried for twenty seconds.** If the network isn't found it is
  looked for again, and a password refused twice ends the trying early.
- **A join that fails brings the Pico's own network back,** with the reason
  for the page to show. A network that has never worked is forgotten, so it
  isn't tried again at every power-up, and the network saved before it is
  saved again: a mistyped password never costs the network that worked. One
  that has worked before is kept.
- **At power-up the saved network is joined,** in the same way, but with no
  trial: it has worked before.
- **A network that is lost for a minute** counts as a join that failed.
- **Back on its own network with a network still saved,** it listens for
  that network once a minute while no phone has joined its own, and joins it
  again once it is heard. So a router that comes up later than the Pico, or
  comes back after a power cut, is gone back to without anyone asking.

Nothing tells it what to do. It watches the clock, as everything else does,
and acts half a second after a change: the change came over the very network
that is about to go, and the page that asked is owed its answer.

It never touches the radio itself. It asks whatever it is given as `radio` to
start and stop the networks and to say how a join is going, which on the Pico
is `synthsync.device.radio`, and on a laptop is a pretend one. Also here: what
a SynthSync calls its own network, and how the networks a scan hears are
tidied into the list the page shows.
"""

import synthsync.core.clock

# mypy treats this name as true, and nothing else does: it lets a type be named
# for checking without importing `typing` where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:

	import typing

	# What a scan heard of one transmitter: its network's name, its strength in
	# dBm, and whether a password is needed, as a yes or no or as the radio's own number.
	Heard = tuple[bytes | str, int, object]

	class Radio (typing.Protocol):

		"""What `Wifi` needs from whatever works the radio. Each call returns at once."""

		def own_up (self, name: str, password: str, host_name: str) -> None:

			"""Start the Pico's own network, in place of one already up."""

		def own_down (self) -> None:

			"""Stop the Pico's own network, if it is up."""

		def join (self, name: str, password: str, host_name: str) -> None:

			"""Begin joining a network, or begin again. An empty password is an open network."""

		def leave (self) -> None:

			"""Stop joining, or leave the network that was joined."""

		def link (self) -> str:

			"""Say how the join is going: IDLE, TRYING, NO_ADDRESS, LINKED, NOT_FOUND, WRONG_PASSWORD or FAILED."""

		def address (self) -> str:

			"""Give the address the page is at on whichever network is up, or nothing."""

		def heard (self) -> "typing.Iterable[Heard]":

			"""Listen for networks in range, and give what was heard of each transmitter."""

		def stations (self) -> int:

			"""Say how many phones, or anything else, have joined the Pico's own network."""

# How the name of a SynthSync's own network begins. Six characters of its
# hardware id follow, so two in one room can be told apart.
OWN_PREFIX = "SynthSync "

# Where the page is on a SynthSync's own network, which is MicroPython's choice.
OWN_ADDRESS = "192.168.4.1"

# What the radio is doing, as the page is told.
OWN = "own"
JOINING = "joining"
FOUND = "found"
JOINED = "joined"

# How a join is going, as whatever works the radio reports it.
IDLE = "idle"
TRYING = "trying"
NO_ADDRESS = "no_address"
LINKED = "linked"
NOT_FOUND = "not_found"
WRONG_PASSWORD = "wrong_password"
FAILED = "failed"

# Why a network that was joined was given up, which the radio never reports itself.
LOST = "lost"

# How long a change to the settings is left before it is acted on, in milliseconds.
SETTLE = 500

# How long a join is tried for, and how long a joined network may be lost for,
# before the Pico's own network comes back, in milliseconds.
JOIN_FOR = 20000
LOST_FOR = 60000

# How long to leave between one try that failed and the next, in milliseconds.
AGAIN_AFTER = 2000

# How long the Pico's own network stays up to say where a trial join found
# SynthSync will be, before it joins for good, in milliseconds. A phone that
# wandered off while the own network was down has time to come back, and the
# clock plays on meanwhile.
STAY_FOR = 120000

# How often to listen for the saved network while the Pico is back on its own,
# in milliseconds. Listening keeps the first core for two thirds of a second,
# so a page opened in that moment answers late. The clock doesn't notice.
LISTEN_EVERY = 60000

# How many times a password may be refused before the trying ends early. Once
# could be a bad moment on the air. Twice is the wrong password.
REFUSALS = 2

# Why the Pico is back on its own network, in words fit to show. Each takes the network's name.
_PROBLEMS = {
	NOT_FOUND: "There was no network called %s in range",
	WRONG_PASSWORD: "%s didn't accept that password",
	FAILED: "%s wouldn't let SynthSync join",
	NO_ADDRESS: "%s let SynthSync join but gave it no address",
	TRYING: "%s didn't answer in time",
	LOST: "SynthSync lost %s and couldn't get it back",
}

_NO_SCAN = "SynthSync couldn't look for networks just then. Try again in a moment."
_BUSY_JOINING = "SynthSync is busy joining a network. Try again in a moment."


def own_name (identity: bytes) -> str:

	"""Name a SynthSync's own network after its hardware id: "SynthSync" and the id's last six characters.

	The id never changes, so neither does the name, and a phone that has
	joined once finds the same network next time.
	"""

	return OWN_PREFIX + "".join("%02X" % byte for byte in identity[-3:])


def in_range (heard: "typing.Iterable[Heard]") -> "list[dict[str, object]]":

	"""Tidy what a scan heard into what the page lists: each network once, strongest first.

	`heard` holds a name, a strength in dBm and whether a password is needed
	for every transmitter the radio heard. A network with several of them is
	heard several times, and only its strongest is kept. A network that hides
	its name, or whose name isn't text, can't be picked from a list, so it is
	left out.
	"""

	best: "dict[str, tuple[int, bool]]" = {}

	for name, strength, secure in heard:

		if not isinstance(name, str):

			try:
				name = name.decode()
			except (UnicodeError, ValueError):
				continue

		# A hidden network sends no name, or one made of nothing.
		if not name.strip("\x00"):
			continue

		if name not in best or strength > best[name][0]:
			best[name] = (strength, bool(secure))

	# Strongest first, and by name where two are as strong, so the list doesn't shuffle between scans.
	names = sorted(best, key=lambda each: (-best[each][0], each))

	return [{"name": name, "strength": best[name][0], "secure": best[name][1]} for name in names]


class Wifi:

	"""Decides what the radio should be doing, and says how the network stands.

	`look` wants calling every tenth of a second or so, with the time since
	it was last called. `status` and `scan` are what the page's API asks of a
	network.
	"""

	def __init__ (self, clock: synthsync.core.clock.Clock, radio: "Radio", own: str, join_for: int = JOIN_FOR, lost_for: int = LOST_FOR, again_after: int = AGAIN_AFTER, stay_for: int = STAY_FOR, listen_every: int = LISTEN_EVERY) -> None:

		"""Follow this clock's settings, through this radio. Nothing is switched on until the first `look`.

		`own` is what the Pico's own network is called. The times are how long
		a join is tried for, how long a joined network may be lost for, how
		long to leave between tries, how long to stay on the own network
		after a trial join before joining for good, and how often to listen
		for a saved network from the own one, in milliseconds.
		"""

		self.clock = clock
		self.settings = clock.settings
		self.radio = radio
		self.own = own
		self.join_for = join_for
		self.lost_for = lost_for
		self.again_after = again_after
		self.stay_for = stay_for
		self.listen_every = listen_every

		# How long until the saved network is next listened for, while the Pico is back on its own.
		self._listen = listen_every

		# What the radio is doing: OWN, JOINING, FOUND or JOINED, or nothing before the first look.
		self.mode = ""

		# Whether the join under way is a trial, and once one has worked, the
		# address the network gave the Pico and how long until it joins for good.
		self._trial = False
		self._found = ""
		self._stay = 0

		# Why the Pico is on its own network when it was asked to be on another: in
		# words fit to show, and as one of the names above. Both are empty when it wasn't.
		self.problem = ""
		self.reason = ""

		# The network being joined or joined, and the one that is known to have worked, each as a (name, password).
		self._target: "tuple[str, str] | None" = None
		self._proven: "tuple[str, str] | None" = None

		# Whether the radio has been started since power-up. Starting afresh
		# after the radio has failed isn't a power-up, and proves nothing.
		self._begun = False

		# Whether the radio failed part way through something, so that either
		# network may still be up, and both are to be switched off before starting afresh.
		self._unsure = False

		# The password the Pico's own network is running with, or None while it is down.
		self._own_password: "str | None" = None

		# How long until a change to the settings is acted on, or None when there is none to act on,
		# and whether the change came since the last look, whose time must not count towards it.
		self._due: "int | None" = None
		self._fresh = False

		# How many requests to join, and to switch to a network a trial found, the clock had counted when this last looked.
		self._joins = clock.joins
		self._switches = clock.switches

		# While joining: how long it has been tried for, how many times the password
		# has been refused, and the last thing that went wrong. While joined: how
		# long the network has been lost for.
		self._waited = 0
		self._refused = 0
		self._went_wrong = TRYING

		# How long until the next try, or None when one is under way or none is wanted.
		self._again: "int | None" = None

		# True while this is itself changing the settings, which it mustn't then act on.
		self._quiet = False

		clock.watch(self._changed)

	def _changed (self, what: "str | int", by: object) -> None:

		"""Hear of a change to the clock, and note the ones that are about the network."""

		if self._quiet:
			return

		if what == synthsync.core.clock.NETWORK or what == synthsync.core.clock.EVERYTHING:
			self._due = SETTLE
			self._fresh = True

	# -----------------------------------------------------------------------
	# Changing what the radio is doing.

	def _own_is_up (self) -> bool:

		"""Say whether the Pico's own network is up: on its own, or to say where a trial join found SynthSync will be."""

		return self.mode == OWN or self.mode == FOUND

	def _start_own (self) -> None:

		"""Start the Pico's own network with the password the settings hold, in place of one already up."""

		self.radio.own_up(self.own, self.settings.own_password, self.settings.host_name)

		self._own_password = self.settings.own_password

	def _own (self, problem: str, reason: str) -> None:

		"""Start the Pico's own network, in place of whatever was up, and go back to having no network to join."""

		if self.mode == JOINING or self.mode == JOINED:
			self.radio.leave()

		self._start_own()
		self._settle_own(problem, reason)

	def _settle_own (self, problem: str, reason: str) -> None:

		"""Note that the Pico is on its own network with no network to join, saying why, without touching the radio."""

		self._target = None
		self._again = None
		self._found = ""
		self._listen = self.listen_every
		self.mode = OWN
		self.problem = problem
		self.reason = reason

	def _join (self, name: str, password: str, trial: bool = False) -> None:

		"""Switch off whatever is up and begin joining a network, as a trial if asked."""

		# Noted before the radio is touched, so that a radio that fails part way
		# is started afresh with the same join, still a trial if this was one.
		self._target = (name, password)
		self._trial = trial

		if self._own_is_up():
			self.radio.own_down()
			self._own_password = None

		elif self.mode:
			self.radio.leave()

		self._found = ""
		self._waited = 0
		self._refused = 0
		self._went_wrong = TRYING
		self._again = None
		self.mode = JOINING
		self.problem = ""
		self.reason = ""

		self.radio.join(name, password, self.settings.host_name)

	def _come_back (self) -> None:

		"""A trial join has worked: note the address the network gave, leave it, and put the own network back up to say it."""

		target = self._target
		found = self.radio.address()

		self._own("", "")

		self._target = target
		self._found = found
		self._stay = self.stay_for
		self.mode = FOUND

	def _give_up (self, reason: str) -> None:

		"""Go back to the Pico's own network, saying why, and forget the network unless it has worked before.

		The network that worked before is saved again in its place, if there
		was one, and is listened for once a minute, as after losing it.
		"""

		assert self._target is not None

		name = self._target[0]
		proven = self._target == self._proven

		self._own(_PROBLEMS[reason] % name, reason)

		if proven:
			return

		# Left in the settings, it would be tried for twenty seconds at every power-up.
		self._quiet = True

		try:

			if self._proven is None:
				self.clock.forget_network()
			else:
				self.clock.put_back_network(self._proven[0], self._proven[1])

		finally:
			self._quiet = False

	def _begin (self) -> None:

		"""Start as the settings say, as at power-up: join the saved network, or run the Pico's own.

		After the radio has failed, a trial join it was in the middle of is
		begun again as a trial, so the page still learns where SynthSync will be.
		"""

		name = self.settings.network_name
		password = self.settings.network_password
		trial = self._begun and self._trial and self._target == (name, password)

		self._joins = self.clock.joins
		self._switches = self.clock.switches
		self._due = None

		# At power-up, only a network that has been joined is in the settings, so that one has worked before.
		if not self._begun:
			self._begun = True
			self._proven = (name, password) if name else None

		# Whatever the radio failed at may have left a network up, and the two are never up together.
		if self._unsure:
			self.radio.own_down()
			self.radio.leave()
			self._unsure = False

		if name:
			self._join(name, password, trial)
		else:
			self._own("", "")

	def _apply (self) -> None:

		"""Act on a change to the settings that has had its half second."""

		asked = self.clock.joins != self._joins
		switch = self.clock.switches != self._switches
		name = self.settings.network_name
		password = self.settings.network_password

		self._joins = self.clock.joins
		self._switches = self.clock.switches

		if not name:

			# The network was forgotten, or every setting was put back as new.
			self._proven = None

			if not self._own_is_up() or self.settings.own_password != self._own_password:
				self._own("", "")

			else:
				# Already on its own network, with nothing new to start it for: only an old reason for being there goes, and any network a trial found.
				self._settle_own("", "")

			return

		if asked:

			# Asked to join the network it is already on, there is nothing to do.
			if not (self.mode == JOINED and self._target == (name, password)):
				self._join(name, password, True)

			return

		if switch and self.mode == FOUND:
			self._switch()
			return

		# Any other change, such as a new password for the Pico's own network, only matters while that network is up.
		if self._own_is_up() and self.settings.own_password != self._own_password:
			self._start_own()

	def _switch (self) -> None:

		"""Join for good the network a trial found. It has worked, so a failure keeps it saved."""

		assert self._target is not None

		self._join(self._target[0], self._target[1])

	# -----------------------------------------------------------------------
	# Watching a join.

	def _try_again (self, elapsed: int) -> None:

		"""Count down to the next try, and begin it when the time comes."""

		assert self._target is not None

		if self._again is None:
			self._again = self.again_after
			return

		self._again -= elapsed

		if self._again <= 0:
			self._again = None
			self.radio.join(self._target[0], self._target[1], self.settings.host_name)

	def _see_join (self, elapsed: int) -> None:

		"""See how a join is going: joined, still trying, to be tried again, or given up."""

		self._waited += elapsed
		link = self.radio.link()

		if link == LINKED:

			self._proven = self._target
			self._waited = 0
			self._again = None

			if self._trial:
				self._come_back()
			else:
				self.mode = JOINED

			return

		if link == TRYING or link == NO_ADDRESS:

			# Being let in and then given no address is worth saying, should the time run out.
			if link == NO_ADDRESS:
				self._went_wrong = NO_ADDRESS

		else:

			# This try has failed. It is counted once, when it is first seen to have.
			if self._again is None:

				self._went_wrong = FAILED if link == IDLE else link

				if link == WRONG_PASSWORD:

					self._refused += 1

					if self._refused >= REFUSALS:
						self._give_up(WRONG_PASSWORD)
						return

			self._try_again(elapsed)

		if self._waited >= self.join_for:
			self._give_up(self._went_wrong)

	def _see_link (self, elapsed: int) -> None:

		"""See that the joined network is still there, and try to get it back if it isn't."""

		link = self.radio.link()

		if link == LINKED:
			self._waited = 0
			self._again = None
			return

		self._waited += elapsed

		if self._waited >= self.lost_for:
			self._give_up(LOST)
			return

		# The radio may be getting it back by itself. If it has stopped trying, it is asked again.
		if link != TRYING and link != NO_ADDRESS:
			self._try_again(elapsed)

	def _see_found (self, elapsed: int) -> None:

		"""Count down the time the own network stays up to say where SynthSync will be, and join for good when it is up."""

		self._stay -= elapsed

		if self._stay <= 0:
			self._switch()

	def _see_own (self, elapsed: int) -> None:

		"""With a network still saved, listen for it once a minute, and join it again once it is heard.

		Not while a phone has joined the Pico's own network: the join would
		take the page from under it. The network has worked before, so the
		join is no trial, and one that fails keeps it saved and brings the
		own network back to listen again.
		"""

		name = self.settings.network_name

		# A change waiting its half second may be a join asked for from the page, which goes its own way.
		if not name or self._due is not None:
			return

		self._listen -= elapsed

		if self._listen > 0:
			return

		self._listen = self.listen_every

		if self.radio.stations():
			return

		try:
			heard = in_range(self.radio.heard())

		except OSError:
			# The radio couldn't listen just then. A minute on, it is asked again.
			return

		for network in heard:

			if network["name"] == name:
				self._join(name, self.settings.network_password)
				return

	def look (self, elapsed: int) -> None:

		"""Do whatever is due, `elapsed` milliseconds after the last look.

		The first look starts the radio as the settings say. If the radio
		can't do as it is asked, the error comes out of here, and the next
		look starts afresh from the settings.
		"""

		try:

			if not self.mode:
				self._begin()
				return

			if self._due is not None:

				# A change waits its half second from the first look after it, however long that look was in coming.
				if self._fresh:
					self._fresh = False
				else:
					self._due -= elapsed

				if self._due <= 0:

					self._due = None
					self._apply()

					# Whatever that began is first looked at on the next look, a full step after it began.
					return

			if self.mode == JOINING:
				self._see_join(elapsed)

			elif self.mode == JOINED:
				self._see_link(elapsed)

			elif self.mode == FOUND:
				self._see_found(elapsed)

			elif self.mode == OWN:
				self._see_own(elapsed)

		except OSError:

			# Nothing is known to be up any more.
			self.mode = ""
			self._own_password = None
			self._unsure = True

			raise

	# -----------------------------------------------------------------------
	# What the page asks.

	def status (self) -> "dict[str, object]":

		"""Say how the network stands, as the page shows it.

		"name" is the network being joined, joined or found, or else the
		Pico's own, and "address" is where the page is on whichever network
		is up. "found_at" is where SynthSync will be once it has switched to
		the network a trial found, and is empty at any other time.
		"""

		away = self.mode == JOINING or self.mode == FOUND or self.mode == JOINED

		return {
			"mode": self.mode or OWN,
			"name": self._target[0] if away and self._target is not None else self.own,
			"address": self.radio.address() if self._own_is_up() or self.mode == JOINED else "",
			"problem": self.problem,
			"own": self.own,
			"found_at": self._found if self.mode == FOUND else "",
		}

	def scan (self) -> "list[dict[str, object]]":

		"""List the networks in range, strongest first, each with its name, its strength and whether it needs a password.

		Raises ValueError, in words fit to show, when the radio can't listen just now.
		"""

		# Listening in the middle of a join holds the first core for ten seconds, and can spoil the join.
		if self.mode == JOINING:
			raise ValueError(_BUSY_JOINING)

		try:
			heard = self.radio.heard()

		except OSError:
			raise ValueError(_NO_SCAN)

		return in_range(heard)

	def close (self) -> None:

		"""Switch everything off. A look after this starts afresh from the settings."""

		if self.mode == JOINING or self.mode == JOINED or self._unsure:
			self.radio.leave()

		self.radio.own_down()

		self.mode = ""
		self._unsure = False
		self._own_password = None
		self._target = None
		self._found = ""
		self._trial = False
		self._due = None
		self._begun = False
