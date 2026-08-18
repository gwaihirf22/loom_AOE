"""
Loom — placement mode must never wait for the game.

Place Overlay's job is a reference corner and a draggable panel. It used to
travel the live path and block in hud.connect() until a game window appeared,
which meant the panel could not be positioned without starting a match - the
screen fallback existed but was unreachable from placement mode.

placement_origin is the fix: one non-blocking look for the game, then the
primary screen. These tests drive it with fakes, the same style as
launcher.beside() - the interesting cases (no game, a game that cannot be
captured right now, no backend at all) are exactly the ones inconvenient to
reproduce with real windows.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

import loom_overlay
from loom.capture.errors import CaptureError


@pytest.fixture
def screen(monkeypatch):
    """A fake primary screen, so no QApplication is ever constructed."""
    monkeypatch.setattr(loom_overlay, "screen_origin",
                        lambda app: (10, 20, 800))
    return (10, 20, 800)


def test_a_running_game_is_the_origin(monkeypatch, screen):
    monkeypatch.setattr(loom_overlay.capture, "open_display",
                        lambda: "session")
    monkeypatch.setattr(loom_overlay.capture, "find_game_window",
                        lambda display: "window")
    monkeypatch.setattr(loom_overlay.capture, "window_geometry",
                        lambda window, display: (5, 6, 700, 400))

    assert loom_overlay.placement_origin(app=None) == (
        5, 6, 700, "the game window")


def test_no_game_falls_back_to_the_screen(monkeypatch, screen):
    """The whole point: None means "not running", and placement proceeds."""
    monkeypatch.setattr(loom_overlay.capture, "open_display",
                        lambda: "session")
    monkeypatch.setattr(loom_overlay.capture, "find_game_window",
                        lambda display: None)

    assert loom_overlay.placement_origin(app=None) == (
        10, 20, 800, "the primary screen")


def test_a_game_that_cannot_be_captured_falls_back(monkeypatch, screen):
    """A minimised game raises rather than returning None - on Windows the
    capture stream cannot start on a minimised window. Placement must not
    care: the screen is a perfectly good ruler."""
    def refuse(display):
        raise CaptureError("the window is minimised")

    monkeypatch.setattr(loom_overlay.capture, "open_display",
                        lambda: "session")
    monkeypatch.setattr(loom_overlay.capture, "find_game_window", refuse)

    assert loom_overlay.placement_origin(app=None) == (
        10, 20, 800, "the primary screen")


def test_no_capture_backend_at_all_falls_back(monkeypatch, screen):
    """On a platform with no backend, open_display is a stub that raises.
    Placement is exactly the kind of task that must survive that."""
    def unavailable():
        raise CaptureError("no backend for this platform")

    monkeypatch.setattr(loom_overlay.capture, "open_display", unavailable)

    assert loom_overlay.placement_origin(app=None) == (
        10, 20, 800, "the primary screen")


def test_the_source_is_a_printable_phrase(monkeypatch, screen):
    """The source is printed so the player knows which corner the saved
    offset is measured from - on a multi-monitor desktop with the game on a
    non-primary screen, placing game-less measures from the wrong corner,
    and the printed origin is what makes that noticeable."""
    monkeypatch.setattr(loom_overlay.capture, "open_display",
                        lambda: "session")
    monkeypatch.setattr(loom_overlay.capture, "find_game_window",
                        lambda display: None)

    *_rest, source = loom_overlay.placement_origin(app=None)

    assert isinstance(source, str) and source


# ---- the default spot and the off-screen rescue -----------------------------
#
# Both born of the same live incident: a placement measured against one
# origin was replayed against another, and the panel sat off both displays -
# invisible, while believing itself perfectly placed. A player cannot drag a
# window they cannot see.

class FakePanel:
    def __init__(self, width=560, height=246):
        self._width, self._height = width, height
        self.moved_to = None

    def width(self):
        return self._width

    def height(self):
        return self._height

    def move(self, x, y):
        self.moved_to = (x, y)


def test_the_default_is_top_right_under_the_bar():
    panel = FakePanel(width=560)

    dx, dy = loom_overlay.default_offset(panel, width=2560)

    assert dx == 2560 - 560 - loom_overlay.PANEL_RIGHT_MARGIN
    assert dy == loom_overlay.PANEL_TOP_MARGIN


def test_the_default_top_margin_follows_the_hud_scale():
    """The bar is taller when the HUD is bigger, and the anchor scale is the
    measurement everything else already follows - a fixed margin here would
    be the pixel-constant rule broken one more time."""
    panel = FakePanel()

    _, small = loom_overlay.default_offset(panel, 2560, hud_scale=1.0)
    _, large = loom_overlay.default_offset(panel, 2560, hud_scale=1.5)

    assert large == round(small * 1.5)


def test_a_window_narrower_than_the_panel_still_shows_it():
    panel = FakePanel(width=560)

    dx, _ = loom_overlay.default_offset(panel, width=400)

    assert dx == 0


def test_a_rect_on_a_screen_is_visible():
    screens = [(0, 0, 2560, 1440)]

    assert loom_overlay.visible_on(screens, 100, 100, 560, 246)


def test_a_rect_off_every_screen_is_not():
    """The live bug's geometry: two side-by-side displays, panel beyond
    both."""
    screens = [(0, 0, 2560, 1440), (2560, -654, 1440, 2560)]

    assert not loom_overlay.visible_on(screens, 4200, 100, 560, 246)
    assert not loom_overlay.visible_on(screens, 100, -2000, 560, 246)


def test_a_sliver_does_not_count_as_visible():
    """Ten pixels of corner peeking in is lost for every practical purpose."""
    screens = [(0, 0, 2560, 1440)]

    assert not loom_overlay.visible_on(screens, 2550, 100, 560, 246)


def test_a_negative_coordinate_monitor_counts():
    """Multi-monitor desktops put screens at negative coordinates; a panel
    there is perfectly visible."""
    screens = [(-1920, 0, 1920, 1080)]

    assert loom_overlay.visible_on(screens, -1000, 100, 560, 246)


def test_an_offscreen_saved_position_is_rescued(monkeypatch, capsys):
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: (9000, 9000))
    panel = FakePanel()

    chosen = loom_overlay.place_panel(panel, 0, 0, 2560,
                                      screens=[(0, 0, 2560, 1440)])

    assert chosen == loom_overlay.default_offset(panel, 2560)
    assert panel.moved_to == chosen
    assert "off every screen" in capsys.readouterr().out


def test_a_good_saved_position_is_obeyed(monkeypatch, capsys):
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: (300, 400))
    panel = FakePanel()

    chosen = loom_overlay.place_panel(panel, 0, 0, 2560,
                                      screens=[(0, 0, 2560, 1440)])

    assert chosen == (300, 400)
    assert panel.moved_to == (300, 400)
    assert capsys.readouterr().out == ""


def test_no_screens_known_means_no_second_guessing(monkeypatch):
    """Callers that cannot enumerate screens must not have their saved
    position vetoed on no evidence."""
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: (9000, 9000))
    panel = FakePanel()

    chosen = loom_overlay.place_panel(panel, 0, 0, 2560)

    assert chosen == (9000, 9000)
