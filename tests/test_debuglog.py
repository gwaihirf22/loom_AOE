"""
Tests for the forensic session log.

The log exists because a live failure left no evidence: the villager count
froze for a whole game and nothing on disk could say whether the band read
wrong, read nothing, or was never looked at. So the line format is tested
for exactly that distinction - raw beside believed - and the writer for the
one property it must never lose: being unable to hurt the overlay.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import debuglog, reader


def make_reading(**overrides):
    fields = dict(villagers=6, game_time=310, event=None, hud_visible=True,
                  raw_villagers=6, raw_clock=310, population=(7, 15))
    fields.update(overrides)
    return reader.Reading(**fields)


# ---- the line format ---------------------------------------------------

def test_a_held_belief_and_a_fresh_read_print_differently():
    """The whole point of the raw column."""
    fresh = debuglog.describe_reading(make_reading())
    held = debuglog.describe_reading(make_reading(raw_villagers=None))
    assert "vill 6 raw 6" in fresh
    assert "vill 6 raw -" in held


def test_the_gap_rides_the_line_once_announced():
    line = debuglog.describe_reading(make_reading(raw_villagers=None,
                                                  villager_gap=24))
    assert "UNREAD 24s" in line


def test_events_and_alerts_ride_the_line():
    line = debuglog.describe_reading(
        make_reading(event="game_started",
                     game_events=["town_center_built"]),
        alerts_list=[("TC IDLE", "full")])
    assert "EVENT game_started" in line
    assert "feed town_center_built" in line
    assert "alert TC IDLE:full" in line


def test_an_unreadable_poll_still_makes_a_line():
    line = debuglog.describe_reading(
        make_reading(villagers=None, game_time=None, raw_villagers=None,
                     raw_clock=None, population=None, hud_visible=False))
    assert "t --:--" in line
    assert "vill - raw -" in line


# ---- the writer --------------------------------------------------------

def test_lines_reach_the_file(tmp_path):
    log = debuglog.SessionLog(directory=tmp_path)
    log.line("hello")
    log.poll(make_reading())
    log.close()
    text = log.path.read_text(encoding="utf-8")
    assert "hello" in text
    assert "vill 6 raw 6" in text


def test_old_sessions_are_pruned_newest_kept(tmp_path):
    for stamp in ("20260101_000001", "20260101_000002", "20260101_000003"):
        (tmp_path / f"overlay_{stamp}.log").write_text("old")
    log = debuglog.SessionLog(directory=tmp_path, keep=3)
    log.close()
    names = sorted(p.name for p in tmp_path.glob("overlay_*.log"))
    assert len(names) == 3                      # two old + the new one
    assert "overlay_20260101_000001.log" not in names
    assert "overlay_20260101_000003.log" in names


def test_an_unwritable_directory_is_survived(tmp_path):
    blocked = tmp_path / "not_a_dir"
    blocked.write_text("a file where the directory should be")
    log = debuglog.SessionLog(directory=blocked / "logs")
    log.line("nothing happens")                 # must not raise
    log.close()
    assert log.path is None


def test_the_null_log_is_inert(tmp_path):
    log = debuglog.NullLog()
    log.line("nothing")
    log.poll(make_reading())
    log.close()
    assert log.path is None
    assert list(tmp_path.iterdir()) == []
