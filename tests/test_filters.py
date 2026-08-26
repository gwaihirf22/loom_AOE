"""
Tests for the read filters.

Both bugs this code has ever had were silent and severe: the reading looked
plausible while being wrong for the rest of the game. So most of these tests
exist to pin down a specific failure that actually happened, and each one says
which.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

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


# ---- the read gap ------------------------------------------------------

def test_fresh_reads_never_announce_a_gap():
    gap = filters.ReadGap(announce_after=10, misses_before=3)
    assert gap.update(6, 100) is None
    assert gap.update(6, 102) is None
    assert gap.update(7, 104) is None


def test_a_band_gone_quiet_while_the_clock_runs_is_announced():
    """Regression: the frozen villager count.

    Live, the villager band stopped producing reads while the clock kept
    going; the count filter held its belief of 6 - as designed - and the
    panel wore that held number as if it were read, for the rest of the
    game. The gap is what lets the panel admit it is holding."""
    gap = filters.ReadGap(announce_after=10, misses_before=3)
    gap.update(6, 100)
    assert gap.update(None, 104) is None          # too soon to accuse
    assert gap.update(None, 108) is None
    assert gap.update(None, 112) == 12            # long enough, misses enough
    assert gap.update(None, 120) == 20            # and it keeps counting


def test_one_good_read_clears_the_gap():
    gap = filters.ReadGap(announce_after=10, misses_before=3)
    gap.update(6, 100)
    for t in (104, 108, 112):
        gap.update(None, t)
    assert gap.update(7, 116) is None             # read again: fresh
    assert gap.update(None, 118) is None          # and the count restarts


def test_a_menu_does_not_accuse_the_band():
    """With the clock gone too, the whole HUD is gone - a menu, not a band
    failure. Those polls say nothing, and must not spend the miss guard."""
    gap = filters.ReadGap(announce_after=10, misses_before=3)
    gap.update(6, 100)
    for _ in range(30):                            # a long menu
        assert gap.update(None, None) is None
    # Back from the menu in multiplayer: the clock leaps forward. A single
    # missed read must not flash an accusation the band never earned.
    assert gap.update(None, 220) is None
    assert gap.update(None, 222) is None
    assert gap.update(None, 224) == 124            # but persistent misses do


def test_a_new_game_is_not_charged_the_old_games_gap():
    gap = filters.ReadGap(announce_after=10, misses_before=3)
    gap.update(30, 900)
    assert gap.update(None, 5) is None             # clock went backwards
    for t in (7, 9, 11, 13, 15, 17):
        assert gap.update(None, t) is None         # no old moment to count from

def test_a_clock_watched_the_whole_way_cannot_have_leapt():
    """Measured, live, twice: 32:07 -> 52:07 and 35:41 -> 55:42, both
    exactly twenty minutes, both the tens-of-minutes digit read as 5
    instead of 3. The recorded game says the second was 36:00, not 55:45.

    The old confirmation rule could not refuse them. It asked for a
    second reading to agree - but a digit misread is not a flicker, it
    reads the same next frame, so 55:42 -> 55:43 "moved forward
    sensibly" and confirmed itself. The confirming witness was the
    reader under suspicion.

    What Loom actually knows is stronger: it never lost sight of the
    clock. Between two consecutive readings the game cannot have run
    twenty minutes, so the leap is refused outright rather than put to a
    vote its accuser gets to cast.
    """
    clock = filters.StableClock()
    for reading in (2140, 2141, 2142):
        clock.update(reading)
    assert clock.value == 2142, "the clock was watched continuously to here"

    # the misread, and it repeats exactly as the real one did
    assert clock.update(3342) == 2142, "a 20-minute leap was believed"
    assert clock.update(3343) == 2142, "the misread confirmed itself"

    # and the real clock is still coming in underneath it. A refusal that
    # left the filter stuck would be the worse bug of the two.
    assert clock.update(2143) == 2143, "refusing a leap froze the clock"


def test_a_leap_is_believed_once_the_clock_was_actually_lost():
    """The refusal must not become a stuck filter - the rule Loom has
    already been bitten by. Loom looking away IS how a clock legitimately
    jumps: a menu, an alt-tab, a load. Absence has to be observed, so it
    is the unreadable polls that license the leap, never elapsed time."""
    clock = filters.StableClock()
    for reading in (600, 601, 602):
        clock.update(reading)

    clock.update(None)                      # the HUD went away
    clock.update(None)
    assert clock.update(1800) == 602, "one reading is still not enough"
    assert clock.update(1801) == 1801, "the filter never came back"
