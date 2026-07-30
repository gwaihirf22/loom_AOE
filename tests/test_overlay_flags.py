"""
Loom — the overlay's window flags.

One expression in overlay.py carries three separately hard-won decisions, each
of which looks like clutter to a later reader:

  * ToolTip is what gets the panel above a full-screen game (notes 11b)
  * WindowTransparentForInput is what stops the pointer entering the panel,
    which is what stops the game losing its grip on the cursor (notes 11d)
  * neither may apply to --place, which exists to be dragged

None of that needs a display or a QApplication to check - window flags are just
bits. So these tests pin the bits, and the comments say what breaks when they
change. Both decisions were found by experiment and cost real time; a test is
cheaper than finding them twice.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PyQt6.QtCore import Qt

from loom.overlay import OVERLAY_WINDOW_FLAGS, PLACING_WINDOW_FLAGS

# The flags word packs a window TYPE into its low byte and a pile of
# independent hint bits above it. WindowType_Mask picks out just the type.
TYPE_MASK = int(Qt.WindowType.WindowType_Mask)
TRANSPARENT = int(Qt.WindowType.WindowTransparentForInput)


def window_type(flags):
    return int(flags) & TYPE_MASK


def test_the_overlay_is_still_a_tooltip_window():
    # Tool and SplashScreen were both tried against a live full-screen game:
    # Tool drew over everything except the game, SplashScreen never appeared.
    # Only ToolTip lands in a stacking layer above KWin's "active" layer.
    assert window_type(OVERLAY_WINDOW_FLAGS) == int(Qt.WindowType.ToolTip)


def test_transparent_for_input_cannot_displace_the_window_type():
    # Why the two decisions can coexist: the passthrough flag is a hint bit
    # living outside the type mask, so OR-ing it in cannot silently demote the
    # ToolTip to something that loses to the game.
    assert TRANSPARENT & TYPE_MASK == 0


def test_the_overlay_is_transparent_for_input():
    # The flag that empties the window's X11 input region. Without it the
    # pointer enters the panel, the game stops confining the cursor, and the
    # mouse escapes onto the second monitor in the middle of a match.
    assert int(OVERLAY_WINDOW_FLAGS) & TRANSPARENT


def test_the_overlay_is_frameless_and_stays_on_top():
    assert int(OVERLAY_WINDOW_FLAGS) & int(Qt.WindowType.FramelessWindowHint)
    assert int(OVERLAY_WINDOW_FLAGS) & int(Qt.WindowType.WindowStaysOnTopHint)


def test_placement_mode_still_takes_mouse_input():
    # --place exists precisely so the panel CAN be dragged. A window with an
    # empty input region cannot be picked up by the window manager at all, so
    # the passthrough flag must never leak into this branch.
    assert not (int(PLACING_WINDOW_FLAGS) & TRANSPARENT)
    assert window_type(PLACING_WINDOW_FLAGS) == int(Qt.WindowType.Window)
