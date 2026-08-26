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


def test_a_few_seconds_of_a_long_game_is_not_worth_a_file():
    """The bug that filled the author's stats folder with about a hundred
    fragments, and a silent one - every restart simply wrote another file.

    An overlay started at 44:00 and stopped seconds later has a DURATION of
    2645, because the clock really did say that. It has watched five
    seconds. has_data asked the duration, so every fragment passed.
    """
    recorder = gamestats.GameRecorder("fast_castle", "Fast Castle", "now")
    for moment in range(2640, 2646):
        recorder.observe(moment, 120, 0)

    assert recorder.duration() >= 2645, "the clock did reach that"
    assert recorder.observed() < gamestats.MIN_DURATION
    assert not recorder.has_data(), "wrote a file for five seconds of game"


def test_a_real_game_is_still_worth_a_file():
    recorder = gamestats.GameRecorder("fast_castle", "Fast Castle", "now")
    for moment in range(0, 400):
        recorder.observe(moment, 3 + moment // 25, 0)

    assert recorder.has_data()
    assert recorder.observed() >= gamestats.MIN_DURATION


def test_the_watched_span_travels_in_the_file():
    """Anything per-second has to divide by the span watched, not by the
    clock - statsview's idle percentage did, and a fragment of a long game
    divided by the whole game."""
    recorder = gamestats.GameRecorder("fast_castle", "Fast Castle", "now")
    for moment in range(1800, 2000):
        recorder.observe(moment, 90, 0)

    written = recorder.to_dict()["game"]
    assert written["duration"] == 1999
    assert written["observed"] == 199

class FakeProfile:
    name = "annehk"


def a_found_hud(**over):
    """What HudReader.hud looks like once the anchor has landed."""
    found = {"profile": FakeProfile(), "scale": 0.68, "score": 0.913,
             "frame": (1920, 1080), "backdrop": 84.0}
    found.update(over)
    return found


def test_a_stats_file_says_what_it_was_read_from():
    """Twice now, working out which skin and resolution wrote a bad file
    has meant dating git commits. The file should just say."""
    import json
    meta = gamestats.hud_meta(a_found_hud())
    flat = json.dumps(meta)          # it has to survive being written out
    assert "annehk" in flat, "the skin is not in there"
    assert "1920" in flat and "1080" in flat, "the resolution is not in there"
    assert "0.913" in flat or "0.91" in flat, "the anchor score is not in there"
    assert "84" in flat, "the backdrop luminance is not in there"


def test_the_hud_scale_is_never_recorded_as_a_percentage():
    """reader.py carries the scar: found["scale"] is the HUD's size against
    a 2560x1440 reference, NOT the in-game slider. A 1080p screen with the
    slider at 100% measures 0.68, and Loom once printed that as "~68% -
    Loom reads best at 100%" to players who were already at 100%.

    Writing it into a file a human reads months later is the same trap with
    a longer fuse, so whatever it is called, it must not call itself a
    percentage."""
    meta = gamestats.hud_meta(a_found_hud(scale=0.68))
    for key in meta:
        assert "percent" not in key.lower() and "pct" not in key.lower(), key
    assert 68 not in meta.values(), "0.68 was written out as 68"


def test_a_hud_is_described_once_and_the_first_answer_stands():
    """Re-acquiring the anchor mid-game must not relabel readings that
    were taken under the first one."""
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    recorder.describe_hud(a_found_hud())
    recorder.describe_hud(a_found_hud(scale=1.0, score=0.5))
    assert "0.913" in json.dumps(recorder.meta["hud"]) or         "0.91" in json.dumps(recorder.meta["hud"]), "the second call won"


def test_no_hud_means_no_claim_about_one():
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    recorder.describe_hud(None)
    assert "hud" not in recorder.meta


def test_a_stats_file_records_which_BUILD_wrote_it(monkeypatch):
    """meta.loom cannot answer this and looks as though it can. The version
    moves on releases; readers change between them, so every file written
    on the day three notification fixes landed says 1.0.5 on BOTH sides of
    them, and a corpus grouped by version silently mixes generations.

    The rejected alternative was bumping the version by hand on material
    reader changes: it makes a machine-checkable fact depend on discipline,
    fails silently when someone forgets, and overloads a number the
    launcher shows to players."""
    from loom import paths
    monkeypatch.setattr(paths, "build_commit", lambda: "abc1234")
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    assert recorder.meta["commit"] == "abc1234"


def test_a_build_that_cannot_say_which_commit_it_is_says_nothing(monkeypatch):
    """Absence is a real answer - "written before this was recorded" beats
    a guess, and it is the same rule as unplaceable_buckets: the key is
    there only when it means something."""
    from loom import paths
    monkeypatch.setattr(paths, "build_commit", lambda: None)
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    assert "commit" not in recorder.meta


def test_army_losses_are_the_population_that_was_not_villagers():
    """The author's correction, and he is right: generic losses ARE
    readable even though the unit type is not. Population minus villagers,
    falling, is every non-villager leaving the population."""
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    for moment, vills, pop in ((100, 20, 30), (105, 20, 30), (110, 20, 24)):
        recorder.observe(moment, vills, 0, population=(pop, 50))
    assert recorder.army_losses == [(110, 6, False)]
    assert recorder.max_army == 10


def test_a_dead_villager_is_not_also_an_army_loss():
    """A villager dying moves BOTH numbers, so the difference is unchanged
    and cannot be counted twice."""
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    for moment, vills, pop in ((100, 20, 30), (110, 17, 27)):
        recorder.observe(moment, vills, 0, population=(pop, 50))
    assert recorder.army_losses == []
    assert [d[1] for d in recorder.deaths] == [3]


def test_an_unread_population_is_not_an_army_of_zero():
    """Both halves must have been read or the difference means nothing -
    a subtraction with one side missing is not a small army."""
    recorder = gamestats.GameRecorder("t", "T", "2026-08-25T12:00:00")
    recorder.observe(100, 20, 0, population=(30, 50))
    recorder.observe(105, 20, 0, population=None)
    recorder.observe(110, 20, 0, population=(30, 50))
    assert recorder.army_losses == [], "an unread poll was read as a wipe"

