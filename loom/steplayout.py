"""
Loom — how many item rows a step needs, and how big they may be.

A build step is a LIST of instructions, not one instruction with footnotes.
Both front ends draw that list - the overlay panel over the game and the
preview's cards - so the arithmetic that decides "how tall does this step
make the box, and does the text have to shrink" lives here, once, with no
Qt in it. Same reason overlay.OverlayLayout and browser.card_scale are pure:
a display is not needed to check a size, and a box four pixels short clips
an instruction while looking perfectly fine.

THE ORDER OF CONCESSIONS, which is the whole design:

    1. grow the box               up to MAX_GROWN_ITEMS rows
    2. then split into columns    if the widest item genuinely fits
    3. then shrink the text       down to MIN_ITEM_POINTS
    4. then truncate              and SAY SO, with "+N more"

Growing first is the author's rule and it is the right way round: a smaller
font is a real cost on a panel being read out of the corner of an eye
mid-match, and vertical space is the cheap thing. Truncating last, and
loudly, is the house rule about admitted gaps - the two front ends silently
dropped everything past the second footnote before this existed, so a
seven-item step showed three and looked complete.

Steps 2 to 4 do not happen in any shipped build; see WHY SEVEN.

WHY SEVEN. Counted across the thirteen shipped builds: 54 steps of one item,
48 of two, 24 of three, 10 of four, 3 of five, 10 of six and 1 of seven.
Growing to seven rows therefore covers EVERY step of every build on disk
with no shrinking at all; steps 2 and 3 exist for imported builds that go
further, not for anything shipped.

Every number here is a DESIGNED pixel value at scale 1.0, like the literals
in overlay.py and browser.py. Callers map them through their own scale -
OverlayLayout.y()/pt() or StepCard._s()/_pt() - so nothing in this module is
a pixel constant that could fail to follow the HUD.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import math
from collections import namedtuple

# One size for every item. Between the 15pt headline and the 10pt footnotes
# it replaces, because that split was never a ranking - see
# build_order.split_notes for what the shipped builds actually put first.
ITEM_POINTS = 12

# The baseline-to-baseline pitch for one item row at ITEM_POINTS.
ITEM_ROW = 22

# How many rows the designed box already holds without growing. Three is
# what the 186px panel and the 132px card fit today between the header
# divider and the VILLS row, so a step of three items or fewer - 126 of the
# 150 shipped steps - draws in exactly the box that shipped in 1.0.4.
BASE_ITEMS = 3

# The tallest the box may grow to, in rows. See WHY SEVEN above.
MAX_GROWN_ITEMS = 7

# The smallest the item text may be squeezed to. Below this the panel stops
# being readable at a glance, which is the only thing it is for.
MIN_ITEM_POINTS = 8

# The gutter between two columns, in designed pixels. Only used to decide
# whether two columns fit; the caller scales it before asking.
COLUMN_GAP = 24


# What one step's list needs. Everything except `columns` is derived.
#
#   rows        rows actually drawn (shown items over columns)
#   columns     1 or 2
#   points      the font size for every item
#   row_height  baseline-to-baseline pitch at that size
#   shown       how many items are drawn
#   hidden      how many are not - 0 in every shipped build
#   extra       designed pixels the box grows by, beyond the base box
ItemPlan = namedtuple("ItemPlan", "rows columns points row_height "
                                  "shown hidden extra")


def row_height_for(points):
    """The row pitch that goes with a font size.

    Pitch follows the font rather than the other way round, because the
    font is the thing being conceded and rows must not overlap when it
    shrinks. Never 0: a 0px pitch would stack every item on one baseline.
    """
    return max(1, round(ITEM_ROW * points / ITEM_POINTS))


def plan_items(count, columns=1):
    """How to draw `count` items in `columns` columns. Returns an ItemPlan.

    Applies the three concessions in order - grow, then shrink, then
    truncate. A step with no items at all returns a plan for zero rows and
    no growth, which draws nothing and is what "build complete" wants.
    """
    columns = max(1, int(columns))
    count = max(0, int(count))
    if count == 0:
        return ItemPlan(0, columns, ITEM_POINTS, ITEM_ROW, 0, 0, 0)

    wanted = math.ceil(count / columns)

    # 1. Grow. The box takes as many rows as are wanted, between the base
    #    box and the ceiling.
    box_rows = max(BASE_ITEMS, min(MAX_GROWN_ITEMS, wanted))
    box_pixels = box_rows * ITEM_ROW
    extra = (box_rows - BASE_ITEMS) * ITEM_ROW

    # 2. Shrink, a point at a time, only while the rows genuinely overflow
    #    the box. A loop rather than arithmetic because the row pitch is
    #    rounded to whole pixels at each size, so solving for it directly
    #    would be off by one at some sizes and right at others - which is
    #    the kind of bug that only shows up at one scale.
    points = ITEM_POINTS
    while (points > MIN_ITEM_POINTS
           and wanted * row_height_for(points) > box_pixels):
        points -= 1
    row_height = row_height_for(points)

    # 3. Truncate, keeping a row for the "+N more" line so the gap is
    #    admitted rather than silent.
    capacity = max(1, box_pixels // row_height)
    if wanted <= capacity:
        return ItemPlan(wanted, columns, points, row_height, count, 0, extra)

    shown_rows = max(1, capacity - 1)
    shown = min(count, shown_rows * columns)
    return ItemPlan(shown_rows, columns, points, row_height,
                    shown, count - shown, extra)


def columns_for(content_width, widest_item_width, count, gap):
    """Two columns, or one? All widths in the SAME units - real pixels.

    The caller measures the widest item with its own painter and scales
    `gap` itself, because font metrics are the only honest source for how
    wide a line of text is, and this module has no painter.

    A SECOND COLUMN IS THE THIRD CONCESSION, not the first. The box grows
    first, and only a list too long to grow into - more than
    MAX_GROWN_ITEMS - is worth splitting. That ordering is what keeps the
    common case identical to the panel that shipped: every step in every
    shipped build fits one column, so nothing on disk today ever sees two.

    It was not gated that way at first, and looking at the result decided
    it. With the @icon@ artwork drawn inline, instructions measure far
    shorter than their words do - "Move 2 [vill] from straggler to
    [berries]" is about 240px, not the 426 its text alone measures - so
    "does it fit" passed for most four-item steps and the default 560px
    panel split into two cramped 252px columns. It fitted; it read worse.

    Deliberately NOT also gated on a width THRESHOLD, which is the obvious
    way to write "only when the overlay is big". The panel's size knobs
    scale width and font together, so a uniformly bigger overlay has
    exactly the designed proportions and would never pass such a gate at
    any setting the sliders can reach - measured across the full range of
    both. A proportional gate would not restrict this feature, it would
    delete it. Whether the item FITS is the honest question and it answers
    correctly at every scale.
    """
    if count <= MAX_GROWN_ITEMS or widest_item_width <= 0:
        return 1
    column = (content_width - gap) / 2
    return 2 if widest_item_width <= column else 1


def more_label(hidden):
    """The line that admits a truncation. "" when nothing was dropped."""
    return f"+{hidden} more" if hidden > 0 else ""
