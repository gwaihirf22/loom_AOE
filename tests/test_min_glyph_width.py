"""
Loom — the speck filter must never be wider than the digit "1".

reader.min_glyph_width decides how narrow a column run may be and still count
as a character. It exists to drop specks. The trap is that "1" is far thinner
than every other digit - 7px against 12-13px in the same band - so a threshold
that scales faster than the font eventually deletes real ones.

That is not hypothetical. At HUD scale 1.37 the old int(6 * scale) returned 8
and skipped a 7px "1": a population of 19/25 was read as 9/25, and 18 villagers
as 8. Both were reported confidently, which is the one thing Loom must never
do - a wrong villager count silently desynchronises the whole build order.

The other half of the trap is that the existing fixture tests hand
read_clock_seconds a gentler width than the runtime actually computed, so they
kept passing while the live path was broken. These tests close that gap by
driving the fixtures with the REAL formula.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import pytest

from loom import digits, reader

DATA = pathlib.Path(__file__).parent / "data" / "clock"

# The HUD scales Loom has actually been observed at: 0.68 and 1.37 on the Mac,
# ~1.0 on the Linux box the fixtures came from.
OBSERVED_SCALES = [0.68, 0.86, 1.0, 1.37, 2.0]


def band(name):
    image = cv2.imread(str(DATA / name))
    assert image is not None, f"missing fixture {name}"
    return image


@pytest.fixture(scope="module")
def templates():
    return digits.load_digit_templates()


@pytest.mark.parametrize("scale", OBSERVED_SCALES)
def test_never_wide_enough_to_swallow_a_one(scale):
    """The threshold must stay under the narrowest digit at every HUD scale.

    "1" measures about 5.1 reference pixels wide - 7px at scale 1.37. A
    threshold at or above that deletes it silently.
    """
    narrowest_digit = 5.1 * scale
    assert reader.min_glyph_width(scale) < narrowest_digit


@pytest.mark.parametrize("name,expected", [
    ("live_dark_bar_1295.png", 1295),
    ("red_dark_theme_10.png", 10),
    ("yellow_stone_27.png", 27),
    ("yellow_stone_21.png", 21),
])
def test_fixtures_read_with_the_real_formula(templates, name, expected):
    """Every clock fixture reads using the width the RUNTIME would pick.

    test_clock_themes.py passes hand-picked widths, which is what let the
    runtime formula drift away from what the fixtures need. This drives the
    same fixtures through reader.min_glyph_width instead.
    """
    width = reader.min_glyph_width(1.0)
    value, score = digits.read_clock_seconds(band(name), templates, width)
    assert value == expected, f"{name} read {value} at min_glyph_width {width}"
    assert score >= digits.MIN_MATCH_SCORE


def test_live_population_reads_with_the_real_formula(templates):
    current, cap = digits.read_population(
        band("live_dark_bar_1295_pop.png"), templates,
        reader.min_glyph_width(1.0))
    assert (current, cap) == (35, 45)


def test_floor_holds_for_a_tiny_hud():
    """Shrinking the HUD must not drive the threshold to zero, or every
    speck becomes a candidate digit."""
    assert reader.min_glyph_width(0.1) == 3
