"""
Loom — regenerate the application icon from the logo (development tool).

    python -m tools.make_icon

Turns images/loom_logo.png into images/loom.ico, the multi-resolution icon
Windows wants: the taskbar, the title bar, alt-tab and the eventual
installer each pick their own size, and a single-size icon looks blurry in
whichever spot resamples it. The .ico is committed, so this only needs
running when the logo itself changes.

Needs Pillow (pip install pillow) - a development dependency only, which is
why it is not in requirements.txt: Loom itself never generates icons.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PIL import Image

from loom import paths

# The sizes Windows actually uses, per its own guidelines. 256 is stored
# PNG-compressed inside the .ico, which keeps the file small.
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128),
         (256, 256)]


def main():
    logo = Image.open(paths.LOGO_PATH)
    target = paths.ICON_PATH
    logo.save(target, format="ICO", sizes=SIZES)
    print(f"wrote {target} ({target.stat().st_size} bytes, "
          f"{len(SIZES)} sizes)")


if __name__ == "__main__":
    main()
