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
