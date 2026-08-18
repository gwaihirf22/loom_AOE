"""
Loom — the click-through self-check must answer on macOS too.

The overlay being click-through is the one property whose failure is silent
and expensive: the pointer enters the panel, the game loses its hold on the
cursor, and the mouse walks onto another monitor mid-match. On X11 that is
verified by asking the server for the window's input region. Off X11 the
check used to answer "cannot tell", which is honest but means the guarantee
went unverified on macOS entirely.

It can be verified there, and more directly. Qt turns
WindowTransparentForInput into NSWindow.ignoresMouseEvents, which is the
window server's own routing flag rather than a library-internal filter - so
asking the window is asking the thing that actually decides.

Verified against a real overlay on this machine: overlay mode reports
ignoresMouseEvents=True and cannot become the key window, while placement
mode reports False, which is correct - a panel you are meant to drag has to
accept the mouse.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

import pytest

from loom import passthrough


def test_an_unrealised_window_is_cannot_tell_not_a_crash():
    """Every path through this module answers rather than raising.

    An overlay that refused to start because its self-check could not run
    would be a worse bug than the one the check exists to catch.

    Only 0 is exercised, and deliberately. Handing Objective-C an arbitrary
    non-null pointer does not raise - it segfaults the interpreter, which no
    try/except can catch. Writing that test crashed the whole suite once,
    which is exactly why the null guard now comes first.
    """
    assert passthrough.ignores_mouse_events(0) is None
    assert passthrough.ignores_mouse_events(None) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS path")
def test_check_routes_to_cocoa_on_macos():
    verdict, message = passthrough.check(0)
    assert verdict is None
    assert "NSWindow" in message, message


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS path")
def test_a_window_that_ignores_the_mouse_is_confirmed(monkeypatch):
    monkeypatch.setattr(passthrough, "ignores_mouse_events", lambda _: True)
    verdict, message = passthrough.check(123)
    assert verdict is True
    assert "confirmed" in message


@pytest.mark.skipif(sys.platform != "darwin", reason="the macOS path")
def test_a_window_that_accepts_the_mouse_is_reported_loudly(monkeypatch):
    """The message has to say what is at stake, because the symptom - a mouse
    escaping to another monitor - looks nothing like its cause."""
    monkeypatch.setattr(passthrough, "ignores_mouse_events", lambda _: False)
    verdict, message = passthrough.check(123)
    assert verdict is False
    assert "NOT click-through" in message
    assert "WindowTransparentForInput" in message
