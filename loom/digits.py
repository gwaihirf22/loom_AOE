"""
Loom — Milestone 1, digit recognition.

Reads numbers out of small HUD crops by cutting them into individual glyphs
and matching each glyph against labelled reference images of 0-9.

I use template matching rather than an OCR engine like Tesseract because the
AoE2 HUD font is fixed and pixel-identical every frame: there are only ten
possible shapes, so comparing ten small images is both more accurate and much
faster than a general-purpose engine trained on prose.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np

from . import paths

# Every glyph is squashed to this size before comparing, so the same templates
# work whatever the HUD scale happens to be.
GLYPH_WIDTH = 14
GLYPH_HEIGHT = 20

# Below this correlation I refuse to guess.
MIN_MATCH_SCORE = 0.55

# Bright text on the wooden bar vs. colored text inside a dark icon box.
CLOCK_THRESHOLD = 220
ICON_BOX_THRESHOLD = 150


def _normalize(image):
    """Zero-mean, unit-variance version of an image, for correlation."""
    values = image.astype(np.float32)
    return (values - values.mean()) / (values.std() + 1e-6)


def load_digit_templates():
    """Load labelled digit templates as (label, normalized image) pairs.

    There is more than one template per digit: the game renders the same digit
    slightly differently depending on sub-pixel position, and keeping every
    variant makes matching more forgiving.
    """
    templates = []
    for path in sorted(glob.glob(str(paths.DIGIT_TEMPLATES_DIR / "*.png"))):
        label = int(os.path.basename(path).split("_")[0])
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        templates.append((label, _normalize(image)))

    if not templates:
        raise FileNotFoundError(f"No digit templates found in {paths.DIGIT_TEMPLATES_DIR}")
    return templates


def to_binary(region_bgr, threshold):
    """Turn a color crop into white-text-on-black."""
    grey = cv2.cvtColor(region_bgr, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
    return binary


def find_column_runs(binary):
    """Find groups of adjacent columns that contain any white pixel.

    Each run is one character. Returns a list of (start, end) column indices.
    """
    has_ink = binary.max(axis=0) > 0

    runs = []
    start = None
    for x, inked in enumerate(has_ink):
        if inked and start is None:
            start = x
        elif not inked and start is not None:
            runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, len(has_ink)))
    return runs


def extract_glyph(binary, start, end):
    """Crop one character and squash it to the standard glyph size."""
    column_slice = binary[:, start:end]

    # Trim blank rows so glyphs line up regardless of where they sat vertically.
    inked_rows = np.nonzero(column_slice.max(axis=1))[0]
    if len(inked_rows) == 0:
        return None
    trimmed = column_slice[inked_rows.min():inked_rows.max() + 1, :]

    return cv2.resize(trimmed, (GLYPH_WIDTH, GLYPH_HEIGHT),
                      interpolation=cv2.INTER_AREA)


def classify_glyph(glyph, templates):
    """Return (digit, score) for the best-matching template."""
    normalized = _normalize(glyph)

    best_label, best_score = None, -1.0
    for label, template in templates:
        # Normalized correlation: 1.0 is a perfect match.
        score = float((normalized * template).mean())
        if score > best_score:
            best_label, best_score = label, score

    return best_label, best_score


def read_digits(region_bgr, templates, threshold, min_glyph_width, max_runs=None):
    """Read a run of digits from a crop.

    Returns (digits, weakest_score), or (None, 0.0) if nothing was readable.
    Runs narrower than min_glyph_width are ignored — that is how colons in the
    clock get skipped, since they are far thinner than any digit.

    max_runs stops me reading past the value I want. The clock band also
    contains text like "(Normal - 1.7)", whose letters would fail to classify
    and abandon an otherwise good reading.
    """
    binary = to_binary(region_bgr, threshold)
    return read_binary(binary, templates, min_glyph_width, max_runs)


def read_binary(binary, templates, min_glyph_width, max_runs=None):
    """Read digits from an already black-and-white image.

    Split out from read_digits so callers that need a different way of turning
    colour into black-and-white - the yellow per-resource numbers, say - can do
    their own thresholding and still share the segmentation and matching.
    """
    runs = find_column_runs(binary)
    if max_runs is not None:
        runs = runs[:max_runs]

    digits = []
    weakest = 1.0
    for start, end in runs:
        if end - start < min_glyph_width:
            continue

        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue

        label, score = classify_glyph(glyph, templates)
        if score < MIN_MATCH_SCORE:
            return None, 0.0

        digits.append(label)
        weakest = min(weakest, score)

    if not digits:
        return None, 0.0
    return digits, weakest


def digits_to_int(digits):
    """[2, 2] -> 22"""
    value = 0
    for digit in digits:
        value = value * 10 + digit
    return value


def read_count(region_bgr, templates, min_glyph_width):
    """Read a villager count from inside a dark HUD icon box."""
    digits, score = read_digits(region_bgr, templates,
                               ICON_BOX_THRESHOLD, min_glyph_width)
    if digits is None:
        return None, 0.0
    return digits_to_int(digits), score


def read_clock_seconds(band_bgr, templates, min_glyph_width):
    """Read the HH:MM:SS clock and return it as total seconds.

    The band deliberately extends past the clock (it also catches text like
    "(Normal - 1.7)"), so I only trust the first six digits and reject
    anything that does not look like a complete time.
    """
    # HH:MM:SS is 8 characters — six digits and two colons.
    digits, score = read_digits(band_bgr, templates, CLOCK_THRESHOLD,
                                min_glyph_width, max_runs=8)
    if digits is None or len(digits) != 6:
        return None, 0.0

    hours = digits_to_int(digits[0:2])
    minutes = digits_to_int(digits[2:4])
    seconds = digits_to_int(digits[4:6])

    # Minutes and seconds above 59 mean I mis-segmented something.
    if minutes > 59 or seconds > 59:
        return None, 0.0

    return hours * 3600 + minutes * 60 + seconds, score
