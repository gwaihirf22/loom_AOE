"""
Loom — where things live on disk.

Every other module asks this one for file locations, rather than writing
relative paths like "templates/pop_icon.png".

Why that matters: a relative path is resolved against the *current working
directory* — wherever the user happened to be when they ran the program, not
where the code lives. That works right up until someone runs Loom from another
folder, at which point the templates silently cannot be found.

Deriving paths from __file__ instead means they are anchored to the source
tree, so they are correct no matter where Loom is started from.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from pathlib import Path

# __file__ is this file. .resolve() turns it into a full path with any
# symlinks followed. .parent is loom/, and its .parent is the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEMPLATES_DIR = PROJECT_ROOT / "templates"
POP_ICON_TEMPLATE = TEMPLATES_DIR / "pop_icon.png"
DIGIT_TEMPLATES_DIR = TEMPLATES_DIR / "digits"

BUILDS_DIR = PROJECT_ROOT / "builds"
CAPTURES_DIR = PROJECT_ROOT / "captures"

# Optional resource icons the player can drop in (wood.png, food.png, ...).
# Loom ships none - they are game art - so this folder may not exist, and the
# overlay falls back to words when an icon is missing.
ICONS_DIR = PROJECT_ROOT / "icons"
