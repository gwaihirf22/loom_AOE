"""
Tests for the game session tracker.

These are the four things that actually happen while playing, and the one that
matters most is telling a new game apart from alt-tabbing back into the old
one: getting that wrong would restart the build order under the player.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import session


@pytest.fixture
def tracker():
    # Three unreadable polls is enough to count as lost, so the tests stay short.
    return session.GameSession(polls_before_lost=3)


def feed(tracker, polls, per_poll=1.0):
    """Push (game_time, villagers) pairs in, and return the events that fired.

    Wall time advances `per_poll` seconds a poll, because a new match is
    only believed once a lower clock has HELD for a few wall seconds - a
    misread bounces back within one. Supplying it here rather than in
    every test keeps the tests describing game states rather than
    stopwatches.
    """
    events = []
    wall = 0.0
    for game_time, villagers in polls:
        event = tracker.update(game_time, villagers, now=wall)
        wall += per_poll
        if event is not None:
            events.append(event)
    return events


def test_starting_loom_at_the_beginning_of_a_game(tracker):
    assert feed(tracker, [(3, 3), (8, 3)]) == [session.GAME_STARTED]


def test_starting_loom_midway_through_a_game(tracker):
    """Joining a game in progress must not restart the build order."""
    assert feed(tracker, [(600, 22), (605, 22)]) == [session.GAME_RESUMED]


def test_a_brief_pause_is_not_reported(tracker):
    """Opening the in-game menu dims the HUD. Under the threshold, say nothing."""
    events = feed(tracker, [(600, 22), (None, None), (None, None), (612, 22)])
    assert events == [session.GAME_RESUMED]     # only the initial one


def test_a_long_pause_is_reported_but_does_not_restart_the_build(tracker):
    events = feed(tracker, [(600, 22),
                            (None, None), (None, None), (None, None),
                            (612, 22)])
    assert events == [session.GAME_RESUMED,
                      session.TRACKING_LOST,
                      session.GAME_RESUMED]


def test_quitting_and_starting_a_new_game(tracker):
    """The clock going backwards across the gap is what identifies a new match.

    A threshold like "is the clock near zero?" would fail whenever loading took
    long enough that the HUD first appeared at 0:30.
    """
    events = feed(tracker, [(600, 22),
                            (None, None), (None, None), (None, None),
                            (5, 3)])
    assert events[-1] == session.GAME_STARTED


def test_a_restart_with_no_gap_is_still_noticed(tracker):
    """If the HUD never vanishes for long enough, the backwards clock alone
    still gives it away."""
    # The new match counting up from its own start, which is what a real
    # restart looks like and what a misread never does.
    events = feed(tracker, [(600, 22), (605, 22),
                            (4, 3), (5, 3), (6, 3), (7, 3)])
    assert events[-1] == session.GAME_STARTED


def test_is_in_game_reflects_the_state(tracker):
    assert not tracker.is_in_game()
    tracker.update(100, 8)
    assert tracker.is_in_game()
    for _ in range(3):
        tracker.update(None, None)
    assert not tracker.is_in_game()


def test_a_wobbling_clock_is_not_a_new_game():
    """The fault behind seventy-five stats files for one ten-minute match.

    Any backwards step at all used to mean a new game, so a single
    misread wrote a permanent file. The reader is good enough now that it
    rarely wobbles - which makes this guard cheap, not unnecessary.
    """
    watcher = session.GameSession()
    assert watcher.update(100, 10) is not None      # first sighting
    for wobble in (99, 98, 96, 100):
        assert watcher.update(wobble, 10) is None, (
            f"a step back to {wobble} was read as a new match")


def test_a_real_seam_is_still_a_new_game():
    """The other half. Measured over 252 recorded games, a real restart
    jumps back hundreds of seconds - 529 to 7 - so the tolerance has room
    to spare and must not swallow one."""
    watcher = session.GameSession()
    watcher.update(529, 30, now=0)
    # Held for the same reason: one low reading is a misread until it
    # keeps being low. The villagers fell too, so nothing else objects.
    assert watcher.update(7, 4, now=1) is None
    assert watcher.update(8, 4, now=5) == session.GAME_STARTED


def test_the_tolerance_is_the_one_events_measured():
    """Two modules asking the same question must not carry two numbers.

    events.py measured it; session.py had none at all, which is the same
    fault at its limit. Pinned against the source rather than a literal.
    """
    from loom import events
    assert session.SEAM_TOLERANCE_SECONDS is events.SEAM_TOLERANCE_SECONDS


# ---- issue #14: a misread clock is not a new match ----------------------

def test_a_clock_stuck_low_never_becomes_a_new_match():
    """The worst case in the reported log, and the one a hold cannot catch.

    His clock read 03:59, then 01:00, and stayed at 01:00 for 96 seconds
    while the game ran on to 05:40. Persistence says new match - it held
    for a minute and a half, and a real one would too. What says
    otherwise is the OTHER witness: eleven villagers on the board, which
    no match has at one minute old.
    """
    watcher = session.GameSession()
    watcher.update(239, 11, now=0)                  # 03:59
    for tick in range(1, 100):
        assert watcher.update(60, 11, now=tick) is None, (
            f"a clock stuck at 01:00 became a new match after {tick}s")


def test_villagers_merely_CHANGING_is_not_a_fresh_match():
    """The first version of the villager test asked whether the count had
    MOVED, and that is the weaker question. Two villagers lost to a raid
    during his stuck clock satisfied it, and a new match was declared
    mid-game. Nine is no more a fresh start than eleven."""
    watcher = session.GameSession()
    watcher.update(239, 11, now=0)
    watcher.update(60, 11, now=1)
    for tick in range(2, 40):
        assert watcher.update(60, 9, now=tick) is None


def test_a_pending_restart_is_not_wedged_by_a_lagging_villager_count():
    """The guard must never STICK, which is filters.py's oldest rule.

    If the villager reader is a poll or two behind a real restart, the
    candidate stays pending rather than being thrown away - so the match
    lands the moment the count catches up.
    """
    watcher = session.GameSession()
    watcher.update(600, 22, now=0)
    for tick in range(1, 6):                        # clock reset, count stale
        assert watcher.update(5 + tick, 22, now=tick) is None
    assert watcher.update(12, 3, now=7) == session.GAME_STARTED


def test_the_reported_session_restarts_once(tmp_path):
    """The regression test this issue actually deserves.

    784 clock readings from the reporter's own log restarted his build
    order 45 times. Replayed here they must restart it once - at the
    start of his match, where a match really did begin.

    Written as the SHAPE of his log rather than the file, which is not
    mine to commit: a clock climbing normally, punctuated by the two
    misreads that dominated it (8 read as 0, 3 read as 1) and by the
    96-second stretch stuck at 01:00.
    """
    watcher = session.GameSession()
    events = []
    wall = 0.0
    villagers = 3

    def poll(clock, vills):
        nonlocal wall
        got = watcher.update(clock, vills, now=wall)
        wall += 0.35                                # his rate, about 3/s
        if got is not None:
            events.append(got)

    poll(2, 3)
    for second in range(3, 240):
        villagers = min(11, 3 + second // 25)
        poll(second, villagers)
        if second % 10 == 3:
            poll(second - 2, villagers)             # 3 misread as 1
        if second % 10 == 8:
            poll(second - 8, villagers)             # 8 misread as 0
    for _ in range(280):                            # 96s stuck at 01:00
        poll(60, 11)

    assert events == [session.GAME_STARTED], events
