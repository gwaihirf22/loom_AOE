"""
Loom — tests for the alert policy.

The policy is where "what happened" becomes "how loudly to say it", so these
tests pin the behaviour the player actually experiences: the idle-TC warning
fades out as the economy matures, housed always shouts, pop-capped stays
quiet, and the player's own thresholds win over the defaults.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import alerts, config, production
from loom.alerts import FULL, SOFT, OFF, IdleTcPolicy


def test_severity_fades_with_villager_count():
    policy = IdleTcPolicy(soften_at=100, silence_at=120)
    assert policy.severity(4) == FULL
    assert policy.severity(99) == FULL
    assert policy.severity(100) == SOFT
    assert policy.severity(119) == SOFT
    assert policy.severity(120) == OFF
    assert policy.severity(180) == OFF


def test_unknown_villager_count_gets_the_full_alert():
    # The count is only unknown before the first stable reading - early
    # game, exactly when an idle TC hurts most.
    assert IdleTcPolicy().severity(None) == FULL


def test_silence_never_below_soften():
    # A config with the numbers crossed should not create a band where the
    # alert is silent below the soft threshold.
    policy = IdleTcPolicy(soften_at=100, silence_at=80)
    assert policy.severity(90) == FULL
    assert policy.severity(105) == OFF


def test_housed_shouts_pop_cap_stays_quiet():
    assert alerts.BLOCK_SEVERITY[production.HOUSED] == FULL
    assert alerts.BLOCK_SEVERITY[production.POP_CAPPED] == OFF


def test_config_defaults_apply_without_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    assert config.idle_tc_limits() == (alerts.SOFTEN_AT, alerts.SILENCE_AT)


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.set_idle_tc_limits(80, 110)
    assert config.idle_tc_limits() == (80, 110)


def test_garbage_config_falls_back_to_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.save({"idle_tc_alert": "loud please"})
    assert config.idle_tc_limits() == (alerts.SOFTEN_AT, alerts.SILENCE_AT)


# --- the one alert the overlay shows ---------------------------------------

from loom import queue
from loom.production import ProductionTracker


def vill(tint="green"):
    return queue.SlotReading(0, tint, None, None, "villager_male", 0.5)


def settled(polls):
    """A tracker fed the same polls twice, so its beliefs are confirmed."""
    tracker = ProductionTracker()
    for game_time, slots in polls:
        tracker.update(game_time, slots)
        tracker.update(game_time + 3, slots)
    return tracker


def test_no_alert_while_producing():
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(tracker, 20, IdleTcPolicy(), 106) == (None, None)


def test_idle_tc_alert_with_duration():
    tracker = settled([(100, [vill()]), (200, [])])
    text, severity = alerts.production_alert(tracker, 20, IdleTcPolicy(), 230)
    assert severity == FULL
    assert text.startswith("TC IDLE")
    assert "30s" in text


def test_housed_comes_from_the_population_indicator():
    # Housed is judged from pop current==cap below 200 - never from queue
    # tints, which bare skin fooled for a whole live session.
    tracker = settled([(100, [vill()])])
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(25, 25))
    assert severity == FULL
    assert "HOUSED" in text


def test_house_warning_fires_before_the_wall():
    # 21/25 leaves 4 space: the pre-emptive warning, so the house goes up
    # BEFORE production stalls rather than after.
    tracker = settled([(100, [vill()])])
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(21, 25))
    assert severity == FULL
    assert text == "HOUSE NOW — 4 pop space left"


def test_ample_pop_space_stays_quiet():
    # 20/25 leaves 5 space: one more than the warning threshold.
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(20, 25)) == (None, None)


def test_full_at_standard_cap_is_not_housed():
    # 200/200 is pop-capped: usually good, and never a house problem - and
    # neither is APPROACHING 200, where no house can add anything.
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(
        tracker, 150, IdleTcPolicy(), 106, population=(200, 200)) == (None, None)
    assert alerts.production_alert(
        tracker, 150, IdleTcPolicy(), 106, population=(198, 200)) == (None, None)


def test_housed_beats_idle():
    tracker = settled([(100, [vill(), vill()]), (200, [])])
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 206, population=(20, 20))
    assert severity == FULL
    assert "HOUSED" in text


def test_idle_alert_softens_then_silences():
    tracker = settled([(100, [vill()]), (200, [])])
    _, severity = alerts.production_alert(tracker, 105, IdleTcPolicy(), 230)
    assert severity == SOFT
    assert alerts.production_alert(tracker, 150, IdleTcPolicy(), 230) == (None, None)


def test_pop_capped_stays_silent():
    tracker = settled([(100, [vill()]), (200, [vill("amber")])])
    assert alerts.production_alert(tracker, 190, IdleTcPolicy(), 206) == (None, None)


# --- stacking: both facts shown at once -------------------------------------

def idle_tracker():
    """A tracker with a confirmed idle TC."""
    return settled([(100, [vill()]), (200, [])])


def test_house_warning_and_idle_tc_stack():
    # A TC sitting quiet while the pop cap closes in: both facts are true,
    # both must show - hiding one behind the other taught the player nothing.
    found = alerts.production_alerts(
        idle_tracker(), 20, IdleTcPolicy(), 230, population=(22, 25))
    assert len(found) == 2
    assert found[0][0].startswith("HOUSE NOW")   # pop trouble nearest the panel
    assert found[1][0].startswith("TC IDLE")


def test_singular_wrapper_returns_the_most_urgent():
    text, severity = alerts.production_alert(
        idle_tracker(), 20, IdleTcPolicy(), 230, population=(22, 25))
    assert text.startswith("HOUSE NOW")
    assert severity == FULL


def test_toggles_silence_each_family_independently():
    quiet_pop = alerts.AlertToggles(house_warning=False)
    found = alerts.production_alerts(
        idle_tracker(), 20, IdleTcPolicy(), 230, population=(22, 25),
        toggles=quiet_pop)
    assert [t for t, _ in found] == ["TC IDLE — 30s"]

    quiet_tc = alerts.AlertToggles(idle_tc=False)
    found = alerts.production_alerts(
        idle_tracker(), 20, IdleTcPolicy(), 230, population=(22, 25),
        toggles=quiet_tc)
    assert len(found) == 1
    assert found[0][0].startswith("HOUSE NOW")


# --- switching alert families off ------------------------------------------

from loom.alerts import AlertToggles


def test_no_toggles_means_everything_fires():
    # toggles=None is what every pre-launcher call site passes implicitly;
    # this pins that the default is all alerts on, exactly as before.
    idle = settled([(100, [vill()]), (200, [])])
    text, severity = alerts.production_alert(idle, 20, IdleTcPolicy(), 230)
    assert (text.startswith("TC IDLE"), severity) == (True, FULL)
    housed = settled([(100, [vill()])])
    text, _ = alerts.production_alert(
        housed, 20, IdleTcPolicy(), 106, population=(25, 25))
    assert "HOUSED" in text


def test_idle_tc_toggle_silences_only_the_idle_alert():
    tracker = settled([(100, [vill()]), (200, [])])
    quiet = AlertToggles(idle_tc=False)
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 230, toggles=quiet) == (None, None)
    # Housed still fires: the toggle switches off one family, not the system.
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 230, population=(25, 25), toggles=quiet)
    assert ("HOUSED" in text, severity) == (True, FULL)


def test_housed_toggle_silences_housed_at_zero_headroom():
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(25, 25),
        toggles=AlertToggles(housed=False)) == (None, None)


def test_house_warning_off_still_reports_actually_housed():
    # The pre-emptive warning and the stalled-production alert are separate
    # choices: with only house_warning off, approaching the wall is quiet
    # but hitting it still shouts.
    tracker = settled([(100, [vill()])])
    quiet = AlertToggles(house_warning=False)
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(21, 25),
        toggles=quiet) == (None, None)
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(25, 25), toggles=quiet)
    assert ("HOUSED" in text, severity) == (True, FULL)


def test_housed_off_lets_an_idle_tc_through():
    # Priority only applies among the alerts the player wants: housed being
    # switched off must not hide the idle TC behind it.
    tracker = settled([(100, [vill(), vill()]), (200, [])])
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 206, population=(20, 20),
        toggles=AlertToggles(housed=False))
    assert ("IDLE" in text, severity) == (True, FULL)


def test_house_headroom_is_the_players_number():
    # A boom eats more pop space per house than a one-TC opening, so the
    # threshold is configurable. 8 space left: quiet at the default 4,
    # warned with the player's 8.
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(17, 25)) == (None, None)
    text, severity = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(17, 25),
        house_headroom=8)
    assert (text, severity) == ("HOUSE NOW — 8 pop space left", FULL)


def test_house_headroom_can_also_tighten():
    # With headroom 1, 2 space left is still quiet - the warning only lands
    # at the very last moment, which is the player's own choice to make.
    tracker = settled([(100, [vill()])])
    assert alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(23, 25),
        house_headroom=1) == (None, None)
    text, _ = alerts.production_alert(
        tracker, 20, IdleTcPolicy(), 106, population=(24, 25),
        house_headroom=1)
    assert text == "HOUSE NOW — 1 pop space left"


def test_toggles_build_from_config_dict(tmp_path, monkeypatch):
    # The exact hand-off the overlay does at startup:
    # AlertToggles(**config.alert_toggles()).
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    config.set_alert_toggle("idle_tc", False)
    toggles = AlertToggles(**config.alert_toggles())
    assert (toggles.idle_tc, toggles.housed, toggles.house_warning) == (
        False, True, True)
