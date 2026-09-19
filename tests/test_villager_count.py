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
    downstream will happily believe.

    The rule this disables is the ORIGINAL width test, restored here
    verbatim. is_character measures height alone now, so setting the
    height fraction out of reach no longer reproduces the bug - it refuses
    every run and reads nothing, which is a gap rather than a lie. The
    width shortcut is what produced the lie, so the width shortcut is what
    this puts back."""
    def width_only(binary, start, end, min_glyph_width, tallest):
        if end - start >= min_glyph_width:
            return True
        box = digits._run_box(binary, start, end)
        return box is not None and box[1] >= max(
            2, round(tallest * digits.COLON_HEIGHT_FRACTION))

    monkeypatch.setattr(digits, "COLON_HEIGHT_FRACTION", 99.0)
    monkeypatch.setattr(digits, "is_character", width_only)

    value, _score = digits.read_count(
        band("small_hud_1080p_18.png"), templates, SMALL_HUD_GATE)

    assert value == 8, "the fixture no longer reproduces the original bug"


def test_the_height_rule_refuses_rather_than_shrinks_the_number(templates,
                                                               monkeypatch):
    """And with height the only test, disabling it fails safe: nothing
    reads at all. A gap costs a poll; the 8 above costs the whole game."""
    monkeypatch.setattr(digits, "COLON_HEIGHT_FRACTION", 99.0)

    value, _score = digits.read_count(
        band("small_hud_1080p_18.png"), templates, SMALL_HUD_GATE)

    assert value is None


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


# ---- the band's RIGHT edge, and the bar art beyond it -------------------

# One band cut from a real 2560x1440 frame at HUD slider 115%, anchor
# scale 1.155, spanning REFERENCE x 14..70 - wider than either candidate
# right edge, so one fixture can be asked both questions.
WIDE_BAND = "annehk_at_1.155_ref14to70_22.png"
BAND_SCALE = 1.155
BAND_LEFT = 14                      # the reference x the crop starts at


def wide_band_columns(right_edge):
    """The 115% band cut to a candidate right edge, in reference pixels."""
    image = cv2.imread(str(DATA / WIDE_BAND))
    assert image is not None, f"missing fixture {WIDE_BAND}"
    return image[:, :int((right_edge - BAND_LEFT) * BAND_SCALE)]


def test_the_old_right_edge_took_in_the_bar_art():
    """The bug, kept as a fixture so it cannot come back unnoticed.

    A sliver of the bar's own art sits at reference x 58.2-60.0, and the
    band used to end at 60 - so it was clipped to two columns and drawn
    as tall as a digit. The height test that skips colons cannot see a
    full-height sliver, and classify_glyph called it a "9".

    Asserted as the FAULT rather than as a number nobody can check: 22
    villagers read as 229, which is the reading the player actually got.
    """
    band = wide_band_columns(60)
    templates = digits.load_digit_templates()
    value, _score = digits.read_count(band, templates, 6)
    assert value == 229, (
        "this fixture exists to hold the misread; if it no longer "
        "reproduces, the fixture has been replaced rather than the bug "
        "fixed elsewhere")


def test_the_band_ends_clear_of_the_bar_art():
    """And the fix, on the same pixels.

    Trimming the RIGHT edge is what makes this safe. The count is
    right-aligned - measured on these frames it occupies reference x
    30-51 whether it reads "3" or "22" - so it grows LEFTWARD and the
    left edge is the one that must stay generous. Cutting that is what
    once clipped the leading digit of "12" and reported 2.
    """
    templates = digits.load_digit_templates()
    value, score = digits.read_count(
        wide_band_columns(hud.ANNEHK.villager_region[2]), templates, 6)
    assert value == 22
    assert score >= digits.MIN_MATCH_SCORE


def test_the_right_edge_sits_in_the_gap_rather_than_on_a_number():
    """The rule, not the constant.

    A right edge is right when it falls between the number and the art -
    not because it is 55. Measured from the fixture itself: the number's
    ink ends and the sliver's begins, and the profile's edge has to be
    strictly between them with room on both sides. That still holds if
    the band is ever re-cut, and fails the moment it creeps back.
    """
    image = cv2.imread(str(DATA / WIDE_BAND))
    binary = digits.to_binary(image, digits.ICON_BOX_THRESHOLD)
    runs = digits.find_column_runs(binary)
    assert len(runs) >= 3, "the wide fixture should hold digits AND the art"

    # In reference pixels, so the assertion is about the HUD rather than
    # about this one frame's size.
    number_ends = BAND_LEFT + runs[-2][1] / BAND_SCALE
    art_begins = BAND_LEFT + runs[-1][0] / BAND_SCALE
    edge = hud.ANNEHK.villager_region[2]
    assert number_ends < edge < art_begins, (
        f"the band's right edge {edge} is not in the gap between the "
        f"number (ends {number_ends:.1f}) and the bar art "
        f"(begins {art_begins:.1f})")
    assert edge - number_ends >= 3, "too little room for the number"
    assert art_begins - edge >= 3, "too little room before the art"
