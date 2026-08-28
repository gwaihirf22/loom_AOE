"""
Loom — the Linux package says the same things the code does.

Packaging is where facts get written down a second time, and a second copy
is a copy that can disagree. The app id alone appears in five places, none
of which imports any of the others: the Flatpak manifest, the AppStream
metainfo, the .desktop basename, the icon basename, and entry.DESKTOP_ID.
Four of the five are read by a desktop shell rather than by Python, so
nothing raises when they drift - the symptom is a grey placeholder in the
taskbar, or a store listing that will not install, and both look like
somebody else's bug.

That is the shape CLAUDE.md names: one question answered in two places,
answered differently, in silence. The corrective it prescribes is a test
that WALKS the source of truth rather than agreeing with it - so the asset
check below reads loom/paths.py's own constants and asks the manifest about
each, instead of listing the directories again here and drifting alongside.

Deliberately text-based. The manifest is YAML and parsing it would be
tidier, but that would add a dependency to the suite for one file, and the
questions asked here (does this string appear, does that one not) are ones
a text scan cannot be out-clevered on. The same reasoning as the guard in
test_civ_reference.py.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import xml.etree.ElementTree as ET

import pytest

from loom import paths
from loom.entry import DESKTOP_ID

PACKAGING = paths.PROJECT_ROOT / "packaging" / "linux"
MANIFEST = PACKAGING / f"{DESKTOP_ID}.yml"
METAINFO = PACKAGING / f"{DESKTOP_ID}.metainfo.xml"
DESKTOP = PACKAGING / f"{DESKTOP_ID}.desktop"
ICON = PACKAGING / f"{DESKTOP_ID}.png"


def manifest_text():
    return MANIFEST.read_text(encoding="utf-8")


def installed_directories():
    """The directories the manifest actually copies into the package.

    Read off the `cp -r ... /app/share/loom/` command rather than searched
    for anywhere in the file. The first version of this asked whether each
    name appeared in the manifest TEXT, and every name did - including
    `captures`, which appears in a comment explaining why captures do not
    ship. The check passed by matching its own prose, which is the fault
    CLAUDE.md describes as the statistics measuring themselves.
    """
    text = manifest_text()
    start = text.index("- cp -r")
    end = text.index("/app/share/loom/", start)
    words = text[start + len("- cp -r"):end].split()
    return {word for word in words if not word.endswith(".py")
            and not word.endswith(".txt")}


def finish_args():
    """The permissions the package actually REQUESTS.

    Declarations only, not every mention. The manifest explains at length
    why --device=input is absent, and a scan of the whole text reads that
    explanation as the thing it is explaining - so the questions below are
    asked of the list rather than of the prose around it. A finish-arg is a
    list entry: `  - --socket=x11`, and nothing else in the file looks like
    one.
    """
    return [line.strip()[2:] for line in manifest_text().splitlines()
            if line.strip().startswith("- --")]


# ---- the app id, in every place that carries it ---------------------------

@pytest.mark.parametrize("path", [MANIFEST, METAINFO, DESKTOP, ICON],
                         ids=lambda p: p.suffix.lstrip("."))
def test_every_packaging_file_is_named_for_the_app_id(path):
    """The .desktop and .png basenames are not decoration: Icon= in a
    desktop entry is resolved by basename against the icon theme, so the
    file name IS the lookup."""
    assert path.exists(), f"{path.name} is missing from packaging/linux"


def test_the_manifest_declares_the_same_app_id():
    assert f"app-id: {DESKTOP_ID}" in manifest_text()


def test_the_metainfo_declares_the_same_app_id():
    root = ET.parse(METAINFO).getroot()

    assert root.findtext("id") == DESKTOP_ID
    # <launchable> is how a store finds the entry to launch. Pointing it at
    # a desktop file that does not exist installs an app with no way in.
    assert root.findtext("launchable") == f"{DESKTOP_ID}.desktop"


def test_the_desktop_entry_names_the_icon_by_the_app_id():
    text = DESKTOP.read_text(encoding="utf-8")

    assert f"Icon={DESKTOP_ID}" in text
    # StartupWMClass is what an older shell matches a window on; the Qt
    # setDesktopFileName call in entry.app_identity is what a newer one
    # reads. Both name the same id or the taskbar shows two entries.
    assert f"StartupWMClass={DESKTOP_ID}" in text


def test_the_code_hands_that_id_to_qt():
    """Without this the launcher's window inherits the INTERPRETER's
    identity and the shell cannot match it to the entry carrying the icon -
    the Linux half of the problem windows_app_identity was written for."""
    source = (paths.PROJECT_ROOT / "loom" / "entry.py").read_text(
        encoding="utf-8")

    assert "setDesktopFileName(DESKTOP_ID)" in source


# ---- what the package actually contains -----------------------------------

def test_every_shipped_asset_directory_is_installed():
    """Walks paths.py rather than listing the directories again.

    A new shipped asset directory added to paths.py and not to the manifest
    would produce a package that starts, runs, and cannot find its
    templates - and the failure would arrive as a reader that never
    matches, not as a missing file. Asking paths.py for its own answer is
    what makes this fail when the tree grows, instead of months later.
    """
    beside_the_code = {
        value.name
        for value in vars(paths).values()
        if isinstance(value, type(paths.PROJECT_ROOT))
        and value.parent == paths.PROJECT_ROOT
        and value.is_dir()
    }
    assert beside_the_code, "found no directories in paths.py to check"

    # The one exception, and paths.py is where it is decided: captures are
    # development scratch, which is why paths.installed() sends them to the
    # player's data directory rather than here. Named from paths rather
    # than typed, so it follows if it is ever renamed.
    shipped = sorted(beside_the_code - {paths.CAPTURES_DIR.name})

    missing = sorted(set(shipped) - installed_directories())
    assert missing == [], (
        f"packaging/linux does not install {missing}, which loom/paths.py "
        f"expects to find beside the code")


def test_development_scratch_does_not_ship():
    """The other half of the rule above, and the reason it needs its own
    test: captures/ is where tools/ write frames during development, and on
    this machine it holds several gigabytes. Shipping it once already
    happened - a smoke-test crop went out inside the 1.0.0 zip."""
    assert paths.CAPTURES_DIR.name not in installed_directories()


def test_the_tools_package_ships():
    """Not optional on Linux, and the reason is easy to miss: APM is
    tools/apm_counter.py, spawned as a CHILD PROCESS by the launcher
    (loom/apm.py decides which platform counts in-process, and Linux is not
    one). Drop tools/ and APM fails at spawn time with the game running,
    which is the worst possible moment to find out."""
    assert "tools" in manifest_text()


def test_the_licences_travel_with_the_binary():
    """PyQt6 is GPL-3.0-only and a distributed bundle of it is a combined
    work, so this is a licence obligation rather than a nicety.
    tools/package_windows.py refuses to build a zip without these two; the
    same rule has to hold for the Flatpak."""
    text = manifest_text()

    for name in ("LICENSE", "NOTICE"):
        assert f"share/licenses/${{FLATPAK_ID}}/{name}" in text


def test_the_local_build_does_not_sweep_up_the_whole_working_tree():
    """A `dir` source copies everything under it into the build directory
    before any build command runs.

    Two things must not be swept up, for completely different reasons.
    captures/ is development scratch and stands at 150 GB on this machine,
    so copying it turns a two-minute build into an afternoon. reference/ is
    the game's own design transcribed - private, and stripped from every
    public snapshot by tools/release.py - so naming it here is what makes a
    LOCAL build from the private tree see what a Flathub build from the
    public tarball would.

    Neither could reach the package itself: the install command is
    explicit. This is about the copy that happens before it."""
    text = manifest_text()
    start = text.index("skip:")
    skipped = {line.strip()[2:] for line in text[start:].splitlines()[1:]
               if line.strip().startswith("- ")}

    for name in (paths.CAPTURES_DIR.name, "reference", ".git", ".venv"):
        assert name in skipped, f"a local build would copy {name}"


# ---- the permissions are the ones that were argued for --------------------

def test_the_package_does_not_ask_to_read_input_devices():
    """The claim in loom/hotkeys/__init__.py is that Loom is handed the few
    combinations it grabbed and cannot learn about a key it did not ask
    for. APM is XInput2 raw events over the X socket, not /dev/input.

    Pinned because --device=input is the permission a reviewer and a player
    both look for, and because it is the kind of thing that gets added
    while debugging something else and never taken out again."""
    assert "--device=input" not in finish_args()
    assert "--device=all" not in finish_args()


def test_the_package_does_not_ask_for_the_whole_home_directory():
    """Loom reads one save folder, after a match has ended. Flathub asks
    for static permissions to be kept to a minimum and --filesystem=home is
    the usual way that promise quietly stops being true."""
    granted = finish_args()

    assert "--filesystem=home" not in granted
    assert "--filesystem=host" not in granted


def test_the_recorded_game_holes_are_read_only():
    """CLAUDE.md's rule is that a record may be read, only after the game
    has ended. Nothing in Loom writes one, so a writable hole would be a
    permission granted for no purpose at all."""
    holes = [arg for arg in finish_args() if arg.startswith("--filesystem=")]

    assert holes, "the recorded-game holes vanished from the manifest"
    for hole in holes:
        assert hole.endswith(":ro"), f"{hole} is not read-only"


def test_the_x11_socket_is_asked_for_by_name():
    """fallback-x11 would satisfy a reviewer and break Loom: it is for apps
    that PREFER Wayland, and Loom has to be on the same X server the game
    is, because that is where the pixels and the key grabs are."""
    granted = finish_args()

    assert "--socket=x11" in granted
    assert "--socket=fallback-x11" not in granted


# ---- the store listing ----------------------------------------------------

def test_every_screenshot_the_listing_promises_exists():
    """The metainfo points a store at raw.githubusercontent URLs, which
    resolve against the PUBLIC repository - so a screenshot renamed here
    becomes a broken image on the listing, and nothing local would notice.
    Checked by filename against images/, which is what gets published."""
    root = ET.parse(METAINFO).getroot()
    images = [node.text for node in root.findall("screenshots/screenshot/image")]

    assert images, "a graphical app needs at least one screenshot"
    for url in images:
        name = url.rsplit("/", 1)[-1]
        assert (paths.IMAGES_DIR / name).exists(), \
            f"{name} is promised to the store but not in images/"


def test_the_licence_the_listing_declares_is_the_one_that_binds():
    """GPL-3.0-only, not -or-later. PyQt6 is GPL-3.0-only, so -or-later
    would be claiming a freedom Loom cannot grant."""
    root = ET.parse(METAINFO).getroot()

    assert root.findtext("project_license") == "GPL-3.0-only"
