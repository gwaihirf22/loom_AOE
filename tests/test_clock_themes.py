"""
Loom — the clock must read on every architecture theme.

The clock band sits on the civ's architecture-set border artwork, which
differs per civ - and light borders (Portuguese, Vietnamese and friends)
glint brighter than any plain brightness threshold. That broke the
six-digit segmentation, which silently disabled new-game detection on
those civs' games. These fixtures are cut from the actual games that
exposed it: one dark East Asian border (always worked), two light stone
borders (the failures), and one band from a civ whose border reads dark
(the must-never-regress case).

The fixture bands from recorded games came through screenshot scaling, so
they also exercise the soft second pass of the white mask.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import numpy as np
import pytest

from loom import digits

DATA = pathlib.Path(__file__).parent / "data" / "clock"


def band(name):
    image = cv2.imread(str(DATA / name))
    assert image is not None, f"missing fixture {name}"
    return image


@pytest.fixture(scope="module")
def templates():
    return digits.load_digit_templates()


@pytest.mark.parametrize("name,expected,glyph", [
    ("live_dark_bar_1295.png", 1295, 6),   # the live game: never regress
    ("red_dark_theme_10.png", 10, 4),      # dark border, always worked
    ("yellow_stone_27.png", 27, 4),        # light stone: the bug
    ("yellow_stone_21.png", 21, 4),        # light stone: the bug
    # Live macOS bands at HUD scale 1.46, saved by the lag probe when the
    # reader refused them. Two defects, one corpus: the over-tall band put
    # the shape filter's height bar at exactly glyph height, so digits
    # vanished frame to frame (_fit_clock_rows is the fix), and the 4K "5"
    # scored 0.54 against templates cut from another renderer (the 5_3
    # variant is the fix). One in five live polls refused a legible clock
    # until both landed. A leading-junk retry was also tried and removed:
    # with the fitted band it rescued nothing these fixtures don't cover.
    ("mac_border_junk_420.png", 420, 6),   # read even before the fixes
    ("mac_border_junk_471.png", 471, 6),   # the vanished "5"
    ("mac_border_junk_473.png", 473, 6),   # ditto, neighbouring frame
    ("mac_border_junk_475.png", 475, 6),   # ditto
    # 1920x1080, Anne_HK, anchor scale 0.735 - the smallest HUD Loom had
    # ever been asked to read, and it read NO clock at all: 0 of 185
    # consecutive live frames. Everything else on that HUD was fine
    # (villagers, population, the whole production queue), which is what
    # made it look like a clock that was merely "off" rather than a clock
    # that never once arrived. Four separate things had to be true at this
    # size, each measured over those 185 frames: the templates are scored
    # at the glyph's own size as well as stretched (2 frames without),
    # narrow-but-full-height runs count as digits so a 3px "1" is not
    # mistaken for a colon (10 frames without), hollow "0"s split by the
    # threshold are rejoined (one frame read backwards without), and a
    # third, fainter white pass exists at all (2 frames without).
    ("small_hud_1080p_100.png", 100, 4),
    ("small_hud_1080p_161.png", 161, 4),
])
def test_clock_reads_on_every_theme(templates, name, expected, glyph):
    value, score = digits.read_clock_seconds(band(name), templates, glyph)
    assert value == expected
    assert score >= digits.MIN_MATCH_SCORE


def test_live_population_still_reads(templates):
    # The population band shares the white mask; the live reading must
    # survive the theme fix.
    current, cap = digits.read_population(band("live_dark_bar_1295_pop.png"),
                                          templates, 6)
    assert (current, cap) == (35, 45)


def test_white_mask_drops_warm_highlights():
    # Stone glints are bright but warm; text is bright and colorless. Build
    # a synthetic band with both and check only the "text" column survives.
    image = np.zeros((20, 30, 3), np.uint8)
    image[4:16, 5:10] = (250, 250, 250)      # white column: keep
    image[4:16, 20:25] = (170, 235, 250)     # bright warm stone: drop
    mask = digits.white_mask(image)
    assert mask[10, 7] == 255
    assert mask[10, 22] == 0


def test_white_mask_drops_speck_noise():
    # A lone glint that passes the color test is not shaped like a
    # character and must not become a column run.
    image = np.zeros((20, 30, 3), np.uint8)
    image[4:16, 5:10] = (250, 250, 250)      # character-sized: keep
    image[9, 20] = (255, 255, 255)           # single-pixel glint: drop
    mask = digits.white_mask(image)
    assert mask[10, 7] == 255
    assert mask[9, 20] == 0


def test_trailing_text_cannot_poison_the_clock(templates):
    # The band includes "(Normal - 1.7)..." after the time. Parsing stops
    # at six digits, so the letters never get a vote - but a garbage run
    # BEFORE six digits still refuses, per the never-guess rule.
    value, _ = digits.read_clock_seconds(band("red_dark_theme_10.png"),
                                         templates, 4)
    assert value == 10


# ---- the small-HUD read, mechanism by mechanism ----------------------------
#
# 1920x1080 is the smallest HUD Loom has been asked to read, and it read no
# clock at all: 0 of 185 consecutive live frames, while villagers, population
# and the production queue on the same frames were all correct. Three things
# had to change for it, and each of the tests below removes one and watches
# the read die - so a later reader can tell at a glance which lines are
# load-bearing rather than guessing from the diff.

SMALL = "small_hud_1080p_161.png"
SMALL_GLYPH_WIDTH = 4


def test_the_small_hud_reads(templates):
    assert digits.read_clock_seconds(
        band(SMALL), templates, SMALL_GLYPH_WIDTH)[0] == 161


def test_it_needs_the_faint_white_pass(templates, monkeypatch):
    """At this size antialiasing pulls whole strokes under both of the
    older gates - the bottom bar of the "2" in 00:02:41 disappears, and
    what is left scores as a "7" just under the match gate."""
    monkeypatch.setattr(digits, "WHITE_PASSES",
                        (digits.WHITE_STRICT, digits.WHITE_SOFT))

    assert digits.read_clock_seconds(
        band(SMALL), templates, SMALL_GLYPH_WIDTH)[0] is None


def test_it_needs_narrow_but_tall_runs_to_count_as_digits(templates,
                                                          monkeypatch):
    """The width gate exists to skip the colons, and at this size it also
    skips the "1": 3px wide against a gate of 4. Height is what tells them
    apart - the "1" stands as tall as every other digit."""
    monkeypatch.setattr(digits, "COLON_HEIGHT_FRACTION", 99.0)

    assert digits.read_clock_seconds(
        band(SMALL), templates, SMALL_GLYPH_WIDTH)[0] is None


def test_it_needs_glyphs_scored_at_their_own_size(templates, monkeypatch):
    """Stretching a 6x12 glyph into the 14x20 template box invents detail
    the screen never drew. Shrinking the template to the glyph instead
    compares what the game actually rendered."""
    as_shipped = digits.classify_glyph

    def stretched_only(glyph, tmpl, native=None):
        return as_shipped(glyph, tmpl)

    monkeypatch.setattr(digits, "classify_glyph", stretched_only)

    assert digits.read_clock_seconds(
        band(SMALL), templates, SMALL_GLYPH_WIDTH)[0] is None


def test_a_band_of_split_zeros_never_reads_as_a_marathon():
    """00:00:04 at the start of a match, on the small HUD where every
    hollow "0" splits down the middle.

    Read straight, the halves classify as confident "1"s and the band comes
    out as "10:01:00" - six digits, minutes and seconds both legal, 36060
    seconds of nonsense that arrived on about one live frame in eight. A
    clock Loom declines to read costs one poll; a clock it reads wrongly
    desynchronises the whole build, so the hours field is bounded.

    The exact seconds are deliberately not asserted: this band still reads
    a couple of seconds fast on its last digit, which StableClock absorbs.
    What must never come back is the ten-hour answer.
    """
    templates = digits.load_digit_templates()

    value, _score = digits.read_clock_seconds(
        band("small_hud_1080p_split_zeros.png"), templates, SMALL_GLYPH_WIDTH)

    assert value is None or value < 3600, f"read {value}s from a fresh match"


def test_the_hours_bound_is_the_thing_that_stops_it(monkeypatch):
    """Pinned separately so the bound cannot be quietly widened: without
    it, this very band is where the 36060 came from."""
    monkeypatch.setattr(digits, "MAX_GAME_HOURS", 99)
    templates = digits.load_digit_templates()

    value, _score = digits.read_clock_seconds(
        band("small_hud_1080p_split_zeros.png"), templates, SMALL_GLYPH_WIDTH)

    assert value == 36060, "the misread this guard exists for has changed"
