"""
Loom — where writable data goes, and how it got there.

Settings and match history used to live in the source tree. That was fine
while Loom was only ever run from a git clone, and wrong for every other way
of running it: installed, the tree is read-only; frozen into a one-file
bundle, it is a temporary directory deleted on exit, so a match's statistics
would be written and then destroyed.

Two things are checked here. That each OS is given the directory it actually
uses - a Linux user backing up ~/.config expects to find settings in it, and a
Windows user does not expect a dotfile in their home directory. And that the
migration brings an existing clone's data across without ever destroying it,
because a player whose settings and match history silently vanished would
reasonably conclude the update broke Loom.

The platform functions take the platform as an argument rather than reading
sys.platform, so all three answers are checked from whichever machine happens
to be running - the same reason the Windows backend keeps its imports inside
functions.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
from pathlib import Path

import pytest

from loom import paths


# ---- which directory each OS gets -----------------------------------------

def test_windows_uses_the_roaming_profile(monkeypatch):
    """APPDATA, not LOCALAPPDATA: settings should follow the user between
    machines on a domain, which is what roaming means."""
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")

    assert paths.data_home("win32") == Path(
        r"C:\Users\someone\AppData\Roaming") / "Loom"


def test_windows_without_appdata_still_answers(monkeypatch):
    """A missing APPDATA means a strange environment, not a reason to refuse
    to save anything ever again."""
    monkeypatch.delenv("APPDATA", raising=False)

    assert paths.data_home("win32").parts[-3:] == (
        "AppData", "Roaming", "Loom")


def test_macos_uses_application_support(monkeypatch):
    assert paths.data_home("darwin") == (
        Path.home() / "Library" / "Application Support" / "Loom")


def test_linux_uses_xdg_data_home(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/home/someone/.local/share")

    assert paths.data_home("linux") == Path("/home/someone/.local/share/loom")


def test_linux_falls_back_to_the_xdg_default(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    assert paths.data_home("linux") == Path.home() / ".local" / "share" / "loom"


def test_a_relative_xdg_value_is_ignored(monkeypatch):
    """The spec says a relative XDG value is invalid and must be ignored, and
    obeying that is worth it here: a stray XDG_DATA_HOME=. would otherwise
    put the player's match history in whatever directory Loom was started
    from, which is the exact bug loom/paths.py exists to prevent."""
    monkeypatch.setenv("XDG_DATA_HOME", "relative/path")

    assert paths.data_home("linux").is_absolute()
    assert paths.data_home("linux") == Path.home() / ".local" / "share" / "loom"


def test_linux_splits_settings_from_data(monkeypatch):
    """Honoured rather than flattened: on Linux these really are two
    different directories and users expect them to be."""
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/someone/.config")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/someone/.local/share")

    assert paths.config_home("linux") != paths.data_home("linux")
    assert paths.config_home("linux") == Path("/home/someone/.config/loom")


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macos_keep_settings_with_data(platform, monkeypatch):
    """Both conventions put one directory per application, so splitting them
    would be inventing a distinction neither OS makes."""
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")

    assert paths.config_home(platform) == paths.data_home(platform)


def test_captures_deliberately_stay_in_the_source_tree():
    """Scratch written by tools/ during development, not the player's data -
    so this one does NOT move. Stated as a test because it looks like an
    oversight otherwise."""
    assert paths.CAPTURES_DIR.parent == paths.PROJECT_ROOT


def test_read_only_assets_stay_anchored_to_the_source_tree():
    for shipped in (paths.TEMPLATES_DIR, paths.BUILDS_DIR, paths.ICONS_DIR):
        assert shipped.parent == paths.PROJECT_ROOT


# ---- printing a path without crashing --------------------------------------

def test_a_path_inside_the_tree_is_shown_relative():
    """Tool output stays as short as it always was."""
    inside = paths.CAPTURES_DIR / "run_123" / "frame.png"

    assert paths.for_display(inside) == Path("captures/run_123/frame.png")


def test_a_path_outside_the_tree_is_shown_in_full():
    """The case that used to raise. Statistics now live in the OS's data
    directory, which is not under the project root, so asking for it relative
    to the root is a ValueError - and the overlay was doing exactly that on
    every clean exit, from inside an aboutToQuit slot where PyQt cannot
    propagate a Python exception out through C++ signal emission. The process
    aborted with STATUS_STACK_BUFFER_OVERRUN after the stats file had been
    written, so the only symptom was a nonzero exit code."""
    outside = Path(r"C:\Users\someone\AppData\Roaming\Loom\stats\game.json")

    assert paths.for_display(outside) == outside


def test_the_real_stats_directory_can_always_be_printed():
    """The specific path that crashed, wherever this test happens to run."""
    assert paths.for_display(paths.STATS_DIR / "2026-01-01_build.json")


# ---- the migration --------------------------------------------------------

@pytest.fixture
def relocated(tmp_path, monkeypatch):
    """Point every writable path at a scratch directory.

    Both the old locations and the new ones, so a test can lay out a
    convincing "existing clone" without touching the real one.
    """
    new, old = tmp_path / "new", tmp_path / "old"
    new.mkdir()
    old.mkdir()

    monkeypatch.setattr(paths, "DATA_DIR", new)
    monkeypatch.setattr(paths, "CONFIG_DIR", new)
    monkeypatch.setattr(paths, "STATS_DIR", new / "stats")
    monkeypatch.setattr(paths, "CONFIG_PATH", new / "config.json")
    monkeypatch.setattr(paths, "LEGACY_CONFIG_PATH", old / "config.json")
    monkeypatch.setattr(paths, "LEGACY_STATS_DIR", old / "stats")
    return new, old


def test_a_fresh_install_has_nothing_to_migrate(relocated):
    new, _ = relocated

    assert paths.migrate_legacy_writables() == []
    assert (new / "stats").is_dir(), "the directories should still be made"


def test_settings_come_across(relocated):
    new, old = relocated
    (old / "config.json").write_text(json.dumps({"active_build": "arena"}))

    notes = paths.migrate_legacy_writables()

    assert len(notes) == 1
    moved = json.loads((new / "config.json").read_text())
    assert moved == {"active_build": "arena"}


def test_the_original_is_left_alone(relocated):
    """A copy, not a move. If this migration is ever wrong, the player can
    still get at their old file; the old ones are gitignored and harmless
    where they are."""
    _, old = relocated
    (old / "config.json").write_text("{}")

    paths.migrate_legacy_writables()

    assert (old / "config.json").exists()


def test_existing_settings_are_never_overwritten(relocated):
    """The destructive case, and the one worth being certain about."""
    new, old = relocated
    (old / "config.json").write_text(json.dumps({"active_build": "old"}))
    (new / "config.json").write_text(json.dumps({"active_build": "current"}))

    paths.migrate_legacy_writables()

    assert json.loads((new / "config.json").read_text()) == {
        "active_build": "current"}


def test_saved_games_come_across(relocated):
    new, old = relocated
    (old / "stats").mkdir()
    for name in ("2026-01-01_fast_castle.json", "2026-01-02_arena.json"):
        (old / "stats" / name).write_text("{}")

    notes = paths.migrate_legacy_writables()

    assert len(notes) == 1
    assert sorted(p.name for p in (new / "stats").glob("*.json")) == [
        "2026-01-01_fast_castle.json", "2026-01-02_arena.json"]


def test_saved_games_are_not_recopied_once_there_is_a_history(relocated):
    """Only when the new home is empty. Past that the player has a history
    here, and re-copying could resurrect games they deleted."""
    new, old = relocated
    (old / "stats").mkdir()
    (old / "stats" / "deleted.json").write_text("{}")
    (new / "stats").mkdir(parents=True, exist_ok=True)
    (new / "stats" / "kept.json").write_text("{}")

    paths.migrate_legacy_writables()

    assert not (new / "stats" / "deleted.json").exists()


def test_running_it_twice_does_nothing_the_second_time(relocated):
    """It runs on every start, so being idempotent is not a nicety."""
    _, old = relocated
    (old / "config.json").write_text("{}")
    (old / "stats").mkdir()
    (old / "stats" / "game.json").write_text("{}")

    first = paths.migrate_legacy_writables()
    second = paths.migrate_legacy_writables()

    assert first, "the first run should have done something"
    assert second == []


def test_an_unwritable_destination_reports_rather_than_raises(
        relocated, monkeypatch):
    """A player whose data could not be copied should get Loom with default
    settings and a line saying so, not a program that refuses to start."""
    def refuse(*_args, **_kwargs):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", refuse)

    notes = paths.migrate_legacy_writables()

    assert len(notes) == 1
    assert "could not create" in notes[0]


# ---- folders that hold both shipped files and the player's ------------------
#
# Build orders above all: loading community ones is the entire reason the RTS
# Overlay format was adopted, and in an installed or frozen copy the shipped
# folder is read-only. So each of these is a search path rather than a place.

def test_the_player_s_copy_is_searched_first(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    first, second = paths.asset_search_path("builds")

    assert first == tmp_path / "data" / "builds"
    assert second == tmp_path / "shipped" / "builds"


def test_a_shipped_asset_is_found(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped" / "builds"
    shipped.mkdir(parents=True)
    (shipped / "fast_castle.json").write_text("{}")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    assert paths.find_asset("builds", "fast_castle.json") == (
        shipped / "fast_castle.json")


def test_the_player_s_own_file_shadows_a_shipped_one(tmp_path, monkeypatch):
    """The natural way to edit a build order Loom ships without touching the
    installation - which may well be read-only."""
    for where in ("shipped", "data"):
        (tmp_path / where / "builds").mkdir(parents=True)
        (tmp_path / where / "builds" / "fast_castle.json").write_text(where)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    found = paths.find_asset("builds", "fast_castle.json")

    assert found.read_text() == "data"


def test_listing_merges_both_places_without_duplicates(tmp_path, monkeypatch):
    (tmp_path / "shipped" / "builds").mkdir(parents=True)
    (tmp_path / "data" / "builds").mkdir(parents=True)
    (tmp_path / "shipped" / "builds" / "a.json").write_text("shipped")
    (tmp_path / "shipped" / "builds" / "b.json").write_text("shipped")
    (tmp_path / "data" / "builds" / "b.json").write_text("mine")
    (tmp_path / "data" / "builds" / "c.json").write_text("mine")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    found = paths.asset_files("builds", "*.json")

    assert sorted(found) == ["a.json", "b.json", "c.json"]
    assert found["b.json"].read_text() == "mine", "the player's copy must win"


def test_a_missing_asset_is_none_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    assert paths.find_asset("builds", "nothing.json") is None
    assert paths.asset_files("builds", "*.json") == {}


def test_the_place_to_add_files_is_created_on_request(tmp_path, monkeypatch):
    """What the launcher should write into, and what to tell somebody who
    asks where to put a build order."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")

    directory = paths.user_asset_dir("builds")

    assert directory == tmp_path / "data" / "builds"
    assert directory.is_dir()


def test_every_shared_folder_is_under_the_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "shipped")

    for kind in paths.SHARED_ASSET_DIRS:
        assert paths.asset_search_path(kind)[0].parent == tmp_path / "data"


# ---- where Loom's own files are, packaged or not ---------------------------

def test_from_a_clone_the_root_is_the_source_tree():
    assert (paths.PROJECT_ROOT / "loom" / "paths.py").exists()


def test_frozen_onefile_uses_the_extraction_directory(monkeypatch):
    """PyInstaller's onefile mode unpacks the data files to a temporary
    directory and names it in sys._MEIPASS. __file__ points inside the
    bundle's archive and is no use for finding templates."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "_MEIPASS", r"C:\Temp\_MEI123",
                        raising=False)

    assert paths._install_root() == Path(r"C:\Temp\_MEI123")


def test_frozen_onedir_uses_the_executable_s_folder(monkeypatch):
    """onedir has no _MEIPASS - the data sits beside the executable."""
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.delattr(paths.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(Path.cwd() / "Loom.exe"))

    assert paths._install_root() == Path.cwd()


# ---- branding ---------------------------------------------------------------

def test_the_logo_and_its_icon_ship():
    """Both halves of the brand: the PNG is the source of truth, the .ico is
    what Windows and the eventual installer consume. tools/make_icon.py
    regenerates the second from the first."""
    assert paths.LOGO_PATH.exists()
    assert paths.ICON_PATH.exists()
    assert paths.ICON_PATH.stat().st_size > 0


def test_the_icon_really_is_an_ico():
    """The four magic bytes: a renamed PNG would load in Qt and then fail
    only inside PyInstaller, at release time, which is the worst moment."""
    header = paths.ICON_PATH.read_bytes()[:4]
    assert header == b"\x00\x00\x01\x00"
