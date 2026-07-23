"""
Tests for the game session tracker.

These are the four things that actually happen while playing, and the one that
matters most is telling a new game apart from alt-tabbing back into the old
one: getting that wrong would restart the build order under the player.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

import pytest

from loom import session


@pytest.fixture
def tracker():
    # Three unreadable polls is enough to count as lost, so the tests stay short.
    return session.GameSession(polls_before_lost=3)


def feed(tracker, polls):
    """Push (game_time, villagers) pairs in, and return the events that fired."""
    events = []
    for game_time, villagers in polls:
        event = tracker.update(game_time, villagers)
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
    events = feed(tracker, [(600, 22), (605, 22), (4, 3)])
    assert events[-1] == session.GAME_STARTED


def test_is_in_game_reflects_the_state(tracker):
    assert not tracker.is_in_game()
    tracker.update(100, 8)
    assert tracker.is_in_game()
    for _ in range(3):
        tracker.update(None, None)
    assert not tracker.is_in_game()
