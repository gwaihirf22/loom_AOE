"""
Loom — a whole session with no build order behind it.

The panels are tested next door; this drives the real LiveController with
build=None against a fake HUD, because the thing worth proving is not that
the drawing works but that the READERS still do: the alerts fire, the
statistics get written, and nothing anywhere quietly invents a build.

The load-bearing test is the last one. A tracking session must never grow a
build section in its stats file, and the way that is guaranteed is that
there is no PaceTracker and no BuildReport at all - absent, not guarded. A
zero-step BuildOrder would have latched pace.complete on the first poll and
snapshotted a report for a build that never existed, which is exactly the
ASSUMED-dressed-as-OBSERVED failure the checklist rules exist to prevent,
arriving in the statistics instead of on the panel.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import loom_overlay
from loom import alerts, config, follow, overlay, paths, reader


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path / "stats")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeHud:
    """Hands out whatever reading the test wants next."""

    def __init__(self):
        self.villagers = 0
        self.clock = 0
        self.event = None
        self.queue = []
        self.population = None

    def poll(self):
        return reader.Reading(
            villagers=self.villagers, game_time=self.clock,
            event=self.event, hud_visible=True,
            population=self.population or (self.villagers, 200),
            queue_slots=self.queue, per_resource={"food": 6, "wood": 4},
            game_events=[], villager_gap=None)


@pytest.fixture
def tracking(app):
    """The bands-only session: no build, no steps, no cursor."""
    panel = overlay.Overlay(layout=overlay.OverlayLayout(),
                            panel_mode=overlay.BANDS_PANEL)
    hud = FakeHud()
    following = follow.FollowState(hold_seconds=10, step_count=0)
    controller = loom_overlay.LiveController(
        panel, None, hud, build_stem="tracking", follow_state=following)
    return controller, panel, hud, following


def run(controller, hud, polls, step=5):
    for n in range(polls):
        hud.villagers = min(4 + n // 4, 40)
        hud.clock = step * n
        controller.tick()
        hud.event = None


# ---- it runs at all -----------------------------------------------------


def test_a_build_free_session_polls_without_a_build(tracking):
    controller, panel, hud, _following = tracking
    run(controller, hud, 12)

    assert panel.have_reading
    assert controller.build is None


def test_nothing_build_shaped_was_ever_built(tracking):
    """Absent, not guarded. A PaceTracker over an empty build latches
    complete on its first poll; a Checklist over one has nothing to tick.
    Constructing neither is what makes the guarantee structural rather than
    a branch a later edit could drop."""
    controller, _panel, _hud, _following = tracking

    assert controller.pace is None
    assert controller.checklist is None


def test_the_reset_registry_holds_only_what_exists(tracking):
    """A subsystem left out of fresh_each_game is a silent carry-over into
    the next match; a None left IN it is a crash on the next game. The
    registry is filtered at construction so both stay impossible."""
    controller, _panel, _hud, _following = tracking

    assert controller.fresh_each_game
    assert all(part is not None for part in controller.fresh_each_game)
    assert controller.production in controller.fresh_each_game
    assert controller.ages in controller.fresh_each_game


# ---- the alerts, which are the whole point ------------------------------


def test_an_idle_town_centre_still_raises_a_band(tracking):
    """The reason someone would pick this mode at all. An empty queue past
    the debounce is idle production, and that band does not need a build to
    mean something."""
    controller, panel, hud, _following = tracking
    hud.queue = []
    run(controller, hud, 30)

    assert any("IDLE" in text for text, _severity in panel.alerts), (
        f"no idle band raised; got {panel.alerts}")


def test_housed_still_raises_a_band(tracking):
    """Judged from the population indicator, which has nothing to do with a
    build order."""
    controller, panel, hud, _following = tracking
    hud.population = (99, 100)
    run(controller, hud, 12)

    assert any("HOUSE" in text for text, _severity in panel.alerts), (
        f"no housing band raised; got {panel.alerts}")


def test_the_click_up_band_is_absent_rather_than_silent(tracking):
    """CLICK UP means "your build says click up NOW". With no build there is
    no now, so it cannot exist - and that is not a gap to be filled later."""
    controller, panel, hud, _following = tracking
    run(controller, hud, 30)

    assert not any("CLICK UP" in text for text, _severity in panel.alerts)


# ---- the hotkeys have nothing to move -----------------------------------


def test_the_step_keys_find_nothing_to_follow(tracking):
    """following_yet() is what start_hotkeys gates every step key behind.
    False here for the same reason PlacementSession says False: there are no
    steps, so a stray press must not move a cursor nobody can see."""
    controller, _panel, _hud, _following = tracking

    assert controller.following_yet() is False
    assert controller.reviewing() is False


# ---- the statistics -----------------------------------------------------


def test_a_stats_file_is_written_and_has_no_build_section(tracking):
    """"Tracking" is a promise about the statistics as much as the screen.

    The build section stays None because nothing can fill it: snapshot_build
    is the only writer and only the completion branch calls it, which needs a
    pace tracker that does not exist here.
    """
    controller, _panel, hud, _following = tracking
    run(controller, hud, 40)
    controller.finish()

    written = controller.recorder.to_dict()
    assert written["build"] is None
    assert written["meta"]["build"] == "tracking"
    assert written["meta"]["build_name"] == "No build order"


def test_the_pace_column_is_empty_rather_than_zero(tracking):
    """There is no plan to be ahead or behind of. Zero would be a claim
    about a build; None is the admission gamestats already understands."""
    controller, _panel, hud, _following = tracking
    run(controller, hud, 40)

    pace_column = controller.recorder.to_dict()["timeline"]["pace"]
    assert pace_column, "the timeline recorded nothing at all"
    assert all(value is None for value in pace_column)


def test_a_new_game_mints_a_fresh_tracking_recorder(tracking):
    """The lifecycle half of Hideable has to work without a BuildOrder -
    renew_recorder used to reach through self.build for its name, and so did
    the debug log's opening line.

    The RECORDER is what is asserted, not the filename. stats_path stamps to
    the nearest second, so two games inside one second share a path - true in
    build mode too, and a property of the clock rather than of this change.
    Asserting on it would be testing how fast the test ran.
    """
    controller, _panel, hud, _following = tracking
    run(controller, hud, 30)
    first = controller.recorder

    controller.start_fresh_game()
    run(controller, hud, 30)

    assert controller.recorder is not first, "the recorder was not renewed"
    assert controller.recorder.to_dict()["build"] is None
    assert controller.recorder.to_dict()["meta"]["build_name"] == (
        "No build order")
