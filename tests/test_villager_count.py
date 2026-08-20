"""
Loom — the villager count must not lose its leading digit.

The villager count is the ONLY signal that advances the build order, so a
wrong one is the most expensive mistake Loom can make - and this one was
silent. At 1920x1080 the digit "1" is drawn 3px wide against a width gate
of 4, so it was skipped as if it were a colon: a band plainly showing "18"
returned 8, and "21" returned 2. Measured over one live capture, 189 of 300
frames dropped a full-height 3px run, every one of them a "1".

Nothing looked wrong while it happened. The readings were self-consistent
frame to frame - zero single-frame contradictions in those 300 frames -
because the leading digit was missing consistently. That is the shape of
failure the project's rules single out: not a gap, which Loom admits, but a
confident wrong number.

The rule that fixes it is digits.is_character: a run narrower than the gate
is still a character when it stands as tall as its neighbours. A colon is
two dots around the middle and never does.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import pytest

from loom import anchor, digits, hud, queue

DATA = pathlib.Path(__file__).parent / "data" / "villagers"

# The gate in force on these bands: anchor scale 0.735, Anne_HK, so
# reader.min_glyph_width answers 4 - one wider than the "1" it was hiding.
SMALL_HUD_GATE = 4


def band(name):
    image = cv2.imread(str(DATA / name))
    assert image is not None, f"missing fixture {name}"
    return image


@pytest.fixture(scope="module")
def templates():
    return digits.load_digit_templates()


@pytest.mark.parametrize("name,expected", [
    ("small_hud_1080p_12.png", 12),
    ("small_hud_1080p_18.png", 18),   # returned 8 before the rule
    ("small_hud_1080p_21.png", 21),   # returned 2 before the rule
])
def test_two_digit_counts_read_whole(templates, name, expected):
    value, score = digits.read_count(band(name), templates, SMALL_HUD_GATE)

    assert value == expected
    assert score >= digits.MIN_MATCH_SCORE


def test_without_the_rule_the_leading_one_vanishes(templates, monkeypatch):
    """Pinned as its own case because the failure it prevents is not a
    crash or a gap - it is a plausible smaller number that every filter
    downstream will happily believe."""
    monkeypatch.setattr(digits, "COLON_HEIGHT_FRACTION", 99.0)

    value, _score = digits.read_count(
        band("small_hud_1080p_18.png"), templates, SMALL_HUD_GATE)

    assert value == 8, "the fixture no longer reproduces the original bug"


def test_a_short_narrow_run_is_still_skipped(templates):
    """The rule must not simply lower the gate. Height is the whole of it:
    a run that is narrow AND short - speck, colon, antialiasing crumb -
    stays out."""
    binary = cv2.imread(str(DATA / "small_hud_1080p_18.png"),
                        cv2.IMREAD_GRAYSCALE) * 0
    binary[4:6, 2:4] = 255              # 2px wide, 2px tall: a speck
    binary[2:14, 8:14] = 255            # a real digit-sized block

    runs = digits.find_column_runs(binary)
    _boxes, tallest = digits._bar_context(binary, runs)

    assert not digits.is_character(binary, 2, 4, SMALL_HUD_GATE, tallest)
    assert digits.is_character(binary, 8, 14, SMALL_HUD_GATE, tallest)


def test_a_narrow_but_full_height_run_counts(templates):
    """The "1" itself, in the abstract: 3px wide, as tall as the digits
    beside it."""
    binary = cv2.imread(str(DATA / "small_hud_1080p_18.png"),
                        cv2.IMREAD_GRAYSCALE) * 0
    binary[2:14, 2:5] = 255             # 3px wide, full height
    binary[2:14, 8:14] = 255

    runs = digits.find_column_runs(binary)
    _boxes, tallest = digits._bar_context(binary, runs)

    assert digits.is_character(binary, 2, 5, SMALL_HUD_GATE, tallest)


# ---- the stock band must not reach into the banner -------------------------
#
# The stock villager band used to start at y=35, which is inside the banner
# art above the number. Where a digit's columns also carried a speck of that
# art, extract_glyph's row-trim spanned from the speck down to the digit and
# squashed the digit into the bottom half of its 14x20 box - so it was matched
# in a shape it never had on screen. Measured across five stock runs: the
# glyphs scored a median 0.32 at 1080p and 0.28 at 1440p, nearly all under the
# 0.55 gate. The dark-box pass therefore refused them and the badge fallback,
# which sees only the leading digit, answered instead: 10, 11, 14 and 15 read
# as a bare "1", and a ten-minute game topped out at 17 villagers.
#
# It was never a resolution bug - the banner scales with everything else - and
# never a template one, which is where the search started.

STOCK_GATE = 3          # reader.min_glyph_width for stock at these scales


@pytest.mark.parametrize("name,expected", [
    ("stock_1080p_10.png", 10),   # read as 1 before
    ("stock_1080p_14.png", 14),   # read as 1 before
    ("stock_1080p_21.png", 21),
])
def test_stock_two_digit_counts_read_whole(templates, name, expected):
    value, score = digits.read_count(band(name), templates, STOCK_GATE)

    assert value == expected
    assert score >= digits.MIN_MATCH_SCORE


def test_the_stock_band_holds_the_digits_and_nothing_above_them():
    """The rule itself, checked on a real frame rather than on the constant.

    A band that holds only the number has ONE run of inked rows. Reaching up
    into the banner adds a speck with a blank gap beneath it, which is
    exactly what fooled the row-trim - so contiguity is the property to
    assert, and it fails for the old offset while passing for this one.
    """
    frames = pathlib.Path(__file__).parent / "data" / "frames"
    frame = cv2.imread(str(frames / "hud_1920x1080_slider100.png"))
    assert frame is not None

    templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    found = anchor.identify_hud(frame, templates, wood_templates=woods)
    assert found["profile"].name == "stock"

    x1, y1, x2, y2 = found["villagers"]
    binary = digits.to_binary(frame[y1:y2, x1:x2], digits.ICON_BOX_THRESHOLD)
    inked = [index for index, row in enumerate(binary) if row.max()]

    assert inked, "the band found no number at all"
    assert inked == list(range(inked[0], inked[-1] + 1)), (
        f"the band has ink on rows {inked} - a gap means it is taking in "
        "the banner art above the number")
