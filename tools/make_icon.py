"""
Loom — regenerate the application icon from the logo (development tool).

    python -m tools.make_icon

Turns images/loom_logo.png into two things, one per platform, because the
desktops want different shapes:

  * images/loom.ico - the multi-resolution icon Windows wants. The taskbar,
    the title bar, alt-tab and the eventual installer each pick their own
    size, and a single-size icon looks blurry wherever it has to resample.
  * packaging/linux/<app-id>.png - one 256x256 PNG, which is what the
    freedesktop icon theme spec and Flathub ask for. Named for the app id
    rather than for Loom, because that is how a desktop file's Icon= key is
    resolved: the basename IS the lookup.

Both are committed, so this only needs running when the logo changes.

Needs Pillow (pip install pillow) - a development dependency only, which is
why it is not in requirements.txt: Loom itself never generates icons.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PIL import Image

from loom import paths
from loom.entry import DESKTOP_ID

# The sizes Windows actually uses, per its own guidelines. 256 is stored
# PNG-compressed inside the .ico, which keeps the file small.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
         (256, 256)]

# The app id, which has to match the .desktop basename, the metainfo <id>,
# the Flatpak manifest and loom.entry.DESKTOP_ID. Read from entry rather
# than typed again here, so there is one place it can be wrong.
LINUX_ICON = (paths.PROJECT_ROOT / "packaging" / "linux"
              / f"{DESKTOP_ID}.png")

# Freedesktop's icon theme spec sizes; 256 is the one Flathub asks for and
# the one every current shell picks.
LINUX_SIZE = (256, 256)


def main():
    logo = Image.open(paths.LOGO_PATH)

    target = paths.ICON_PATH
    logo.save(target, format="ICO", sizes=SIZES)
    print(f"wrote {target} ({target.stat().st_size} bytes, "
          f"{len(SIZES)} sizes)")

    # LANCZOS because the logo is 1254px square and this is a large
    # reduction; the default resample visibly softens the loom threads.
    LINUX_ICON.parent.mkdir(parents=True, exist_ok=True)
    logo.resize(LINUX_SIZE, Image.Resampling.LANCZOS).save(
        LINUX_ICON, format="PNG")
    print(f"wrote {LINUX_ICON} ({LINUX_ICON.stat().st_size} bytes, "
          f"{LINUX_SIZE[0]}x{LINUX_SIZE[1]})")


if __name__ == "__main__":
    main()
