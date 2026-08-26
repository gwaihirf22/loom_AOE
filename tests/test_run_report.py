"""Comparing a whole Loom game against the game's own record.

Every other gate scores ONE crop of pixels and can pass on every frame
while the count for the whole game is wrong. This is the axis that catches
that, and its own correctness matters more than most, because a gate that
is wrong about a reader is worse than no gate at all.

The two directions are NOT symmetrical, and these tests exist mostly to
keep them apart:

  * Loom OVER the record is a fault. The record counts placement ORDERS,
    which is a ceiling - a cancelled or destroyed foundation announces
    nothing - so Loom cannot honestly exceed it.
  * Loom UNDER the record usually is not. The feed showed
    `--Villager Created--` at most 57 times in a game with 112 villagers,
    because a line lingers and the next replaces it.
"""

import json

from tools import run_report


def a_stats_file(tmp_path, events, duration=3038):
    path = tmp_path / "game.json"
    path.write_text(json.dumps(
        {"game": {"duration": duration, "events": events}}), encoding="utf-8")
    return path


def test_over_the_record_is_a_fault():
    result = {"rows": [("barracks", 15, 3)], "repeated_techs": {},
              "loom_duration": 3038, "replay_duration": 3038,
              "unnamed_ids": [], "not_ruled_on": []}
    problems = run_report.faults(result)
    assert len(problems) == 1
    assert "15" in problems[0] and "3" in problems[0]


def test_under_the_record_is_not_a_fault():
    # The feed's own ceiling, not the reader's. Failing a run for this would
    # make the gate demand something the game cannot do.
    result = {"rows": [("house", 12, 20)], "repeated_techs": {},
              "loom_duration": 3038, "replay_duration": 3038,
              "unnamed_ids": [], "not_ruled_on": []}
    assert run_report.faults(result) == []


def test_a_clock_that_disagrees_with_the_record_fails_before_any_count():
    # Both clocks are the GAME's, so a real match agrees exactly. When they
    # do not, comparing counts is comparing two different games.
    result = {"rows": [], "repeated_techs": {},
              "loom_duration": 3345, "replay_duration": 2160,
              "unnamed_ids": [], "not_ruled_on": []}
    problems = run_report.faults(result)
    assert problems and "apart" in problems[0]


def test_a_technology_claimed_twice_is_a_fault_with_no_record_at_all():
    """Arithmetic on Loom's own output, needing no ground truth.

    A research can be cancelled at any point before it completes, but a
    cancelled one is never announced and there is nothing to research a
    second time afterwards. Live files claimed hand_cart five times.
    """
    reported = {("researched", "hand_cart"): 5,
                ("researched", "loom"): 1,
                ("built", "house"): 12}
    assert run_report.repeated_technologies(reported) == {"hand_cart": 5}


def test_a_building_built_twice_is_not_a_technology(tmp_path):
    # Buildings really can be built many times; only technologies cannot.
    reported = {("built", "house"): 12}
    assert run_report.repeated_technologies(reported) == {}


def test_loom_counts_reads_the_event_pairs(tmp_path):
    path = a_stats_file(tmp_path, [[30, "built:house"], [55, "created:villager"],
                                   [60, "built:house"], [70, "nonsense"]])
    counts, duration = run_report.loom_counts(path)
    assert counts[("built", "house")] == 2
    assert counts[("created", "villager")] == 1
    assert duration == 3038
    # A malformed entry is skipped rather than crashing the gate.
    assert all(":" not in subject for _kind, subject in counts)
