"""
Loom — placement mode's panel: the button, the footprint, the live sliders.

Place Overlay answers one question - "where do I put this so it does not
cover something I need" - and every one of these tests guards a way that
answer can be quietly wrong.

The footprint tests are the important ones. The saved position is the
panel's TOP-LEFT and the panel grows DOWNWARD, so a placement panel that is
shorter than the real one lets a player choose a corner that looks clear and
is not. Two things make the real panel taller than its resting height: a
step with many items, and the alert bands. Placement shows both, and the
button hangs below them rather than inside the region being measured.

Offscreen Qt throughout, like the rest of the widget tests.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from PyQt6.QtWidgets import QApplication

import loom_overlay
from loom import overlay as overlay_module
from loom.overlay import MAX_ALERT_BANDS, Overlay, OverlayLayout

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QKeyEvent, QMouseEvent


def _left_press_at(panel, dx, dy):
    """A left-button press dx,dy into the panel, in global coordinates."""
    where = QPointF(panel.frameGeometry().topLeft() + QPoint(dx, dy))
    return QMouseEvent(QMouseEvent.Type.MouseButtonPress,
                       QPointF(dx, dy), where,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


def _move_to(x, y):
    """The pointer, now at this global position, still holding the button."""
    return QMouseEvent(QMouseEvent.Type.MouseMove,
                       QPointF(0, 0), QPointF(x, y),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout():
    """A fixed layout, so nothing here depends on the player's settings."""
    return OverlayLayout()


def test_the_normal_overlay_has_no_button(app, layout):
    """The button is placement chrome. On the real overlay it would be a
    widget inside a window the X server does not even consider input-
    receiving - invisible to the mouse, and pure confusion in a screenshot."""
    panel = Overlay(layout=layout)
    assert panel.place_button is None


def test_placement_mode_has_the_button(app, layout):
    panel = Overlay(placing=True, layout=layout)
    assert panel.place_button is not None
    assert panel.place_button.text() == "Set Overlay Position"


def test_the_button_sits_below_the_alert_bands(app, layout):
    """Not over them, and not over the card.

    The whole panel plus both bands is the footprint the player is placing.
    A button drawn inside that region would cover part of the thing being
    measured, and the player would place a panel they had not actually seen.
    """
    panel = Overlay(placing=True, layout=layout)
    bands_bottom = (panel.panel_height()
                    + MAX_ALERT_BANDS * (layout.band_gap + layout.band_height))

    assert panel.place_button.y() >= bands_bottom


def test_the_window_is_tall_enough_for_the_button(app, layout):
    """A button positioned past the bottom of its own window is invisible,
    and the failure looks exactly like a feature that was never built."""
    panel = Overlay(placing=True, layout=layout)
    button = panel.place_button

    assert button.y() + button.height() <= panel.height()


def test_placement_reserves_more_height_than_the_real_overlay(app, layout):
    """The button's room is additional, never borrowed from the bands."""
    plain = Overlay(layout=layout)
    placing = Overlay(placing=True, layout=layout)

    assert placing.chrome_height() > plain.chrome_height()
    assert (placing.chrome_height() - plain.chrome_height()
            == layout.place_button_gap + layout.place_button_height)


def test_the_button_follows_a_panel_that_grew_for_its_items(app, layout):
    """The one that a per-site resize would get wrong.

    A step with many items makes the card taller, which pushes the bands
    down, which pushes the button down. resizeEvent is what keeps all three
    in step; before it existed the button was laid out once at startup and
    a taller step drew straight through it.
    """
    panel = Overlay(placing=True, layout=layout)
    before = panel.place_button.y()

    panel.item_rows = [[("text", f"item {n}")] for n in range(8)]
    panel.item_states = []
    panel.fit_to_items()

    assert panel._extra > 0, "eight items should have grown the card"
    assert panel.place_button.y() > before
    assert (panel.place_button.y() + panel.place_button.height()
            <= panel.height())


def test_pressing_the_button_announces_rather_than_saving(app, layout):
    """The panel does not know what corner the offset is measured from, so
    it must not be the thing that writes it down - see
    loom_overlay.remember_position, which does know."""
    panel = Overlay(placing=True, layout=layout)
    heard = []
    panel.position_accepted.connect(lambda: heard.append(True))

    panel.place_button.click()

    assert heard == [True]


def test_a_live_scale_change_moves_the_button_with_the_panel(app, monkeypatch):
    """Appearance sliders reach placement mode live, so the button has to
    travel with the panel it belongs to - including on the path that swaps
    the whole layout out from under it."""
    monkeypatch.setattr(overlay_module.config, "overlay_scale", lambda: 1.0)
    monkeypatch.setattr(overlay_module.config, "text_scale", lambda: 1.0)
    panel = Overlay(placing=True)
    before = panel.place_button.y()

    monkeypatch.setattr(overlay_module.config, "overlay_scale", lambda: 1.5)
    assert panel.apply_appearance() is True

    assert panel.place_button.y() > before
    assert (panel.place_button.y() + panel.place_button.height()
            <= panel.height())


def test_the_real_overlay_ignores_a_mouse_press(app, layout):
    """The drag handler must never wake up on the panel that plays.

    It cannot normally be reached there - the X server does not route the
    pointer into a window with an empty input region - but "cannot be
    reached" is a property of the platform, not of this code, and the
    overlay is the one window in Loom that must never move itself.
    """
    panel = Overlay(layout=layout)
    before = panel.pos()

    panel.mousePressEvent(_left_press_at(panel, 40, 40))
    panel.mouseMoveEvent(_move_to(400, 400))

    assert panel._drag_from is None
    assert panel.pos() == before


def test_placement_moves_itself_on_a_drag(app, layout):
    """Frameless means the window manager will not move it, so the panel
    carries the pointer itself."""
    panel = Overlay(placing=True, layout=layout)
    panel.move(100, 100)

    panel.mousePressEvent(_left_press_at(panel, 30, 20))
    panel.mouseMoveEvent(_move_to(340, 260))

    # Pressed 30,20 into the card, so the corner keeps that vector.
    assert panel.pos().x() == 310
    assert panel.pos().y() == 240


def test_a_release_ends_the_drag(app, layout):
    """Otherwise the panel keeps following the pointer with no button down."""
    panel = Overlay(placing=True, layout=layout)
    panel.mousePressEvent(_left_press_at(panel, 10, 10))
    panel.mouseReleaseEvent(None)

    assert panel._drag_from is None

    settled = panel.pos()
    panel.mouseMoveEvent(_move_to(900, 900))
    assert panel.pos() == settled


def test_escape_abandons_placement(app, layout):
    """Cancelling has to stay as easy as committing. Frameless leaves no
    close button, so without this the only ways out are the launcher's Stop
    and saving a position the player may not want."""
    panel = Overlay(placing=True, layout=layout)
    panel.show()
    saved = []
    panel.position_accepted.connect(lambda: saved.append(True))

    panel.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress,
                                  Qt.Key.Key_Escape.value,
                                  Qt.KeyboardModifier.NoModifier))

    assert not panel.isVisible()
    assert saved == [], "escape must not save the position it abandons"


# ---- what placement chooses to show ------------------------------------


class FakeStep:
    def __init__(self, items, villagers, time):
        self.items_segments = [[("text", item)] for item in items]
        self.villager_count = villagers
        self.time = time


class FakeBuild:
    def __init__(self, steps):
        self.steps = steps


def test_the_busiest_step_is_the_one_placement_shows():
    """Space is the question, so the tallest step is the honest answer."""
    tallest = FakeStep(["a", "b", "c", "d", "e"], 14, 300)
    last = FakeStep(["a", "b"], 20, 480)
    build = FakeBuild([FakeStep(["a"], 6, 60), tallest, last])

    step, following, villagers, game_time = loom_overlay.busiest_step(build)

    assert step is tallest
    assert following is last
    assert (villagers, game_time) == (14, 300)


def test_placement_returns_the_step_rather_than_numbers_to_look_it_up_with():
    """The bug this signature exists to prevent.

    build.active_step answers "which step should be in progress NOW", so
    feeding it the busiest step's own count and time hands back the step
    AFTER it - by the time the HUD reads those numbers, that step's work is
    done. Placement measured the tallest step and drew a shorter one, and
    the footprint the player was invited to place around was never the one
    it had chosen.
    """
    tallest = FakeStep(["a", "b", "c", "d", "e"], 14, 300)
    later = FakeStep(["a"], 20, 480)
    build = FakeBuild([tallest, later])

    step, _following, villagers, game_time = loom_overlay.busiest_step(build)

    assert step is tallest
    assert len(step.items_segments) == 5
    # The numbers alone are genuinely ambiguous about which step they mean,
    # which is why the step travels with them instead of being re-derived.
    assert (villagers, game_time) == (tallest.villager_count, tallest.time)


def test_the_last_step_has_no_following_step():
    """A build whose busiest step is its last one still has to draw."""
    only = FakeStep(["a", "b"], 20, 480)

    step, following, _v, _t = loom_overlay.busiest_step(FakeBuild([only]))

    assert step is only
    assert following is None


def test_a_build_with_no_steps_does_not_crash_placement():
    """buildcheck would have refused this file, but placement is not the
    place to find that out - it opens a window, it does not judge builds."""
    step, following, villagers, game_time = loom_overlay.busiest_step(
        FakeBuild([]))

    assert step is None and following is None
    assert villagers > 0 and game_time > 0


def test_placement_alerts_fill_every_band():
    """The footprint is only honest if both bands are up. One alert would
    show the player a panel one band shorter than the game can draw."""
    shown = loom_overlay.placement_alerts()

    assert len(shown) == MAX_ALERT_BANDS


def test_placement_alerts_are_a_shape_the_panel_can_draw(app, layout):
    """Same (text, severity) pairs as alerts.production_alerts, because
    show_alerts is the one drawing them either way."""
    panel = Overlay(placing=True, layout=layout)
    panel.show_alerts(loom_overlay.placement_alerts())

    assert len(panel.alerts) == MAX_ALERT_BANDS
    for text, severity in panel.alerts:
        assert text
        assert severity in (overlay_module.alerts.FULL,
                            overlay_module.alerts.SOFT,
                            overlay_module.alerts.URGE)
