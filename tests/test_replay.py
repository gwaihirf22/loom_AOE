"""
Loom — tests for finding the recorded game behind a match.

Everything here runs with no game, no mgz and no pixels: the whole risk of
replay-derived statistics is picking the RIGHT file, and that is decidable
from names and mtimes alone.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import datetime
import os

import pytest

from loom import replay


def a_record(tmp_path, stamp, minutes=30, name="SP Replay v101.103.48987.0"):
    """A record file named the way the game names them, aged so it counts
    as finished."""
    path = tmp_path / f"{name} @{stamp}.aoe2record"
    path.write_bytes(b"not really a record")
    started = replay.started_at(path)
    ended = started + datetime.timedelta(minutes=minutes)
    import os
    os.utime(path, (ended.timestamp(), ended.timestamp()))
    return replay.Record(path, started, ended)


def test_the_start_time_comes_out_of_the_games_own_filename():
    assert replay.started_at("SP Replay v101.103.48987.0 @2026.08.25 010700"
                             ".aoe2record") == datetime.datetime(
        2026, 8, 25, 1, 7, 0)
    # Multiplayer names carry a lobby id and a duplicate suffix.
    assert replay.started_at("MP Replay v101.102.12638.0 #(78174) "
                             "@2023.03.14 144700 (5).aoe2record") == \
        datetime.datetime(2023, 3, 14, 14, 47, 0)
    assert replay.started_at("savegame.aoe2spgame") is None


def test_a_match_in_progress_is_never_readable(tmp_path):
    """The house rule, and the reason it needs enforcing rather than
    stating: rec.aoe2record parses perfectly while the game is being
    played, and it holds everything fog would have hidden."""
    live = tmp_path / replay.LIVE_RECORD
    live.write_bytes(b"in progress")
    assert not replay.is_finished(live)


def test_a_record_still_being_written_is_not_offered(tmp_path):
    """Freshly modified means the game may still be appending to it."""
    fresh = tmp_path / "SP Replay v1 @2026.08.25 010700.aoe2record"
    fresh.write_bytes(b"still going")
    assert not replay.is_finished(fresh)
    # ...and the same file, once it has settled.
    old = datetime.datetime.now() - datetime.timedelta(minutes=5)
    assert replay.is_finished(fresh, now=datetime.datetime.now()
                              + datetime.timedelta(minutes=5))


def test_the_session_is_matched_by_containment_not_by_nearness(tmp_path):
    """Measured over 265 games: containment matched 56 where nearest-start
    matched 51. A session that began 19 seconds INTO its match is nearer to
    the next match's start than to its own whenever games run back to back."""
    mine = a_record(tmp_path, "2026.08.25 010700", minutes=36)
    later = a_record(tmp_path, "2026.08.25 015000", minutes=20)
    stats = tmp_path / "2026-08-25_010714_fast_castle.json"

    found = replay.match(stats, available=[mine, later])
    assert found.confidence == replay.CERTAIN
    assert found.record.path == mine.path


def test_two_games_running_at_once_is_admitted_not_guessed(tmp_path):
    """Picking the nearer one would be Loom guessing at the exact seam
    where a wrong answer attaches one game's truth to another's readings."""
    one = a_record(tmp_path, "2026.08.25 010700", minutes=60)
    two = a_record(tmp_path, "2026.08.25 010500", minutes=60)
    stats = tmp_path / "2026-08-25_010714_fast_castle.json"

    found = replay.match(stats, available=[one, two])
    assert found.confidence == replay.AMBIGUOUS
    assert found.record is None
    assert "2" in found.why


def test_a_session_with_no_record_says_so(tmp_path):
    """Watching an existing replay writes a stats file and no record. 205
    of 265 files on the author's machine are exactly this, and it is
    correct behaviour rather than a gap."""
    elsewhere = a_record(tmp_path, "2026.08.24 100000", minutes=20)
    stats = tmp_path / "2026-08-25_010714_fast_castle.json"
    found = replay.match(stats, available=[elsewhere])
    assert found.confidence == replay.NONE
    assert found.record is None


def test_loom_may_have_been_running_before_the_match_began(tmp_path):
    """Loom usually starts after the game - median 19 seconds after - but
    it can be up first and acquire the HUD as the match loads."""
    record = a_record(tmp_path, "2026.08.25 010700", minutes=36)
    early = tmp_path / "2026-08-25_010600_fast_castle.json"
    assert replay.match(early, available=[record]).confidence == replay.CERTAIN
    # ...but not arbitrarily early, or every idle Loom claims the next game.
    hours = tmp_path / "2026-08-25_004000_fast_castle.json"
    assert replay.match(hours, available=[record]).confidence == replay.NONE


def test_the_duration_gap_is_reported_and_never_used_to_reject():
    """The 08-25 game: Loom said 55:45, the record said 36:00, and the
    record was right - a Loom clock misread. A matcher that rejected on
    disagreement would have thrown away the only evidence that found it."""
    found = replay.Match("a record", replay.CERTAIN, "one game", None)
    agreed = replay.corroborate(found, 3038, 3038)
    assert agreed.gap == 0 and agreed.confidence == replay.CERTAIN

    disagreed = replay.corroborate(found, 3345, 2160)
    assert disagreed.gap == -1185
    assert disagreed.confidence == replay.CERTAIN, \
        "a disagreeing duration rejected the match instead of reporting it"

    # Nothing parsed yet is not agreement.
    assert replay.corroborate(found, 3345, None).gap is None


def test_a_stats_file_with_no_timestamp_matches_nothing(tmp_path):
    found = replay.match(tmp_path / "handwritten.json", available=[])
    assert found.confidence == replay.NONE


def test_every_platform_can_say_where_it_looked():
    """"No records found" and "the game is not installed" are different
    answers, so the paths come back whether or not they exist."""
    assert replay.search_paths(), "this platform names nowhere to look"
    assert all("Age of Empires 2 DE" in str(p) for p in replay.search_paths())


def test_a_flatpak_steam_is_looked_in(monkeypatch):
    """Steam installed as a Flatpak redirects its whole home, so NONE of
    the ordinary paths exist and Loom reported "no recorded games" on a
    machine full of them. That is a large share of Bazzite and Steam Deck
    players, and it is the shape Loom's own Flatpak will most often meet.

    Checked by parts rather than by string so it reads the same from
    either boot."""
    monkeypatch.delenv(replay.RECORDS_DIR_ENV, raising=False)

    wanted = (".var", "app", "com.valvesoftware.Steam")
    assert any(all(part in path.parts for part in wanted)
               for path in replay.search_paths("posix")),         "a Flatpak'd Steam is not among the places looked in"


def test_a_named_records_folder_is_looked_in_first(monkeypatch, tmp_path):
    """The guesses can only ever cover the DEFAULT Steam library. A library
    on a second drive is wherever the player put it, so they can say."""
    monkeypatch.setenv(replay.RECORDS_DIR_ENV, str(tmp_path))

    assert replay.search_paths("posix")[0] == tmp_path


def test_two_libraries_can_be_named(monkeypatch, tmp_path):
    """os.pathsep-separated, like PATH: a player with two Steam libraries
    has two answers and choosing one for them would be a guess."""
    second = tmp_path / "other"
    monkeypatch.setenv(replay.RECORDS_DIR_ENV,
                       os.pathsep.join([str(tmp_path), str(second)]))

    assert replay.search_paths("posix")[:2] == [tmp_path, second]


def test_naming_a_folder_does_not_hide_the_usual_places(monkeypatch, tmp_path):
    """A mistyped variable should leave a visibly wrong entry at the top of
    a list that still contains the right places, not replace the list with
    nothing and report that the game is not installed."""
    monkeypatch.setenv(replay.RECORDS_DIR_ENV, str(tmp_path / "typo"))

    assert len(replay.search_paths("posix")) > 1
    assert any("Steam" in path.parts for path in replay.search_paths("posix"))


def test_reading_a_record_costs_nothing_until_one_is_read():
    """mgz is imported inside the parser, not at module scope. Most runs of
    Loom never open a record, and a frozen build should not carry the cost
    of a library for a feature the player may not use.

    Asserted with the AST rather than by trusting the comment: a later edit
    that hoists the import to the top is exactly the change that would
    silently undo this."""
    import ast
    import pathlib
    source = pathlib.Path(replay.__file__).read_text(encoding="utf-8")
    for node in ast.parse(source).body:           # module scope ONLY
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module]
        else:
            continue
        for name in names:
            assert not (name or "").startswith("mgz"), \
                "mgz was hoisted to module scope"


def test_a_live_match_is_refused_where_the_BYTES_are_opened(tmp_path):
    """The guarantee has to live on the door of the function that opens
    the file, not wherever a caller happened to look it up.

    It used to sit in records(), the automatic finder. harvest() does not
    go through records(), so the statistics window's file picker walked
    straight past it - browse to rec.aoe2record mid-match and it would
    have been parsed, both players' orders, everything the fog was
    hiding. A safeguard every caller must remember to ask for is a
    hand-curated list and fails the same silent way.
    """
    live = tmp_path / replay.LIVE_RECORD
    live.write_bytes(b"a match in progress")

    # The route the picker takes, which skips records() entirely.
    with pytest.raises(replay.GameStillRunning):
        replay.harvest(live)
    with pytest.raises(replay.GameStillRunning):
        list(replay.operations(live))


def test_the_refusal_says_which_rule_and_not_just_no(tmp_path):
    """Two refusals that mean different things to a person: the match
    being played right now, and a game that ended moments ago and is
    worth trying again in a few seconds."""
    live = tmp_path / replay.LIVE_RECORD
    live.write_bytes(b"in progress")
    assert "right now" in replay.refusal_reason(live)

    fresh = tmp_path / "SP Replay v1 @2026.08.25 010700.aoe2record"
    fresh.write_bytes(b"just ended")
    assert "still writing" in replay.refusal_reason(fresh)

    assert replay.refusal_reason(fresh, now=datetime.datetime.now()
                                 + datetime.timedelta(minutes=5)) is None


def test_a_broken_file_is_not_a_running_game(tmp_path):
    """Its own exception type, because "this file is broken" and "Loom
    will not look at this yet" need completely different sentences said
    to the person who clicked."""
    rubbish = tmp_path / "SP Replay v1 @2026.08.25 010700.aoe2record"
    rubbish.write_bytes(b"not a recorded game at all")
    import os
    old = (datetime.datetime.now() - datetime.timedelta(minutes=5)).timestamp()
    os.utime(rubbish, (old, old))
    with pytest.raises(Exception) as raised:
        replay.harvest(rubbish)
    assert not isinstance(raised.value, replay.GameStillRunning)


def test_the_frozen_build_names_the_parser_it_only_imports_lazily():
    """mgz appears nowhere at module scope, so nothing reading this
    project's imports would know the bundle needs it.

    NOT guarding a bug. I assumed a lazy import would be invisible to
    PyInstaller and measured instead: two builds, one with these names in
    hiddenimports and one without, both produced 40 mgz entries and 11
    construct entries in the PYZ. Its modulegraph walks imports inside
    function bodies too. The names stay because they cost nothing, they
    make a runtime dependency visible to anyone reading the build, and an
    import moved behind a try/except or importlib later would genuinely
    vanish - at which point only a packaged run would notice.
    """
    import pathlib
    spec = pathlib.Path(replay.__file__).parent.parent / "loom.spec"
    text = spec.read_text(encoding="utf-8")
    hidden = text[text.index("hiddenimports=["):text.index("excludes=[")]
    for needed in ('"mgz"', '"mgz.fast"', '"construct"'):
        assert needed in hidden, f"{needed} is not named in the build"


def test_a_race_and_an_absence_never_share_a_sentence(tmp_path):
    """The bug this pair exists to stop from coming back.

    A record whose match Loom sat through, written seconds ago, is NOT
    the same answer as no record at all - one is worth waiting for and
    the other never will be. They were the same answer for the whole life
    of the feature, because `records` dropped the fresh file before
    `match` could see it and `match` then said "no recorded game was
    running then" about a file sitting on the disk.
    """
    covering = a_record(tmp_path, "2026.08.25 010700", minutes=36)
    stats = tmp_path / "2026-08-25_010714_fast_castle.json"

    # As it is: aged, so it reads.
    assert replay.match(stats, available=[covering]).confidence \
        == replay.CERTAIN

    # The same file, seconds old. The session is still inside it.
    import os
    now = datetime.datetime.now()
    os.utime(covering.path, (now.timestamp(), now.timestamp()))
    fresh = replay.Record(covering.path, covering.started, now)
    found = replay.match(stats, available=[fresh])
    assert found.confidence == replay.NOT_YET
    assert found.record is None, "a record that may not be read is not offered"
    # And it says which of the two it is, in words a person can act on.
    assert "few seconds" in found.why

    # Nothing covering the session at all is still NONE, and says so.
    away = a_record(tmp_path, "2026.08.25 230000", minutes=10)
    missing = replay.match(stats, available=[away])
    assert missing.confidence == replay.NONE
    assert missing.why != found.why


def test_the_unsettled_records_are_fetched_only_when_asked(tmp_path):
    """`settled_only=False` exists for `match` alone. Every other caller
    keeps the refusal, so a looser lookup cannot widen what may be read."""
    fresh = tmp_path / "SP Replay v1 @2026.08.25 010700.aoe2record"
    fresh.write_bytes(b"still going")

    assert replay.records(roots=[tmp_path]) == []
    asked = replay.records(roots=[tmp_path], settled_only=False)
    assert [r.path for r in asked] == [fresh]
