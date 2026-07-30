"""
Loom — the capture backend seam.

loom/capture used to be one X11 module. It is now a package that picks a
backend by platform, so the same `from loom import capture` works on Linux and
macOS, and a Windows backend will slot in beside them.

These tests guard the seam itself rather than any backend's pixels: that every
backend offers the whole contract, that the platform choice is what it claims,
and - the one that matters most while the author is travelling - that the X11
backend still IMPORTS on a machine that is not Linux. python-xlib is a
pure-Python wheel, so it loads anywhere; only opening a display needs an X
server. That makes "did the move break the Linux module?" answerable from a
Mac, days before the Linux box is available to say so properly.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import importlib

import pytest

from loom import capture


def test_package_exports_the_whole_contract():
    for name in capture.CONTRACT:
        assert callable(getattr(capture, name)), f"{name} missing from loom.capture"


def test_every_known_backend_offers_the_whole_contract():
    """A backend that is missing a name would fail at the worst moment -
    mid-match, the first time that particular call is reached."""
    for platform, name in capture.BACKENDS.items():
        try:
            module = importlib.import_module(f"loom.capture.{name}")
        except ImportError:
            # A backend whose own dependencies are absent here (pyobjc on
            # Linux, say) cannot be inspected, and that is not a failure.
            continue
        for wanted in capture.CONTRACT:
            assert hasattr(module, wanted), \
                f"{name} backend ({platform}) has no {wanted}"


def test_x11_backend_imports_off_linux():
    """The Linux path must stay loadable from any machine.

    This is what makes the capture.py -> capture/x11.py move checkable without
    a Linux box: if the move broke an import or a name, this fails here.
    """
    x11 = importlib.import_module("loom.capture.x11")
    for wanted in capture.CONTRACT:
        assert hasattr(x11, wanted)


def test_override_wins_over_the_platform(monkeypatch):
    monkeypatch.setenv("LOOM_CAPTURE_BACKEND", "somebackend")
    assert capture.backend_name() == "somebackend"


def test_platform_choice_without_an_override(monkeypatch):
    monkeypatch.delenv("LOOM_CAPTURE_BACKEND", raising=False)
    monkeypatch.setattr(capture.sys, "platform", "linux")
    assert capture.backend_name() == "x11"
    monkeypatch.setattr(capture.sys, "platform", "darwin")
    assert capture.backend_name() == "macos"


def test_unknown_platform_is_a_capture_error(monkeypatch):
    monkeypatch.delenv("LOOM_CAPTURE_BACKEND", raising=False)
    monkeypatch.setattr(capture.sys, "platform", "plan9")
    assert capture.backend_name() is None
    with pytest.raises(capture.CaptureError):
        capture.load_backend()


def test_missing_backend_module_is_a_capture_error():
    with pytest.raises(capture.CaptureError):
        capture.load_backend("no_such_backend")


def test_a_missing_backend_fails_on_use_not_on_import():
    """Importing loom.capture must never explode.

    An import-time raise would take down anything that merely touches
    loom.reader - the test suite included - on a platform with no backend yet.
    The complaint belongs at the moment somebody asks for pixels.
    """
    stub = capture._stub(capture.CaptureError("no backend here"))
    with pytest.raises(capture.CaptureError):
        stub("some", "arguments")
