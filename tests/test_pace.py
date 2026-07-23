"""
Tests for the pace tracker.

The number this produces is the one thing on the overlay a player watches out
of the corner of their eye, so how it *behaves over time* matters more than
any single value. These tests are mostly about that behavior: what it does
while nothing is changing.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom.build_order import BuildOrder
from loom.pace import PaceTracker

# Villagers every 25 seconds, so the arithmetic in these tests is easy to
# follow: 6 at 1:15, 10 at 2:55, 12 at 3:45, 15 at 5:00.
SAMPLE = {
    "name": "Steady Build",
    "build_order": [
        {"villager_count": 6, "time": "1:15",
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
         "notes": ["Six to Sheep"]},
        {"villager_count": 10, "time": "2:55",
         "resources": {"food": 6, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Four to Wood"]},
        {"villager_count": 12, "time": "3:45",
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Two to Berries"]},
        {"villager_count": 15, "time": "5:00",
         "resources": {"food": 11, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Three to Farms"]},
    ],
}


@pytest.fixture
def tracker():
    return PaceTracker(BuildOrder(SAMPLE))


def test_nothing_to_report_before_the_first_checkpoint(tracker):
    assert tracker.update(3, 10) is None


def test_following_the_build_exactly_reads_zero(tracker):
    for villagers, moment in [(6, 75), (10, 175), (12, 225), (15, 300)]:
        assert tracker.update(villagers, moment) == pytest.approx(0)


def test_the_number_holds_still_between_villagers(tracker):
    """Regression: the pace used to creep upward every second.

    It was recomputed from scratch each poll. Villagers arrive in jumps while
    time runs continuously, so the answer sawtoothed forever and a player who
    was thirty seconds late watched it climb as if they were still losing
    ground. Measuring arrival events instead fixes it.
    """
    tracker.update(10, 175)                      # arrived exactly on time
    steady = [tracker.update(10, moment) for moment in (180, 190, 200, 210)]
    assert all(value == pytest.approx(0) for value in steady)


def test_being_behind_but_keeping_up_holds_a_constant_number(tracker):
    """Thirty seconds late, then producing at the build's own rate. The player
    is not falling further behind, so the number must not grow."""
    assert tracker.update(10, 205) == pytest.approx(30)   # 30s late
    assert tracker.update(10, 215) == pytest.approx(30)   # still 30s late
    assert tracker.update(11, 230) == pytest.approx(30)   # next one, also 30s
    assert tracker.update(12, 255) == pytest.approx(30)   # and the next


def test_an_idle_town_center_makes_the_number_climb(tracker):
    """The opposite case: production has stopped, so the player IS losing
    ground and the number should say so."""
    tracker.update(10, 175)
    stalled = [tracker.update(10, moment) for moment in (250, 300, 350)]
    assert stalled == sorted(stalled)          # only ever grows
    assert stalled[-1] > 100


def test_being_ahead_reads_as_negative(tracker):
    """Villager 10 arrived twenty seconds early."""
    assert tracker.update(10, 155) == pytest.approx(-20)


def test_reset_forgets_the_previous_game(tracker):
    tracker.update(10, 260)                    # a badly late game
    assert tracker.update(10, 260) > 0

    tracker.reset()
    assert tracker.update(6, 75) == pytest.approx(0)


def test_a_missing_reading_is_not_treated_as_a_number(tracker):
    tracker.update(10, 175)
    assert tracker.update(None, 200) is None
    assert tracker.update(10, None) is None
