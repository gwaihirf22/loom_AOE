"""
Loom — reading the global production queue off the HUD.

The game shows a "global queue" at the far top-left, just below the leftmost
(wood) resource icon: a row of small unit/tech portraits growing left-to-right,
wrapping onto a second row below when it fills. Each icon stands for one
*consecutive group* of the same item from one building (three villagers queued
in a row show as a single villager icon with a count of 3, and a tech queued
behind them shows as its own icon). Each occupied slot is a bordered portrait
with a count and a coloured progress tint:

  * a GREEN wash over the portrait  — the item is progressing normally. This is a slow progresstion from left to right. when what is complete the item has finished.
  * a RED wash                      — blocked because housed (build a house)
  * a YELLOW/amber wash             — blocked at the population cap (200 in
                                      standard games; houses will not help)
  * no slot at all                  — nothing is producing there

Techs are immune to population blocks: in a fully pop-capped queue every unit
group goes amber but a queued tech keeps its green progress wash. The grid is
capped at two rows of 15; anything queued beyond 30 visible groups is simply
hidden until space frees up. A white highlight ring appears on whichever slot
the mouse hovers over, so border brightness is not a reliable signal.

One honesty note: the display is known to *understate* what is queued (the game
has a long-standing bug where a second group of the same unit type from the
same building is hidden until the first finishes). So "the queue shows nothing"
is trustworthy, but "the queue shows exactly N things" is not - alerts should
key on emptiness, never on exact contents.

That last case is the one I care about most. An empty queue means every
production building is idle; in the early game, when the Town Centre is my only
producer, an empty queue means an idle TC - and continuous villager production
is the single most important thing to keep going. There is no obvious on-screen
signal for it (the game only shows it if I click a TC), so reading the queue is
how Loom can warn me.

I read it exactly like resources.py reads the per-resource numbers: the
population icon has already established the HUD scale, so I match one nearby icon
- here the WOOD icon, because it sits right above the queue and the closest
anchor drifts the least - and then step down a known offset to each slot.

Reading a frame happens in two stages. First, occupancy: the queue always
fills as a contiguous prefix (left to right, top row before bottom, no holes -
when a group finishes, everything after it shifts left), so finding "how many
slots are occupied" means walking the grid in order until a cell stops looking
like a slot. The test for "looks like a slot" is edge structure: an occupied
cell has a crisp axis-aligned box outline at the exact grid position, and
terrain almost never draws one of those exactly there. I use edges rather than
the bevel's grey colour because the blocked tints wash over the bevel too -
a colour test that worked fine on green cells fell apart on an amber queue.

Second, per occupied slot: the tint (by hue), the progress of a green wash
(it fills left to right, so the covered fraction IS the progress), the group
count (the big pale numeral, read with dedicated templates - the queue uses a
larger outlined font than the HUD numbers), and the identity (matched against
templates/queue/, pre-zoomed library art; see tools/build_queue_templates.py).

Identity matching is the only expensive step, so QueueReader never does it in
steady state: each occupied slot's identity is cached and merely re-verified
with a single cheap correlation per poll; the full search over every template
runs only when a slot's content actually changed. A steady queue costs almost
nothing no matter how full it is.

Debug usage:
    python -m loom.queue captures/frame_0033.png
writes an annotated copy so I can SEE the slot boxes and what each one read.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os
import sys

import cv2
import numpy as np

from . import anchor, digits, hud, paths, resources

# The first slot's box, in reference pixels, as an offset from the WOOD icon's
# top-left corner: (dx1, dy1, dx2, dy2). Cells are 48x48. Fitted numerically
# against a fully packed 15x2 queue - frame_0519 of the capture run named
# run_20260724_182337_annehk_packed-queue-grid-fit - by maximising the
# image-edge energy under the grid lines, then checked visually against both
# capture runs. The woven trim below row one is decorative HUD
# edge - it is there whether or not anything is queued, so it stays outside
# the box.
#
# This is the Anne_HK skin's origin, and the default for callers that pass no
# profile. The stock bar puts its wood icon in a different place relative to
# the strip, so hud.STOCK carries its own - fitted the same way. Only the
# ORIGIN is per-skin; SLOT_PITCH and ROW_PITCH below are the game's grid.
SLOT_ONE = (-4.5, 60.5, 43.5, 108.5)

# Spacing from one slot to the next along a row, in reference pixels. The queue
# grows left-to-right, and when a row fills it wraps to a second row directly
# below the first, aligned to the first row's left edge (hardcoded in the game,
# so UI mods cannot move it). Fitted from the same packed frame; the fraction
# matters, because a rounding error compounds across 15 columns.
SLOT_PITCH = 49.8

# Spacing from row one down to row two, fitted from the same frame.
ROW_PITCH = 53.8

# The grid is hard-capped at 15 slots per row and two rows: a packed queue
# shows exactly 30 groups and hides the rest until space frees up. There is
# never a third row.
SLOTS_PER_ROW = 15
MAX_ROWS = 2

# Below this score I decide the wood icon is not on screen (a mod replaced the
# bar, or the HUD is not up) and give no slots rather than reading garbage.
MIN_WOOD_SCORE = 0.6

# ...and the score alone is not enough. The wood icon is the LEFTMOST thing in
# the resource bar, so a match out in the middle of the frame is a false
# positive however well it scored. Measured: the Anne_HK wood template matches
# the stock bar at x=1078 with score 0.701 - comfortably past the gate above -
# which would have anchored the whole slot grid a thousand pixels wrong and
# read confident nonsense off the terrain. A fraction of the frame width, not
# a pixel count, because the bar's width is the display's.
MAX_WOOD_X_FRACTION = 0.15

# Occupancy: a cell counts as a slot when at least three of its four border
# lines show a strong brightness step (mean absolute pixel difference across
# the line, with 2px of slack for grid drift). Measured on 56 occupied cells
# (green, amber, red, dark, dim) vs 59 empty ones: the second-weakest side of
# every occupied cell scored >= 34, every empty cell <= 11. 20 splits the gap.
MIN_EDGE_STEP = 20.0

# Tints, as OpenCV hue buckets (H runs 0..179), each with its own evidence
# rule because the portrait art fights each one differently. Bare skin votes
# for warm hues, and dark background pixels are technically saturated too, so
# hue alone lies. Measured on labelled cells from run_20260724_182337:
#
#   green: hue 40-85, saturated AND bright (V>80). A training villager reads
#          0.40, a tech wash still early in its fill reads 0.15; skin and
#          waiting portraits read 0.00, so the bar sits low at 0.10.
#   amber: hue 12-35, saturated AND bright. A pop-capped wash lights the
#          whole cell (0.98+); skin tops out at 0.18. The bar was 0.30 for
#          skin's sake and that put it UNDER the artwork it had to clear:
#          the Loom technology's icon is a red-and-gold woven tartan and
#          reads 0.28-0.31, straddling the bar. It flapped amber/untinted
#          frame to frame, and amber means "waiting, producing nothing", so
#          a Town Centre researching Loom was reported IDLE - Loom the
#          program defeated by Loom the technology. Re-measured across every
#          fixture: a REAL amber cell reads 0.76 at its faintest (0.762,
#          0.878, 0.880, 0.971) while the busiest warm ARTWORK reads 0.36.
#          0.55 sits in the middle of that gap instead of at the edge of
#          one side, which is what the pixel-constant rule asks for.
#   red:   wrap-around hues, saturated, NO brightness floor - a housed wash
#          is dark crimson and much of it sits under V=80. Unfloored, a true
#          wash reads 0.59 and skin tops out at 0.27. Bar at 0.40.
MIN_TINT_SATURATION = 90
MIN_TINT_VALUE = 80
GREEN_HUES, GREEN_FRACTION = (40, 85), 0.10
AMBER_HUES, AMBER_FRACTION = (12, 35), 0.55
RED_TINT_FRACTION = 0.40

# Identity: the best-scoring icon template must clear this, or the slot stays
# "occupied, identity unknown". Correct matches on live cells score 0.22-0.55;
# wrong icons idle around 0.1.
MIN_IDENTITY_SCORE = 0.20

# Which template identities are techs/ages rather than units. Kept here so
# the count/identity reconciliation below can use the game's own rule:
# TECHS NEVER SHOW A COUNT DIGIT in the queue. A drift-guard test checks
# every built template lands in exactly one of the two sets.
TECH_IDENTITIES = frozenset({
    "loom", "town_watch", "town_patrol", "wheelbarrow", "hand_cart",
    "feudal_age", "castle_age", "imperial_age",
    "double_bit_axe", "bow_saw", "two_man_saw", "horse_collar",
    "heavy_plow", "gold_mining", "stone_mining", "coinage", "masonry",
    "ballistics", "hoardings", "pikeman_upgrade",
})

# The occupancy content gate needs a STRONGER identity than the matcher's
# floor: a flat panel with a bright frame - which is what per-civ corner
# decor looks like - reaches 0.30 against the dark-silhouette tech icons
# (the frame correlates with the icon's bright edges). As occupancy
# evidence, an identity has to be convincing on its own; a weaker one only
# counts when a count numeral or a wash corroborates - and every real queue
# item shows one of those long before identity is the deciding vote.
CONTENT_IDENTITY_SCORE = 0.38
# A cached identity is re-verified each poll with one correlation. If its
# score drops this far below what it scored when first identified, the slot's
# content probably changed (groups shift left when one finishes) and the full
# search runs again.
IDENTITY_DROP = 0.12

# When the full search crowns a DIFFERENT name than the cached one, the
# challenger must beat the incumbent's current score by this much to take
# the slot. Weak cells re-rank every poll (see _identify_cached), and two
# similar portraits trade hair's-breadth wins poll to poll - a real
# villager batch flapped villager/monk around 0.50 and dropped out of the
# TC busy count each time it lost, dancing the idle alert between 5, 6
# and 7. A genuine content change wins decisively: the militia batch that
# once inherited a stale villager name beat it by 0.15.
IDENTITY_HYSTERESIS = 0.05

# Above this correlation a slot crop IS a known piece of civ decoration
# (see load_decor_templates) and cannot be a queue item. Measured: the
# harvested tapestry scores 0.95+ against its own later frames, while real
# queue cards drawn over the same spot score far below - the portrait
# covers the pattern.
DECOR_MATCH_SCORE = 0.7

# An identity with NO corroborating wash or count numeral must either
# score at least this well or beat the runner-up identity by at least the
# margin below. Junk cells match everything a little (best 0.46-0.52,
# margin 0.03-0.07 measured on terrain that read as "galley"); a real
# uncorroborated portrait wins clearly (a green villager measured 0.13).
# Corroborated cells are exempt: a genuine amber villager batch measured
# margin 0.05, and its wash already proves something real is drawn there.
CLEAR_IDENTITY_SCORE = 0.6
MIN_IDENTITY_MARGIN = 0.08

# A tech identity claim is either excellent or wrong: every real tech
# icon in the capture corpus reads 0.91+ (the silhouettes are crisp and
# distinctive), while junk and unit batches peak against tech templates
# in the 0.4s. A weak "tech" is a misread - and a misread TC tech once
# credited a TC with wheelbarrow research while a halberdier batch
# trained, masking a real idle TC.
TECH_IDENTITY_SCORE = 0.7


def load_wood_template(profile=None):
    """Load a HUD skin's wood-icon template as greyscale (the queue's anchor)."""
    if profile is None:
        profile = hud.DEFAULT
    template = cv2.imread(str(profile.wood_icon), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(
            f"Missing wood template: {profile.wood_icon}")
    return template


def load_decor_templates():
    """Civ UI decorations that hang exactly where queue slots sit.

    Some civilizations drape artwork from the resource bar right through
    the queue grid - the red tapestry did more than fake a slot: its
    saturated red read as a BLOCKED villager group, which marked one TC
    busy forever and turned a six-TC "6 idle" into "5 TCs IDLE" in a live
    game. The content gate cannot catch it (a wash IS content), so known
    decorations are matched explicitly and excluded. Harvested per art
    from capture frames, like every other template; an empty directory
    just means no decor is known yet.
    """
    templates = []
    for path in sorted(glob.glob(str(paths.TEMPLATES_DIR / "queue_decor"
                                     / "*.png"))):
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is not None:
            templates.append(image)
    return templates


def find_wood_icon(frame_bgr, wood_template, scale):
    """Locate the wood icon's top-left corner at the known HUD scale.

    Returns (score, x, y) in frame coordinates, or None if nothing matched.
    This is the same fixed-scale match resources.py does for each resource icon:
    the scale is already known from the population anchor, so I match at that one
    size instead of searching across sizes.
    """
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    bar_height = int(frame_gray.shape[0] * resources.BAR_HEIGHT_FRACTION)
    search_area = frame_gray[0:bar_height, :]

    match = resources._match_at_scale(search_area, wood_template, scale)
    if match is None or match[0] < MIN_WOOD_SCORE:
        return None
    if not _wood_position_is_plausible(match[1], frame_bgr.shape[1]):
        return None
    return match  # (score, x, y)


def _wood_position_is_plausible(wood_x, frame_width):
    """Is a match somewhere the leftmost resource icon could actually be?

    A frame that is only the queue strip is already narrow, so the fraction is
    measured against whatever width was handed in - the strip starts at the
    frame's left edge either way, which is the thing that matters.
    """
    return wood_x <= max(60, frame_width * MAX_WOOD_X_FRACTION)


def slot_boxes(wood_x, wood_y, scale, per_row=SLOTS_PER_ROW, rows=MAX_ROWS,
               slot_one=None):
    """Return the candidate slot boxes, row by row, left to right.

    Each box is (x1, y1, x2, y2) in frame coordinates. These are only *where a
    slot would be* - whether a slot is actually occupied is a later step.
    """
    dx1, dy1, dx2, dy2 = SLOT_ONE if slot_one is None else slot_one
    boxes = []
    for row in range(rows):
        for col in range(per_row):
            left_offset = dx1 + col * SLOT_PITCH
            top_offset = dy1 + row * ROW_PITCH
            boxes.append((
                int(wood_x + left_offset * scale),
                int(wood_y + top_offset * scale),
                int(wood_x + (left_offset + (dx2 - dx1)) * scale),
                int(wood_y + (top_offset + (dy2 - dy1)) * scale),
            ))
    return boxes


def locate_slots(frame_bgr, wood_template, scale, slot_one=None):
    """Find the wood anchor and return the queue's candidate slot boxes.

    Returns {"wood_score", "wood": (x, y), "slots": [box, ...]} or None if the
    wood icon could not be found.
    """
    match = find_wood_icon(frame_bgr, wood_template, scale)
    if match is None:
        return None

    score, wood_x, wood_y = match
    return {
        "wood_score": score,
        "wood": (wood_x, wood_y),
        "slots": slot_boxes(wood_x, wood_y, scale, slot_one=slot_one),
    }


def strip_extent(scale, slot_one=None):
    """How much of the frame's top-left corner the queue can occupy.

    Returns (width, height, search_height) in pixels at the given HUD scale.
    search_height is the band the wood-icon search covers: the icon sits at
    the top of the bar, so ~80 reference pixels of depth is always enough.
    Callers that capture only this strip (rather than the whole window) hand
    the reader a fraction of the pixels with nothing lost.

    "with nothing lost" is why slot_one is a parameter rather than the
    module default. The default is the MOD's cell, and stock's sits four
    reference pixels lower (its slot_one ends at 112 against 108.5), so
    sizing a stock strip from the mod's geometry cropped the bottom of the
    second row - the strip was measured against the wrong skin. Nothing has
    misread because of it yet, second rows being rare, but a skin whose
    cells sat lower still would silently lose slots. Callers know their
    profile; they should say so.
    """
    if slot_one is None:
        slot_one = SLOT_ONE
    search_height = int(80 * scale) + 20
    height = search_height + int((slot_one[3] + ROW_PITCH + 12) * scale)
    width = int((slot_one[2] + SLOTS_PER_ROW * SLOT_PITCH + 20) * scale) + 80
    return width, height, search_height


def load_icon_templates():
    """Load the queue identity templates as {name: [grayscale images]}.

    One identity can have several template variants, because the game renders
    some icons differently per civilization - the age-up shields change with
    the civ's architecture region, so a single Castle Age template matched
    one civ's queue and read another civ's age-up as an idle TC. Variants are
    named "castle_age.png", "castle_age.kite.png", ...: everything before the
    first dot is the identity. Same idea as the digit template variants.

    Templates are pre-sized by tools/build_queue_templates.py, so matching
    them against a raw slot crop needs no scale handling at all.
    """
    templates = {}
    for path in sorted(glob.glob(str(paths.TEMPLATES_DIR / "queue" / "*.png"))):
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is not None:
            name = os.path.basename(path).split(".")[0]
            templates.setdefault(name, []).append(image)
    if not templates:
        raise FileNotFoundError(
            f"No queue icon templates in {paths.TEMPLATES_DIR / 'queue'} "
            "- run: python -m tools.build_queue_templates")
    return templates


def load_count_templates():
    """Load digit templates for the queue's group-count numerals.

    The queue uses a larger outlined font than the HUD numbers, so it has its
    own template set (cut from labelled capture frames). Digits that have not
    appeared in a capture yet fall back to the HUD templates: a fallback glyph
    that fails to reach the match threshold reads as None, never as a wrong
    number, so the fallback cannot lie - it can only fill gaps.
    """
    templates = []
    have = set()
    for path in sorted(glob.glob(str(paths.TEMPLATES_DIR / "queue_digits" / "*.png"))):
        label = int(os.path.basename(path).split("_")[0])
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        templates.append((label, digits._normalize(image)))
        have.add(label)
    for label, template in digits.load_digit_templates():
        if label not in have:
            templates.append((label, template))
    return templates


def _edge_second_weakest(frame_gray, box):
    """How box-like a cell's outline is: the 2nd-weakest of its four edges.

    Each edge is the mean absolute brightness step across the border line,
    maximised over +/-2px so grid drift does not miss the bevel. Taking the
    second-weakest side means one accidentally strong terrain edge cannot fake
    a slot, and one soft side (a shared bevel) cannot break a real one.
    """
    x1, y1, x2, y2 = box
    height, width = frame_gray.shape
    values = frame_gray.astype(np.int16)

    def vertical(x):
        return np.abs(values[y1:y2, x + 1] - values[y1:y2, x - 1]).mean()

    def horizontal(y):
        return np.abs(values[y + 1, x1:x2] - values[y - 1, x1:x2]).mean()

    def best(measure, position, limit):
        # Slot one's left border sits almost on the frame edge, so the slack
        # window is clamped to stay inside the frame instead of giving up.
        spots = [position + d for d in (-2, -1, 0, 1, 2)
                 if 1 <= position + d < limit - 1]
        return max(measure(spot) for spot in spots) if spots else 0.0

    sides = sorted([
        best(vertical, x1, width),
        best(vertical, x2, width),
        best(horizontal, y1, height),
        best(horizontal, y2, height),
    ])
    return sides[1]


def count_occupied(frame_gray, boxes):
    """How many slots are occupied, using the contiguous-prefix property.

    The queue never has holes: groups pack to the front and shift left when
    one finishes. So the first cell that does not look like a slot ends the
    queue, and nothing after it needs testing.
    """
    for index, box in enumerate(boxes):
        if _edge_second_weakest(frame_gray, box) < MIN_EDGE_STEP:
            return index
    return len(boxes)


def classify_tint(cell_bgr, scale=1.0):
    """Return (tint, progress) for one occupied slot crop.

    tint is 'green', 'red', 'amber', or None for the plain dark portrait of a
    group that is waiting its turn. progress is only meaningful for green: the
    wash fills left to right as the item completes, so the fraction of columns
    it covers IS the completion fraction.

    The interior margin is in reference pixels and must scale with the cell:
    a fixed [4:44] read only the top-left corner of a 150%-HUD cell and
    clipped nothing off a 75% one, quietly skewing every wash fraction.
    """
    lo = int(round(4 * scale))
    hi = int(round(44 * scale))
    interior = cell_bgr[lo:hi, lo:hi]
    hsv = cv2.cvtColor(interior, cv2.COLOR_BGR2HSV)
    hue = hsv[:, :, 0].astype(int)
    saturated = hsv[:, :, 1] > MIN_TINT_SATURATION
    bright = hsv[:, :, 2] > MIN_TINT_VALUE

    red = ((hue <= 10) | (hue >= 168)) & saturated
    if red.mean() >= RED_TINT_FRACTION:
        return "red", None

    green = ((hue >= GREEN_HUES[0]) & (hue <= GREEN_HUES[1])
             & saturated & bright)
    if green.mean() >= GREEN_FRACTION:
        # Progress: how far right the green wash reaches.
        covered_columns = green.mean(axis=0) > 0.25
        return "green", float(covered_columns.mean())

    amber = ((hue >= AMBER_HUES[0]) & (hue <= AMBER_HUES[1])
             & saturated & bright)
    if amber.mean() >= AMBER_FRACTION:
        return "amber", None

    return None, None


def read_count(cell_bgr, count_templates, scale=1.0):
    """Read the group-count numeral from a slot crop. None if unreadable.

    The numeral is pale against whatever tint covers the portrait. Two masks
    catch it: high minimum-channel (white/cream digits - an amber wash has low
    blue, so the digit stands out) and high brightness (for green washes that
    tint the digit itself). Both thresholds are relative to the crop, because
    the tints change how bright "bright" is. Misreads fail the glyph match and
    return None - per the never-guess rule, a gap beats a wrong count.
    """
    # The numeral corner is in reference pixels; scaled like the cell, or an
    # off-100% HUD reads the wrong patch of the portrait.
    corner = cell_bgr[int(round(1 * scale)):int(round(37 * scale)),
                      int(round(1 * scale)):int(round(27 * scale))]
    channel_min = corner.min(axis=2)
    brightness = corner.max(axis=2)
    mask = ((channel_min > max(100, int(channel_min.max() * 0.68)))
            | (brightness > max(120, int(brightness.max() * 0.82))))
    mask = resources._keep_digit_shapes(mask.astype(np.uint8) * 255)

    # The speck gate shrinks with the HUD but never grows past its tuned
    # reference value - a wider gate is how the reader once swallowed a
    # thin "1" (see reader.min_glyph_width for that story).
    min_glyph = max(4, min(int(5 * scale), 5))
    found, _ = digits.read_binary(mask, count_templates,
                                  min_glyph_width=min_glyph)
    return None if found is None else digits.digits_to_int(found)


# The batch-count numeral is painted OVER the portrait, and it wrecks
# identification. Measured on a live game at 1080p: a male villager cell
# scored villager_male 0.515 while wheelbarrow won on 0.533, and another
# scored villager_male 0.531 behind dragon_ship on 0.539 - beaten by a
# hundredth, out of five hundred templates. The winner was then thrown away
# by the tech gate, the slot read as no identity at all, and the Town Centre
# training those villagers was reported IDLE.
#
# Male villagers lose worst, exactly as reported. The female icon is pale
# clothing with strong structure of its own; the male is a dark, low-contrast
# torso, so a bright numeral laid across it is a far larger share of what the
# correlation actually sees. With the numeral removed the same two cells score
# 0.919 and 0.868 and win outright.
#
# WHITE TOP-HAT rather than a brightness threshold, and that is the whole
# trick: read_count's mask asks "is this pixel bright?", which a green
# progress wash defeats - it lifts the digit and the portrait together, so
# the mask came back EMPTY on every washed cell (measured; which also means
# the count itself is unreadable there, noted in the issue). A top-hat asks
# "does this stand out from its own surroundings?", and a wash cannot take
# that away because it lifts the surroundings too.
#
# Nothing here touches tints. Which washes mean "producing" is decided in
# production.py and is deliberately not this module's business.
NUMERAL_CORNER = (1, 1, 27, 37)      # x1, y1, x2, y2 in reference pixels

# The top-hat window, in REFERENCE pixels, so it scales with the HUD like
# everything else measured in pixels must. It has to be wider than a digit
# stroke or the digit survives its own removal; ~12 reference pixels is a
# little over one numeral's width, and lands on the 9px kernel this was
# tuned at on a 0.73-scale HUD.
NUMERAL_TOPHAT_WINDOW = 12

# How far above its surroundings a pixel must stand to be numeral rather than
# portrait detail. The floor is an absolute contrast, not a length, so unlike
# the window it does not scale.
NUMERAL_TOPHAT_FLOOR = 25
NUMERAL_TOPHAT_FRACTION = 0.5


def without_numeral(cell_gray, scale):
    """The slot crop with the batch-count numeral painted out.

    Returns the cell unchanged when no numeral is found, so a slot that never
    had one costs one morphology pass and nothing else. See the comment above
    for why this exists and why it is a top-hat.
    """
    x1, y1, x2, y2 = (int(round(v * scale)) for v in NUMERAL_CORNER)
    corner = cell_gray[y1:y2, x1:x2]
    if corner.size == 0:
        return cell_gray

    window = max(3, int(round(NUMERAL_TOPHAT_WINDOW * scale)) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (window, window))
    hat = cv2.morphologyEx(corner, cv2.MORPH_TOPHAT, kernel)
    threshold = max(NUMERAL_TOPHAT_FLOOR,
                    int(hat.max() * NUMERAL_TOPHAT_FRACTION))

    # Shaped like a numeral, not merely bright. Without this the top-hat
    # removes any small bright detail it finds, which on a busy tech icon is
    # part of the picture - and since only the CELL is cleaned and never the
    # template, erasing real detail is an asymmetry that could cost more than
    # the numeral ever did. The same connected-component filter the resource
    # numbers use: a digit is tall for its size, a highlight speck is not.
    digits_only = resources._keep_digit_shapes(
        (hat >= threshold).astype(np.uint8) * 255)

    mask = np.zeros(cell_gray.shape, np.uint8)
    mask[y1:y2, x1:x2] = digits_only
    if not mask.any():
        return cell_gray
    # Grown by one pixel: the numeral carries a dark outline that is not
    # bright enough to be masked and would otherwise be left behind as a
    # digit-shaped hole, which correlates about as badly as the digit did.
    grown = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.inpaint(cell_gray, grown, 3, cv2.INPAINT_TELEA)


def _match_variants(cell_gray, variants):
    """Best score any of one identity's template variants achieves."""
    return max(float(cv2.matchTemplate(cell_gray, template,
                                       cv2.TM_CCOEFF_NORMED).max())
               for template in variants)


# Identities that are the same thing in different clothes. The margin
# below is "how clearly did the winner beat everything ELSE" - and a
# female villager outscoring the male by a whisker is not ambiguity, it
# is a villager. Without this, every real villager cell carried a
# near-zero margin against its own sibling.
FAMILY = {"villager_male": "villager", "villager_female": "villager"}


def identify(cell_gray, icon_templates):
    """Best-matching icon for a slot crop, as (name, score, margin).

    Plain normalised correlation over every template variant. This is the
    expensive call, which is why QueueReader caches its results - see read().

    margin is how far the winner beats the best identity outside its own
    FAMILY. It is the tell that separates a real match from junk: terrain
    that sneaks past the occupancy edge test - and any icon the template
    set does not know - matches everything a little and nothing well
    (measured: "galley" at 0.46-0.52 with the runner-up 0.03-0.06 behind;
    an unknown Armenian icon at 0.42 with castle_age 0.42 right behind),
    while a real portrait wins clearly.
    """
    scores = {}
    best_name, best_score = None, -1.0
    for name, variants in icon_templates.items():
        score = _match_variants(cell_gray, variants)
        scores[name] = score
        if score > best_score:
            best_name, best_score = name, score
    if best_score < MIN_IDENTITY_SCORE:
        return None, best_score, 0.0
    family = FAMILY.get(best_name, best_name)
    second = max((s for n, s in scores.items()
                  if FAMILY.get(n, n) != family), default=-1.0)
    return best_name, best_score, max(0.0, best_score - second)


def reconcile_identity_and_count(identity, score, count):
    """Apply the game's rule that techs never carry count digits.

    A slot claiming both a tech identity and a count is lying about one of
    them. Which one depends on the identity's confidence:

    * A CONFIDENT tech (the age shields match at 0.4+) keeps its identity
      and drops the count - the shield's roman numerals segment as digits
      ("castle_age x11" was the III strokes), so the count is the lie.
    * A WEAK tech match surrenders its identity - the dark tech silhouettes
      mis-match military batches (a green halberdier ×4 once read as
      "wheelbarrow at 70%", crediting a Town Centre with research it never
      did and masking a real idle TC). The count came from real pixels; the
      identity is the lie.

    Returns the (identity, count) to believe.
    """
    if identity in TECH_IDENTITIES and count is not None:
        if score >= CONTENT_IDENTITY_SCORE:
            return identity, None
        return None, count
    return identity, count


class SlotReading:
    """What one occupied queue slot showed this poll."""

    def __init__(self, index, tint, progress, count, identity, identity_score,
                 identity_margin=None):
        self.index = index                    # 0-29, reading order
        self.tint = tint                      # 'green' | 'red' | 'amber' | None
        self.progress = progress              # 0.0-1.0 for green, else None
        self.count = count                    # group size, or None if unreadable
        self.identity = identity              # template name, or None if unsure
        self.identity_score = identity_score
        # How clearly the identity beat every other family, when the full
        # search ran this poll; None when the answer came from the cache
        # or hysteresis (the score speaks for those).
        self.identity_margin = identity_margin

    def __repr__(self):
        return (f"Slot({self.index}: {self.identity or '?'}"
                f" x{self.count if self.count is not None else '?'}"
                f" {self.tint or 'waiting'})")


class QueueReader:
    """Reads the global queue from a frame, poll by poll.

    Holds the templates and the identity cache. The cache makes steady-state
    polling cheap: a slot whose cached identity still verifies is not searched
    again, so the full template sweep only runs when a slot's content changes.
    """

    def __init__(self, profile=None):
        self.profile = profile or hud.DEFAULT
        self.wood_template = load_wood_template(self.profile)
        self.icon_templates = load_icon_templates()
        self.count_templates = load_count_templates()
        self.decor_templates = load_decor_templates()
        # Icon templates resized for a non-100% HUD, built lazily the first
        # time an off-unit scale is seen and cached - the HUD scale cannot
        # change mid-game, so this happens at most once per session.
        self._scaled_icons = {}
        self._scaled_decor = {}
        # Per slot index: (identity, score at identification time).
        self._cache = {}
        # Where the wood icon was last seen. The HUD never moves during play,
        # so after the first full-width search each poll only re-verifies the
        # icon in a small window around this spot - the difference between a
        # ~5 ms poll and a ~1 ms one.
        self._wood = None

    def use_profile(self, profile):
        """Switch to the HUD skin the anchor identified.

        The reader calls this once the skin is known. Cheap and idempotent,
        but it does drop the wood position: the icon it was tracking belonged
        to the old skin's art, and a cached position from the wrong picture is
        the sort of thing that reads plausible garbage for a whole game.
        """
        if profile is self.profile:
            return
        self.profile = profile
        self.wood_template = load_wood_template(profile)
        self._wood = None
        self._cache = {}

    def _find_wood(self, frame_gray, scale, search_height):
        """The wood icon's position, from cache when it still verifies.

        search_height bounds the full search. It is computed from the HUD
        scale, not from the image height: frame_gray may be just the top-left
        strip, and a fraction of a strip is a sliver too short to search.
        """
        template = cv2.resize(self.wood_template, None, fx=scale, fy=scale,
                              interpolation=cv2.INTER_AREA)
        if self._wood is not None:
            x, y = self._wood
            margin = 8
            window = frame_gray[max(0, y - margin):y + template.shape[0] + margin,
                                max(0, x - margin):x + template.shape[1] + margin]
            if window.shape[0] >= template.shape[0] and window.shape[1] >= template.shape[1]:
                scores = cv2.matchTemplate(window, template, cv2.TM_CCOEFF_NORMED)
                _, best, _, where = cv2.minMaxLoc(scores)
                if best >= MIN_WOOD_SCORE:
                    self._wood = (max(0, x - margin) + where[0],
                                  max(0, y - margin) + where[1])
                    return self._wood

        match = resources._match_at_scale(frame_gray[0:search_height, :],
                                          self.wood_template, scale)
        if match is None or match[0] < MIN_WOOD_SCORE:
            self._wood = None
            return None
        # Score alone has let a wrong-skin match through at x=1078; the icon
        # is the leftmost thing in the bar or it is not the icon.
        if not _wood_position_is_plausible(match[1], frame_gray.shape[1]):
            self._wood = None
            return None
        self._wood = (match[1], match[2])
        return self._wood

    def read(self, frame_bgr, scale):
        """Read every occupied slot. Returns a list of SlotReading, or None
        when the queue area cannot be located at all (no wood anchor)."""
        # Only the top-left corner can contain the queue, so only that strip
        # gets converted to grey - a full-frame cvtColor at 1440p costs more
        # than everything else in this method put together. The strip starts
        # at (0,0), so its coordinates are frame coordinates.
        strip_width, strip_height, search_height = strip_extent(
            scale, self.profile.slot_one)
        frame_gray = cv2.cvtColor(
            frame_bgr[:min(strip_height, frame_bgr.shape[0]),
                      :min(strip_width, frame_bgr.shape[1])],
            cv2.COLOR_BGR2GRAY)
        wood = self._find_wood(frame_gray, scale, search_height)
        if wood is None:
            self._cache.clear()
            return None

        boxes = slot_boxes(wood[0], wood[1], scale,
                           slot_one=self.profile.slot_one)
        self._use_templates_for(scale)
        occupied = count_occupied(frame_gray, boxes)

        readings = []
        for index in range(occupied):
            x1, y1, x2, y2 = boxes[index]
            cell_bgr = frame_bgr[y1:y2, x1:x2]
            cell_gray = frame_gray[y1:y2, x1:x2]

            # Known civ decoration showing through means no item is drawn
            # here - and the queue is a contiguous prefix, so it ends here.
            # This must run before the tint check: the tapestry's saturated
            # red passes the content gate as a "blocked" wash.
            if self._is_decor(cell_gray, scale):
                occupied = index
                break

            tint, progress = classify_tint(cell_bgr, scale)
            count = read_count(cell_bgr, self.count_templates, scale)
            # Identity alone reads the numeral-free cell. Everything else -
            # the tint, the count, the decor test, the occupancy edges - wants
            # the real pixels, and only template correlation is confused by
            # having a number drawn across its subject.
            identity, score, margin = self._identify_cached(
                index, without_numeral(cell_gray, scale),
                tint is not None or count is not None)
            identity, count = reconcile_identity_and_count(identity, score,
                                                           count)

            # The edge test alone is not enough to call a cell a queue item:
            # several civs hang decorative UI art exactly where slot one
            # sits, and decor draws box-like edges too. A real queue item
            # always shows at least one piece of CONTENT - a wash, a count
            # numeral, or a CONFIDENT identity. Identity is the right test
            # rather than a raw score: with five hundred templates, junk
            # always lucks past any fixed score against SOMETHING, but the
            # clear-win and margin gates in _identify_cached have already
            # turned an unconvincing match into None by the time it gets
            # here. Decor shows none of the three. The queue is a
            # contiguous prefix, so the first contentless cell ends it.
            if tint is None and count is None and identity is None:
                occupied = index
                break

            readings.append(SlotReading(index, tint, progress, count,
                                        identity, score, margin))

        # Slots past the believed end of the queue no longer exist.
        for index in [i for i in self._cache if i >= occupied]:
            del self._cache[index]

        return readings

    def _is_decor(self, cell_gray, scale):
        """Is this cell a known piece of civ decoration, not a queue item?

        A real card drawn over the decoration covers its pattern, so this
        only matches when nothing is actually queued in the slot.
        """
        for template in self._decor_for(scale):
            if (template.shape[0] > cell_gray.shape[0]
                    or template.shape[1] > cell_gray.shape[1]):
                continue
            score = cv2.matchTemplate(cell_gray, template,
                                      cv2.TM_CCOEFF_NORMED).max()
            if score >= DECOR_MATCH_SCORE:
                return True
        return False

    def _decor_for(self, scale):
        """Decor templates sized for this HUD scale, cached like the icons."""
        key = round(scale, 2)
        if abs(key - 1.0) <= 0.02 or not self.decor_templates:
            return self.decor_templates
        if key not in self._scaled_decor:
            self._scaled_decor[key] = [
                cv2.resize(t, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA)
                for t in self.decor_templates]
        return self._scaled_decor[key]

    def _use_templates_for(self, scale):
        """Point identification at templates sized for this HUD scale.

        Templates are cut at 100% HUD scale; a shrunken HUD shrinks the
        queue cells with it, and a 40px template cannot even slide inside a
        38px cell. Resizing the whole set once per session keeps non-100%
        HUDs working instead of silently reading nothing.
        """
        key = round(scale, 2)
        if abs(key - 1.0) <= 0.02:
            return
        if key not in self._scaled_icons:
            self._scaled_icons[key] = {
                name: [cv2.resize(t, None, fx=scale, fy=scale,
                                  interpolation=cv2.INTER_AREA)
                       for t in variants]
                for name, variants in load_icon_templates().items()
            }
        self.icon_templates = self._scaled_icons[key]

    def _identify_cached(self, index, cell_gray, has_content):
        """Identify a slot, re-using the cached answer while it still fits.

        has_content says whether a wash or a count numeral corroborates
        this cell. Fresh identifications are vetted before they are cached
        or believed: an uncorroborated identity must win clearly (see
        CLEAR_IDENTITY_SCORE / MIN_IDENTITY_MARGIN), and a tech claim must
        be excellent (TECH_IDENTITY_SCORE) whatever the corroboration -
        both gates turn confident-sounding junk into an honest None.
        """
        incumbent = None            # (cached name, its score on THIS cell)
        cached = self._cache.get(index)
        if cached is not None:
            name, score_then = cached
            variants = self.icon_templates.get(name)
            if variants:
                score_now = _match_variants(cell_gray, variants)
                # The drop-check alone is not enough to reuse a name: queue
                # contents shift left as groups finish, and a DIFFERENT
                # unit can score almost as well as the old one against the
                # old one's template - a militia batch inherited a cached
                # "villager_male" at 0.49 for its whole training run and
                # minted a phantom TC, while a fresh search ranked militia
                # 0.64. A weakly-scoring cached name must re-earn its slot
                # through the full search below.
                if (score_now >= max(MIN_IDENTITY_SCORE,
                                     score_then - IDENTITY_DROP)
                        and score_now >= CLEAR_IDENTITY_SCORE):
                    return name, score_now, None
                incumbent = (name, score_now)

        identity, score, margin = identify(cell_gray, self.icon_templates)

        # A slot that held a believed item on the LAST poll corroborates this
        # one, exactly as a wash or a count numeral does. What the
        # uncorroborated gate is really asking is "is anything actually drawn
        # here, or is this terrain and decoration?", and a cell that was a
        # queue item 300ms ago has already answered that: decoration does not
        # come and go, and a slot that genuinely empties has its cache entry
        # deleted below, so nothing stale can vouch for it.
        #
        # Without this, the moment that costs the most is the one right after
        # an item is queued. Measured live on stock: a villager placed but
        # not yet washed reads villager_male at 0.585 with a margin of 0.04 -
        # the RIGHT answer, fifteen thousandths under the gate. It was thrown
        # away, the cell then showed no wash, no count and no identity, the
        # content gate below ended the queue at that slot, and the queue read
        # EMPTY with a villager plainly training. Two such polls and the
        # tracker announced TC IDLE while the Town Centre was working.
        corroborated = has_content or cached is not None
        if (identity is not None and not corroborated
                and score < CLEAR_IDENTITY_SCORE
                and margin < MIN_IDENTITY_MARGIN):
            identity = None
        if identity in TECH_IDENTITIES and score < TECH_IDENTITY_SCORE:
            identity = None
        # Hysteresis: a challenger takes an occupied slot only by beating
        # the incumbent decisively (IDENTITY_HYSTERESIS). Weak cells
        # re-rank every poll, and two similar portraits trading
        # hair's-breadth wins must not flap the identity - and with it the
        # TC busy count - poll to poll.
        if (incumbent is not None and identity is not None
                and identity != incumbent[0]
                and incumbent[1] >= MIN_IDENTITY_SCORE
                and score - incumbent[1] < IDENTITY_HYSTERESIS):
            (identity, score), margin = incumbent, None
        if identity is not None:
            self._cache[index] = (identity, score)
        else:
            self._cache.pop(index, None)
        return identity, score, margin


def draw_debug(frame_bgr, found):
    """Draw the wood anchor and the slot boxes so a human can check them."""
    annotated = frame_bgr.copy()
    wx, wy = found["wood"]
    cv2.rectangle(annotated, (wx, wy), (wx + 40, wy + 40), (255, 0, 0), 1)
    for i, (x1, y1, x2, y2) in enumerate(found["slots"]):
        thickness = 2 if i == 0 else 1
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), thickness)
        cv2.putText(annotated, str(i), (x1 + 2, y1 + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    return annotated


def main():
    if len(sys.argv) < 2:
        print("usage: python -m loom.queue <frame.png> [more.png ...]")
        return

    pop_templates = {profile: anchor.load_template(profile)
                     for profile in hud.PROFILES}
    wood_templates = {profile: load_wood_template(profile)
                      for profile in hud.PROFILES}
    reader = QueueReader()

    for path in sys.argv[1:]:
        frame = cv2.imread(path)
        if frame is None:
            print(f"{path}: could not read")
            continue

        # The population anchor establishes both the HUD scale and which skin
        # the queue's own geometry should come from.
        pop_match = anchor.identify_hud(frame, pop_templates,
                                        wood_templates=wood_templates)
        if pop_match is None:
            print(f"{path}: no population anchor")
            continue
        scale = pop_match["scale"]
        reader.use_profile(pop_match["profile"])

        readings = reader.read(frame, scale)
        reader._cache.clear()   # each frame judged fresh in the debug tool
        if readings is None:
            print(f"{path}: no wood anchor")
            continue

        found = locate_slots(frame, reader.wood_template, scale,
                             slot_one=reader.profile.slot_one)
        out_path = path.replace(".png", "_queue_debug.png")
        cv2.imwrite(out_path, draw_debug(frame, found))
        print(f"{path}: hud={pop_match['profile'].name} "
              f"{len(readings)} occupied -> {out_path}")
        for slot in readings:
            print(f"   {slot}")


if __name__ == "__main__":
    main()
