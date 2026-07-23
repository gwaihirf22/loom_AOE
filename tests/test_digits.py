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
