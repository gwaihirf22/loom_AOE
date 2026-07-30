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


def test_meter_retires_when_the_build_is_complete(tracker):
    # Still one villager short at 5:40. The 14th villager interpolates to
    # a 4:35 target, so arriving at 5:40 reads 65 seconds behind.
    assert tracker.update(14, 340) == 65
    assert not tracker.complete
    # The 15th villager arrives: the build is done, however late it ran.
    # Silence, for the rest of the game.
    assert tracker.update(15, 345) is None
    assert tracker.complete
    assert tracker.update(20, 600) is None


def test_villager_deaths_do_not_revive_a_finished_build(tracker):
    tracker.update(16, 400)
    assert tracker.complete
    # A raid drops the count below the final step's target. The build was
    # done; it stays done.
    assert tracker.update(11, 650) is None
    assert tracker.complete


# A build with the classic age-up shape: the count HOLDS at 12 across a
# time-gated stretch (the TC cannot train while the age researches), which
# is exactly where an accidental 13th villager slips in.
GATED = {
    "name": "Gated Build",
    "build_order": [
        {"villager_count": 6, "time": "1:15",
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
         "notes": ["Six to Sheep"]},
        {"villager_count": 12, "time": "3:45",
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Click Feudal Age"]},
        {"villager_count": 12, "time": "5:00",
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["In Feudal Age"]},
        {"villager_count": 15, "time": "6:00",
         "resources": {"food": 11, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Three to Farms"]},
    ],
}


def test_extra_villager_does_not_flip_the_meter_ahead():
    """The accidental pre-age-up villager, live-reported: scoring it against
    an interpolated target used to read AHEAD while the click slid late."""
    tracker = PaceTracker(BuildOrder(GATED))
    assert tracker.update(12, 225) == pytest.approx(0)   # on the build
    # The 13th villager arrives inside the time-gated stretch: no credit,
    # and certainly not the -70s "ahead" the interpolation used to award.
    assert tracker.update(13, 250) == pytest.approx(0)
    # And once the next real checkpoint (15 vills by 6:00) goes overdue,
    # the meter climbs - stuck at 13, twenty seconds past it:
    assert tracker.update(13, 380) == pytest.approx(20)


def test_extra_villager_detector():
    from loom.build_order import extra_villagers
    build = BuildOrder(GATED)
    assert extra_villagers(build, 12, 250) == 0      # exactly on the build
    assert extra_villagers(build, 13, 250) == 1      # trained into the hold
    assert extra_villagers(build, 40, 9999) == 0     # build finished
    assert extra_villagers(build, None, 250) == 0


def test_slightly_ahead_is_not_extra():
    """The live false positive: "+1 VILL" on every slightly-ahead build.

    Villager 11 popping before the 10-villager checkpoint's timestamp is
    AHEAD, not overproduction - the counts around it differ, so the build
    has not said "stop training". Only a hold (repeated count) counts.
    """
    from loom.build_order import extra_villagers
    build = BuildOrder(SAMPLE)                # 6@1:15, 10@2:55, 12@3:45...
    assert extra_villagers(build, 11, 160) == 0


def test_ahead_arrivals_still_score_as_ahead():
    tracker = PaceTracker(BuildOrder(SAMPLE))
    tracker.update(10, 130)                   # running ahead already
    # Villager 11 arrives 40s early. The meter reports AHEAD - capped by
    # its max() rule at how far ahead the next unfinished instruction
    # allows (-15s here) - and crucially is NOT frozen by the hold-clamp,
    # which must only bite on repeated-count holds.
    assert tracker.update(11, 160) == pytest.approx(-15)


def test_reset_clears_completion(tracker):
    tracker.update(16, 400)
    tracker.reset()
    assert not tracker.complete
    # A fresh game reports pace again: the 6th villager due at 1:15
    # arriving at 1:40 reads 25 seconds behind.
    assert tracker.update(6, 100) == 25


def test_following_the_build_exactly_reads_zero(tracker):
    for villagers, moment in [(6, 75), (10, 175), (12, 225)]:
        assert tracker.update(villagers, moment) == pytest.approx(0)
    # The final villager arriving IS the build completing, so that reading
    # retires the meter rather than saying "on pace" one last time.
    assert tracker.update(15, 300) is None
    assert tracker.complete


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
