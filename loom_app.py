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
import traceback

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMessageBox

from loom import entry, paths
from loom.launcher import LauncherWindow


def report_crash(kind, value, trace):
    """Show an unhandled exception instead of dying quietly.

    This exists because of how a packaged Loom fails. PyQt6 aborts the
    process on an unhandled exception inside a slot, and loom.spec builds
    with console=False - so there is no stderr anywhere a player can see,
    and a crash looks exactly like the window vanishing for no reason. A
    tester reported three of six developer buttons doing precisely that and
    had nothing to send me but the sentence "it closes".

    A message box is not a fix for any particular bug; it is the difference
    between a bug report and a shrug. Printed as well, for a clone run from
    a terminal and for the launcher's own output pane when this is a child.
    """
    traceback.print_exception(kind, value, trace)
    try:
        QMessageBox.critical(
            None, "Loom has hit a problem",
            "Loom hit an error it did not expect and may not work properly"
            " from here on.\n\n"
            f"{kind.__name__}: {value}\n\n"
            "If you are reporting this, the full details are below.",
            QMessageBox.StandardButton.Ok)
    except Exception:
        # A dialog needs a QApplication, and the crash may BE the
        # application. The traceback above is already out; never let the
        # reporter become the second failure.
        pass


def main():
    # Before anything else, the launcher and every child alike. It used to sit
    # below the --mode dispatch, which returns - so the children, the only
    # processes that ever crashed in front of a tester, were the ones it never
    # covered. A capture failure in the packaged overlay came out as
    # PyInstaller's raw "Unhandled exception in script" dialog on top of the
    # game. See report_crash.
    sys.excepthook = report_crash

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
    # Sized against the screen it is opening on rather than to a fixed
    # number, and restored to where it was left. The window knows its own
    # screen and its own remembered geometry, so both live there - see
    # LauncherWindow.fit_to_screen.
    window.fit_to_screen()
    window.show()
    # After show(), so the How-to-use window opens in front of a launcher
    # that already exists rather than racing it. Only ever on a fresh
    # install; it marks itself seen on the way out.
    window.show_about_if_unseen()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
