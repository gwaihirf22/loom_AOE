"""
Loom — tests for the build preview's window arithmetic.

The widgets are tested by using them, like the rest of the launcher. What
earns automated tests is the index arithmetic underneath: which four steps
are visible, and which step a live reading should highlight. Off-by-ones
here would show a wrong step with total confidence, which is exactly the
kind of bug the eye forgives until it costs a game.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom.browser import (CARD_WIDTH, MAX_CARD_SCALE, MIN_CARD_SCALE,
                          STACK_MARGIN, card_scale, live_focus,
                          usable_card_width, visible_indices)


def test_visible_indices_mid_build():
    assert visible_indices(5, 12) == [4, 5, 6, 7]


def test_visible_indices_at_the_first_step():
    # Nothing before the first step: the top slot stays empty so the stack
    # keeps its shape instead of jumping.
    assert visible_indices(0, 12) == [None, 0, 1, 2]


def test_visible_indices_near_the_end():
    assert visible_indices(10, 12) == [9, 10, 11, None]
    assert visible_indices(11, 12) == [10, 11, None, None]


def test_visible_indices_tiny_build():
    assert visible_indices(0, 1) == [None, 0, None, None]
    assert visible_indices(1, 2) == [0, 1, None, None]


def test_visible_indices_clamps_a_wild_focus():
    # Scrolling past the ends clamps rather than showing an empty stack.
    assert visible_indices(-5, 12) == [None, 0, 1, 2]
    assert visible_indices(99, 12) == [10, 11, None, None]


def test_visible_indices_with_no_build():
    assert visible_indices(0, 0) == [None, None, None, None]


def test_live_focus_before_the_first_step():
    # current_index is -1 before the first step is reached; the player
    # should be looking at step 0, not an empty slot.
    assert live_focus(-1, 12) == 0


def test_live_focus_mid_build():
    # current_index is the last step already reached; the card to highlight
    # is the one the player should be DOING - the next one.
    assert live_focus(4, 12) == 5


def test_live_focus_when_the_build_is_complete():
    # A finished build rests on its final card rather than past the end.
    assert live_focus(11, 12) == 11
    assert live_focus(50, 12) == 11


# --- window size -> card size -----------------------------------------------

def test_card_scale_is_one_at_the_designed_width():
    # The golden case again: a window offering exactly the designed width
    # draws the cards exactly as designed.
    assert card_scale(CARD_WIDTH) == 1.0


def test_card_scale_follows_the_window():
    assert card_scale(CARD_WIDTH * 2) == 2.0
    assert card_scale(CARD_WIDTH * 1.5) == 1.5


def test_card_scale_clamps_at_both_ends():
    # A carelessly tiny window stays readable-ish; a full-screen one does
    # not produce poster-sized villagers.
    assert card_scale(10) == MIN_CARD_SCALE
    assert card_scale(100000) == MAX_CARD_SCALE


# ---- the width a card may actually occupy ---------------------------------
#
# The preview showed a horizontal scrollbar at every window size, and clipped
# the right-hand edge of every card. One cause, seen from two sides: the scale
# was computed from the whole viewport and the card was then made exactly that
# wide, so the stack's margins pushed the column wider than the viewport it
# had to fit inside. The margins were the platform's default - 11px a side,
# measured - and nothing had ever subtracted them.


def test_the_card_leaves_room_for_the_margins_around_it():
    """A card sized to the full viewport is a card that does not fit in it."""
    assert usable_card_width(600, 17) == 600 - 2 * STACK_MARGIN - 17


def test_a_card_and_its_chrome_fit_the_viewport_they_were_measured_from():
    """The property the always-off horizontal scrollbar depends on. With no
    bar to reach it, content wider than the viewport is not scrolled to - it
    is simply gone."""
    for viewport in (300, 420, 640, 900, 1920):
        for bar in (0, 15, 17, 24):
            usable = usable_card_width(viewport, bar)
            assert usable + 2 * STACK_MARGIN + bar <= viewport


def test_the_scrollbar_is_subtracted_even_when_it_is_not_showing():
    """Sizing to the bar-less width makes the bar appear, which narrows the
    viewport, which makes the card too wide. That oscillation is what the old
    CARD_MARGINS fudge was damping."""
    assert usable_card_width(600, 17) < usable_card_width(600, 0)


def test_a_viewport_smaller_than_its_own_chrome_still_gives_a_usable_width():
    """Mid-construction a viewport can report almost nothing. Returning zero
    or a negative there would divide the scale into nonsense."""
    assert usable_card_width(4, 17) >= 1
    assert usable_card_width(0, 0) >= 1
