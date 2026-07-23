"""
Tests for the read filters.

Both bugs this code has ever had were silent and severe: the reading looked
plausible while being wrong for the rest of the game. So most of these tests
exist to pin down a specific failure that actually happened, and each one says
which.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

from loom import filters


def feed(filter_object, readings):
    """Push a list of readings through a filter and return what it believed."""
    return [filter_object.update(reading) for reading in readings]


# ---- villager count ----------------------------------------------------

def test_a_value_must_repeat_before_it_is_believed():
    counter = filters.StableCount(required_repeats=2)
    assert counter.update(10) is None      # seen once: not yet trusted
    assert counter.update(10) == 10        # seen twice: believed


def test_a_one_frame_glitch_never_gets_through():
    counter = filters.StableCount(required_repeats=2)
    believed = feed(counter, [10, 10, 47, 10])
    assert believed == [None, 10, 10, 10]


def test_a_large_change_is_accepted_once_it_persists():
    """Regression: the count used to stick forever.

    An earlier version rejected any jump bigger than three as impossible. When
    a new game started the count went 22 -> 4, every reading was refused, and
    the overlay showed 22 villagers for the rest of the session. A briefly
    wrong value fixes itself next poll; a permanently stuck one does not.
    """
    counter = filters.StableCount(required_repeats=2)
    believed = feed(counter, [22, 22, 4, 4])
    assert believed[-1] == 4


def test_the_count_is_allowed_to_fall():
    """Villagers really do die - a boar, or an early rush. A rule of "can only
    go up" would ignore that forever."""
    counter = filters.StableCount(required_repeats=2)
    assert feed(counter, [20, 20, 19, 19])[-1] == 19


def test_an_unreadable_frame_keeps_the_last_good_value():
    counter = filters.StableCount(required_repeats=2)
    feed(counter, [12, 12])
    assert counter.update(None) == 12


# ---- game clock --------------------------------------------------------

def test_the_clock_moves_forward_freely():
    clock = filters.StableClock()
    assert feed(clock, [100, 101, 102])[-1] == 102


def test_a_single_absurd_clock_reading_is_ignored():
    clock = filters.StableClock()
    believed = feed(clock, [100, 101, 102, 9999, 103])
    assert believed[3] == 102      # the absurd value never took hold
    assert believed[4] == 103


def test_a_new_game_resets_the_clock_once_confirmed():
    """A new match makes the clock jump backwards, which looks exactly like a
    misread on a single frame - so it takes a second reading to be believed."""
    clock = filters.StableClock()
    believed = feed(clock, [600, 601, 3, 4])
    assert believed[2] == 601      # not believed on first sight
    assert believed[3] == 4        # confirmed by the next reading


def test_the_clock_filter_does_not_assume_how_often_it_is_polled():
    """Regression: confirmation used to require two readings within 2 seconds.

    That held at 0.3s polling, where the clock moves about half a second per
    poll. Replaying frames captured 3 seconds apart - about 5 game-seconds at
    1.7x speed - no two readings were ever close enough, so the clock filter
    accepted nothing at all and the time never appeared.
    """
    slow = filters.StableClock()
    assert feed(slow, [5, 10, 15, 20])[-1] == 20

    fast = filters.StableClock()
    assert feed(fast, [5.0, 5.5, 6.0, 6.5])[-1] == 6.5


# ---- formatting --------------------------------------------------------

def test_time_formatting():
    assert filters.format_time(0) == "00:00"
    assert filters.format_time(75) == "01:15"
    assert filters.format_time(605) == "10:05"
    assert filters.format_time(None) == "--:--"
