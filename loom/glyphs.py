"""
Loom â€” reading the notification font, one character at a time.

The game states events as text lines ("--Mill Built--", "--Knight
Created--") in one fixed font. Ten digit templates already read every
number the HUD can show; this module makes the same bet on the alphabet:
harvest each character's glyph once (tools/build_notification_font.py),
and every line the game can ever print becomes readable - no template per
phrase, no OCR engine, no AI.

Text isolation must survive all eight player colours (attack warnings
render in the attacker's colour). Grayscale is the wrong axis for that:
yellow text reads ~226 in luminance but pure blue reads ~29, nearly as
dark as the panel behind it. The colour-agnostic axis is the BRIGHTEST
CHANNEL - every saturated player colour drives at least one channel near
full - thresholded relative to the line's own peak, which also catches
grey, the one unsaturated colour.

The never-guess rule holds at the character level: one unclassifiable
glyph kills the whole line. A dropped line costs a stats entry; a misread
word would poison them.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np

from . import digits, paths

FONT_DIR = paths.TEMPLATES_DIR / "notification_font"

# Below this correlation a glyph is not believed. Measured on real lines:
# a true match scores 0.99+, the best WRONG letter scores about 0.6 - so
# 0.8 sits in open water, and a line of near-misses dies rather than
# reading as plausible garbage (one did, at 0.57, before this was raised).
MIN_GLYPH_SCORE = 0.8

# One glyph per line may fall short of MIN_GLYPH_SCORE, down to this floor,
# without killing the line. A HUD rendered a hair off the harvest scale
# softens every glyph a little and occasionally drops exactly one below the
# gate (measured at a 2% resample: eighteen glyphs at 0.83-0.97, one at
# 0.76) - all-or-nothing turned that into a whole game of unread lines.
# The tolerated glyph still enters as its best-scoring label, and
# parse_event's KNOWN_WORDS vocabulary is the backstop: a wrong letter
# makes a non-word, and the line refuses at the event stage instead.
WEAK_GLYPH_FLOOR = 0.65

# A candidate and a template must have roughly similar shapes to compare at
# all: extract_glyph squashes everything to one box, which would make a
# stretched "i" impersonate an "l" - so the natural width/height ratio is
# checked first, and only templates within this factor compete.
ASPECT_TOLERANCE = 1.8

# The floor under the space threshold, as a fraction of the line's height
# and as bare pixels. See space_gap_for, which is where the real decision
# is made - these two only stop it going mad on a line with no spaces in it
# at all.
SPACE_FRACTION = 0.15
MIN_SPACE_PIXELS = 3.5

# Runs narrower than this fraction of line height are dropped as noise
# specks (a real "i" or "l" is thin but taller than this is wide).
MIN_RUN_FRACTION = 0.08

# Every real glyph carries the font's near-black outline; bright terrain
# showing past the notification box's edge does not. A run whose darkest
# tenth is brighter than this is scenery, not text.
OUTLINE_DARKNESS = 60

# How label names map to characters, for the filename scheme. Filenames
# must survive case-insensitive filesystems (the Windows goal), so "A" and
# "a" become upper_A / lower_a rather than colliding files.
PUNCT_NAMES = {
    "-": "hyphen", ".": "period", ",": "comma", "'": "apostrophe",
    "(": "lparen", ")": "rparen", "!": "bang", "/": "slash", ":": "colon",
}
PUNCT_CHARS = {name: char for char, name in PUNCT_NAMES.items()}

# Shapes the game draws as ONE run that are really two characters. The
# framing "--" was the first of these and had a special case of its own;
# this is the general form of the same idea. See char_for.
MERGED_CHARS = {"merge_t_hyphen": "t-",
                # The stock rendering welds these pairs into one ink run
                # routinely where Anne_HK's font keeps a valley: measured,
                # they are why "--Market Built--" read as "--Markn
                # Built--" (et), "Built" as "Buih" (lt) and "Villager" as
                # "ViBager" (ll) all afternoon.
                "merge_e_t": "et",
                "merge_l_t": "lt",
                "merge_l_l": "ll"}


def label_for(char):
    """The filesystem-safe label for one character."""
    if char.isalpha():
        return ("upper_" if char.isupper() else "lower_") + char
    if char.isdigit():
        return "digit_" + char
    if char in PUNCT_NAMES:
        return "punct_" + PUNCT_NAMES[char]
    raise ValueError(f"no label scheme for {char!r}")


def char_for(label):
    """The character (or characters) a label stands for.

    punct_dashes is the one multi-character label: the "--" framing around
    every notification renders as a single joined stroke, so it segments
    as one glyph and reads back as two characters.
    """
    if label == "punct_dashes":
        return "--"
    # Other merged pairs, of which the framing dashes were only the first.
    # The game ends most of its lines with "Built--" or "Complete--", and at
    # some sub-pixel positions the last letter runs together with the first
    # hyphen into a single shape - which then classifies CONFIDENTLY as an
    # "F" or a "p", so nothing downstream tries to split it and the line
    # loses its closing frame. parse_event then refuses it as a fragment,
    # correctly, because a fragment must never become an event.
    #
    # Measured live at 2560x1440: "--House BuilF-" seventeen times and
    # "--House Builp-" fifteen, against "--Mill Built--" reading cleanly
    # fifteen times. House and Stable never fired all game while Mill did,
    # which is exactly what the author reported.
    #
    # Splitting the shape apart is not available: the hyphen touches the
    # "t" crossbar, so there is no valley in the ink to cut at. A tail
    # re-split was written and measured first and found EXACTLY NOTHING -
    # 109 events and 320 sightings either way - so it was removed. Giving
    # the merged shape its own template is what worked, and it is the same
    # answer punct_dashes already was: it competes against F and p on its
    # own merits rather than by a special case.
    if label.startswith("merge_"):
        return MERGED_CHARS[label]
    kind, _, name = label.partition("_")
    if kind in ("upper", "lower", "digit"):
        return name
    if kind == "punct":
        return PUNCT_CHARS[name]
    raise ValueError(f"unrecognized label {label!r}")


# How far a variant's harvest scale may sit from the line being read and
# still compete. The two renderings on disk sit at ~0.73 and ~0.98, and
# mixing them is measured poison: harvesting 1080p variants untagged sank
# UNDERSTOOD on every run at once - twice, once as an auto-harvest and once
# as a labelled manifest - because a 15px "l" resembles a 21px "i" more
# than either resembles its own letter elsewhere. 0.15 keeps the two
# renderings apart with room; untagged variants are universal and always
# compete, which is exactly the pre-tagging behaviour.
SCALE_TOLERANCE = 0.15

# A line's height is about 21px at scale 1.0 (the notification pitch is 28
# and a line's glyph core is three quarters of it). Height over this is the
# scale HINT a caller gets for free when nothing better is known - the
# same formula the harvest tool tags new variants with, so the hint and
# the tags agree by construction.
LINE_HEIGHT_AT_FULL_SCALE = 21.0


def load_font(directory=None):
    """The glyph set: {label: [(image, aspect, scale, skin), ...]}.

    Variants per label, like the digit templates - the game renders the
    same character slightly differently by sub-pixel position. `scale` is
    the rendering a variant was harvested from ("lower_a@0.73_5.png"),
    and `skin` the HUD skin where the tag carries one
    ("lower_n@stock~0.95_2.png") - the two skins draw DIFFERENT FONTS at
    the same scale, which the scale tag alone cannot separate. Measured
    the day stock coverage first landed untagged by skin: stock's n and
    E, competing at Anne_HK's own scale, turned "Swordsman" into
    "Swordsmai" and "Eagle" into "Fagle" across six 1440p lines the
    reader had owned for days. None in either slot means untagged: the
    variant serves every rendering. Returns {} when no font has been
    harvested yet; the caller treats that as "cannot read", never as an
    error.
    """
    directory = directory or FONT_DIR
    font = {}
    for path in sorted(glob.glob(str(directory / "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0]
        label = name.rsplit("_", 1)[0]     # strip the variant number
        label, _, tagged = label.partition("@")
        skin, tilde, scale_part = tagged.rpartition("~")
        skin = skin if tilde else None
        try:
            harvest_scale = float(scale_part) if scale_part else None
        except ValueError:
            continue                       # a scale that is not a number
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        aspect = image.shape[1] / image.shape[0]
        # Each glyph also enters at a few slightly resampled sizes. The
        # harvested pixels are one exact rendering; the game at any other
        # HUD scale antialiases the same character differently, and that
        # alone drops match scores from 1.00 to below the 0.8 gate -
        # measured on the very line the font was harvested from, resampled
        # by 2%. Blurring the variants into the set keeps the gate strict
        # while letting a slightly softer rendering through.
        for factor in (0.96, 0.98, 1.0, 1.02, 1.04):
            source = image
            if factor != 1.0:
                source = cv2.resize(image, None, fx=factor, fy=factor,
                                    interpolation=cv2.INTER_AREA)
                if source.size == 0:
                    continue
            boxed = cv2.resize(source,
                               (digits.GLYPH_WIDTH, digits.GLYPH_HEIGHT),
                               interpolation=cv2.INTER_AREA)
            _admit(font.setdefault(label, []),
                   digits._normalize(boxed), aspect, harvest_scale, skin)
    return font


def _admit(variants, normalized, aspect, harvest_scale=None, skin=None):
    """Add a variant unless the label already holds a near-twin.

    Many resamples of the same source glyph collapse to almost the same
    normalized box, and every kept variant is paid for on every classify
    of every run of every line - the whole variant set went 5x when the
    resampled sizes were added, and reading one busy panel crossed 150ms.
    Templates are normalized, so a true twin correlates near 1.0; keeping
    only sufficiently different variants preserves the tolerance the
    resamples exist for at a fraction of their cost.
    """
    for kept, _aspect, kept_scale, kept_skin in variants:
        if kept_scale == harvest_scale and kept_skin == skin \
                and float((kept * normalized).mean()) >= 0.985:
            return
    variants.append((normalized, aspect, harvest_scale, skin))


# ---- isolating the text ----------------------------------------------------


def text_mask(line_bgr):
    """White-on-black image of the line's text, any player colour.

    Brightest-channel, thresholded relative to the line's own peak: white
    text, all eight player colours, and grey all clear it, while the dark
    notification box and the terrain showing through it do not. The floor
    keeps an all-dark crop from amplifying its own noise.
    """
    peak = line_bgr.max(axis=2)
    # The absolute floor guards an all-dark crop against amplifying its own
    # noise; 100 rather than higher because the GREY player's text sits
    # near 128 and must clear it - the relative term does the real work.
    floor = max(100, 0.72 * float(peak.max()))
    return ((peak >= floor) * 255).astype(np.uint8)


# One rendered notification line is about this tall. Bands much taller
# than it are stacked or wrapped lines that must be split apart before
# reading - a fused band can never read. Measured 28 on live panels (the
# notification feed's line pitch); the 26 it used to be made the splitter
# over-count lines in tall fused bands and cut real lines in half.
NOMINAL_LINE_HEIGHT = 28

# A band taller than this many pitches is more than one line fused
# together, and is cut back apart. notifications.FUSED_BAND_PITCHES'
# number, measured there: band heights are sharply bimodal, a single line
# reaching 0.8 pitches and a fused PAIR about 1.5, so 1.1 sits in the gap.
FUSED_BAND_PITCHES = 1.1

# The fraction of a band's pixels that must be near-black for it to count
# as text-on-the-notification-box. Bright terrain has highlights that pass
# the ink mask, but it has no dark box and no outline behind them.
#
# 0.30, matching notifications.MIN_BOX_DARKNESS - whose own comment has said
# for some time that the 0.10 here was too low and passed the 0.15-0.23 that
# a panel's edges read. Measured over a Castle-age game on bright farm and
# desert terrain: 0.30 threw away 78 of 350 bands and cost NOT ONE readable
# line. Every band it removed was scenery. Both numbers are grey LEVELS and
# fractions of a count, so neither scales with the HUD.
DARK_FRACTION = 0.30
DARK_LEVEL = 70

# How far past a line's ink the crop reaches, as a fraction of the band's
# own height. A Goldilocks number, not a floor - see where it is used.
BAND_PAD_FRACTION = 0.18

# A trailing band row with less than this fraction of the band's densest
# row is a descender tail, not a text row, and the band's height must not
# follow it. Measured on the one line that failed deterministically, three
# sessions running: "Mining Camp" holds TWO descender letters (the g and
# the p), whose tails together cleared the row floor and stretched the ink
# run five rows past the baseline - the pad then followed the taller band,
# and every glyph was normalised into a canvas a third taller than the
# templates' own. Lines with one descender stay under the floor, which is
# why "--Lumber Camp Built--" read all game while "--Mining Camp Built--"
# never existed. The tails measured 6-14 outlined pixels against a
# baseline row of 104, so a fifth of the peak separates them with room.
# The TOP is never trimmed: ascender tips and i-dots are just as sparse
# but they are real text - measured, trimming the top row turned a 0.920
# correct read into a 0.864 misread. The baseline is the densest row in
# the band, so a bottom trim stops crisply against it; there is no such
# stop at the top.
DESCENDER_TAIL_FRACTION = 0.20


def find_lines(panel_bgr, min_height=10, scale=1.0):
    """Text-line bands in the notification panel: [(y1, y2), ...].

    Rows with enough inked columns, grouped - then two corrections the
    real feed forced:

    * Stacked and WRAPPED lines sit so close that their bands fuse (a
      two-line attack warning, or four messages arriving together), so a
      band much taller than one line is split again at the valleys of its
      row-ink profile.
    * Bright terrain fakes ink without a notification box behind it, so a
      band must also contain a decent share of near-black pixels (the box
      and the font's outline) or it is scenery.
    """
    # The line pitch is the one thing that reliably sets the size of a text
    # line, and it follows the HUD. NOMINAL_LINE_HEIGHT alone was a pixel
    # constant - notifications.py has scaled its own copy of this number
    # since 1920x1080 forced it to.
    pitch = max(4.0, NOMINAL_LINE_HEIGHT * scale)
    min_height = max(4, int(round(min_height * scale)))

    mask = text_mask(panel_bgr)
    gray = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2GRAY)
    # Only ink NEXT TO the font's near-black outline counts toward a line.
    # Brightness alone is not text: sunlit terrain showing past the panel
    # clears the relative threshold easily, and a whole game of "lines"
    # found this way read as nothing but saved junk crops. A dark-adjacency
    # test is how the phrase watcher's band finder stays clean, and it is
    # the same trick here - terrain highlights have no outline behind them.
    dark = (gray < DARK_LEVEL).astype(np.uint8)
    near_dark = cv2.dilate(dark, np.ones((3, 3), np.uint8))
    outlined = mask & (near_dark * 255)
    rows = outlined.sum(axis=1) // 255
    coarse = []
    start = None
    for y, count in enumerate(rows):
        if count >= 6 and start is None:
            start = y
        elif count < 6 and start is not None:
            if y - start >= min_height:
                coarse.append((start, y))
            start = None
    if start is not None and len(rows) - start >= min_height:
        coarse.append((start, len(rows)))

    bands = []
    for y1, y2 in coarse:
        for a, b in _split_tall_band(rows, y1, y2, min_height, pitch):
            # The darkness that proves a notification box is AROUND the
            # ink as much as inside it, so the gate samples a few rows of
            # margin too - ink rows alone can be wall-to-wall bright.
            gate = gray[max(0, a - 4):min(len(rows), b + 4)]
            if gate.size == 0:
                continue
            if (gate < DARK_LEVEL).mean() < DARK_FRACTION:
                continue                     # scenery, not a boxed line
            # The pad follows the BAND'S OWN HEIGHT, the most direct
            # measure of the text's size there is. The fixed 2px it
            # replaces was a pixel constant, and at 2560x1440 - where a
            # line is 22 rows rather than the 15 it was tuned at - it was
            # clipping the ascenders off every line. Measured on a live
            # Castle-age game: proportional padding took readable lines
            # from 26 to 40 out of the same 272 bands.
            #
            # MORE IS NOT BETTER, which is worth knowing before anyone
            # widens it. extract() uses the whole band as the glyph's
            # canvas and deliberately never trims to ink, so the band's
            # height is part of how every glyph gets normalised. Padding
            # out past the text shrinks the glyph inside its box and stops
            # it matching templates cut more tightly - the same sweep read
            # 15 lines at a 6px pad and 14 at 8px, against 40 at 4. The
            # band has to FIT the line, not merely contain it.
            # Descender tails must not set the band's height - see
            # DESCENDER_TAIL_FRACTION. The pad below then re-covers the
            # first rows of tail, so a g keeps most of its hook; what goes
            # is only the stretch of canvas that squashed every OTHER
            # glyph in the line.
            peak = int(rows[a:b].max())
            tail_floor = max(6, int(round(peak * DESCENDER_TAIL_FRACTION)))
            while b - a > min_height and rows[b - 1] < tail_floor:
                b -= 1
            pad = max(2, round((b - a) * BAND_PAD_FRACTION))
            bands.append((max(0, a - pad), min(len(rows), b + pad)))
    return bands


def _split_tall_band(rows, y1, y2, min_height=10, pitch=NOMINAL_LINE_HEIGHT):
    """Split one fused band at the valleys of its row-ink profile.

    FUSED_BAND_PITCHES rather than the 1.6 this used to carry, and the line
    count is height/pitch + 0.5 rather than height/pitch. Both numbers are
    notifications._split_fused_band's, whose comments record what measured
    them; this module had its own guesses and they were wrong in the same
    two ways that one already documents.

    What that cost: on a BUSY feed - the ordinary case in Castle age, and
    the case the whole checklist depends on - messages arrive close enough
    that neighbouring bands fuse. Measured across a live Castle-age game
    against a quiet Dark-age one at the same 2560x1440, band heights ran to
    59 rows against a median of 22, and 1.6 let every band up to 44 rows
    through unsplit. A band holding one line and a slice of the next can
    never classify, so the reader was not failing on the long technology
    names it appeared to fail on - it was failing on how close together
    they arrived.
    """
    height = y2 - y1
    if height <= pitch * FUSED_BAND_PITCHES:
        return [(y1, y2)]
    # n fused lines span (n - 1) pitches plus one line of text, and a line
    # is about half a pitch - so n is height/pitch + 0.5. Rounding the bare
    # ratio reads a three-line fusion as two, and Python rounds 2.5 DOWN,
    # so it did.
    lines = max(2, int(round(height / pitch + 0.5)))
    approx = height / lines
    cuts = [y1]
    for index in range(1, lines):
        # The valley nearest the expected boundary: line gaps have the
        # least ink even when they never reach zero.
        target = y1 + int(index * approx)
        reach = max(1, int(round(pitch * 0.25)))
        lo = max(y1 + min_height, target - reach)
        hi = min(y2 - min_height, target + reach)
        if lo >= hi:
            continue
        valley = min(range(lo, hi), key=lambda y: rows[y])
        cuts.append(valley)
    cuts.append(y2)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= min_height]


def segment_line(line_bgr):
    """One line into per-character crops: (mask, [(start, end), ...]).

    Shared by the reader and the harvest tool, so the glyphs the font was
    built from segment exactly like the glyphs read at runtime.

    Two filters beyond the raw column runs: width (specks are not
    characters) and OUTLINE (real glyphs carry the font's near-black
    outline; bright terrain past the notification box's edge does not, and
    it otherwise segments into convincing phantom runs).
    """
    mask = text_mask(line_bgr)
    gray = cv2.cvtColor(line_bgr, cv2.COLOR_BGR2GRAY)
    height = mask.shape[0]
    min_run = max(2, int(height * MIN_RUN_FRACTION))

    runs = []
    for start, end in digits.find_column_runs(mask):
        if end - start < min_run:
            continue
        margin = 2
        region = gray[:, max(0, start - margin):end + margin]
        if float(np.percentile(region, 10)) > OUTLINE_DARKNESS:
            continue
        # No pre-emptive splitting of wide runs here: read_line splits a
        # run only when reading it whole has FAILED and every piece then
        # classifies - splitting first once carved an "m" into "ln".
        runs.append((start, end))
    return mask, runs


# A run wider than this fraction of line height is suspected of being two
# touching letters. Wide single glyphs stay under it: "m" and the joined
# "--" both measure ~0.65 of the height; merged pairs measure 0.85+.
MERGED_RUN_FRACTION = 0.85


def _pinch_split(mask, start, end, height):
    """Split a suspiciously wide run at its thinnest column, recursively.

    Touching letters ("ey", "rs") arrive as one run; the seam between them
    is a pinch - a column with far less ink than the run's average. A wide
    glyph with no real pinch (a "w") is left whole. Geometry only: the
    caller decides whether the split's READING is acceptable.
    """
    width = end - start
    if width < height * MERGED_RUN_FRACTION:
        return [(start, end)]
    columns = mask[:, start:end].sum(axis=0) / 255
    centre = columns[width // 4: width - width // 4]
    if len(centre) == 0:
        return [(start, end)]
    pinch = int(np.argmin(centre)) + width // 4
    if columns[pinch] > 0.4 * columns.mean():
        return [(start, end)]
    return (_pinch_split(mask, start, start + pinch, height)
            + _pinch_split(mask, start + pinch, end, height))


def _read_join(mask, runs, index, font, space_gap, scale=None,
               skin=None):
    """Read this run and the next as ONE letter, or refuse.

    The mirror of _read_split, and the other thing that goes wrong at a
    small rendering. A glyph about 6px wide has strokes a pixel or two
    across, and one column of it falling under the ink threshold cuts the
    letter into two runs - "u" arriving as "p" and "t", "M" as "l" and "l".
    Nothing downstream can recover from that: both halves are wide enough to
    survive the noise filter, so they both classify, confidently, as the
    wrong letters.

    The bar is the same as the split's and for the same reason: this is only
    reached once reading the run ALONE has failed, the two runs have to be
    close enough to be one letter (a word gap is never joined), and the
    join has to classify confidently. A real "l" followed by a real "i"
    reads fine on its own and never gets here.
    """
    if index + 1 >= len(runs):
        return None
    start, end = runs[index]
    next_start, next_end = runs[index + 1]
    if next_start - end >= space_gap:
        return None                     # a word gap: different words
    glyph, aspect = extract(mask, start, next_end)
    if glyph is None:
        return None
    char, score = classify(glyph, aspect, font, scale, skin)
    if char is None or score < MIN_GLYPH_SCORE:
        return None
    return char, score


def _read_split(mask, start, end, font, scale=None, skin=None):
    """Read one failed run as touching letters, or refuse.

    The bar is deliberately high: the split only counts if it actually
    produced MORE than one piece and EVERY piece classifies confidently.
    A real "m" survives because reading it whole succeeds long before
    this is reached; a real merged "ey" arrives here having failed whole,
    splits at its pinch, and both halves read.
    """
    pieces = _pinch_split(mask, start, end, mask.shape[0])
    if len(pieces) < 2:
        return None
    characters = []
    weakest = 1.0
    for piece_start, piece_end in pieces:
        glyph, aspect = extract(mask, piece_start, piece_end)
        if glyph is None:
            return None
        char, score = classify(glyph, aspect, font, scale, skin)
        if char is None or score < MIN_GLYPH_SCORE:
            return None
        characters.append(char)
        weakest = min(weakest, score)
    return characters, weakest


# ---- reading ---------------------------------------------------------------


def extract(mask, start, end):
    """One character crop: (boxed glyph, natural aspect) or (None, 0).

    Unlike digits.extract_glyph this does NOT trim to the character's own
    ink rows - the full line height is the canvas. Two reasons, both
    learned the hard way: a hyphen trimmed to its ink is a featureless
    solid block (zero variance, so normalized correlation degenerates to
    zero and the whole line dies), and vertical position is real signal -
    a hyphen lives mid-line, a period on the baseline, a descender hangs
    below. The aspect is width over LINE height, measured before the
    squash so a stretched "i" cannot impersonate an "l"; the harvest tool
    cuts templates the same way.
    """
    column_slice = mask[:, start:end]
    if column_slice.max() == 0:
        return None, 0.0
    aspect = column_slice.shape[1] / column_slice.shape[0]
    boxed = cv2.resize(column_slice, (digits.GLYPH_WIDTH, digits.GLYPH_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    return boxed, aspect


# The font, flattened into arrays for matching. Rebuilt whenever a
# different font dict arrives, which in practice is once per process.
#
# Why this exists: classify is called for every run of every line of every
# poll, and it used to compare against each variant in a Python loop. That
# was affordable at 1073 variants and stopped being so at 2244 - measured
# on a busy Castle-age panel, one read went to 921ms median and 1510ms at
# worst, against a poll budget of about 300ms. The arithmetic is identical;
# it is one matrix multiply instead of two thousand array multiplies, and
# the aspect gate becomes a boolean mask over the same rows.
_packed = {"font": None, "matrix": None, "labels": None,
           "aspects": None, "scales": None, "skins": None}


def _pack(font):
    """(matrix, labels, aspects, scales, skins), cached on identity."""
    if _packed["font"] is font:
        return (_packed["matrix"], _packed["labels"], _packed["aspects"],
                _packed["scales"], _packed["skins"])
    rows, labels, aspects, scales, skins = [], [], [], [], []
    for label, variants in font.items():
        for template, template_aspect, harvest_scale, skin in variants:
            rows.append(template.ravel())
            labels.append(label)
            aspects.append(template_aspect or 1e-6)
            # NaN marks a universal variant: comparisons with NaN are
            # False, and the mask below treats that as "always allowed".
            scales.append(float("nan") if harvest_scale is None
                          else harvest_scale)
            # "" marks skin-universal the same way.
            skins.append(skin or "")
    matrix = (np.stack(rows) if rows
              else np.zeros((0, digits.GLYPH_WIDTH * digits.GLYPH_HEIGHT),
                            dtype="float32"))
    _packed.update(font=font, matrix=matrix, labels=labels,
                   aspects=np.asarray(aspects, dtype="float64"),
                   scales=np.asarray(scales, dtype="float64"),
                   skins=np.asarray(skins, dtype=object))
    return (matrix, labels, _packed["aspects"], _packed["scales"],
            _packed["skins"])


def _allowed_mask(aspect, aspects, scales, scale, skins=None, skin=None):
    """Which packed variants may compete for this glyph.

    The aspect gate as ever - a stretched "i" must not impersonate an "l" -
    plus the scale gate: a variant tagged with a rendering only competes
    when the line being read is near that rendering. Universal variants
    (scale NaN) always compete, so a font with no tags behaves exactly as
    it always did.

    And the SKIN gate, because the two skins draw different fonts at the
    same scale and the scale gate cannot separate them: a skin-tagged
    variant competes only when the reader knows it is looking at that
    skin. A caller with no skin lets everything compete - dev tools and
    tests without an anchor - but the live reader always knows, from the
    same identification that picked the HUD profile.
    """
    ratio = aspect / aspects
    allowed = (ratio <= ASPECT_TOLERANCE) & (ratio >= 1 / ASPECT_TOLERANCE)
    if scale is not None:
        with np.errstate(invalid="ignore"):
            near = np.abs(scales - scale) <= SCALE_TOLERANCE
        allowed &= near | np.isnan(scales)
    if skin is not None and skins is not None:
        allowed &= (skins == "") | (skins == skin)
    return allowed


def classify(glyph, aspect, font, scale=None, skin=None):
    """(character, score) for the best glyph match, aspect-, scale- and
    skin-gated."""
    matrix, labels, aspects, scales, skins = _pack(font)
    if matrix.shape[0] == 0:
        return None, 0.0
    normalized = digits._normalize(glyph).ravel()
    allowed = _allowed_mask(aspect, aspects, scales, scale, skins, skin)
    if not allowed.any():
        return None, 0.0
    # mean() over the flattened box is dot() over the same length, and the
    # division is a constant, so it does not change which template wins -
    # but it is kept so the SCORE is the number the gates were measured on.
    scores = matrix @ normalized / normalized.size
    scores[~allowed] = -1.0
    best = int(np.argmax(scores))
    if scores[best] < 0:
        return None, 0.0
    return char_for(labels[best]), float(scores[best])


def space_gap_for(runs, height):
    """How wide a gap has to be, in THIS line, to be a space.

    Measured from the line rather than taken as a fraction of its height,
    and the difference is not academic - a fraction cannot do this job at
    all. Across the fixtures and two captures of one game at two
    resolutions:

        1080p, 15px line     letter gaps to 3px (0.200 of height)
        mangonel, 34px line  word gaps from 7px (0.206 of height)

    The biggest letter gap and the smallest word gap sit six thousandths
    apart when expressed as fractions, on opposite sides of what the answer
    should be. They do not overlap in PIXELS, though: letter gaps run 1-3px
    and word gaps 5-8px at every rendering measured. The gaps are simply not
    proportional to the line height - the letter ones are already at the
    floor a pixel grid allows, and cannot shrink further - so a fraction
    tuned at one size will always be wrong at another. The old 0.15 was
    measured at 26-34px lines and put the gate at 2.25px on a 1080p line,
    straight through the letter gaps: "Bu iet" for Built.

    So: find the widest JUMP between the distinct gap widths this line
    actually has, and put the threshold in it. A line with words in it is
    sharply bimodal and the jump is obvious; the floor below is what stops a
    single-word line, whose only jump might be 1px to 2px, deciding it has a
    space in the middle.
    """
    floor = max(MIN_SPACE_PIXELS, height * SPACE_FRACTION)
    gaps = sorted({runs[index + 1][0] - runs[index][1]
                   for index in range(len(runs) - 1)})
    if len(gaps) < 2:
        return floor
    widest, at = 0, None
    for low, high in zip(gaps, gaps[1:]):
        if high - low > widest:
            widest, at = high - low, (low + high) / 2
    return max(floor, at if at is not None else floor)


# A gap wider than this many line heights is not a word space - it is the
# message ending. See trim_to_message.
WORLD_GAP_HEIGHTS = 1.0


def trim_to_message(runs, height):
    """The runs belonging to the message, dropping the world beyond it.

    The band is as wide as the feed's CROP, and the game's message box
    ends about 250px before that crop does - so anything the game happens
    to draw in the WORLD at the same height as a line lands in the same
    strip and is segmented as if it were text. It is not text, it cannot
    classify, and one unclassifiable run refuses the whole line.

    Found in a real game: a unit's white health bar sat level with
    "--Barracks Built--". Cropped to the box that line reads at 0.921;
    with the health bar left on the end it read nothing at all, three
    looks running, and the barracks never ticked off the build. The
    market and the farms of the same game went the same way. Nothing
    failed loudly - the line simply never existed, which is the
    allowlist failure mode in a new place.

    The threshold is the line's own height, and the two things it
    separates are nowhere near it: word gaps run 5-8px at every rendering
    measured (see space_gap_for) while the void between a message and the
    world beside its box is a couple of hundred. A message is contiguous
    text, so nothing past the first such gap belongs to it.

    Only the trailing case is repaired, because only it can be repaired:
    world content to the LEFT of the box would take the message with it
    and the line still refuses - the same refusal as today, and a
    refusal is the safe direction.
    """
    for index in range(len(runs) - 1):
        if runs[index + 1][0] - runs[index][1] > height * WORLD_GAP_HEIGHTS:
            return runs[:index + 1]
    return runs


# ---- structural repair -----------------------------------------------------
#
# The two repairs below substitute LETTERS. This one changes how many
# letters there are, and it exists because a small rendering breaks the
# segmentation itself rather than the classification.
#
# At 1920x1080 a capital H loses its crossbar entirely - absent from the
# mask, not faint - so its two verticals segment as separate runs, each a
# confident "l". "--House Built--" reads "--llouse Built--" and the
# commonest event in any build order is lost. The same thing happens to C,
# L, r, m and u, and at 2560x1440 it happens in reverse: "ll" welds into a
# single run that classifies as "B", so "Villager" reads "ViBager".
#
# Scores cannot arbitrate this, and it is worth being precise about why
# before anyone tries: the rejoined H scores 0.906, LOWER than either of
# the halves it is made of (0.969 and 0.932), because a clean fragment is
# a simpler shape than the letter it came from. Meanwhile a real "ll" in
# "Villager" reads 1.00 and 1.00. There is no threshold anywhere that
# separates those two cases, and three attempts to find one all failed.
#
# The vocabulary separates them without looking at a score at all.
# "llouse" is not a game word; "Villager" is. So the repair fires only on
# a token the vocabulary does not recognise, tries the structural edits
# the ink actually supports, and accepts only if exactly one of them
# yields a real word.
#
# The safety property that makes this affordable: it runs ONLY on a line
# read_line has already thrown away - no text, or text whose words are not
# game words. A line Loom already understood is never restructured, so
# this cannot change a right answer into a wrong one. It can only turn a
# discarded line into a read one, or leave it discarded.
#
# The residual risk is the allowlist failure mode wearing a new hat: if a
# REAL word were missing from KNOWN_WORDS, a correctly-read token would
# look unknown here and might be "repaired" into a word that is in the
# list. That is why every repair is logged, and why the edit must be
# something the ink supports rather than the nearest dictionary word.

# A join or split only enters the search if the new shape classifies at
# least this well. Deliberately BELOW MIN_GLYPH_SCORE: the whole premise
# is that a broken letter's rejoined shape scores worse than its own clean
# fragments, so a gate tuned for confident single glyphs would refuse the
# very cases this exists for. The vocabulary is what decides; this only
# keeps shapes the ink does not support at all out of the search.
STRUCTURAL_GLYPH_FLOOR = 0.70

# How many edits one token may be repaired by, and how far past that the
# search still looks. Measured on the 1080p corpus: real repairs need one
# or two - "Created" needs two, a rejoined C and a rejoined r - and the
# extra tier exists only so a rival can VETO, never so it can win.
MAX_STRUCTURAL_EDITS = 2
STRUCTURAL_RIVAL_REACH = 1

# How many of a line's words this pass may rebuild, and it is a safety
# bound rather than a tuning choice.
#
# A broken letter damages one word; a broken RENDERING damages the line.
# The search is powerful enough to walk a badly-read line into a
# real-sounding sentence, and the corpus caught it doing exactly that:
#
#   "--Spearman Created--" read as "--Siega Rane Coreated--" and rebuilt,
#   three words at once, into "--Siege Ram Created--" - the Slege Ram bug
#   reborn through a new door.
#
# That is repair_unknown_word's "two unknown words means the line is badly
# read" ruling, at the granularity that actually holds at 1920x1080:
# counting the words REBUILT rather than the words unrecognised, because
# at that size an ordinary line has several of the latter and only one of
# the former.
#
# Two, not one, and the difference was measured on the 924-line corpus
# rather than reasoned about. Both are honest - neither produces a single
# wrong event - but one costs 41 correct ones, because a real line
# routinely needs its subject AND its "Created--" put back together:
#
#     cap 1   791 events   0 wrong
#     cap 2   832 events   0 wrong
#     cap 3   844 events   2 wrong      <- fabrications start here
#
# The cap does not stand alone. At cap 2 without the capitalisation rule
# below the corpus still held one fabricated event, and at cap 3 with it
# there are two; each rule refuses cases the other admits, so neither is
# redundant and neither is sufficient.
MAX_REPAIRED_TOKENS = 2


def _alternatives_from(top):
    """[(cost, text)] for one already-classified shape: its best reading,
    and its runner-up when the ink genuinely nearly said that instead.

    `top` is a _classify_top2 result, passed in rather than recomputed -
    the same pixels were classified for the plain reading already, and
    that multiply is the expensive part of reading a line.

    The runner-up matters here for a reason particular to a small
    rendering. When a letter breaks, what breaks is the stroke JOINING its
    verticals - so a split "u" and a split "n" are the same two bare
    verticals, and rejoining them gives a shape no classifier can assign.
    Measured: the rejoined u of "Built" reads n at 0.901 with u second at
    0.893, eight thousandths behind. That is not a classifier failure to
    be tuned away, it is genuine ambiguity in the ink, and the vocabulary
    is the only thing that can settle it.
    """
    if top is None:
        return []
    char, score, char2, score2 = top
    if char is None or score < STRUCTURAL_GLYPH_FLOOR:
        return []
    options = [(0, char)]
    # The same evidence rule repair_unknown_word applies: a runner-up must
    # be what the ink nearly said, not merely the next name on the list.
    if (char2 is not None and score2 >= REPAIR_FLOOR
            and score - score2 <= REPAIR_MARGIN):
        options.append((1, char2))
    return options


def _frame_words():
    """The event phrasings, which the vocabulary deliberately never holds.

    parse_event strips the suffix before the KNOWN_WORDS gate runs, so
    "built" and "created" are not in it. A token check that did not know
    them would call every second token of every line unknown.
    """
    return {kind for _suffix, kind in EVENT_SUFFIXES} | {
        'built', 'created', 'found', 'research', 'complete',
        'destroyed', 'lost'}


def _token_is_known(spelling):
    """Is every alphabetic word in this token a real game word?

    Non-alphabetic characters are separators, exactly as slugify treats
    them, so the framing "--" and the hyphen inside "Two-Handed" both fall
    out rather than needing a case. A token with no letters at all - the
    framing dashes on their own - is known by default: there is nothing in
    it to be wrong about.
    """
    words = "".join(c if c.isalpha() else " " for c in spelling).split()
    if not words:
        return True
    vocabulary = KNOWN_WORDS | _frame_words()
    return all(word.lower() in vocabulary for word in words)


def _keeps_the_games_capitals(spelling):
    """Does this candidate still look like something the game would draw?

    The game capitalises EVERY word of a notification - checked across all
    924 labelled corpus lines, there is not one lowercase-initial word
    among them - so a rebuilt token that comes out lowercase is not a
    repair, it is a coincidence that happens to be spelled like a real
    word.

    This is what refuses the last fabricated event the corpus held.
    "--Carvel Hull Research Complete--" reads "Hoill" at 1080p, whose
    first two runs join into a lowercase "m", giving "mill" - a real word,
    one edit away, with no rival to keep it honest, and Loom minted a
    "Carvel Mill" technology that does not exist. The game would have
    drawn a capital there. Every legitimate repair keeps its capital:
    House, Built, Created, Complete.

    Each alphabetic run is checked, not just the first, because the game
    capitalises both halves of "Two-Handed" and "Double-Bit" too.
    """
    for word in "".join(c if c.isalpha() else " "
                        for c in spelling).split():
        if not word[0].isupper():
            return False
    return True


def _token_spans(runs, space_gap):
    """The runs grouped into tokens: [[run index, ...], ...].

    Tokens - what sits between two spaces - rather than alphabetic words,
    because the failure this serves BREAKS letters, and a half-read "r"
    classifies as a hyphen. Splitting into words first would cut "Created"
    in two before the repair ever saw it.
    """
    tokens = []
    current = []
    for index, (start, _end) in enumerate(runs):
        if current and start - runs[index - 1][1] >= space_gap:
            tokens.append(current)
            current = []
        current.append(index)
    if current:
        tokens.append(current)
    return tokens


def _plain_reading(mask, runs, token, font, scale, skin):
    """Each run of the token classified on its own: (chars, scores, tops).

    The cheap pass, and it is kept separate from the expensive one on
    purpose. Classifying is a matrix multiply against the whole packed
    font and it dominates the cost of reading a line, so a token that
    already spells a real game word must not pay for joins, splits and
    runner-ups it will never use. On "--Villager Created--" only one of
    the three tokens is broken.

    `tops` carries the top-two result per position so the search below can
    reuse it rather than classifying the same pixels a second time.
    """
    chars, scores, tops = {}, {}, {}
    for position, index in enumerate(token):
        start, end = runs[index]
        glyph, aspect = extract(mask, start, end)
        if glyph is None:
            continue
        top = _classify_top2(glyph, aspect, font, scale, skin)
        if top[0] is None:
            continue
        chars[position] = top[0]
        scores[position] = top[1]
        tops[position] = top
    return chars, scores, tops


def _structural_options(mask, runs, token, tops, font, scale, skin):
    """What the ink supports at each position of one token.

    Returns (ones, twos): `ones` are the readings that consume one run -
    the run alone, its runner-up, or the run split at its pinch - and
    `twos` those that consume this run and the next, which is a broken
    letter put back together. Each carries the cost of claiming it.

    Everything is classified ONCE here so the search below runs over
    strings rather than pixels: a token of eleven runs costs thirty-odd
    classifies in total, not thirty per candidate spelling.
    """
    ones, twos = {}, {}
    height = mask.shape[0]
    for position, index in enumerate(token):
        start, end = runs[index]
        choices = list(_alternatives_from(tops.get(position)))
        pieces = _pinch_split(mask, start, end, height)
        if len(pieces) > 1:
            chars = []
            for piece_start, piece_end in pieces:
                piece, piece_aspect = extract(mask, piece_start, piece_end)
                if piece is None:
                    chars = None
                    break
                char, score = classify(piece, piece_aspect, font, scale, skin)
                if char is None or score < STRUCTURAL_GLYPH_FLOOR:
                    chars = None
                    break
                chars.append(char)
            if chars:
                choices.append((1, "".join(chars)))
        if choices:
            ones[position] = choices
        if position + 1 < len(token):
            following = token[position + 1]
            glyph, aspect = extract(mask, start, runs[following][1])
            if glyph is not None:
                joined = _alternatives_from(
                    _classify_top2(glyph, aspect, font, scale, skin))
                # A join is itself a claim about the ink, so it costs one
                # whichever reading of the joined shape is taken.
                joined = [(1 + extra, text) for extra, text in joined]
                if joined:
                    twos[position] = joined
    return ones, twos


def _spellings(length, ones, twos, budget):
    """Every spelling the token can take within `budget` edits.

    Yields (edits, spelling). Positions are consumed strictly left to
    right, so two edits can never overlap by construction - a run is read
    alone, altered, split, or swallowed by a join, never two of those. The
    budget prunes as it descends rather than after the fact, which is what
    keeps an eleven-run token to a couple of thousand candidates instead
    of an exponential sweep.
    """
    if length <= 0:
        yield 0, ""
        return

    def walk(position, remaining):
        if position >= length:
            yield 0, ""
            return
        for cost, text in ones.get(position, ()):
            if cost > remaining:
                continue
            for edits, rest in walk(position + 1, remaining - cost):
                yield edits + cost, text + rest
        for cost, text in twos.get(position, ()):
            if cost > remaining:
                continue
            for edits, rest in walk(position + 2, remaining - cost):
                yield edits + cost, text + rest

    yield from walk(0, budget)


def _repair_token(length, ones, twos):
    """One token's repaired spelling, or None to leave it as it reads.

    The acceptance rule is nearest_event_repair's, for the same reason it
    has one. Of the edit sets that yield a real game word only the
    SMALLEST count, there must be exactly one distinct word among them,
    AND it must beat every other real word by a clear edit. A winner that
    merely came first is a winner on points, and the corpus has already
    shown what that costs: "Hoill" - a broken "Hull" - reaches "mill" in
    one join and "Hull" in a join plus the joined shape's runner-up. Take
    the nearest and Loom mints "Carvel Mill Research Complete", a
    technology that does not exist. See the rival and it refuses, which is
    the right answer.
    """
    by_edits = {}
    for edits, candidate in _spellings(
            length, ones, twos,
            MAX_STRUCTURAL_EDITS + STRUCTURAL_RIVAL_REACH):
        if (edits and _token_is_known(candidate)
                and _keeps_the_games_capitals(candidate)):
            by_edits.setdefault(edits, set()).add(candidate)
    if not by_edits:
        return None
    fewest = min(by_edits)
    if fewest > MAX_STRUCTURAL_EDITS:
        return None                 # only a vetoing tier was reached
    winners = by_edits[fewest]
    if len(winners) != 1:
        return None                 # two real words equally close: refuse
    winner = next(iter(winners))
    rivals = {other for edits, words in by_edits.items()
              if edits <= fewest + STRUCTURAL_RIVAL_REACH
              for other in words if other != winner}
    if rivals:
        return None                 # won on points, not outright
    return winner


def _structural_read(mask, runs, font, scale, skin, space_gap):
    """The line re-read with the segmentation allowed to be wrong.

    Returns (text, weakest) or (None, 0.0). The line must carry its "--"
    framing, because a fragment must never become an event.

    ONE token may survive unrecognised, and only if every run in it
    classified confidently on its own. That is the hand-off to the two
    letter-level repairs downstream, and it is what the real 1080p line
    needs: "--llouse Bnilt--" holds two independent faults, a broken H
    that only a join can fix and an n-for-u that only a substitution can.
    Demanding that this pass finish the job alone refused the line for the
    half of it this pass cannot do, while leaving exactly one unknown word
    is precisely the shape repair_unknown_word and nearest_event_repair
    are built to take. More than one, and the rendering is too far gone to
    reason about - the same judgement those two already make.
    """
    tokens = _token_spans(runs, space_gap)
    if not tokens:
        return None, 0.0
    spellings = []
    repaired = 0
    left_unknown = 0
    for token in tokens:
        singles, scores, tops = _plain_reading(mask, runs, token, font,
                                               scale, skin)
        plain = "".join(singles.get(position, "")
                        for position in range(len(token)))
        whole = len(singles) == len(token)
        # A token that already spells a real game word is done, and pays
        # for none of the search below.
        if whole and _token_is_known(plain):
            spellings.append(plain)
            continue
        ones, twos = _structural_options(mask, runs, token, tops, font,
                                         scale, skin)
        fixed = _repair_token(len(token), ones, twos)
        if fixed is not None:
            repaired += 1
            if repaired > MAX_REPAIRED_TOKENS:
                return None, 0.0
            spellings.append(fixed)
            continue
        # Nothing structural explains this token. It may still be one
        # confident letter misread, which is somebody else's job - but
        # only if the ink was confident about all of it. A token holding a
        # run nobody could classify is not a misread word, it is an
        # unreadable one.
        confident = whole and all(score >= MIN_GLYPH_SCORE
                                  for score in scores.values())
        if not confident or left_unknown:
            return None, 0.0
        left_unknown += 1
        spellings.append(plain)
    if not repaired:
        return None, 0.0
    text = " ".join(spellings).strip()
    if not (text.startswith("--") and text.endswith("--")):
        return None, 0.0
    return text, STRUCTURAL_GLYPH_FLOOR


# How close a glyph's runner-up must be for a repair to consider it, and
# the floor it must clear. Measured on the case this exists for: at one
# 2560x1440 rendering the "b" of "Stable" scores lower_h 0.929 against
# lower_b 0.915 - a 0.014 gap, a coin toss per sub-pixel position, and
# harvesting more variants cannot fix a margin that small. 0.06 covers it
# with room; a runner-up further behind than that is not "what the ink
# says", it is a different letter.
REPAIR_MARGIN = 0.06
REPAIR_FLOOR = MIN_GLYPH_SCORE


def _classify_top2(glyph, aspect, font, scale=None, skin=None):
    """(best_char, best_score, runner_up_char, runner_up_score).

    The runner-up is the best DIFFERENT character, not the second variant
    of the same one - h beating h tells a repair nothing.
    """
    matrix, labels, aspects, scales, skins = _pack(font)
    if matrix.shape[0] == 0:
        return None, 0.0, None, 0.0
    normalized = digits._normalize(glyph).ravel()
    allowed = _allowed_mask(aspect, aspects, scales, scale, skins,
                            skin)
    if not allowed.any():
        return None, 0.0, None, 0.0
    scores = matrix @ normalized / normalized.size
    scores[~allowed] = -1.0
    order = np.argsort(scores)[::-1]
    best = order[0]
    if scores[best] < 0:
        return None, 0.0, None, 0.0
    best_char = char_for(labels[best])
    for index in order[1:]:
        if scores[index] < 0:
            break
        char = char_for(labels[index])
        if char != best_char:
            return best_char, float(scores[best]), char, float(scores[index])
    return best_char, float(scores[best]), None, 0.0


def repair_unknown_word(text, alternatives):
    """One evidence-backed substitution, or None to leave the text alone.

    NOT spell-correction, and the difference is the whole safety case: a
    repair may only substitute a glyph's own RUNNER-UP reading - a letter
    the ink itself nearly said - and only when everything else lines up:

      * the line is framed (it is a real notification, not a fragment);
      * exactly ONE word is unknown to the vocabulary;
      * exactly ONE substitution produces exactly ONE known word.

    Any ambiguity refuses. Two candidate repairs to different words, or two
    positions that both fix it, and nothing is claimed - the KNOWN_WORDS
    gate exists because near-twin glyphs once minted "Slege Ram", and a
    repair that guessed between candidates would be that bug reborn.

    Why it exists: some pairs are true near-twins at some renderings, and
    no amount of harvesting separates them. Measured: "Stable" read as
    "Stahle" 23 times in one game, b scoring 0.915 against h's 0.929, so
    built:stable never fired - while the b was sitting right there in
    second place.

    `alternatives` is one entry per character of `text`: (char2, score2,
    margin) for a character with a viable runner-up, None otherwise.
    """
    stripped = text.strip()
    if not (stripped.startswith("--") and stripped.endswith("--")):
        return None

    # The words, with their character positions in `text`.
    words = []
    start = None
    for index, char in enumerate(text + " "):
        if char.isalpha():
            if start is None:
                start = index
        elif start is not None:
            words.append((start, text[start:index]))
            start = None
    # The event phrasings count as known here even though KNOWN_WORDS never
    # carries them - it only ever vets SUBJECTS, because parse_event strips
    # the suffix before the vocabulary gate runs. Without these, every line
    # has at least two unknown words ("Stahle" and "Built") and the repair
    # never fires on anything.
    frame_words = {kind for _suffix, kind in EVENT_SUFFIXES} | {
        'built', 'created', 'found', 'research', 'complete',
        'destroyed', 'lost'}
    unknown = [(at, word) for at, word in words
               if word.lower() not in KNOWN_WORDS
               and word.lower() not in frame_words]
    if len(unknown) != 1:
        return None
    at, word = unknown[0]

    repairs = set()
    for offset in range(len(word)):
        alternative = alternatives[at + offset]
        if alternative is None:
            continue
        char2, score2, margin = alternative
        if (char2 is None or not char2.isalpha()
                or score2 < REPAIR_FLOOR or margin > REPAIR_MARGIN):
            continue
        candidate = word[:offset] + char2 + word[offset + 1:]
        if candidate.lower() in KNOWN_WORDS:
            repairs.add((offset, char2, candidate))
    if len(repairs) != 1:
        return None                     # none, or ambiguous: leave it alone
    offset, char2, _candidate = next(iter(repairs))
    fixed = at + offset
    return text[:fixed] + char2 + text[fixed + 1:]


# How far a misread word may sit from the vocabulary word it is repaired
# to, and how short a word may be repaired at all. Measured on the stock
# HUD, where the font's coverage is thinnest: the reader's misses run one
# or two letters ("Markn" for Market, "Buih" for Built, "Eound" for
# Found), never more - three letters wrong is a different word, not a
# misread. Words under four letters are never repaired: two edits on a
# three-letter word is half the word, and "Ram"/"Farm" sit two edits
# apart in the real vocabulary.
REPAIR_DISTANCE = 2
REPAIR_MIN_LENGTH = 4


def _edit_distance(a, b, cap):
    """Levenshtein distance, giving up past `cap` (returns cap + 1)."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def nearest_event_repair(text):
    """(event, repaired_text) for a line one word short of an event, or
    None to claim nothing.

    The second repair layer, for when repair_unknown_word's ink evidence
    is not available - the whole word read confidently and wrongly, which
    is what a rendering the font barely knows produces ("--Markn Built--"
    on the stock HUD). NOT spell-correction against the vocabulary alone,
    and the difference is the safety case: a candidate word is accepted
    only when substituting it makes the WHOLE LINE parse to exactly one
    real event. That is what breaks ties the word level cannot - "Buih"
    sits two edits from both "Built" and "Build", but only one of them
    makes "--Blacksmith ___--" a line the game can print. The grammar is
    the game's truth; matching against what the build HOPES for would be
    the never-guess rule broken in a new place.

    The rules, every one refusing toward silence:

      * the line is framed, and exactly ONE word is unknown;
      * the unknown word is REPAIR_MIN_LENGTH letters or longer;
      * candidates sit within REPAIR_DISTANCE edits, from the vocabulary
        or the event phrasings;
      * of the candidates at the SMALLEST distance that yield an event,
        there is exactly one distinct event - two different events at the
        same distance, and nothing is claimed. The "line:" fallback never
        counts: repairs may only produce events, not unclassified lines;
      * the winner must beat every OTHER event-yielding candidate by two
        clear edits. Measured on the labelled corpus: without the margin,
        "--GaBeon Created--" (a merged-ll misread of Galley) repaired to
        Galleon, a real entity ONE edit nearer than the truth - the only
        wrong repair in thirty-nine. A margin of two is the gap between
        "nothing else comes close" and "it won on points".
    """
    stripped = text.strip()
    if not (stripped.startswith("--") and stripped.endswith("--")):
        return None

    words = []
    start = None
    for index, char in enumerate(text + " "):
        if char.isalpha():
            if start is None:
                start = index
        elif start is not None:
            words.append((start, text[start:index]))
            start = None
    frame_words = {kind for _suffix, kind in EVENT_SUFFIXES} | {
        'built', 'created', 'found', 'research', 'complete',
        'destroyed', 'lost'}
    unknown = [(at, word) for at, word in words
               if word.lower() not in KNOWN_WORDS
               and word.lower() not in frame_words]
    if len(unknown) != 1:
        return None
    at, word = unknown[0]
    if len(word) < REPAIR_MIN_LENGTH:
        return None

    # Searched one edit past the acceptance cap, so a rival just outside
    # it can still veto - the margin rule needs to SEE the galley to keep
    # the galleon honest.
    by_distance = {}
    for candidate in KNOWN_WORDS | frame_words:
        distance = _edit_distance(word.lower(), candidate,
                                  REPAIR_DISTANCE + 1)
        if not 0 < distance <= REPAIR_DISTANCE + 1:
            continue
        # Match the read word's shape: the game capitalises every word of
        # a notification, so keep the first letter's case as read.
        cased = (candidate.capitalize() if word[0].isupper() else candidate)
        repaired = text[:at] + cased + text[at + len(word):]
        event = parse_event(repaired)
        if event is None or event.startswith("line:"):
            continue
        by_distance.setdefault(distance, {})[event] = repaired
    if not by_distance or min(by_distance) > REPAIR_DISTANCE:
        return None
    best = min(by_distance)
    nearest = by_distance[best]
    if len(nearest) != 1:
        return None                     # two events equally close: refuse
    event, repaired = next(iter(nearest.items()))
    rivals = {other for distance, events in by_distance.items()
              if distance <= best + 1 for other in events
              if other != event}
    if rivals:
        return None                     # won on points, not outright
    return event, repaired


def read_line(line_bgr, font, scale=None, skin=None):
    """The line as text, or (None, 0.0) if any character is not believed.

    Spaces come from gaps: the font's word gaps are far wider than its
    letter gaps, so a gap over SPACE_FRACTION of the line height reads as
    one space.

    `scale` gates which font variants compete (see _allowed_mask). When no
    caller knows it, the line's own height is the hint - the same formula
    the harvest tags variants with, so hint and tags agree by
    construction. An anchor-measured scale, where a caller has one, is the
    better number and wins.
    """
    if not font:
        return None, 0.0
    if scale is None and line_bgr is not None and line_bgr.shape[0] > 0:
        scale = line_bgr.shape[0] / LINE_HEIGHT_AT_FULL_SCALE
    mask, runs = segment_line(line_bgr)
    if not runs:
        return None, 0.0

    height = mask.shape[0]
    # Before anything is measured FROM the runs: the world showing past
    # the end of the message box is not part of this line, and leaving it
    # in refuses the whole line. Trimmed first so the space gap below is
    # measured on the message alone.
    runs = trim_to_message(runs, height)
    space_gap = space_gap_for(runs, height)
    characters = []
    # One entry per character: the glyph's runner-up reading, for the
    # unknown-word repair below. None for anything that is not a plain
    # single-glyph classification - spaces, split pieces, merged pairs.
    alternatives = []
    weakest = 1.0
    weak_used = False
    previous_end = None
    index = 0
    # A glyph nobody believes used to return here and now falls through, so
    # the structural pass below still gets to see the runs. The refusal is
    # unchanged - `refused` is checked before any text is returned - but a
    # line that dies at one orphaned fragment is exactly the line worth
    # re-reading with the segmentation in question.
    refused = False
    while index < len(runs):
        start, end = runs[index]
        index += 1
        if previous_end is not None and start - previous_end >= space_gap:
            characters.append(" ")
            alternatives.append(None)
        previous_end = end

        glyph, aspect = extract(mask, start, end)
        if glyph is None:
            continue
        char, score, char2, score2 = _classify_top2(glyph, aspect, font,
                                                     scale, skin)
        if char is None or score < MIN_GLYPH_SCORE:
            # Before giving up: this may be two touching letters. The
            # split must EARN acceptance - the whole run failed AND every
            # piece classifies - or an "m" would read as "ln" (it did,
            # once, in a real game: "colnplete").
            pieces = _read_split(mask, start, end, font, scale, skin)
            if pieces is not None:
                chars, piece_weakest = pieces
                characters.extend(chars)
                alternatives.extend([None] * len(chars))
                weakest = min(weakest, piece_weakest)
                continue
            # Or the opposite problem: one letter that came apart into two
            # runs, which is what a small rendering does. See _read_join.
            joined = _read_join(mask, runs, index - 1, font, space_gap,
                                scale, skin)
            if joined is not None:
                char, score = joined
                characters.append(char)
                alternatives.append(None)
                weakest = min(weakest, score)
                previous_end = runs[index][1]
                index += 1
                continue
            # Not touching letters either. One slightly-soft glyph per
            # line is forgiven (see WEAK_GLYPH_FLOOR); a second means the
            # rendering is genuinely off, and the line dies rather than
            # guess. Never guess: one BAD glyph still kills the line.
            if (char is not None and score >= WEAK_GLYPH_FLOOR
                    and not weak_used):
                weak_used = True
                characters.append(char)
                alternatives.append((char2, score2, score - score2))
                weakest = min(weakest, score)
                continue
            refused = True
            break
        # Merged pairs read back as several characters; a repair cannot
        # substitute inside them, so they carry no alternative.
        for extra_char in char:
            characters.append(extra_char)
            alternatives.append((char2, score2, score - score2)
                                if len(char) == 1 else None)
        weakest = min(weakest, score)

    text = "" if refused else "".join(characters)
    if text.strip():
        repaired = repair_unknown_word(text, alternatives)
        if repaired is not None:
            text = repaired
        # A framed line whose every token is a real game word is a clean
        # read and is returned untouched. Anything else - no text at all,
        # or text the vocabulary does not recognise - is a line Loom would
        # otherwise discard, so the segmentation gets questioned before it
        # is thrown away. Ordering this test BEFORE the structural pass is
        # what guarantees a right answer can never be restructured into a
        # wrong one.
        stripped = text.strip()
        framed = stripped.startswith("--") and stripped.endswith("--")
        if framed and all(_token_is_known(token)
                          for token in stripped.split()):
            return stripped, weakest
    structural, structural_score = _structural_read(mask, runs, font, scale,
                                                    skin, space_gap)
    if structural is not None:
        return structural, structural_score
    if refused or not text.strip():
        return None, 0.0
    return text.strip(), weakest


# ---- from text to events ---------------------------------------------------

# Every word an event subject may contain. The subjects are GAME ENTITIES
# - a finite vocabulary - and this list is the last line of defence
# against confident misreads: "Slege Ram" and "Rracer" both cleared the
# per-glyph score gate in a real game (i/l and A/R are near-twins when a
# line is fading), but "slege" is not a word, and a subject containing an
# unknown word refuses rather than minting a unit that does not exist.
# Curated by hand from the game's unit/building/technology names; a
# genuinely new word costs one addition here when it shows up.
#
# The cost of a MISSING word is silent, which is worth knowing before
# trusting this list. "camp" was absent until the step checklist went
# looking for the events the shipped builds actually name, and its absence
# refused "--Lumber Camp Built--" and "--Mining Camp Built--" outright - 23
# items across the thirteen builds, every one of them a perfectly read line
# thrown away at the last gate. Nothing anywhere reported it; the events
# simply never arrived.
#
# So the list was then AUDITED against the game's real subject vocabulary
# rather than extended one word at a time, and twenty-two more were
# missing. The worst was "armor", which refused all nine armour upgrades;
# "patrol" refused Town Patrol, which production.py counts as a Town Centre
# technology; "chain", "casting", "husbandry", "keep", "tracking" and
# "architecture" refused their own. A hand-curated allowlist fails silently
# by construction - when adding a subject to any consumer of this module,
# check its words are here, and prefer auditing a whole category to adding
# one word.
KNOWN_WORDS = set("""
acropolis age arambai arbalest arbalester archaic archer archery
architecture armor armored arms arrow arrows arrowslits arson
artemisias artisan assassination at atonement axe axeman ballista
ballistaelephant ballistics banking barding barracks battering battle
berries berserk bireme bit blacksmith blackwood blast block bloodlines
boar bodkin bolas bombard bow bowman boyar bracer buffalo camel
camelrider camp cannon cannoneer capped capybara caravan caravanserai
caravel careening carrack cart cartography carvel casting castle
cataphract catapult cavalier cavalry cavalryarcher ceasefire center
centurion chain chakram champi champion chariot chemistry chicken chu
chuko church civic classical clinker cog coinage collar colonization
complete composite condottiero conquistador conscription construction
coustillier cow crane crop crossbowman cults dao dark dedication deer
defensive democracy demolition demoraft demoship devotion diplomatic
dock dolphin domestication donjon dorado double dragon dragonship
drills dromon druzhina dry eagle eaglescout eaglewarrior economic
elephant elite elitesteppelancer emergency emplacement emplacements
engineers ephorate exorcism faith farm fast fastfireship feather
feitoria fervor festival feudal fire fireship fish fishing flaming
flemish fletching folwark forging fortification fortified furnace
galleon galley gambesons garden gate gbeto gendarme genitour genoese
ghulam gift gillnets goat gold goose grenadier guang guard guardsman
guecha guilds gun halberdier hand handcart handed harbor haruspicy
heated heavy heavycamelrider heavycavalryarcher heavydemoship
heavyscorpion hei hemlock herbal heresy hippagretai hire hoardings
holes hoplite horse houfnice house howdah hulk hull husbandry huskarl
hussar hussite huszar hypozomata ibex ibirapema illumination imperial
imperialskirmisher incendiaries incendiary infantry iron jaguar
janissary jian kamayuk karambit karambitwarrior keep keshik kipchak
knight ko kona konnik kopis kotthybos krepost laminated lancer lancers
leather legionary leitis lembos leviathan liao light lightcavalry
lighthouse lines llama long longboat longbowman longswordsman loom
lumber lysanders maceman magyar mail mameluke man manatarms mangonel
mangudai market marlin masonry medicine mercenaries military militia
mill mining missionary monaspa monastery monk morai mounted mule
murder mystery nu obuch offensive oligarchs oligarchy onager oracle
organ ostrich outpost packed padded pagoda paladin palintonon palisade
paragon parthian pastoralism pasture patrol perch petard phalangites
picked pig pikeman plate plow plumed pontoon practice priest printing
purification quell raft raid raider ram ramming range ranged ratha
rattan rattanarcher recurve redemption redeploy relic repair
requisition rhinoceros rider ring riot rocket rotation runner
sacrificial sail salmon samurai sanctity sapper sappers satrapy savar
saw scale scorpion scout scoutcavalry serjeant settlement shaft sheep
ship shipwright shock shore shot shotel shotelwarrior shrivamsha siege
siegetower siphons skeuophoroi skirmisher slinger slits snapper
spearman spies squires stable steppe steppelancer stone supllies
supplies swordman swordsman syncretism tactics target tarkan telamon
temple teutonic theatre theocracy thirisadai thrower throwing thumb
tiger tower town tracking traction trade tradecart train transhumance
transport transportship trap treadmill treason trebuchet tribute
trireme troops tuna tunnel turkey turtle turtles two twohanded tyranny
university up upgrade urumi villager wagon wall war warrior warships
watch water wheelbarrow white winged woad wonder wood workshop wounded
xianbei xyston yak zebra
""".split())

# The event phrasings the game uses, learned from real lines. Longest
# suffix first, so "research complete" wins before any shorter match.
EVENT_SUFFIXES = (
    (" research complete", "researched"),
    (" created", "created"),
    (" built", "built"),
    (" found", "found"),
    (" destroyed", "destroyed"),
    (" lost", "lost"),
)


def slugify(words):
    """'Town Center' -> 'town_center': the stats-file spelling."""
    cleaned = "".join(c if c.isalnum() or c == " " else " "
                      for c in words.lower())
    return "_".join(cleaned.split())


def parse_event(text):
    """One read line as an event name, or None for a line worth ignoring.

    "--Mill Built--" -> "built:mill"; "--Town Center Built--" additionally
    keeps its legacy name (production counts TCs by it). Attack warnings
    WRAP across two lines - the fixed first line is "--Warning: You are
    being attacked by" and the attacker's name follows on its own line in
    their player colour - so "attacked" parses from the first line alone
    and the name line is free to drop (arbitrary gamer tags are outside
    any font's coverage, and the event is already counted).

    Event lines must carry their "--" framing. A fragment without it is a
    partially-read line, and treating fragments as facts is how a real
    game once recorded "colnplete" as an event. A framed line matching no
    known shape becomes "line:<slug>" - observed facts are kept, even
    unclassified ones - but frame-less text is refused outright.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("--warning:") or "attacked by" in lowered:
        return "attacked"

    # Everything else must be a whole framed line.
    if not (stripped.startswith("--") and stripped.endswith("--")):
        return None
    words = stripped.strip("- ").strip()
    if not words or len(words) < 3:
        return None
    lowered = words.lower()

    for suffix, kind in EVENT_SUFFIXES:
        if lowered.endswith(suffix):
            subject = slugify(words[: -len(suffix)])
            if not subject:
                return None
            # The vocabulary gate: every subject word must be a real game
            # word, or this "event" is a confident misread.
            if any(word not in KNOWN_WORDS
                   for word in subject.split("_")):
                return None
            if kind == "built" and subject == "town_center":
                return "town_center_built"
            return f"{kind}:{subject}"

    slug = slugify(words)
    if any(word not in KNOWN_WORDS for word in slug.split("_")):
        return None
    return f"line:{slug}"


def join_wrapped(stack):
    """Rejoin messages the game split across two lines.

    The notification box is a FIXED width - about 390px of a 2560px frame -
    and the game wraps a message too long for it rather than shrinking it.
    So "--Chain Barding Armor Research Complete--" arrives as two bands:
    "--Chain Barding Armor Research" and then "Complete--" on its own line.
    Measured in a live game; the crop is not what cuts them, the box ends
    250px before the crop does.

    parse_event demands a whole framed "--...--" line, and rightly - a
    fragment treated as a fact is how "colnplete" once became an event - so
    without this every long technology in the game is structurally
    unreadable however good the font gets. Iron Casting reads; Chain Barding
    Armor never can.

    The join is deliberately narrow: an opening frame with no closing one,
    followed by a line that does not open a frame of its own. Anything else
    is left alone, because two unrelated lines glued together would be a
    fabricated event, which is worse than a missing one.

    Unreadable ("pixels") bands never join. A wrap whose second half could
    not be read is a message this cannot honestly reconstruct, so it stays
    two entries and simply does not fire.
    """
    joined = []
    index = 0
    while index < len(stack):
        kind, value = stack[index]
        if kind == "text" and index + 1 < len(stack):
            next_kind, next_value = stack[index + 1]
            opens = value.strip().startswith("--")
            closes = value.strip().endswith("--")
            if (opens and not closes and next_kind == "text"
                    and not next_value.strip().startswith("--")):
                joined.append(("text", f"{value.strip()} {next_value.strip()}"))
                index += 2
                continue
        joined.append((kind, value))
        index += 1
    return joined


# How long after sighting a line before the same text can count as a new
# event, in game seconds - and the trap that makes the BOTTOM rule
# necessary: the feed redisplays HISTORY. After idling it fades, and the
# next message brings recent lines back above itself, so an old line
# resurfaces long past any cooldown. A fresh message always arrives as
# the bottom-most line of the stack; redisplayed history sits above newer
# lines. So only the bottom line may fire, and every visible line
# refreshes its cooldown so history cannot re-fire by scrolling back down.
# How many raw spellings to remember the meaning of. Identifying a line
# walks every sentence the game can print, and it now runs for every line
# on every look rather than only when something arrives - so the same
# misreads, which recur constantly, must not pay for it twice.
IDENTITY_CACHE = 512

TEXT_COOLDOWN_SECONDS = 15

# The longest a single printed line can hold one place in the feed before
# the only honest reading is that the game printed it again. This is the
# ONE thing position cannot decide - a second print landing where the
# first sat, with nothing arriving to push the first up - so it is a
# tiebreaker rather than the mechanism.
#
# It is also the one number here that moves with the game's notification
# duration setting (Options -> Interface), which is why it must be a
# ceiling rather than a measurement: too low and a lingering line is
# counted twice, too high and a genuine quiet-game repeat is missed. 90
# seconds is far above the longest single appearance measured on the
# NORMAL setting (p75 42 game seconds over four recordings of one game),
# so it never fires on a line that is merely lingering. The blind spot it
# leaves - two of the same thing inside one window with nothing else
# arriving - is real, and for units the production queue closes it from
# the other side.
LINE_LIFETIME_SECONDS = 90

# At most one unreadable-line crop is saved per this many game seconds -
# enough to harvest from, not enough to flood the disk.
UNREAD_SAVE_GAP = 30


def _lcs_matched(previous, stack):
    """Indices of `stack` carried over from `previous`, order-preserving.

    The feed's lines move up, never past each other, so the lines both
    looks share form a common subsequence of the two stacks - the largest
    one is the honest guess at "still the same line", and everything it
    cannot match is what changed. Matching is positional, never by text
    alone: a lingering "Villager Created" must not vouch for a new one.
    Stacks are a handful of entries, so the quadratic table is nothing.
    """
    n, m = len(previous), len(stack)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            if previous[i] == stack[j]:
                best[i][j] = best[i + 1][j + 1] + 1
            else:
                best[i][j] = max(best[i + 1][j], best[i][j + 1])
    matched = set()
    i = j = 0
    while i < n and j < m:
        if previous[i] == stack[j]:
            matched.add(j)
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return matched


def _entry_counts(entries):
    """How many copies of each entry a stack holds."""
    counts = {}
    for entry in entries:
        counts[entry] = counts.get(entry, 0) + 1
    return counts


class TextWatcher:
    """Reads the notification feed as text, one event per appearance.

    The glyph-path sibling of notifications.NotificationWatcher, sharing
    its cooldown semantics and the bottom-line rule. Lines the font cannot
    read are saved to captures/notif_unread/ - each saved crop is one
    harvest command away from becoming coverage.
    """

    def __init__(self, save_unread=True):
        self.font = load_font()
        self._last_fired = {}
        # value -> (place, since, last): where this line sat, when it
        # first held that place, and when I last saw it. The whole
        # counting mechanism - see _decide_arrival.
        self._places = {}
        self._identities = {}
        # What was on screen last look, so an absence can be OBSERVED
        # rather than inferred from elapsed time.
        self._last_texts = set()
        # Did the last look read the WHOLE feed? Only then is a line's
        # absence from it evidence that the line has gone.
        self._read_everything_last = False
        self._last_signature = None
        self._last_unread_save = None
        self.save_unread = save_unread
        # Digest -> read result. A line lingers ~10 seconds and gets
        # re-read on every look; with the resampled font variants a busy
        # panel costs ~100ms to read, so each distinct rendering pays
        # that once and lingering is free. The digest is the same coarse
        # fingerprint the stack signature uses, so anything stable enough
        # to track is stable enough to cache.
        self._read_cache = {}

    def watch(self, panel_bgr, game_time, scale=1.0, skin=None):
        """Read the feed once. Returns event names newly sighted.

        `scale` is the HUD scale from the anchor, so the line pitch this
        measures bands against follows the size the game is drawing at.
        It defaults to 1.0 so the harvest tool and the tests can call this
        without one, but the live reader always has it and always passes it.

        Counting works on the STACK SIGNATURE - the tuple of every visible
        line, unreadable ones included as pixel digests - rather than a
        per-text cooldown, because a per-text cooldown counted "Villager
        Created" eight times in a 145-villager game.

        ARRIVALS ARE FOUND BY ALIGNMENT, not by watching the bottom line.
        Bottom-only firing silently dropped every message that arrived
        TOGETHER with a newer one between looks: the older line was
        already one up when first seen and never allowed to speak -
        measured on a fast-forwarded replay, a five-line chunk arrived at
        once and the Mill and Lumber Camp in its middle never fired. The
        feed orders lines by event time, so between looks a new line can
        even INSERT above a newer line that keeps the bottom (measured:
        the Mill slotted above a younger Villager line).

        The carried-over lines of two looks form an order-preserving
        common subsequence - lines move up, never past each other - so
        _lcs_matched finds them, and what it cannot match is what
        changed. Text equality is NOT identity (a lingering "Villager
        Created" must not vouch for a new one), which is why the match
        is positional. The rules:

        * Unmatched entries BELOW the shallowest match ARRIVED - each
          fires. Between-match insertions are arrivals too.
        * The unmatched PREFIX above every match is redisplayed history
          and never fires (found live: one TC fired three times).
        * A static lingering stack never refires (signature unchanged).
        * An arrival that GROWS its text's count fires past the cooldown
          - repeats in a burst are real. An arrival that does not (a
          reorder flicker, a fade-and-return, a same-count replacement)
          is rate-floored by the cooldown.
        * With nothing carried over at all there is no alignment to
          trust, and only a changed bottom line may fire, cooldown
          applied - the lone-line flap case.
        """
        if not self.font or panel_bgr is None or panel_bgr.size == 0 \
                or game_time is None:
            return []

        bands = find_lines(panel_bgr, scale=scale)
        stack = []
        for (y1, y2) in bands:
            line = panel_bgr[y1:y2]
            digest = _band_digest(line)
            if digest in self._read_cache:
                text = self._read_cache[digest]
            else:
                text, _score = read_line(line, self.font, scale, skin)
                # The cache maps renderings, not game state, so dropping
                # it wholesale now and then costs one re-read per visible
                # line and caps the footprint for a whole session.
                if len(self._read_cache) >= 256:
                    self._read_cache.clear()
                self._read_cache[digest] = text
            if text is None:
                stack.append(("pixels", digest))
            else:
                stack.append(("text", text))
        stack = join_wrapped(stack)
        signature = tuple(stack)

        events = []
        # WHERE each line sits, counted from the newest upward: 0 is the
        # bottom line, 1 is one above it, and so on. This is the whole
        # counting mechanism - see _decide_arrival for why position can
        # answer what a timer cannot.
        places = {}
        resolved = {}
        for index, (kind, value) in enumerate(stack):
            if kind != "text":
                continue
            place = len(stack) - 1 - index
            # Identity FIRST. Two spellings of one line are one thing here,
            # where before they were two - see _identify.
            key, event, repaired = self._identify(value)
            resolved[key] = (value, event, repaired)
            lowest, copies = places.get(key, (place, 0))
            places[key] = (min(lowest, place), copies + 1)

        # With no prior knowledge of the feed there is no evidence that
        # anything ARRIVED - a full panel on the first look is just as
        # likely to be history I have never seen. Only the bottom line may
        # speak then. This is the guard that stopped one Town Centre
        # firing three times when the feed faded and redisplayed itself.
        blind = not self._places
        seen_now = set(places)
        for key, (place, copies) in places.items():
            if blind and place != 0:
                self._places[key] = (place, copies, game_time)
                continue
            if self._decide_arrival(key, place, copies, game_time):
                read, event, repaired = resolved[key]
                # Logged on ARRIVAL rather than on identification, or a
                # line lingering for twenty looks would file twenty
                # identical repairs. A repaired event is evidence-backed
                # but not a letter-perfect read, and this trail is what
                # lets a wrong repair be caught rather than trusted.
                if repaired is not None:
                    self._log_repair(read, repaired, game_time)
                if event is not None:
                    events.append(event)

        self._last_texts = seen_now
        self._read_everything_last = not any(kind == "pixels"
                                             for kind, _v in stack)

        # Lines I have not seen for longer than one can possibly live are
        # forgotten, so the next print of that text starts clean.
        for value in [v for v, seen in self._places.items()
                      if v not in places
                      and game_time - seen[2] > LINE_LIFETIME_SECONDS]:
            del self._places[value]

        if stack:
            # The unread-save hook keeps its original trigger: a new
            # unreadable band at the BOTTOM. The bottom is where a new
            # line is sharpest.
            kind, _bottom = stack[-1]
            previous = list(self._last_signature or ())
            depth_grew = len(stack) > len(previous)
            bottom_changed = not previous or previous[-1] != stack[-1]
            if kind == "pixels" and (depth_grew or bottom_changed):
                self._save_unread(panel_bgr[bands[-1][0]:bands[-1][1]],
                                  game_time)
        self._last_signature = signature
        return events

    def _identify(self, value):
        """What a read line IS, before anything is counted about it.

        The counting mechanism keys on this rather than on the text, and
        that ordering is the whole point. Every misread spelling of one
        line is a text never seen before, so keying on the text made five
        readings of one technology into five arrivals:

            --Hand Can--  --Hand Cart--  --Hand Car--  --Hand Caet--

        all of which resolved to `researched:hand_cart` AFTERWARDS, by
        which point five events had already been emitted. The counter was
        never wrong; it was handed five names for one thing. Decide what a
        line is before counting it as anything.

        Returns (key, event, repaired text or None). A line nothing can
        identify keys on its own text: it produces no event either way, so
        two spellings of an unknown line cost nothing, and it still gets
        tracked for presence like any other.

        Cached on the raw text because the same misreads recur constantly -
        `nearest_line` walks every sentence the game can print, and this
        now runs for every line on every look rather than only on arrival.
        """
        if value in self._identities:
            return self._identities[value]

        event, repaired = parse_event(value), None
        if event is None:
            fix = nearest_event_repair(value)
            if fix is not None:
                event, repaired = fix
        if event is None:
            # Imported here rather than at module scope: lines imports
            # glyphs for the vocabulary, and this is the one direction
            # that would close the loop.
            from . import lines as line_reader
            matched = line_reader.nearest_line(value)
            if matched is not None:
                repaired, event = matched

        answer = (event if event is not None else value, event, repaired)
        if len(self._identities) >= IDENTITY_CACHE:
            self._identities.clear()
        self._identities[value] = answer
        return answer

    def _decide_arrival(self, value, place, copies, game_time):
        """Is this sighting a NEW print of the line, or one I already counted?

        Position answers it, and a timer cannot. The feed puts a new line
        at the BOTTOM and everything already there is pushed UPWARD; a
        line has no mechanism to travel down. So, for the NEWEST copy of
        this text on screen:

          * never seen -> a new print. That covers a line appearing at the
            bottom AND the burst case, where several arrived between looks
            and this one was already pushed up before I first saw it.
            Firing only on the bottom line silently dropped those -
            measured, a five-line chunk arrived at once and the Mill and
            Lumber Camp in its middle never fired.
          * MORE COPIES than last look -> at least one more was printed.
            This is the real burst: a second identical line arriving
            beneath the first, which is how a 145-villager game was once
            counted as eight.
          * HIGHER, or unmoved -> the same print, drifting up or sitting.

        A rule that BELONGS here by the invariant and is deliberately NOT
        implemented: "lower than I last saw it, so the game printed
        another". It follows from lines only ever moving up, and it is
        still wrong - a capture taken mid-scroll transposes two adjacent
        lines, and Loom sees a drop the game never made. Position is
        stored and reported because it is the right thing to reason with;
        it is not on its own allowed to convict.

        Why this replaces the cooldown it grew out of. The old rule fired
        on a 15-second timer measured with the game's notification
        duration at its SHORTEST. On the normal setting - the default, and
        what most players will have - a line outlives that cooldown 43% of
        the time, so it re-fired: one lingering "--Barracks Built--" was
        counted ELEVEN times across thirteen game seconds. Position does
        not move when a player changes that setting, so the count stops
        depending on a setting nothing enforces.

        It disposes of the flicker for free, too. A line Loom loses sight
        of and finds again has not moved, so it reads as the same print -
        where the old rule watched it leave the stack and come back, and
        counted it twice.

        WHAT IT STILL CANNOT SEE: a second print landing in the same place
        with nothing arriving to push the first one up. Position cannot
        separate that from a line simply sitting there, and duration is
        the only other evidence - but a lingering line must never refire,
        so that door stays shut here. For units the production queue
        closes it from the other side; for buildings it stays open.
        """
        seen = self._places.get(value)
        if seen is None:
            self._places[value] = (place, copies, game_time)
            return True
        was_at, before, last = seen
        # More copies on screen than last look: a second identical line
        # arrived beneath the first. That is the real burst, and the
        # undercount it fixes is a 145-villager game once counted as eight.
        more = copies > before
        # Or it was gone long enough to have PROVABLY left and come back.
        # This is the cooldown doing the job it is actually good at -
        # judging an absence - rather than rate-limiting a line that never
        # went anywhere. A gap shorter than this is a read that wobbled,
        # not a line that left; a gap longer is a departure.
        # Absence has to be OBSERVED, not inferred from the clock. A line
        # I saw last look never went anywhere however long ago that look
        # was - the poll rate is not a clock, and reading a gap between
        # two distant polls as a departure refired a line that had sat
        # there the whole time.
        #
        # And not inferred from a FAILED READ either, which is the same
        # mistake one step further in. "It is not among the texts I read"
        # and "it is not on the screen" are different claims, and only the
        # second is evidence. A look that left any band unread cannot
        # support the second, so it is no longer allowed to: measured over
        # a whole game this branch produced 103 of 234 firings, and its
        # biggest single contributor was `--ViBager Created--`, one misread
        # of Villager flickering in and out of legibility 33 times.
        #
        # A complete look is still not enough on its own, because a band
        # that was never DETECTED is not an unread band - it is simply not
        # in the stack, so a line fading at the top edge of the panel
        # produces a look that reads everything it found and does not
        # contain the line. Every remaining over-count in a whole game was
        # that: an archery range, a castle and two barracks coming back at
        # the same or a HIGHER position, 15 to 70 seconds later.
        # And it has to have come back LOWER than it sat before. A new
        # print enters at the bottom, so a line that has not moved DOWN
        # cannot be one - it is the line that was already there. Coming
        # back AT the bottom counts on its own: place 0 is where a new
        # print goes, and a lone line on an empty feed never has anywhere
        # lower to return to. Note the
        # direction this is used in: not "lower, therefore a new print",
        # which is the refuted rule above, but "not lower, therefore not a
        # new print". A veto rather than a conviction, and it does not
        # care whether a mid-scroll capture transposed two lines, because
        # a transposition can only ever manufacture the evidence this
        # refuses to accept.
        came_back_lower = place == 0 or place < was_at
        returned = (self._read_everything_last
                    and came_back_lower
                    and value not in self._last_texts
                    and game_time - last >= TEXT_COOLDOWN_SECONDS)
        self._places[value] = (place, copies, game_time)
        return more or returned

    def reset(self):
        """Forget sightings. Call when a new game starts.

        `_identities` is deliberately NOT cleared. It maps a rendering to
        its meaning, and a new game does not change what a line says - the
        same reasoning `_read_cache` is kept on.
        """
        self._last_fired.clear()
        self._places.clear()
        self._last_texts = set()
        self._read_everything_last = False
        self._last_signature = None

    def _log_repair(self, read, repaired, game_time):
        """One line per repair, beside the unread saves: what was read,
        what it was taken to mean, and when - the reviewable trail the
        author asked for ("keep it flagged, not just be 100%")."""
        print(f"notif repair: {read!r} -> {repaired!r} at t={game_time}")
        if not self.save_unread:
            return
        out_dir = paths.CAPTURES_DIR / "notif_unread"
        os.makedirs(out_dir, exist_ok=True)
        with open(out_dir / "repairs.tsv", "a", encoding="utf-8") as log:
            log.write(f"{int(game_time)}\t{read}\t{repaired}\n")

    def _save_unread(self, line_bgr, game_time):
        if not self.save_unread:
            return
        if (self._last_unread_save is not None
                and game_time - self._last_unread_save < UNREAD_SAVE_GAP):
            return
        self._last_unread_save = game_time
        out_dir = paths.CAPTURES_DIR / "notif_unread"
        os.makedirs(out_dir, exist_ok=True)
        cv2.imwrite(str(out_dir / f"line_t{int(game_time)}.png"), line_bgr)


def _band_digest(line_bgr):
    """A small stable fingerprint for an unreadable band, so the stack
    signature can still track it across polls."""
    mask = text_mask(line_bgr)
    small = cv2.resize(mask, (32, 4), interpolation=cv2.INTER_AREA)
    return bytes((small > 96).flatten().tolist()).hex()
