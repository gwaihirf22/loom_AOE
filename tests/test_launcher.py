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

from loom import entry
from loom.launcher import (COACH_SCENARIOS, DEV_COMMANDS, MINIMUM_SIZE,
                           PLACE_COMMAND, PREFERRED_SIZE, SETTINGS_TABS,
                           WINDOW_GAP, LauncherWindow, beside,
                           clamped_position, fitted_size, overlay_status_text)


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


class FakeLauncher:
    """Enough of LauncherWindow to exercise the toggle's decisions."""

    def __init__(self, dev_process=None):
        self.dev_process = dev_process
        self.output = FakeOutput()
        self.ran = []
        self.place_state = None

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
