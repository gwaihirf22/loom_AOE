"""
Loom — the overlay.

Reads the game and draws the current build order step on top of it.

    python loom_overlay.py
    python loom_overlay.py --build fast_castle
    python loom_overlay.py --demo          # no game needed, replays a match
    python loom_overlay.py --place         # drag it where you want it

Ctrl+C in the terminal to stop.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os

# Must be set before Qt starts. On Wayland a client is not allowed to raise
# itself above other windows, so the overlay would never appear over the game.
# Running through XWayland puts it in the same X server as the game, where
# "keep above" still means something. Doing it here so the program can just be
# run, rather than relying on remembering an environment variable.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import argparse
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from loom import capture, config, overlay, pace, reader
from loom import session
from loom.build_order import BuildOrder

POLL_INTERVAL_MS = 300

# Where to put the panel when the player has not chosen a spot yet: below the
# resource bar, so it never sits on top of the numbers being read.
PANEL_TOP_MARGIN = 96


def game_geometry(window, display):
    """Absolute position and size of the game window on the desktop.

    get_geometry() is relative to the parent window, so I ask X to translate
    into root coordinates - otherwise the panel lands on the wrong monitor.
    """
    geometry = window.get_geometry()
    root = display.screen().root
    translated = window.translate_coords(root, 0, 0)
    return (-translated.x, -translated.y, geometry.width, geometry.height)


def default_offset(panel, width):
    """Where the panel goes before the player has moved it."""
    return ((width - panel.width()) // 2, PANEL_TOP_MARGIN)


def place_panel(panel, origin_x, origin_y, width):
    """Put the panel at the saved offset from the game window's corner.

    The offset is stored relative to the game rather than to the desktop, so
    it survives a resolution change or the game moving to another monitor.
    """
    offset = config.overlay_offset() or default_offset(panel, width)
    panel.move(origin_x + offset[0], origin_y + offset[1])
    return offset


class LiveController:
    """Polls the game and pushes each reading into the panel."""

    def __init__(self, panel, build, hud):
        self.panel = panel
        self.build = build
        self.hud = hud
        self.pace = pace.PaceTracker(build)

    def tick(self):
        reading = self.hud.poll()

        # A new match means starting the build again, so the pace tracker must
        # forget how late the last game was.
        if reading.event == session.GAME_STARTED:
            self.pace.reset()

        if not reading.is_usable():
            self.panel.show_waiting("waiting for the game...")
            return

        villagers = reading.villagers
        game_time = reading.game_time

        self.panel.show_step(
            self.build,
            villagers,
            game_time,
            self.build.active_step(villagers, game_time),
            self.build.following_step(villagers, game_time),
            self.pace.update(villagers, game_time),
            reading.per_resource,
        )


class DemoController:
    """Replays a match with no game running, so the panel can be checked."""

    def __init__(self, panel, build, speed=20):
        self.panel = panel
        self.build = build
        self.speed = speed
        self.moment = 0
        self.last = max(s.time for s in build.steps if s.time is not None)
        self.pace = pace.PaceTracker(build)

    def tick(self):
        # Each tick advances game time by however many seconds the chosen
        # speed implies, so a whole match plays out in about a minute.
        self.moment += self.speed * (POLL_INTERVAL_MS / 1000.0)
        if self.moment > self.last + 60:
            self.moment = 0
            self.pace.reset()

        villagers = villagers_following_build(self.build, self.moment)
        self.panel.show_step(
            self.build,
            villagers,
            self.moment,
            self.build.active_step(villagers, self.moment),
            self.build.following_step(villagers, self.moment),
            self.pace.update(villagers, self.moment),
        )


def villagers_following_build(build, moment):
    """Villager count for a player following the build exactly."""
    first = build.steps[0]
    if first.time and moment < first.time:
        return int(3 + (moment / first.time) * (first.villager_count - 3))

    expected = build.expected_villagers(moment)
    return 3 if expected is None else int(expected)


def screen_origin(app):
    """Fallback origin when there is no game window: the primary screen.

    Hardcoding a desktop coordinate does not work here. On this desktop the
    primary screen starts at y=1085 and the second at x=2560, so a position
    like (200, 200) is not on any screen at all - which is exactly the bug
    that made demo mode appear to show nothing.
    """
    geometry = app.primaryScreen().geometry()
    return geometry.x(), geometry.y(), geometry.width()


def remember_position(panel, origin_x, origin_y):
    """Save where the player dragged the panel to."""
    dx = panel.x() - origin_x
    dy = panel.y() - origin_y
    config.set_overlay_offset(dx, dy)
    print(f"Saved overlay offset ({dx}, {dy}) to {config.CONFIG_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Loom overlay")
    parser.add_argument("--build", default="fast_castle")
    parser.add_argument("--demo", action="store_true",
                        help="replay a match instead of reading the game")
    parser.add_argument("--place", action="store_true",
                        help="drag the panel where you want it, then close it")
    parser.add_argument("--speed", type=float, default=20.0,
                        help="demo only: simulated seconds per real second")
    args = parser.parse_args()

    build = BuildOrder.load_by_name(args.build)
    for problem in build.validate():
        print(f"warning: {problem}")

    app = QApplication(sys.argv)
    panel = overlay.Overlay(placing=args.place)

    # Where the panel's offset is measured from: the game window if there is
    # one, otherwise the primary screen.
    origin_x, origin_y, area_width = screen_origin(app)

    if args.demo:
        controller = DemoController(panel, build, args.speed)
        print(f"Demo mode at {args.speed}x. Ctrl+C to stop.")
    else:
        hud = reader.HudReader()
        if not hud.connect():
            print("Could not find the Age of Empires II window. Is the game running?")
            return

        print("Looking for the HUD...")
        if not hud.find_hud():
            print("Could not find the HUD. Are you in a game rather than a menu?")
            return
        print(f"HUD found (match {hud.hud['score']:.3f}, scale {hud.hud['scale']:.2f})")

        display = capture.open_display()
        window = capture.find_game_window(display)
        origin_x, origin_y, area_width, _ = game_geometry(window, display)

        controller = LiveController(panel, build, hud)
        print(f"Overlay running on '{build.name}'. Ctrl+C to stop.")

    offset = place_panel(panel, origin_x, origin_y, area_width)

    if args.place:
        panel.show_step(build, 13, 250,
                        build.active_step(13, 250),
                        build.following_step(13, 250),
                        0)
        print(f"Placement mode. Current offset from the game corner: {offset}")
        print("Drag the window where you want it, then close it to save.")
        app.aboutToQuit.connect(
            lambda: remember_position(panel, origin_x, origin_y))
        panel.show()
        app.exec()
        return

    panel.show()

    timer = QTimer()
    timer.timeout.connect(controller.tick)
    timer.start(POLL_INTERVAL_MS)

    # Without this, Ctrl+C in the terminal is not noticed while Qt is idle,
    # because the interpreter only runs signal handlers between bytecodes.
    idle = QTimer()
    idle.timeout.connect(lambda: None)
    idle.start(200)

    try:
        app.exec()
    except KeyboardInterrupt:
        pass
    print("\nStopped.")


if __name__ == "__main__":
    main()
