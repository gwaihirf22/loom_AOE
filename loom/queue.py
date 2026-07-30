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

from . import anchor, digits, paths, resources

# The first slot's box, in reference pixels, as an offset from the WOOD icon's
# top-left corner: (dx1, dy1, dx2, dy2). Cells are 48x48. Fitted numerically
# against a fully packed 15x2 queue (run_20260724_182337/frame_0519) by
# maximising the image-edge energy under the grid lines, then checked visually
# against both capture runs. The woven trim below row one is decorative HUD
# edge - it is there whether or not anything is queued, so it stays outside
# the box.
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
#          whole cell (0.98+); skin tops out at 0.18. Bar at 0.30.
#   red:   wrap-around hues, saturated, NO brightness floor - a housed wash
#          is dark crimson and much of it sits under V=80. Unfloored, a true
#          wash reads 0.59 and skin tops out at 0.27. Bar at 0.40.
MIN_TINT_SATURATION = 90
MIN_TINT_VALUE = 80
GREEN_HUES, GREEN_FRACTION = (40, 85), 0.10
AMBER_HUES, AMBER_FRACTION = (12, 35), 0.30
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
    "ballistics",
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


def load_wood_template():
    """Load the wood-icon template as greyscale (the queue's anchor)."""
    template = cv2.imread(str(paths.TEMPLATES_DIR / "wood_icon.png"),
                          cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(
            f"Missing wood template: {paths.TEMPLATES_DIR / 'wood_icon.png'}")
    return template


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
    return match  # (score, x, y)


def slot_boxes(wood_x, wood_y, scale, per_row=SLOTS_PER_ROW, rows=MAX_ROWS):
    """Return the candidate slot boxes, row by row, left to right.

    Each box is (x1, y1, x2, y2) in frame coordinates. These are only *where a
    slot would be* - whether a slot is actually occupied is a later step.
    """
    dx1, dy1, dx2, dy2 = SLOT_ONE
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


def locate_slots(frame_bgr, wood_template, scale):
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
        "slots": slot_boxes(wood_x, wood_y, scale),
    }


def strip_extent(scale):
    """How much of the frame's top-left corner the queue can occupy.

    Returns (width, height, search_height) in pixels at the given HUD scale.
    search_height is the band the wood-icon search covers: the icon sits at
    the top of the bar, so ~80 reference pixels of depth is always enough.
    Callers that capture only this strip (rather than the whole window) hand
    the reader a fraction of the pixels with nothing lost.
    """
    search_height = int(80 * scale) + 20
    height = search_height + int((SLOT_ONE[3] + ROW_PITCH + 12) * scale)
    width = int((SLOT_ONE[2] + SLOTS_PER_ROW * SLOT_PITCH + 20) * scale) + 80
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


def classify_tint(cell_bgr):
    """Return (tint, progress) for one occupied slot crop.

    tint is 'green', 'red', 'amber', or None for the plain dark portrait of a
    group that is waiting its turn. progress is only meaningful for green: the
    wash fills left to right as the item completes, so the fraction of columns
    it covers IS the completion fraction.
    """
    interior = cell_bgr[4:44, 4:44]
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


def read_count(cell_bgr, count_templates):
    """Read the group-count numeral from a slot crop. None if unreadable.

    The numeral is pale against whatever tint covers the portrait. Two masks
    catch it: high minimum-channel (white/cream digits - an amber wash has low
    blue, so the digit stands out) and high brightness (for green washes that
    tint the digit itself). Both thresholds are relative to the crop, because
    the tints change how bright "bright" is. Misreads fail the glyph match and
    return None - per the never-guess rule, a gap beats a wrong count.
    """
    corner = cell_bgr[1:37, 1:27]
    channel_min = corner.min(axis=2)
    brightness = corner.max(axis=2)
    mask = ((channel_min > max(100, int(channel_min.max() * 0.68)))
            | (brightness > max(120, int(brightness.max() * 0.82))))
    mask = resources._keep_digit_shapes(mask.astype(np.uint8) * 255)

    found, _ = digits.read_binary(mask, count_templates, min_glyph_width=5)
    return None if found is None else digits.digits_to_int(found)


def _match_variants(cell_gray, variants):
    """Best score any of one identity's template variants achieves."""
    return max(float(cv2.matchTemplate(cell_gray, template,
                                       cv2.TM_CCOEFF_NORMED).max())
               for template in variants)


def identify(cell_gray, icon_templates):
    """Best-matching icon for a slot crop, as (name, score).

    Plain normalised correlation over every template variant. This is the
    expensive call, which is why QueueReader caches its results - see read().
    """
    best_name, best_score = None, -1.0
    for name, variants in icon_templates.items():
        score = _match_variants(cell_gray, variants)
        if score > best_score:
            best_name, best_score = name, score
    if best_score < MIN_IDENTITY_SCORE:
        return None, best_score
    return best_name, best_score


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

    def __init__(self, index, tint, progress, count, identity, identity_score):
        self.index = index                    # 0-29, reading order
        self.tint = tint                      # 'green' | 'red' | 'amber' | None
        self.progress = progress              # 0.0-1.0 for green, else None
        self.count = count                    # group size, or None if unreadable
        self.identity = identity              # template name, or None if unsure
        self.identity_score = identity_score

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

    def __init__(self):
        self.wood_template = load_wood_template()
        self.icon_templates = load_icon_templates()
        self.count_templates = load_count_templates()
        # Icon templates resized for a non-100% HUD, built lazily the first
        # time an off-unit scale is seen and cached - the HUD scale cannot
        # change mid-game, so this happens at most once per session.
        self._scaled_icons = {}
        # Per slot index: (identity, score at identification time).
        self._cache = {}
        # Where the wood icon was last seen. The HUD never moves during play,
        # so after the first full-width search each poll only re-verifies the
        # icon in a small window around this spot - the difference between a
        # ~5 ms poll and a ~1 ms one.
        self._wood = None

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
        self._wood = (match[1], match[2])
        return self._wood

    def read(self, frame_bgr, scale):
        """Read every occupied slot. Returns a list of SlotReading, or None
        when the queue area cannot be located at all (no wood anchor)."""
        # Only the top-left corner can contain the queue, so only that strip
        # gets converted to grey - a full-frame cvtColor at 1440p costs more
        # than everything else in this method put together. The strip starts
        # at (0,0), so its coordinates are frame coordinates.
        strip_width, strip_height, search_height = strip_extent(scale)
        frame_gray = cv2.cvtColor(
            frame_bgr[:min(strip_height, frame_bgr.shape[0]),
                      :min(strip_width, frame_bgr.shape[1])],
            cv2.COLOR_BGR2GRAY)
        wood = self._find_wood(frame_gray, scale, search_height)
        if wood is None:
            self._cache.clear()
            return None

        boxes = slot_boxes(wood[0], wood[1], scale)
        self._use_templates_for(scale)
        occupied = count_occupied(frame_gray, boxes)

        readings = []
        for index in range(occupied):
            x1, y1, x2, y2 = boxes[index]
            cell_bgr = frame_bgr[y1:y2, x1:x2]
            cell_gray = frame_gray[y1:y2, x1:x2]

            tint, progress = classify_tint(cell_bgr)
            count = read_count(cell_bgr, self.count_templates)
            identity, score = self._identify_cached(index, cell_gray)
            identity, count = reconcile_identity_and_count(identity, score,
                                                           count)

            # The edge test alone is not enough to call a cell a queue item:
            # several civs hang decorative UI art exactly where slot one
            # sits, and decor draws box-like edges too. A real queue item
            # always shows at least one piece of CONTENT - a wash, a count
            # numeral, or a CONVINCING icon match (see CONTENT_IDENTITY_SCORE
            # for why a borderline one is not enough here). Decor shows none.
            # The queue is a contiguous prefix, so the first contentless cell
            # ends it.
            if (tint is None and count is None
                    and score < CONTENT_IDENTITY_SCORE):
                occupied = index
                break

            readings.append(SlotReading(index, tint, progress, count,
                                        identity, score))

        # Slots past the believed end of the queue no longer exist.
        for index in [i for i in self._cache if i >= occupied]:
            del self._cache[index]

        return readings

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

    def _identify_cached(self, index, cell_gray):
        """Identify a slot, re-using the cached answer while it still fits."""
        cached = self._cache.get(index)
        if cached is not None:
            name, score_then = cached
            variants = self.icon_templates.get(name)
            if variants:
                score_now = _match_variants(cell_gray, variants)
                if score_now >= max(MIN_IDENTITY_SCORE,
                                    score_then - IDENTITY_DROP):
                    return name, score_now

        identity, score = identify(cell_gray, self.icon_templates)
        if identity is not None:
            self._cache[index] = (identity, score)
        else:
            self._cache.pop(index, None)
        return identity, score


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

    pop_template = anchor.load_template()
    reader = QueueReader()

    for path in sys.argv[1:]:
        frame = cv2.imread(path)
        if frame is None:
            print(f"{path}: could not read")
            continue

        # The population anchor establishes the HUD scale for everything else.
        pop_match = anchor.find_icon(frame, pop_template)
        if pop_match is None:
            print(f"{path}: no population anchor")
            continue
        scale = pop_match[3]

        readings = reader.read(frame, scale)
        reader._cache.clear()   # each frame judged fresh in the debug tool
        if readings is None:
            print(f"{path}: no wood anchor")
            continue

        found = locate_slots(frame, reader.wood_template, scale)
        out_path = path.replace(".png", "_queue_debug.png")
        cv2.imwrite(out_path, draw_debug(frame, found))
        print(f"{path}: {len(readings)} occupied -> {out_path}")
        for slot in readings:
            print(f"   {slot}")


if __name__ == "__main__":
    main()
