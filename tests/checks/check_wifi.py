"""A SynthSync's own network is named, the networks in range are listed, and a join is tried and given up, the same way on the Pico's Python as on a laptop's."""

import synthsync.core.clock
import synthsync.core.wifi
import tests.checks.check_clock

# mypy treats this name as true, and nothing else does, so `typing` is never
# imported where MicroPython would have to find it.
TYPE_CHECKING = False

if TYPE_CHECKING:
	import typing

OWN_NAME = "SynthSync AB78D9"
OWN_ADDRESS = "192.168.4.1"
JOINED_ADDRESS = "192.168.1.23"


class Radio:

	"""Stands in for the radio: it notes what it is asked, and joins the networks it holds as in range."""

	def __init__ (self) -> None:

		"""Begin with everything off and two networks in range, one of them open."""

		self.calls: "list[tuple[typing.Any, ...]]" = []
		self.networks = {"Studio": "studio wifi", "Cafe": ""}

		# What is up: the Pico's own network, and how a join stands.
		self.own_is_up = False
		self.away = False
		self.result = synthsync.core.wifi.IDLE

		# What every try comes to, in place of what the networks in range would say.
		self.stuck: "str | None" = None

		# Set once if the two networks were ever up together, which must never happen.
		self.together = False

		# Whether the radio fails at whatever it is asked, and whether it can listen for networks.
		self.broken = False
		self.deaf = False

		# How many phones have joined the own network, and how many times the radio has listened.
		self.phones = 0
		self.listened = 0

	def _do (self, call: "tuple[typing.Any, ...]") -> None:

		"""Note one thing that was asked, or fail at it."""

		if self.broken:
			raise OSError("The radio isn't answering")

		self.calls.append(call)

	def own_up (self, name: str, password: str, host_name: str) -> None:

		"""Start the pretend own network."""

		self._do(("own_up", name, password, host_name))

		self.own_is_up = True
		self.together = self.together or self.away

	def own_down (self) -> None:

		"""Stop the pretend own network."""

		self._do(("own_down",))

		self.own_is_up = False

	def join (self, name: str, password: str, host_name: str) -> None:

		"""Try a network: it joins if it is in range and the password is right."""

		self._do(("join", name, password, host_name))

		self.away = True
		self.together = self.together or self.own_is_up

		if self.stuck is not None:
			self.result = self.stuck

		elif name not in self.networks:
			self.result = synthsync.core.wifi.NOT_FOUND

		elif self.networks[name] != password:
			self.result = synthsync.core.wifi.WRONG_PASSWORD

		else:
			self.result = synthsync.core.wifi.LINKED

	def leave (self) -> None:

		"""Stop trying, or leave."""

		self._do(("leave",))

		self.away = False
		self.result = synthsync.core.wifi.IDLE

	def lose (self) -> None:

		"""Have the joined network go away, as when a router is switched off."""

		self.result = synthsync.core.wifi.FAILED

	def link (self) -> str:

		"""Say how the join is going."""

		return self.result

	def address (self) -> str:

		"""Give the address of whichever network is up."""

		if self.own_is_up:
			return OWN_ADDRESS

		return JOINED_ADDRESS if self.result == synthsync.core.wifi.LINKED else ""

	def heard (self) -> "list[tuple[bytes, int, int]]":

		"""Give what a scan would hear of the networks in range: one network from two transmitters, one open, one hidden."""

		if self.deaf:
			raise OSError("The radio is busy")

		self.listened += 1

		every = [(b"Studio", -70, 5), (b"Cafe", -83, 0), (b"Studio", -48, 5), (b"", -30, 5)]

		return [each for each in every if not each[0] or each[0].decode() in self.networks]

	def stations (self) -> int:

		"""Say how many phones have joined the own network."""

		return self.phones if self.own_is_up else 0

	def named (self, kind: str) -> "list[tuple[typing.Any, ...]]":

		"""Give the calls of one kind made so far, in order."""

		return [call for call in self.calls if call[0] == kind]


def make (saved: "tuple[str, str] | None" = None) -> "tuple[synthsync.core.wifi.Wifi, Radio, synthsync.core.clock.Clock]":

	"""Make a clock, a pretend radio and the WiFi between them, with a network already saved if one is given."""

	clock, engine, storage = tests.checks.check_clock.make()

	if saved is not None:
		clock.settings.set_network(saved[0], saved[1])

	radio = Radio()

	return synthsync.core.wifi.Wifi(clock, radio, OWN_NAME), radio, clock


def spend (wifi: synthsync.core.wifi.Wifi, milliseconds: int, step: int = 100) -> None:

	"""Let time pass, with a look every tenth of a second."""

	for _ in range(milliseconds // step):
		wifi.look(step)


def saves (clock: synthsync.core.clock.Clock) -> int:

	"""Say how many times the clock has asked for its settings to be saved."""

	storage = clock.storage

	assert isinstance(storage, tests.checks.check_clock.Storage)

	return storage.changes


def joined (network: str = "Studio", password: str = "studio wifi") -> "tuple[synthsync.core.wifi.Wifi, Radio, synthsync.core.clock.Clock]":

	"""Make a SynthSync that has joined a network from the page, switching to it once the trial worked, and forget what the radio was asked on the way."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network(network, password)
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.FOUND

	clock.switch_network()
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.JOINED

	radio.calls = []

	return wifi, radio, clock


def found () -> "tuple[synthsync.core.wifi.Wifi, Radio, synthsync.core.clock.Clock]":

	"""Make a SynthSync whose trial join of Studio has worked, so it is back on its own network to say where it will be, and forget what the radio was asked on the way."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Studio", "studio wifi")
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.FOUND

	radio.calls = []

	return wifi, radio, clock


def check_the_own_network_is_named_after_the_last_six_characters_of_the_hardware_id () -> None:

	"""The network a SynthSync puts up is called SynthSync and six characters that are its own."""

	identity = bytes([0xE6, 0x61, 0xA4, 0xD1, 0x23, 0xAB, 0x78, 0xD9])

	assert synthsync.core.wifi.own_name(identity) == "SynthSync AB78D9"


def check_the_own_networks_name_is_in_capitals_with_every_nought_kept () -> None:

	"""The six characters are capitals, and a byte below 16 keeps the nought in front of it."""

	assert synthsync.core.wifi.own_name(bytes([0x11, 0x00, 0x0A, 0xB0])) == "SynthSync 000AB0"
	assert synthsync.core.wifi.own_name(bytes([0xFF, 0xFE, 0xFD])) == "SynthSync FFFEFD"


def check_two_synthsyncs_have_different_names_and_one_keeps_its_own () -> None:

	"""Two SynthSyncs in one room can be told apart, and each has the same name every time it starts."""

	one = bytes([1, 2, 3, 4, 5, 6, 7, 8])
	other = bytes([1, 2, 3, 4, 5, 6, 7, 9])

	assert synthsync.core.wifi.own_name(one) != synthsync.core.wifi.own_name(other)
	assert synthsync.core.wifi.own_name(one) == synthsync.core.wifi.own_name(bytes(one))

	# Only the end of the id is used, so the rest makes no difference.
	assert synthsync.core.wifi.own_name(bytes([9, 9, 9, 9, 9, 6, 7, 8])) == synthsync.core.wifi.own_name(one)


def check_the_own_networks_name_fits_what_wifi_allows () -> None:

	"""The name is sixteen characters, well inside the 32 bytes WiFi allows a network's name."""

	name = synthsync.core.wifi.own_name(bytes([0xAB] * 8))

	assert len(name) == 16
	assert len(name.encode()) <= 32
	assert name.startswith("SynthSync ")


def check_a_network_heard_several_times_is_listed_once_at_its_strongest () -> None:

	"""A network with several transmitters, as a mesh at home has, is listed once, as strong as the nearest."""

	heard = [
		(b"Studio", -70, True),
		(b"Studio", -48, True),
		(b"Studio", -81, True),
	]

	assert synthsync.core.wifi.in_range(heard) == [{"name": "Studio", "strength": -48, "secure": True}]


def check_networks_are_listed_strongest_first () -> None:

	"""The page lists the networks in range with the strongest at the top, where the one to pick usually is."""

	heard = [
		(b"Cafe", -83, False),
		(b"Studio", -48, True),
		(b"Upstairs", -71, True),
	]

	listed = synthsync.core.wifi.in_range(heard)

	assert [each["name"] for each in listed] == ["Studio", "Upstairs", "Cafe"]
	assert [each["strength"] for each in listed] == [-48, -71, -83]
	assert [each["secure"] for each in listed] == [True, True, False]


def check_networks_as_strong_as_each_other_are_listed_by_name () -> None:

	"""Networks heard as strongly as each other are put in order by name, so the list doesn't shuffle between looks."""

	one_way = synthsync.core.wifi.in_range([(b"Barn", -60, True), (b"Attic", -60, True), (b"Cellar", -60, False)])
	other_way = synthsync.core.wifi.in_range([(b"Cellar", -60, False), (b"Attic", -60, True), (b"Barn", -60, True)])

	assert [each["name"] for each in one_way] == ["Attic", "Barn", "Cellar"]
	assert one_way == other_way


def check_a_network_that_hides_its_name_is_left_out () -> None:

	"""A network that hides its name can't be picked from a list, so it isn't in it."""

	heard = [
		(b"", -40, True),
		(b"\x00\x00\x00\x00", -41, True),
		(b"Studio", -48, True),
	]

	assert [each["name"] for each in synthsync.core.wifi.in_range(heard)] == ["Studio"]


def check_a_name_that_isnt_text_is_left_out_and_the_rest_still_listed () -> None:

	"""A name the radio gives as bytes that aren't text is passed over, and the networks after it are still listed."""

	heard = [
		(b"\xff\xfe\xfd", -40, True),
		(b"Studio", -48, True),
	]

	assert [each["name"] for each in synthsync.core.wifi.in_range(heard)] == ["Studio"]


def check_a_name_with_accents_or_spaces_is_kept_whole () -> None:

	"""A network whose name has accents or spaces in it is listed exactly as it is called."""

	heard = [
		("Café Müller".encode(), -55, False),
		(b"  Two Spaces ", -60, True),
	]

	listed = synthsync.core.wifi.in_range(heard)

	assert [each["name"] for each in listed] == ["Café Müller", "  Two Spaces "]


def check_whether_a_password_is_needed_is_as_the_strongest_says () -> None:

	"""Whether a network needs a password is as its strongest transmitter says, and the radio's 0 means none."""

	heard = [
		(b"Studio", -80, False),
		(b"Studio", -50, True),
	]

	assert synthsync.core.wifi.in_range(heard)[0]["secure"] is True

	# The radio says how a network is secured as a number, and none at all is 0.
	assert synthsync.core.wifi.in_range([(b"Open", -50, 0)])[0]["secure"] is False


def check_names_already_text_are_taken_as_they_are () -> None:

	"""A radio that gives names as text rather than bytes, as the laptop's pretend one does, is listened to as it is."""

	assert synthsync.core.wifi.in_range([("Studio", -48, True)]) == [{"name": "Studio", "strength": -48, "secure": True}]


def check_hearing_nothing_lists_nothing () -> None:

	"""With no network in range the list is empty, and nothing goes wrong."""

	assert synthsync.core.wifi.in_range([]) == []


# ---------------------------------------------------------------------------
# What the radio is doing, and when it changes.


def check_nothing_is_switched_on_until_the_first_look () -> None:

	"""Making the WiFi asks nothing of the radio: it starts at the first look."""

	wifi, radio, clock = make()

	assert radio.calls == []
	assert wifi.status()["mode"] == "own"
	assert wifi.status()["address"] == ""


def check_a_new_synthsync_runs_its_own_network () -> None:

	"""Out of the box, with nothing saved, SynthSync puts up its own network for a phone to join."""

	wifi, radio, clock = make()

	wifi.look(0)

	assert radio.calls == [("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}

	spend(wifi, 5000)

	assert len(radio.calls) == 1


def check_a_new_password_starts_the_own_network_again_after_half_a_second_and_not_before () -> None:

	"""A new password for the own network takes effect half a second after it is set, so the page that set it hears back first."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_own_password("a new password")

	# The first look after the change only notes it. The half second runs from there.
	spend(wifi, 500)

	assert len(radio.calls) == 1

	spend(wifi, 100)

	assert radio.calls[1:] == [("own_up", OWN_NAME, "a new password", "synthsync")]

	spend(wifi, 5000)

	assert len(radio.calls) == 2


def check_the_half_second_is_counted_from_the_first_look_after_the_change_however_late_that_look_is () -> None:

	"""The half second starts at the first look after the change, so a look that came late can't cut it short."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_own_password("a new password")

	# The Pico was busy for five seconds, and the change came at the very end of them.
	wifi.look(5000)

	assert len(radio.calls) == 1

	wifi.look(400)

	assert len(radio.calls) == 1

	wifi.look(100)

	assert len(radio.calls) == 2


def check_a_new_name_for_the_pico_starts_nothing_again () -> None:

	"""Renaming the Pico leaves its own network as it is, and phones on it stay on it."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_host_name("studio-clock")
	spend(wifi, 3000)

	assert len(radio.calls) == 1

	# The name is taken up when a network next starts.
	clock.set_own_password("a new password")
	spend(wifi, 1000)

	assert radio.calls[1:] == [("own_up", OWN_NAME, "a new password", "studio-clock")]


def check_a_join_is_tried_first_and_the_own_network_comes_back_to_say_where_synthsync_will_be () -> None:

	"""A network picked on the page is tried first: once it gives the Pico an address, the own network comes back to tell the page where SynthSync will be."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Studio", "studio wifi")
	spend(wifi, 500)

	assert len(radio.calls) == 1

	wifi.look(100)

	assert radio.calls[1:] == [("own_down",), ("join", "Studio", "studio wifi", "synthsync")]
	assert wifi.status() == {"mode": "joining", "name": "Studio", "address": "", "problem": "", "own": OWN_NAME, "found_at": ""}

	# Joined and given an address, it notes the address, leaves, and is straight back on its own network to say it.
	wifi.look(100)

	assert radio.calls[3:] == [("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status() == {"mode": "found", "name": "Studio", "address": OWN_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": JOINED_ADDRESS}
	assert clock.settings.network_name == "Studio"

	# It waits two minutes for the page to say so, and then joins for good by itself.
	spend(wifi, 119900)

	assert wifi.mode == synthsync.core.wifi.FOUND
	assert len(radio.calls) == 5

	spend(wifi, 100)

	assert radio.calls[5:] == [("own_down",), ("join", "Studio", "studio wifi", "synthsync")]

	wifi.look(100)

	assert wifi.status() == {"mode": "joined", "name": "Studio", "address": JOINED_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}

	spend(wifi, 30000)

	assert len(radio.calls) == 7
	assert not radio.together


def check_the_page_can_have_it_switch_at_once_to_the_network_found () -> None:

	"""After a trial that worked, the page can have SynthSync join for good at once rather than two minutes later."""

	wifi, radio, clock = found()

	clock.switch_network()
	spend(wifi, 500)

	# Half a second first, as for any change, so the page that asked hears its answer.
	assert radio.calls == []

	spend(wifi, 200)

	assert radio.calls == [("own_down",), ("join", "Studio", "studio wifi", "synthsync")]
	assert wifi.status() == {"mode": "joined", "name": "Studio", "address": JOINED_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}
	assert not radio.together


def check_asking_to_switch_when_nothing_was_found_does_nothing () -> None:

	"""A switch asked for when no trial has found anything changes nothing."""

	wifi, radio, clock = make()

	wifi.look(0)
	radio.calls = []
	clock.switch_network()
	spend(wifi, 1000)

	assert radio.calls == []
	assert wifi.mode == synthsync.core.wifi.OWN

	wifi, radio, clock = joined()

	clock.switch_network()
	spend(wifi, 1000)

	assert radio.calls == []
	assert wifi.mode == synthsync.core.wifi.JOINED


def check_a_switch_that_fails_brings_the_own_network_back_and_keeps_the_network () -> None:

	"""A network that worked in the trial but has gone by the switch brings the own network back, and stays saved, since it has worked."""

	wifi, radio, clock = found()

	# The router goes off between the trial and the switch.
	del radio.networks["Studio"]
	clock.switch_network()
	spend(wifi, 21000)

	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "There was no network called Studio in range", "own": OWN_NAME, "found_at": ""}

	# It has worked, so it is tried again at the next power-up.
	assert clock.settings.network_name == "Studio"
	assert not radio.together


def check_forgetting_the_network_found_leaves_the_own_network_up_as_it_is () -> None:

	"""Forgetting the network a trial found, before switching to it, leaves the own network up without starting it again."""

	wifi, radio, clock = found()

	clock.forget_network()
	spend(wifi, 1000)

	assert radio.calls == []
	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}

	# Nor does anything join by itself when the two minutes would have been up.
	spend(wifi, 130000)

	assert radio.calls == []


def check_a_new_password_for_the_own_network_while_found_starts_it_again_and_keeps_what_was_found () -> None:

	"""A new password for the own network while it says where SynthSync will be starts it again, still saying where."""

	wifi, radio, clock = found()

	clock.set_own_password("a new password")
	spend(wifi, 1000)

	assert radio.calls == [("own_up", OWN_NAME, "a new password", "synthsync")]
	assert wifi.status()["mode"] == "found"
	assert wifi.status()["found_at"] == JOINED_ADDRESS


def check_another_join_while_found_is_tried_afresh () -> None:

	"""Picking another network while a trial's result is shown begins a new trial, of that one."""

	wifi, radio, clock = found()

	clock.set_network("Cafe", "")
	spend(wifi, 1000)

	assert radio.calls == [("own_down",), ("join", "Cafe", "", "synthsync"), ("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status()["name"] == "Cafe"
	assert wifi.status()["mode"] == "found"
	assert not radio.together


def check_an_open_network_is_joined_with_no_password () -> None:

	"""A network with no password is joined with none."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Cafe", "")
	spend(wifi, 1000)

	assert radio.named("join") == [("join", "Cafe", "", "synthsync")]
	assert wifi.mode == synthsync.core.wifi.FOUND


def check_a_password_refused_twice_ends_the_trying_early_and_the_network_is_forgotten () -> None:

	"""A password refused twice is wrong, so the trying ends there rather than after twenty seconds, and a network that never worked isn't kept."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Studio", "not the password")
	spend(wifi, 700)

	assert len(radio.named("join")) == 1
	assert wifi.mode == synthsync.core.wifi.JOINING

	# Once could be a bad moment on the air, so it is tried once more, two seconds on.
	spend(wifi, 1900)

	assert len(radio.named("join")) == 1

	spend(wifi, 300)

	assert radio.calls[1:] == [
		("own_down",),
		("join", "Studio", "not the password", "synthsync"),
		("join", "Studio", "not the password", "synthsync"),
		("leave",),
		("own_up", OWN_NAME, "synthsync", "synthsync"),
	]
	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "Studio didn't accept that password", "own": OWN_NAME, "found_at": ""}
	assert wifi.reason == synthsync.core.wifi.WRONG_PASSWORD

	# It never worked, so it isn't left to be tried at every power-up.
	assert clock.settings.network_name == ""
	assert clock.settings.network_password == ""
	assert not radio.together


def check_forgetting_a_network_itself_isnt_taken_as_a_change_to_act_on () -> None:

	"""When the WiFi itself forgets a network that failed, it doesn't act on that as if the page had asked, and the reason stays to be shown."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Studio", "not the password")
	spend(wifi, 4000)

	asked = len(radio.calls)

	assert wifi.problem == "Studio didn't accept that password"

	# The reason stays for the page to show, and the network isn't started again for nothing.
	spend(wifi, 5000)

	assert wifi.problem == "Studio didn't accept that password"
	assert len(radio.calls) == asked


def check_a_network_that_isnt_there_is_looked_for_until_twenty_seconds_are_up () -> None:

	"""A network not in range is looked for again and again for twenty seconds, in case it is slow to answer."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 600)

	assert len(radio.named("join")) == 1

	# The join began 600 ms in, so its twenty seconds are up at 20,600.
	spend(wifi, 19900)

	assert wifi.mode == synthsync.core.wifi.JOINING
	assert len(radio.named("join")) >= 8
	assert len(radio.named("own_up")) == 1

	spend(wifi, 100)

	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "There was no network called Garden in range", "own": OWN_NAME, "found_at": ""}
	assert wifi.reason == synthsync.core.wifi.NOT_FOUND
	assert radio.calls[-2:] == [("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert clock.settings.network_name == ""
	assert not radio.together


def check_a_network_that_turns_up_late_is_joined () -> None:

	"""A network that comes into range part way through the twenty seconds is joined."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 7000)

	assert wifi.mode == synthsync.core.wifi.JOINING

	radio.networks["Garden"] = "garden wifi"
	spend(wifi, 2500)

	assert wifi.status()["mode"] == "found"
	assert wifi.status()["name"] == "Garden"
	assert clock.settings.network_name == "Garden"

	# Its own network came up at the start, and again once the trial had worked.
	assert len(radio.named("own_up")) == 2


def check_a_join_that_never_answers_ends_when_the_time_is_up () -> None:

	"""A network that neither lets SynthSync in nor refuses it is given up when the twenty seconds are up."""

	for stuck, words in (
		(synthsync.core.wifi.TRYING, "Studio didn't answer in time"),
		(synthsync.core.wifi.NO_ADDRESS, "Studio let SynthSync join but gave it no address"),
		(synthsync.core.wifi.FAILED, "Studio wouldn't let SynthSync join"),
		(synthsync.core.wifi.IDLE, "Studio wouldn't let SynthSync join"),
	):

		wifi, radio, clock = make()

		radio.stuck = stuck
		wifi.look(0)
		clock.set_network("Studio", "studio wifi")
		spend(wifi, 20400)

		assert wifi.mode == synthsync.core.wifi.JOINING, stuck

		spend(wifi, 300)

		assert wifi.status()["mode"] == "own", stuck
		assert wifi.status()["problem"] == words, stuck
		assert clock.settings.network_name == "", stuck

		# Only a try that has failed is made again. One still under way is left to finish.
		if stuck == synthsync.core.wifi.TRYING or stuck == synthsync.core.wifi.NO_ADDRESS:
			assert len(radio.named("join")) == 1, stuck
		else:
			assert len(radio.named("join")) >= 8, stuck


def check_the_last_thing_that_went_wrong_is_the_reason_given_even_while_another_try_is_under_way () -> None:

	"""The reason shown is what last went wrong, not that another try was under way when the time ran out."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 19000)

	# The last try is still under way when the time runs out.
	radio.stuck = synthsync.core.wifi.TRYING
	spend(wifi, 3000)

	assert wifi.status()["problem"] == "There was no network called Garden in range"


def check_at_power_up_the_saved_network_is_joined_at_once () -> None:

	"""At power-up the saved network is joined at once, with no trial, and the own network doesn't come up first."""

	wifi, radio, clock = make(("Studio", "studio wifi"))

	wifi.look(0)

	# The Pico's own network never comes up on the way.
	assert radio.calls == [("join", "Studio", "studio wifi", "synthsync")]
	assert wifi.status()["mode"] == "joining"

	wifi.look(100)

	assert wifi.status() == {"mode": "joined", "name": "Studio", "address": JOINED_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}
	assert not radio.together


def check_a_saved_network_that_cant_be_joined_at_power_up_is_kept_and_the_own_network_comes_up () -> None:

	"""A saved network that isn't there at power-up brings the own network up, and stays saved for when it is back."""

	wifi, radio, clock = make(("Garden", "garden wifi"))

	wifi.look(0)
	spend(wifi, 19800)

	assert wifi.mode == synthsync.core.wifi.JOINING

	spend(wifi, 300)

	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "There was no network called Garden in range", "own": OWN_NAME, "found_at": ""}

	# It has worked before, so it is tried again the next time the Pico is switched on.
	assert clock.settings.network_name == "Garden"
	assert clock.settings.network_password == "garden wifi"

	# It isn't tried again until it is heard, not even for another change to the network settings.
	asked = len(radio.named("join"))

	clock.set_own_password("a new password")
	spend(wifi, 121000)

	assert len(radio.named("join")) == asked
	assert radio.listened == 2
	assert radio.calls[-1] == ("own_up", OWN_NAME, "a new password", "synthsync")
	assert wifi.problem == "There was no network called Garden in range"


def check_a_saved_network_refused_at_power_up_is_kept_too () -> None:

	"""A saved network that refuses its password at power-up stays saved too, with the reason shown."""

	wifi, radio, clock = make(("Studio", "an old password"))

	wifi.look(0)
	spend(wifi, 5000)

	assert wifi.status()["mode"] == "own"
	assert wifi.status()["problem"] == "Studio didn't accept that password"
	assert clock.settings.network_name == "Studio"


def check_asking_again_for_the_saved_network_tries_it_again () -> None:

	"""Picking the network that is already saved, from the page, tries it again."""

	wifi, radio, clock = make(("Garden", "garden wifi"))

	wifi.look(0)
	spend(wifi, 21000)

	assert wifi.mode == synthsync.core.wifi.OWN

	# The router is back, and the page asks for the very network the settings already hold.
	radio.networks["Garden"] = "garden wifi"
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 1000)

	assert wifi.status()["mode"] == "found"
	assert wifi.status()["problem"] == ""
	assert not radio.together


def check_a_network_lost_for_a_minute_brings_the_own_network_back_and_is_kept () -> None:

	"""A joined network that goes away, as when its router is switched off, is tried for a minute, then the own network comes back and the network stays saved."""

	wifi, radio, clock = joined()
	saved = saves(clock)

	del radio.networks["Studio"]
	radio.lose()
	spend(wifi, 59800)

	# For the whole minute it keeps trying to get back, and its own network stays off.
	assert wifi.mode == synthsync.core.wifi.JOINED
	assert len(radio.named("join")) >= 20
	assert radio.named("own_up") == []

	spend(wifi, 300)

	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "SynthSync lost Studio and couldn't get it back", "own": OWN_NAME, "found_at": ""}
	assert wifi.reason == synthsync.core.wifi.LOST
	assert radio.calls[-2:] == [("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert clock.settings.network_name == "Studio"
	assert not radio.together

	# What was saved already holds it, so nothing is written to flash again.
	assert saves(clock) == saved


def check_a_network_that_comes_back_within_the_minute_is_carried_on_with () -> None:

	"""A network that comes back inside the minute is carried on with, and the next time it goes it has a whole minute again."""

	wifi, radio, clock = joined()

	del radio.networks["Studio"]
	radio.lose()
	spend(wifi, 40000)

	radio.networks["Studio"] = "studio wifi"
	spend(wifi, 2500)

	assert wifi.status()["mode"] == "joined"
	assert wifi.status()["address"] == JOINED_ADDRESS
	assert radio.named("own_up") == []

	# Lost again, it has a whole minute again.
	del radio.networks["Studio"]
	radio.lose()
	spend(wifi, 50000)

	assert wifi.mode == synthsync.core.wifi.JOINED

	spend(wifi, 10200)

	assert wifi.mode == synthsync.core.wifi.OWN


def check_a_radio_getting_a_lost_network_back_by_itself_is_left_to () -> None:

	"""While the radio is getting a lost network back by itself, it isn't interrupted with tries of SynthSync's own."""

	wifi, radio, clock = joined()

	radio.result = synthsync.core.wifi.TRYING
	spend(wifi, 30000)

	assert radio.calls == []

	radio.result = synthsync.core.wifi.LINKED
	spend(wifi, 60000)

	assert wifi.mode == synthsync.core.wifi.JOINED
	assert radio.calls == []


def lost () -> "tuple[synthsync.core.wifi.Wifi, Radio, synthsync.core.clock.Clock]":

	"""Make a SynthSync that lost Studio, its saved network, a minute ago, and has just come back to its own network."""

	wifi, radio, clock = joined()

	del radio.networks["Studio"]
	radio.lose()
	spend(wifi, 60100)

	assert wifi.mode == synthsync.core.wifi.OWN
	assert clock.settings.network_name == "Studio"

	return wifi, radio, clock


def check_a_lost_network_that_comes_back_is_joined_again_by_itself () -> None:

	"""Back on its own network, SynthSync listens for the network it lost once a minute, and joins it for good once it is heard."""

	wifi, radio, clock = lost()

	# A whole minute passes before it first listens.
	spend(wifi, 59700)

	assert radio.listened == 0

	spend(wifi, 300)

	assert radio.listened == 1

	# Not heard, it stays on its own network and listens again a minute later.
	spend(wifi, 60000)

	assert radio.listened == 2
	assert wifi.mode == synthsync.core.wifi.OWN
	assert wifi.problem == "SynthSync lost Studio and couldn't get it back"

	# The router is back.
	radio.networks["Studio"] = "studio wifi"
	spend(wifi, 60000)

	# Heard, it is joined at once and for good: it has worked before, so there is no trial.
	assert wifi.status() == {"mode": "joined", "name": "Studio", "address": JOINED_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}
	assert radio.named("join")[-1] == ("join", "Studio", "studio wifi", "synthsync")
	assert not radio.together


def check_a_saved_network_is_never_listened_for_while_a_phone_is_on_the_own_network () -> None:

	"""A phone on the Pico's own network may be using the page, which a join would take from under it, so SynthSync waits until it has gone."""

	wifi, radio, clock = make(("Studio", "studio wifi"))

	# The router is slower to start than the Pico.
	del radio.networks["Studio"]
	wifi.look(0)
	spend(wifi, 21000)

	assert wifi.mode == synthsync.core.wifi.OWN

	radio.phones = 1
	radio.networks["Studio"] = "studio wifi"
	spend(wifi, 180000)

	assert radio.listened == 0
	assert wifi.mode == synthsync.core.wifi.OWN

	# Once the phone has gone, the next minute's listening hears it.
	radio.phones = 0
	spend(wifi, 60000)

	assert radio.listened == 1
	assert wifi.mode == synthsync.core.wifi.JOINED
	assert not radio.together


def check_nothing_is_listened_for_with_no_network_saved () -> None:

	"""A SynthSync that has never joined a network, or has forgotten it, stays on its own network and never listens."""

	wifi, radio, clock = make()

	wifi.look(0)
	spend(wifi, 300000)

	assert radio.listened == 0

	wifi, radio, clock = lost()

	clock.forget_network()
	spend(wifi, 300000)

	assert radio.listened == 0
	assert wifi.mode == synthsync.core.wifi.OWN


def check_a_saved_network_heard_but_refused_brings_the_own_network_back_to_listen_again () -> None:

	"""A router whose password has changed is heard and refused: SynthSync comes back to its own network, still keeping the network, and tries again only a minute on."""

	wifi, radio, clock = make(("Studio", "an old password"))

	wifi.look(0)
	spend(wifi, 5000)

	assert wifi.mode == synthsync.core.wifi.OWN

	asked = len(radio.named("join"))

	spend(wifi, 65000)

	assert radio.listened == 1
	assert len(radio.named("join")) == asked + 2
	assert wifi.mode == synthsync.core.wifi.OWN
	assert wifi.problem == "Studio didn't accept that password"
	assert clock.settings.network_name == "Studio"
	assert not radio.together


def check_a_radio_that_cant_listen_just_then_is_asked_again_a_minute_on () -> None:

	"""A radio too busy to listen leaves SynthSync on its own network, untroubled, until the next minute."""

	wifi, radio, clock = lost()

	radio.networks["Studio"] = "studio wifi"
	radio.deaf = True
	spend(wifi, 60000)

	assert wifi.mode == synthsync.core.wifi.OWN

	radio.deaf = False
	spend(wifi, 60000)

	assert wifi.mode == synthsync.core.wifi.JOINED


def check_a_join_asked_for_just_as_listening_is_due_is_tried_as_asked () -> None:

	"""A network picked on the page a moment before SynthSync would listen is tried once, as a trial, and not joined by the listening first."""

	wifi, radio, clock = lost()

	spend(wifi, 59700)
	clock.set_network("Cafe", "")
	spend(wifi, 1000)

	assert [call for call in radio.named("join") if call[1] == "Cafe"] == [("join", "Cafe", "", "synthsync")]
	assert wifi.mode == synthsync.core.wifi.FOUND


def check_forgetting_the_network_brings_the_own_network_back () -> None:

	"""Forgetting the network from the page while on it brings the own network back."""

	wifi, radio, clock = joined()

	clock.forget_network()
	spend(wifi, 500)

	assert radio.calls == []

	spend(wifi, 100)

	assert radio.calls == [("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status() == {"mode": "own", "name": OWN_NAME, "address": OWN_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}
	assert not radio.together


def check_a_new_password_for_the_own_network_while_joined_waits_until_that_network_is_next_up () -> None:

	"""A new password for the own network while another is joined changes nothing on the air until the own network next comes up."""

	wifi, radio, clock = joined()

	clock.set_own_password("a new password")
	spend(wifi, 3000)

	assert radio.calls == []
	assert wifi.mode == synthsync.core.wifi.JOINED

	clock.forget_network()
	spend(wifi, 1000)

	assert radio.calls == [("leave",), ("own_up", OWN_NAME, "a new password", "synthsync")]


def check_putting_every_setting_back_while_joined_brings_the_own_network_back_as_new () -> None:

	"""Putting every setting back as new while on another network brings the own network back as a new SynthSync has it."""

	wifi, radio, clock = joined()

	clock.set_own_password("a new password")
	spend(wifi, 1000)
	clock.reset()
	spend(wifi, 1000)

	assert radio.calls == [("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status()["mode"] == "own"
	assert not radio.together


def check_joining_another_network_leaves_the_first () -> None:

	"""Picking another network while joined to one leaves the first, and tries the second as a trial."""

	wifi, radio, clock = joined()

	clock.set_network("Cafe", "")
	spend(wifi, 1000)

	# It is a trial too, so the Pico comes back on its own network to say where it will be.
	assert radio.calls == [("leave",), ("join", "Cafe", "", "synthsync"), ("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status()["name"] == "Cafe"
	assert wifi.status()["mode"] == "found"
	assert not radio.together


def check_a_change_of_network_that_fails_keeps_the_network_saved_before_and_goes_back_to_it () -> None:

	"""Asked from the page for a network that isn't there, SynthSync forgets that one, saves again the network it was on, and goes back to it as after losing it."""

	wifi, radio, clock = joined()
	saved = saves(clock)

	clock.set_network("Garden", "garden wifi")
	spend(wifi, 21000)

	assert wifi.status()["mode"] == "own"
	assert wifi.status()["problem"] == "There was no network called Garden in range"

	# Garden never worked. Studio did, so it is saved again, and that is saved to flash.
	assert clock.settings.network_name == "Studio"
	assert clock.settings.network_password == "studio wifi"
	assert saves(clock) == saved + 2

	# Putting it back is no request to join, so nothing is tried until the minute's listening hears it.
	asked = len(radio.named("join"))
	spend(wifi, 59000)

	assert len(radio.named("join")) == asked
	assert wifi.mode == synthsync.core.wifi.OWN

	spend(wifi, 1100)

	assert wifi.status() == {"mode": "joined", "name": "Studio", "address": JOINED_ADDRESS, "problem": "", "own": OWN_NAME, "found_at": ""}
	assert not radio.together


def check_a_mistyped_password_for_the_saved_network_keeps_the_password_that_worked () -> None:

	"""The same network asked for again with a wrong password is refused, and the password it was saved with is kept."""

	wifi, radio, clock = make(("Studio", "studio wifi"))

	wifi.look(0)
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.JOINED

	clock.set_network("Studio", "studio wify")
	spend(wifi, 4000)

	assert wifi.reason == synthsync.core.wifi.WRONG_PASSWORD
	assert clock.settings.network_name == "Studio"
	assert clock.settings.network_password == "studio wifi"

	# A restart, as after a power cut, joins it with the password that works.
	wifi.close()
	radio.calls = []
	wifi.look(0)
	spend(wifi, 1000)

	assert radio.named("join") == [("join", "Studio", "studio wifi", "synthsync")]
	assert wifi.mode == synthsync.core.wifi.JOINED


def check_a_failed_join_with_nothing_saved_before_leaves_nothing_saved () -> None:

	"""A SynthSync that had no network saved has none after a join of one that fails."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 21000)

	assert wifi.mode == synthsync.core.wifi.OWN
	assert clock.settings.network_name == ""
	assert clock.settings.network_password == ""


def check_a_forgotten_network_isnt_put_back_after_a_failed_join () -> None:

	"""Forgetting a network on the page means it: a failed join after that leaves nothing saved."""

	wifi, radio, clock = joined()

	clock.forget_network()
	spend(wifi, 1000)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 21000)

	assert wifi.mode == synthsync.core.wifi.OWN
	assert clock.settings.network_name == ""


def check_asking_for_the_network_it_is_already_on_does_nothing () -> None:

	"""Picking the network SynthSync is on, with the same password, does nothing, and with another password it is tried."""

	wifi, radio, clock = joined()

	clock.set_network("Studio", "studio wifi")
	spend(wifi, 3000)

	assert radio.calls == []
	assert wifi.mode == synthsync.core.wifi.JOINED

	# The same name with another password is another request, and is tried.
	clock.set_network("Studio", "another password")
	spend(wifi, 700)

	assert radio.calls == [("leave",), ("join", "Studio", "another password", "synthsync")]


def check_an_old_reason_goes_when_the_next_thing_is_asked () -> None:

	"""Why the last join failed is shown until the next thing is asked: forgetting the network, or another join, clears it."""

	wifi, radio, clock = make(("Garden", "garden wifi"))

	wifi.look(0)
	spend(wifi, 21000)

	assert wifi.problem != ""

	asked = len(radio.calls)

	# Forgetting it clears the reason, and the own network, already up, isn't started again.
	clock.forget_network()
	spend(wifi, 1000)

	assert wifi.problem == ""
	assert wifi.reason == ""
	assert len(radio.calls) == asked

	# And a reason is gone as soon as another join begins.
	clock.set_network("Studio", "not the password")
	spend(wifi, 4000)

	assert wifi.problem == "Studio didn't accept that password"

	radio.stuck = synthsync.core.wifi.TRYING
	clock.set_network("Studio", "studio wifi")
	spend(wifi, 700)

	assert wifi.status()["mode"] == "joining"
	assert wifi.status()["problem"] == ""


def check_a_radio_that_cant_do_as_asked_is_started_afresh_from_the_settings () -> None:

	"""A radio that fails at what it is asked is started afresh from the settings at the next look, with whatever it left up switched off first."""

	wifi, radio, clock = make()

	radio.broken = True

	for _ in range(3):

		try:
			wifi.look(100)
			assert False, "the radio's failure went unsaid"

		except OSError:
			pass

	assert wifi.status()["address"] == ""

	radio.broken = False
	wifi.look(100)

	# Whatever the failure left up is switched off first, so the two networks are never up together.
	assert radio.calls == [("own_down",), ("leave",), ("own_up", OWN_NAME, "synthsync", "synthsync")]
	assert wifi.status()["mode"] == "own"

	# A failure half way through a change is recovered from the same way.
	clock.set_network("Studio", "studio wifi")
	wifi.look(100)
	radio.broken = True

	try:
		spend(wifi, 1000)
		assert False, "the radio's failure went unsaid"

	except OSError:
		pass

	radio.broken = False
	radio.calls = []
	wifi.look(100)

	assert radio.calls == [("own_down",), ("leave",), ("join", "Studio", "studio wifi", "synthsync")]

	# It was asked for from the page, so it is still a trial, and the page learns where SynthSync will be.
	spend(wifi, 1000)

	assert wifi.status()["mode"] == "found"
	assert wifi.status()["found_at"] == JOINED_ADDRESS
	assert not radio.together


def check_a_radio_that_fails_as_a_join_is_asked_still_puts_back_the_network_saved_before () -> None:

	"""The radio failing just as the page's join begins doesn't make the join one for good, and when it fails the network that worked is still saved again."""

	wifi, radio, clock = joined()

	clock.set_network("Garden", "garden wifi")
	wifi.look(100)
	radio.broken = True

	try:
		spend(wifi, 1000)
		assert False, "the radio's failure went unsaid"

	except OSError:
		pass

	radio.broken = False
	spend(wifi, 22000)

	assert wifi.status()["mode"] == "own"
	assert wifi.status()["problem"] == "There was no network called Garden in range"
	assert clock.settings.network_name == "Studio"
	assert clock.settings.network_password == "studio wifi"


def check_a_radio_that_fails_during_a_first_join_doesnt_make_the_network_count_as_having_worked () -> None:

	"""A radio failing part way through a network's first join doesn't make that network count as one that has worked."""

	wifi, radio, clock = make()

	wifi.look(0)
	clock.set_network("Garden", "garden wifi")
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.JOINING

	radio.broken = True

	try:
		spend(wifi, 3000)
		assert False, "the radio's failure went unsaid"

	except OSError:
		pass

	radio.broken = False
	spend(wifi, 22000)

	# It is tried again from the start, and still forgotten when it fails.
	assert wifi.status()["mode"] == "own"
	assert wifi.status()["problem"] == "There was no network called Garden in range"
	assert clock.settings.network_name == ""


def check_the_two_networks_are_never_up_together_whatever_is_asked () -> None:

	"""Whatever is asked, in whatever order, the own network and a joined one are never up at the same moment."""

	wifi, radio, clock = make()

	wifi.look(0)

	asks: "tuple[typing.Callable[[], object], ...]" = (
		lambda: clock.set_network("Studio", "studio wifi"),
		lambda: clock.set_own_password("a new password"),
		lambda: clock.set_network("Garden", "garden wifi"),
		lambda: clock.set_network("Cafe", ""),
		lambda: clock.forget_network(),
		lambda: clock.set_network("Studio", "not the password"),
		lambda: clock.reset(),
		lambda: clock.set_network("Cafe", ""),
		lambda: radio.lose(),
		lambda: clock.set_host_name("studio-clock"),
		lambda: clock.set_network("Studio", "studio wifi"),
	)

	# Each thing is asked at every sort of moment: mid-join, mid-wait, and once settled.
	for gap in (100, 300, 700, 2500, 23000):

		for ask in asks:

			ask()
			spend(wifi, gap)

			assert not radio.together
			assert radio.own_is_up != radio.away

	assert wifi.status()["mode"] in ("own", "joining", "found", "joined")


def check_the_networks_in_range_are_listed_tidily_and_a_radio_that_cant_listen_says_so_in_words () -> None:

	"""Looking for networks gives the tidy list the page shows, and a radio that can't listen just then gives words fit to show instead."""

	wifi, radio, clock = make()

	wifi.look(0)

	assert wifi.scan() == [
		{"name": "Studio", "strength": -48, "secure": True},
		{"name": "Cafe", "strength": -83, "secure": False},
	]

	radio.deaf = True

	try:
		wifi.scan()
		assert False, "a scan that failed went unsaid"

	except ValueError as problem:
		assert "couldn't look for networks" in str(problem)


def check_nothing_is_listened_for_in_the_middle_of_a_join () -> None:

	"""Looking for networks while a join is under way is refused in words, since it would hold the Pico up and could spoil the join."""

	wifi, radio, clock = make()

	radio.stuck = synthsync.core.wifi.TRYING
	wifi.look(0)
	clock.set_network("Studio", "studio wifi")
	spend(wifi, 1000)

	assert wifi.mode == synthsync.core.wifi.JOINING

	# The radio would take ten seconds over it, and the join could be spoiled.
	radio.deaf = True

	try:
		wifi.scan()
		assert False, "a scan was made in the middle of a join"

	except ValueError as problem:
		assert "busy joining" in str(problem)

	# Once the trial has worked, its own network is back, and it listens as usual.
	radio.deaf = False
	radio.result = synthsync.core.wifi.LINKED
	spend(wifi, 200)

	assert [each["name"] for each in wifi.scan()] == ["Studio", "Cafe"]


def check_closing_switches_everything_off_and_a_look_afterwards_starts_afresh () -> None:

	"""Closing switches both networks off, and a look afterwards starts again from the settings."""

	wifi, radio, clock = joined()

	wifi.close()

	assert radio.calls == [("leave",), ("own_down",)]
	assert not radio.own_is_up and not radio.away
	assert wifi.status()["address"] == ""

	radio.calls = []
	wifi.look(100)

	assert radio.calls == [("join", "Studio", "studio wifi", "synthsync")]

	wifi, radio, clock = make()

	wifi.look(0)
	wifi.close()

	assert radio.calls[1:] == [("own_down",)]
	assert not radio.own_is_up
