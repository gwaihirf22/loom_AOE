"""
Loom — screen capture, the X11 backend.

Reads pixels straight out of the game's X window. This is the original and
still the only capture path on Linux; loom/capture/__init__.py picks between
this and its siblings by platform.

Why not capture the whole screen? Two reasons:
  1. On Wayland, applications are not allowed to read the screen, so a normal
     screenshot comes back black. The game is an XWayland client, though, so
     its own window CAN still be read.
  2. Working in window coordinates means the window's position on the desktop,
     and which monitor it is on, never matter.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import functools

import numpy as np
from Xlib import display, error, X

from .errors import CaptureError

WINDOW_NAME_FRAGMENT = "Age of Empires II"

# Everything python-xlib raises when the server says no or goes away. Listed
# rather than catching Exception, so a genuine bug in this module still
# surfaces as itself instead of being relabelled a capture failure.
XLIB_ERRORS = (error.XError, error.DisplayError, error.ConnectionClosedError,
               error.ResourceIDError)


def _translates_errors(function):
    """Re-raise this function's Xlib failures as CaptureError.

    The contract promises one error type whatever the backend, so callers can
    degrade to "no reading" without importing Xlib to know what went wrong.
    That matters most for the failure this makes survivable: the game exits
    mid-session, its window id stops resolving, and every later capture raises
    BadWindow. Before this it climbed straight out of the poll timer and took
    the overlay with it.
    """
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except XLIB_ERRORS as problem:
            raise CaptureError(
                f"X11 capture failed in {function.__name__}: "
                f"{type(problem).__name__}: {problem}") from problem
    return wrapper


@_translates_errors
def open_display():
    """Connect to the X server."""
    return display.Display()


@_translates_errors
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


@_translates_errors
def window_size(window):
    """Return (width, height) of a window."""
    geometry = window.get_geometry()
    return geometry.width, geometry.height


@_translates_errors
def window_geometry(window, display):
    """Absolute position and size of the window on the desktop.

    Returns (x, y, width, height). The overlay measures its offset from the
    game window's corner, so it needs that corner in DESKTOP coordinates.

    get_geometry() is relative to the parent window, so I ask X to translate
    into root coordinates - otherwise the panel lands on the wrong monitor.

    The display is passed in rather than reached through the window, and that
    is deliberate. `window.display` is python-xlib's PROTOCOL display, which
    has no screen() - only the Xlib.display.Display wrapper does. Deriving the
    root from the window would need
    window.display.info.roots[window.display.default_screen], which is a second
    way of spelling the same thing and one I cannot test without an X server.
    This body is byte-identical to the loom_overlay.game_geometry it replaces.
    """
    geometry = window.get_geometry()
    root = display.screen().root
    translated = window.translate_coords(root, 0, 0)
    return (-translated.x, -translated.y, geometry.width, geometry.height)


@_translates_errors
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


@_translates_errors
def capture_window(window):
    """Capture a whole window as a BGR image."""
    width, height = window_size(window)
    return capture_region(window, 0, 0, width, height)
