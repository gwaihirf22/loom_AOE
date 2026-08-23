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

from loom.browser import (CARD_HEIGHT, CARD_GAP, CARD_WIDTH,
                          CHROME_FADE_IN_SECONDS, CHROME_FADE_OUT_SECONDS,
                          MAX_CARD_SCALE, MIN_CARD_SCALE, MIN_HEIGHT_CHROME,
                          MAX_VISIBLE_CARDS, MIN_VISIBLE_CARDS,
                          RESIZE_MARGIN, STACK_MARGIN,
                          WINDOW_MARGIN, WINDOW_RADIUS,
                          card_alpha, card_scale, cards_for_steps,
                          chrome_target, ground_opacity, live_focus,
                          resize_edges, stepped_opacity, usable_card_width,
                          visible_indices)


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


# ---------------------------------------------------------------------------
# The chrome fade
#
# The window keeps only its cards until the pointer arrives. Two of these are
# arithmetic; the third is a promise about what the player is allowed not to
# see, and it is the one that matters.


def test_the_pointer_arriving_brings_the_chrome_back():
    assert chrome_target(True, False) == 1.0


def test_the_pointer_leaving_takes_it_away_again():
    assert chrome_target(False, False) == 0.0


def test_a_warning_pins_the_chrome_up_with_no_pointer_anywhere_near():
    """The rule this whole feature has to bend around.

    "manual" is the panel saying it has stopped following the game, which
    CLAUDE.md forbids it doing quietly. Chrome that faded would leave manual
    and following looking identical from across the desk - the same silent,
    trusted failure as a wrong villager count.
    """
    assert chrome_target(False, True) == 1.0
    assert chrome_target(True, True) == 1.0


def test_a_fade_reaches_its_target_exactly_and_stops_there():
    for target, start in ((1.0, 0.0), (0.0, 1.0)):
        value = start
        for _ in range(500):
            value = stepped_opacity(value, target, 0.02)
        assert value == target


def test_a_fade_never_overshoots_however_long_the_tick():
    """A window that stalls for a second must not come back at -3.0 opacity."""
    for seconds in (0.016, 0.5, 5.0, 1000.0):
        assert stepped_opacity(0.5, 1.0, seconds) <= 1.0
        assert stepped_opacity(0.5, 0.0, seconds) >= 0.0


def test_a_fade_is_monotone():
    """It walks toward the target and never back the way it came."""
    value = 0.0
    for _ in range(20):
        nxt = stepped_opacity(value, 1.0, 0.02)
        assert nxt >= value
        value = nxt


def test_reaching_for_a_control_is_quicker_than_leaving_it():
    """Fading in wants to be immediate; fading out wants to be undramatic."""
    assert CHROME_FADE_IN_SECONDS < CHROME_FADE_OUT_SECONDS
    arriving = stepped_opacity(0.5, 1.0, 0.02) - 0.5
    leaving = 0.5 - stepped_opacity(0.5, 0.0, 0.02)
    assert arriving > leaving


def test_a_step_of_no_time_at_all_changes_nothing():
    """Two ticks in the same instant must not advance the fade."""
    assert stepped_opacity(0.4, 1.0, 0.0) == 0.4


def test_a_fade_already_finished_stays_finished():
    assert stepped_opacity(1.0, 1.0, 0.02) == 1.0
    assert stepped_opacity(0.0, 0.0, 0.02) == 0.0


# ---------------------------------------------------------------------------
# Resizing a window with no frame of its own


def test_each_edge_and_corner_is_recognised():
    wide, tall = 400, 300
    assert resize_edges(1, 150, wide, tall) == frozenset({"left"})
    assert resize_edges(398, 150, wide, tall) == frozenset({"right"})
    assert resize_edges(200, 1, wide, tall) == frozenset({"top"})
    assert resize_edges(200, 298, wide, tall) == frozenset({"bottom"})
    assert resize_edges(1, 1, wide, tall) == frozenset({"left", "top"})
    assert resize_edges(398, 1, wide, tall) == frozenset({"right", "top"})
    assert resize_edges(1, 298, wide, tall) == frozenset({"left", "bottom"})
    assert resize_edges(398, 298, wide, tall) == frozenset({"right",
                                                            "bottom"})


def test_the_middle_of_the_window_is_not_an_edge():
    assert resize_edges(200, 150, 400, 300) == frozenset()


def test_a_window_at_its_minimum_still_has_a_middle():
    """With the margin unclamped the two sides meet, and every point in the
    window becomes a resize handle - including every card in it."""
    for width in (1, 2, 3, 8, 20, 344):
        for height in (1, 2, 3, 8, 20, 294):
            middle = resize_edges(width // 2, height // 2, width, height,
                                  margin=RESIZE_MARGIN)
            if width > 2 * RESIZE_MARGIN and height > 2 * RESIZE_MARGIN:
                assert middle == frozenset()
            # Whatever the size, the answer is a sane set of names.
            assert middle <= frozenset({"left", "right", "top", "bottom"})


def test_no_point_is_both_sides_of_the_window_at_once():
    """left|right or top|bottom would fold the window inside out."""
    for width in (10, 40, 344, 1920):
        for x in range(0, width, max(1, width // 37)):
            edges = resize_edges(x, 5, width, 400)
            assert not {"left", "right"} <= edges


def test_the_window_minimum_still_leaves_the_cards_room_under_the_chrome():
    """The chrome floats over card 0 rather than sitting above the stack, so
    the minimum height has to keep paying for it: covering the top card is
    the design, covering the current one is not."""
    stack = (round(CARD_HEIGHT * MIN_CARD_SCALE) * MIN_VISIBLE_CARDS
             + CARD_GAP * (MIN_VISIBLE_CARDS - 1))
    assert MIN_HEIGHT_CHROME < round(CARD_HEIGHT * MIN_CARD_SCALE)
    assert stack - MIN_HEIGHT_CHROME > round(CARD_HEIGHT * MIN_CARD_SCALE)


# ---------------------------------------------------------------------------
# The window's own frame, now that Loom draws it


def test_the_gutter_stays_wide_enough_to_grab_the_window_by():
    """The ground around the cards is as thin as it goes, and this is why it
    stops there. The gutter is the strip of window belonging to no child
    widget - a child covering those pixels takes the mouse events with it, so
    a gutter narrower than the resize margin is a window that cannot be
    resized by its own edge."""
    assert WINDOW_MARGIN >= RESIZE_MARGIN


def test_no_child_widget_pokes_a_square_corner_through_a_round_one():
    """The content starts at (WINDOW_MARGIN, WINDOW_MARGIN). That corner has
    to sit INSIDE the corner arc, or the scroll area's own square corner is
    drawn over the rounding and the window looks merely clipped."""
    reach = WINDOW_RADIUS - WINDOW_MARGIN
    if reach > 0:
        assert (reach ** 2 + reach ** 2) ** 0.5 <= WINDOW_RADIUS


# ---------------------------------------------------------------------------
# The appearance settings
#
# Two players, two wallpapers, two right answers - so the ground, the cards
# and the text all became knobs. What earns tests is the arithmetic that
# keeps the defaults byte-identical to the designed look, because "the
# settings exist" must not quietly mean "every install looks different".


def test_the_designed_fade_is_rest_zero_hover_one():
    """The defaults reproduce the pre-settings behaviour exactly: nothing at
    rest, a solid ground at the end of the hover fade."""
    assert ground_opacity(0.0, 0.0, 1.0) == 0.0
    assert ground_opacity(1.0, 0.0, 1.0) == 1.0
    assert ground_opacity(0.5, 0.0, 1.0) == 0.5


def test_a_resting_ground_is_held_with_the_pointer_away():
    assert ground_opacity(0.0, 0.4, 1.0) == 0.4


def test_equal_endpoints_mean_the_ground_never_moves():
    for chrome in (0.0, 0.3, 1.0):
        assert ground_opacity(chrome, 0.6, 0.6) == 0.6


def test_the_ground_may_fade_the_other_way():
    """Hover BELOW rest is legal: a player may want the ground to clear
    when they reach in, to see the cards against it."""
    assert ground_opacity(1.0, 0.8, 0.2) == 0.2
    assert ground_opacity(0.0, 0.8, 0.2) == 0.8


def test_ground_opacity_is_clamped():
    assert ground_opacity(2.0, 0.0, 1.0) == 1.0
    assert ground_opacity(-1.0, 0.0, 1.0) == 0.0


def test_the_default_card_opacity_is_byte_identical():
    """The golden case: at the designed setting every role's alpha comes
    back exactly as drawn before the knob existed."""
    designed = 235 / 255
    for alpha in (235, 215, 205):
        assert card_alpha(alpha, designed) == alpha


def test_card_opacity_reaches_both_extremes():
    assert card_alpha(235, 0.0) == 0
    assert card_alpha(235, 1.0) == 255
    assert card_alpha(215, 0.0) == 0


def test_card_alpha_is_monotone_and_bounded():
    previous = -1
    for step in range(0, 101):
        value = card_alpha(235, step / 100)
        assert 0 <= value <= 255
        assert value >= previous
        previous = value


def test_bigger_cards_mean_fewer_of_them_in_the_same_window():
    """The height sum must scale with whatever makes a card taller, or the
    stack overflows the window - the pixel-constant rule, in the card count.

    It used to be the text multiplier alone. Cards now also grow to fit a
    step with a lot of instructions, so the count is taken from each card's
    own height rather than from one number times a count; the property being
    guarded is the same one.
    """
    height = 900
    plain = cards_for_steps([120] * MAX_VISIBLE_CARDS, height, CARD_GAP)
    grown = cards_for_steps([180] * MAX_VISIBLE_CARDS, height, CARD_GAP)
    assert grown < plain


def test_the_stack_never_overflows_the_window_it_was_measured_for():
    """The whole point of choosing the count: nothing to scroll inside the
    scroll area, which is what let the wheel and the scrollbar disagree.

    Mixed heights on purpose - a real stack holds a one-item step next to a
    seven-item one, and it is the SUM that has to fit, not any average.
    """
    heights = [90, 200, 130, 240, 110, 180, 150, 220, 100, 170, 140, 210]
    for available in (300, 500, 900, 1400):
        count = cards_for_steps(heights, available, CARD_GAP)
        used = sum(heights[:count]) + CARD_GAP * (count - 1)
        assert count >= MIN_VISIBLE_CARDS
        if count > MIN_VISIBLE_CARDS:
            assert used <= available, (
                f"{count} cards need {used}px of {available}px")


def test_the_shortest_stack_always_fits_however_tall_its_cards_are():
    """Below three cards it has stopped being a preview of anything, so the
    count holds and the CALLER shrinks the scale instead - the same escape
    the width already has."""
    assert cards_for_steps([400] * 12, 100, CARD_GAP) == MIN_VISIBLE_CARDS
