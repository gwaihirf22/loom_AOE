"""
Loom — a capture that fails must not take the program with it.

Nothing upstream of the reader catches anything: LiveController.tick calls
poll() bare from a Qt timer, and loom_read/loom_coach call it bare from their
loops. So before this, one BadWindow when the game exited - or one macOS
refusal while the window was not being composited - ended the overlay in the
middle of a match.

The contract is that a poll which cannot see the screen comes back as a
Reading nobody can use, having told the session tracker the truth, and that
the next poll can recover.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import numpy as np
import pytest

from loom import capture, reader, session


class Boom:
    """A window whose every capture fails, however it is asked."""


def _reader_that_cannot_see(monkeypatch, hud=True):
    """A HudReader wired to a backend that always refuses."""
    def refuse(*_args, **_kwargs):
        raise capture.CaptureError("the window went away")

    monkeypatch.setattr(capture, "capture_region", refuse)
    monkeypatch.setattr(capture, "capture_window", refuse)
    monkeypatch.setattr(capture, "window_size", refuse)

    hud_reader = reader.HudReader()
    hud_reader.window = Boom()
    if hud:
        # Regions a previous successful find_hud would have left behind.
        hud_reader.hud = {
            "scale": 1.0, "score": 0.95,
            "villagers": (0, 0, 10, 10),
            "clock": (0, 0, 10, 10),
            "population": (0, 0, 10, 10),
            "min_glyph_width": reader.min_glyph_width(1.0),
            "max_glyph_width": reader.max_glyph_width(1.0),
        }
    return hud_reader


def test_poll_returns_an_unreadable_reading_instead_of_raising(monkeypatch,
                                                               capsys):
    hud_reader = _reader_that_cannot_see(monkeypatch)

    result = hud_reader.poll()

    assert result.hud_visible is False
    assert result.villagers is None
    assert result.game_time is None
    # Said once, so a failure lasting minutes does not bury the log at three
    # prints a second.
    assert "capture unavailable" in capsys.readouterr().out


def test_the_complaint_is_printed_once_not_every_poll(monkeypatch, capsys):
    hud_reader = _reader_that_cannot_see(monkeypatch)

    for _ in range(5):
        hud_reader.poll()

    printed = capsys.readouterr().out
    assert printed.count("capture unavailable") == 1


def test_the_session_is_told_the_truth(monkeypatch):
    """A blind poll must not look like a healthy one.

    If it passed a stale value the session tracker would never notice the game
    had gone, which is the one job it has.
    """
    hud_reader = _reader_that_cannot_see(monkeypatch)
    seen = []
    monkeypatch.setattr(hud_reader._session, "update",
                        lambda t, v: seen.append((t, v)))

    hud_reader.poll()

    assert seen == [(None, None)]


def test_find_hud_reports_no_hud_rather_than_raising(monkeypatch):
    """wait_for_hud loops on this, and the re-anchor path calls it mid-poll."""
    hud_reader = _reader_that_cannot_see(monkeypatch, hud=False)
    hud_reader._icon_template = np.zeros((8, 8), np.uint8)

    assert hud_reader.find_hud() is False


def test_a_reader_recovers_when_capture_comes_back(monkeypatch, capsys):
    """The failure must be a state, not a one-way door."""
    hud_reader = _reader_that_cannot_see(monkeypatch)
    hud_reader.poll()
    assert hud_reader._capture_failed is True

    # The screen comes back: a plain grey frame reads as no digits, but it
    # does not raise, which is what clearing the flag depends on.
    monkeypatch.setattr(capture, "capture_region",
                        lambda *a, **k: np.full((10, 10, 3), 60, np.uint8))
    monkeypatch.setattr(capture, "window_size", lambda *a, **k: (10, 10))
    hud_reader.poll()

    # Still nothing readable in grey, so the flag stays until digits return -
    # what matters here is that the poll completed without raising.
    assert hud_reader.hud is not None


def test_capture_error_is_one_type_across_backends():
    """Backends translate their own failures, so callers catch one thing."""
    from loom.capture import errors
    assert capture.CaptureError is errors.CaptureError
    assert issubclass(capture.CaptureError, RuntimeError)
