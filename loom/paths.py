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

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

from pathlib import Path

# __file__ is this file. .resolve() turns it into a full path with any
# symlinks followed. .parent is loom/, and its .parent is the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEMPLATES_DIR = PROJECT_ROOT / "templates"
POP_ICON_TEMPLATE = TEMPLATES_DIR / "pop_icon.png"
DIGIT_TEMPLATES_DIR = TEMPLATES_DIR / "digits"

BUILDS_DIR = PROJECT_ROOT / "builds"
CAPTURES_DIR = PROJECT_ROOT / "captures"
