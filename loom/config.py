"""
Loom — saved settings.

Small JSON file holding the things a player sets once and expects to stay
put: where the overlay sits, which build order is active, which alerts are
welcome, and whether the launcher shows its developer tools.

It used to sit next to the code. It now lives wherever the OS keeps user
settings - loom/paths.py decides where, and migrates an existing one across -
because the source tree is read-only in an installed copy and temporary in a
frozen one, and settings that vanish when the program exits are worse than no
settings at all.

The overlay position is stored as an **offset from the game window's top-left
corner**, not as a desktop coordinate. Desktop coordinates would break the
moment the game changed resolution or moved to another monitor, and on a
multi-monitor desktop they are not even guaranteed to land on a screen.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

from . import paths

# Re-exported from paths rather than built here, so there is exactly one
# place that decides where writable data goes. Tests monkeypatch this name.
CONFIG_PATH = paths.CONFIG_PATH


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
    """Write the settings file, creating its directory if it is not there.

    The mkdir is not optional now that this lives outside the source tree:
    on a fresh install nothing has created %APPDATA%/Loom or ~/.config/loom
    yet, and the first setting a player changes would otherwise fail.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
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
#   house_warning  the pre-emptive "HOUSE SOON" alert just before the wall
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


# How much pop space the HOUSE SOON warning may be set to fire at. Zero would
# be the housed alert itself (and the warning has its own off switch); past
# twenty the warning would rarely stop showing.
HOUSE_HEADROOM_BOUNDS = (1, 20)


def house_headroom():
    """How much pop space remaining triggers the HOUSE SOON warning.

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
    """Remember the player's HOUSE SOON threshold."""
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


def _scale(key, bounds, default=1.0):
    """One clamped multiplier from the settings file.

    Anything unusable means the default - for the size knobs that is 1.0,
    the overlay at its designed size. The bool check is not paranoia: True
    IS an int in Python, and float(True) is a plausible-looking 1.0 that
    would silently hide a mangled file.
    """
    value = load().get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = bounds
        return min(high, max(low, float(value)))
    return default


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


def browser_position():
    """Where the build preview window was left, as (x, y). None if never moved.

    A DESKTOP coordinate, unlike overlay_offset above, and that difference is
    deliberate. The overlay is measured from the game window because the game
    moves; the preview is an ordinary desktop window that stays where the
    player put it, so where it was is the whole answer.

    None means "never placed", which is the launcher's cue to put it beside
    itself rather than let the window manager drop it wherever - historically
    behind the launcher, where it was easy to miss entirely.
    """
    value = load().get("browser_position")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return None


def set_browser_position(x, y):
    """Remember where the build preview window was left."""
    settings = load()
    settings["browser_position"] = [int(x), int(y)]
    save(settings)
    return settings


def launcher_window():
    """The launcher's last size, as (width, height). None if never resized.

    Same shape as browser_window above, and restored the same way - but the
    launcher passes it through launcher.fitted_size first, because this one
    can be a size that does not fit the screen it is being restored onto.
    """
    value = load().get("launcher_window")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return None


def set_launcher_window(width, height):
    """Remember the launcher's size."""
    settings = load()
    settings["launcher_window"] = [int(width), int(height)]
    save(settings)
    return settings


def launcher_position():
    """Where the launcher was left, as (x, y). None if never moved.

    A desktop coordinate, like browser_position. None means "never placed",
    which leaves the placing to the window manager - the right answer on a
    first run and under Wayland, where a client may not position itself at
    all.
    """
    value = load().get("launcher_position")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return None


def set_launcher_position(x, y):
    """Remember where the launcher was left."""
    settings = load()
    settings["launcher_position"] = [int(x), int(y)]
    save(settings)
    return settings


def about_seen():
    """Whether the How-to-use window has been shown and dismissed. Default: no.

    Default-off like developer_mode, so a fresh install shows it once. It
    earns that one interruption: Loom's templates were cut from a modded HUD,
    and on the stock panel the anchor scores below the threshold - so without
    being told, a new player's first experience is a program that hangs with
    no explanation.
    """
    return load().get("about_seen") is True


def set_about_seen(seen):
    """Remember that the player has seen the How-to-use window."""
    settings = load()
    settings["about_seen"] = bool(seen)
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


def clear_overlay_offset():
    """Forget where the player put the overlay: back to the default spot.

    The way out of a position gone wrong - a placement measured against one
    origin and replayed against another can land the panel off every screen,
    and a player cannot drag a window they cannot see.
    """
    settings = load()
    settings.pop("overlay_offset", None)
    save(settings)
    return settings


# The hotkey actions, partitioned by which PROCESS registers them. The
# partition is load-bearing: a combination may be registered by exactly one
# process, so each side filters to its own actions - without that, every
# overlay start would try to register the launcher's key and print a
# spurious "already in use".
#
# The overlay's, active while it runs:
#   previous_step  step the panel back one
#   next_step      step the panel forward one
#   toggle_follow  stop/resume following the game automatically
#
# Hotkeys are a CORRECTION for a reading that has drifted, not the way Loom is
# meant to be used - see loom/follow.py. That is why the two step actions only
# suspend automatic following for manual_hold_seconds rather than switching it
# off: the player nudges the step, reads it, and Loom takes over again without
# being asked.
OVERLAY_HOTKEY_ACTIONS = ("previous_step", "next_step", "toggle_follow")

# The launcher's, active for its whole session - necessarily, because the one
# thing this key does is start a process that does not exist yet:
#   start_stop_overlay  one key toggling what the Start/Stop buttons do
LAUNCHER_HOTKEY_ACTIONS = ("start_stop_overlay",)

HOTKEY_ACTIONS = OVERLAY_HOTKEY_ACTIONS + LAUNCHER_HOTKEY_ACTIONS

# Ctrl+Shift+Q/W sit together under the left hand; R is deliberately further
# away, because toggling automatic following off is the one that matters and
# should be hard to hit by accident. Every one of them is rebindable and can
# be switched off entirely - AoE2 players remap heavily, and a hotkey Loom
# registers is TAKEN FROM THE GAME, so a fixed binding would be a bug.
#
# start_stop_overlay ships UNBOUND: an empty binding is the grammar's own
# "switched off", and a key that starts and stops a whole program is one a
# player should choose to have, not discover by accident.
DEFAULT_HOTKEYS = {
    "previous_step": "Ctrl+Shift+Q",
    "next_step": "Ctrl+Shift+W",
    "toggle_follow": "Ctrl+Shift+R",
    "start_stop_overlay": "",
}

# How long a step hotkey may suspend automatic following. The floor stops a
# hold so short it expires before the player has read the step; the ceiling
# stops a "temporary" hold that is temporary in name only - past a minute,
# the toggle is the honest thing to use instead.
MANUAL_HOLD_BOUNDS = (3, 60)


def hotkeys():
    """The player's bindings, as {action: text} with every action filled.

    Stored as "hotkeys": {"next_step": "Ctrl+Shift+W", ...}. Anything missing
    falls back to the default for that action, so a partially written file
    still gives a complete set and callers never handle a missing key.

    An empty string is kept as-is rather than replaced: it is how a player
    says "no key for this one", and quietly restoring the default would
    re-take a combination from the game that they had deliberately given
    back. Only a MISSING action gets its default.
    """
    value = load().get("hotkeys")
    if not isinstance(value, dict):
        value = {}
    bindings = {}
    for action in HOTKEY_ACTIONS:
        binding = value.get(action, DEFAULT_HOTKEYS[action])
        bindings[action] = binding if isinstance(binding, str) else \
            DEFAULT_HOTKEYS[action]
    return bindings


def set_hotkey(action, binding):
    """Remember one binding. "" switches that action off."""
    if action not in HOTKEY_ACTIONS:
        raise ValueError(f"unknown hotkey action: {action!r}")
    settings = load()
    bindings = settings.get("hotkeys")
    if not isinstance(bindings, dict):
        bindings = {}
    bindings[action] = "" if binding is None else str(binding).strip()
    settings["hotkeys"] = bindings
    save(settings)
    return settings


def hotkeys_enabled():
    """Should Loom register hotkeys at all?

    Default-on like the preview and APM, so the `is not False` check. The
    master switch exists so a player can hand every combination back to the
    game at once, without clearing three settings one at a time.
    """
    return load().get("hotkeys_enabled") is not False


def set_hotkeys_enabled(enabled):
    """Remember whether hotkeys are wanted at all."""
    settings = load()
    settings["hotkeys_enabled"] = bool(enabled)
    save(settings)
    return settings


def manual_hold_seconds():
    """How long a step hotkey suspends automatic following, in seconds."""
    value = load().get("manual_hold_seconds")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        low, high = MANUAL_HOLD_BOUNDS
        return min(high, max(low, int(value)))
    return 10


def set_manual_hold_seconds(value):
    """Remember the player's hold length."""
    settings = load()
    settings["manual_hold_seconds"] = int(value)
    save(settings)
    return settings


# The two transparency sliders, both full-range. The background may vanish
# entirely - text floating straight on the game is a legitimate style - and
# it may also go fully SOLID, hiding the game behind the card. Text has no
# floor either, by explicit decision: 0 is unreadable and the player said so
# themselves while asking for it.
BACKGROUND_OPACITY_BOUNDS = (0.0, 1.0)
TEXT_VISIBILITY_BOUNDS = (0.0, 1.0)

# The designed card is alpha 205 of 255. The default reproduces it exactly,
# so an untouched install paints byte-identical frames - and it happens to
# be the "75% ish" the beta feedback asked the default to feel like.
DEFAULT_BACKGROUND_OPACITY = 205 / 255


def background_opacity():
    """How solid the overlay's dark card is drawn. TRUE opacity: 1.0 is a
    solid card the game cannot be seen through, 0.0 is no card at all."""
    return _scale("background_opacity", BACKGROUND_OPACITY_BOUNDS,
                  default=DEFAULT_BACKGROUND_OPACITY)


def set_background_opacity(value):
    """Remember the player's background opacity."""
    settings = load()
    settings["background_opacity"] = float(value)
    save(settings)
    return settings


def text_visibility():
    """How visible the overlay's writing is, on a scale where 0.5 is the
    DESIGNED look. Below 0.5 the content fades toward invisible; above it
    the colours climb toward full contrast - see overlay.content_style.

    Its own key rather than the old "text_opacity", because the meaning
    changed: above the midpoint this is not an opacity at all, and a saved
    1.0 from the old scale silently becoming "maximum contrast" would be a
    surprise nobody asked for."""
    return _scale("text_visibility", TEXT_VISIBILITY_BOUNDS, default=0.5)


def set_text_visibility(value):
    """Remember the player's text visibility."""
    settings = load()
    settings["text_visibility"] = float(value)
    save(settings)
    return settings

