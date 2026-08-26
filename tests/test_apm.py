"""
Loom — tests for APM alignment.

The counter lives on the wall clock; the game lives on its own clock,
which runs at 1.7x in multiplayer and pauses. The alignment maths is where
those meet, so these tests pin the awkward cases: pauses, menus with no
mapping at all, and buckets outside the match.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import apm, config


def test_interpolates_between_pairs():
    pairs = [(100, 60), (110, 77)]        # 1.7x game speed
    assert apm.game_time_at(105, pairs) == 68.5


def test_a_pause_means_game_time_stands_still():
    pairs = [(100, 60), (110, 60)]        # ten wall seconds, game paused
    assert apm.game_time_at(105, pairs) == 60


def test_outside_the_mapping_is_unknowable():
    pairs = [(100, 60), (110, 77)]
    assert apm.game_time_at(50, pairs) is None      # long before the match
    assert apm.game_time_at(200, pairs) is None     # long after it
    # Just off the edge still counts - the last reading was a moment ago.
    assert apm.game_time_at(112, pairs) == 77


def test_a_long_hole_in_the_mapping_drops_buckets():
    # Alt-tabbed to a browser for two minutes mid-session: readings exist
    # on both sides but the middle is unknowable.
    pairs = [(100, 60), (250, 90)]
    assert apm.game_time_at(150, pairs) is None


def test_align_converts_counts_to_per_minute():
    # Pairs arrive about once a second in reality; anything sparser than
    # a minute reads as a hole (tested separately).
    pairs = [(100, 60), (150, 145), (200, 230)]
    buckets = [(150, 4, 6)]               # 10 actions in a 5s bucket
    section = apm.align(buckets, pairs)
    assert section["t"] == [145]
    assert section["apm"] == [120]
    assert section["keys_total"] == 4
    assert section["clicks_total"] == 6


def test_align_keeps_totals_for_dropped_buckets():
    # Menu clicking counts toward the session totals but stays off the
    # game-time series.
    pairs = [(100, 60), (110, 77)]
    buckets = [(105, 5, 5), (500, 30, 30)]
    section = apm.align(buckets, pairs)
    assert len(section["t"]) == 1
    assert section["keys_total"] == 35
    assert section["clicks_total"] == 35


def test_align_with_nothing_usable_is_none():
    assert apm.align([(105, 5, 5)], []) is None
    assert apm.align([], [(100, 60)]) is None


def test_track_apm_defaults_on(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert config.track_apm() is True
    config.set_track_apm(False)
    assert config.track_apm() is False
    config.save({"track_apm": "sure"})
    assert config.track_apm() is True     # garbage means the default: on


def test_a_new_game_discards_the_OLD_one_not_the_real_one():
    """Measured on 2026-08-25_224115: the overlay attached to a game
    already at 0:54, lost it, and a new game began - all in one session.
    The recorder's timeline restarted for the new game; this series did
    not, which is why apm.t was the only series in 266 files ever going
    backwards.

    The tempting fix is to drop whatever lands behind the last bucket. On
    this file that keeps the OLD game's four buckets and throws away the
    first sixty-three seconds of the real one - a series that looks
    monotonic and clean and belongs to two matches. A plausible hybrid is
    worse than an obvious seam, so the EARLIER game goes."""
    pairs = [(1000.0, 54), (1010.0, 64), (1020.0, 6), (1030.0, 16)]
    buckets = [(1000.0, 9, 0), (1010.0, 9, 0), (1020.0, 3, 0), (1030.0, 3, 0)]
    section = apm.align(buckets, pairs)
    assert section["t"] == [6, 16], "the earlier game was kept instead"
    assert section["buckets_from_an_earlier_game"] == 2
    # Named for what they were: calling them unplaceable would send
    # someone hunting a fault that is not there.
    assert "unplaceable_buckets" not in section


def test_a_clock_that_wobbles_is_not_a_new_game():
    """A second the clock did not advance past has nowhere honest to go,
    but it must not throw away everything before it."""
    pairs = [(1000.0, 100), (1010.0, 110), (1020.0, 108), (1030.0, 130)]
    buckets = [(1000.0, 5, 0), (1010.0, 5, 0), (1020.0, 5, 0), (1030.0, 5, 0)]
    section = apm.align(buckets, pairs)
    assert section["t"] == [100, 110, 130], "a wobble restarted the series"
    assert section["unplaceable_buckets"] == 1
    assert "buckets_from_an_earlier_game" not in section


def test_a_clean_game_makes_no_claim_about_dropped_buckets():
    """The key is absent rather than zero, so its presence always means
    something happened."""
    pairs = [(1000.0, 100), (1010.0, 110)]
    section = apm.align([(1000.0, 5, 0), (1010.0, 5, 0)], pairs)
    assert "unplaceable_buckets" not in section


def test_the_timeline_tells_the_seam_rather_than_the_clock_hinting_at_it():
    """Measured on 2026-08-25_224115: one overlay session, two matches.
    The buckets run across both; the recorder started a fresh file for the
    second and its timeline begins at 1. Nothing in the bucket stream says
    where the join is - the timeline does, because it only ever held the
    game the file is about."""
    pairs = [(1000.0, 54), (1010.0, 64), (1020.0, 6), (1030.0, 16)]
    buckets = [(1000.0, 9, 0), (1010.0, 9, 0), (1020.0, 3, 0), (1030.0, 3, 0)]
    section = apm.align(buckets, pairs, game_from=1)
    assert section["t"] == [6, 16]
    assert section["buckets_from_an_earlier_game"] == 2


def test_a_bucket_just_before_the_first_reading_still_counts():
    """The timeline starts at Loom's first successful read, not at the
    game's first second. A bucket a moment earlier belongs to this game
    and must not be thrown out for being punctual."""
    pairs = [(1000.0, 48), (1010.0, 58)]
    section = apm.align([(1000.0, 4, 0), (1010.0, 4, 0)], pairs, game_from=50)
    assert section["t"] == [48, 58], "a bucket from this game was discarded"


def test_being_told_nothing_falls_back_to_noticing_the_step():
    """Every stats file written before the timeline was passed in, and any
    caller that has no timeline to offer."""
    pairs = [(1000.0, 54), (1010.0, 64), (1020.0, 6), (1030.0, 16)]
    buckets = [(1000.0, 9, 0), (1010.0, 9, 0), (1020.0, 3, 0), (1030.0, 3, 0)]
    assert apm.align(buckets, pairs)["t"] == [6, 16]

