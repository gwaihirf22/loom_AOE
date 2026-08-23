"""
Loom — live Appearance changes reaching a built overlay panel.

The panel reads its settings once at construction; apply_appearance() is the
live path the launcher's stdin request lands on. The assertion that earns
this file is the atomicity one: after a scale change the widget's own width
must equal the layout's panel_width, because the drawing code mixes the two
and a mismatch clips every frame - the trap that made live size risky.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import loom_overlay
from loom import config, overlay, paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path / "stats")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_transparency_changes_colours_and_nothing_else(app):
    panel = overlay.Overlay()
    size = (panel.width(), panel.height())
    designed_fill = panel._background.alpha()

    config.set_background_opacity(0.3)
    config.set_text_visibility(0.9)
    resized = panel.apply_appearance()

    assert resized is False, "a colour change must not resize the panel"
    assert panel._background.alpha() != designed_fill
    assert panel._content_contrast > 0, "above 0.5 must boost contrast"
    assert (panel.width(), panel.height()) == size


def test_scale_changes_layout_geometry_and_icons_together(app):
    """The atomicity trap: self.width() is set once by resize() while
    L.panel_height is recomputed every paint. Update one without the other
    and the next frame draws a new height inside an old width."""
    panel = overlay.Overlay()
    designed_width = panel.width()
    designed_icon = panel._layout.icon(overlay.ICON_HEIGHT)

    config.set_overlay_scale(1.5)
    resized = panel.apply_appearance()
    L = panel._layout

    assert resized is True
    assert panel.width() == L.panel_width, "widget and layout disagree"
    assert panel.height() == (L.panel_height + overlay.MAX_ALERT_BANDS
                              * (L.band_gap + L.band_height))
    assert panel.width() > designed_width
    assert L.icon(overlay.ICON_HEIGHT) > designed_icon, "icons not re-baked"


def test_text_scale_grows_height_and_never_width(app):
    panel = overlay.Overlay()
    wide, tall = panel.width(), panel.height()

    config.set_text_scale(1.5)
    panel.apply_appearance()

    assert panel.width() == wide, "text scale must never widen the panel"
    assert panel.height() > tall


def test_an_injected_layout_is_the_callers_not_ours(app):
    """Tests pass a layout precisely so they never depend on the settings
    file; a live reload that overwrote it would quietly reintroduce that
    dependency everywhere at once."""
    fixed = overlay.OverlayLayout(1.0, 1.0)
    panel = overlay.Overlay(layout=fixed)

    config.set_overlay_scale(2.0)
    panel.apply_appearance()

    assert panel._layout is fixed
    assert panel._layout.overlay_scale == 1.0


def test_growing_the_panel_keeps_it_on_the_games_right_edge(app):
    """The re-placement half. The default spot is right-aligned against the
    game window while move() sets the top-left, so a panel that grew without
    being placed again hung off the edge by exactly the growth - 224px at
    140% when measured."""
    game_x, game_y, game_width = 100, 50, 1920

    class Session(loom_overlay.Hideable):
        def __init__(self, panel):
            self.panel = panel

    panel = overlay.Overlay()
    session = Session(panel)
    loom_overlay.place_panel(panel, game_x, game_y, game_width, hud_scale=1.0)
    session.placed_against = (game_x, game_y, game_width, 1.0)
    right_gap = (game_x + game_width) - (panel.x() + panel.width())

    config.set_overlay_scale(1.4)
    session.apply_appearance()

    assert ((game_x + game_width) - (panel.x() + panel.width())
            == right_gap), "the panel drifted off the right edge"
