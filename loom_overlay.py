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
import sys

# Must be set before Qt starts. On Wayland a client is not allowed to raise
# itself above other windows, so the overlay would never appear over the game.
# Running through XWayland puts it in the same X server as the game, where
# "keep above" still means something. Doing it here so the program can just be
# run, rather than relying on remembering an environment variable.
#
# Linux only. macOS has one windowing system and its Qt plugin is "cocoa";
# asking for "xcb" there does not fall back, it refuses to start at all.
if sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

import argparse
import datetime
import signal
import sys
import time

from PyQt6.QtCore import QMetaObject, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from loom import alerts, apm, build_order, capture, config, entry, follow, gamestats
from loom import hotkeys, overlay, pace, passthrough, paths, production
from loom.hotkeys import keyspec
from loom import reader, report, session, statefeed, stopline
from loom.build_order import BuildOrder

POLL_INTERVAL_MS = 300

# Where the panel goes when the player has never chosen a spot: tucked into
# the TOP-RIGHT corner, below the game's own bar. Top-right rather than
# centred because centred sits over the action; the right edge is where the
# game keeps its own passive displays. Both numbers the default needs are
# knowable: the window width exactly, and the bar's height as this reference
# times the measured HUD scale - the same anchor scale everything else
# follows, so the pixel-constant rule is satisfied rather than dodged. When
# no game has been measured (demo, placement without a game) the scale is
# 1.0 and this is the same guess the old fixed margin made.
PANEL_TOP_MARGIN = 96
PANEL_RIGHT_MARGIN = 16

# How much of the panel must be on SOME screen for a saved position to be
# believed. A sliver does not count: a panel showing 10 pixels of its corner
# is lost for every practical purpose.
MIN_VISIBLE = 60


def default_offset(panel, width, hud_scale=1.0):
    """Where the panel goes before the player has moved it: top-right,
    under the game's bar. Clamped left so a window narrower than the panel
    still shows it."""
    return (max(0, width - panel.width() - PANEL_RIGHT_MARGIN),
            round(PANEL_TOP_MARGIN * hud_scale))


def visible_on(screens, x, y, width, height):
    """Is a meaningful amount of this rectangle on any of these screens?

    screens is [(x, y, width, height)]. Pure, so the interesting inputs - a
    monitor at negative coordinates, a rect straddling two screens, a rect
    off every screen - are testable without arranging real monitors, exactly
    like launcher.beside().
    """
    for screen_x, screen_y, screen_w, screen_h in screens:
        overlap_w = min(x + width, screen_x + screen_w) - max(x, screen_x)
        overlap_h = min(y + height, screen_y + screen_h) - max(y, screen_y)
        if overlap_w >= MIN_VISIBLE and overlap_h >= MIN_VISIBLE:
            return True
    return False


def screen_rects(app):
    """Every screen's geometry as plain tuples, for visible_on."""
    return [(g.x(), g.y(), g.width(), g.height())
            for g in (screen.geometry() for screen in app.screens())]


def place_panel(panel, origin_x, origin_y, width, hud_scale=1.0,
                screens=None):
    """Put the panel at the saved offset from the game window's corner.

    The offset is stored relative to the game rather than to the desktop, so
    it survives a resolution change or the game moving to another monitor.

    A saved offset that lands the panel off EVERY screen is not obeyed - it
    is replaced by the default, with a line saying so. This happened live: a
    placement measured against one origin was replayed against another, and
    the panel sat invisibly outside both displays while looking, from the
    inside, perfectly placed. An overlay nobody can see is worse than one in
    the wrong corner, and the player can always place it again.
    """
    offset = config.overlay_offset()
    fallback = default_offset(panel, width, hud_scale)
    chosen = offset or fallback
    x, y = origin_x + chosen[0], origin_y + chosen[1]
    if (offset is not None and screens
            and not visible_on(screens, x, y, panel.width(), panel.height())):
        print(f"The saved overlay position {offset} is off every screen - "
              f"using the default instead. Place overlay again to choose a "
              f"new spot.")
        chosen = fallback
        x, y = origin_x + chosen[0], origin_y + chosen[1]
    panel.move(x, y)
    return chosen


def stats_path(build_stem):
    """Where this game's statistics file goes: timestamped, per match."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return paths.STATS_DIR / f"{stamp}_{build_stem}.json"


def start_recorder(build_stem, build_name):
    """A fresh GameRecorder and the file it will write to."""
    recorder = gamestats.GameRecorder(
        build_stem, build_name, datetime.datetime.now().isoformat(timespec="seconds"))
    return recorder, stats_path(build_stem)


def start_apm(app, panel):
    """Count keys and clicks for the statistics, where this platform does it.

    On Windows that is here, inside the overlay: Raw Input needs a window and
    a message pump and the overlay has both. On Linux the launcher runs
    tools/apm_counter.py instead, so this does nothing. apm.counted_in_the_
    overlay is the single place that decides, because the failure mode of the
    two disagreeing is counting everything twice.

    Returns the counter so it can be stopped, or None.
    """
    if not config.track_apm():
        return None
    if not apm.counted_in_the_overlay(sys.platform):
        return None

    from loom import apmwin
    counter = apmwin.start(int(panel.winId()))
    if counter is not None:
        app.aboutToQuit.connect(counter.stop)
    return counter


def start_hotkeys(app, controller, follow_state):
    """Register the player's hotkeys, or explain why there are none.

    Returns the listener so it can be stopped, or None. Nothing here is
    allowed to stop the overlay starting: hotkeys are a convenience laid over
    a program whose whole point is that it advances itself, so every failure
    is a printed line and a shrug.

    Registration is deliberately noisy about failure. A combination another
    program already owns is not reported by anything else, and the symptom -
    a key that does nothing - looks identical to a key that was never bound.
    """
    if not config.hotkeys_enabled():
        return None
    # Only the overlay's OWN actions. The launcher registers its
    # start/stop key and holds it for its whole session; asking Windows for
    # it again here would fail and print "already in use" on every start.
    bindings = {action: binding
                for action, binding in config.hotkeys().items()
                if action in config.OVERLAY_HOTKEY_ACTIONS}

    for action, binding in sorted(bindings.items()):
        trouble = keyspec.problem(binding)
        if trouble:
            print(f"hotkey {action}: {trouble}")
    for first, second in keyspec.conflicts(bindings):
        print(f"hotkeys {first} and {second} are on the same combination; "
              f"only one of them will work")

    def on_hotkey(action):
        moment = time.monotonic()
        if action == "next_step":
            follow_state.next_step(controller.auto_index(), moment)
        elif action == "previous_step":
            follow_state.previous_step(controller.auto_index(), moment)
        elif action == "toggle_follow":
            following = follow_state.toggle()
            print("hotkeys: following the game again" if following
                  else "hotkeys: no longer following the game")
        # Redraw now rather than at the next poll, so the panel answers the
        # keypress immediately. This costs nothing - it reuses the last
        # reading instead of asking the HUD again.
        controller.refresh()

    try:
        listener = hotkeys.listen(bindings, on_hotkey)
    except hotkeys.HotkeyError as problem:
        print(f"hotkeys unavailable: {problem}")
        return None

    for action, binding, reason in listener.failures:
        print(f"hotkey {action} ({binding}) could not be registered: {reason}")

    if listener.actions:
        # Worth saying out loud: while Loom holds a combination, no other
        # program sees it - including the game.
        taken = ", ".join(sorted(bindings[action]
                                 for action in listener.actions.values()))
        print(f"hotkeys: {taken} (these are taken from the game while Loom "
              f"runs)")
    app.aboutToQuit.connect(lambda: hotkeys.stop(listener))
    return listener


def resume_hint():
    """The binding that switches following back on, for the panel to name.

    Read from the config rather than hardcoded, so the panel can never
    advertise a key the player has rebound or switched off. None when there
    is nothing useful to suggest, and the panel then just says it is not
    following.
    """
    if not config.hotkeys_enabled() or not hotkeys.available():
        return None
    binding = config.hotkeys().get("toggle_follow")
    try:
        return keyspec.normalise(binding)
    except ValueError:
        return None


class LiveController:
    """Polls the game and pushes each reading into the panel."""

    def __init__(self, panel, build, hud, build_stem="unknown",
                 follow_state=None):
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
        # The player's own HOUSE SOON threshold, same read-once contract.
        self.house_headroom = config.house_headroom()
        # The launcher's build preview follows the game through these state
        # lines on stdout; when nothing is listening they are just quiet
        # noise in a pipe nobody reads.
        self.feed = statefeed.StateEmitter()
        self.report = report.BuildReport()
        self.recorder, self.stats_file = start_recorder(build_stem, build.name)
        # Where the panel is looking, and whether the game gets to move it.
        # Passed in rather than made here so main() can hand the same object
        # to the hotkey listener - there is exactly one cursor.
        self.follow = follow_state or follow.FollowState()
        # Named once at startup, like every other setting the overlay reads.
        self.resume_hint = resume_hint()
        # The last usable reading, kept so a hotkey can redraw the panel at
        # once instead of waiting up to a poll interval for the next tick.
        # Polling again on a keypress would cost a full HUD read, which is
        # the expensive thing this whole loop is budgeted around.
        self._last = None

    def auto_index(self):
        """The step the READING implies, ignoring anything the player pressed.

        What a hotkey steps away from, and what following returns to.
        """
        return -1 if self._last is None else self._last[0]

    def refresh(self):
        """Redraw from the last reading. For after a hotkey changes the step."""
        if self._last is not None and not self.pace.complete:
            self._render(*self._last)

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
            print(f"stats: wrote {paths.for_display(self.stats_file)}")

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
            # A cursor left on step 20 of the last game would be exactly the
            # silent desynchronisation everything else here exists to avoid.
            self.follow.reset()

        # The game's own notification feed states TC completions outright -
        # the one exact source of TC count; queue evidence only corroborates.
        # Counted per occurrence, not membership: two TCs finishing in the
        # same feed redraw arrive as the same name twice.
        for event in reading.game_events:
            if event == "town_center_built":
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
            self._last = (self.build.current_index(villagers, game_time),
                          villagers, game_time, delta,
                          reading.per_resource,
                          list(reading.population) if reading.population
                          else None,
                          extra)
            self._render(*self._last)

    def _render(self, auto_index, villagers, game_time, delta, per_resource,
                population, extra):
        """Draw the panel and announce the state, for one reading.

        Split out of tick so a hotkey can call it directly: the player's step
        change then shows immediately rather than at the next poll, without
        reading the HUD again.

        The step shown comes from follow, which is either the reading's own
        answer or the one the player nudged it to. Note what does NOT go
        through it - pace, the villager surplus, the report and the recorder
        all stay on the reading, so the statistics remain an honest record of
        the game whatever the player happened to be looking at.
        """
        index = self.follow.effective_index(auto_index, time.monotonic())
        mode = self.follow.mode(time.monotonic())
        active = self.build.active_step_at(index)
        self.panel.show_step(
            self.build,
            villagers,
            game_time,
            active,
            self.build.following_step_at(index),
            delta,
            per_resource,
            extra=extra,
            milestone_queued=self.report.milestone_queued(active),
            follow_mode=mode,
            resume_hint=self.resume_hint,
        )
        # Whole-number time and pace so the emitter's change-check works:
        # float jitter would otherwise make every poll look "new".
        self.feed.emit({
            "usable": True,
            "idx": index,
            "mode": mode,
            "vills": villagers,
            "t": int(game_time),
            "pace": None if delta is None else round(delta),
            "res": per_resource or None,
            "pop": population,
        })


class DemoController:
    """Replays a match with no game running, so the panel can be checked."""

    def __init__(self, panel, build, speed=20, build_stem="demo",
                 follow_state=None):
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
        # Demo mode drives the same cursor, so hotkeys and the manual/holding
        # indicator can be watched end to end with no game running.
        self.follow = follow_state or follow.FollowState()
        self.resume_hint = resume_hint()
        self._last = None

    def auto_index(self):
        return -1 if self._last is None else self._last[0]

    def refresh(self):
        if self._last is not None:
            self._render(*self._last)

    def finish(self):
        if self.recorder.has_data() and not getattr(self.recorder, "closed", False):
            self.recorder.closed = True
            self.recorder.write(self.stats_file)
            print(f"stats: wrote {paths.for_display(self.stats_file)}")

    def tick(self):
        # Each tick advances game time by however many seconds the chosen
        # speed implies, so a whole match plays out in about a minute.
        self.moment += self.speed * (POLL_INTERVAL_MS / 1000.0)
        if self.moment > self.last + 60:
            self.finish()
            self.moment = 0
            self.pace.reset()
            self.follow.reset()
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
        self._last = (self.build.current_index(villagers, self.moment),
                      villagers, self.moment, delta)
        self._render(*self._last)

    def _render(self, auto_index, villagers, moment, delta):
        index = self.follow.effective_index(auto_index, time.monotonic())
        mode = self.follow.mode(time.monotonic())
        self.panel.show_step(
            self.build,
            villagers,
            moment,
            self.build.active_step_at(index),
            self.build.following_step_at(index),
            delta,
            follow_mode=mode,
            resume_hint=self.resume_hint,
        )
        self.feed.emit({
            "usable": True,
            "idx": index,
            "mode": mode,
            "vills": villagers,
            "t": int(moment),
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
            found.append(("HOUSE SOON — 2 pop space left", alerts.FULL))
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


def placement_origin(app):
    """Where placement mode measures the panel's offset from.

    Returns (x, y, width, source), where source is a phrase fit to print.

    ONE non-blocking look for the game window, then the primary screen.
    Placement used to travel the live path and block in hud.connect() until
    a game appeared - which meant the panel could not be positioned without
    starting a match, for a task that only needs a reference corner.

    The caveat the printed source exists for: the saved offset is measured
    from whichever origin was used. A fullscreen game at the primary
    screen's corner - the common case - makes the two identical. On a
    multi-monitor desktop where the game lives on ANOTHER screen, placing
    with no game running saves an offset measured from the wrong corner,
    and saying which origin was used is what lets the player notice.
    """
    try:
        display = capture.open_display()
        window = capture.find_game_window(display)
        if window is not None:
            x, y, width, _height = capture.window_geometry(window, display)
            return x, y, width, "the game window"
    except capture.CaptureError:
        # No backend, no permission, or a window that cannot be captured
        # right now (minimised, say). Placement must not care: the screen
        # is a perfectly good ruler when the game cannot be measured.
        pass
    x, y, width = screen_origin(app)
    return x, y, width, "the primary screen"


_last_placement = None


def watch_placement(panel):
    """Say when the panel's own geometry changes, and only then.

    For an overlay reported to vibrate. place_panel runs once at startup and
    nothing here moves the panel afterwards, so this answers the question
    that decides where to look next: if the geometry never changes while the
    panel is visibly shivering, nothing in Loom is moving it and the shake is
    either the panel's own drawing or the compositor - and if it does change,
    this prints what changed it into.
    """
    global _last_placement
    now = (panel.x(), panel.y(), panel.width(), panel.height())
    if now != _last_placement:
        if _last_placement is not None:
            print(f"[place] panel moved {_last_placement} -> {now}")
        else:
            print(f"[place] panel at {now}")
        _last_placement = now


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
    parser.add_argument("--debug-place", action="store_true",
                        help="print where the panel sits each poll, for "
                             "chasing an overlay that will not hold still")
    args = parser.parse_args()

    # Settings and match history live outside the source tree now, so an
    # existing clone's are brought across the first time. Idempotent: after
    # that first run this is two stat() calls and nothing else.
    for note in paths.migrate_legacy_writables():
        print(note)

    build = BuildOrder.load_by_name(args.build)
    for problem in build.validate():
        print(f"warning: {problem}")

    entry.windows_app_identity()
    app = QApplication(sys.argv)
    # Mostly for placement mode and alt-tab: the overlay proper is a
    # frameless tooltip with no taskbar presence, but the placing window is
    # an ordinary one and deserves to look like Loom.
    app.setWindowIcon(QIcon(str(paths.ICON_PATH)))
    panel = overlay.Overlay(placing=args.place)

    # The launcher stops the overlay with SIGTERM, whose default action
    # skips every Qt cleanup hook. Turning it into a clean quit means
    # aboutToQuit runs - so the stats file gets its final write and
    # placement mode saves its offset even when stopped from the launcher.
    # The idle timer below is what guarantees the interpreter wakes up to
    # run the handler.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    # The same request, arriving as a line on stdin instead of as a signal,
    # because SIGTERM is not portable: on Windows QProcess::terminate posts
    # WM_CLOSE to top-level windows and this panel is a ToolTip, so measured
    # there the signal above never fires and everything below aboutToQuit was
    # being lost. Whichever request lands first wins; on Linux that is still
    # SIGTERM and nothing about this path changes.
    #
    # invokeMethod rather than app.quit directly: the stop line is read on a
    # background thread, and Qt objects may only be touched from the thread
    # that owns them. A queued invocation is the supported way across.
    stopline.watch(lambda: QMetaObject.invokeMethod(
        app, "quit", Qt.ConnectionType.QueuedConnection))

    # Placement leaves before the controllers exist: it needs a reference
    # corner and a draggable panel, not a game. It used to fall through the
    # live branch below and block waiting for the game window - the fallback
    # origin existed but was unreachable from here.
    if args.place:
        origin_x, origin_y, area_width, source = placement_origin(app)
        offset = place_panel(panel, origin_x, origin_y, area_width,
                             screens=screen_rects(app))
        panel.show_step(build, 13, 250,
                        build.active_step(13, 250),
                        build.following_step(13, 250),
                        0)
        print(f"Placement mode, measured from {source}. "
              f"Current offset: {offset}")
        print("Drag the window where you want it, then close it to save.")
        app.aboutToQuit.connect(
            lambda: remember_position(panel, origin_x, origin_y))
        panel.show()
        app.exec()
        return

    # Where the panel's offset is measured from: the game window if there is
    # one, otherwise the primary screen.
    origin_x, origin_y, area_width = screen_origin(app)

    # One cursor, shared by whichever controller runs and by the hotkeys, so
    # a keypress and the next poll can never disagree about where the panel
    # is looking.
    follow_state = follow.FollowState(
        hold_seconds=config.manual_hold_seconds(),
        step_count=len(build.steps))

    if args.demo:
        controller = DemoController(panel, build, args.speed,
                                    build_stem=args.build,
                                    follow_state=follow_state)
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

        controller = LiveController(panel, build, hud, build_stem=args.build,
                                    follow_state=follow_state)
        print(f"Overlay running on '{build.name}'. Ctrl+C to stop.")

    # Live mode has measured the HUD by now, so the default position can
    # sit exactly under the bar at ITS size; demo mode assumes 1.0.
    hud_scale = 1.0 if args.demo else hud.hud["scale"]
    place_panel(panel, origin_x, origin_y, area_width, hud_scale=hud_scale,
                screens=screen_rects(app))

    panel.show()

    listener = start_hotkeys(app, controller, follow_state)
    counter = start_apm(app, panel)

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
    if args.debug_place:
        timer.timeout.connect(lambda: watch_placement(panel))
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
