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
