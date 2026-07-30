"""
Loom — screen capture, and which backend does it.

Every other module says `from loom import capture` and calls the same names
whatever the operating system. This module picks the implementation once, at
import, and re-exports it; nothing downstream knows or cares.

The contract a backend must provide:

    open_display()                      connect to whatever hands out windows
    find_game_window(session, fragment) the game's window, or None
    window_size(window)                 (width, height) in pixels
    window_geometry(window, session)    (x, y, width, height) on the desktop
    capture_region(window, x, y, w, h)  a BGR numpy array
    capture_window(window)              a BGR numpy array

How the game is IDENTIFIED is deliberately left to the backend rather than
fixed here: on X11 it is a window-title substring, while on macOS the app has a
bundle identifier, which is exact where a title is a guess.

CaptureError earns its place in the contract. A capture that fails must raise,
never return a black array - a black frame is an environment fault (a
permission not granted, a window not being composited), and if it reaches the
reader as pixels it looks exactly like "the HUD is not on screen right now".
That is the difference between Loom saying "I cannot see" and Loom quietly
believing something wrong.

Importing this module NEVER fails, even with no backend for the platform.
Failing at import would take the whole program down at `from loom import
capture` - including the test suite, which has no interest in capturing
anything. Instead the missing backend is remembered and raised at the moment
somebody actually asks for pixels, where the message can say what is wrong.

LOOM_CAPTURE_BACKEND overrides the choice. It exists for tests, and for asking
a machine to load a backend it would not have picked - the only way to check
the Linux path still imports while sitting at a Mac.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import importlib
import os
import sys


class CaptureError(RuntimeError):
    """A frame could not be captured, with a reason worth printing.

    Defined here rather than in a backend so callers can catch it without
    knowing which backend is loaded.
    """


# Which backend belongs to which platform. Windows joins this table and
# nothing else in Loom changes - that is the whole point of the split.
BACKENDS = {
    "linux": "x11",
    "darwin": "macos",
}

# The names every backend must define, and which this module re-exports.
CONTRACT = ("open_display", "find_game_window", "window_size",
            "window_geometry", "capture_region", "capture_window")


def backend_name():
    """The backend this machine should use, honouring the override."""
    return os.environ.get("LOOM_CAPTURE_BACKEND") or BACKENDS.get(sys.platform)


def load_backend(name=None):
    """Import a backend module by name. Raises CaptureError if it cannot."""
    name = name or backend_name()
    if not name:
        raise CaptureError(
            f"Loom has no screen capture backend for {sys.platform!r}. "
            f"Known platforms: {', '.join(sorted(BACKENDS))}.")
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError as missing:
        raise CaptureError(
            f"the {name!r} capture backend could not be loaded on "
            f"{sys.platform!r}: {missing}") from missing


def _stub(problem):
    """Stand in for a backend that is not available.

    Every contract name becomes this, so the complaint arrives when something
    asks for pixels rather than when a test imports a module that happens to
    pull in loom.reader.
    """
    def unavailable(*_args, **_kwargs):
        raise problem
    return unavailable


try:
    backend = load_backend()
except CaptureError as problem:
    backend = None
    for _name in CONTRACT:
        globals()[_name] = _stub(problem)
else:
    for _name in CONTRACT:
        globals()[_name] = getattr(backend, _name)
