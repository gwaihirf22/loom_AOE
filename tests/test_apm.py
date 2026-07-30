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
