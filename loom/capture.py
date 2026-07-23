"""
Loom — screen capture.

Reads pixels straight out of the game's X window.

Why not capture the whole screen? Two reasons:
  1. On Wayland, applications are not allowed to read the screen, so a normal
     screenshot comes back black. The game is an XWayland client, though, so
     its own window CAN still be read.
  2. Working in window coordinates means the window's position on the desktop,
     and which monitor it is on, never matter.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

import numpy as np
from Xlib import display, X

WINDOW_NAME_FRAGMENT = "Age of Empires II"


def open_display():
    """Connect to the X server."""
    return display.Display()


def find_game_window(dpy, fragment=WINDOW_NAME_FRAGMENT):
    """Search the X window tree for the game window.

    X keeps every window in a tree starting at the root window, so I walk it
    and check each window's title. Returns None if the game is not running.
    """
    def walk(window):
        try:
            name = window.get_wm_name()
        except Exception:
            name = None

        if name and fragment in name:
            return window

        try:
            children = window.query_tree().children
        except Exception:
            children = []

        for child in children:
            found = walk(child)
            if found is not None:
                return found
        return None

    return walk(dpy.screen().root)


def window_size(window):
    """Return (width, height) of a window."""
    geometry = window.get_geometry()
    return geometry.width, geometry.height


def capture_region(window, x, y, width, height):
    """Capture part of a window and return it as a BGR image.

    x and y are relative to the window's own top-left corner.

    get_image() is the X protocol's XGetImage request:
      * X.ZPixmap    - give the pixels back row by row
      * 0xffffffff   - the "plane mask": send every color plane
    """
    raw = window.get_image(x, y, width, height, X.ZPixmap, 0xFFFFFFFF)

    # X sends 4 bytes per pixel: blue, green, red, and one padding byte.
    # frombuffer reads those bytes as numbers without copying them.
    pixels = np.frombuffer(raw.data, dtype=np.uint8)
    pixels = pixels.reshape(height, width, 4)

    # Drop the padding byte, leaving plain BGR for OpenCV.
    return pixels[:, :, :3]


def capture_window(window):
    """Capture a whole window as a BGR image."""
    width, height = window_size(window)
    return capture_region(window, 0, 0, width, height)
