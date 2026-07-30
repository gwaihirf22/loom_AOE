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
import datetime
import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from loom import alerts, build_order, capture, config, gamestats, overlay
from loom import pace, passthrough, paths, production, reader, report
from loom import session, statefeed
from loom.build_order import BuildOrder

POLL_INTERVAL_MS = 300

# Where to put the panel when the player has not chosen a spot yet: below the
# resource bar, so it never sits on top of the numbers being read.
PANEL_TOP_MARGIN = 96


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


def stats_path(build_stem):
    """Where this game's statistics file goes: timestamped, per match."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return paths.STATS_DIR / f"{stamp}_{build_stem}.json"


def start_recorder(build_stem, build_name):
    """A fresh GameRecorder and the file it will write to."""
    recorder = gamestats.GameRecorder(
        build_stem, build_name, datetime.datetime.now().isoformat(timespec="seconds"))
    return recorder, stats_path(build_stem)


class LiveController:
    """Polls the game and pushes each reading into the panel."""

    def __init__(self, panel, build, hud, build_stem="unknown"):
        self.panel = panel
        self.build = build
        self.hud = hud
        self.build_stem = build_stem
        self.pace = pace.PaceTracker(build)
        self.production = production.ProductionTracker()
        # The player's own thresholds for when the idle-TC warning softens
        # and shuts off, from config.json; defaults suit a standard game.
        self.policy = alerts.IdleTcPolicy(*config.idle_tc_limits())
        # Which alert families the player wants at all. Read once at startup:
        # settings changed in the launcher apply on the next overlay launch,
        # which keeps the alert logic pure and this loop free of file reads.
        self.toggles = alerts.AlertToggles(**config.alert_toggles())
        # The player's own HOUSE NOW threshold, same read-once contract.
        self.house_headroom = config.house_headroom()
        # The launcher's build preview follows the game through these state
        # lines on stdout; when nothing is listening they are just quiet
        # noise in a pipe nobody reads.
        self.feed = statefeed.StateEmitter()
        self.report = report.BuildReport()
        self.recorder, self.stats_file = start_recorder(build_stem, build.name)

    def finish(self):
        """Write the game's statistics file, if there is a game worth one.

        Called at exit and on a new match starting. Exit reaches here twice
        on a clean quit (aboutToQuit, then after app.exec returns - Ctrl+C
        only takes the second path), so it makes itself idempotent; the
        periodic flush in tick() makes even a hard kill lose at most thirty
        game-seconds.
        """
        if self.recorder.has_data() and not getattr(self.recorder, "closed", False):
            self.recorder.closed = True
            self.recorder.write(self.stats_file)
            print(f"stats: wrote {self.stats_file.relative_to(paths.PROJECT_ROOT)}")

    def tick(self):
        reading = self.hud.poll()

        # A new match means starting the build again, so the pace tracker must
        # forget how late the last game was - and the production tracker must
        # forget the old game's Town Centres. The finished game's statistics
        # go to disk before the new recorder takes over.
        if reading.event == session.GAME_STARTED:
            self.finish()
            self.pace.reset()
            self.production.reset()
            self.report = report.BuildReport()
            self.recorder, self.stats_file = start_recorder(
                self.build_stem, self.build.name)

        # The game's own notification feed states TC completions outright -
        # the one exact source of TC count; queue evidence only corroborates.
        if "town_center_built" in reading.game_events:
            self.production.register_tc_built()

        # Feed the production tracker every poll. reading.queue is None when
        # the queue was unreadable, which the tracker treats as "no news".
        self.production.update(reading.game_time, reading.queue)

        if not reading.is_usable():
            self.panel.show_waiting("waiting for the game...")
            self.feed.emit({"usable": False})
            return

        villagers = reading.villagers
        game_time = reading.game_time
        delta = self.pace.update(villagers, game_time)
        extra = build_order.extra_villagers(self.build, villagers, game_time)

        self.report.update(game_time, self.production, delta,
                           reading.queue, reading.game_events,
                           extra=extra, villagers=villagers)

        alerts_list = alerts.production_alerts(
            self.production, villagers, self.policy, game_time,
            reading.population, self.toggles, self.house_headroom)
        self.panel.show_alerts(alerts_list)

        # The statistics recorder watches the whole game, build and after.
        self.recorder.observe(game_time, villagers, delta, self.production,
                              reading.population, reading.queue,
                              reading.game_events, alerts_list)
        if self.recorder.due_flush():
            self.recorder.write(self.stats_file)

        # The build finishing is the payoff moment: the panel flips from
        # instructions to the report and stays there for the rest of the
        # game (the alert bands keep working - the game goes on).
        if self.pace.complete:
            self.report.complete(game_time)
            self.recorder.snapshot_build(self.report, self.build)
            self.panel.show_report(
                self.report.summary(self.build), self.build.name,
                f"{report.format_time(game_time)}   {villagers} villagers")
        else:
            active = self.build.active_step(villagers, game_time)
            self.panel.show_step(
                self.build,
                villagers,
                game_time,
                active,
                self.build.following_step(villagers, game_time),
                delta,
                reading.per_resource,
                extra=extra,
                milestone_queued=self.report.milestone_queued(active),
            )
        # Whole-number time and pace so the emitter's change-check works:
        # float jitter would otherwise make every poll look "new".
        self.feed.emit({
            "usable": True,
            "idx": self.build.current_index(villagers, game_time),
            "vills": villagers,
            "t": int(game_time),
            "pace": None if delta is None else round(delta),
            "res": reading.per_resource or None,
            "pop": list(reading.population) if reading.population else None,
        })


class DemoController:
    """Replays a match with no game running, so the panel can be checked."""

    def __init__(self, panel, build, speed=20, build_stem="demo"):
        self.panel = panel
        self.build = build
        self.speed = speed
        self.moment = 0
        self.last = max(s.time for s in build.steps if s.time is not None)
        self.pace = pace.PaceTracker(build)
        # Demo mode feeds the launcher's preview too, so the whole follow
        # behaviour can be watched with no game running.
        self.feed = statefeed.StateEmitter()
        self.build_stem = build_stem
        # And it writes a stats file like the real thing, so the whole
        # stats pipeline can be exercised with no game.
        self.recorder, self.stats_file = start_recorder(build_stem, build.name)

    def finish(self):
        if self.recorder.has_data() and not getattr(self.recorder, "closed", False):
            self.recorder.closed = True
            self.recorder.write(self.stats_file)
            print(f"stats: wrote {self.stats_file.relative_to(paths.PROJECT_ROOT)}")

    def tick(self):
        # Each tick advances game time by however many seconds the chosen
        # speed implies, so a whole match plays out in about a minute.
        self.moment += self.speed * (POLL_INTERVAL_MS / 1000.0)
        if self.moment > self.last + 60:
            self.finish()
            self.moment = 0
            self.pace.reset()
            self.recorder, self.stats_file = start_recorder(
                self.build_stem, self.build.name)

        villagers = villagers_following_build(self.build, self.moment)
        delta = self.pace.update(villagers, self.moment)
        demo_alerts = self._demo_alerts()
        self.panel.show_alerts(demo_alerts)
        self.recorder.observe(self.moment, villagers, delta,
                              alerts_list=demo_alerts)
        if self.recorder.due_flush():
            self.recorder.write(self.stats_file)
        self.panel.show_step(
            self.build,
            villagers,
            self.moment,
            self.build.active_step(villagers, self.moment),
            self.build.following_step(villagers, self.moment),
            delta,
        )
        self.feed.emit({
            "usable": True,
            "idx": self.build.current_index(villagers, self.moment),
            "vills": villagers,
            "t": int(self.moment),
            "pace": None if delta is None else round(delta),
            "res": None,
            "pop": None,
        })

    def _demo_alerts(self):
        """Scripted alert moments, so the bands can be seen without a game.

        The middle window deliberately overlaps two alerts, because the
        whole point of stacking is both being visible at once.
        """
        found = []
        if 240 <= self.moment < 300:
            found.append((f"TC IDLE — {self.moment - 240:.0f}s", alerts.FULL))
        if 260 <= self.moment < 300:
            found.append(("HOUSE NOW — 2 pop space left", alerts.FULL))
        if 420 <= self.moment < 460:
            found.append(("TC IDLE", alerts.SOFT))
        return found


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


def warn_if_not_click_through(panel):
    """Say something loudly if the overlay can still catch the mouse.

    Silent when it is fine, and silent when the question cannot be answered:
    only a definite "no" is worth interrupting the startup output for. The
    failure this guards against is quiet and expensive - the game loses its
    grip on the cursor and the mouse wanders off mid-match.
    """
    verdict, message = passthrough.check(int(panel.winId()))
    if verdict is False:
        print(f"warning: {message}")


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

    # The launcher stops the overlay with SIGTERM, whose default action
    # skips every Qt cleanup hook. Turning it into a clean quit means
    # aboutToQuit runs - so the stats file gets its final write and
    # placement mode saves its offset even when stopped from the launcher.
    # The idle timer below is what guarantees the interpreter wakes up to
    # run the handler.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    # Where the panel's offset is measured from: the game window if there is
    # one, otherwise the primary screen.
    origin_x, origin_y, area_width = screen_origin(app)

    if args.demo:
        controller = DemoController(panel, build, args.speed,
                                    build_stem=args.build)
        print(f"Demo mode at {args.speed}x. Ctrl+C to stop.")
    else:
        hud = reader.HudReader()
        # Both waits are open-ended: start the overlay first, then the game.
        try:
            print("Waiting for the Age of Empires II window... (Ctrl+C to quit)")
            hud.connect()
            print("Waiting for a match to start...")
            hud.wait_for_hud()
        except KeyboardInterrupt:
            print("\nStopped.")
            return
        print(f"HUD found (match {hud.hud['score']:.3f}, scale {hud.hud['scale']:.2f})")

        display = capture.open_display()
        window = capture.find_game_window(display)
        origin_x, origin_y, area_width, _ = capture.window_geometry(
            window, display)

        controller = LiveController(panel, build, hud, build_stem=args.build)
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

    # The game's statistics go to disk on any clean exit - Ctrl+C, the
    # launcher's Stop (SIGTERM, see above), or the window closing.
    app.aboutToQuit.connect(controller.finish)

    # Ask the X server whether click-through actually took effect rather than
    # trusting that it did. Deferred by one turn of the event loop because the
    # shape request Qt sends at show() has to reach the server before a second
    # connection can see its effect.
    QTimer.singleShot(0, lambda: warn_if_not_click_through(panel))

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
    # Ctrl+C propagates out of exec without firing aboutToQuit, so the
    # stats write is repeated here; finish() is idempotent.
    controller.finish()
    print("\nStopped.")


if __name__ == "__main__":
    main()
