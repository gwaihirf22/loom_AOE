"""
Loom — the resource bar's own woodwork is not a digit.

The villagers-on-this-resource numbers are read out of a strip that starts a
few reference pixels LEFT of each resource icon, because the number is not
centred the same way on every one. For food, gold and stone those few pixels
land on the previous icon's black box. Wood is the LEFTMOST resource, so they
land on the bar's end cap: brown wooden chrome, tall enough to survive the
digit-shape filter, and therefore a second "glyph" standing beside the real
one. read_one then saw two digits where the game had drawn one and refused
the whole crop.

The refusal was correct - that is the never-guess rule doing its job - so
the panel honestly showed a dash for wood while food, gold and stone read.
The fault was upstream, in what the mask let through.

MEASURED, because a threshold that separates "ink" from "not ink" has to be:
the mod draws these digits at blue EXACTLY 0 (min, median and max all zero
across every digit in a live frame) and the chrome starts at blue 61. The
gate was 110, above the chrome. Swept over 1686 anchored frames of the
capture corpus, moving it to 40 took wood from 97.0% to 99.8% read and left
food, gold and stone untouched at 99.8%.

The fixture is real pixels from captures/run_20260826_142630_annehk, a run
where the wood band failed on all 25 frames sampled. Its ground truth is
plain to the eye: brown chrome down the left, a bright yellow 0 on the right.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import cv2
import numpy as np
import pytest

from loom import digits, paths, resources

FIXTURE = paths.PROJECT_ROOT / "tests" / "data" / "villagers" / \
    "wood_band_annehk_chrome.png"

# The two populations, as measured off a live frame. Blue channel only,
# because that is the one that separates them.
DIGIT_BLUE = 0
CHROME_BLUE = 61


@pytest.fixture(scope="module")
def crop():
    image = cv2.imread(str(FIXTURE))
    assert image is not None, f"missing fixture: {FIXTURE}"
    return image


def test_the_wood_band_reads_through_the_bars_own_woodwork(crop):
    """The regression. This crop read as None for the life of the bug."""
    value = resources.read_one(crop, digits.load_digit_templates(), 5)

    assert value == 0


def test_the_chrome_is_not_kept_as_ink(crop):
    """One glyph in the crop, not two.

    Asserted on the mask rather than only on the answer, because a reader
    that returned 0 while still seeing two components would be right by
    luck - and the next crop, where the chrome happens to look like a 1,
    would not be.
    """
    mask = resources.yellow_mask(crop)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    assert count - 1 == 1, "the bar's end cap is still coming through as ink"
    # ...and the surviving one is the digit on the right, not the chrome,
    # which touches the left edge.
    assert stats[1, cv2.CC_STAT_LEFT] > 0


def test_the_gate_sits_between_the_two_populations():
    """The numbers the constant was chosen from, so a later edit has to
    argue with the measurement rather than with a bare number."""
    assert DIGIT_BLUE < resources.MAX_DIGIT_BLUE < CHROME_BLUE


@pytest.mark.parametrize("blue", [0, resources.MAX_DIGIT_BLUE - 1])
def test_ink_the_game_really_draws_is_kept(blue):
    """Yellow at the blue the digits actually use, and at the last level the
    gate admits.

    The upper case is derived from the constant rather than typed out. It
    was 39 when the gate was 40, and went red the moment the gate moved to
    30 - a test pinning today's number instead of the rule, which is the
    fault this project has written up more than once.
    """
    pixel = np.array([[[blue, 200, 240]]], dtype=np.uint8)

    assert resources.yellow_mask(np.repeat(np.repeat(pixel, 20, 0), 20, 1)).any()


@pytest.mark.parametrize("bgr", [(86, 133, 182), (61, 120, 170)])
def test_the_bars_woodwork_is_rejected(bgr):
    """The chrome's own colour, from the crop that broke the reader."""
    pixel = np.array([[list(bgr)]], dtype=np.uint8)

    assert not resources.yellow_mask(
        np.repeat(np.repeat(pixel, 20, 0), 20, 1)).any()
