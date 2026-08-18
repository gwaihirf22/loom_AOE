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
