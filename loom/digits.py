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

# The population band asks for more confidence than the rest, because its
# failure mode is worse. Everywhere else a doubtful glyph costs a reading
# and the next poll tries again; here a "1" where the screen says "4" is a
# plausible number that the housing alerts will act on, and it repeats frame
# after frame rather than flickering, so no filter downstream catches it.
#
# Measured over two stock 1080p games, at the point where the small-HUD
# templates first made this band readable at all: at 0.55 it read 609 bands
# and 33 of them disagreed with the villager count beside them; at 0.65 it
# read 604 and 14 disagreed. Five readings traded for nineteen wrong ones.
# Above 0.72 the band goes quiet again - 97 readings out of 348 - so this
# sits at the knee rather than at the safest possible value.
POPULATION_MATCH_SCORE = 0.65

# The longest game clock worth believing. Age of Empires matches do not run
# for hours on end, and every value above this seen so far has been a
# mis-segmentation rather than a marathon.
MAX_GAME_HOURS = 4

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
# A third, softer pass for a small HUD. At 1920x1080 the clock is drawn
# about 6x12, and its strokes are so thin that antialiasing pulls whole
# parts of a glyph under both gates above - the bottom bar of a "2" went
# missing, leaving a shape that scored as a "7" just under the match gate
# and threw the read away. Measured over 185 consecutive 1080p frames:
# two frames read with the first two passes, 185 with this one, and the
# times never once went backwards. It is tried last, so nothing that reads
# today reaches it, and every validation rule still applies.
WHITE_FAINT = 170
WHITE_PASSES = (WHITE_STRICT, WHITE_SOFT, WHITE_FAINT)
WHITE_MAX_SPREAD = 45


def _white_pixels(band_bgr, min_channel=WHITE_STRICT):
    """The raw white test alone: which pixels are bright AND colorless.

    Split from white_mask so a caller can see the ink BEFORE the shape
    filter runs - _fit_clock_rows needs that, because it exists to repair
    the reference height that very filter uses.
    """
    blue = band_bgr[:, :, 0].astype(int)
    green = band_bgr[:, :, 1].astype(int)
    red = band_bgr[:, :, 2].astype(int)

    lowest = np.minimum(np.minimum(blue, green), red)
    spread = np.maximum(np.maximum(blue, green), red) - lowest
    return (lowest >= min_channel) & (spread <= WHITE_MAX_SPREAD)


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
    is_white = _white_pixels(band_bgr, min_channel)
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


def trimmed_glyph(binary, start, end):
    """Crop one character at the size the screen actually drew it."""
    column_slice = binary[:, start:end]

    # Trim blank rows so glyphs line up regardless of where they sat vertically.
    inked_rows = np.nonzero(column_slice.max(axis=1))[0]
    if len(inked_rows) == 0:
        return None
    return column_slice[inked_rows.min():inked_rows.max() + 1, :]


def extract_glyph(binary, start, end):
    """Crop one character and squash it to the standard glyph size."""
    trimmed = trimmed_glyph(binary, start, end)
    if trimmed is None:
        return None
    return cv2.resize(trimmed, (GLYPH_WIDTH, GLYPH_HEIGHT),
                      interpolation=cv2.INTER_AREA)


# Templates shrunk to one glyph size, kept because a clock read asks for the
# same size six times over and a whole session asks for it thousands of
# times. Keyed by the template list as well as the size: a caller that
# loads its own templates must not be answered with somebody else's.
_TEMPLATES_AT_SIZE = {}
_TEMPLATE_CACHE_LIMIT = 32


def templates_at_size(templates, shape):
    """The same templates resized to (height, width), normalized as usual."""
    key = (id(templates), shape)
    resized = _TEMPLATES_AT_SIZE.get(key)
    if resized is None:
        height, width = shape
        resized = [(label, _normalize(cv2.resize(template, (width, height),
                                                 interpolation=cv2.INTER_AREA)))
                   for label, template in templates]
        if len(_TEMPLATES_AT_SIZE) >= _TEMPLATE_CACHE_LIMIT:
            _TEMPLATES_AT_SIZE.clear()
        _TEMPLATES_AT_SIZE[key] = resized
    return resized


def classify_glyph(glyph, templates, native=None):
    """Return (digit, score) for the best-matching template.

    Scored two ways when the caller passes the untouched crop as `native`,
    keeping whichever answers more confidently.

    The standard path stretches every glyph to 14x20 so one set of templates
    serves every HUD size. That works until the glyph is SMALLER than the
    box: at 1920x1080 the clock is drawn about 6x12, and blowing that up to
    14x20 invents detail it never had - enough to turn a 2 into a 7 scoring
    0.43, under MIN_MATCH_SCORE, which aborted the whole clock read. Loom
    read no clock at all in 185 consecutive 1080p frames because of it.

    Shrinking the TEMPLATE to the glyph instead throws away detail the
    screen never drew, which is the honest direction: what is left is what
    the game actually rendered. Both are tried because neither wins
    everywhere - upscaling still reads the large HUDs best, and those are
    the sizes the templates were cut at.
    """
    best_label, best_score = None, -1.0

    normalized = _normalize(glyph)
    for label, template in templates:
        # Normalized correlation: 1.0 is a perfect match.
        score = float((normalized * template).mean())
        if score > best_score:
            best_label, best_score = label, score

    # Only worth asking when the glyph is smaller than the box it would be
    # stretched into; at or above that size the standard path is already
    # comparing real pixels against real pixels.
    if (native is not None and native.size
            and native.shape[0] < GLYPH_HEIGHT
            and native.shape[1] <= GLYPH_WIDTH
            and min(native.shape) >= 2):
        as_drawn = _normalize(native)
        for label, template in templates_at_size(templates, native.shape):
            score = float((as_drawn * template).mean())
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

    # The tallest run is the yardstick for "as tall as its neighbours", so
    # it is measured before anything is discarded.
    _boxes, tallest = _bar_context(binary, runs)

    digits = []
    weakest = 1.0
    for start, end in runs:
        if not is_character(binary, start, end, min_glyph_width, tallest):
            continue

        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue

        label, score = classify_glyph(glyph, templates,
                                      trimmed_glyph(binary, start, end))
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


# The villager badge on the STOCK bar is not in a dark box - it is stamped on
# the population icon's own portrait, so the crop around it holds skin, cloth
# and player colour as well as digits. A plain brightness gate cannot separate
# those: it keeps the bright parts of the portrait, they touch the digits, and
# the whole thing reads as one unsplittable run.
#
# The white-and-colourless test can, because it is the same problem the
# population band already solves on civ border art - skin is warm and cloth is
# saturated, digits are neither. It needs its own threshold, though: measured
# on a live stock bar, WHITE_SOFT (190) eats the base off a "2" and leaves a
# shape that classifies as a confident "7". The badge font is smaller than the
# bar's and its anti-aliasing runs dimmer, so 170 is where the whole glyph
# survives. Anything at or below 170 read "12" correctly; 190 and above did
# not.
BADGE_WHITE = 170


def read_count(region_bgr, templates, min_glyph_width):
    """Read a villager count from a HUD icon's corner.

    The dark-box pass runs first and unchanged, so a HUD it already reads is
    read exactly as before. The portrait pass exists for skins that stamp the
    number straight onto the art.
    """
    digits, score = read_digits(region_bgr, templates,
                               ICON_BOX_THRESHOLD, min_glyph_width)
    if digits is None:
        digits, score = read_binary(white_mask(region_bgr, BADGE_WHITE),
                                    templates, min_glyph_width)
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


# A "1" is a bar, and a bar defeats template matching outright.
#
# extract_glyph stretches every run to one 14x20 box, which is exactly what
# makes the other digits comparable - but a three-pixel-wide bar stretched to
# fourteen columns becomes a SOLID BLOCK, and _normalize then divides by a
# standard deviation of almost nothing. Measured on live stock frames, those
# runs score 0.27 against the "1" template, less than half of
# MIN_MATCH_SCORE. The digit is not merely matched poorly; the normalisation
# has thrown away the one feature that identifies it.
#
# So a "1" is recognised by SHAPE, the same way the slash above already is.
# Measured across ~1,600 glyph runs from two live stock games:
#
#              width/height     ink fraction
#     "1"      0.23 - 0.31      0.77 - 0.97
#     slash    0.31 - 0.38      0.22 - 0.27
#     others   0.46 - 0.73      0.35 - 0.62
#
# Ink alone separates a bar from every other glyph with a margin of 0.15, and
# the aspect test is a second opinion rather than the deciding one - the same
# corroborate-with-a-second-measurement habit identify_hud uses.
BAR_ASPECT = 0.45
BAR_INK = 0.70

# How tall a bar must be relative to the tallest run beside it. Deliberately
# a FRACTION of a measured neighbour rather than a pixel count: a constant
# here would be the pixel-constant bug all over again, failing at some HUD
# scale nobody tested. Its job is only to stop a speck of noise being called
# a "1", and every real digit in a band shares one text height.
BAR_MIN_HEIGHT_FRACTION = 0.6


def _run_box(binary, start, end):
    """(width, height, ink fraction) of one run's inked box, or None."""
    column_slice = binary[:, start:end]
    inked_rows = np.nonzero(column_slice.max(axis=1))[0]
    if len(inked_rows) == 0:
        return None
    trimmed = column_slice[inked_rows.min():inked_rows.max() + 1, :]
    height, width = trimmed.shape
    if height == 0 or width == 0:
        return None
    return width, height, float((trimmed > 0).mean())


def _is_bar(box, tallest):
    """Is this run a solid vertical bar - which is to say, a "1"?"""
    if box is None or not tallest:
        return False
    width, height, ink = box
    return (height >= BAR_MIN_HEIGHT_FRACTION * tallest
            and width / height <= BAR_ASPECT
            and ink >= BAR_INK)


# Two bars this close together are one hollow digit's SIDES, not two "1"s.
# Measured on the frame that taught it: a "0" eroded by the last-resort
# brightness pass to its two side strokes, gaps of 2-3px against a text
# height of 12 - while two REAL adjacent "1"s sit 5-6px apart, a full
# digit-spacing away. A fraction of the measured text height rather than a
# pixel count, per the pixel-constant rule.
HOLLOW_GAP_FRACTION = 0.3

# How tall a run must stand, against the tallest run beside it, to be a
# digit rather than a colon. Measured at 1920x1080, where the clock's "1" is
# 10px of an 11px line while the colons do not survive the white mask at
# all; a colon that did survive is two dots around the middle and comes
# nowhere near this.
COLON_HEIGHT_FRACTION = 0.7


def is_character(binary, start, end, min_glyph_width, tallest):
    """Is this column run a character, or something to skip?

    Width alone is the wrong question at a small HUD. The gate exists to
    skip the colons in the clock, and a "1" is the narrowest digit there is
    - at 1920x1080 it measures 3px against a gate of 4, so it was dropped
    and "18" villagers read as a confident "8". Measured over one live
    capture: 189 of 300 frames lost a full-height 3px run that way, every
    one of them a "1".

    Height is what tells them apart. A "1" stands as tall as every digit
    beside it; a colon is two dots around the middle and never does.
    """
    if end - start >= min_glyph_width:
        return True
    box = _run_box(binary, start, end)
    return box is not None and box[1] >= max(
        2, round(tallest * COLON_HEIGHT_FRACTION))


def _merge_hollow_pairs(binary, runs, tallest):
    """Rejoin a hollow digit that a harsh threshold split into two bars.

    Without this, the bar rule reads each side of an eroded "0" as a "1" -
    live, "10/15" became "111/15", which passed every plausibility check and
    announced HOUSED five villagers early. Merged back into one run, the
    pair classifies as the outline it is (a skeletal "0" scored 0.77 against
    the 0 template on the same frame) or fails the match gate and reads as
    nothing - either of which beats a confident wrong number.

    Only PAIRS of bars merge, and only across a sliver of a gap: two real
    "1"s stand a whole digit-spacing apart and are untouched.
    """
    threshold = max(2, round(tallest * HOLLOW_GAP_FRACTION))
    merged = []
    index = 0
    while index < len(runs):
        if index + 1 < len(runs):
            start_a, end_a = runs[index]
            start_b, end_b = runs[index + 1]
            if (start_b - end_a <= threshold
                    and _is_bar(_run_box(binary, start_a, end_a), tallest)
                    and _is_bar(_run_box(binary, start_b, end_b), tallest)):
                merged.append((start_a, end_b))
                index += 2
                continue
        merged.append(runs[index])
        index += 1
    return merged


def _bar_context(binary, runs):
    """The boxes of every run, and the tallest of them.

    Measured once per band so _is_bar can compare a run against its
    neighbours instead of against a fixed number of pixels.
    """
    boxes = {(start, end): _run_box(binary, start, end)
             for start, end in runs}
    heights = [box[1] for box in boxes.values() if box is not None]
    return boxes, (max(heights) if heights else 0)


# How far current may exceed cap before the pair is called a misread.
# Current over cap is REAL - houses burn down, and the game shows 21/20 -
# but each burned house frees only 5, so a large overshoot is a phantom
# digit, not a fire. Live, "10/15" misread as "111/15" sailed through the
# old unbounded check and announced HOUSED five villagers early.
BURNED_HOUSE_ALLOWANCE = 15


def _plausible_population(current, cap):
    """Could this pair actually appear on the HUD?

    Pop cap only ever comes from houses (+5), Town Centres and castles (also
    multiples of 5), or the lobby's population limit (multiples of 25) - so a
    cap that is not a multiple of 5 is necessarily a misread. Current may
    exceed the cap - houses burn down - but only by a few burned houses'
    worth; see BURNED_HOUSE_ALLOWANCE.
    """
    return (cap % 5 == 0 and 5 <= cap <= 500
            and 0 <= current <= cap + BURNED_HOUSE_ALLOWANCE)


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


def read_population(band_bgr, templates, min_glyph_width,
                    max_glyph_width=None):
    """Read the "current/cap" population display. Returns (current, cap).

    Three passes for the three styles the game actually uses: white text
    (normal), warning-yellow text (headroom running out), and the bright
    housed box (at the wall). Each pass keeps the same strict parsing
    rules, so a fallback can fill a gap but never invent a reading.

    max_glyph_width is how wide a run may be before it is treated as two
    characters touching. It must track the HUD scale - see _split_merged_runs
    - and callers that know the scale should pass reader.max_glyph_width().
    """
    # white_mask, not a plain threshold: this band sits on the civ's
    # border artwork, and light architecture sets glint past any
    # brightness gate. Strict pass first, soft pass for scaled/softened
    # frames - see white_mask and WHITE_PASSES for the whole story.
    for min_channel in WHITE_PASSES:
        white = white_mask(band_bgr, min_channel)
        result = _parse_population(white, templates, min_glyph_width,
                                   max_glyph_width)
        if result[0] is not None:
            return result

    result = _parse_population(yellow_pop_mask(band_bgr), templates,
                               min_glyph_width, max_glyph_width)
    if result[0] is not None:
        return result

    # The housed box: cream digits on a bright olive fill. Bright enough
    # for a plain gate, and too desaturated for the yellow mask above.
    housed = ((band_bgr.max(axis=2) > HOUSED_STYLE_THRESHOLD)
              .astype("uint8") * 255)
    return _parse_population(housed, templates, min_glyph_width,
                             max_glyph_width)


# No digit in the HUD's population font is wider than this; a wider run is
# two characters touching (on the Portuguese band the slash brushes the
# digit after it, and "/2" arrived as one sixteen-pixel run).
MAX_POP_GLYPH_WIDTH = 13


def _split_merged_runs(binary, runs, max_glyph_width=None):
    """Split any run too wide to be one character at its thinnest column.

    Touching characters still pinch to a one-or-two pixel bridge where they
    meet; the column with the least ink inside the run is that bridge. One
    split per run is enough here - the failure mode is a pair, not a train.

    max_glyph_width has to grow with the HUD or it starts cutting real digits
    in half. Measured at HUD scale 1.48: a "4" is 15px and a "5" is 14px, so
    the fixed 13 split both and "4/5" read as nothing at all. Callers that
    know the scale pass a scaled value; the default keeps the reference figure
    for callers that do not.
    """
    if max_glyph_width is None:
        max_glyph_width = MAX_POP_GLYPH_WIDTH
    split = []
    for start, end in runs:
        if end - start <= max_glyph_width:
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


def _parse_population(binary, templates, min_glyph_width,
                      max_glyph_width=None):
    """Parse an already-thresholded band into (current, cap).

    Returns (None, None) if it does not read cleanly. The slash's *position*
    matters: it is the split between the two numbers, so there must be
    exactly one, with digits on both sides.
    """
    runs = _split_merged_runs(binary, find_column_runs(binary),
                              max_glyph_width)
    _boxes, tallest = _bar_context(binary, runs)
    runs = _merge_hollow_pairs(binary, runs, tallest)
    boxes, tallest = _bar_context(binary, runs)

    labels = []
    separators = []
    for start, end in runs:
        # A bar is exempt from the width gate, and has to be: a "1" is
        # NARROWER than the gate by nature. Measured on live stock frames the
        # "1" comes back 3px against a gate of 4, so it was being skipped -
        # and skipping is silent, so "21/30" was read as "2/30" with total
        # confidence. Widening the gate instead would let noise through; the
        # shape test is what makes the exemption safe.
        bar = _is_bar(boxes.get((start, end)), tallest)
        if end - start < min_glyph_width and not bar:
            # Not "skip it" - REFUSE THE BAND. Everywhere else a run this
            # narrow is a colon or a speck, but the population display has
            # no such thing: its only narrow glyph is the "1", and that is
            # a bar, caught above. So a narrow run here is a piece of a
            # digit that the threshold broke in half, and reading the other
            # piece on its own is how a "4" became a confident "1" - the
            # last-resort housed pass split it into 2px and 3px, dropped
            # the 2px, and read 4/5 as 1/5 for as long as the band stayed
            # on screen. Silence costs a poll; that costs the housing
            # alerts their meaning.
            return None, None
        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue
        if _diagonality(glyph) > SLASH_DIAGONALITY:
            separators.append(len(labels))
            continue
        if bar:
            labels.append(1)
            continue
        label, score = classify_glyph(glyph, templates,
                                      trimmed_glyph(binary, start, end))
        if score < POPULATION_MATCH_SCORE:
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


def _fit_clock_rows(band_bgr):
    """Trim a clock band to the rows its text actually occupies.

    The band's crop is derived from the anchor 590 reference-pixels away, so
    at large HUD scales it arrives much taller than the text - measured, a
    55px band around 21px digits. That is not merely waste: _keep_text_shapes
    keeps a component only if it spans 35% of the BAND height, and 35% of 55
    is 19.25px, which sits exactly at glyph height. Whichever digits eroded a
    pixel short that frame were silently deleted - a "5" vanished from
    "00:07:51" and the whole read refused, on a fifth of live polls.

    Fitting the band to its densest contiguous ink rows makes the ratio safe
    by construction: 35% of a fitted ~25px band is 9px, far under any digit.
    The row profile comes from the raw color test, NOT white_mask, because
    white_mask ends with the very shape filter whose reference this exists to
    fix. On a band that is already tight the fit changes nothing.
    """
    is_white = _white_pixels(band_bgr, WHITE_SOFT)
    rows = is_white.sum(axis=1)
    peak = int(rows.max())
    if peak < 3:
        return band_bgr

    inked = rows >= max(3, int(peak * 0.35))
    best = current = None
    for y, on in enumerate(inked):
        if on:
            current = (current[0], y) if current else (y, y)
            if best is None or current[1] - current[0] > best[1] - best[0]:
                best = current
        else:
            current = None

    top = max(0, best[0] - 2)
    bottom = min(band_bgr.shape[0], best[1] + 3)
    if bottom - top < 8:
        return band_bgr                  # too thin to be a text line
    return band_bgr[top:bottom]


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
    band_bgr = _fit_clock_rows(band_bgr)
    for min_channel in WHITE_PASSES:
        value, score = _parse_clock(white_mask(band_bgr, min_channel),
                                    templates, min_glyph_width)
        if value is not None:
            return value, score
    return None, 0.0


def _parse_clock(binary, templates, min_glyph_width):
    """Parse an already-masked clock band into total seconds.

    Strictly left to right, aborting on the first run that classifies badly.
    A retry that skipped leading junk runs lived here briefly - border
    decoration ahead of the digits was aborting a fifth of live reads - but
    once _fit_clock_rows landed, replaying every saved live band showed the
    retry rescuing exactly nothing the fitted band did not already rescue, so
    it went. It carried a real cost for that nothing: each dropped run was a
    chance for well-scoring junk to head a valid-looking time.
    """
    runs = find_column_runs(binary)
    # Rejoin a hollow digit the threshold split down the middle. The clock
    # is drawn small - about 6x12 at 1920x1080 - and there the thin top and
    # bottom bars of a "0" drop out of the white mask, leaving two upright
    # strokes that each classify as a confident "1". This is the same fault
    # the population band hit ("10/15" read as "111/15"), so it is the same
    # repair; it was simply never wired in over here, where nothing smaller
    # than 1440p had been read.
    _boxes, tallest = _bar_context(binary, runs)
    runs = _merge_hollow_pairs(binary, runs, tallest)
    digits = []
    weakest = 1.0
    for start, end in runs:
        if len(digits) == 6:
            break
        if not is_character(binary, start, end, min_glyph_width, tallest):
            continue                     # the colons, far thinner than digits
        glyph = extract_glyph(binary, start, end)
        if glyph is None:
            continue
        label, score = classify_glyph(glyph, templates,
                                      trimmed_glyph(binary, start, end))
        if score < MIN_MATCH_SCORE:
            return None, 0.0
        digits.append(label)
        weakest = min(weakest, score)

    if len(digits) != 6:
        return None, 0.0

    hours = digits_to_int(digits[0:2])
    minutes = digits_to_int(digits[2:4])
    seconds = digits_to_int(digits[4:6])

    # Minutes and seconds above 59 mean I mis-segmented something, and so
    # does an hours field a real match cannot reach. Both are cheap, and the
    # second earns its place: a small HUD whose hollow "0"s split down the
    # middle reads four zeros as "10:01:00" - six digits, minutes and
    # seconds both legal, and 36060 seconds of confident nonsense. A clock
    # Loom refuses to read costs a poll; one it reads wrongly desynchronises
    # the whole build.
    if minutes > 59 or seconds > 59 or hours > MAX_GAME_HOURS:
        return None, 0.0

    return hours * 3600 + minutes * 60 + seconds, weakest
