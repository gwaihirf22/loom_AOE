"""
Loom — the overlay entry point, driven whole: completion, review, new game.

Everything else under tests/ works on pure functions, which is why the entry
point loom_overlay.py had near-zero coverage - and where three real bugs hid
in two days (a review the poll loop overwrote, a demo checklist that survived
its replay loop, a stats file per demo minute). These tests construct the real
controllers against a fake HUD and press the real hotkey path, offscreen, the
way test_passthrough_windows already builds a QApplication when it must.

The registry test at the bottom is the load-bearing one: the set of things
that forget a finished game is data now (fresh_each_game), and this is what
makes forgetting to register a new subsystem a test failure instead of a
silent carry-over into the next match.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

import loom_overlay
from loom import (age as age_reader, alerts, build_order, checklist,
                  config, follow, overlay, paths, reader, session)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # No test may touch the real settings or write into the player's stats.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path / "stats")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def build():
    """A shipped build with timed steps - DemoController needs at least one
    step time to know how long a replay loop is."""
    pairs, problems = build_order.available_builds()
    assert pairs, problems
    return next(build for _, build in pairs
                if any(step.time is not None for step in build.steps))


class FakeHud:
    """Hands out whatever reading the test wants next.

    `age` defaults to None - no crest reading - and that is not a neutral
    choice. build_order.extra_villagers has two rules and the second, the
    age ceiling, is disabled entirely without an age. So a test that leaves
    this None cannot see the villager surplus at all, and an assertion
    about it would pass for a reason unrelated to what it claims to check.
    Set it when the surplus is the subject.
    """

    def __init__(self):
        self.villagers = 0
        self.clock = 0
        self.event = None
        self.villager_gap = None
        self.age = None

    def poll(self):
        return reader.Reading(
            villagers=self.villagers, game_time=self.clock,
            event=self.event, hud_visible=True,
            age=(age_reader.AgeReading(age=self.age, score=1.0)
                 if self.age is not None else None),
            population=(self.villagers, 200), queue_slots=[],
            game_events=[], villager_gap=self.villager_gap)


def press(controller, follow_state, action):
    """Exactly what main()'s on_hotkey does, minus the Qt plumbing."""
    moment = time.monotonic()
    if action in ("next_step", "previous_step"):
        if controller.reviewing() and follow_state.auto:
            follow_state.toggle()
        if action == "next_step":
            follow_state.next_step(controller.auto_index(), moment)
        else:
            follow_state.previous_step(controller.auto_index(), moment)
    elif action == "toggle_follow":
        follow_state.toggle()
    controller.refresh()


def play_to_completion(controller, hud, build):
    """Enough villagers AND enough clock: active_step gates on the pair."""
    top = max(step.villager_count for step in build.steps)
    for n in range(0, top + 40):
        hud.villagers = min(n, top + 5)
        hud.clock = 30 * n
        controller.tick()
        hud.event = None
        if controller.pace.complete:
            return
    raise AssertionError("the build never completed")


@pytest.fixture
def live(app, build):
    panel = overlay.Overlay(layout=overlay.OverlayLayout())
    hud = FakeHud()
    following = follow.FollowState(hold_seconds=10,
                                   step_count=len(build.steps))
    controller = loom_overlay.LiveController(
        panel, build, hud, build_stem="e2e", follow_state=following)
    return controller, panel, hud, following


def test_completion_rests_on_the_last_card_not_the_report(live, build):
    """The author's ruling: the report must not steal the panel. The final
    card of a build is "settle into this age" - exactly what the player
    wants in front of them while the game goes on - so completion rests
    there, says BUILD DONE, and leaves the report one next-step press
    forward (the tail opens for it)."""
    controller, panel, hud, following = live
    play_to_completion(controller, hud, build)

    assert panel.report_rows is None, "the report stole the panel"
    assert following.tail == 1
    assert controller.auto_index() == len(build.steps) - 2
    assert panel.header_note.startswith("BUILD DONE")


def test_one_press_forward_shows_the_report(live, build):
    """What the BUILD DONE note promises. The first press also switches
    following off (reviewing), so the report must not time out - six more
    polls arrive and the panel must not move."""
    controller, panel, hud, following = live
    play_to_completion(controller, hud, build)

    press(controller, following, "next_step")

    assert panel.report_rows is not None, "the note promised the report"
    assert following.hold_until is None, "a review must not expire"

    for _ in range(6):
        hud.clock += 20
        controller.tick()

    assert panel.report_rows is not None, "a poll pulled the report away"


def test_both_ways_between_the_cards_and_the_report(live, build):
    controller, panel, hud, following = live
    play_to_completion(controller, hud, build)

    press(controller, following, "next_step")
    assert panel.report_rows is not None, "forward off the last card"

    press(controller, following, "previous_step")
    assert panel.report_rows is None, "back off the report"

    press(controller, following, "toggle_follow")
    assert panel.report_rows is None, "resuming rests on the last card"
    assert following.cursor is None
    assert panel.header_note.startswith("BUILD DONE")


def test_a_new_match_forgets_everything(live, build):
    """The other half of the review feature: the tail, the report and the
    cursor must all be gone, or the next game starts haunted."""
    controller, panel, hud, following = live
    play_to_completion(controller, hud, build)
    press(controller, following, "previous_step")

    hud.event = session.GAME_STARTED
    hud.villagers = 3
    hud.clock = 5
    controller.tick()

    assert not controller.pace.complete
    assert following.tail == 0
    assert following.cursor is None
    assert panel.report_rows is None


def test_the_fresh_each_game_registry_is_complete(live, build, app):
    """Every stateful subsystem with a reset() must be in the registry.

    This is the drift that already happened once: the demo gained a
    checklist in a merge and its hand-maintained reset list never learned,
    so replay loops started pre-ticked. The registry makes the list data;
    this test makes leaving it incomplete a failure.
    """
    controller = live[0]
    registered = set(map(id, controller.fresh_each_game))
    for name in ("pace", "production", "report", "follow",
                 "checklist", "ages", "houses"):
        assert id(getattr(controller, name)) in registered, name

    demo = loom_overlay.DemoController(overlay.Overlay(
        layout=overlay.OverlayLayout()), build, speed=200,
        build_stem="fast_castle")
    registered = set(map(id, demo.fresh_each_game))
    for name in ("pace", "follow", "checklist"):
        assert id(getattr(demo, name)) in registered, name


def test_the_demo_loop_resets_its_checklist_and_keeps_one_file(app, build):
    """The two demo bugs this audit found, pinned together: the second
    replay loop must start with the checklist forgotten, and however long
    the demo runs it leaves exactly one stats file.

    A THIRD one lived in how this test used to call the constructor. It
    omitted build_stem and so took a default of "demo" that main never
    takes - main passes --build's value, which defaults to "fast_castle" -
    so the _demo assertion below passed on a stem no real run produces, and
    every demo the author had ever run was sitting in the history looking
    like a game they played. The stem passed here is now a build name, as
    main passes, and the mark has to survive that.
    """
    demo = loom_overlay.DemoController(
        overlay.Overlay(layout=overlay.OverlayLayout()), build, speed=200,
        build_stem="fast_castle")
    first_file = demo.stats_file

    resets = []
    original = demo.checklist.reset
    demo.checklist.reset = lambda: (resets.append(True), original())[-1]

    crossed = 0
    for _ in range(400):
        before = demo.moment
        demo.tick()
        if demo.moment < before:
            crossed += 1
            if crossed == 2:
                break
    assert crossed == 2, "the demo never looped twice"

    assert resets, "the replay loop did not reset the checklist"
    assert demo.stats_file == first_file, "the demo minted a second file"
    written = list(paths.STATS_DIR.glob("*.json"))
    assert len(written) <= 1, written
    for path in written:
        assert path.stem.endswith("_demo")


# ---- losing sight of the game, and saying so ---------------------------

def test_lost_sight_is_a_band_not_a_takeover(live):
    """Regression twice over. TRACKING_LOST had no consumer: the filters
    hold their beliefs through the gap - by design - so the panel kept
    wearing a live face built from held numbers, a count frozen at 6 for a
    whole game, silent and trusted. The first fix replaced the whole panel
    with a waiting face, and the author overruled it the same evening: it
    took the held step and the hotkeys away exactly when they are still
    worth having. So: a SOFT band above a panel that keeps working, until
    the session itself says the game is back."""
    controller, panel, hud, following = live
    hud.villagers = 6
    hud.clock = 100
    controller.tick()
    assert panel.have_reading is True
    assert ("LOST SIGHT OF THE GAME", alerts.SOFT) not in panel.alerts

    hud.event = session.TRACKING_LOST
    controller.tick()
    assert panel.have_reading is True, "the panel must keep working"
    assert panel.alerts[0] == ("LOST SIGHT OF THE GAME", alerts.SOFT)

    # The next polls carry no event and is_usable() is true on the held
    # beliefs - exactly the trap. The band must persist.
    hud.event = None
    controller.tick()
    assert panel.alerts[0] == ("LOST SIGHT OF THE GAME", alerts.SOFT)

    hud.event = session.GAME_RESUMED
    hud.clock = 130
    controller.tick()
    assert ("LOST SIGHT OF THE GAME", alerts.SOFT) not in panel.alerts


def test_a_stale_villager_count_says_so_on_the_face(live):
    """The reader's villager_gap reaches the header note, and clears the
    moment the band reads again."""
    controller, panel, hud, following = live
    hud.villagers = 6
    hud.clock = 100
    controller.tick()
    assert panel.header_note == ""

    hud.clock = 124
    hud.villager_gap = 24
    controller.tick()
    assert panel.header_note == "VILLAGERS UNREAD · 24s"

    hud.clock = 128
    hud.villager_gap = None
    controller.tick()
    assert panel.header_note == ""


# ---- the BUILD DONE note when there is no key to name -------------------

@pytest.fixture
def has_hotkeys(monkeypatch):
    """A machine that CAN register hotkeys, whatever this one is.

    These tests are about the config logic, not about the platform, and
    the suite runs on three of them - a Linux runner with no python-xlib
    would otherwise turn "the switch is off" into "nothing would help"
    and fail for a reason that is not the one under test."""
    monkeypatch.setattr(loom_overlay.hotkeys, "available", lambda: True)


def test_a_bound_key_is_no_trouble(has_hotkeys):
    config.set_hotkeys_enabled(True)
    config.set_hotkey("next_step", "Ctrl+Shift+W")
    assert loom_overlay.step_hint() == "Ctrl+Shift+W"
    assert loom_overlay.step_hint_trouble() is None


def test_the_master_switch_being_off_is_the_reported_trouble(has_hotkeys):
    """The shipped default from 1.0.5, and so the state a new player's
    first finished build ends in."""
    config.set_hotkeys_enabled(False)
    config.set_hotkey("next_step", "Ctrl+Shift+W")
    assert loom_overlay.step_hint() is None
    assert loom_overlay.step_hint_trouble() == overlay.HOTKEYS_OFF


def test_an_emptied_step_key_is_a_different_trouble(has_hotkeys):
    """Switched on, but the one key that reaches the report was cleared.
    Telling this player to enable hotkeys would name a switch that is
    already on - the wrong remedy is worse than none."""
    config.set_hotkeys_enabled(True)
    config.set_hotkey("next_step", "")
    assert loom_overlay.step_hint() is None
    assert loom_overlay.step_hint_trouble() == overlay.HOTKEYS_UNBOUND


def test_a_machine_with_no_hotkeys_at_all_is_offered_nothing(monkeypatch):
    """No backend means no switch to throw and no key to bind. The note
    stays bare rather than promising a remedy that does not exist."""
    monkeypatch.setattr(loom_overlay.hotkeys, "available", lambda: False)
    config.set_hotkeys_enabled(False)
    assert loom_overlay.step_hint_trouble() is None
    config.set_hotkeys_enabled(True)
    assert loom_overlay.step_hint_trouble() is None


# ---- the build ends, and the counting ends with it ---------------------
#
# Live, twenty minutes after a build that finished at +4, the header read
# "+55 VILL · —" directly above a report row still correctly saying "+4
# beyond the build". extra_villagers is pure and knows nothing about a
# build ending, so its age-ceiling rule degenerates to "villagers minus the
# largest count anywhere in the build" and climbs for the rest of the game.
# What stops it is the controller taking the figure from report.max_extra
# once the build is complete - the same number the report row draws.


def test_the_villager_surplus_stops_climbing_when_the_build_ends(live, build):
    """The bug itself. The count kept rising for the whole rest of the game
    while the player went on playing, and nothing on the panel knew the
    build had ended - even though the dash beside it meant exactly that."""
    controller, panel, hud, _following = live
    hud.age = max(step.age for step in build.steps if step.age)
    play_to_completion(controller, hud, build)
    settled = panel.pace_text

    for _ in range(30):
        hud.villagers += 2
        hud.clock += 30
        controller.tick()

    assert panel.pace_text == settled, (
        f"the chip moved after the build ended: {settled!r} -> "
        f"{panel.pace_text!r}")


def test_the_chip_and_the_report_row_cannot_disagree(live, build):
    """Taken from report.max_extra rather than frozen separately, so there
    is one number rather than two that have to be kept equal."""
    controller, panel, hud, _following = live
    hud.age = max(step.age for step in build.steps if step.age)
    play_to_completion(controller, hud, build)

    for _ in range(20):
        hud.villagers += 3
        hud.clock += 30
        controller.tick()

    surplus = controller.report.max_extra
    if surplus > 0:
        assert panel.pace_text == f"+{surplus} VILLS > BUILD"
    else:
        assert panel.pace_text == ""


def test_the_report_page_does_not_inherit_a_stale_chip(live, build):
    """Both numbers were on screen together because show_report never wrote
    the chip - it drew whatever the last step-page poll left behind."""
    controller, panel, hud, following = live
    hud.age = max(step.age for step in build.steps if step.age)
    play_to_completion(controller, hud, build)
    for _ in range(10):
        hud.villagers += 3
        hud.clock += 30
        controller.tick()
    press(controller, following, "next_step")

    assert panel.report_rows is not None, "expected the report page"
    surplus = controller.report.max_extra
    expected = f"+{surplus} VILLS > BUILD" if surplus > 0 else ""
    assert panel.pace_text == expected


def test_completion_ticks_nothing_off_the_last_card(live, build):
    """The author's other half: consider the build complete internally, but
    do not mark anything done until Loom has actually seen it.

    checklist._assume_through clamps at len(steps) - 2 so the final card is
    never assumed past, however far the cursor runs. This is the guard at
    the CONTROLLER level - that the completion latch, and the freeze that
    now rides on it, still credit nobody. The fake HUD emits no events at
    all, so anything ticked here was invented.
    """
    controller, panel, hud, _following = live
    hud.age = max(step.age for step in build.steps if step.age)
    play_to_completion(controller, hud, build)

    for _ in range(20):
        hud.villagers += 1
        hud.clock += 30
        controller.tick()

    last = len(build.steps) - 1
    states = controller.checklist.states(last, len(build.steps[last].items))

    assert all(state is checklist.NOT_DONE for state in states), (
        f"completion credited work on the last card: {states}")
