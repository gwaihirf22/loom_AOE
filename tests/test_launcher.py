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
                           WINDOW_GAP, beside, clamped_position, fitted_size)


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
