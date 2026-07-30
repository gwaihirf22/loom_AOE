"""
Loom — tests for the statistics window's pure parts.

The window itself is hand-tested like the rest of the launcher; what earns
automated tests is the file reading (which faces user-visible disk and must
never crash on garbage), the TC-efficiency arithmetic, and the axis-tick
maths behind the graphs.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

import pytest

from loom import paths, statsview
from loom.gamestats import GameRecorder


@pytest.fixture
def stats_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path)
    return tmp_path


def write_game(directory, name, duration=300):
    recorder = GameRecorder("test", "Test Build", "2026-07-25T12:00:00")
    for t in range(0, duration + 1, 5):
        recorder.observe(t, 3 + t // 25, -2)
    recorder.write(directory / name)


def test_list_stats_newest_first_with_labels(stats_dir):
    write_game(stats_dir, "2026-07-24_a.json")
    write_game(stats_dir, "2026-07-25_b.json")
    rows = statsview.list_stats()
    assert [p.name for p, _, _ in rows] == ["2026-07-25_b.json",
                                            "2026-07-24_a.json"]
    assert "Test Build" in rows[0][1]
    assert "5:00" in rows[0][1]


def test_corrupt_and_foreign_files_are_listed_as_unreadable(stats_dir):
    (stats_dir / "broken.json").write_text("{ not json")
    (stats_dir / "foreign.json").write_text(json.dumps({"schema": 99}))
    rows = statsview.list_stats()
    assert all(data is None for _, _, data in rows)
    assert all("unreadable" in label for _, label, _ in rows)


def test_empty_or_missing_folder_is_fine(stats_dir):
    assert statsview.list_stats() == []


def test_tc_efficiency():
    assert statsview.tc_efficiency(
        {"duration": 1000, "tc_count": 2, "tc_idle_seconds": 200}) == 0.9
    # No TCs or no duration: no honest answer.
    assert statsview.tc_efficiency({"duration": 0, "tc_count": 1}) is None
    # Overcounted idleness clamps rather than going negative.
    assert statsview.tc_efficiency(
        {"duration": 10, "tc_count": 1, "tc_idle_seconds": 100}) == 0.0


def test_game_rows_carry_the_honesty_notes():
    rows = statsview.game_rows(
        {"duration": 600, "max_villagers": 30, "tc_count": 2,
         "tc_idle_seconds": 12.0, "queued": {"knight": 500},
         "deaths": [[300, 2, True]], "attacks": [295]})
    labels = {label for label, _, _ in rows}
    assert "knight" in labels
    values = dict((label, value) for label, value, _ in rows)
    # Queue sightings are labelled as sightings, never as produced counts.
    assert values["knight"].startswith("first queued")
    assert values["villagers lost"] == "2 (2 to raids)"


def test_nice_ticks_are_round_and_cover_the_range():
    ticks = statsview.nice_ticks(0, 47)
    assert ticks[0] == 0
    assert ticks[-1] >= 40
    assert all(t == round(t, 10) for t in ticks)
    assert statsview.nice_ticks(5, 5) == [5]
