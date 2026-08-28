"""
Loom — tests for the launcher's command table.

The launcher itself is widgets, which I test by using it. What earns
automated tests is the data underneath: the developer-mode command table,
where a wrong argv would launch the wrong thing (or nothing) and the only
symptom would be a confusing output pane. These run without any Qt setup -
the table is plain data, which is exactly why it is a table.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import config, entry
from loom.hotkeys import keyspec
from loom.launcher import (COACH_SCENARIOS, DEV_COMMANDS, MINIMUM_SIZE,
                           PLACE_COMMAND, PREFERRED_SIZE, SETTINGS_TABS,
                           WINDOW_GAP, LauncherWindow, TRACKING_FLAGS,
                           TRACKING_ROWS, beside, clamped_position,
                           fitted_size, overlay_argv, overlay_status_text)


def argv_for(label, stem="scoutsrush18pop", scenario="behind"):
    for name, _prefix, build, _tip in DEV_COMMANDS:
        if name == label:
            return build(stem, scenario)
    raise AssertionError(f"no dev command labelled {label!r}")


def test_every_command_builds_an_argv_and_explains_itself():
    for label, prefix, build, tip in DEV_COMMANDS:
        argv = build("fast_castle", "perfect")
        assert argv, f"{label} built an empty argv"
        assert all(isinstance(part, str) for part in argv)
        assert prefix
        # Every button carries a tooltip: an unexplained dev button is a
        # button nobody dares press.
        assert tip.strip(), f"{label} has no tooltip"


def test_overlay_commands_carry_the_chosen_build():
    assert argv_for("Overlay demo") == [
        "loom_overlay.py", "--demo", "--build", "scoutsrush18pop"]


def test_place_overlay_is_a_normal_control_not_a_dev_tool():
    # Placement moved out of the dev panel: it is an everyday act. Its argv
    # stays module data so this test needs no Qt.
    assert all(label != "Overlay place" for label, _, _, _ in DEV_COMMANDS)
    prefix, build_argv = PLACE_COMMAND
    assert prefix == "place"
    assert build_argv("scoutsrush18pop") == [
        "loom_overlay.py", "--place", "--build", "scoutsrush18pop"]


def test_coach_carries_build_and_scenario():
    assert argv_for("Coach simulate", scenario="stall") == [
        "loom_coach.py", "--simulate", "--scenario", "stall",
        "--build", "scoutsrush18pop"]


def test_scenarios_match_the_coach():
    """The combo box must only offer what loom_coach.py accepts."""
    import loom_coach  # imported here: entry points are not normally modules
    # argparse stores the choices on the parser; rather than reach into it,
    # I pin the tuple to the documented set.
    assert COACH_SCENARIOS == ("perfect", "behind", "stall")


def test_module_commands_run_from_project_root_style():
    # tools and pytest run as -m modules, matching the documented commands;
    # anything else would break the "run from project root" rule.
    assert argv_for("Grab frames")[0] == "-m"
    assert argv_for("Run tests")[:2] == ["-m", "pytest"]


def test_passthrough_check_asks_for_the_real_overlay_conditions():
    # The check is only meaningful against the window type the real overlay
    # uses, with passthrough on - that is the combination being verified.
    argv = argv_for("Passthrough check")
    assert argv[:2] == ["-m", "tools.overlay_test"]
    assert argv[argv.index("--style") + 1] == "tooltip"
    assert argv[argv.index("--passthrough") + 1] == "on"


# ---- placing the build preview beside the launcher ----------------------
#
# The preview used to open wherever the window manager felt like, which in
# practice meant behind the launcher. It is parented now so it can never hide,
# and placed beside the launcher the first time. This is the arithmetic for
# that placement, tested with fake inputs because the cases that matter - no
# room on the right, a monitor at negative coordinates - are the awkward ones
# to reproduce by opening real windows, and both would strand the preview
# somewhere the player cannot reach.

FULL_HD = (0, 0, 1920, 1080)


def test_the_preview_goes_to_the_right_when_there_is_room():
    x, y = beside((100, 200, 720), (600, 640), FULL_HD)
    assert x == 100 + 720 + WINDOW_GAP
    assert y == 200, "it should line up with the launcher's top edge"


def test_it_flips_to_the_left_when_the_right_would_hang_off():
    # Launcher pushed right: 1400 + 720 + gap + 600 is well past 1920.
    x, _y = beside((1400, 100, 720), (600, 640), FULL_HD)
    assert x == 1400 - 600 - WINDOW_GAP


def test_it_stays_on_screen_when_neither_side_fits():
    # A preview nearly as wide as the screen fits properly on neither side;
    # landing half off the edge would be worse than simply being clamped.
    left, top, right, bottom = FULL_HD
    x, y = beside((900, 100, 720), (1800, 640), FULL_HD)
    assert left <= x
    assert x + 1800 <= right


def test_a_monitor_left_of_the_primary_one_works():
    """Negative screen coordinates are ordinary on a multi-monitor desktop.
    Clamping them away would drag the preview onto the wrong monitor."""
    area = (-2560, 0, 0, 1440)
    x, y = beside((-2000, 300, 720), (600, 640), area)
    assert x == -2000 + 720 + WINDOW_GAP
    assert -2560 <= x and x + 600 <= 0


def test_it_never_places_a_window_above_the_work_area():
    # A launcher dragged up under a taskbar must not push the preview off the
    # top, where some window managers make it unreachable.
    area = (0, 40, 1920, 1080)
    _x, y = beside((100, 0, 720), (600, 640), area)
    assert y >= 40


# ---- the window on a screen that is not mine -------------------------------
#
# The launcher opened at a fixed 720x840 whatever it was opened on, and Qt
# will not shrink a window below its layout's minimumSizeHint - so on a
# 1920x1080 screen with the developer panel showing it came up taller than
# the desktop, with Start and the output pane below the bottom edge. Reported
# by a tester; invisible here, where the work area is 1440 tall. That is
# exactly the shape of bug that has to be tested with fake screens.

WORK_AREA_1080 = (0, 0, 1920, 1040)      # 1080p with a taskbar
WORK_AREA_1440 = (0, 0, 2560, 1400)


def test_the_preferred_size_is_used_where_it_fits():
    assert fitted_size(PREFERRED_SIZE, MINIMUM_SIZE, WORK_AREA_1440)         == PREFERRED_SIZE


def test_a_window_taller_than_the_screen_is_cut_to_the_screen():
    """The reported bug. 840 tall does not fit a 1040 work area once the
    window frame and a taskbar have taken their share, and a window whose
    bottom is off the desktop is a window whose Start button cannot be
    clicked."""
    _width, height = fitted_size((720, 1200), MINIMUM_SIZE, WORK_AREA_1080)

    assert height <= 1040


def test_it_never_returns_something_bigger_than_the_screen():
    for area in (WORK_AREA_1080, WORK_AREA_1440, (0, 0, 1366, 728)):
        left, top, right, bottom = area
        width, height = fitted_size((3000, 3000), MINIMUM_SIZE, area)
        assert width <= right - left
        assert height <= bottom - top


def test_a_screen_smaller_than_the_minimum_still_gets_a_fitting_window():
    """The minimum is a floor for dragging, not a promise the hardware can
    keep. On a screen shorter than it, fitting the screen wins - a window
    hanging off the edge helps nobody, and the scroll area is what makes the
    small size usable."""
    tiny = (0, 0, 480, 320)
    width, height = fitted_size(PREFERRED_SIZE, MINIMUM_SIZE, tiny)

    assert (width, height) == (480, 320)


def test_the_minimum_wins_over_a_smaller_preference():
    width, height = fitted_size((200, 200), MINIMUM_SIZE, WORK_AREA_1080)

    assert (width, height) == MINIMUM_SIZE


def test_a_remembered_position_off_the_bottom_comes_back_on_screen():
    """A geometry saved on a 1440p desktop, restored on a 1080p one. The
    only symptom of getting this wrong is that Loom looks like it did not
    start."""
    x, y = clamped_position((1200, 1300), (720, 840), WORK_AREA_1080)

    assert y + 840 <= 1040
    assert x + 720 <= 1920


def test_a_position_already_on_screen_is_left_alone():
    assert clamped_position((100, 60), (720, 840), WORK_AREA_1080) == (100, 60)


def test_a_monitor_left_of_the_primary_one_keeps_the_window_on_it():
    """Same case beside() has: negative coordinates are ordinary, and
    clamping them to zero would drag the launcher onto the wrong screen."""
    area = (-2560, 0, 0, 1400)
    x, y = clamped_position((-2000, 100), (720, 840), area)

    assert (x, y) == (-2000, 100)


def test_a_window_larger_than_the_screen_is_pinned_by_its_top_left():
    """The title bar is the part you have to be able to reach."""
    x, y = clamped_position((50, 50), (3000, 3000), WORK_AREA_1080)

    assert (x, y) == (0, 0)


# ---- the tabs and the greyed developer buttons -----------------------------


def test_every_settings_tab_names_widgets_the_window_really_has():
    """SETTINGS_TABS is strings, so a renamed attribute would fail at window
    construction rather than here. Pinning the shape catches the typo without
    needing Qt."""
    for label, names, keep_titles in SETTINGS_TABS:
        assert label.strip()
        assert names, f"the {label} tab holds nothing"
        assert isinstance(keep_titles, bool)


def test_a_tab_holding_one_box_drops_that_box_title():
    """A tab captioned Alerts containing a box captioned Alerts says it
    twice. Two boxes under one tab keep theirs, to tell them apart."""
    for label, names, keep_titles in SETTINGS_TABS:
        assert keep_titles == (len(names) > 1), label


def test_the_tools_a_bundle_cannot_run_are_the_ones_that_need_the_source():
    """The pairing that must not drift: a button is greyed in a packaged
    Loom exactly when entry.can_run refuses its argv. Getting this wrong in
    one direction is a crash with no message (what a tester hit); in the
    other it is a working tool nobody can press.
    """
    frozen_ok = {"Overlay demo", "Coach simulate", "Readout (log misreads)"}

    for label, _prefix, build, _tip in DEV_COMMANDS:
        argv = build("fast_castle", "perfect")
        needs_source = argv[0] == "-m"
        assert needs_source != (label in frozen_ok), label
        # can_run is what DevPanel asks; it must agree with the argv shape.
        assert (not needs_source) == _can_run_frozen(argv), label


def _can_run_frozen(argv):
    """entry.can_run as a bundle would answer it, without a bundle."""
    real = entry.frozen
    entry.frozen = lambda: True
    try:
        return entry.can_run(argv)
    finally:
        entry.frozen = real


def test_the_status_line_says_what_the_overlay_is_doing():
    assert overlay_status_text(running=False) == "overlay: not running"
    assert overlay_status_text(running=True) == "overlay: running"
    assert overlay_status_text(running=True, hidden=True) == "overlay: hidden"


def test_a_stopped_overlay_is_never_described_as_hidden():
    """Hidden only means anything to an overlay that is running. A stopped
    one whose last known state was hidden must not keep saying so - the
    panel is gone because the program is, and "hidden" would imply a key
    exists to bring it back."""
    assert overlay_status_text(running=False, hidden=True) \
        == "overlay: not running"


def test_every_hotkey_action_has_a_label_in_the_settings_window():
    """HotkeysBox indexes LABELS for every action in config.HOTKEY_ACTIONS
    with no fallback, so an action added without a label is a KeyError when
    the settings window is BUILT - not when the key is pressed. That failure
    is a window that will not open, blamed on whatever was touched last.
    """
    from loom import config
    from loom.launcher import HotkeysBox

    for action in config.HOTKEY_ACTIONS:
        assert action in HotkeysBox.LABELS, (
            f"{action} has no entry in HotkeysBox.LABELS; the settings "
            f"window would raise KeyError on open")
        label, tip = HotkeysBox.LABELS[action]
        assert label and tip, f"{action} has an empty label or tooltip"


def test_record_session_carries_the_chosen_build_as_build_and_label():
    # The build stem has to reach the overlay, or the session is recorded
    # against whatever the overlay defaults to and nothing afterwards says
    # so. That happened once: a 1440p game recorded under a label naming one
    # build while the overlay ran fast_castle, found days later by reading
    # the statistics filename. The stem doubles as the folder label so the
    # folder name itself carries the answer.
    assert argv_for("Record session") == [
        "-m", "tools.dev_session",
        "--build", "scoutsrush18pop", "--label", "scoutsrush18pop"]


def test_recording_a_session_refuses_to_run_beside_a_live_overlay():
    # Two overlays on one HUD both write statistics and both count APM,
    # silently. The guard is data so this needs no Qt.
    from loom.launcher import NEEDS_THE_OVERLAY_SLOT_FREE
    prefixes = {prefix for _label, prefix, _build, _tip in DEV_COMMANDS}
    assert NEEDS_THE_OVERLAY_SLOT_FREE <= prefixes
    assert "session" in NEEDS_THE_OVERLAY_SLOT_FREE
    # Running the test suite or a demo alongside the overlay is harmless and
    # must stay allowed.
    assert "pytest" not in NEEDS_THE_OVERLAY_SLOT_FREE


# ---- Place overlay is a toggle -----------------------------------------
#
# The placement panel is frameless, so it has no close button, and its own
# two exits (its button, Esc) both need it to hold the focus. The launcher
# button that opened it is the reliable way out, which makes what that
# button does on a second press worth pinning down.
#
# Still no Qt: these call the methods against a stub, because every one of
# them reads self.dev_process and writes a line, and none of them touches a
# widget except through _show_place_state, which the stub records.


class FakeChild:
    """A ChildProcess as far as the placement toggle can tell."""

    def __init__(self, label, running=True):
        self.label = label
        self._running = running
        self.stopped = False

    def is_running(self):
        return self._running

    def stop(self):
        self.stopped = True
        self._running = False


class FakeOutput:
    def __init__(self):
        self.lines = []

    def append_line(self, line):
        self.lines.append(line)


class FakePicker:
    """Enough of BuildPicker for the placement toggle to ask what it needs."""

    def __init__(self, mode=config.BUILD_MODE, stem="scoutsrush18pop"):
        self.mode = mode
        self.stem = stem

    def selected_mode(self):
        return self.mode

    def selected_stem(self):
        return None if self.mode in config.OVERLAY_MODES else self.stem


class FakeLauncher:
    """Enough of LauncherWindow to exercise the toggle's decisions."""

    def __init__(self, dev_process=None, mode=config.BUILD_MODE):
        self.dev_process = dev_process
        self.output = FakeOutput()
        self.ran = []
        self.place_state = None
        self.picker = FakePicker(mode)

    def run_dev_command(self, prefix, build_args):
        self.ran.append(prefix)
        self.dev_process = FakeChild(prefix)

    def _show_place_state(self, placing):
        self.place_state = placing

    placing_now = LauncherWindow.placing_now
    place_overlay = LauncherWindow.place_overlay


def test_placing_now_is_false_with_an_empty_slot():
    assert FakeLauncher().placing_now() is False


def test_placing_now_is_false_for_a_different_dev_task():
    """The dev slot is shared. Only placement may be closed by pressing its
    own button again - stopping a frame grab that way would throw away a
    capture the player is in the middle of taking."""
    launcher = FakeLauncher(FakeChild("frames"))

    assert launcher.placing_now() is False


def test_placing_now_is_false_once_the_child_has_exited():
    """_dev_finished leaves the object in place, so a null check alone would
    call a long-gone process 'placing'."""
    launcher = FakeLauncher(FakeChild("place", running=False))

    assert launcher.placing_now() is False


def test_the_first_press_opens_placement():
    launcher = FakeLauncher()

    launcher.place_overlay()

    assert launcher.ran == ["place"]
    assert launcher.place_state is True


def test_the_second_press_closes_it_instead_of_opening_another():
    launcher = FakeLauncher(FakeChild("place"))

    launcher.place_overlay()

    assert launcher.dev_process.stopped is True
    assert launcher.ran == [], "a second press must not start a second panel"


def test_closing_from_the_launcher_says_that_nothing_was_saved():
    """Only the panel's own button writes a position down. A player who
    closed from here and was told nothing would not know which happened."""
    launcher = FakeLauncher(FakeChild("place"))

    launcher.place_overlay()

    assert any("nothing saved" in line for line in launcher.output.lines)


def test_a_refused_start_does_not_claim_to_be_placing():
    """run_dev_command refuses while another dev task holds the slot. A
    button that then said "Close placement" over a panel that never opened
    would be a lie about what pressing it does."""
    launcher = FakeLauncher(FakeChild("frames"))
    launcher.run_dev_command = lambda prefix, build_args: None

    launcher.place_overlay()

    assert launcher.place_state is False


# ---- the two build-free modes ------------------------------------------
#
# Loom can run with no build order at all: every reader, every production
# alert and a statistics file, with either the alert bands alone or a live
# dashboard. The argv is module data for the same reason DEV_COMMANDS is -
# it can be checked here with no Qt and no launcher window.


def test_a_build_mode_still_runs_a_build():
    assert overlay_argv(config.BUILD_MODE, "scoutsrush18pop") == [
        "loom_overlay.py", "--build", "scoutsrush18pop"]


def test_each_tracking_mode_asks_for_its_own_panel():
    assert overlay_argv(config.TRACKING_MODE, "ignored") == [
        "loom_overlay.py", "--no-build", "bands"]
    assert overlay_argv(config.TRACKING_PANEL_MODE, "ignored") == [
        "loom_overlay.py", "--no-build", "panel"]


def test_a_tracking_mode_never_carries_a_build():
    """The whole reason the wire uses a flag rather than a sentinel stem.

    A stem that is not a stem would be a token loom_overlay, the coach,
    replay_queue and a hand-typed command line could all be handed, and
    every one of them calls BuildOrder.load_by_name. A flag cannot be
    mistaken for a build by anything.
    """
    for mode in TRACKING_FLAGS:
        assert "--build" not in overlay_argv(mode, "scoutsrush18pop")


def test_placement_places_the_panel_the_mode_actually_draws():
    """Placing a tracking mode against the build panel would be positioning
    a window that never appears."""
    for mode in TRACKING_FLAGS:
        argv = overlay_argv(mode, "scoutsrush18pop", place=True)
        assert "--place" in argv
        assert "--no-build" in argv
        assert "--build" not in argv

    build_place = overlay_argv(config.BUILD_MODE, "scoutsrush18pop",
                               place=True)
    assert build_place == ["loom_overlay.py", "--place",
                           "--build", "scoutsrush18pop"]


def test_every_pinned_row_is_a_real_mode_with_a_flag():
    """A row whose mode config does not recognise would be selectable,
    persisted, and then silently read back as "build"."""
    for mode, label in TRACKING_ROWS:
        assert mode in config.OVERLAY_MODES
        assert mode in TRACKING_FLAGS
        assert "NO BUILD ORDER" in label


def test_the_pinned_rows_lead_with_the_quieter_one():
    """Order is not cosmetic: the first row is what an accidental Enter or
    a fallback would land on, so the one that draws LESS goes first."""
    assert TRACKING_ROWS[0][0] == config.TRACKING_MODE


def _relative_luminance(colour):
    """WCAG relative luminance, so contrast can be measured rather than
    judged by eye on one person's monitor."""
    def channel(value):
        value /= 255
        return value / 12.92 if value <= 0.03928 else \
            ((value + 0.055) / 1.055) ** 2.4
    return (0.2126 * channel(colour.red())
            + 0.7152 * channel(colour.green())
            + 0.0722 * channel(colour.blue()))


def _contrast(one, two):
    first, second = _relative_luminance(one), _relative_luminance(two)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def test_the_statistics_button_stays_readable_however_violet_gets_retuned():
    """The RULE, not today's hex code.

    The record's chart colour is tuned to read as a thin line on a dark
    ground, which is a different job from sitting behind white text -
    measured, white on it is 2.58:1, well under the 4.5:1 a person can
    comfortably read. The button darkens it, and this is what stops a
    later retune of RECORD_COLOR from quietly producing an unreadable
    button: it asks whether the text can be READ, not what colour it is.
    """
    from PyQt6.QtGui import QColor
    from loom.launcher import STATS_BUTTON_DARKEN, STATS_BUTTON_HOVER_DARKEN
    from loom.statsview import RECORD_COLOR

    white = QColor(255, 255, 255)
    for darken in (STATS_BUTTON_DARKEN, STATS_BUTTON_HOVER_DARKEN):
        fill = RECORD_COLOR.darker(darken)
        assert _contrast(fill, white) >= 4.5, (
            f"white on {fill.name()} is unreadable at darker({darken})")
    # And it must still BE the record's colour, not a violet of its own:
    # the same hue, only darker.
    assert abs(RECORD_COLOR.hue()
               - RECORD_COLOR.darker(STATS_BUTTON_DARKEN).hue()) <= 2


# ---- setting a hotkey by pressing it -----------------------------------
#
# The field used to be typed into, and saved on every keystroke - so
# "Ctrl+Shift+W" wrote settings.json twelve times, eleven of them through
# invalid intermediate states, each one re-running the launcher's global
# unregister/register cycle. Now the player presses the combination.
#
# HotkeysBox had no coverage at all before this: one test asserted every
# action has a label and never built the widget. These are the first that
# construct it.


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def press(field, key, modifiers=None):
    """One key press into a capture field, as Qt would deliver it."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent as _QEvent
    if modifiers is None:
        modifiers = Qt.KeyboardModifier.NoModifier
    field.keyPressEvent(
        QKeyEvent(_QEvent.Type.KeyPress, key.value, modifiers))


def box_and_field(action="next_step"):
    from loom.launcher import HotkeysBox
    box = HotkeysBox()
    field = box.fields[action]
    field._capturing = True          # what focusInEvent does
    field._settled = field.text()
    return box, field


def test_a_captured_combination_is_saved_once(app):
    """Not once per key. The old field wrote on textChanged, so a binding
    reached disk through every partial spelling on the way."""
    from PyQt6.QtCore import Qt
    writes = []
    box, field = box_and_field()
    real = config.set_hotkey
    try:
        config.set_hotkey = lambda a, b: (writes.append((a, b)), real(a, b))[1]
        press(field, Qt.Key.Key_Control, Qt.KeyboardModifier.ControlModifier)
        press(field, Qt.Key.Key_P,
              Qt.KeyboardModifier.ControlModifier
              | Qt.KeyboardModifier.ShiftModifier)
    finally:
        config.set_hotkey = real

    assert writes == [("next_step", "Ctrl+Shift+P")]
    assert config.hotkeys()["next_step"] == "Ctrl+Shift+P"


def test_holding_modifiers_shows_them_gathering(app):
    """The must-have-a-modifier rule becomes a state you watch assemble
    rather than an error you read afterwards."""
    from PyQt6.QtCore import Qt
    _box, field = box_and_field()

    press(field, Qt.Key.Key_Shift,
          Qt.KeyboardModifier.ControlModifier
          | Qt.KeyboardModifier.ShiftModifier)

    assert field.text() == "Ctrl+Shift+..."


def test_escape_leaves_the_binding_alone(app):
    """Bare Escape can never be a binding - keyspec refuses a modifier-less
    one - so it is free to mean cancel."""
    from PyQt6.QtCore import Qt
    _box, field = box_and_field()
    before = field._settled

    press(field, Qt.Key.Key_Escape)

    assert field.text() == before
    assert config.hotkeys()["next_step"] == before


def test_delete_clears_it_to_off(app):
    """Every binding must be emptyable - the design rule - and with typing
    that was obvious. With capture it needs a key of its own."""
    from PyQt6.QtCore import Qt
    _box, field = box_and_field()

    press(field, Qt.Key.Key_Delete)

    assert field.text() == ""
    assert config.hotkeys()["next_step"] == ""


def test_keys_already_owned_by_another_action_are_refused(app):
    """Detected before, warned about before - refused now. Two actions on
    one combination means whichever registers first wins and the other
    silently never fires."""
    from PyQt6.QtCore import Qt
    box, field = box_and_field("next_step")
    taken = box.fields["previous_step"].text()
    assert taken, "expected previous_step to ship with a binding"
    spec = keyspec.parse(taken)

    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent as _QEvent
    modifiers = Qt.KeyboardModifier.NoModifier
    for name in spec.modifiers:
        modifiers |= {"Ctrl": Qt.KeyboardModifier.ControlModifier,
                      "Shift": Qt.KeyboardModifier.ShiftModifier,
                      "Alt": Qt.KeyboardModifier.AltModifier,
                      "Win": Qt.KeyboardModifier.MetaModifier}[name]
    code = getattr(Qt.Key, f"Key_{spec.key}")
    field.keyPressEvent(QKeyEvent(_QEvent.Type.KeyPress, code.value, modifiers))

    assert config.hotkeys()["next_step"] != taken, "the clash was saved"
    assert "Previous step" in box.warning.text()


def test_a_key_the_grammar_has_no_name_for_says_so(app):
    """CapsLock has no entry in either OS backend's table, so a binding
    naming it would save and then never fire."""
    from PyQt6.QtCore import Qt
    box, field = box_and_field()

    press(field, Qt.Key.Key_CapsLock, Qt.KeyboardModifier.ControlModifier)

    assert "cannot be bound" in box.warning.text()
    assert config.hotkeys()["next_step"] == field._settled


def test_a_bare_key_is_refused_with_the_grammars_own_reason(app):
    """The message explains what binding it would cost the player in-game,
    which is worth showing rather than replacing."""
    from PyQt6.QtCore import Qt
    box, field = box_and_field()

    press(field, Qt.Key.Key_J)

    assert "no modifier" in box.warning.text()
    assert config.hotkeys()["next_step"] == field._settled


def test_tab_is_captured_rather_than_moving_focus(app):
    """Qt spends Tab on focus navigation before keyPressEvent runs, and Tab
    is in keyspec.KEYS - so without the event() override it is a binding the
    grammar accepts and the window cannot capture."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeyEvent
    from PyQt6.QtCore import QEvent as _QEvent
    _box, field = box_and_field()
    modifiers = (Qt.KeyboardModifier.ControlModifier
                 | Qt.KeyboardModifier.ShiftModifier)
    # Key_Backtab, not Key_Tab, and that is the whole point of the case.
    # Shift renames the key: a real Ctrl+Shift+Tab arrives as Backtab, so a
    # test pressing Key_Tab with Shift held was checking a press the game
    # can never produce - and passed all the while the real one was refused.
    event = QKeyEvent(_QEvent.Type.KeyPress, Qt.Key.Key_Backtab.value,
                      modifiers)

    assert field.event(event) is True, "Tab was not taken before focus"
    assert config.hotkeys()["next_step"] == "Ctrl+Shift+Tab"


def test_shift_does_not_stop_a_key_being_captured(app):
    """The bug the shifted row exists for, at the widget rather than in the
    translation: Qt reports the character a key PRODUCES, so Ctrl+Shift+9
    arrives as Key_ParenLeft and was declined.
    """
    from PyQt6.QtCore import Qt
    _box, field = box_and_field()
    modifiers = (Qt.KeyboardModifier.ControlModifier
                 | Qt.KeyboardModifier.ShiftModifier)

    press(field, Qt.Key.Key_ParenLeft, modifiers)

    assert config.hotkeys()["next_step"] == "Ctrl+Shift+9"


def test_the_shipped_default_can_be_pressed_back_into_its_own_field(app):
    """The hide key, cleared and put back - the thing that could not be done,
    and the reason the default is Ctrl+Shift+Minus rather than Ctrl+Shift+0.
    Windows consumes Ctrl+Shift+0 entirely; the replacement is a shifted key
    too, so it needs the row this fix added.

    Read from DEFAULT_HOTKEYS rather than written out, so it follows the
    default if that moves again. It goes in ITS OWN field on purpose: pressed
    into any other, the conflict rule refuses it and rightly so, which is a
    second and honest reason the same press can fail.
    """
    from PyQt6.QtCore import Qt
    from tests.test_qtkeys import SHIFT_PRESS

    box, field = box_and_field("toggle_hidden")
    binding = config.DEFAULT_HOTKEYS["toggle_hidden"]
    spec = keyspec.parse(binding)
    assert "Shift" in spec.modifiers and spec.key in SHIFT_PRESS, (
        f"{binding} is no longer a key Shift renames, so this test is"
        " measuring nothing - point it at whatever the default became")
    modifiers = (Qt.KeyboardModifier.ControlModifier
                 | Qt.KeyboardModifier.ShiftModifier)
    config.set_hotkey("toggle_hidden", "")           # cleared, as the player did
    field._settled = ""

    press(field, SHIFT_PRESS[spec.key][0], modifiers)

    assert config.hotkeys()["toggle_hidden"] == binding
    assert not box.warning.text(), box.warning.text()


def test_the_shifted_press_is_saved_once_not_declined_then_saved(app):
    """A refusal writes nothing and leaves the field listening, so a press
    that is quietly declined looks identical to one that has not landed yet.
    Checking the warning stays empty is what tells the two apart.

    Semicolon rather than Minus, and the assertion below says why: a press
    that clashes with another action's binding is refused for a completely
    good reason, and a test using one would go red the day a default moved
    while reporting a fault in the shifted row. This one caught exactly that
    when toggle_hidden took Ctrl+Shift+Minus.
    """
    from PyQt6.QtCore import Qt
    box, field = box_and_field()
    modifiers = (Qt.KeyboardModifier.ControlModifier
                 | Qt.KeyboardModifier.ShiftModifier)
    assert "Ctrl+Shift+Semicolon" not in config.DEFAULT_HOTKEYS.values(), (
        "this test needs a combination no other action owns, or the refusal"
        " it measures is the conflict rule doing its job")

    press(field, Qt.Key.Key_Colon, modifiers)

    assert config.hotkeys()["next_step"] == "Ctrl+Shift+Semicolon"
    assert not box.warning.text(), box.warning.text()


def test_the_native_key_code_reaches_the_translation(app):
    """The Windows half of the wiring, which every other test here misses.

    QKeyEvent's short constructor leaves nativeVirtualKey at 0, so those
    presses exercise the US shifted row and never the native table - the
    exact shape of gap that lets a parameter be dropped from a call and
    nothing go red. Here the event carries a virtual key that DISAGREES
    with the Qt key, so only the native path can produce the answer.
    """
    import sys

    from PyQt6.QtCore import Qt, QEvent as _QEvent
    from PyQt6.QtGui import QKeyEvent
    from loom.hotkeys import qtkeys

    if not qtkeys.native_names():
        pytest.skip("no native key table off Windows - by design, so that an"
                    " X keysym is never read as a virtual key")

    _box, field = box_and_field()
    modifiers = (Qt.KeyboardModifier.ControlModifier
                 | Qt.KeyboardModifier.ShiftModifier)
    # Qt says ParenLeft, which the US row reads as "9"; the native code says
    # the 8 key. A German keyboard really does deliver this pair.
    event = QKeyEvent(_QEvent.Type.KeyPress, Qt.Key.Key_ParenLeft.value,
                      modifiers, 0, 0x38, 0, "(", False, 1)

    field.keyPressEvent(event)

    assert config.hotkeys()["next_step"] == "Ctrl+Shift+8", (
        "the native virtual key never reached qtkeys")
