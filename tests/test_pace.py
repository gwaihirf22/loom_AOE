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
    # Zero because there is no AGE READING, not because the build is over.
    # The comment here used to say "build finished", which was the right
    # intent asserted by the wrong mechanism: the age ceiling is the rule
    # that keeps counting, it is disabled without an age, and live always
    # has one. So this line passed happily while the overlay header climbed
    # to +55 on a build that ended at +4. See the next test.
    assert extra_villagers(build, 40, 9999) == 0
    assert extra_villagers(build, None, 250) == 0


def test_the_surplus_keeps_counting_after_the_build_ends():
    """Deliberately NOT a bug in this function, and worth stating plainly.

    extra_villagers is pure and knows nothing about a build being over. Its
    age-ceiling rule compares the count against the largest ask in any age
    at or below the one on the crest, so once the player is in the build's
    final age that is the whole build and the answer is "villagers minus
    the build's maximum" - which climbs for the rest of the game.

    That is a correct answer to the question this function is asked. Making
    it stop here would need it to learn what completion is, which belongs
    to the caller. loom_overlay takes its displayed figure from
    report.max_extra once pace.complete latches; this test exists so that
    anyone reading the +55 in a bug report finds the mechanism rather than
    concluding this function is broken.
    """
    from loom.build_order import BuildOrder as Build, extra_villagers
    build = Build.load_by_name("scoutsrush18pop")
    top = max(step.villager_count for step in build.steps)
    final_age = max(step.age for step in build.steps if step.age)

    assert extra_villagers(build, top, 9999, age=final_age) == 0
    assert extra_villagers(build, top + 20, 9999, age=final_age) == 20
    assert extra_villagers(build, top + 55, 9999, age=final_age) == 55


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


def test_the_build_cannot_complete_before_its_last_age_is_reached():
    """The measured live failure: an extra villager exhausted the villager
    constraint, the build's ideal times ran out, and the meter declared
    the build complete - showing the report card - while the last step's
    age was still ninety seconds of research away. The crest is a ceiling
    on the step, so completion now waits for the age to actually arrive.
    """
    aged = {
        "name": "Aged Build",
        "build_order": [
            {"villager_count": 6, "age": 1, "time": "1:15",
             "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
             "notes": ["Six to Sheep"]},
            {"villager_count": 10, "age": 1, "time": "2:55",
             "resources": {"food": 6, "wood": 4, "gold": 0, "stone": 0},
             "notes": ["Click Feudal Age"]},
            {"villager_count": 10, "age": 2, "time": "4:00",
             "resources": {"food": 6, "wood": 4, "gold": 0, "stone": 0},
             "notes": ["In Feudal Age: build a Market"]},
        ],
    }
    tracker = PaceTracker(BuildOrder(aged))
    # Villagers and clock past everything, crest still Dark Age: the last
    # step is not reached, so the build is not complete and the meter
    # keeps reporting how overdue the age-up is.
    assert tracker.update(11, 400, age=1) is not None
    assert not tracker.complete
    # Feudal arrives: the final step is reached and the meter retires.
    tracker.update(11, 410, age=2)
    assert tracker.complete


def test_no_age_reading_completes_the_way_it_always_did():
    """Every caller that passes no age keeps the old behaviour - the demo
    front end has no crest to read."""
    tracker = PaceTracker(BuildOrder(SAMPLE))
    tracker.update(15, 345)
    assert tracker.complete


def test_player_time_delays_by_measured_lateness(tracker):
    # Villager 10 arrived thirty seconds late: the player's clock runs
    # thirty seconds behind the game's from then on.
    tracker.update(10, 205)
    assert tracker.player_time(300) == 270


def test_player_time_never_advances_for_an_ahead_player(tracker):
    # Twenty seconds ahead. The villager count already carries an ahead
    # player forward; the clock must not credit it twice - and ahead of a
    # fixed build usually means out of sequence, which delays the end
    # goal anyway (the author's ruling).
    tracker.update(10, 155)
    assert tracker._delta_on_arrival < 0
    assert tracker.player_time(300) == 300


def test_player_time_without_a_measurement_is_the_game_clock(tracker):
    assert tracker.player_time(300) == 300


def test_more_villagers_than_the_age_allows_is_extra():
    """The live false NEGATIVE, from a real scouts game: a player behind
    the clock, whose cursor had not reached the hold window yet, at 18
    villagers in a build whose Dark Age never asks past 17. The hold test
    saw active and completed steps with different counts and said 0 - but
    the steps asking for more are all gated behind an age the crest says
    has not arrived, so the surplus is real whatever the clock says."""
    from loom.build_order import BuildOrder, extra_villagers
    build = BuildOrder.load_by_name("scoutsrush18pop")
    # Behind the clock, before the hold window (step 7 is 17 vills at 6:15).
    assert extra_villagers(build, 18, 360, age=1) == 1
    assert extra_villagers(build, 19, 360, age=1) == 2
    # Feudal arrives: the build asks for 20 there, so 18 is growth, not
    # surplus.
    assert extra_villagers(build, 18, 560, age=2, clicked=2) == 0
    # Without an age reading the ceiling would be a guess: unchanged.
    assert extra_villagers(build, 18, 360) == 0
    # Exactly the age's ask is not surplus.
    assert extra_villagers(build, 17, 420, age=1) == 0


# ---- the law: lag moves no faster than the clock ------------------------

def test_pace_never_moves_faster_than_a_second_per_second(tracker):
    """The law, stated directly.

    Pace is the horizontal gap between two curves that both only move
    forward, so it cannot change faster than the clock in either
    direction. Stand still and the build's clock runs on at exactly one
    per second; play perfectly and the best available is to close the gap
    at one per second. Anything quicker is the measuring stick moving.
    """
    said, last = [], None
    for t in range(80, 400):
        # A villager count that lurches on purpose, so the underlying
        # measurement swings far harder than any player could.
        villagers = 6 + (t // 7) % 9
        out = tracker.update(villagers, t)
        if out is None:
            continue
        if last is not None:
            assert abs(out - last) <= 1 + 1e-9, (
                f"pace moved {out - last:+.1f} in one second at t={t}")
        last = out
        said.append(out)
    assert said, "the tracker never spoke at all"


def test_it_converges_rather_than_sticking(tracker):
    """The distinction that keeps this from being the filter that froze
    the villager count at 22 for a whole game.

    The bound is on the RATE, never on the value, so a steady
    measurement is always reached - the only question is how many
    seconds it takes.
    """
    tracker.update(12, 225)                 # on pace, nothing to converge from
    reached = None
    for t in range(226, 400):
        reached = tracker.update(12, t)     # no new villager: overdue climbs
    # The measurement at t=399 with 12 villagers is the overdue term, and
    # the report has walked all the way up to it rather than settling short.
    assert reached == pytest.approx(tracker._overdue(12, 399), abs=1e-6)


def test_a_poll_gap_earns_proportionally_more_room(tracker):
    """Load shedding must not turn this into a stuck meter.

    Five seconds of game time between polls permits five seconds of
    movement, because five seconds of the gap really did elapse.
    """
    # Both start well behind, then a villager arrives that puts the
    # measurement far below where the report sits. How far the report may
    # follow it down is exactly the elapsed game time.
    was = tracker.update(12, 310)
    close = tracker.update(14, 311)         # one second later
    far = PaceTracker(BuildOrder(SAMPLE))
    assert far.update(12, 310) == was
    wide = far.update(14, 330)              # twenty seconds later

    assert was - close == pytest.approx(1, abs=1e-6), "one second, one second"
    assert was - wide == pytest.approx(20, abs=1e-6), "twenty earns twenty"


def test_a_new_game_starts_free_rather_than_converging_out_of_the_last(tracker):
    """`reset` clears it, so the first reading of a match is adopted
    whole. Crawling out of the previous game's answer at one per second
    would be the last match's pace on this match's panel."""
    for t in range(226, 380):
        tracker.update(12, t)
    assert tracker.update(12, 380) > 50, "not far behind enough to matter"
    tracker.reset()
    assert tracker._shown is None and tracker._last_time is None
    # A fresh game, a villager arriving 60 seconds late: said at once.
    assert tracker.update(12, 285) == pytest.approx(60)


def test_the_clock_going_backwards_earns_no_room(tracker):
    """A backwards clock is a misread or a seam, and neither is elapsed
    time. Nothing may move on the strength of it."""
    tracker.update(12, 300)
    was = tracker._shown
    assert tracker.update(15, 250) == pytest.approx(was)


def test_the_cursor_is_not_run_through_the_pace_filter(tracker):
    """Two different questions, and the seam between them stays sharp.

    `player_time` asks WHERE the player is in the build; pace asks HOW
    LATE they are. It reads `_delta_on_arrival` directly and must go on
    doing so - its own docstring reasons carefully about why only that
    term may feed the cursor, and running it through this filter would
    quietly answer one question with the other's number.
    """
    tracker.update(12, 285)                 # 60 seconds late on arrival
    for t in range(286, 340):               # the report converges upward
        tracker.update(12, t)
    assert tracker.player_time(340) == pytest.approx(
        340 - tracker._delta_on_arrival)
