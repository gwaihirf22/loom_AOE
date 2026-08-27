"""The screening tool has to say "this one, not those ones".

A screen that flags everything is the same as one that flags nothing, and
this one runs over 270 real games where most are fine. So both halves are
tested: the phantom game is found, and the honest ones are left alone.

The first draft of this tool scored a game by idle seconds over
duration x tc_count, and test_the_share_alone_would_have_missed_it below is
that draft's epitaph. It is kept because it is the whole reason the tool
measures what it measures.
"""

import json

from tools import tc_audit


def _write(directory, name, duration, idle, tcs, timeline=None,
           version="1.0.6"):
    record = {
        "meta": {"loom": version, "hud": {"profile": "annehk"}},
        "game": {"duration": duration, "tc_idle_seconds": idle,
                 "tc_count": tcs},
    }
    if timeline is not None:
        record["timeline"] = timeline
    (directory / name).write_text(json.dumps(record), encoding="utf-8")


def _episode(start, length, total):
    """A timeline with one unbroken idle run of `length` game seconds."""
    times = list(range(total))
    idle = [1 if start <= t < start + length else 0 for t in times]
    return {"t": times, "idle_tcs": idle}


def test_a_phantom_game_is_flagged(tmp_path):
    # The real one: 2829 seconds, 1907 billed idle, three Town Centres by
    # the end, and 517 seconds without a single recovering poll.
    _write(tmp_path, "phantom.json", 2829, 1907, 3,
           _episode(582, 517, 2829))
    found = tc_audit.games(tmp_path)
    assert tc_audit.suspicious(found) == found


def test_the_share_alone_would_have_missed_it(tmp_path):
    # Why the tool does not score games that way. The phantom ran while
    # there was ONE Town Centre; dividing by the three it finished with
    # scores it at 22%, below any bar an honest game would clear.
    _write(tmp_path, "phantom.json", 2829, 1907, 3, _episode(582, 517, 2829))
    assert tc_audit.games(tmp_path)[0]["share"] < 0.25


def test_an_honest_game_is_not_flagged(tmp_path):
    # Real idle time, noticed and fixed. Most of the archive is this: the
    # median longest episode over 147 long games is 24 seconds.
    _write(tmp_path, "fine.json", 2400, 120, 3, _episode(600, 40, 2400))
    assert tc_audit.suspicious(tc_audit.games(tmp_path)) == []


def test_many_short_spells_are_not_one_long_one(tmp_path):
    # Ten minutes of idleness in twenty pieces is a player being busy. The
    # same total in one piece is a belief that never recovered, and only the
    # second is what this looks for.
    times = list(range(2400))
    idle = [1 if (t // 30) % 2 else 0 for t in times]
    _write(tmp_path, "busy.json", 2400, 1200, 3, {"t": times,
                                                  "idle_tcs": idle})
    assert tc_audit.suspicious(tc_audit.games(tmp_path)) == []


def test_a_short_game_is_never_judged(tmp_path):
    # Three minutes cannot contain the failure this looks for.
    _write(tmp_path, "short.json", 180, 170, 1, _episode(5, 170, 180))
    assert tc_audit.suspicious(tc_audit.games(tmp_path)) == []


def test_a_misread_clock_is_not_a_ten_hour_game(tmp_path):
    # 1.0.1 recorded "games" of 36060 seconds whose idle episode is ten
    # hours long. Without this they sit at the top of every listing forever
    # and the real worst game is never seen.
    _write(tmp_path, "impossible.json", 36060, 5, 1, _episode(0, 36000,
                                                              36060))
    assert tc_audit.games(tmp_path) == []


def test_a_record_with_no_timeline_is_not_reported_as_clean(tmp_path):
    # "I could not measure this" is not "this was fine" - the same rule the
    # readers live by, applied to the audit of them.
    _write(tmp_path, "old.json", 2400, 900, 2)
    assert tc_audit.games(tmp_path)[0]["episode"] is None


def test_an_unreadable_file_is_skipped_not_fatal(tmp_path):
    # The stats directory is the player's, and a half-written file from a
    # crashed session must not stop the audit reading the other 269.
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path, "good.json", 2400, 120, 3, _episode(0, 10, 2400))
    assert [g["name"] for g in tc_audit.games(tmp_path)] == ["good.json"]


def test_a_game_with_no_town_centre_count_is_skipped(tmp_path):
    # Older records predate tc_count. Dividing by it would be a crash, and
    # guessing it would be a fabricated share.
    (tmp_path / "old.json").write_text(json.dumps(
        {"meta": {}, "game": {"duration": 2400, "tc_idle_seconds": 900}}),
        encoding="utf-8")
    assert tc_audit.games(tmp_path) == []


def test_versions_are_summarised_over_judgeable_games_only(tmp_path):
    _write(tmp_path, "a.json", 2400, 1800, 1, _episode(0, 900, 2400),
           version="1.0.4")
    _write(tmp_path, "b.json", 120, 0, 1, _episode(0, 5, 120),
           version="1.0.4")
    assert tc_audit.by_version(tc_audit.games(tmp_path))["1.0.4"][0] == 1
