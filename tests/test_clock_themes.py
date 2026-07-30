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
