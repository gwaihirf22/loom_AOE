"""
Loom — tests for the per-game statistics recorder.

The recorder's job is to keep watching after BuildReport closes its book,
and to write one honest file per game. What earns pinning: the timeline
records one row per game-second (not per poll), the accumulators keep
running past build completion, alerts are transitions rather than the poll
rate, and the file survives a round trip.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

from loom import gamestats, queue
from loom.gamestats import GameRecorder


class FakeTracker:
    """Just the fields observe() reads off a ProductionTracker."""

    def __init__(self, idle_tcs=0, blocked=None, tcs_seen=1):
        self.idle_tcs = idle_tcs
        self.blocked = blocked
        self.tcs_seen = tcs_seen


def slot(identity):
    return queue.SlotReading(0, "green", 0.5, None, identity, 0.9)


def recorder():
    return GameRecorder("test_build", "Test Build", "2026-07-25T12:00:00")


def test_timeline_is_one_row_per_game_second():
    r = recorder()
    # Three polls inside the same second, then one in the next.
    r.observe(10.1, 5, 0)
    r.observe(10.4, 5, 0)
    r.observe(10.7, 5, 0)
    r.observe(11.2, 6, 0)
    assert r.t == [10, 11]
    assert r.villagers == [5, 6]


def test_accumulators_keep_running_after_build_completion():
    # The whole point of the recorder: BuildReport stops at complete();
    # the game statistics must not.
    r = recorder()
    r.observe(100, 20, 0, FakeTracker(idle_tcs=1))
    r.observe(110, 20, 0, FakeTracker(idle_tcs=1))
    r.snapshot_build(_completed_report(), _tiny_build())
    r.observe(120, 20, None, FakeTracker(idle_tcs=1))
    assert r.tc_idle_seconds == 20     # 100->110 and 110->120, both idle
    assert r.build_section is not None


def test_housed_seconds_accumulate():
    # Elapsed time is attributed to the state at the CURRENT poll, the same
    # convention as the TC idle integral: 100->110 arrives housed (+10),
    # 110->120 arrives unblocked (+0).
    r = recorder()
    r.observe(100, 20, 0, FakeTracker(blocked="housed"))
    r.observe(110, 20, 0, FakeTracker(blocked="housed"))
    r.observe(120, 20, 0, FakeTracker())
    assert r.housed_seconds == 10


def test_deaths_recorded_with_raid_attribution():
    r = recorder()
    r.observe(100, 20, 0)
    r.observe(105, 20, 0, game_events=["attacked"])
    r.observe(110, 18, 0)
    assert r.deaths == [(110, 2, True)]
    # A boar lure is not a raid.
    r.observe(200, 18, 0, game_events=["attacked", "wild_animals"])
    r.observe(210, 17, 0)
    assert r.deaths[-1] == (210, 1, False)


def test_queue_identities_first_seen_only_and_no_villagers():
    r = recorder()
    r.observe(100, 20, 0, slots=[slot("villager_male"), slot("knight")])
    r.observe(200, 20, 0, slots=[slot("knight"), slot("loom")])
    assert r.queued == {"knight": 100, "loom": 200}


def test_alerts_recorded_on_transition_not_per_poll():
    r = recorder()
    for t in (100, 101, 102):
        r.observe(t, 20, 0, alerts_list=[(f"TC IDLE — {t-100}s", "full")])
    r.observe(103, 20, 0, alerts_list=[])
    r.observe(104, 20, 0, alerts_list=[("TC IDLE", "full")])
    # One transition for the first spell (duration suffix stripped), one
    # for the reappearance after it cleared.
    assert r.alerts == [(100, "TC IDLE", "full"), (104, "TC IDLE", "full")]


def test_absurd_time_jumps_do_not_become_idleness():
    r = recorder()
    r.observe(100, 20, 0, FakeTracker(idle_tcs=2))
    r.observe(700, 20, 0, FakeTracker(idle_tcs=2))   # misread or new game
    assert r.tc_idle_seconds == 0


def test_short_games_are_not_worth_a_file():
    r = recorder()
    r.observe(10, 4, None)
    r.observe(50, 5, None)
    assert not r.has_data()
    r.observe(70, 6, None)
    assert r.has_data()


def test_round_trip_through_disk(tmp_path):
    r = recorder()
    for t in range(100, 200, 5):
        r.observe(t, t // 10, -3, FakeTracker(), population=(20, 25))
    r.snapshot_build(_completed_report(), _tiny_build())
    path = tmp_path / "sub" / "game.json"
    r.write(path)

    loaded = json.loads(path.read_text())
    assert loaded["schema"] == gamestats.SCHEMA
    assert loaded["meta"]["build"] == "test_build"
    assert loaded["game"]["duration"] == 195
    assert loaded["game"]["max_villagers"] == 19
    assert loaded["build"]["rows"]
    assert loaded["timeline"]["t"][0] == 100
    assert loaded["apm"] is None


def _tiny_build():
    from loom.build_order import BuildOrder
    return BuildOrder({"name": "x", "build_order": [
        {"villager_count": 6, "time": "1:00",
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
         "notes": ["Six to Sheep"]},
    ]})


def _completed_report():
    from loom.report import BuildReport
    report = BuildReport()
    report.update(100, FakeTracker(), -5, [], [], villagers=20)
    report.complete(110)
    return report
