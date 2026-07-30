"""
Loom — saved settings.

Small JSON file next to the code, holding the things a player sets once and
expects to stay put: where the overlay sits, which build order is active,
which alerts are welcome, and whether the launcher shows its developer tools.

The overlay position is stored as an **offset from the game window's top-left
corner**, not as a desktop coordinate. Desktop coordinates would break the
moment the game changed resolution or moved to another monitor, and on a
multi-monitor desktop they are not even guaranteed to land on a screen.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

from . import paths

CONFIG_PATH = paths.PROJECT_ROOT / "config.json"


def load():
    """Read the settings file. Returns {} if there is not one yet."""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        # A corrupt settings file should not stop Loom from running.
        return {}


def save(settings):
    """Write the settings file."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")


def idle_tc_limits():
    """The villager counts where the idle-TC alert softens, then shuts off.

    Returns (soften_at, silence_at). Stored as "idle_tc_alert": [100, 120].
    The defaults live in alerts.py; this only overrides them when the player
    has written something usable.
    """
    from . import alerts
    value = load().get("idle_tc_alert")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return alerts.SOFTEN_AT, alerts.SILENCE_AT


def set_idle_tc_limits(soften_at, silence_at):
    """Remember the player's idle-TC alert thresholds."""
    settings = load()
    settings["idle_tc_alert"] = [int(soften_at), int(silence_at)]
    save(settings)
    return settings


# The alerts a player can switch off, and what each name means:
#   idle_tc        the TC IDLE warning (empty production queue)
#   housed         the HOUSED alert once production has actually stalled
#   house_warning  the pre-emptive "HOUSE NOW" alert just before the wall
ALERT_NAMES = ("idle_tc", "housed", "house_warning")


def alert_toggles():
    """Which alerts the player wants, as {name: bool} with every name filled.

    Stored as "alerts_enabled": {"idle_tc": true, ...}. Anything missing or
    unusable means the alert stays ON - a player who has never touched the
    setting should get every warning, and a corrupt file should not silently
    switch protection off.
    """
    value = load().get("alerts_enabled")
    if not isinstance(value, dict):
        value = {}
    return {name: bool(value.get(name, True)) for name in ALERT_NAMES}


def set_alert_toggle(name, enabled):
    """Remember whether one named alert is wanted."""
    if name not in ALERT_NAMES:
        raise ValueError(f"unknown alert toggle: {name!r}")
    settings = load()
    toggles = settings.get("alerts_enabled")
    if not isinstance(toggles, dict):
        toggles = {}
    toggles[name] = bool(enabled)
    settings["alerts_enabled"] = toggles
    save(settings)
    return settings


def developer_mode():
    """Whether the launcher shows its developer tools. Default: no."""
    return load().get("developer_mode") is True


def set_developer_mode(enabled):
    """Remember whether the launcher shows its developer tools."""
    settings = load()
    settings["developer_mode"] = bool(enabled)
    save(settings)
    return settings


# How much pop space the HOUSE NOW warning may be set to fire at. Zero would
# be the housed alert itself (and the warning has its own off switch); past
# twenty the warning would rarely stop showing.
HOUSE_HEADROOM_BOUNDS = (1, 20)


def house_headroom():
    """How much pop space remaining triggers the HOUSE NOW warning.

    The default lives in alerts.py; this only overrides it when the player
    has written something usable.
    """
    from . import alerts
    value = load().get("house_headroom")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = HOUSE_HEADROOM_BOUNDS
        return min(high, max(low, int(value)))
    return alerts.HOUSE_WARNING_HEADROOM


def set_house_headroom(value):
    """Remember the player's HOUSE NOW threshold."""
    settings = load()
    settings["house_headroom"] = int(value)
    save(settings)
    return settings


# How far the overlay's two size knobs may go. The lower bound stops an
# accidental 0 from shrinking the panel to nothing; the upper bounds stop a
# fat-fingered value from filling the monitor. Composed worst case is 5x
# fonts, already past useful.
OVERLAY_SCALE_BOUNDS = (0.75, 2.5)
TEXT_SCALE_BOUNDS = (0.75, 2.0)


def _scale(key, bounds):
    """One size multiplier from the settings file, clamped into bounds.

    Anything unusable means 1.0 - the overlay at its designed size. The bool
    check is not paranoia: True IS an int in Python, and float(True) is a
    plausible-looking 1.0 that would silently hide a mangled file.
    """
    value = load().get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = bounds
        return min(high, max(low, float(value)))
    return 1.0


def overlay_scale():
    """How much bigger than designed the whole overlay should be."""
    return _scale("overlay_scale", OVERLAY_SCALE_BOUNDS)


def set_overlay_scale(value):
    """Remember the player's overall overlay size."""
    settings = load()
    settings["overlay_scale"] = float(value)
    save(settings)
    return settings


def text_scale():
    """How much bigger than designed the overlay's text should be.

    Independent of overlay_scale: text growth makes the panel taller, not
    wider, so the two knobs compose without lines colliding.
    """
    return _scale("text_scale", TEXT_SCALE_BOUNDS)


def set_text_scale(value):
    """Remember the player's overlay text size."""
    settings = load()
    settings["text_scale"] = float(value)
    save(settings)
    return settings


def track_apm():
    """Whether the APM counter runs alongside the overlay. Default: yes.

    Counts only - the counter never sees which key. Default-on like the
    preview, so the "is not False" check.
    """
    return load().get("track_apm") is not False


def set_track_apm(enabled):
    """Remember whether the APM counter runs with the overlay."""
    settings = load()
    settings["track_apm"] = bool(enabled)
    save(settings)
    return settings


def build_browser():
    """Whether the launcher shows the build preview panel. Default: yes.

    The mirror image of developer_mode: this defaults ON, so the check is
    "is not False" - anything but a deliberate false, garbage included,
    keeps the panel visible.
    """
    return load().get("show_build_browser") is not False


def set_build_browser(enabled):
    """Remember whether the launcher shows the build preview panel."""
    settings = load()
    settings["show_build_browser"] = bool(enabled)
    save(settings)
    return settings


def active_build():
    """The build order the player last chose, as a file stem for builds/.

    The default matches the entry points' own --build default, so the
    launcher and the command line agree on what "just run it" means.
    """
    value = load().get("active_build")
    if isinstance(value, str) and value:
        return value
    return "fast_castle"


def set_active_build(stem):
    """Remember which build order the player chose."""
    settings = load()
    settings["active_build"] = str(stem)
    save(settings)
    return settings


def browser_window():
    """The build preview window's last size, as (width, height). None if
    never resized - the browser picks its own default then."""
    value = load().get("browser_window")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return None


def set_browser_window(width, height):
    """Remember the build preview window's size."""
    settings = load()
    settings["browser_window"] = [int(width), int(height)]
    save(settings)
    return settings


def overlay_offset():
    """Where the player put the overlay, as (dx, dy). None if never placed."""
    value = load().get("overlay_offset")
    if isinstance(value, list) and len(value) == 2:
        return int(value[0]), int(value[1])
    return None


def set_overlay_offset(dx, dy):
    """Remember where the player put the overlay."""
    settings = load()
    settings["overlay_offset"] = [int(dx), int(dy)]
    save(settings)
    return settings
