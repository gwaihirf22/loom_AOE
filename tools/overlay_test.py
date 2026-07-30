"""
Loom — overlay stacking test (Milestone 4, step one).

Before building any UI, answer the one question the whole overlay depends on:
will an always-on-top window actually draw over Age of Empires II while it is
running in exclusive full screen?

This deliberately contains no Loom logic. It puts a bright, obvious panel on
the screen for a few seconds so the answer can be seen with human eyes.

Run it with the game running and visible:

    QT_QPA_PLATFORM=xcb python -m tools.overlay_test
    QT_QPA_PLATFORM=xcb python -m tools.overlay_test --seconds 30

Why force xcb: on Wayland a client is not allowed to raise itself above other
windows - the compositor owns stacking, in the same spirit as Wayland refusing
to let applications read the screen. X11 has no such rule, and the game is an
XWayland client, so running the overlay through XWayland too puts both windows
in one X server where "keep above" still means something.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import os
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from loom import capture, passthrough


# A focused full-screen window sits in KWin's "active" layer, which outranks an
# ordinary keep-above window. Some window types live in higher layers still -
# that is why the volume popup appears over a full-screen game. So the type is
# worth trying several ways rather than assuming.
WINDOW_STYLES = {
    "tool": Qt.WindowType.Tool,
    "tooltip": Qt.WindowType.ToolTip,
    "splash": Qt.WindowType.SplashScreen,
    "notification": Qt.WindowType.Tool,  # type set properly via X11 below
}


class TestOverlay(QWidget):
    """A deliberately garish panel, so there is no doubt whether it is visible."""

    def __init__(self, seconds, style="tool", passthrough=True):
        super().__init__()
        self.style_name = style
        self.passthrough_name = "on" if passthrough else "off"

        flags = (Qt.WindowType.FramelessWindowHint      # no title bar or border
                 | Qt.WindowType.WindowStaysOnTopHint   # ask to sit above everything
                 | WINDOW_STYLES[style])
        if passthrough:
            # The arm under test. This is what empties the window's X11 input
            # region, so the pointer never enters it - a different mechanism
            # from WA_TransparentForMouseEvents below, which only stops Qt
            # delivering events and leaves the X server none the wiser.
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)

        # Per-pixel transparency: only what I paint shows, the rest is see-through.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Qt-level click-through. On its own this is NOT enough - that is the
        # whole point of the --passthrough comparison.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        # Never take focus, which would minimise a full-screen game.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.remaining = seconds
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            QApplication.quit()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Solid magenta panel: nothing in AoE2 looks like this.
        painter.setBrush(QColor(255, 0, 200, 220))
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 12, 12)

        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("sans", 22, QFont.Weight.Bold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                         f"LOOM OVERLAY TEST [{self.style_name} /"
                         f" passthrough {self.passthrough_name}]"
                         f"\nvisible? closing in {self.remaining}s")


def game_geometry():
    """Absolute position and size of the game window on the desktop.

    get_geometry() gives coordinates relative to the parent window, so I ask X
    to translate them into root-window coordinates - otherwise the overlay
    lands in the wrong place on a multi-monitor desktop.
    """
    display = capture.open_display()
    window = capture.find_game_window(display)
    if window is None:
        return None

    geometry = window.get_geometry()
    root = display.screen().root
    translated = window.translate_coords(root, 0, 0)

    # translate_coords returns the offset OF the root relative to the window,
    # so negate it to get the window's position on the desktop.
    return (-translated.x, -translated.y, geometry.width, geometry.height)


def set_x11_window_type(widget, type_name):
    """Ask X to treat this window as a particular kind of thing.

    Qt has no flag for "notification", but the window type is just an X
    property, so I can set it directly once the window exists. KWin decides
    which stacking layer a window belongs in partly from this.
    """
    from Xlib import display as xdisplay

    dpy = xdisplay.Display()
    window = dpy.create_resource_object("window", int(widget.winId()))
    window.change_property(
        dpy.intern_atom("_NET_WM_WINDOW_TYPE"),
        dpy.intern_atom("ATOM"),
        32,
        [dpy.intern_atom(type_name)],
    )
    dpy.sync()
    print(f"set window type to {type_name}")


def main():
    parser = argparse.ArgumentParser(description="Overlay stacking test")
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--style", default="tool", choices=sorted(WINDOW_STYLES),
                        help="which kind of window to ask the compositor for")
    parser.add_argument("--passthrough", default="on", choices=("on", "off"),
                        help="on: also set WindowTransparentForInput, which "
                             "empties the window's X11 input region. off: the "
                             "old behaviour, Qt-level click-through only - use "
                             "it to reproduce the cursor escaping the game.")
    args = parser.parse_args()

    placement = game_geometry()
    if placement is None:
        print("Could not find the Age of Empires II window. Is the game running?")
        return
    x, y, width, height = placement

    app = QApplication(sys.argv)
    print(f"Qt platform in use: {app.platformName()}")
    print(f"Game window: {width}x{height} at ({x}, {y})")

    overlay = TestOverlay(args.seconds, args.style, args.passthrough == "on")

    # Top-center of the game window, where a build-order panel would sit.
    panel_width, panel_height = 460, 120
    overlay.setGeometry(x + (width - panel_width) // 2, y + 60,
                        panel_width, panel_height)
    overlay.show()

    if args.style == "notification":
        set_x11_window_type(overlay, "_NET_WM_WINDOW_TYPE_NOTIFICATION")

    # Ask the X server what it believes, rather than trusting the flag took.
    rectangles = passthrough.input_rectangles(int(overlay.winId()))
    print(f"passthrough: {args.passthrough}")
    print(f"X11 input region of the test window: {rectangles}")
    if rectangles is None:
        print("  (could not ask the X server - not running under XWayland?)")
    elif rectangles:
        print("  the pointer ENTERS this window; expect the game to lose its"
              " grip on the cursor")
    else:
        print("  empty - the pointer passes straight through to the game")

    print()
    print("LOOK AT THE GAME NOW.")
    print("  visible over the game        -> the overlay draws where it should")
    print("  only visible when tabbed out -> full screen wins, use Windowed")
    print("  never visible at all         -> stacking is blocked")
    print()
    print("NOW MOVE THE MOUSE SLOWLY ACROSS THE PANEL and watch the cursor:")
    print("  it stays inside the game, panel or no panel")
    print("        -> passthrough works; the game keeps the mouse confined")
    print("  it comes unstuck, or slides onto the other monitor")
    print("        -> the panel released the game's cursor; passthrough FAILED")
    print("  (run again with --passthrough off to see the failure on purpose)")
    print()
    print("THEN CLICK where the panel is, on something selectable:")
    print("  the game selects the unit -> clicks reach the game")
    print("  nothing happens           -> the panel swallowed the click")
    print()

    app.exec()
    print("Test finished.")


if __name__ == "__main__":
    main()
