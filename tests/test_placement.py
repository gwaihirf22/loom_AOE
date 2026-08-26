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


# ---- the offset must round-trip through the SAME corner ----------------
#
# The bug these exist for. Placement measured the offset from
# placement_origin, which prefers the GAME WINDOW; the overlay applied it at
# startup from screen_origin, which is always the PRIMARY SCREEN. For a
# full-screen game on the primary monitor those are the same corner, so the
# mismatch cancelled and nothing showed for as long as the game was played
# that way. Windowed, on a second monitor, they diverge - and a position the
# player had plainly dropped on screen came back off every screen, was
# rescued to the default, and looked exactly like placement failing to save.
#
# A saved coordinate is meaningless without the origin it was measured from,
# so what is pinned here is that the two agree.


class MovablePanel(FakePanel):
    """A FakePanel that remembers where it is, so a position can round-trip."""

    def __init__(self, width=560, height=246):
        super().__init__(width, height)
        self._x, self._y = 0, 0

    def x(self):
        return self._x

    def y(self):
        return self._y

    def move(self, x, y):
        super().move(x, y)
        self._x, self._y = x, y


@pytest.fixture
def windowed_game(monkeypatch, screen):
    """A game NOT at the primary screen's corner - the case that diverges."""
    monkeypatch.setattr(loom_overlay.capture, "open_display",
                        lambda: "session")
    monkeypatch.setattr(loom_overlay.capture, "find_game_window",
                        lambda display: "window")
    monkeypatch.setattr(loom_overlay.capture, "window_geometry",
                        lambda window, display: (2042, 108, 1165, 1358))
    return (2042, 108)


def test_a_placed_position_comes_back_where_it_was_put(monkeypatch,
                                                       windowed_game):
    """The whole round trip: place, save, start, land in the same spot."""
    screens = [(0, 0, 2048, 1152), (2560, -1555, 1152, 2752)]
    saved = {}
    monkeypatch.setattr(loom_overlay.config, "set_overlay_offset",
                        lambda dx, dy: saved.update(offset=(dx, dy)))

    # Placement: measure a corner, drop the panel somewhere plainly on screen.
    place_x, place_y, _width, _source = loom_overlay.placement_origin(app=None)
    panel = MovablePanel()
    panel.move(1296, 550)
    loom_overlay.remember_position(panel, place_x, place_y)

    # Startup: the same rule picks the corner, so the offset means something.
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: saved["offset"])
    start_x, start_y, width, _source = loom_overlay.placement_origin(app=None)
    fresh = MovablePanel()
    loom_overlay.place_panel(fresh, start_x, start_y, width, screens=screens)

    assert fresh.moved_to == (1296, 550)


def test_the_two_origins_are_the_same_corner(windowed_game):
    """Stated directly, because the round trip above can only fail loudly
    once someone plays windowed. This fails the moment they diverge."""
    placement = loom_overlay.placement_origin(app=None)[:3]

    # screen_origin is the FALLBACK inside placement_origin, not a rival
    # answer to it - it is what placement_origin returns when there is no
    # game to measure. With a game running it must not be what is used.
    assert placement == (2042, 108, 1165)
    assert placement != loom_overlay.screen_origin(app=None)


def test_the_offset_is_meaningless_against_the_other_corner(monkeypatch,
                                                            windowed_game,
                                                            capsys):
    """What the bug actually did, kept as evidence rather than a story."""
    screens = [(0, 0, 2048, 1152), (2560, -1555, 1152, 2752)]
    # The offset a player gets by dropping the panel at (1296, 550) with the
    # game windowed at (2042, 108).
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: (1296 - 2042, 550 - 108))
    panel = MovablePanel()

    # Applied against the primary screen, as startup used to do.
    loom_overlay.place_panel(panel, 0, 0, 2048, screens=screens)

    assert panel.moved_to != (1296, 550)
    assert "off every screen" in capsys.readouterr().out


def test_reset_puts_the_panel_at_the_GAME_window_top_right(windowed_game):
    """Not the screen's top right, which is a different corner entirely
    once the game is windowed.

    PANEL_TOP_MARGIN exists to tuck the panel under the game's resource
    bar, and a bar is only ever at the top of the GAME window - measured
    down from the screen's top edge that number means nothing unless the
    game happens to be full-screen. The author's ruling, and the two agree
    anyway for full-screen play, which is why this went unnoticed.
    """
    origin_x, origin_y, width, _source = loom_overlay.placement_origin(
        app=None)
    panel = MovablePanel()

    dx, dy = loom_overlay.default_offset(panel, width)

    # Right-aligned inside the GAME window, not inside the screen.
    assert origin_x + dx + panel.width() == origin_x + width -         loom_overlay.PANEL_RIGHT_MARGIN
    assert dy == loom_overlay.PANEL_TOP_MARGIN


# ---- the DEFAULT needs the same guard the saved position gets ----------


def test_a_default_off_every_screen_is_rescued_to_the_primary(monkeypatch,
                                                              capsys):
    """The hole the old guard left.

    It asked its question only about a SAVED position, as though the
    fallback were safe by construction. The default is the game window's
    top-right, so a game dragged mostly off the desktop puts the default off
    every screen too - and rescuing a bad saved position onto an equally
    invisible default is not a rescue.
    """
    monkeypatch.setattr(loom_overlay.config, "overlay_offset", lambda: None)
    screens = [(0, 0, 2048, 1152)]
    panel = MovablePanel(width=560, height=246)

    # A game window far off the right of the desktop.
    chosen = loom_overlay.place_panel(panel, 9000, 50, 1165, screens=screens,
                                      primary=(0, 0, 2048, 1152))

    x, y = panel.moved_to
    assert loom_overlay.visible_on(screens, x, y, panel.width(),
                                   panel.height())
    assert x == 2048 - 560 - loom_overlay.PANEL_RIGHT_MARGIN
    assert "off every screen too" in capsys.readouterr().out
    # The rescue is returned as an offset from the same origin, so a caller
    # that remembers it puts the panel in the same place next time.
    assert (9000 + chosen[0], 50 + chosen[1]) == (x, y)


def test_a_reachable_default_is_left_alone(monkeypatch, capsys):
    """The rescue must not fire on the normal case."""
    monkeypatch.setattr(loom_overlay.config, "overlay_offset", lambda: None)
    screens = [(0, 0, 2048, 1152), (2560, -1555, 1152, 2752)]
    panel = MovablePanel()

    chosen = loom_overlay.place_panel(panel, 2042, 108, 1165, screens=screens,
                                      primary=(0, 0, 2048, 1152))

    assert chosen == loom_overlay.default_offset(panel, 1165)
    assert capsys.readouterr().out == ""


def test_without_a_primary_there_is_no_rescue(monkeypatch, capsys):
    """Deliberate, and the reason primary is a parameter rather than looked
    up inside.

    The rescue belongs to placing a panel for the FIRST time, where an
    invisible panel looks like Loom failing to start. A live re-place - a
    slider nudged mid-match - must never tear the panel off to another
    screen; that is the same failure the launcher's Reset button avoids by
    waiting for the next start.
    """
    monkeypatch.setattr(loom_overlay.config, "overlay_offset", lambda: None)
    screens = [(0, 0, 2048, 1152)]
    panel = MovablePanel()

    chosen = loom_overlay.place_panel(panel, 9000, 50, 1165, screens=screens)

    assert chosen == loom_overlay.default_offset(panel, 1165)
    assert "off every screen too" not in capsys.readouterr().out


def test_no_screens_still_means_no_second_guessing(monkeypatch):
    """Unchanged by the new guard: "I could not check" must never become
    "it is not visible"."""
    monkeypatch.setattr(loom_overlay.config, "overlay_offset",
                        lambda: (9000, 9000))
    panel = MovablePanel()

    chosen = loom_overlay.place_panel(panel, 0, 0, 2560)

    assert chosen == (9000, 9000)
