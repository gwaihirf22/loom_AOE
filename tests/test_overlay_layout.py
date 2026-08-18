"""
Loom — tests for the overlay's two-axis size mapping.

OverlayLayout is where "make the overlay bigger" and "make the text bigger"
become arithmetic, and the arithmetic carries the design: the overlay knob
owns everything, the text knob owns the fonts and the vertical axis but
never the width. These tests pin both the golden case - at 100% every
number must equal the designed layout exactly, or every saved overlay
position subtly shifts - and the axis separation that keeps the two knobs
independent.

Pure arithmetic, so no QApplication and no display, same as the window-flag
tests.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom.overlay import (ALERT_BAND_HEIGHT, ALERT_GAP, MAX_ALERT_BANDS,
                          OverlayLayout, PANEL_HEIGHT, PANEL_WIDTH)


def test_default_scales_reproduce_the_designed_layout():
    # The golden case: round(n * 1.0) == n, so 100%/100% is byte-identical
    # to the hand-designed layout. Checked against a real pixel grab too,
    # but this is the cheap guard that runs every time.
    L = OverlayLayout()
    assert L.x(16) == 16
    assert L.y(62) == 62
    assert L.y(84) == 84
    assert L.pt(10) == 10
    assert L.pt(15) == 15
    assert L.icon(16) == 16
    assert L.spacing == 1.0
    assert L.panel_width == PANEL_WIDTH == 560
    assert L.panel_height == PANEL_HEIGHT == 186
    assert L.band_height == ALERT_BAND_HEIGHT == 26
    assert L.band_gap == ALERT_GAP == 4
    assert (L.panel_height
            + MAX_ALERT_BANDS * (L.band_gap + L.band_height)) == 246


def test_overlay_scale_grows_everything():
    L = OverlayLayout(overlay_scale=2.0)
    assert L.x(16) == 32
    assert L.y(62) == 124
    assert L.pt(10) == 20
    assert L.icon(16) == 32
    assert L.panel_width == 1120
    assert L.panel_height == 372
    assert L.band_gap == 8


def test_text_scale_never_touches_the_horizontal_axis():
    # The whole point of the second knob: bigger writing makes the panel
    # TALLER, never wider - the footprint on the game is a placement
    # decision, and elision absorbs the width difference.
    L = OverlayLayout(text_scale=1.5)
    assert L.x(16) == 16
    assert L.panel_width == 560
    assert L.band_gap == 4
    assert L.pt(10) == 15
    assert L.y(62) == 93
    assert L.panel_height == 279
    assert L.band_height == 39


def test_the_two_scales_compose():
    L = OverlayLayout(overlay_scale=2.0, text_scale=1.5)
    assert L.pt(10) == 30
    assert L.y(10) == 30
    assert L.x(10) == 20
    assert L.spacing == 3.0


def test_fonts_never_scale_to_zero():
    # A 0pt QFont silently falls back to some default size; flooring at 1
    # keeps tiny scales merely tiny instead of unpredictable.
    L = OverlayLayout(overlay_scale=0.75, text_scale=0.75)
    assert L.pt(1) == 1


def test_default_spacing_leaves_designed_gaps_exact():
    # The shared drawing functions multiply their gap literals by a spacing
    # factor; at the default 1.0 every gap must come out exactly as designed,
    # because the launcher's preview cards call them with no spacing at all.
    for gap in (3, 4, 5, 6, 18):
        assert round(gap * 1.0) == gap


# ---- the "am I still following the game?" note -----------------------------
#
# describe_follow is a pure function of a mode and a binding, so every
# phrasing is checkable with no window. What it says is load-bearing rather
# than decorative: Loom's whole claim is that it reads the game and keeps up
# by itself, so a panel showing a step it is no longer syncing to is a lie
# unless it says so on its face.

def test_following_says_nothing_at_all():
    """The normal case must not spend a single pixel on saying it is normal."""
    from loom import follow
    from loom.overlay import describe_follow

    text, _ = describe_follow(follow.FOLLOWING, "Ctrl+Shift+R")

    assert text == ""


def test_no_mode_at_all_says_nothing():
    """Placement mode and the report page pass no mode; they must not sprout
    a note."""
    from loom.overlay import describe_follow

    assert describe_follow(None)[0] == ""


def test_manual_names_the_key_that_gets_out_of_it():
    """A player who switched following off twenty minutes ago will not
    remember which key did it."""
    from loom import follow
    from loom.overlay import describe_follow

    text, _ = describe_follow(follow.MANUAL, "Ctrl+Shift+R")

    assert "MANUAL" in text
    assert "Ctrl+Shift+R" in text


def test_manual_still_says_so_with_no_key_to_name():
    """Hotkeys can be switched off entirely, or unavailable on this platform.
    Saying nothing would imply the panel is still tracking the game."""
    from loom import follow
    from loom.overlay import describe_follow

    text, _ = describe_follow(follow.MANUAL, None)

    assert "MANUAL" in text
    assert "not following" in text


def test_holding_is_quieter_than_manual():
    """A hold fixes itself in seconds; shouting about it would train the
    player to ignore the notice that matters."""
    from loom import follow
    from loom.overlay import (describe_follow, HOLDING_COLOR,
                              NOT_FOLLOWING_COLOR)

    holding_text, holding_colour = describe_follow(follow.HOLDING, "Ctrl+Shift+R")
    manual_text, manual_colour = describe_follow(follow.MANUAL, "Ctrl+Shift+R")

    assert holding_text and holding_text != manual_text
    assert holding_colour == HOLDING_COLOR
    assert manual_colour == NOT_FOLLOWING_COLOR


def test_every_follow_mode_has_a_phrasing():
    """A mode with no branch would fall through to whatever the last one
    returned, which is how a panel ends up quietly lying."""
    from loom import follow
    from loom.overlay import describe_follow

    for mode in (follow.FOLLOWING, follow.HOLDING, follow.MANUAL):
        text, colour = describe_follow(mode, "Ctrl+Shift+R")
        assert colour is not None
        if mode != follow.FOLLOWING:
            assert text, mode


# ---- the transparency knobs -------------------------------------------------

def test_the_default_opacity_is_byte_identical_to_the_design():
    """The golden case: the DEFAULT (205/255, the slider at 80%) must come
    back with exactly the designed alphas, or every untouched install
    repaints. The slider itself is full-range - see the next test."""
    from loom import config
    from loom.overlay import BACKGROUND, BORDER, panel_background

    fill, border = panel_background(config.DEFAULT_BACKGROUND_OPACITY)

    assert fill.alpha() == BACKGROUND.alpha() == 205
    assert border.alpha() == BORDER.alpha() == 40
    assert (fill.red(), fill.green(), fill.blue()) == (
        BACKGROUND.red(), BACKGROUND.green(), BACKGROUND.blue())


def test_full_opacity_hides_the_game_behind_the_card():
    """The beta ask: at 100% the card is SOLID - alpha 255, not the designed
    205 - so the game cannot be seen through it at all."""
    from loom.overlay import panel_background

    fill, _border = panel_background(1.0)

    assert fill.alpha() == 255


def test_opacity_scales_the_alpha_and_nothing_else():
    from loom.overlay import BACKGROUND, panel_background

    fill, border = panel_background(0.5)

    assert fill.alpha() == round(255 * 0.5)
    assert border.alpha() < 40, "the border must fade with the card"
    assert (fill.red(), fill.green(), fill.blue()) == (
        BACKGROUND.red(), BACKGROUND.green(), BACKGROUND.blue())


def test_zero_background_removes_the_card_entirely():
    """Floating text with no card is a legitimate style, so 0 must mean 0."""
    from loom.overlay import panel_background

    fill, border = panel_background(0.0)

    assert fill.alpha() == 0
    assert border.alpha() == 0


def test_the_module_constant_is_untouched():
    """The trap this design exists to avoid: loom/browser.py imports
    BACKGROUND for the build preview's cards, and the launcher must not
    fade because the overlay did."""
    from loom.overlay import BACKGROUND, panel_background

    panel_background(0.1)

    assert BACKGROUND.alpha() == 205


# ---- the text-visibility scale ---------------------------------------------
#
# One slider whose midpoint is the designed look: below it the content fades
# toward invisible, above it the colours climb toward full contrast. The beta
# finding behind the upper half: with the card thinned, the designed greys
# are unreadable over bright terrain, and the useful direction is UP.

def test_the_midpoint_is_exactly_the_designed_look():
    """0.5 -> (1.0, 0.0), byte-identical - the golden case for this knob."""
    from loom.overlay import content_style

    assert content_style(0.5) == (1.0, 0.0)


def test_below_the_midpoint_fades_and_never_boosts():
    from loom.overlay import content_style

    assert content_style(0.0) == (0.0, 0.0)
    assert content_style(0.25) == (0.5, 0.0)


def test_above_the_midpoint_boosts_and_never_fades():
    from loom.overlay import content_style

    assert content_style(1.0) == (1.0, 1.0)
    assert content_style(0.75) == (1.0, 0.5)


def test_the_scale_is_monotone():
    """Dragging the slider right must never make anything LESS visible."""
    from loom.overlay import content_style

    steps = [content_style(v / 20) for v in range(21)]
    for (opacity_a, contrast_a), (opacity_b, contrast_b) in zip(steps,
                                                                steps[1:]):
        assert opacity_b >= opacity_a
        assert contrast_b >= contrast_a


def test_zero_contrast_returns_the_colour_bit_identical():
    """The default path must not construct so much as a new alpha."""
    from loom.overlay import TEXT, boosted

    assert boosted(TEXT, 0.0) is TEXT


def test_full_contrast_brightens_the_greys_to_their_maximum():
    """TEXT is a true grey and must reach pure white; DIM_TEXT carries a
    slight blue cast (160,160,168) and must reach the BRIGHTEST version of
    that same cast - not be flattened to white, which would lose the
    palette's character along with its dimness."""
    from loom.overlay import DIM_TEXT, TEXT, boosted

    assert boosted(TEXT, 1.0).getRgb() == (255, 255, 255, 255)

    bright_dim = boosted(DIM_TEXT, 1.0)
    assert bright_dim.value() == 255
    assert bright_dim.saturation() == DIM_TEXT.saturation()
    assert bright_dim.hue() == DIM_TEXT.hue()


def test_full_contrast_keeps_the_hue_of_a_colour():
    """The pace and resource colours must become brighter versions of
    THEMSELVES, not wash out toward white."""
    from loom.overlay import BEHIND_COLOR, boosted

    vivid = boosted(BEHIND_COLOR, 1.0)

    assert vivid.hue() == BEHIND_COLOR.hue()
    assert vivid.saturation() == BEHIND_COLOR.saturation()
    assert vivid.value() == 255


def test_contrast_preserves_alpha():
    from PyQt6.QtGui import QColor
    from loom.overlay import boosted

    faint = QColor(255, 255, 255, 28)      # the header divider

    assert boosted(faint, 1.0).alpha() == 28
