"""
Loom — the macOS backend's logic, without a Mac.

Two things in loom/capture/macos.py are pure enough to test anywhere, and both
are places where a mistake produces a plausible-looking picture rather than an
error - which is the kind Loom is least able to notice.

  * Frames arrive as CVPixelBuffers whose rows are PADDED. Reshaping by width
    instead of by the buffer's stride shears the image into a diagonal smear
    that still has a sensible mean brightness.
  * A stream that stops delivering must not keep serving its last frame. The
    game clock would stop moving, and a repeated value is believed by design,
    so Loom would report a frozen clock as real game time.

Skipped whole on other platforms, where pyobjc is not installed.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys
import time

import numpy as np
import pytest

macos = pytest.importorskip("loom.capture.macos",
                            reason="the macOS backend needs pyobjc")


# ---- which window is the game ------------------------------------------
#
# choose_window is a pure function of plain dicts, the same split the
# Windows backend makes and for the same reason: the rule that decides
# what Loom will read every coordinate through has to be testable without
# a desktop full of windows to arrange. The dicts here are drawn from a
# real `macos_probe --list` of this machine - the CrossOverHelper bundle
# id really does carry a per-bottle suffix, and the same bottle really
# does own windows whose app name is just the exe.


def candidate(title="", app_name="", bundle_id="", area=100):
    return {"window": object(), "title": title, "app_name": app_name,
            "bundle_id": bundle_id, "area": area}


FERAL = candidate(title="Age Of Empires II",
                  app_name="Age Of Empires II",
                  bundle_id="com.feralinteractive.ageofempires2",
                  area=2_000_000)
CROSSOVER = candidate(title="Age of Empires II: Definitive Edition",
                      app_name="Steam (AoE2 bottle)",
                      bundle_id="com.codeweavers.CrossOverHelper.57A12CF1",
                      area=2_000_000)
WINE_EXE = candidate(title="Age of Empires II: Definitive Edition",
                     app_name="AoE2DE_s.exe",
                     bundle_id="",
                     area=2_000_000)
BROWSER = candidate(title="aoc-mgz: Age of Empires II recorded game parsing",
                    app_name="Vivaldi",
                    bundle_id="com.vivaldi.Vivaldi",
                    area=3_000_000)


def test_the_feral_bundle_id_needs_no_corroboration():
    assert macos.choose_window([BROWSER, FERAL]) is FERAL


def test_a_crossover_owned_title_match_is_accepted():
    assert macos.choose_window([BROWSER, CROSSOVER]) is CROSSOVER


def test_a_bare_exe_owned_title_match_is_accepted():
    """The same bottle presents some of its windows with no bundle id at
    all, just the exe as the app name - both shapes are the game."""
    assert macos.choose_window([BROWSER, WINE_EXE]) is WINE_EXE


def test_a_browser_tab_about_the_game_is_refused():
    """The Vivaldi incident, ported: a title fragment alone is a guess,
    and an uncorroborated guess believed poisons every coordinate
    downstream. Note the browser window is the LARGEST candidate here -
    area must never outrank corroboration."""
    assert macos.choose_window([BROWSER]) is None


def test_the_playfield_beats_the_slivers():
    sliver = candidate(title="Age of Empires II: Definitive Edition",
                       app_name="AoE2DE_s.exe", area=57_024)
    assert macos.choose_window([sliver, WINE_EXE]) is WINE_EXE


def test_an_explicit_fragment_bypasses_corroboration():
    """Stating a target is not guessing one - the dev override exists to
    aim at an arbitrary window on purpose."""
    chosen = macos.choose_window([BROWSER], fragment="recorded game")
    assert chosen is BROWSER


def test_nothing_matching_is_nobody_home():
    assert macos.choose_window([BROWSER], fragment="chess") is None
    assert macos.choose_window([]) is None


class FakeWindow:
    """Stands in for a GameWindow without any ScreenCaptureKit behind it."""

    def __init__(self, size=(64, 32)):
        self.capture_size = size
        self._lock = __import__("threading").Lock()
        self._frame = None
        self._frame_at = 0.0
        self._pending = None

    latest = macos.GameWindow.latest
    _accept = macos.GameWindow._accept


def test_a_fresh_frame_is_served_as_is():
    window = FakeWindow()
    frame = np.full((32, 64, 3), 120, np.uint8)
    window._accept(frame)

    assert np.array_equal(window.latest(), frame)


def test_a_stale_frame_becomes_black():
    """Past the staleness bound the cache stops being evidence."""
    window = FakeWindow()
    window._accept(np.full((32, 64, 3), 120, np.uint8))
    # Backdate the arrival rather than sleeping through the real bound.
    window._frame_at = time.monotonic() - (macos.STALE_AFTER + 0.5)

    served = window.latest()

    assert served.shape == (32, 64, 3)
    assert served.max() == 0, "a stale frame must not be served as a reading"


def test_no_frame_yet_is_black_of_the_right_shape():
    """Black rather than None or a raise: the reader can crop it, fail to
    find the HUD, and report "no reading" through a path that already
    exists."""
    window = FakeWindow(size=(128, 48))

    served = window.latest()

    assert served.shape == (48, 128, 3)
    assert served.max() == 0


def test_a_new_frame_replaces_a_stale_one():
    window = FakeWindow()
    window._accept(np.full((32, 64, 3), 10, np.uint8))
    window._frame_at = time.monotonic() - (macos.STALE_AFTER + 0.5)
    assert window.latest().max() == 0

    window._accept(np.full((32, 64, 3), 200, np.uint8))

    assert window.latest().max() == 200


def test_the_staleness_bound_covers_several_missed_frames():
    """It has to be loose enough not to fire between two healthy polls.

    Loom polls every 300ms and the stream runs at FRAMES_PER_SECOND, so the
    bound must clear both comfortably or a momentary hiccup would blank a
    perfectly good HUD.
    """
    frame_interval = 1.0 / macos.FRAMES_PER_SECOND
    assert macos.STALE_AFTER > 0.3, "must outlast one poll interval"
    assert macos.STALE_AFTER >= frame_interval * 5


def test_window_id_is_how_streams_are_reused():
    """reader.connect and loom_overlay both look the window up; the second
    must not start a second stream over the same window."""
    assert isinstance(macos._WINDOWS, dict)


def _padded_bgra(width, height, stride):
    """A BGRA buffer whose rows are padded, with a known per-pixel value.

    Pixel (x, y) gets blue = x, green = y, so a sheared reshape is obvious
    rather than merely different.
    """
    buffer = np.zeros((height, stride // 4, 4), np.uint8)
    for y in range(height):
        for x in range(width):
            buffer[y, x] = (x, y, 200, 255)
    # Padding bytes are deliberately loud: if they survive into the result,
    # the slice back to the real width did not happen.
    buffer[:, width:] = 77
    return buffer.tobytes()


def test_padded_rows_are_sliced_back_to_the_real_width():
    width, height = 5, 3
    stride = 8 * 4          # padded well past width * 4
    raw = _padded_bgra(width, height, stride)

    frame = macos.bgra_to_bgr(raw, width, height, stride)

    assert frame.shape == (height, width, 3)
    assert 77 not in frame, "row padding leaked into the image"
    for y in range(height):
        for x in range(width):
            # BGR: blue = x, green = y, alpha dropped.
            assert tuple(frame[y, x]) == (x, y, 200)


def test_an_unpadded_buffer_still_works():
    """stride == width * 4 is legal and must not be a special case."""
    width, height = 4, 2
    stride = width * 4
    raw = _padded_bgra(width, height, stride)

    frame = macos.bgra_to_bgr(raw, width, height, stride)

    assert frame.shape == (height, width, 3)
    assert tuple(frame[1, 3]) == (3, 1, 200)


def test_the_frame_does_not_alias_the_source_buffer():
    """CoreVideo unlocks the buffer as soon as the conversion returns, so a
    view over it would be reading freed memory on some later poll."""
    width, height = 4, 2
    stride = width * 4
    raw = bytearray(_padded_bgra(width, height, stride))

    frame = macos.bgra_to_bgr(bytes(raw), width, height, stride)
    before = frame.copy()
    raw[:] = b"\xff" * len(raw)

    assert np.array_equal(frame, before)


# ---- the display-scale join ------------------------------------------------
#
# scale_for is pure on purpose: this join has been wrong twice. First the
# desktop's LARGEST factor doubled a window already in real pixels; then
# display.width()-over-points returned 1.0 on every display of a machine
# whose Retina screens were 2x, halving every capture - clock strokes
# thinned until hollow zeros split and a whole session read no clock at
# all. The fixture below is that machine's real display list, measured
# 30 Aug 2026.

THIS_DESK = [
    (-2560, 198, 2560, 1440, 5),    # 1x 2560 monitor
    (0, 0, 3360, 1890, 4),          # 4K in a scaled mode
    (3360, 356, 1728, 1117, 1),     # built-in Retina panel
]
THIS_BACKING = {5: 1.0, 4: 2.0, 1: 2.0}


def test_a_window_on_the_scaled_4k_display_captures_at_2x():
    """The game window that lost its clock: centred on display 4."""
    centre = (720 + 960, 356 + 556)

    assert macos.scale_for(centre, THIS_DESK, THIS_BACKING, 9.0) == 2.0


def test_a_window_on_the_1x_monitor_is_not_doubled():
    """The original mixed-DPI bug, kept refused: 1x pixels stay 1x."""
    centre = (-1280, 700)

    assert macos.scale_for(centre, THIS_DESK, THIS_BACKING, 9.0) == 1.0


def test_a_centre_no_display_owns_takes_the_fallback():
    centre = (99999, 99999)

    assert macos.scale_for(centre, THIS_DESK, THIS_BACKING, 2.0) == 2.0


def test_a_display_with_no_screen_entry_takes_the_fallback():
    """The join is by id, and an id NSScreen never mentioned must not be
    treated as 1x - falling back is honest, inventing a factor is not."""
    centre = (100, 100)

    assert macos.scale_for(centre, THIS_DESK, {}, 2.0) == 2.0
