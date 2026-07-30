"""
Loom — tests for the build-complete report.

Pure logic over fake polls, like the tracker tests: the report may only
ever state what was actually observed.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import queue
from loom.build_order import BuildOrder, milestone_targets
from loom.production import ProductionTracker
from loom.report import BuildReport

SAMPLE = {
    "name": "Report Build",
    "build_order": [
        {"villager_count": 6, "time": "1:00", "notes": ["Research Loom"],
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0}},
        {"villager_count": 10, "time": "3:00", "notes": ["Click Feudal Age"],
         "resources": {"food": 6, "wood": 4, "gold": 0, "stone": 0}},
        {"villager_count": 12, "time": "5:00", "notes": ["Two to Berries"],
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0}},
    ],
}


def build():
    return BuildOrder(SAMPLE)


def slot(identity, tint="green"):
    return queue.SlotReading(0, tint, None, None, identity, 0.5)


def idle_tracker(idle_tcs):
    tracker = ProductionTracker()
    tracker.idle_tcs = idle_tcs
    return tracker


def test_milestone_targets_found_by_words():
    targets = milestone_targets(build())
    assert targets == {"loom": 60, "feudal_age": 180}


def test_idle_time_integrates_only_sane_intervals():
    report = BuildReport()
    report.update(100, idle_tracker(1), None, [], [])
    report.update(103, idle_tracker(1), None, [], [])   # +3s
    report.update(106, idle_tracker(2), None, [], [])   # +6s (two TCs)
    report.update(20, idle_tracker(1), None, [], [])    # clock jumped back: 0
    report.update(500, idle_tracker(1), None, [], [])   # absurd gap: 0
    assert report.tc_idle_seconds == 9


def test_raids_counted_but_wild_animals_are_not():
    report = BuildReport()
    report.update(100, idle_tracker(0), None, [], ["attacked"])
    report.update(200, idle_tracker(0), None, [],
                  ["attacked", "wild_animals"])   # boar lure, not a raid
    assert report.attacks == [100]


def test_milestones_record_first_sighting_only():
    report = BuildReport()
    report.update(70, idle_tracker(0), None, [slot("loom")], [])
    report.update(75, idle_tracker(0), None, [slot("loom")], [])
    report.update(200, idle_tracker(0), None, [slot("feudal_age", None)], [])
    assert report.milestones == {"loom": 70, "feudal_age": 200}


def test_final_delta_is_the_last_reported_pace():
    report = BuildReport()
    report.update(100, idle_tracker(0), 10, [], [])
    report.update(200, idle_tracker(0), 42, [], [])
    report.update(210, idle_tracker(0), None, [], [])   # meter retired
    report.complete(210)
    assert report.final_delta == 42
    assert report.worst_delta == 42
    assert report.completed_at == 210


def test_summary_reports_only_what_was_seen():
    report = BuildReport()
    report.update(70, idle_tracker(0), 5, [slot("loom")], [])
    report.update(400, idle_tracker(0), 30, [], [])
    report.complete(400)
    rows = {label: (value, good) for label, value, good in
            report.summary(build())}
    assert "Build complete" in rows
    assert rows["loom"] == ("1:10  on time", True)      # 10s slip = tolerance
    assert "feudal age" not in rows                     # never observed
    assert rows["vs perfect build"] == ("30s behind", False)
    assert rows["TC idle time"] == ("0s", True)
    assert not any(label.startswith("attacked") for label in rows)


def test_nothing_counts_after_completion():
    # A raid at 16 minutes belongs to the rest of the game, not the build.
    report = BuildReport()
    report.update(100, idle_tracker(0), 5, [], [])
    report.complete(120)
    report.update(200, idle_tracker(2), 40, [slot("loom")], ["attacked"])
    assert report.attacks == []
    assert report.milestones == {}
    assert report.tc_idle_seconds == 0
    assert report.worst_delta == 5


def test_extra_villagers_tracked_and_reported():
    report = BuildReport()
    report.update(100, idle_tracker(0), None, [], [], extra=1)
    report.update(110, idle_tracker(0), None, [], [], extra=2)
    report.update(120, idle_tracker(0), None, [], [], extra=0)
    report.complete(120)
    rows = {label: (value, good) for label, value, good in
            report.summary(build())}
    assert rows["extra villagers"] == ("+2 beyond the build", False)


def test_confirmed_villager_drop_is_a_death():
    report = BuildReport()
    report.update(100, idle_tracker(0), None, [], [], villagers=10)
    report.update(110, idle_tracker(0), None, [], ["attacked"], villagers=10)
    report.update(120, idle_tracker(0), None, [], [], villagers=9)
    assert report.deaths == [(120, 1, True)]     # attributed to the raid
    # A later, unrelated drop is a death without attribution.
    report.update(300, idle_tracker(0), None, [], [], villagers=8)
    assert report.deaths[-1] == (300, 1, False)


def test_milestone_queued_matches_the_step():
    report = BuildReport()
    report.update(200, idle_tracker(0), None, [slot("feudal_age", None)], [])
    step = build().steps[1]                       # "Click Feudal Age"
    assert report.milestone_queued(step)
    assert not report.milestone_queued(build().steps[2])   # "Two to Berries"
    assert not report.milestone_queued(None)


def test_out_of_tolerance_milestone_reads_late_and_bad():
    report = BuildReport()
    report.update(150, idle_tracker(0), None, [slot("loom")], [])
    report.complete(400)
    rows = {label: (value, good) for label, value, good in
            report.summary(build())}
    assert rows["loom"] == ("2:30  90s late", False)