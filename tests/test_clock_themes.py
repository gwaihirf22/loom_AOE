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
    what is left scores as a "7" just under the match gate.

    The neck-faint gate is pushed out of reach too: it rescues this same
    fixture by another route, and with it live the test would pass while
    the gate it exists to prove load-bearing quietly stopped mattering."""
    monkeypatch.setattr(digits, "WHITE_PASSES",
                        (digits.WHITE_STRICT, digits.WHITE_SOFT))
    monkeypatch.setattr(digits, "CLOCK_NECK_FAINT", 999)

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


# The macOS renderer, windowed at 1080p-in-points on a Retina display and
# captured at the full 2x backing. Its antialiasing reran the small-HUD
# story thirty grey-levels lower: the NECK of the seconds-tens "2" in
# 00:02:23 renders at 139-146 while the same frame's minutes "2" stays
# above 170, so under every older gate the bottom bar detaches, the shape
# filter deletes it as a glint, and the topless "2" scores as a "7" -
# refused, which read from outside as Loom losing sight of the game for
# ten seconds of every minute.

MAC_NECK = "mac_windowed_neck_143.png"
MAC_NECK_GLYPH_WIDTH = 6


def test_the_mac_windowed_band_reads(templates):
    assert digits.read_clock_seconds(
        band(MAC_NECK), templates, MAC_NECK_GLYPH_WIDTH)[0] == 143


def test_it_needs_the_neck_faint_gate(templates, monkeypatch):
    """Remove the gate and this band dies exactly as it did live - which is
    what makes the gate load-bearing rather than decorative."""
    monkeypatch.setattr(digits, "CLOCK_NECK_FAINT", 999)

    assert digits.read_clock_seconds(
        band(MAC_NECK), templates, MAC_NECK_GLYPH_WIDTH)[0] is None


def large_hud_templates():
    """The template set as it was before any were cut at 1080p.

    Two different repairs cover this fixture now and each has to be tested
    against the problem it was built for, or one silently stops being
    exercised: scoring a glyph at its own size (below), and templates cut
    at the rendering the glyph came from (test_small_templates_read_it_
    stretched). With both in place the fixture reads either way, which is
    the point - but it also means the plain read no longer proves either.
    """
    import glob
    import os

    import cv2

    from loom import paths
    out = []
    for path in sorted(glob.glob(str(paths.DIGIT_TEMPLATES_DIR / "*.png"))):
        if "1080p" in os.path.basename(path):
            continue
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        out.append((int(os.path.basename(path).split("_")[0]),
                    digits._normalize(image)))
    return out


def test_it_needs_glyphs_scored_at_their_own_size(monkeypatch):
    """Stretching a 6x12 glyph into the 14x20 template box invents detail
    the screen never drew. Shrinking the template to the glyph instead
    compares what the game actually rendered.

    Against the LARGE-HUD templates, which is the set that made this
    necessary: with only those, and only the stretching path, the small
    HUD does not read at all. The neck-faint gate is pushed out of reach
    for the same reason as in test_it_needs_the_faint_white_pass: it
    reshapes this fixture's segmentation enough to rescue the stretched
    read, and this test is about the scoring path, not the mask."""
    as_shipped = digits.classify_glyph

    def stretched_only(glyph, tmpl, native=None):
        return as_shipped(glyph, tmpl)

    monkeypatch.setattr(digits, "classify_glyph", stretched_only)
    monkeypatch.setattr(digits, "CLOCK_NECK_FAINT", 999)

    assert digits.read_clock_seconds(
        band(SMALL), large_hud_templates(), SMALL_GLYPH_WIDTH)[0] is None


def test_small_templates_read_it_stretched(templates, monkeypatch):
    """The other repair, on its own terms. Templates cut at 1080p are
    compared against a 1080p glyph in the same box, so the stretching does
    the same thing to both and the detail it invents matches - no
    shrink-to-fit needed."""
    as_shipped = digits.classify_glyph

    def stretched_only(glyph, tmpl, native=None):
        return as_shipped(glyph, tmpl)

    monkeypatch.setattr(digits, "classify_glyph", stretched_only)

    assert digits.read_clock_seconds(
        band(SMALL), templates, SMALL_GLYPH_WIDTH)[0] == 161


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


def test_the_ten_hour_misread_cannot_be_produced_any_more(monkeypatch):
    """This band once read 36060s - four hollow zeros split into "1"s and
    "10:01:00" got out through the first pass that parsed. Two guards were
    layered over it (the most-confident-answer sweep, then MAX_GAME_HOURS
    as backstop), and this test used to pin WHICH one caught it first.

    Then the hollow-half merge reached every pass - the previous version
    of this test carried its own tripwire ("no pass misreads it any more;
    this test is moot") and the tripwire fired: with split zeros rejoined
    before classification, every single pass now reads this band as the
    6s it really shows. So what is pinned now is the stronger fact: the
    misread cannot be PRODUCED, on any pass, with the hours bound pushed
    out of the way - and the correct read comes out the front door.
    """
    monkeypatch.setattr(digits, "MAX_GAME_HOURS", 99)
    templates = digits.load_digit_templates()

    value, _score = digits.read_clock_seconds(
        band("small_hud_1080p_split_zeros.png"), templates, SMALL_GLYPH_WIDTH)

    assert value == 6, f"read {value}s from a band showing 00:00:06"

    fitted = digits._fit_clock_rows(band("small_hud_1080p_split_zeros.png"))
    for min_channel, max_spread in digits.clock_passes():
        got, _s = digits._parse_clock(
            digits.white_mask(fitted, min_channel, max_spread),
            templates, SMALL_GLYPH_WIDTH)
        assert got != 36060, (
            f"pass ({min_channel},{max_spread}) resurrected the "
            "ten-hour misread")


# The Transparent UI mod removes the HUD backdrop entirely, so the clock is
# drawn straight onto the map: grass, stone, buildings, units, whatever the
# camera happens to be over. The glyphs do not change - only what is behind
# them, and that changes every frame.
#
# It cost the clock two thirds of its reads. Across the four matched 1080p
# recordings of one game: stock 284/284 and Anne_HK 284/285 without the mod,
# against 210/284 and 102/287 with it. Both skins, so it is the mod and not
# the skin.

TERRAIN = "transparent_terrain_417.png"
TERRAIN_SECONDS = 417


def test_the_clock_reads_through_the_transparent_ui_mod(templates):
    assert digits.read_clock_seconds(
        band(TERRAIN), templates, SMALL_GLYPH_WIDTH)[0] == TERRAIN_SECONDS


def test_it_needs_the_tight_colour_spread(templates, monkeypatch):
    """Terrain is bright and COLOURED; the clock is bright and is not.

    Measured on the pixels each side owns: the clock's ink has a colour
    spread of 0 at the median and 1 at its worst, while bright terrain runs
    to 10 at the median and 57 at the ninetieth. WHITE_MAX_SPREAD's 45 was
    measured against the civ border artwork and is right for that; against
    terrain it admits the map as ink and glues runs 26 and 38 pixels wide
    together, where a digit is 6.

    Removing the tight pass leaves the loose one, which is what shipped
    before, and this band goes back to being unreadable.

    The hollow-half merge's WIDENED ink floor is rolled back to the old
    bar floor here - not disabled outright, which would remove the merges
    the pre-change code made too and manufacture a third behaviour (the
    band then reads a confident wrong 0). With the old floor restored,
    the merge behaves exactly as it did when this proof was measured, and
    the arcs the wider floor rescues stay unmerged - the same
    second-rescuer problem the neck-faint gate posed to the small-HUD
    tests, answered the same way: this test is about the spread.
    """
    monkeypatch.setattr(digits, "CLOCK_TIGHT_SPREAD", digits.WHITE_MAX_SPREAD)
    monkeypatch.setattr(digits, "HOLLOW_HALF_INK", digits.BAR_INK)

    assert digits.read_clock_seconds(
        band(TERRAIN), templates, SMALL_GLYPH_WIDTH)[0] is None


def test_the_tight_pass_is_tried_before_the_loose_one(templates):
    """Order is the whole design, not tidiness.

    A terrain-contaminated band has to meet a colourless pass BEFORE a
    loose one sees it, or the loose pass answers first with the map mixed
    into the digits. The looser passes still exist because the theme
    fixtures need them - those came through screenshot scaling, which adds
    chroma noise the game window never has - but they answer last.
    """
    passes = digits.clock_passes()
    tight = [i for i, (_gate, spread) in enumerate(passes)
             if spread == digits.CLOCK_TIGHT_SPREAD]
    loose = [i for i, (_gate, spread) in enumerate(passes)
             if spread == digits.WHITE_MAX_SPREAD]
    assert tight and loose, passes
    assert max(tight) < min(loose)
    # Every brightness gate is still tried at both spreads - and the neck
    # gate ONLY at the tight one, on purpose: at that brightness a loose
    # spread would admit warm terrain mid-tones as ink.
    assert len(tight) == len(digits.WHITE_PASSES) + 1
    assert len(loose) == len(digits.WHITE_PASSES)
    assert (digits.CLOCK_NECK_FAINT, digits.WHITE_MAX_SPREAD) not in passes
