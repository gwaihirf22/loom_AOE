"""
Loom — the speck filter's width, and the two cliffs on either side of it.

reader.min_glyph_width decides how narrow a column run may be and still count
as a character. Too high and it deletes the digit "1", which is far thinner
than its siblings - measured at anchor scale 1.37, the old int(6 * scale)
returned 8, the "1" was 7px, and a population of 19/25 was confidently read
as 9/25. Too low and it admits colon-sized junk into the clock parse, which
is how a briefly-gentler formula caused live misreads on both platforms and
an overlay that could no longer attach to a match in progress.

What these tests deliberately do NOT do is drive the runtime formula through
the fixtures in tests/data/clock. That was tried, and it is how the bad
formula got justified: those fixtures are RESCALED screenshots (their own
docstring says so), so they carry smaller glyphs than the live game ever
shows, and a width that suits them fails reality. The guarantees pinned here
are the ones with live evidence behind them.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import reader


def test_the_proven_value_holds_at_reference_scale(monkeypatch):
    """max(4, int(6 * scale)) at scale 1.0 gave 6 through months of live
    Linux play. That service record is the strongest evidence this number
    has; changing it needs live evidence, not fixture evidence."""
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.min_glyph_width(1.0) == 6


@pytest.mark.parametrize("scale", [0.5, 0.68, 0.86, 0.89, 1.0])
def test_small_huds_keep_their_long_proven_values(monkeypatch, scale):
    """At and below reference scale the formula is exactly the original one -
    the region where nothing was ever wrong is the region nothing changes."""
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.min_glyph_width(scale) == max(4, int(6 * scale))


@pytest.mark.parametrize("scale", [1.2, 1.37, 1.48, 2.0])
def test_large_huds_never_swallow_the_one(monkeypatch, scale):
    """The measured bug: at anchor scale 1.37-1.48 the digit "1" is 7px, and
    the uncapped formula reached 8 and deleted it. The cap must hold the
    value under 7 however large the HUD gets - the icon's scale outgrows the
    text's, so extrapolating the multiplier upward is exactly the mistake
    that was made."""
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.min_glyph_width(scale) < 7


def test_the_floor_holds_for_a_tiny_hud(monkeypatch):
    """Shrinking the HUD must not drive the threshold toward zero, or every
    speck becomes a candidate digit."""
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.min_glyph_width(0.1) == 4


def test_the_override_wins(monkeypatch):
    """LOOM_MIN_GLYPH_WIDTH exists so the number can be A/B-tested against a
    live game in seconds; it has to beat the formula at every scale."""
    monkeypatch.setenv("LOOM_MIN_GLYPH_WIDTH", "5")
    assert reader.min_glyph_width(0.5) == 5
    assert reader.min_glyph_width(1.48) == 5


def test_a_garbage_override_falls_back(monkeypatch):
    monkeypatch.setenv("LOOM_MIN_GLYPH_WIDTH", "not a number")
    assert reader.min_glyph_width(1.0) == 6


def test_max_glyph_width_still_tracks_the_hud(monkeypatch):
    """The sibling bound: a run wider than this is split as two touching
    characters, so it must GROW with the HUD or it halves single large
    digits - measured at scale 1.48, a 15px "4" was split by the fixed 13
    and "4/5" read as nothing."""
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.max_glyph_width(1.0) == 13
    assert reader.max_glyph_width(1.48) == 19


def test_max_glyph_width_shrinks_with_the_hud_too(monkeypatch):
    """And it must SHRINK, which it once did not.

    This bound was originally floored at its own reference value of 13, so
    that "behaviour at and below scale 1.0 is untouched" - a deliberately
    conservative choice, and the wrong one. A threshold that cannot scale
    down is the fault this project keeps meeting, and here is what it cost:
    at 1920x1080 the mod's population runs measure 2-8px and a slash merged
    into the digit beside it lands around 12. Under a floor of 13 that
    merged run was never split, classified as nothing, and took the whole
    band down with it - 111 of 263 readings lost, which is why the housing
    alerts went quiet at that resolution.

    Pinned at the scales that matter: 0.735 is the mod at 1080p, 0.995 is
    stock at 1440p.
    """
    monkeypatch.delenv("LOOM_MIN_GLYPH_WIDTH", raising=False)
    assert reader.max_glyph_width(0.735) == 9
    assert reader.max_glyph_width(0.68) == 8

    # Still comfortably above the widest single digit measured at that
    # size (8px), so a lone digit is never sawn in half to get here.
    assert reader.max_glyph_width(0.735) > 8
