"""
Loom — saved settings.

Small JSON file next to the code, holding the things a player sets once and
expects to stay put. Currently just where the overlay sits.

The overlay position is stored as an **offset from the game window's top-left
corner**, not as a desktop coordinate. Desktop coordinates would break the
moment the game changed resolution or moved to another monitor, and on a
multi-monitor desktop they are not even guaranteed to land on a screen.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

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
