"""
Tests for reading villagers-per-resource.

The parts that need a real screenshot (matching the icons) are checked by hand
against captured frames, not here. What these cover is the image cleanup that
turns a noisy yellow crop into readable digits - which is where the reading
went wrong first time, so it is worth pinning down.
"""

import numpy as np

from loom import resources


def test_yellow_mask_keeps_yellow_and_drops_white():
    """The whole reason for a colour mask: the wooden bar's white highlights
    must not be read as digits, only the yellow numbers."""
    crop = np.zeros((20, 30, 3), dtype=np.uint8)
    # A yellow blob (BGR: low blue, high green, high red).
    crop[4:16, 4:12] = (40, 220, 250)
    # A white blob right next to it.
    crop[4:16, 18:26] = (245, 245, 245)

    mask = resources.yellow_mask(crop)
    assert mask[10, 8] == 255, "yellow should survive"
    assert mask[10, 22] == 0, "white should be dropped"


def test_keep_digit_shapes_removes_thin_horizontal_lines():
    """A bar-highlight line spans every column, which would merge the digits
    into one blob. It is short, so the height filter drops it."""
    mask = np.zeros((20, 40), dtype=np.uint8)
    mask[10:11, 0:40] = 255          # a 1px-tall line across the whole width

    cleaned = resources._keep_digit_shapes(mask)
    assert cleaned.max() == 0, "a thin line is not digit-shaped"


def test_keep_digit_shapes_keeps_a_tall_blob():
    mask = np.zeros((20, 40), dtype=np.uint8)
    mask[3:18, 10:18] = 255          # a digit-height block

    cleaned = resources._keep_digit_shapes(mask)
    assert cleaned.max() == 255, "a tall blob is a plausible digit"


# Matching the icons themselves needs real screenshots, so it is validated
# against captured frames by hand rather than here: across 121 frames, 108 read
# all four resources with the per-resource sum never exceeding the total
# villager count, and the rest read fewer rather than reading anything wrong.
