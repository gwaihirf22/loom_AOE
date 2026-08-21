"""
Loom — tests for the saved settings.

The launcher writes these settings and the entry points read them, so what
matters here is the contract between the two: values round-trip, and anything
missing or mangled falls back to a safe default rather than an error. The
most important default of all: a player who never touched the alert settings
gets every warning.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import config

ALL_ON = {"idle_tc": True, "housed": True, "house_warning": True}


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    # Every test gets its own config file, so none of them can touch the
    # real config.json sitting in the project root.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")


def test_alert_toggles_default_all_on_without_a_file():
    assert config.alert_toggles() == ALL_ON


def test_alert_toggle_roundtrip():
    config.set_alert_toggle("idle_tc", False)
    assert config.alert_toggles() == {**ALL_ON, "idle_tc": False}
    config.set_alert_toggle("idle_tc", True)
    assert config.alert_toggles() == ALL_ON


def test_one_toggle_does_not_disturb_another():
    config.set_alert_toggle("housed", False)
    config.set_alert_toggle("house_warning", False)
    assert config.alert_toggles() == {
        "idle_tc": True, "housed": False, "house_warning": False}


def test_garbage_alert_toggles_fall_back_to_on():
    # A corrupt file must not silently switch protection off.
    config.save({"alerts_enabled": "yes please"})
    assert config.alert_toggles() == ALL_ON


def test_unknown_toggle_name_is_rejected():
    # A typo in the launcher should blow up in development, not write a key
    # nobody will ever read back.
    with pytest.raises(ValueError):
        config.set_alert_toggle("housde", False)


def test_developer_mode_defaults_off_and_roundtrips():
    assert config.developer_mode() is False
    config.set_developer_mode(True)
    assert config.developer_mode() is True
    config.set_developer_mode(False)
    assert config.developer_mode() is False


def test_garbage_developer_mode_means_off():
    config.save({"developer_mode": "totally"})
    assert config.developer_mode() is False


def test_build_browser_defaults_on():
    # Unlike developer mode, the preview is for everyone - so its default
    # is on, and only a deliberate false turns it off.
    assert config.build_browser() is True


def test_build_browser_roundtrip():
    config.set_build_browser(False)
    assert config.build_browser() is False
    config.set_build_browser(True)
    assert config.build_browser() is True


def test_garbage_build_browser_means_on():
    config.save({"show_build_browser": "nah"})
    assert config.build_browser() is True


@pytest.mark.parametrize("getter,setter,key,top", [
    (config.overlay_scale, config.set_overlay_scale, "overlay_scale", 2.5),
    (config.text_scale, config.set_text_scale, "text_scale", 2.0),
])
def test_scale_defaults_roundtrips_and_clamps(getter, setter, key, top):
    # Default: the overlay at its designed size.
    assert getter() == 1.0
    # Round trip.
    setter(1.25)
    assert getter() == 1.25
    # Out of range is clamped, not refused.
    setter(9.0)
    assert getter() == top
    setter(0.1)
    assert getter() == 0.75


@pytest.mark.parametrize("garbage", ["big", [1.5], None, True])
def test_garbage_scale_means_designed_size(garbage):
    # True is the trap: bool IS an int in Python, and float(True) is a
    # plausible 1.0 that would mask a mangled file.
    config.save({"overlay_scale": garbage, "text_scale": garbage})
    assert config.overlay_scale() == 1.0
    assert config.text_scale() == 1.0


def test_house_headroom_defaults_roundtrips_and_clamps():
    from loom import alerts
    assert config.house_headroom() == alerts.HOUSE_WARNING_HEADROOM == 4
    config.set_house_headroom(8)
    assert config.house_headroom() == 8
    config.set_house_headroom(0)     # zero would be the housed alert itself
    assert config.house_headroom() == 1
    config.set_house_headroom(99)
    assert config.house_headroom() == 20


def test_garbage_house_headroom_means_default():
    config.save({"house_headroom": "lots"})
    assert config.house_headroom() == 4
    config.save({"house_headroom": True})   # the bool-is-an-int trap again
    assert config.house_headroom() == 4


def test_browser_window_defaults_to_none():
    # None means "never resized" - the preview picks its own default then.
    assert config.browser_window() is None


def test_browser_window_roundtrip():
    config.set_browser_window(820, 900)
    assert config.browser_window() == (820, 900)


def test_garbage_browser_window_means_none():
    config.save({"browser_window": "big please"})
    assert config.browser_window() is None


def test_launcher_window_defaults_to_none():
    # None means "never resized" - the launcher opens at its preferred size
    # shrunk to whatever screen it lands on.
    assert config.launcher_window() is None


def test_launcher_window_roundtrip():
    config.set_launcher_window(900, 700)
    assert config.launcher_window() == (900, 700)


def test_garbage_launcher_window_means_none():
    """A mangled settings file must cost a player their remembered size, not
    the ability to open the launcher at all."""
    config.save({"launcher_window": [None]})
    assert config.launcher_window() is None


def test_launcher_position_roundtrip_including_a_negative_one():
    # Same as the preview: a monitor left of the primary one has negative x,
    # and clamping it here would move the launcher every launch. Fitting it
    # to the screen is launcher.clamped_position's job, not this one's.
    config.set_launcher_position(-1920, 300)
    assert config.launcher_position() == (-1920, 300)


def test_launcher_position_defaults_to_none():
    """None means "never placed", which leaves the placing to the window
    manager - right on a first run, and the only option under Wayland."""
    assert config.launcher_position() is None


def test_preview_alerts_defaults_off_and_roundtrips():
    """Off by default: the preview has been a quiet browser its whole life,
    and a flashing band nobody asked for is a poor surprise."""
    assert config.preview_alerts() is False
    config.set_preview_alerts(True)
    assert config.preview_alerts() is True


def test_overlay_disabled_defaults_off_and_roundtrips():
    """A PREFERENCE about how the overlay starts, which is deliberately not
    the same thing as whether it is hidden right now - the Hide button and
    the hotkey stay a this-session toggle and are never written here."""
    assert config.overlay_disabled() is False
    config.set_overlay_disabled(True)
    assert config.overlay_disabled() is True


@pytest.mark.parametrize("getter", [config.preview_alerts,
                                    config.overlay_disabled])
def test_garbage_leaves_both_switches_off(getter):
    """Default-off polarity, like developer_mode: anything but a deliberate
    True means off. A mangled settings file must not hide somebody's overlay."""
    config.save({"preview_alerts": "yes please", "overlay_disabled": 1})
    assert getter() is False


def test_browser_position_defaults_to_none():
    # None means "never placed", which is the launcher's cue to put the
    # preview beside itself instead of letting the window manager drop it
    # somewhere - historically behind the launcher, where it was easy to miss.
    assert config.browser_position() is None


def test_browser_position_roundtrip():
    config.set_browser_position(1400, 220)
    assert config.browser_position() == (1400, 220)


def test_a_negative_position_survives():
    # Perfectly ordinary on a multi-monitor desktop: a screen left of the
    # primary one has negative x. Clamping this to zero would drag the
    # preview onto the wrong monitor every launch.
    config.set_browser_position(-1920, 300)
    assert config.browser_position() == (-1920, 300)


def test_garbage_browser_position_means_none():
    config.save({"browser_position": {"x": 10}})
    assert config.browser_position() is None


def test_active_build_defaults_to_fast_castle():
    # The same default as the entry points' --build flag, so the launcher
    # and the command line agree on what "just run it" means.
    assert config.active_build() == "fast_castle"


def test_active_build_roundtrip():
    config.set_active_build("scoutsrush18pop")
    assert config.active_build() == "scoutsrush18pop"


def test_garbage_active_build_falls_back():
    config.save({"active_build": ["not", "a", "name"]})
    assert config.active_build() == "fast_castle"


def test_settings_coexist_in_one_file():
    # The settings all share config.json; writing one must not eat the rest.
    config.set_idle_tc_limits(80, 110)
    config.set_alert_toggle("housed", False)
    config.set_developer_mode(True)
    config.set_build_browser(False)
    config.set_active_build("archers19pop")
    config.set_overlay_scale(1.5)
    config.set_text_scale(1.25)
    config.set_browser_window(820, 900)
    config.set_house_headroom(6)
    config.set_hotkey("next_step", "Ctrl+Alt+N")
    config.set_hotkeys_enabled(False)
    config.set_manual_hold_seconds(20)
    config.set_background_opacity(0.4)
    config.set_text_visibility(0.6)
    assert config.idle_tc_limits() == (80, 110)
    assert config.alert_toggles()["housed"] is False
    assert config.developer_mode() is True
    assert config.build_browser() is False
    assert config.active_build() == "archers19pop"
    assert config.overlay_scale() == 1.5
    assert config.text_scale() == 1.25
    assert config.browser_window() == (820, 900)
    assert config.house_headroom() == 6
    assert config.hotkeys()["next_step"] == "Ctrl+Alt+N"
    assert config.hotkeys_enabled() is False
    assert config.manual_hold_seconds() == 20
    assert config.background_opacity() == 0.4
    assert config.text_visibility() == 0.6


# ---- hotkeys ---------------------------------------------------------------
#
# The dict setting follows alert_toggles: a complete dict out however
# incomplete the file, one entry written at a time, and an unknown action is
# a programming error rather than a key nobody reads back.

def test_hotkeys_default_to_the_shipped_bindings():
    assert config.hotkeys() == config.DEFAULT_HOTKEYS


def test_every_action_is_always_present():
    """Callers must never have to handle a missing action, however little is
    in the file."""
    config.save({"hotkeys": {"next_step": "Ctrl+Alt+N"}})

    bindings = config.hotkeys()

    assert set(bindings) == set(config.HOTKEY_ACTIONS)
    assert bindings["next_step"] == "Ctrl+Alt+N"
    assert bindings["previous_step"] == config.DEFAULT_HOTKEYS["previous_step"]


def test_one_binding_round_trips():
    config.set_hotkey("toggle_follow", "Ctrl+Alt+F12")
    assert config.hotkeys()["toggle_follow"] == "Ctrl+Alt+F12"


def test_rebinding_one_action_leaves_the_others_alone():
    config.set_hotkey("next_step", "Ctrl+Alt+N")
    config.set_hotkey("previous_step", "Ctrl+Alt+P")

    assert config.hotkeys()["next_step"] == "Ctrl+Alt+N"
    assert config.hotkeys()["previous_step"] == "Ctrl+Alt+P"


def test_an_empty_binding_stays_empty():
    """Switching an action off is a decision. Quietly restoring the default
    would re-take a combination from the game that the player had
    deliberately handed back."""
    config.set_hotkey("next_step", "")

    assert config.hotkeys()["next_step"] == ""


def test_an_unknown_action_is_rejected():
    """A typo in the launcher should blow up in development, not write a key
    nobody will ever read back."""
    with pytest.raises(ValueError):
        config.set_hotkey("advance_teh_step", "Ctrl+Shift+W")


@pytest.mark.parametrize("junk", ["ctrl+everything", 42, ["Ctrl", "W"], None])
def test_garbage_hotkeys_fall_back(junk):
    config.save({"hotkeys": junk})
    assert config.hotkeys() == config.DEFAULT_HOTKEYS


def test_a_non_string_binding_falls_back_to_its_default():
    config.save({"hotkeys": {"next_step": 17}})
    assert config.hotkeys()["next_step"] == config.DEFAULT_HOTKEYS["next_step"]


def test_hotkeys_are_enabled_by_default():
    assert config.hotkeys_enabled() is True


def test_the_master_switch_round_trips():
    config.set_hotkeys_enabled(False)
    assert config.hotkeys_enabled() is False


def test_garbage_leaves_hotkeys_enabled():
    config.save({"hotkeys_enabled": "no thanks"})
    assert config.hotkeys_enabled() is True


def test_the_hold_defaults_to_ten_seconds():
    assert config.manual_hold_seconds() == 10


def test_the_hold_round_trips():
    config.set_manual_hold_seconds(25)
    assert config.manual_hold_seconds() == 25


@pytest.mark.parametrize("written, expected", [
    (0, config.MANUAL_HOLD_BOUNDS[0]),
    (1, config.MANUAL_HOLD_BOUNDS[0]),
    (999, config.MANUAL_HOLD_BOUNDS[1]),
])
def test_the_hold_is_clamped(written, expected):
    """A hold of zero would expire before the step could be read; a hold of
    minutes is the toggle wearing a disguise."""
    config.save({"manual_hold_seconds": written})
    assert config.manual_hold_seconds() == expected


@pytest.mark.parametrize("junk", ["ten", None, [10], True])
def test_garbage_hold_falls_back(junk):
    # True is the trap: it IS an int in Python, and would clamp to 3.
    config.save({"manual_hold_seconds": junk})
    assert config.manual_hold_seconds() == 10


# ---- transparency -----------------------------------------------------------

def test_background_opacity_defaults_to_the_designed_alpha():
    """205/255 - the slider at 80%, and exactly the card as designed, so an
    untouched install paints byte-identical frames. The slider itself runs
    the full range: 100% is a solid card the game cannot be seen through."""
    assert config.background_opacity() == config.DEFAULT_BACKGROUND_OPACITY
    assert config.background_opacity() == 205 / 255


def test_text_visibility_defaults_to_the_designed_midpoint():
    """0.5 is the designed look; below fades, above boosts contrast."""
    assert config.text_visibility() == 0.5


def test_transparency_settings_round_trip():
    config.set_background_opacity(0.35)
    config.set_text_visibility(0.8)
    assert config.background_opacity() == 0.35
    assert config.text_visibility() == 0.8


def test_both_sliders_reach_their_extremes():
    """Full range by explicit decision - including text 0, which the player
    asked for knowing it is unreadable."""
    config.set_background_opacity(0.0)
    config.set_text_visibility(0.0)
    assert config.background_opacity() == 0.0
    assert config.text_visibility() == 0.0
    config.set_background_opacity(1.0)
    config.set_text_visibility(1.0)
    assert config.background_opacity() == 1.0
    assert config.text_visibility() == 1.0


@pytest.mark.parametrize("junk", ["mostly", None, [0.5], True])
def test_garbage_transparency_falls_back(junk):
    # True is the trap again: it IS an int, and float(True) is a plausible
    # 1.0 that would hide a mangled file.
    config.save({"background_opacity": junk, "text_visibility": junk})
    assert config.background_opacity() == config.DEFAULT_BACKGROUND_OPACITY
    assert config.text_visibility() == 0.5


# ---- the hotkey action partition -------------------------------------------
#
# Each action is registered by exactly one process: the overlay's keys while
# it runs, the launcher's start/stop key for its whole session. A combination
# can only be registered once, so an action in both lists would mean one side
# failing with "already in use" on every start.

def test_every_action_belongs_to_exactly_one_process():
    overlay_side = set(config.OVERLAY_HOTKEY_ACTIONS)
    launcher_side = set(config.LAUNCHER_HOTKEY_ACTIONS)

    assert overlay_side | launcher_side == set(config.HOTKEY_ACTIONS)
    assert not overlay_side & launcher_side


def test_the_start_stop_key_ships_unbound():
    """An empty binding is the grammar's own "switched off". A key that
    starts and stops a whole program is one a player should choose to have,
    not discover by accident."""
    assert config.DEFAULT_HOTKEYS["start_stop_overlay"] == ""
    assert config.hotkeys()["start_stop_overlay"] == ""


def test_the_start_stop_key_round_trips_like_any_other():
    config.set_hotkey("start_stop_overlay", "Ctrl+Alt+O")
    assert config.hotkeys()["start_stop_overlay"] == "Ctrl+Alt+O"


def test_every_default_binding_parses_or_is_disabled():
    """A default that stopped parsing would make hotkeys dead on arrival for
    every new install, and nothing else would say so."""
    from loom.hotkeys import keyspec
    for action, binding in config.DEFAULT_HOTKEYS.items():
        assert keyspec.problem(binding) is None, (action, binding)


def test_the_overlay_position_can_be_forgotten():
    """The way out of a position gone wrong: a placement can land the panel
    off every screen, and a player cannot drag a window they cannot see."""
    config.set_overlay_offset(9000, 9000)
    assert config.overlay_offset() == (9000, 9000)

    config.clear_overlay_offset()

    assert config.overlay_offset() is None


def test_forgetting_an_unset_position_is_not_an_error():
    config.clear_overlay_offset()
    assert config.overlay_offset() is None


def test_hiding_the_panel_is_the_overlays_own_key():
    """It must be registered by the overlay, not the launcher: the launcher
    holds its key for its whole session, and a combination may be
    registered by exactly one process. Getting this partition wrong prints
    'already in use' on every overlay start."""
    assert "toggle_hidden" in config.OVERLAY_HOTKEY_ACTIONS
    assert "toggle_hidden" not in config.LAUNCHER_HOTKEY_ACTIONS


def test_every_default_binding_is_usable_and_unique():
    """A default that does not parse, or that collides with another
    default, would ship as a key that silently does nothing."""
    from loom.hotkeys import keyspec

    bindings = config.DEFAULT_HOTKEYS
    for action, binding in bindings.items():
        assert keyspec.problem(binding) is None, \
            f"{action} ships an unusable default: {binding!r}"
    assert keyspec.conflicts(bindings) == [], \
        "two actions ship on the same combination"


def test_the_hide_key_has_a_modifier_like_every_other_one():
    """The load-bearing rule: a bare key would be taken from the game."""
    from loom.hotkeys import keyspec

    spec = keyspec.parse(config.DEFAULT_HOTKEYS["toggle_hidden"])
    assert spec.modifiers, "a global hotkey without a modifier is a bug"
    assert spec.key == "0"
