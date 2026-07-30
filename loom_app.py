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

from PyQt6.QtWidgets import QApplication

from loom.launcher import LauncherWindow


def main():
    app = QApplication(sys.argv)
    window = LauncherWindow()
    # The build preview is its own window now and sizes itself, so the
    # launcher is always the compact single column.
    window.resize(720, 640)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
