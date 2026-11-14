"""A press is told from chatter and knocks the same way on the Pico's Python as on a laptop's."""

import picoclock.core.buttons


def said_yes (thing: picoclock.core.buttons.Button | picoclock.core.buttons.Hold, spans: tuple[tuple[int, bool], ...], every: int = 5, away: tuple[tuple[int, int], ...] = ()) -> list[int]:

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

	said = said_yes(picoclock.core.buttons.Button(), ((50, False), (400, True), (100, False)))

	assert len(said) == 1, said
	assert 65 <= said[0] <= 75, said


def check_chatter_as_a_button_closes_and_opens_is_one_press () -> None:

	"""Contacts that bounce for a few milliseconds each way don't make extra presses."""

	closing = ((1, True), (1, False), (2, True), (1, False), (1, True), (2, False))
	opening = ((1, False), (1, True), (2, False), (1, True), (1, False), (2, True))
	spans = ((50, False),) + closing + ((150, True),) + opening + ((100, False),)

	for every in (1, 3, 5, 7):
		assert len(said_yes(picoclock.core.buttons.Button(), spans, every)) == 1, every


def check_a_knock_shorter_than_20_ms_is_ignored () -> None:

	"""Something that reads as pressed for a moment and goes away isn't a press."""

	for length in (1, 4, 10, 14):
		assert said_yes(picoclock.core.buttons.Button(), ((50, False), (length, True), (200, False))) == [], length

	# Nor is a spike that came and went between two looks, which only the pin's memory shows.
	assert said_yes(picoclock.core.buttons.Button(), ((52, False), (1, True), (200, False))) == []

	# Nor is one that came while the Pico was busy, however long it was busy for.
	for length in (1, 10, 14):
		assert said_yes(picoclock.core.buttons.Button(), ((100, False), (length, True), (500, False)), away=((40, 400),)) == [], length


def check_knocks_one_after_another_never_add_up_to_a_press () -> None:

	"""Each knock is judged alone: ten short ones, spread out, are still not a press."""

	spans = ((50, False),) + ((8, True), (42, False)) * 10

	for every in (1, 5):
		assert said_yes(picoclock.core.buttons.Button(), spans, every) == [], every

	# The same of a button that keeps reading as let go for a moment while it is held: it is never let go.
	held = ((50, False), (100, True)) + ((8, False), (42, True)) * 10 + ((100, False),)

	assert len(said_yes(picoclock.core.buttons.Button(), held)) == 1


def check_two_presses_are_two () -> None:

	"""Letting go and pressing again is a second press."""

	spans = ((50, False), (100, True), (100, False), (100, True), (100, False))

	assert len(said_yes(picoclock.core.buttons.Button(), spans)) == 2


def check_a_press_made_while_the_pico_was_busy_still_counts () -> None:

	"""A tap that came and went while nothing was looking is taken on the pin's word, once, if it was long enough."""

	for length in (25, 100, 300):

		spans = ((50, False), (length, True), (400, False))
		said = said_yes(picoclock.core.buttons.Button(), spans, away=((40, 400),))

		assert said == [405], (length, said)


def check_a_press_still_held_when_the_pico_comes_back_counts_once () -> None:

	"""A button pressed while nothing was looking, and still down afterwards, is one press."""

	spans = ((100, False), (600, True), (100, False))
	said = said_yes(picoclock.core.buttons.Button(), spans, away=((50, 400),))

	assert said == [405], said


def check_a_button_pressed_just_as_the_pico_comes_back_is_believed_after_20_ms_and_not_at_once () -> None:

	"""However long the Pico was busy, a press that has only just begun still has to last."""

	said = said_yes(picoclock.core.buttons.Button(), ((398, False), (200, True), (100, False)), away=((40, 400),))

	assert len(said) == 1 and 415 <= said[0] <= 425, said

	# And one that ends before then doesn't count.
	assert said_yes(picoclock.core.buttons.Button(), ((398, False), (12, True), (300, False)), away=((40, 400),)) == []


def check_letting_go_and_pressing_again_while_busy_is_a_second_press () -> None:

	"""Down before and down after, but let go in between: the pin's memory shows the second press."""

	spans = ((20, False), (100, True), (100, False), (400, True), (100, False))
	said = said_yes(picoclock.core.buttons.Button(), spans, away=((130, 400),))

	assert len(said) == 2 and said[1] == 405, said

	# Held all the way through, it is still only the one.
	held = ((20, False), (600, True), (100, False))

	assert len(said_yes(picoclock.core.buttons.Button(), held, away=((130, 400),))) == 1


def check_the_reset_button_must_be_held_for_three_seconds () -> None:

	"""A knock or a short press does nothing. Three seconds held does it, once, however long it is held after."""

	hold = picoclock.core.buttons.Hold

	assert said_yes(hold(), ((50, False), (300, True), (100, False))) == []
	assert said_yes(hold(), ((50, False), (2900, True), (100, False))) == []

	said = said_yes(hold(), ((50, False), (3200, True), (100, False)))

	# Three seconds from when it went down, not from when that was believed.
	assert len(said) == 1 and 3045 <= said[0] <= 3060, said

	assert len(said_yes(hold(), ((50, False), (10000, True), (100, False)))) == 1

	# The same when the Pico was busy as it went down, and only looked a second later.
	said = said_yes(hold(), ((50, False), (3200, True), (100, False)), away=((40, 1000),))

	assert len(said) == 1 and 3045 <= said[0] <= 3060, said


def check_letting_go_for_a_moment_starts_the_count_again () -> None:

	"""Two seconds, a gap, and two more is not three seconds. Holding on from the second press is."""

	hold = picoclock.core.buttons.Hold

	assert said_yes(hold(), ((2000, True), (100, False), (2000, True), (100, False))) == []

	said = said_yes(hold(), ((2000, True), (100, False), (3100, True), (100, False)))

	assert len(said) == 1 and 5095 <= said[0] <= 5110, said

	# It can be done again after letting go.
	assert len(said_yes(hold(), ((3100, True), (100, False), (3100, True), (100, False)))) == 2


def check_a_hold_counts_through_a_busy_spell_unless_it_was_broken () -> None:

	"""Held from before the Pico was busy until after counts in full. Let go and pressed again meanwhile starts again."""

	hold = picoclock.core.buttons.Hold

	said = said_yes(hold(), ((50, False), (3450, True), (100, False)), away=((1000, 3200),))

	assert said == [3205], said

	broken = ((50, False), (1450, True), (100, False), (1900, True), (100, False))

	assert said_yes(hold(), broken, away=((1000, 3200),)) == []


def check_the_light_is_on_for_the_first_quarter_of_each_beat_while_the_clock_runs () -> None:

	"""Once a beat, and never when the clock isn't running or can't say where it is."""

	light = picoclock.core.buttons.light

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


def check_the_light_flashes_fast_while_reset_is_held_and_stays_on_once_it_is_done () -> None:

	"""Holding the reset button takes the light over: fast flashes, then steady on until the button is let go."""

	hold = picoclock.core.buttons.Hold()
	light = picoclock.core.buttons.light
	seen = []
	done_at = 0

	for now in range(5, 3200, 5):

		if hold.look(True, 0, 5):
			done_at = now

		seen.append(light(False, -1, hold))

	changes = sum(1 for index in range(1, len(seen)) if seen[index] != seen[index - 1])

	# On for 60 ms and off for 60 ms, for three seconds, is about fifty changes.
	assert 45 <= changes <= 55, changes
	assert done_at > 0

	# Done, it is on whatever the clock is doing, until the button is let go.
	for now in range(0, 500, 5):
		hold.look(True, 0, 5)
		assert light(False, -1, hold)
		assert light(True, 600, hold)

	for now in range(0, 40, 5):
		hold.look(False, 0, 5)

	assert not light(False, -1, hold)
	assert light(True, 100, hold)
	assert not light(True, 600, hold)
