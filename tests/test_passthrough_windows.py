"""
Loom — the click-through self-check must answer on Windows too.

The overlay being click-through is the one property whose failure is silent
and expensive: the pointer enters the panel, the game loses its hold on the
cursor, and the mouse walks onto another monitor mid-match. On X11 that is
verified by asking the server for the window's input region, and on macOS by
asking the NSWindow. On Windows the check used to fall through to the X11
path and print "cannot check click-through", which is honest and useless.

It can be verified here, and just as directly. Qt turns
WindowTransparentForInput into WS_EX_TRANSPARENT, which is the bit the window
manager itself consults when it decides where a click lands - not a filter
inside Qt that merely resembles click-through, which is the doubt this whole
module exists to settle.

Verified against a real overlay on this machine: a window carrying
overlay.OVERLAY_WINDOW_FLAGS reports True, and a plain QWidget reports False.
The second half of that matters as much as the first - a check that cannot
say no is not a check.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

import pytest

from loom import passthrough


def test_an_unrealised_window_is_cannot_tell_not_a_crash():
    """Every path through this module answers rather than raising.

    An overlay that refused to start because its self-check could not run
    would be a worse bug than the one the check exists to catch. Runs
    everywhere: off Windows there is no windll to reach, which is one more
    way of not being able to tell.
    """
    assert passthrough.is_transparent_to_the_pointer(0) is None
    assert passthrough.is_transparent_to_the_pointer(None) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows path")
def test_a_handle_windows_does_not_recognise_is_cannot_tell():
    """A style word of 0 means "no answer", not "no flags set".

    GetWindowLongPtrW returns 0 both for a window with no extended style and
    for a handle it does not recognise, and the two cannot be told apart. An
    overlay that has not been realised yet must not be reported as broken, so
    the ambiguous answer is treated as no answer.
    """
    assert passthrough.is_transparent_to_the_pointer(1) is None


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows path")
def test_check_routes_to_win32_on_windows():
    verdict, message = passthrough.check(0)
    assert verdict is None
    assert "extended style" in message, message


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows path")
def test_a_transparent_window_is_confirmed(monkeypatch):
    monkeypatch.setattr(passthrough, "is_transparent_to_the_pointer",
                        lambda _: True)
    verdict, message = passthrough.check(123)
    assert verdict is True
    assert "confirmed" in message
    assert "WS_EX_TRANSPARENT" in message


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows path")
def test_a_window_that_is_hit_tested_is_reported_loudly(monkeypatch):
    """The message has to say what is at stake, because the symptom - a mouse
    that stops at an invisible panel - looks nothing like its cause."""
    monkeypatch.setattr(passthrough, "is_transparent_to_the_pointer",
                        lambda _: False)
    verdict, message = passthrough.check(123)
    assert verdict is False
    assert "NOT click-through" in message
    assert "WindowTransparentForInput" in message


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows path")
def test_the_real_overlay_flags_really_are_transparent():
    """The end-to-end claim, against Qt rather than against my reading of it.

    Everything else here is about the check; this is about the thing being
    checked. It builds a window with the actual OVERLAY_WINDOW_FLAGS and asks
    Windows - so if a Qt upgrade ever stopped mapping WindowTransparentForInput
    onto WS_EX_TRANSPARENT, this fails instead of a mouse escaping mid-match.

    A plain window is checked alongside it, because a self-check that always
    answers yes would pass this file while proving nothing.

    It needs Qt's native "windows" platform plugin: under offscreen - which is
    how CI runs the whole suite - winId() is not a real HWND, so Windows
    answers "cannot tell" about it, honestly. That is the check working as
    designed on a handle that is not a window, not evidence about the flags,
    so the test skips rather than failing. It runs for real on the dev
    machine, where the suite runs against a desktop.
    """
    QtWidgets = pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import Qt

    from loom import overlay

    application = (QtWidgets.QApplication.instance()
                   or QtWidgets.QApplication([]))
    if application.platformName() != "windows":
        pytest.skip("needs the native windows platform plugin; "
                    f"running under {application.platformName()!r}, whose "
                    "winId() is not an HWND")

    transparent = QtWidgets.QWidget()
    transparent.setWindowFlags(overlay.OVERLAY_WINDOW_FLAGS)
    transparent.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    transparent.resize(200, 80)
    transparent.show()

    plain = QtWidgets.QWidget()
    plain.resize(200, 80)
    plain.show()

    application.processEvents()
    try:
        assert passthrough.check(int(transparent.winId()))[0] is True
        assert passthrough.check(int(plain.winId()))[0] is False
    finally:
        transparent.close()
        plain.close()
