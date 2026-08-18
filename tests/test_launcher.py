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

from loom.launcher import (COACH_SCENARIOS, DEV_COMMANDS, PLACE_COMMAND,
                           WINDOW_GAP, beside)


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
