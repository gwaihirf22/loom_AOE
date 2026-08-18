"""
Loom — the launcher.

    python loom_app.py

Pick a build order, start the overlay, adjust the alerts. Developer mode
(a checkbox in the window) adds the debug tools and the test runner.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# Unlike loom_overlay.py, this deliberately does NOT set QT_QPA_PLATFORM=xcb.
# The overlay needs XWayland so "keep above" works over the game; the
# launcher is an ordinary window with no such need, so it runs as a native
# Wayland client. The overlay child sets the variable for itself.

import sys

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from loom import entry, paths
from loom.launcher import LauncherWindow


def main():
    # One executable, four programs. Packaged, this file IS Loom and the
    # launcher starts its children as `Loom.exe --mode overlay`; from a clone
    # the scripts are still run directly and this never fires. Checked before
    # anything else so a child pays for none of the launcher's setup.
    mode, rest = entry.split_mode(sys.argv[1:])
    if mode is not None:
        return entry.run(mode, rest)

    # Settings and match history live outside the source tree now, so an
    # existing clone's are brought across the first time. Idempotent: after
    # that first run this is two stat() calls and nothing else.
    for note in paths.migrate_legacy_writables():
        print(note)

    entry.windows_app_identity()
    app = QApplication(sys.argv)
    # The application-wide icon: every window this process opens - launcher,
    # preview, statistics, How-to-use - inherits it. The .ico carries seven
    # sizes so the title bar, taskbar and alt-tab each get a crisp one.
    app.setWindowIcon(QIcon(str(paths.ICON_PATH)))
    window = LauncherWindow()
    # The build preview is its own window now and sizes itself, so the
    # launcher is always the compact single column.
    # Taller since the hotkeys box joined the settings column; without
    # this the output pane is squeezed to a couple of lines.
    window.resize(720, 840)
    window.show()
    # After show(), so the How-to-use window opens in front of a launcher
    # that already exists rather than racing it. Only ever on a fresh
    # install; it marks itself seen on the way out.
    window.show_about_if_unseen()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
