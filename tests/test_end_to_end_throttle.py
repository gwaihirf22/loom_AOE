"""
Loom — the launcher's settings throttle, against a counted fake child.

A slider fires on every tick of a drag. The throttle must let the first
change through at once (the panel follows the drag - the whole feature),
drop the flood in the middle, and always deliver a trailing send so the
value the player settled on is never the one that got dropped.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from loom import config, launcher, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path / "stats")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeChild:
    """Stands in for the overlay's ChildProcess: counts, never writes."""

    def __init__(self, running=True):
        self.requests = 0
        self._running = running

    def is_running(self):
        return self._running

    def request_settings_changed(self):
        self.requests += 1


def wait(app, ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def window(app):
    """ONE launcher for the whole file. A second LauncherWindow in the same
    process aborts outright - it re-registers the global hotkey and rebuilds
    singleton child windows - and the real program never builds two either."""
    win = launcher.LauncherWindow()
    yield win
    win.close()


@pytest.fixture(autouse=True)
def no_child_between_tests(window):
    window.overlay_process = None
    yield
    window.overlay_process = None


def test_a_drag_is_throttled_and_the_last_value_lands(app, window):
    child = FakeChild()
    window.overlay_process = child

    for value in range(40):
        window.transparency.background.setValue(value)
    during = child.requests

    assert 1 <= during < 10, "40 ticks should collapse to a few requests"

    wait(app, launcher.ANNOUNCE_SETTINGS_EVERY_MS * 3)

    assert child.requests > during, "the trailing send never went out"
    assert config.background_opacity() == 39 / 100


def test_a_stopped_overlay_is_not_written_to(app, window):
    """_overlay_finished leaves the dead ChildProcess object in place, so
    the liveness test must be is_running(), not a null check."""
    child = FakeChild(running=False)
    window.overlay_process = child

    window.transparency.background.setValue(55)
    wait(app, launcher.ANNOUNCE_SETTINGS_EVERY_MS * 3)

    assert child.requests == 0
    assert config.background_opacity() == 0.55, "config must still be saved"


def test_no_overlay_at_all_still_saves_config(app, window):
    window.overlay_process = None

    window.appearance.overall.setValue(125)
    wait(app, launcher.ANNOUNCE_SETTINGS_EVERY_MS * 3)

    assert config.overlay_scale() == 1.25


def test_the_size_box_announces_too(app, window):
    child = FakeChild()
    window.overlay_process = child

    window.appearance.text.setValue(110)
    wait(app, launcher.ANNOUNCE_SETTINGS_EVERY_MS * 3)

    assert child.requests >= 1
    assert config.text_scale() == 1.1
