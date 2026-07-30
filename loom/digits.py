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


# What counts as WHITE text: every channel at least this bright, and the
# channels no further apart than this. Stone highlights are bright but warm
# (red and green over blue), so the spread test is what separates them from
# the genuinely colorless clock digits. Two brightness gates: the strict one
# suits crisp native-resolution text and keeps adjacent glyphs separated;
# the soft one recovers text that has been through scaling or compression
# (antialiased edges dim below the strict gate). Readers try strict first
# and only fall back when nothing validated - each pass keeps every
# validation rule, so the fallback can fill a gap but never invent.
WHITE_STRICT = 220
WHITE_SOFT = 190
WHITE_PASSES = (WHITE_STRICT, WHITE_SOFT)
WHITE_MAX_SPREAD = 45


def white_mask(band_bgr, min_channel=WHITE_STRICT):
    """White-on-black image of just the WHITE pixels in a crop.

    A plain brightness threshold only ever worked by luck: the clock band
    sits on the civ's architecture-set border artwork, and the civs I had
    played all happened to have dark ones. Light sets (Portuguese,
    Vietnamese and friends) have highlights bright enough to pass any
    brightness gate, which speckles the binary and breaks the six-digit
    segmentation; that is why Loom never saw some games start. White is
    bright AND colorless, and the stone is bright but warm - so test both,
    the same move the per-resource reader makes for its yellow digits.
    """
    blue = band_bgr[:, :, 0].astype(int)
    green = band_bgr[:, :, 1].astype(int)
    red = band_bgr[:, :, 2].astype(int)

    lowest = np.minimum(np.minimum(blue, green), red)
    spread = np.maximum(np.maximum(blue, green), red) - lowest
    is_white = (lowest >= min_channel) & (spread <= WHITE_MAX_SPREAD)
    return _keep_text_shapes((is_white * 255).astype(np.uint8))


def _keep_text_shapes(mask):
    """Drop mask components that are not shaped like characters.

    Texture glints that survive the color test are tiny specks or long thin
    ridges; characters are a decent fraction of the band tall and narrower
    than a quarter of it. Connected components make that a shape test, the
    same cleanup resources.py uses on the wooden bar's highlight lines.
    """
    if mask.max() == 0:
        return mask
    height, width = mask.shape
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for index in range(1, count):
        x, y, w, h, area = stats[index]
        if h >= height * 0.35 and w <= width * 0.25:
            cleaned[labels == index] = 255
    return cleaned


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


# A glyph whose ink clearly falls from upper-right to lower-left is the
# population display's slash. Unlike the clock's colon it is as WIDE as a
# digit, so it cannot be skipped by width - but no digit is diagonal, so the
# centroid drop between its halves separates it cleanly (slash +0.44, digits
# within +/-0.05 - threshold in the middle).
SLASH_DIAGONALITY = 0.25


def _diagonality(glyph):
    """How much a glyph's ink rises to the right, as a fraction of height."""
    height, width = glyph.shape
    left = glyph[:, :width // 2]
    right = glyph[:, width - width // 2:]

    def centroid_y(half):
        ys = np.nonzero(half)[0]
        return ys.mean() if len(ys) else height / 2

    return (centroid_y(left) - centroid_y(right)) / height


def _plausible_population(current, cap):
    """Could this pair actually appear on the HUD?

    Pop cap only ever comes from houses (+5), Town Centres and castles (also
    multiples of 5), or the lobby's population limit (multiples of 25) - so a
    cap that is not a multiple of 5 is necessarily a misread. Current is NOT
    required to fit under the cap: houses burn down, and the game then shows
    something like 21/20.
    """
    return cap % 5 == 0 and 5 <= cap <= 500 and 0 <= current <= 500


# When the player is housed the game recolours the population display:
# yellow digits on a bright olive box. A grey threshold that isolates the
# normal white text goes blind on it - at the exact moment the number
# matters most - so the housed style gets its own pass on the brightest
# channel. 245 specifically: at 235 the bolder yellow slash bleeds into the
# digit beside it and they merge into one unreadable run.
HOUSED_STYLE_THRESHOLD = 245


def yellow_pop_mask(band_bgr):
    """White-on-black image of the WARNING-YELLOW population digits.

    The game recolours the population text yellow as headroom runs out -
    exactly when the housed logic most needs the number - and on light
    architecture sets that text sits directly on bright stone, so neither
    the white mask nor a brightness gate sees it. Yellow is high red AND
    green over a much lower blue; the stone is warm but nowhere near that
    saturated. Same move as the per-resource reader's yellow digits.
    """
    blue = band_bgr[:, :, 0].astype(int)
    green = band_bgr[:, :, 1].astype(int)
    red = band_bgr[:, :, 2].astype(int)
    is_yellow = ((red > 150) & (green > 130)
                 & (red - blue > 80) & (green - blue > 60))
    return _keep_text_shapes((is_yellow * 255).astype(np.uint8))


def read_population(band_bgr, templates, min_glyph_width):
    """Read the "current/cap" population display. Returns (current, cap).

    Three passes for the three styles the game actually uses: white text
    (normal), warning-yellow text (headroom running out), and the bright
    housed box (at the wall). Each pass keeps the same strict parsing
    rules, so a fallback can fill a gap but never invent a reading.
    """
    # white_mask, not a plain threshold: this band sits on the civ's
    # border artwork, and light architecture sets glint past any
    # brightness gate. Strict pass first, soft pass for scaled/softened
    # frames - see white_mask and WHITE_PASSES for the whole story.
    for min_channel in WHITE_PASSES:
        white = white_mask(band_bgr, min_channel)
        result = _parse_population(white, templates, min_glyph_width)
        if result[0] is not None:
            return result

    result = _parse_population(yellow_pop_mask(band_bgr), templates,
                               min_glyph_width)
    if result[0] is not None:
        return result

    # The housed box: cream digits on a bright olive fill. Bright enough
    # for a plain gate, and too desaturated for the yellow mask above.
    housed = ((band_bgr.max(axis=2) > HOUSED_STYLE_THRESHOLD)
              .astype("uint8") * 255)
    return _parse_population(housed, templates, min_glyph_width)


# No digit in the HUD's population font is wider than this; a wider run is
# two characters touching (on the Portuguese band the slash brushes the
# digit after it, and "/2" arrived as one sixteen-pixel run).
MAX_POP_GLYPH_WIDTH = 13


def _split_merged_runs(binary, runs):
    """Split any run too wide to be one character at its thinnest column.

    Touching characters still pinch to a one-or-two pixel bridge where they
    meet; the column with the least ink inside the run is that bridge. One
    split per run is enough here - the failure mode is a pair, not a train.
    """
    split = []
    for start, end in runs:
        if end - start <= MAX_POP_GLYPH_WIDTH:
            split.append((start, end))
            continue
        ink = (binary[:, start:end] > 0).sum(axis=0)
        # Only pinch in the interior; the edges are the characters proper.
        interior = ink[3:-3]
        if len(interior) == 0:
            split.append((start, end))
            continue
        pinch = start + 3 + int(interior.argmin())
        split.append((start, pinch))
        split.append((pinch, end))
    return split


def _parse_population(binary, templates, min_glyph_width):
    """Parse an already-thresholded band into (current, cap).

    Returns (None, None) if it does not read cleanly. The slash's *position*
    matters: it is the split between the two numbers, so there must be
    exactly one, with digits on both sides.
    """
    runs = _split_merged_runs(binary, find_column_runs(binary))

    labels = []
    separators = []
    for start, end in runs:
        if end - start < min_glyph_width:
            continue
        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue
        if _diagonality(glyph) > SLASH_DIAGONALITY:
            separators.append(len(labels))
            continue
        label, score = classify_glyph(glyph, templates)
        if score < MIN_MATCH_SCORE:
            return None, None
        labels.append(label)

    # Exactly one slash, strictly between digits. Anything else means the
    # band caught something that is not a clean N/M - refuse to guess.
    if len(separators) != 1 or separators[0] == 0 or separators[0] == len(labels):
        return None, None

    current = digits_to_int(labels[:separators[0]])
    cap = digits_to_int(labels[separators[0]:])
    if not _plausible_population(current, cap):
        return None, None
    return current, cap


def read_clock_seconds(band_bgr, templates, min_glyph_width):
    """Read the HH:MM:SS clock and return it as total seconds.

    The band deliberately extends past the clock (it also catches text like
    "(Normal - 1.7)"), so parsing walks left to right and STOPS the moment
    six digits are in hand - the trailing text never gets a vote. A run
    that fails to classify before the six digits are complete still means
    "no reading": that is a real segmentation problem, not decoration.

    white_mask rather than a plain threshold: the band sits on the civ's
    border artwork, and light architecture sets glint brighter than any
    brightness gate, which broke the segmentation - and with it the whole
    new-game detection. White is bright AND colorless; stone is bright
    but warm.
    """
    for min_channel in WHITE_PASSES:
        value, score = _parse_clock(white_mask(band_bgr, min_channel),
                                    templates, min_glyph_width)
        if value is not None:
            return value, score
    return None, 0.0


def _parse_clock(binary, templates, min_glyph_width):
    """Parse an already-masked clock band into total seconds."""
    runs = find_column_runs(binary)

    digits = []
    weakest = 1.0
    for start, end in runs:
        if len(digits) == 6:
            break
        if end - start < min_glyph_width:
            continue                     # the colons, far thinner than digits
        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue
        label, score = classify_glyph(glyph, templates)
        if score < MIN_MATCH_SCORE:
            return None, 0.0
        digits.append(label)
        weakest = min(weakest, score)

    if len(digits) != 6:
        return None, 0.0

    hours = digits_to_int(digits[0:2])
    minutes = digits_to_int(digits[2:4])
    seconds = digits_to_int(digits[4:6])

    # Minutes and seconds above 59 mean I mis-segmented something.
    if minutes > 59 or seconds > 59:
        return None, 0.0

    return hours * 3600 + minutes * 60 + seconds, weakest
