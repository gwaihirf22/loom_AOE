"""
Loom — the overlay never stops watching for a match.

Waiting IS the feature: the panel is meant to be started before the game and
left there. Two ways that promise got broken, both fixed here and both
pinned below.

  * An unreadable screen QUIT the overlay. That was carried over from when
    the wait was a blocking loop in front of the event loop, where the only
    thing an unreadable screen could mean was a startup that had failed. Once
    the panel sat waiting through menus and loading screens, the same code
    turned a passing hiccup into an overlay the player had to start again.

  * The game window was found ONCE. Start the overlay at the main menu, sit
    there a while, then begin a match, and the HUD might never be found -
    not because the anchor search is wrong, but because the frames being
    searched came from a window that was no longer the game's. Restarting
    the overlay fixed it, which is the shape of a stale handle.

No QApplication and no display: LiveSession is handed a stub panel, so the
waiting logic is testable as the plain state machine it is - the same reason
the window-flag and layout tests need no Qt.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

import loom_overlay
from loom import capture


class FakePanel:
    def __init__(self):
        self.stages = []

    def show_pregame(self, _build, stage):
        self.stages.append(stage)


class FakeApp:
    def __init__(self):
        self.quit_calls = 0

    def quit(self):
        self.quit_calls += 1


class FakeHud:
    """A HudReader that never finds a match, however often it is asked."""

    def __init__(self, connect_raises=False, find_raises=False):
        self.window = None
        self.hud = None
        self.connect_raises = connect_raises
        self.find_raises = find_raises
        self.find_calls = 0
        self.reacquire_calls = 0

    def connect(self, wait_seconds=None):
        if self.connect_raises:
            raise capture.CaptureError("no frame has ever arrived")
        self.window = object()
        return True

    def find_hud(self):
        self.find_calls += 1
        if self.find_raises:
            raise capture.CaptureError("the window is minimised")
        return False

    def reacquire_window(self):
        self.reacquire_calls += 1
        return False


def waiting_session(**hud_kwargs):
    panel, app = FakePanel(), FakeApp()
    session = loom_overlay.LiveSession(panel, build=object(), app=app,
                                       follow_state=object(), build_stem="x")
    session.hud = FakeHud(**hud_kwargs)
    return session, panel, app


def test_an_unreadable_screen_never_stops_the_overlay():
    """The regression. A capture failure is a condition Loom understands -
    a minimised window, a game mid-restart, exclusive fullscreen - and none
    of them is a reason to switch off the thing whose job is to wait."""
    session, _panel, app = waiting_session(connect_raises=True)

    for _ in range(50):
        session.tick()

    assert app.quit_calls == 0, "an unreadable screen must not quit"


def test_it_says_so_once_rather_than_every_poll():
    """Three times a second forever would bury everything else in the
    launcher's output pane, and the second line says nothing the first did
    not."""
    session, _panel, _app = waiting_session(connect_raises=True)

    session.tick()
    assert session._warned_unreadable
    before = session._warned_unreadable
    for _ in range(20):
        session.tick()
    assert session._warned_unreadable is before


def test_it_keeps_looking_for_a_hud_indefinitely():
    """There is no version of "waiting for a match" that is improved by
    ceasing to watch for one."""
    session, _panel, app = waiting_session()

    session.tick()                     # connects
    for _ in range(200):
        session.tick()

    assert session.hud.find_calls == 200
    assert app.quit_calls == 0


def test_it_asks_which_window_the_game_is_in_again():
    """connect() answers that once, and the answer goes stale - which is
    what made a match started after a long menu invisible until the overlay
    was restarted."""
    session, _panel, _app = waiting_session()

    session.tick()                     # connects
    for _ in range(loom_overlay.REACQUIRE_EVERY_NTH_LOOK * 3):
        session.tick()

    assert session.hud.reacquire_calls == 3


def test_a_capture_failure_mid_wait_still_counts_as_a_look():
    """Otherwise the re-acquisition and the explanation below would never
    come round for the player whose screen cannot be read at all - which is
    exactly the player who needs both."""
    session, _panel, _app = waiting_session()
    session.tick()                     # connects
    session.hud.find_raises = True

    for _ in range(loom_overlay.REACQUIRE_EVERY_NTH_LOOK):
        session.tick()

    assert session.hud.reacquire_calls == 1


def test_it_explains_itself_once_after_a_while(capsys):
    """A player sitting in a match wondering why nothing has happened needs
    the likely causes named. find_hud already explains a HUD it NEARLY
    recognised; this is for the case where it recognised nothing at all,
    which that note stays silent about."""
    session, _panel, _app = waiting_session()
    session.tick()
    capsys.readouterr()

    for _ in range(loom_overlay.LOOKS_BEFORE_EXPLAINING * 2):
        session.tick()

    said = capsys.readouterr().out
    assert said.count("No match detected yet") == 1
    for cause in ("HUD scale", "resolution", "UI mod"):
        assert cause in said, f"the note should name {cause!r} as a cause"
    assert "keep watching" in said, \
        "it must be clear Loom has not given up"
