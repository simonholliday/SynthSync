"""A press is told from chatter and knocks the same way on the Pico's Python as on a laptop's."""

import synthsync.core.buttons


def said_yes (thing: synthsync.core.buttons.Button | synthsync.core.buttons.Hold, spans: tuple[tuple[int, bool], ...], every: int = 5, away: tuple[tuple[int, int], ...] = ()) -> list[int]:

	"""Play a stretch of a button's life to something that looks at it, and give the times it said yes.

	`spans` is a run of (milliseconds, pressed), saying how the pin reads as
	time passes. A look comes every `every` milliseconds, except during the
	stretches in `away`, each a (from, to), when the Pico is busy and nothing
	looks. The pin remembers when it went to pressed and when it let go
	between looks, as a real one does.
	"""

	readings = []
	downs = []
	ups = []
	start = 0
	was = False

	for length, pressed in spans:

		readings.append((start, start + length, pressed))

		if pressed and not was:
			downs.append(start)

		if was and not pressed:
			ups.append(start)

		was = pressed
		start += length

	said = []
	last = 0
	now = every

	while now < start:

		if not any(begins < now <= ends for begins, ends in away):

			pressed = [reading for begins, ends, reading in readings if begins <= now < ends][0]
			went_down = [moment for moment in downs if last < moment <= now]
			let_go = [moment for moment in ups if last < moment <= now]
			unseen = 0

			if went_down and pressed:
				unseen = now - went_down[0]
			elif went_down and let_go:
				unseen = max(let_go[-1] - went_down[0], 0)

			if thing.look(pressed, unseen, now - last):
				said.append(now)

			last = now

		now += every

	return said


def check_a_press_counts_once_and_about_20_ms_after_it_begins () -> None:

	"""However long a button is held, it is one press, believed once it has read as pressed for 20 ms."""

	said = said_yes(synthsync.core.buttons.Button(), ((50, False), (400, True), (100, False)))

	assert len(said) == 1, said
	assert 65 <= said[0] <= 75, said


def check_chatter_as_a_button_closes_and_opens_is_one_press () -> None:

	"""Contacts that bounce for a few milliseconds each way don't make extra presses."""

	closing = ((1, True), (1, False), (2, True), (1, False), (1, True), (2, False))
	opening = ((1, False), (1, True), (2, False), (1, True), (1, False), (2, True))
	spans = ((50, False),) + closing + ((150, True),) + opening + ((100, False),)

	for every in (1, 3, 5, 7):
		assert len(said_yes(synthsync.core.buttons.Button(), spans, every)) == 1, every


def check_a_knock_shorter_than_20_ms_is_ignored () -> None:

	"""Something that reads as pressed for a moment and goes away isn't a press."""

	for length in (1, 4, 10, 14):
		assert said_yes(synthsync.core.buttons.Button(), ((50, False), (length, True), (200, False))) == [], length

	# Nor is a spike that came and went between two looks, which only the pin's memory shows.
	assert said_yes(synthsync.core.buttons.Button(), ((52, False), (1, True), (200, False))) == []

	# Nor is one that came while the Pico was busy, however long it was busy for.
	for length in (1, 10, 14):
		assert said_yes(synthsync.core.buttons.Button(), ((100, False), (length, True), (500, False)), away=((40, 400),)) == [], length


def check_knocks_one_after_another_never_add_up_to_a_press () -> None:

	"""Each knock is judged alone: ten short ones, spread out, are still not a press."""

	spans = ((50, False),) + ((8, True), (42, False)) * 10

	for every in (1, 5):
		assert said_yes(synthsync.core.buttons.Button(), spans, every) == [], every

	# The same of a button that keeps reading as let go for a moment while it is held: it is never let go.
	held = ((50, False), (100, True)) + ((8, False), (42, True)) * 10 + ((100, False),)

	assert len(said_yes(synthsync.core.buttons.Button(), held)) == 1


def check_two_presses_are_two () -> None:

	"""Letting go and pressing again is a second press."""

	spans = ((50, False), (100, True), (100, False), (100, True), (100, False))

	assert len(said_yes(synthsync.core.buttons.Button(), spans)) == 2


def check_a_press_made_while_the_pico_was_busy_still_counts () -> None:

	"""A tap that came and went while nothing was looking is taken on the pin's word, once, if it was long enough."""

	for length in (25, 100, 300):

		spans = ((50, False), (length, True), (400, False))
		said = said_yes(synthsync.core.buttons.Button(), spans, away=((40, 400),))

		assert said == [405], (length, said)


def check_a_press_still_held_when_the_pico_comes_back_counts_once () -> None:

	"""A button pressed while nothing was looking, and still down afterwards, is one press."""

	spans = ((100, False), (600, True), (100, False))
	said = said_yes(synthsync.core.buttons.Button(), spans, away=((50, 400),))

	assert said == [405], said


def check_a_button_pressed_just_as_the_pico_comes_back_is_believed_after_20_ms_and_not_at_once () -> None:

	"""However long the Pico was busy, a press that has only just begun still has to last."""

	said = said_yes(synthsync.core.buttons.Button(), ((398, False), (200, True), (100, False)), away=((40, 400),))

	assert len(said) == 1 and 415 <= said[0] <= 425, said

	# And one that ends before then doesn't count.
	assert said_yes(synthsync.core.buttons.Button(), ((398, False), (12, True), (300, False)), away=((40, 400),)) == []


def check_letting_go_and_pressing_again_while_busy_is_a_second_press () -> None:

	"""Down before and down after, but let go in between: the pin's memory shows the second press."""

	spans = ((20, False), (100, True), (100, False), (400, True), (100, False))
	said = said_yes(synthsync.core.buttons.Button(), spans, away=((130, 400),))

	assert len(said) == 2 and said[1] == 405, said

	# Held all the way through, it is still only the one.
	held = ((20, False), (600, True), (100, False))

	assert len(said_yes(synthsync.core.buttons.Button(), held, away=((130, 400),))) == 1


def check_stop_must_be_held_for_five_seconds_to_put_the_settings_back () -> None:

	"""A knock or a press of under five seconds does nothing. Five seconds held does it, once, however long it is held after."""

	hold = synthsync.core.buttons.Hold

	assert synthsync.core.buttons.HOLD == 5000

	assert said_yes(hold(), ((50, False), (300, True), (100, False))) == []
	assert said_yes(hold(), ((50, False), (4900, True), (100, False))) == []

	said = said_yes(hold(), ((50, False), (5200, True), (100, False)))

	# Five seconds from when it went down, not from when that was believed.
	assert len(said) == 1 and 5045 <= said[0] <= 5060, said

	assert len(said_yes(hold(), ((50, False), (12000, True), (100, False)))) == 1

	# The same when the Pico was busy as it went down, and only looked a second later.
	said = said_yes(hold(), ((50, False), (5200, True), (100, False)), away=((40, 1000),))

	assert len(said) == 1 and 5045 <= said[0] <= 5060, said


def check_letting_go_for_a_moment_starts_the_count_again () -> None:

	"""Three seconds, a gap, and three more is not five seconds. Holding on from the second press is."""

	hold = synthsync.core.buttons.Hold

	assert said_yes(hold(), ((3000, True), (100, False), (3000, True), (100, False))) == []

	said = said_yes(hold(), ((3000, True), (100, False), (5100, True), (100, False)))

	assert len(said) == 1 and 8095 <= said[0] <= 8110, said

	# It can be done again after letting go.
	assert len(said_yes(hold(), ((5100, True), (100, False), (5100, True), (100, False)))) == 2


def check_a_hold_counts_through_a_busy_spell_unless_it_was_broken () -> None:

	"""Held from before the Pico was busy until after counts in full. Let go and pressed again meanwhile starts again."""

	hold = synthsync.core.buttons.Hold

	said = said_yes(hold(), ((50, False), (5450, True), (100, False)), away=((1000, 5200),))

	assert said == [5205], said

	broken = ((50, False), (1450, True), (100, False), (3900, True), (100, False))

	assert said_yes(hold(), broken, away=((1000, 5200),)) == []


def check_the_light_is_on_for_the_first_quarter_of_each_beat_while_the_clock_runs () -> None:

	"""Once a beat, and never when the clock isn't running or can't say where it is."""

	light = synthsync.core.buttons.light

	assert light(True, 0)
	assert light(True, 249)
	assert not light(True, 250)
	assert not light(True, 999)
	assert light(True, 1000)
	assert light(True, 7100)
	assert not light(True, 7600)

	assert not light(True, -1)
	assert not light(False, 0)
	assert not light(False, 7100)


def check_holding_stop_takes_the_lights_over_from_two_seconds_and_keeps_them_lit_once_the_settings_are_back () -> None:

	"""Nothing for two seconds, then fast flashes until five, then steady on until the button is let go, and both lights alike."""

	hold = synthsync.core.buttons.Hold()
	light = synthsync.core.buttons.light
	warning = synthsync.core.buttons.warning
	seen = []
	done_at = 0

	assert warning(None) is None

	for now in range(5, 5300, 5):

		if hold.look(True, 0, 5):
			done_at = now

		seen.append((now, light(False, -1, hold), warning(hold)))

	# The first two seconds leave the lights as the clock has them, which is out once stop has stopped it.
	assert all(warned is None and not lit for now, lit, warned in seen if now < 1990)

	# From two seconds the tempo LED is worked as the Pico's own light is.
	assert all(warned is not None and warned == lit for now, lit, warned in seen if now > 2010)

	# On for 60 ms and off for 60 ms, from two seconds to five, is about fifty changes.
	flashing = [lit for now, lit, warned in seen if 1990 < now < 4990]
	changes = sum(1 for index in range(1, len(flashing)) if flashing[index] != flashing[index - 1])

	assert 45 <= changes <= 55, changes
	assert 4990 <= done_at <= 5010, done_at

	# Done, they are on whatever the clock is doing, until the button is let go.
	for now in range(0, 500, 5):
		hold.look(True, 0, 5)
		assert light(False, -1, hold) and warning(hold) is True
		assert light(True, 600, hold)

	for now in range(0, 40, 5):
		hold.look(False, 0, 5)

	assert warning(hold) is None
	assert not light(False, -1, hold)
	assert light(True, 100, hold)
	assert not light(True, 600, hold)


def check_letting_go_of_stop_before_five_seconds_gives_the_lights_back () -> None:

	"""Warned, and let go at four seconds: nothing is put back, and the lights are the clock's again."""

	hold = synthsync.core.buttons.Hold()

	for now in range(5, 4000, 5):
		assert not hold.look(True, 0, 5)

	assert synthsync.core.buttons.warning(hold) is not None

	for now in range(0, 40, 5):
		assert not hold.look(False, 0, 5)

	assert synthsync.core.buttons.warning(hold) is None
	assert not synthsync.core.buttons.light(False, -1, hold)

