"""A phone that joins SynthSync's own network is sent to the page the same way on the Pico's Python as on a laptop's."""

import synthsync.web.portal

OWN = "192.168.4.1"

# The Pico's own address, as an answer holds it.
PACKED = bytes([192, 168, 4, 1])

# What a question asks for: an address of the older kind, and of the newer.
A = b"\x00\x01"
AAAA = b"\x00\x1c"

# A note some phones add after the question, saying how long an answer they can take.
ROOMY = b"\x00\x00\x29\x10\x00\x00\x00\x00\x00\x00\x00"


def question (name: str, kind: bytes = A) -> bytes:

	"""Give the question part of a look-up of one name: each part after its length, an ending, what is asked and where."""

	parts = [bytes([len(part)]) + part.encode() for part in name.split(".")]

	return b"".join(parts) + b"\x00" + kind + b"\x00\x01"


def look_up (name: str, kind: bytes = A, number: bytes = b"\x12\x34", flags: bytes = b"\x01\x00", questions: int = 1, after: bytes = b"") -> bytes:

	"""Make a look-up of one name, as a phone sends it, asking for the answer to be looked for if need be."""

	return number + flags + bytes([0, questions]) + b"\x00\x00\x00\x00" + bytes([0, 1 if after else 0]) + question(name, kind) + after


def check_a_look_up_of_any_name_is_answered_with_the_picos_own_address () -> None:

	"""Whatever the name, the answer is the Pico, from the one who knows, to be kept ten seconds."""

	for name in ("connectivitycheck.gstatic.com", "captive.apple.com", "www.msftconnecttest.com", "synthsync"):

		asked = question(name)
		reply = synthsync.web.portal.answer(look_up(name), OWN)

		assert reply is not None, name

		# The look-up's own number, said to be an answer with nothing wrong, from the one who knows, and asked for again.
		assert reply[0:2] == b"\x12\x34"
		assert reply[2] == 0x85
		assert reply[3] == 0x80

		# One question and one answer, and nothing else.
		assert reply[4:12] == b"\x00\x01\x00\x01\x00\x00\x00\x00"
		assert reply[12:12 + len(asked)] == asked

		# The answer names the question's name, then the kind, how long to keep it, and the address.
		assert reply[12 + len(asked):] == b"\xc0\x0c" + A + b"\x00\x01" + b"\x00\x00\x00\x0a" + b"\x00\x04" + PACKED

	# Not asked to look further, it says it wasn't.
	reply = synthsync.web.portal.answer(look_up("captive.apple.com", flags=b"\x00\x00"), OWN)

	assert reply is not None
	assert reply[2] == 0x84


def check_a_look_up_for_an_address_of_another_kind_is_told_there_is_none () -> None:

	"""Asked for an address of the newer kind, the Pico says the name has none, rather than leave the phone waiting."""

	asked = question("connectivitycheck.gstatic.com", AAAA)
	reply = synthsync.web.portal.answer(look_up("connectivitycheck.gstatic.com", AAAA), OWN)

	assert reply is not None
	assert reply[3] == 0x80
	assert reply[4:12] == b"\x00\x01\x00\x00\x00\x00\x00\x00"
	assert reply[12:] == asked


def check_what_a_phone_adds_after_its_question_is_left_out_of_the_answer () -> None:

	"""A note after the question gets no answer of its own, and the question is still answered."""

	asked = question("clients3.google.com")
	reply = synthsync.web.portal.answer(look_up("clients3.google.com", after=ROOMY), OWN)

	assert reply is not None
	assert reply[4:12] == b"\x00\x01\x00\x01\x00\x00\x00\x00"
	assert reply[12:12 + len(asked)] == asked
	assert reply.endswith(PACKED)


def check_anything_but_a_plain_look_up_is_left_unanswered () -> None:

	"""An answer, a request of some other sort, two questions, a look-up cut short or one that points elsewhere gets nothing back."""

	whole = look_up("captive.apple.com")
	cut = len(look_up("x")) - 5

	for odd in (
		b"",
		whole[:11],
		look_up("captive.apple.com", flags=b"\x81\x80"),
		look_up("captive.apple.com", flags=b"\x28\x00"),
		look_up("captive.apple.com", questions=2),
		look_up("captive.apple.com", questions=0),
		whole[:cut],
		whole[:-1],
		whole[:12] + b"\xc0\x0c" + A + b"\x00\x01",
		whole[:12] + b"\x3f" + b"a" * 4,
	):
		assert synthsync.web.portal.answer(odd, OWN) is None, odd


def check_nothing_is_answered_without_an_address_of_the_older_kind () -> None:

	"""With no address to give, or one that isn't of the form 192.168.4.1, no look-up is answered."""

	for address in ("", "localhost", "192.168.4", "192.168.4.1.5", "192.168.4.300", "192.168.four.1", "-1.0.0.0"):
		assert synthsync.web.portal.answer(look_up("captive.apple.com"), address) is None, address


def check_a_phone_is_sent_to_the_pico_only_while_its_own_network_is_up () -> None:

	"""The address a phone is sent to is the Pico's own while that network is up, through a trial that found another, and nothing otherwise."""

	assert synthsync.web.portal.sent_to({"mode": "own", "address": OWN}) == OWN
	assert synthsync.web.portal.sent_to({"mode": "found", "address": OWN}) == OWN
	assert synthsync.web.portal.sent_to({"mode": "joining", "address": ""}) == ""
	assert synthsync.web.portal.sent_to({"mode": "joined", "address": "192.168.0.20"}) == ""
	assert synthsync.web.portal.sent_to({"mode": "none", "address": ""}) == ""


def check_a_request_is_the_picos_own_unless_it_names_another_site () -> None:

	"""A request naming another site isn't the Pico's own while a phone is being sent to the page. Naming the Pico by its name or an address, or naming nothing at all, it is, and every request is once the Pico's own network is down."""

	for other in ("connectivitycheck.gstatic.com", "captive.apple.com:80", "synthsync.lan", "clock-stopper.example", "studio-clock.local"):
		assert not synthsync.web.portal.for_here(other, OWN, "synthsync"), other

	for mine in (OWN, OWN + ":80", "10.0.0.7", "10.0.0.7:8080", "[fe80::1]", "[fe80::1]:80", "synthsync", "synthsync.local", "SynthSync.LOCAL:80", "", None):
		assert synthsync.web.portal.for_here(mine, OWN, "synthsync"), mine

	# On a laptop the Pico is pretended to be at localhost, which is then its own.
	assert synthsync.web.portal.for_here("localhost:8080", "localhost", "synthsync")

	for anything in ("connectivitycheck.gstatic.com", OWN, None):
		assert synthsync.web.portal.for_here(anything, "", "synthsync"), anything


def check_the_pico_is_named_by_its_addresses_and_its_own_name_as_it_is_now () -> None:

	"""What names the Pico, wherever it is: an address, its name with .local or without, or localhost. A site that has pointed its own name at the Pico doesn't."""

	for mine in ("192.168.0.144", "192.168.0.144:80", "[fe80::1]:80", "studio-clock", "studio-clock.local", "Studio-Clock.Local:80", "localhost", "localhost:8000", "", None):
		assert synthsync.web.portal.names_pico(mine, "studio-clock"), mine

	for other in ("synthsync.local", "clock-stopper.example", "studio-clock.example", "studio-clock.local.example", "192.168.0", "fe80::1"):
		assert not synthsync.web.portal.names_pico(other, "studio-clock"), other


class Listener:

	"""Stands in for the socket look-ups arrive on, holding the ones that have arrived and keeping what is sent back."""

	def __init__ (self, waiting: "list[bytes]", full: int = 0) -> None:

		"""Begin with these look-ups waiting, and with no room for the first `full` answers."""

		self.waiting = list(waiting)
		self.sent: "list[tuple[bytes, tuple[str, int]]]" = []
		self.full = full

	def recvfrom (self, most: int) -> "tuple[bytes, tuple[str, int]]":

		"""Give the next look-up and who sent it, failing as a socket does when there are none."""

		if not self.waiting:
			raise OSError(11)

		return self.waiting.pop(0)[:most], ("192.168.4.16", 5353)

	def sendto (self, data: bytes, to: "tuple[str, int]") -> None:

		"""Keep an answer, failing as a socket does when there is no room for it."""

		if self.full:
			self.full -= 1
			raise OSError(12)

		self.sent.append((data, to))


def check_look_ups_are_answered_a_few_at_a_time () -> None:

	"""However many have arrived, only so many are answered at each look, and the rest wait for the next."""

	most = synthsync.web.portal.MOST_AT_ONCE
	listener = Listener([look_up("captive.apple.com") for _ in range(most + 2)])
	asked = []

	def where () -> str:

		"""Give the Pico's own address, noting each time it is asked for."""

		asked.append(1)

		return OWN

	assert synthsync.web.portal.hear(listener, where) == most
	assert len(listener.sent) == most
	assert listener.sent[0][1] == ("192.168.4.16", 5353)
	assert listener.sent[0][0].endswith(PACKED)

	assert synthsync.web.portal.hear(listener, where) == 2
	assert synthsync.web.portal.hear(listener, where) == 0

	# Where to send phones is asked once a look, and only when there is something to answer.
	assert len(asked) == 2


def check_a_look_up_that_cant_be_answered_is_dropped_and_the_rest_are_answered () -> None:

	"""Nonsense, and an answer there was no room to send, are passed over, and what follows them is still answered."""

	listener = Listener([b"nonsense", look_up("captive.apple.com"), look_up("www.google.com")], full=1)

	assert synthsync.web.portal.hear(listener, lambda: OWN) == 1
	assert len(listener.sent) == 1
	assert listener.waiting == []


def check_look_ups_are_left_unanswered_while_the_own_network_is_down () -> None:

	"""While there is nowhere to send a phone, look-ups are taken and nothing goes back, so none pile up for later."""

	listener = Listener([look_up("captive.apple.com"), look_up("www.google.com")])

	assert synthsync.web.portal.hear(listener, lambda: "") == 0
	assert listener.sent == []
	assert listener.waiting == []
