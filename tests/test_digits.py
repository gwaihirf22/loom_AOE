"""
Tests for digit recognition.

This needs no captured frames: the ten glyph templates are themselves the best
available test data. If a template file is ever corrupted, replaced, or saved
under the wrong name, feeding it back through the classifier catches it at
once.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np
import pytest

from loom import digits, paths


def template_files():
    return sorted(glob.glob(str(paths.DIGIT_TEMPLATES_DIR / "*.png")))


def test_templates_are_present():
    files = template_files()
    assert files, "no digit templates found"

    labels = {int(os.path.basename(path).split("_")[0]) for path in files}
    assert labels == set(range(10)), f"missing digits: {set(range(10)) - labels}"


@pytest.mark.parametrize("path", template_files())
def test_each_template_classifies_as_itself(path):
    """A template that does not recognize itself is corrupt or mislabelled."""
    templates = digits.load_digit_templates()
    expected = int(os.path.basename(path).split("_")[0])

    glyph = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    label, score = digits.classify_glyph(glyph, templates)

    assert label == expected, f"{os.path.basename(path)} read as {label}"
    assert score > 0.9, f"{os.path.basename(path)} matched itself weakly: {score:.2f}"


def test_digits_to_int():
    assert digits.digits_to_int([2, 2]) == 22
    assert digits.digits_to_int([0, 0, 7]) == 7
    assert digits.digits_to_int([9]) == 9


# --- the population display ------------------------------------------------
# Real bands cut from capture frames, values verified by eye. The slash is as
# wide as a digit, so these also pin the diagonality test that separates it.

POP_BANDS = {
    "pop_9_15": (9, 15),
    "pop_21_25": (21, 25),
    "pop_capped": (200, 200),
    # The housed style: yellow digits on a bright olive box. The grey pass
    # goes blind on it, so this pins the brightest-channel fallback.
    "pop_housed_25_25": (25, 25),
    # Portuguese (light architecture set): white text with NO dark box,
    # straight on bright stone, and the slash brushing the next digit -
    # pins the pinch-split. This style was 39% of a real game's frames.
    "pop_stone_11_20": (11, 20),
    # The warning-yellow text the game switches to as headroom runs out -
    # exactly when the housed logic most needs the number.
    "pop_warnyellow_4_5": (4, 5),
}


@pytest.mark.parametrize("name,expected", POP_BANDS.items())
def test_read_population(name, expected):
    band = cv2.imread(str(paths.PROJECT_ROOT / "tests" / "data" / "queue"
                          / f"{name}.png"))
    assert band is not None
    templates = digits.load_digit_templates()
    assert digits.read_population(band, templates, 6) == expected


def test_population_plausibility():
    # Cap only comes from houses/TCs/castles/lobby limits - all multiples of
    # 5 - so 23 is a misread by construction. Current over cap is REAL
    # (houses burn down), so 21/20 must pass.
    assert digits._plausible_population(21, 25)
    assert digits._plausible_population(21, 20)
    assert digits._plausible_population(200, 200)
    assert not digits._plausible_population(21, 23)
    assert not digits._plausible_population(21, 3)
    assert not digits._plausible_population(21, 0)
    assert not digits._plausible_population(21, 505)
    assert not digits._plausible_population(501, 200)


# ---- a "1" is a bar, and a bar defeats template matching -------------------
#
# The live bug: on the stock HUD, "21/30" was read as "2/30" with total
# confidence. The "1" comes back three pixels wide against a width gate of
# four, so it was skipped - and skipping is silent. Widening the gate is not
# the fix either: extract_glyph stretches every run to one 14x20 box, and a
# 3px bar stretched to 14 columns becomes a solid block that _normalize
# divides by a standard deviation of nearly zero. Measured, such a run scores
# 0.27 against the "1" template, less than half of MIN_MATCH_SCORE.
#
# So a "1" is recognised by shape, exactly as the slash already was.

def bar_mask(width, height, band_height=20, gap=2):
    """A solid vertical bar in a band, like the game's "1"."""
    mask = np.zeros((band_height, width + 2 * gap), np.uint8)
    top = (band_height - height) // 2
    mask[top:top + height, gap:gap + width] = 255
    return mask


def test_a_three_pixel_bar_is_recognised_as_a_one():
    """The exact geometry that was being dropped."""
    mask = bar_mask(width=3, height=13)
    runs = digits.find_column_runs(mask)
    boxes, tallest = digits._bar_context(mask, runs)

    assert digits._is_bar(boxes[runs[0]], tallest)


def test_a_wide_glyph_is_not_a_bar():
    """Every other digit is wider than it is bar-shaped, and mostly hollow."""
    mask = np.zeros((20, 12), np.uint8)
    mask[4:17, 2:10] = 255
    mask[7:14, 4:8] = 0            # hollow it out, like a 0 or an 8
    runs = digits.find_column_runs(mask)
    boxes, tallest = digits._bar_context(mask, runs)

    assert not digits._is_bar(boxes[runs[0]], tallest)


def test_a_slash_is_not_a_bar():
    """A slash is narrow too, and must stay the separator rather than become
    a digit. Measured on live frames its ink fraction is 0.22-0.27 against a
    bar's 0.77-0.97, so ink is what tells them apart."""
    mask = np.zeros((20, 8), np.uint8)
    for step in range(13):
        mask[16 - step, 1 + step // 3] = 255
    runs = digits.find_column_runs(mask)
    boxes, tallest = digits._bar_context(mask, runs)

    assert not digits._is_bar(boxes[runs[0]], tallest)


def test_a_speck_of_noise_is_not_a_one():
    """The rule compares a run against the tallest run BESIDE it rather than
    against a pixel count - a fixed number here would be the pixel-constant
    bug again, wrong at some HUD scale nobody tested."""
    mask = np.zeros((20, 14), np.uint8)
    mask[4:17, 2:5] = 255          # a real bar
    mask[9:11, 9:10] = 255         # a two-pixel speck beside it
    runs = digits.find_column_runs(mask)
    boxes, tallest = digits._bar_context(mask, runs)

    assert digits._is_bar(boxes[runs[0]], tallest), "the bar"
    assert not digits._is_bar(boxes[runs[1]], tallest), "the speck"


def test_the_bar_rule_scales_with_the_hud():
    """A bar twice the size is still a bar; the rule is all ratios."""
    for height, width in ((13, 3), (26, 6), (7, 2)):
        mask = bar_mask(width=width, height=height, band_height=height + 6)
        runs = digits.find_column_runs(mask)
        boxes, tallest = digits._bar_context(mask, runs)
        assert digits._is_bar(boxes[runs[0]], tallest), (height, width)


# The live bands this was found on, cut straight out of a 2560x1440 stock
# capture at the scale the game actually drew them - not rescaled, because
# rescaled fixtures are what taught this project that fixture evidence is not
# live evidence. Read at the stock profile's own gate of 4, which is the
# configuration that produced the wrong answer.
STOCK_POP_BANDS = {
    "pop_stock_21_30": (21, 30),      # read as 2/30 before the bar rule
    "pop_stock_11_15": (11, 15),      # read as 1/15
}


@pytest.mark.parametrize("name,expected", STOCK_POP_BANDS.items())
def test_a_narrow_one_is_not_dropped(name, expected):
    """The end-to-end regression, on real pixels.

    Both of these contain a "1" three pixels wide, under a gate of four. The
    old reader skipped it silently and reported the rest with total
    confidence, so "21/30" became "2/30" - a plausible number, which is what
    made it dangerous. It also came and went with the value on screen, since
    the same glyph renders 3px or 4px depending on sub-pixel position, so it
    looked like the HUD flickering rather than like a bug.
    """
    band = cv2.imread(str(paths.PROJECT_ROOT / "tests" / "data" / "queue"
                          / f"{name}.png"))
    assert band is not None, "missing regression fixture"
    templates = digits.load_digit_templates()

    assert digits.read_population(band, templates, 4, 10) == expected


# ---- a hollow digit is not two "1"s ----------------------------------------
#
# The bar rule's own regression, found live one day after the rule shipped:
# the last-resort brightness pass eroded a "0" to its two side strokes, the
# bar rule read each stroke as a "1", and "10/15" became "111/15" - which
# passed the old unbounded plausibility check and announced HOUSED five
# villagers before the wall.

def test_a_hollow_zero_is_not_read_as_two_ones():
    """The offending band, cut straight from the live frame. With the pair
    merged back into one run it classifies as the outline it is, and the
    band reads its true value."""
    band = cv2.imread(str(paths.PROJECT_ROOT / "tests" / "data" / "queue"
                          / "pop_stock_hollow_10_15.png"))
    assert band is not None, "missing regression fixture"
    templates = digits.load_digit_templates()

    assert digits.read_population(band, templates, 3, 10) == (10, 15)


def test_two_real_ones_do_not_merge():
    """The rule must narrow, not blunt: adjacent "1"s in a real "11" stand a
    whole digit-spacing apart and stay two digits. This is yesterday's
    fixture, whose leading "1" the bar rule exists to keep."""
    band = cv2.imread(str(paths.PROJECT_ROOT / "tests" / "data" / "queue"
                          / "pop_stock_11_15.png"))
    assert band is not None
    templates = digits.load_digit_templates()

    assert digits.read_population(band, templates, 4, 10) == (11, 15)


def test_bars_a_digit_spacing_apart_stay_separate():
    """The merge gate in isolation: gap below the threshold merges, gap at
    real digit spacing does not."""
    mask = np.zeros((20, 30), np.uint8)
    mask[4:17, 4:6] = 255          # bar
    mask[4:17, 8:10] = 255         # bar, gap 2: one hollow digit's sides
    mask[4:17, 20:22] = 255        # bar, far away: its own digit
    runs = digits.find_column_runs(mask)
    _, tallest = digits._bar_context(mask, runs)

    merged = digits._merge_hollow_pairs(mask, runs, tallest)

    assert merged == [(4, 10), (20, 22)]


def test_a_bar_next_to_a_wide_glyph_does_not_merge():
    """Only PAIRS of bars merge: a "1" standing close to a real digit must
    not be swallowed into it."""
    mask = np.zeros((20, 30), np.uint8)
    mask[4:17, 4:6] = 255          # a bar
    mask[4:17, 9:17] = 255         # a wide solid block right beside it
    runs = digits.find_column_runs(mask)
    _, tallest = digits._bar_context(mask, runs)

    merged = digits._merge_hollow_pairs(mask, runs, tallest)

    assert merged == runs


# ---- the plausibility overshoot bound ---------------------------------------

def test_current_may_exceed_cap_by_a_burned_house_or_three():
    """21/20 is REAL - houses burn down - and must keep passing."""
    assert digits._plausible_population(21, 20)
    assert digits._plausible_population(30, 20)


def test_a_phantom_digit_overshoot_is_a_misread():
    """The live number: 111/15. No quantity of burned houses explains a
    current seven times the cap; that is a phantom digit, and believing it
    announced HOUSED five villagers early."""
    assert not digits._plausible_population(111, 15)
    assert not digits._plausible_population(200, 25)
