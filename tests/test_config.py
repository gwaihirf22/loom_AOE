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
    assert config.idle_tc_limits() == (80, 110)
    assert config.alert_toggles()["housed"] is False
    assert config.developer_mode() is True
    assert config.build_browser() is False
    assert config.active_build() == "archers19pop"
    assert config.overlay_scale() == 1.5
    assert config.text_scale() == 1.25
    assert config.browser_window() == (820, 900)
    assert config.house_headroom() == 6
