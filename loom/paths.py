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

There are two kinds of path here and the split is load-bearing.

READ-ONLY things Loom ships - templates, build orders, icons - are part of the
source tree and stay derived from __file__.

WRITABLE things the player accumulates - settings, per-game statistics - do
not. They used to live in the source tree too, which was fine while Loom was
only ever run from a git clone and is wrong for every other way of running it:
installed under Program Files or /usr/share the tree is read-only, and inside
a frozen one-file bundle it is a temporary directory that is deleted on exit,
so a match's statistics would be written and then destroyed. They now go where
each OS says user data goes, and Loom migrates what it finds in the old place
exactly once. This is the first of the two prerequisites for distribution in
.loom-roadmap.md.

And a THIRD kind that only turned up when packaging was thought through:
folders holding both files Loom ships and files the player adds - build
orders above all, since loading community ones is the point of the format.
Those cannot move (the shipped copies travel with the code) and cannot stay
(an installed copy is read-only), so each is a search path with the player's
copy first. See SHARED_ASSET_DIRS below.

Captures are the deliberate exception and stay in the tree: they are scratch
written by tools/, not the player's data, and CLAUDE.md documents them as
such. Frozen is the exception to the exception - see CAPTURES_DIR below.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import shutil
import sys
from pathlib import Path, PurePosixPath


def _install_root():
    """Where Loom's own files are, running from a clone or from a bundle.

    From a clone: __file__ is this file, .resolve() follows any symlinks,
    .parent is loom/ and its .parent is the project root.

    Frozen: __file__ points inside the bundle's own archive and is no use.
    PyInstaller's onefile mode extracts the data files to a temporary
    directory named by sys._MEIPASS; onedir puts them beside the executable.
    Asking for _MEIPASS first and falling back to the executable's folder
    covers both without needing to know which was built.
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", None)
                    or Path(sys.executable).resolve().parent)
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _install_root()

TEMPLATES_DIR = PROJECT_ROOT / "templates"
POP_ICON_TEMPLATE = TEMPLATES_DIR / "pop_icon.png"
DIGIT_TEMPLATES_DIR = TEMPLATES_DIR / "digits"

# The stock (unmodded) HUD's anchor art. The Anne_HK templates stay where they
# have always been, directly in templates/ - see loom/hud.py, which pairs each
# skin's templates with the offsets that go with them.
STOCK_TEMPLATES_DIR = TEMPLATES_DIR / "stock"

BUILDS_DIR = PROJECT_ROOT / "builds"

# The logo and the icon derived from it. The PNG is the source of truth;
# the .ico is what Windows wants (multi-resolution, for the taskbar, the
# title bar and the eventual installer) and is regenerated from the PNG by
# tools/make_icon.py whenever the logo changes.
IMAGES_DIR = PROJECT_ROOT / "images"
LOGO_PATH = IMAGES_DIR / "loom_logo.png"
ICON_PATH = IMAGES_DIR / "loom.ico"

# CAPTURES_DIR is defined below, after DATA_DIR - frozen it points there.


# ---------------------------------------------------------------------------
# Where the player's own data goes
# ---------------------------------------------------------------------------

# Escape hatch, and the same convention as LOOM_CAPTURE_BACKEND: point Loom's
# writable data somewhere else. Exists for tests and for running two builds
# side by side without them sharing a settings file.
DATA_DIR_ENV = "LOOM_DATA_DIR"


def _windows_data_home():
    # APPDATA is the roaming profile, which is where settings belong; it
    # follows the user between machines on a domain. Falling back to the
    # literal path rather than giving up, because a missing APPDATA means a
    # strange environment, not a reason to refuse to save anything.
    roaming = os.environ.get("APPDATA")
    return Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"


def _xdg_home(variable, default):
    """An XDG base directory, honouring the variable when it is absolute.

    The spec says a relative value is invalid and must be ignored, which is
    worth obeying: a stray XDG_DATA_HOME=. would otherwise put the player's
    match history in whatever directory Loom happened to be started from.

    "Absolute" is judged by POSIX rules rather than the running OS's, because
    XDG is a POSIX-only convention and "/home/me/.local/share" is an absolute
    path whatever machine is asking. Path.is_absolute() would call it relative
    on Windows for want of a drive letter - which changes nothing on Linux,
    where this actually runs, but would quietly make the rule untestable from
    anywhere else. Keeping it checkable from Windows is the same reason the
    Windows backend keeps its imports inside functions.
    """
    value = os.environ.get(variable)
    if value and PurePosixPath(value).is_absolute():
        return Path(value)
    return Path.home() / default


def data_home(platform=None):
    """The directory this OS says user data belongs in.

    Windows and macOS both keep settings and data in one place per
    application, so config_home returns this same directory there. Linux
    splits them, and the split is honoured rather than flattened - a Linux
    user backing up ~/.config expects settings to be in it.
    """
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return _windows_data_home() / "Loom"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Loom"
    return _xdg_home("XDG_DATA_HOME", Path(".local") / "share") / "loom"


def config_home(platform=None):
    """The directory this OS says settings belong in."""
    platform = sys.platform if platform is None else platform
    if platform in ("win32", "darwin"):
        return data_home(platform)
    return _xdg_home("XDG_CONFIG_HOME", Path(".config")) / "loom"


# The override applies to both, so pointing it at a scratch directory moves
# everything a run touches, which is what makes it useful to a test.
_override = os.environ.get(DATA_DIR_ENV)
DATA_DIR = Path(_override) if _override else data_home()
CONFIG_DIR = Path(_override) if _override else config_home()

# Per-game statistics files, one JSON per match. The player's own history,
# not the project's.
STATS_DIR = DATA_DIR / "stats"

def _captures_dir():
    """Captured frames. From a clone they stay in the source tree on
    purpose - scratch written by tools/ during development, see the module
    docstring. A bundle is different: PROJECT_ROOT is the read-only
    _internal directory, and the overlay still writes here at runtime,
    because glyphs.TextWatcher saves unreadable notification lines for
    later harvesting. That is how one of my own smoke-test crops ended up
    shipped inside the 1.0.0 zip. Frozen, captures go with the player's
    data instead."""
    if getattr(sys, "frozen", False):
        return DATA_DIR / "captures"
    return PROJECT_ROOT / "captures"


CAPTURES_DIR = _captures_dir()

CONFIG_PATH = CONFIG_DIR / "config.json"

# Where these things lived before they moved out of the source tree. Kept so
# an existing clone's settings and match history follow the player across,
# rather than a working Loom appearing to forget everything it knew.
LEGACY_CONFIG_PATH = PROJECT_ROOT / "config.json"
LEGACY_STATS_DIR = PROJECT_ROOT / "stats"


# Folders that hold BOTH files Loom ships and files the player adds. That is
# a third kind of path, and neither of the other two fits it: they cannot
# simply move to the data directory, because the shipped ones travel with the
# code, and they cannot stay in the source tree, because an installed or
# frozen copy is read-only and dropping a new build order into Program Files
# is not something to ask of anybody.
#
# So each one is a SEARCH PATH: the player's copy first, then the shipped
# copy. A file the player adds is found; a file with the same name shadows
# the shipped one, which is the natural way to edit a build order Loom ships
# without touching the installation.
SHARED_ASSET_DIRS = ("builds", "icons", "master_aoe2_images")


def asset_search_path(kind):
    """Where to look for one kind of add-your-own asset, best place first."""
    return (DATA_DIR / kind, PROJECT_ROOT / kind)


def find_asset(kind, name):
    """The first existing `name` across the search path, or None."""
    for base in asset_search_path(kind):
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def asset_files(kind, pattern="*"):
    """Every asset of one kind, as {name: path}, the player's copy winning.

    Built shipped-first and then overwritten by the player's, so a file the
    player added under the same name shadows the one Loom ships rather than
    appearing twice.
    """
    found = {}
    for base in reversed(asset_search_path(kind)):
        if not base.is_dir():
            continue
        for path in sorted(base.glob(pattern)):
            found[path.name] = path
    return found


def user_asset_dir(kind):
    """Where a player's own copy of one kind of asset goes, created on ask.

    This is where the launcher should write a build order the player adds,
    and what to tell somebody who asks where to put one.
    """
    directory = DATA_DIR / kind
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def for_display(path):
    """A path in the shortest form that still says where the file is.

    Relative to the project root when it is inside it - which keeps the
    familiar short "captures/run_.../frame.png" in tool output - and the full
    path otherwise.

    This exists because `path.relative_to(PROJECT_ROOT)` RAISES for a path
    that is not underneath it, and that is now the normal case: statistics
    live in the OS's data directory. The overlay was printing exactly that on
    every clean exit, from inside an aboutToQuit slot, where PyQt cannot
    propagate a Python exception out through C++ signal emission - so instead
    of a traceback the whole process aborted with STATUS_STACK_BUFFER_OVERRUN
    after the stats file had been written. A one-line convenience that turns
    into a crash at exit is worth a named function.
    """
    path = Path(path)
    try:
        return path.relative_to(PROJECT_ROOT)
    except ValueError:
        return path


def migrate_legacy_writables():
    """Bring settings and statistics across from the source tree, once.

    Returns a list of one-line descriptions of what it did, so a caller can
    print them; an empty list means there was nothing to do, which is the
    normal case on every run after the first.

    COPIES rather than moves, and never overwrites. A move would be tidier
    and is the wrong trade: if this migration is ever wrong, a copy leaves the
    original where the player can still get at it, and the old files are
    gitignored and harmless where they are. Nothing here is destructive, so
    running it twice does nothing the second time.

    Failure is reported, not raised. A player whose settings could not be
    copied should get Loom with default settings and a line saying so, not a
    program that refuses to start.
    """
    done = []

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as problem:
        return [f"could not create {DATA_DIR}: {problem}"]

    if LEGACY_CONFIG_PATH.exists() and not CONFIG_PATH.exists():
        try:
            shutil.copy2(LEGACY_CONFIG_PATH, CONFIG_PATH)
            done.append(f"settings copied to {CONFIG_PATH}")
        except OSError as problem:
            done.append(f"could not copy settings: {problem}")

    # Only when the new home is empty. Past that the player has a history
    # here and re-copying could resurrect games they deleted.
    if LEGACY_STATS_DIR.is_dir() and not any(STATS_DIR.glob("*.json")):
        copied = 0
        for game in sorted(LEGACY_STATS_DIR.glob("*.json")):
            try:
                shutil.copy2(game, STATS_DIR / game.name)
                copied += 1
            except OSError:
                pass
        if copied:
            done.append(f"{copied} saved games copied to {STATS_DIR}")

    return done

# Optional resource icons the player can drop in (wood.png, food.png, ...).
# Loom ships none - they are game art - so this folder may not exist, and the
# overlay falls back to words when an icon is missing.
ICONS_DIR = PROJECT_ROOT / "icons"

# The personal icon library (gitignored, ~400 game images keyed by the same
# relative paths the build orders' @icon@ tokens use). Everything that draws
# from it degrades to words when it is absent, so a fresh clone still works.
ICON_LIBRARY_DIR = PROJECT_ROOT / "master_aoe2_images"
