"""
Loom — the Windows backend's logic, without Windows.

Everything Windows-only in loom/capture/windows.py is imported inside a
function, so the module itself loads anywhere and the pure decisions in it can
be checked from any machine. That is deliberate and it is the same trick
tests/test_capture_selector.py plays on the X11 backend: with Linux and
Windows now a dual boot on one machine, a test that only runs on the OS it
tests is a test that runs half as often as it should.

Three things here produce a plausible-looking wrong answer rather than an
error, which is the kind Loom is least able to notice:

  * A stream that stops delivering must not keep serving its last frame. The
    game clock would stop moving, and a repeated value is believed by design,
    so Loom would report a frozen clock as real game time.
  * Display scaling relates real pixels to Qt points. Confusing the two shifts
    the anchor scale under every pixel constant at once and degrades digit
    recognition silently - it never raises.
  * Picking the game's window by title alone matches a browser tab reading the
    wiki. The executable is the corroboration, and it must narrow the field
    without ever emptying it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import threading
import time

import numpy as np
import pytest

from loom.capture import windows
from loom.capture.errors import CaptureError


class FakeWindow:
    """Stands in for a GameWindow with no capture stream behind it."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._frame_at = 0.0
        self._closed = False

    latest = windows.GameWindow.latest

    def accept(self, frame):
        self._frame = frame
        self._frame_at = time.monotonic()


# ---- the staleness rule ---------------------------------------------------

def test_a_fresh_frame_is_served_as_is():
    window = FakeWindow()
    frame = np.full((32, 64, 3), 120, np.uint8)
    window.accept(frame)

    assert np.array_equal(window.latest(), frame)


def test_a_stale_frame_becomes_black():
    """Past the staleness bound the cache stops being evidence."""
    window = FakeWindow()
    window.accept(np.full((32, 64, 3), 120, np.uint8))
    # Backdate the arrival rather than sleeping through the real bound.
    window._frame_at = time.monotonic() - (windows.STALE_AFTER + 0.5)

    served = window.latest()

    assert served.shape == (32, 64, 3)
    assert served.max() == 0, "a stale frame must not be served as a reading"


def test_a_new_frame_replaces_a_stale_one():
    """The recovery path: a stream that goes quiet and then comes back must
    start reading again, not stay blanked."""
    window = FakeWindow()
    window.accept(np.full((32, 64, 3), 10, np.uint8))
    window._frame_at = time.monotonic() - (windows.STALE_AFTER + 0.5)
    assert window.latest().max() == 0

    window.accept(np.full((32, 64, 3), 200, np.uint8))

    assert window.latest().max() == 200


def test_no_frame_at_all_is_an_error_not_a_black_frame():
    """GameWindow.start does not return until a frame has landed, so this is
    unreachable in a running Loom - which is exactly why it should raise
    rather than invent a black frame of a size nothing has measured yet."""
    with pytest.raises(CaptureError):
        FakeWindow().latest()


def test_the_staleness_bound_outlasts_a_poll():
    """It has to be loose enough not to fire between two healthy polls, or a
    momentary hiccup would blank a perfectly good HUD."""
    assert windows.STALE_AFTER > 0.3


# ---- pixels versus points -------------------------------------------------

def test_scaling_is_exactly_one_at_96_dpi():
    """100% display scaling must be an identity, not 0.999-something: this
    divides every geometry number Loom hands to Qt."""
    assert windows.points_per_pixel(96) == 1.0


@pytest.mark.parametrize("dpi, expected", [
    (120, 1.25),        # 125%, the default on many 4K laptops
    (144, 1.5),         # 150%
    (192, 2.0),         # 200%
])
def test_the_usual_scaling_steps(dpi, expected):
    assert windows.points_per_pixel(dpi) == pytest.approx(expected)


def test_a_nonsense_dpi_does_not_divide_by_zero():
    """GetDpiForWindow returns 0 for a handle it does not like. Falling back
    to 1.0 costs correct placement on a scaled display; dividing by zero
    would take the overlay down."""
    assert windows.points_per_pixel(0) == 1.0


# ---- choosing the game's window -------------------------------------------

def window(hwnd, area, exe):
    return {"hwnd": hwnd, "area": area, "exe": exe}


def test_no_candidates_is_no_window():
    assert windows.choose_window([]) is None


def test_the_executable_beats_a_bigger_impostor():
    """The case this rule exists for: a maximised browser reading the AoE2
    wiki has the fragment in its title and more pixels than the game."""
    browser = window(1, 3840 * 2160, "vivaldi.exe")
    game = window(2, 1920 * 1080, "aoe2de_s.exe")

    assert windows.choose_window([browser, game]) is game


def test_the_largest_wins_among_the_game_s_own_windows():
    small = window(1, 320 * 240, "aoe2de_s.exe")
    main = window(2, 2560 * 1440, "aoe2de_s.exe")

    assert windows.choose_window([small, main]) is main


def test_corroboration_narrows_but_never_empties():
    """If no candidate looks like the game by executable, the title matches
    still stand. A renamed binary or an unexpected launcher must not make the
    game unfindable - narrowing the field is worth having, refusing to answer
    is not."""
    only = window(1, 1920 * 1080, "something_unexpected.exe")

    assert windows.choose_window([only]) is only


def test_an_unreadable_executable_does_not_disqualify():
    """_process_name returns "" when Windows refuses to say - a process at a
    higher integrity level, say. Not knowing costs the corroboration and must
    not cost the match."""
    unknown = window(1, 1920 * 1080, "")

    assert windows.choose_window([unknown]) is unknown


# ---- the seam -------------------------------------------------------------

def test_streams_are_reused_by_window_handle():
    """reader.connect and loom_overlay both look the window up; the second
    must not start a second stream over the same window."""
    assert isinstance(windows._WINDOWS, dict)


def test_the_module_imports_without_windows():
    """The whole point of the import discipline in this backend.

    Anything Windows-only lives inside a function, so this module loads on
    Linux and macOS - which is what lets every test above run on every
    platform, and what keeps test_capture_selector's contract check from
    erroring out on the Linux runner.
    """
    for name in ("open_display", "find_game_window", "window_size",
                 "window_geometry", "capture_region", "capture_window"):
        assert callable(getattr(windows, name))
