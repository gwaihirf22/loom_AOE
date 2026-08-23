"""
Loom — the arithmetic that decides how tall a step's box is.

Every failure here is SILENT: a box four pixels short does not raise, it
clips an instruction and looks perfectly fine. That is what earns these
tests their place, and it is why none of them check that anything is drawn.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import steplayout
from loom.build_order import available_builds


def test_every_shipped_step_fits_without_shrinking_the_text():
    """The claim MAX_GROWN_ITEMS is chosen to keep.

    Seven rows was picked by counting the shipped builds, so if a build is
    added whose steps go further, the text starts shrinking on a real step
    and nobody would notice from the code. This is the check that says so.
    """
    builds, _problems = available_builds()
    assert builds, "no builds to check"
    for stem, build in builds:
        for number, step in enumerate(build.steps, start=1):
            plan = steplayout.plan_items(len(step.items))
            assert plan.points == steplayout.ITEM_POINTS, (
                f"{stem} step {number} ({len(step.items)} items) has to "
                f"shrink to {plan.points}pt")
            assert plan.hidden == 0, (
                f"{stem} step {number} hides {plan.hidden} items")


def test_a_step_of_three_or_fewer_does_not_grow_the_box():
    """The common case must be the panel that shipped, pixel for pixel."""
    for count in (0, 1, 2, 3):
        assert steplayout.plan_items(count).extra == 0


def test_growing_stops_at_the_ceiling_and_the_text_gives_way_instead():
    """The order of concessions: grow first, and only then shrink."""
    seven = steplayout.plan_items(7)
    assert seven.points == steplayout.ITEM_POINTS
    assert seven.extra == (7 - steplayout.BASE_ITEMS) * steplayout.ITEM_ROW

    eight = steplayout.plan_items(8)
    assert eight.extra == seven.extra, "the box grew past its ceiling"
    assert eight.points < steplayout.ITEM_POINTS, "the text did not give way"


def test_the_rows_always_fit_the_box_they_were_given():
    """The whole point. Rows times their height must never exceed the box.

    Checked across every count a build could plausibly hold, because the row
    pitch is rounded to whole pixels at each font size and an off-by-one
    there is exactly the kind of thing that only bites at one size.
    """
    for count in range(1, 40):
        for columns in (1, 2):
            plan = steplayout.plan_items(count, columns)
            box = max(steplayout.BASE_ITEMS,
                      min(steplayout.MAX_GROWN_ITEMS,
                          plan.rows)) * steplayout.ITEM_ROW
            box = max(box, steplayout.BASE_ITEMS * steplayout.ITEM_ROW
                      + plan.extra)
            assert plan.rows * plan.row_height <= box, (
                f"{count} items in {columns} column(s) overflow the box")


def test_nothing_is_dropped_without_being_counted():
    """A silent truncation reads as "that was the whole step"."""
    for count in range(1, 60):
        plan = steplayout.plan_items(count)
        assert plan.shown + plan.hidden == count
        if plan.hidden:
            assert steplayout.more_label(plan.hidden).endswith("more")
        else:
            assert steplayout.more_label(plan.hidden) == ""


def test_two_columns_are_a_concession_not_a_default():
    """Columns must never surprise a step that would fit one column.

    Every shipped step fits, so nothing on disk should ever see two - the
    check that keeps the default panel identical to the one that shipped.
    """
    assert steplayout.columns_for(528, 100, 4, 24) == 1
    assert steplayout.columns_for(528, 100, steplayout.MAX_GROWN_ITEMS,
                                  24) == 1
    # ...and a list too long to grow into does, when the items really fit.
    assert steplayout.columns_for(528, 100, 12, 24) == 2
    assert steplayout.columns_for(528, 400, 12, 24) == 1, \
        "split into columns an item cannot fit in"
