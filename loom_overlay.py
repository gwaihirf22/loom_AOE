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

from PyQt6.QtCore import QMetaObject, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from loom import age as age_reader
from loom import (alerts, apm, build_order, capture, checklist, config, entry)
from loom import debuglog, follow, gamestats
from loom import hotkeys, overlay, pace, passthrough, paths, production
from loom.hotkeys import keyspec
from loom import reader, report, session, statefeed, stopline
# visible_on and screen_rects moved to loom/placement.py so the launcher can
# ask the same question - it must not import this module, which is an entry
# point. Re-exported under their old names because everything here, and
# tests/test_placement.py, already calls them that.
from loom.placement import MIN_VISIBLE, screen_rects, visible_on
from loom.build_order import BuildOrder

POLL_INTERVAL_MS = 300

# While waiting for a match, how often to ask which window the game is in
# again, counted in failed looks. connect() answers that once at startup and
# the answer can go stale - see reader.reacquire_window. At the poll interval
# above this is about every six seconds, which is far below the cost of
# noticing by hand and restarting.
REACQUIRE_EVERY_NTH_LOOK = 20

# And how many failed looks before explaining what might be wrong. About
# thirty seconds: long enough that it is not shouted at somebody still
# loading a map, short enough to reach the player while they are still
# wondering. Said once - the answer cannot change while nothing does.
LOOKS_BEFORE_EXPLAINING = 100

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


def default_offset(panel, width, hud_scale=1.0):
    """Where the panel goes before the player has moved it: top-right,
    under the game's bar. Clamped left so a window narrower than the panel
    still shows it."""
    return (max(0, width - panel.width() - PANEL_RIGHT_MARGIN),
            round(PANEL_TOP_MARGIN * hud_scale))


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
        if action == "toggle_hidden":
            # First, and outside the guard below: hiding is about the
            # window, not about the game, and the moment a player most
            # wants the panel gone is while it is sitting over the menus
            # with no match to follow yet.
            controller.toggle_hidden()
            return
        if not controller.following_yet():
            # No game yet, so there is no step to move and nothing to
            # redraw from. Silently doing nothing beats moving a cursor
            # that the first real reading would reset anyway.
            return
        if action in ("next_step", "previous_step"):
            if controller.reviewing() and follow_state.auto:
                # The build is over, so this keypress is reading rather than
                # nudging, and it must not time out. The ten second hold
                # exists to stop the panel drifting out of sync with a LIVE
                # build; once the build is done there is nothing left to
                # drift from, and a report card that reappeared mid-sentence
                # would be the feature failing at the one thing it is for.
                #
                # Switching following off is how this is already said - the
                # panel then shows MANUAL and names the key that brings the
                # report back, so the way out is on its face.
                follow_state.toggle()
            if action == "next_step":
                follow_state.next_step(controller.auto_index(), moment)
            else:
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


def step_hint():
    """The next-step binding, for the BUILD DONE note to name.

    Same contract as resume_hint: read from config so the panel can never
    advertise a key the player has rebound or switched off.
    """
    if not config.hotkeys_enabled() or not hotkeys.available():
        return None
    binding = config.hotkeys().get("next_step")
    try:
        return keyspec.normalise(binding)
    except ValueError:
        return None


def step_hint_trouble():
    """Why the BUILD DONE note has no next-step key to name, or None.

    None means either that there IS a key - so the note names it and this
    is not needed - or that nothing can be done about there not being one,
    which is a machine with no hotkey backend at all. Everything else is a
    remedy the note can offer, and the two remedies differ: throw the
    master switch, or bind the key behind it. Hotkeys ship switched off
    from 1.0.5, so the first of those is what a new player meets at the
    end of their first build, with the report otherwise unreachable and
    unmentioned. See overlay.describe_follow.
    """
    if step_hint() or not hotkeys.available():
        return None
    if not config.hotkeys_enabled():
        return overlay.HOTKEYS_OFF
    return overlay.HOTKEYS_UNBOUND


class LauncherRequests(QObject):
    """The launcher's stdin requests, moved onto the GUI thread.

    stopline reads on a daemon thread, and Qt objects may only be touched
    from the thread that owns them - hiding the panel from the reader thread
    would be exactly that mistake. A signal emitted across threads is the
    supported way over: this object is created on the GUI thread, so the
    connection below is queued and the handler runs where the panel lives.

    The stop request has its own route (QMetaObject.invokeMethod on the
    application) because app.quit IS a slot; nothing here is.

    The controller is filled in afterwards rather than passed in, because
    the reader thread starts before there is a controller to give it - and
    the launcher may send a request in that window.
    """

    requested = pyqtSignal()
    settings_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.controller = None
        self.requested.connect(self._toggle_hidden)
        self.settings_changed.connect(self._apply_appearance)

    def _toggle_hidden(self):
        if self.controller is not None:
            self.controller.toggle_hidden()

    def _apply_appearance(self):
        if self.controller is not None:
            self.controller.apply_appearance()


class Hideable:
    """What both controllers share: the window, and the game's lifecycle.

    Two jobs that have nothing to do with where the numbers come from:
    taking the panel off the screen and putting it back, and forgetting a
    finished game when a new one starts. Expects self.panel; the lifecycle
    half also expects self.recorder, self.stats_file, self.build_stem,
    self.build and self.fresh_each_game.

    Hiding is not stopping. Polling, alerts, the statistics recorder and the
    APM counter all carry on with the window gone; stopping would throw away
    the match being tracked, which is the whole reason this is a separate
    thing from the Stop button.

    The overlay owns this state and reports it upward, so the launcher's
    button and the hotkey cannot drift apart: both ask for a toggle, and
    what the button DISPLAYS comes from the line emitted here.
    """

    hidden = False
    _checked_passthrough = False

    # What the panel was last placed against: (origin_x, origin_y,
    # area_width, hud_scale). Remembered because a live size change has to
    # put the panel back, and those numbers otherwise live and die as
    # locals in whoever called place_panel.
    placed_against = None

    def apply_appearance(self):
        """The settings file changed: wear it, and stay where you belong.

        Re-placing is not optional when the size changed. The default
        position is right-aligned against the game window - see
        default_offset - while move() sets the panel's TOP-LEFT, so a panel
        that grew without being placed again hangs off the right edge by
        exactly however much it grew.
        """
        if not self.panel.apply_appearance():
            return
        if self.placed_against is None:
            return
        origin_x, origin_y, area_width, hud_scale = self.placed_against
        place_panel(self.panel, origin_x, origin_y, area_width,
                    hud_scale=hud_scale,
                    screens=screen_rects(QApplication.instance()))

    def begin_hidden(self):
        """Start with the panel off the screen, because the player asked.

        Not a toggle, and not the same code path: at startup there is nothing
        to hide yet - the panel has simply never been shown - so this records
        the state and announces it rather than flipping anything. Announcing
        matters as much as the state does: the launcher's button reads what
        this emits, so without the line it would offer "Hide overlay" for a
        panel that was never up.
        """
        self.hidden = True
        print(statefeed.encode({"hidden": True}))

    def toggle_hidden(self):
        self.hidden = not self.hidden
        self.panel.setVisible(not self.hidden)
        print(statefeed.encode({"hidden": self.hidden}))

        if not self.hidden and not self._checked_passthrough:
            # Click-through lives in the window's X11 input region, which is
            # applied when the window is MAPPED - so showing it again is a
            # moment where it could plausibly be lost, and losing it quietly
            # costs the player their cursor mid-match. Asking is cheap, and
            # only the first re-show needs to answer it.
            self._checked_passthrough = True
            QTimer.singleShot(0, lambda: warn_if_not_click_through(self.panel))

    def following_yet(self):
        """Is there a game to step through? The step hotkeys need to know."""
        return True

    def reviewing(self):
        """Is the build finished, so a step key is reading rather than
        nudging? Only the live panel has a report to rest on; demo mode
        never shows one, so the default is no."""
        return False

    # ---- forgetting a finished game ------------------------------------

    # The objects that must forget the last game, in the order their
    # reset() runs. Each controller builds its own tuple in __init__, and
    # start_fresh_game below is the ONLY place that walks one.
    #
    # A tuple rather than eight lines of resets in tick(), because those
    # lines were maintained by hand in two controllers and had already
    # drifted: the demo gained a checklist in a merge and its reset list
    # never learned, so every replay loop after the first started with the
    # items pre-ticked. A subsystem left out of this tuple is exactly the
    # silent desynchronisation the rest of Loom is built to avoid - a
    # cursor, a count or a tick carried into a game it does not belong to.
    fresh_each_game = ()

    def start_fresh_game(self):
        """A new game is starting: write the old one out and forget it.

        The order is load-bearing. finish() must run FIRST, because it
        writes to self.stats_file and renew_recorder replaces that path -
        swapped, the finished game's statistics would land in the new
        game's file or nowhere.
        """
        self.finish()
        for fresh in self.fresh_each_game:
            fresh.reset()
        self.renew_recorder()

    def renew_recorder(self):
        """A fresh recorder and a fresh file for the game now starting."""
        self.recorder, self.stats_file = start_recorder(
            self.build_stem, self.build.name)

    def finish(self):
        """Write the game's statistics file, if there is a game worth one.

        Called at exit and on a new match starting. Exit reaches here twice
        on a clean quit (aboutToQuit, then after app.exec returns - Ctrl+C
        only takes the second path), so it makes itself idempotent; the
        periodic flush in tick() makes even a hard kill lose at most thirty
        game-seconds.
        """
        if self.recorder.has_data() and not getattr(self.recorder,
                                                    "closed", False):
            self.recorder.closed = True
            self.recorder.write(self.stats_file)
            print(f"stats: wrote {paths.for_display(self.stats_file)}")


class LiveController(Hideable):
    """Polls the game and pushes each reading into the panel."""

    def __init__(self, panel, build, hud, build_stem="unknown",
                 follow_state=None, log=None):
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
        # Which items of the build have been done. Stage 1 only ever
        # ASSUMES - an item is assumed once the reading has passed its step
        # - because the events that would OBSERVE one are unreadable at
        # 1920x1080 until the notification font is harvested there. The seam
        # is here so that turning them on is one argument.
        self.checklist = checklist.Checklist(build)
        # Houses counted from the population cap as well as the feed - the
        # feed structurally misses houses built close together. See
        # checklist.HouseEvidence.
        self.houses = checklist.HouseEvidence()
        # What age the HUD says we are in, and when each age-up was clicked
        # and reached. Debounced like every other belief here.
        self.ages = age_reader.AgeTracker()
        self.recorder, self.stats_file = start_recorder(build_stem, build.name)
        # Where the panel is looking, and whether the game gets to move it.
        # Passed in rather than made here so main() can hand the same object
        # to the hotkey listener - there is exactly one cursor.
        self.follow = follow_state or follow.FollowState()
        # Everything above that must forget a finished game, in reset
        # order - see Hideable.start_fresh_game. The report joins the
        # tuple like the trackers do; its reset() re-runs __init__, so it
        # cannot drift from construction.
        self.fresh_each_game = (self.pace, self.production, self.report,
                                self.follow, self.checklist, self.ages,
                                self.houses)
        # Named once at startup, like every other setting the overlay reads.
        self.resume_hint = resume_hint()
        self.step_hint = step_hint()
        # And what the note should say INSTEAD of a key, when there is no
        # key to name - which is the shipped default now that hotkeys
        # arrive switched off.
        self.step_trouble = step_hint_trouble()
        # When the CLICK UP band's prerequisite suppression began, in game
        # time, or None while it is not suppressing. A scalar rather than
        # a registry object; cleared beside start_fresh_game in tick(),
        # and self-correcting anyway - it resets whenever the suppression
        # conditions break.
        self._prereq_quiet_since = None
        # The last age reading, for the state line. Kept beside _last for
        # the same reason: a hotkey redraws from what was last seen rather
        # than reading the screen again.
        self._last_age = None
        # The last usable reading, kept so a hotkey can redraw the panel at
        # once instead of waiting up to a poll interval for the next tick.
        # Polling again on a keypress would cost a full HUD read, which is
        # the expensive thing this whole loop is budgeted around.
        self._last = None
        # Whether the session has declared sight of the game lost. The
        # filters hold their beliefs through the gap, so without this flag
        # the panel would keep wearing a live face built from held numbers
        # - the frozen-count failure, silent and trusted. Rides beside
        # _last rather than in it: refresh() redraws the last GOOD state,
        # and whether it may is exactly what this flag knows.
        self._sight_lost = False
        # The villager band's read gap from the last poll, for the panel's
        # own staleness note. Kept on self for the same reason _last is:
        # refresh() must redraw the note as it stood, not lose it.
        self._villager_gap = None
        # The forensic log: one line per poll, raw readings beside believed
        # ones, under the data directory. The console dies with the session
        # (frozen builds never had one), so this is the record a live
        # incident leaves behind. Passed in by main() and defaulting to a
        # no-op, so the tests' controllers do not spend the log quota on
        # junk sessions. See loom/debuglog.py.
        from loom import __version__
        self.debuglog = log if log is not None else debuglog.NullLog()
        self.debuglog.line(f"loom {__version__} · build '{build.name}'"
                           f" ({build_stem}) · platform {sys.platform}")
        self._logged_hud = False
        self._logged_alerts = None

    def auto_index(self):
        """The step the READING implies, ignoring anything the player pressed.

        What a hotkey steps away from, and what following returns to.

        Once the build is done that is the LAST CARD, not the report. The
        report used to be the resting place, and it stole the panel: the
        final card of a build is "settle into this age, keep the unique
        units coming" - exactly what a player wants in front of them while
        the game goes on - and it survived on screen for one poll before
        the statistics replaced it. The author's ruling: the panel rests
        where the player still has work, the BUILD DONE note names the
        next-step key, and the report is one press forward for whoever
        wants it right now (see follow.tail).

        Deliberately not stored into _last[0], which stays the honest
        reading-derived index - other things downstream are entitled to ask
        where the GAME is, and must not be told about a resting place that
        exists only for the panel.
        """
        if self.pace.complete:
            return len(self.build.steps) - 2
        return -1 if self._last is None else self._last[0]

    def reviewing(self):
        """Is the build over, so a step key means reading rather than nudging?"""
        return self.pace.complete

    def refresh(self):
        """Redraw from the last reading. For after a hotkey changes the step."""
        if self._last is not None:
            self._render(*self._last)

    def tick(self):
        reading = self.hud.poll()

        # Where the reader anchored, once known - the first question any
        # log reader asks (which skin, what scale, how good a match).
        # getattr because the poll contract is just poll(): the end-to-end
        # tests' fake reader has no anchor to describe.
        if not self._logged_hud and getattr(self.hud, "hud", None):
            self._logged_hud = True
            found = self.hud.hud
            profile = found.get("profile")
            self.debuglog.line(
                f"hud {profile.name if profile else '?'}"
                f" scale {found.get('scale', 0):.2f}"
                f" score {found.get('score', 0):.3f}")
        self.debuglog.poll(reading)

        # The session saying it has LOST SIGHT of the game is the one event
        # nothing used to consume - the filters keep holding their beliefs,
        # is_usable() stays true on the held numbers, and the panel wore a
        # live face over a game it could no longer see. It is announced as
        # a SOFT band above the panel (see the alerts assembly below), and
        # stays announced until the session itself says the game is back: a
        # resumed game reports GAME_RESUMED, a new one GAME_STARTED. A band
        # rather than a takeover, by the author's ruling: the held step and
        # the hotkeys are still worth having in front of you - the panel
        # keeps working, it just stops pretending its numbers are fresh.
        if reading.event == session.TRACKING_LOST:
            self._sight_lost = True
        elif reading.event in (session.GAME_RESUMED, session.GAME_STARTED):
            self._sight_lost = False

        # A new match means starting the build again: everything in
        # fresh_each_game forgets the last game, the finished game's
        # statistics go to disk, and a new recorder takes over - one list,
        # one order, shared with the demo. See Hideable.start_fresh_game.
        if reading.event == session.GAME_STARTED:
            self.start_fresh_game()
            self._prereq_quiet_since = None

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

        # Refreshed every poll: None on the fresh ones, so the note clears
        # itself the moment the band reads again.
        self._villager_gap = reading.villager_gap

        villagers = reading.villagers
        game_time = reading.game_time

        # The age the tracker BELIEVES, never the raw reading. It is a floor
        # AND a ceiling on the step (see build_order.current_index), so a
        # single misread crest would move the panel and it would take
        # another two agreeing looks to come back. Two agreeing looks
        # before it counts, like every other belief in this loop. Read
        # before this poll's crest is fed in, so it trails the screen by at
        # most one poll - the debounce already costs two.
        believed_age = self.ages.age
        # The highest age whose click is proven, for the click gate:
        # the last step of an age is not done until its age-up is
        # clicked - "In Feudal: ... click Castle Age" is one card and
        # it must stay up until the click, not until its ideal time.
        clicked_through = self.ages.clicked_through

        delta = self.pace.update(villagers, game_time, believed_age,
                                 clicked_through)
        # The cursor and everything derived from it run on the player's
        # own clock - the fundamental decision that Loom follows the
        # player and the ideal timings judge them afterwards. Pace, the
        # recorder and the report stay on true game_time below; only
        # step-cursor questions use this.
        cursor_time = self.pace.player_time(game_time)
        extra = build_order.extra_villagers(self.build, villagers,
                                            cursor_time, believed_age,
                                            clicked_through)

        self.report.update(game_time, self.production, delta,
                           reading.queue, reading.game_events,
                           extra=extra, villagers=villagers)

        alerts_list = alerts.production_alerts(
            self.production, villagers, self.policy, game_time,
            reading.population, self.toggles, self.house_headroom)
        # The click-up band goes FIRST: when the build is waiting on an
        # age-up, clicking it is the instruction, and the production bands
        # are commentary on the wait.
        #
        # The band defers to the held card's own watched items (the
        # author's rule: the click needs the card's buildings standing,
        # so CLICK UP while they are going up nags toward a click the
        # game would refuse) - but only for so long. The suppression
        # rests on the reader having SEEN those buildings, and the reader
        # misses lines, so a patience clock in game seconds turns an
        # overstayed False back into "no verdict". The checklist verdict
        # is one poll behind (observe runs below), which the two-look
        # debounces everywhere already make irrelevant.
        prerequisites = self.checklist.click_prerequisites_settled(
            self.build.current_index(villagers, cursor_time, believed_age,
                                     clicked_through) + 1)
        # The clock runs only while the verdict is actually SILENCING a
        # band that would otherwise fire - started any earlier it would
        # be spent before the hold even began, and the suppression would
        # be dead on arrival.
        suppressing = (prerequisites is False
                       and self.ages.advancing is False
                       and build_order.held_by_age(
                           self.build, villagers, cursor_time,
                           believed_age, clicked_through))
        patience_spent = False
        if suppressing:
            if self._prereq_quiet_since is None:
                self._prereq_quiet_since = game_time
            elif (game_time - self._prereq_quiet_since
                    >= alerts.PREREQUISITE_PATIENCE_SECONDS):
                # Spent patience stays what it is rather than becoming
                # "no verdict": the policy gives a timer-driven reminder
                # the SOFT voice and keeps the flashing URGE for a gate
                # lifted by real observations.
                patience_spent = True
        else:
            self._prereq_quiet_since = None
        click_up = alerts.age_up_alert(self.build, villagers, cursor_time,
                                       believed_age, self.ages.advancing,
                                       clicked_through, prerequisites,
                                       patience_spent)
        if click_up:
            alerts_list.insert(0, click_up)
        # Sight lost outranks everything: every band below it is derived
        # from readings, and this one says the readings have stopped. SOFT
        # on purpose - it is information about the panel, not a failure of
        # the player's, and it rides the same channel as the other bands so
        # the preview shows it too when its alerts are on.
        if self._sight_lost:
            alerts_list.insert(0, ("LOST SIGHT OF THE GAME", alerts.SOFT))
        # Alert transitions only, not every poll's repetition: "when did
        # TC IDLE start and stop" is the forensic question.
        if alerts_list != self._logged_alerts:
            self._logged_alerts = alerts_list
            self.debuglog.line("alerts " + (", ".join(
                f"{text}:{severity}" for text, severity in alerts_list)
                if alerts_list else "clear"))
        self.panel.show_alerts(alerts_list)

        # The age, believed from the crest. Fed every poll, including the
        # ones where the reading is None - the tracker treats "not read" as
        # no news rather than as the age having gone away. The production
        # queue rides along as the second witness to a click: the two
        # monitors of "advancing" meet inside the tracker and nowhere
        # else, so everything downstream reads one reconciled belief.
        queued_age = next(
            (age_reader.QUEUE_IDENTITIES[slot.identity]
             for slot in (reading.queue or [])
             if slot.identity in age_reader.QUEUE_IDENTITIES), None)
        age_events = self.ages.update(reading.age, reading.game_time,
                                      queued_age)
        if reading.age is not None:
            self._last_age = reading.age

        # The feed's events tick the build's items off, and this is the one
        # place they may be applied: _render is also called by refresh()
        # after a hotkey, from the SAME reading, and an event applied twice
        # would credit two Houses to one "--House Built--".
        # The feed's events, plus houses the population cap evidenced that
        # the feed never printed. Synthetic built:house entries go ONLY to
        # the checklist - the statistics keep recording the raw feed, so
        # the stats file stays an honest record of what was actually read.
        cap = reading.population[1] if reading.population else None
        feed_houses = sum(1 for e in reading.game_events
                          if e == "built:house")
        new_houses = self.houses.update(cap, believed_age, feed_houses)
        # RECONCILED, not appended, twice over: houses against the
        # population cap, and age completions against the crest. In both
        # cases one real event produces two signals, the feed's copies are
        # dropped, and the better witness's verdict stands in for them.
        # The crest read every age transition in every capture with no
        # frame unread; the feed's "...Research Complete" line wraps and
        # often never reads - the author watched Feudal Age sit amber for
        # a whole game the crest had followed perfectly.
        checklist_events = checklist.merged_age_events(
            checklist.merged_house_events(reading.game_events, new_houses),
            [which for what, which in age_events
             if what == age_reader.REACHED])

        # current_index, not display_index: what was DONE is a different
        # question from what to SHOW, and only the first one belongs here.
        # See build_order.display_index. The age ceiling DOES belong: with
        # it the checklist cannot assume its way past an age boundary the
        # crest has not confirmed, which is exactly how one stray villager
        # once assumed half a build.
        self.checklist.observe(
            self.build.current_index(villagers, cursor_time, believed_age,
                                     clicked_through),
            checklist_events)

        # The statistics recorder watches the whole game, build and after.
        self.recorder.observe(game_time, villagers, delta, self.production,
                              reading.population, reading.queue,
                              reading.game_events, alerts_list, age_events)
        if self.recorder.due_flush():
            self.recorder.write(self.stats_file)

        # The build finishing is the payoff moment: the panel flips from
        # instructions to the report and rests there for the rest of the
        # game (the alert bands keep working - the game goes on).
        #
        # RESTS rather than locks. The report is a slot the step keys can
        # walk off, so that a player can look back at what the build asked
        # for after seeing how it went; _render decides which of the two to
        # draw from where the cursor is. Before this the branch drew the
        # report unconditionally every poll, which overwrote any step a
        # hotkey managed to reach within a third of a second of reaching it.
        if self.pace.complete:
            self.report.complete(game_time)
            self.recorder.snapshot_build(self.report, self.build)
            # One past the last step, and only now that there is something
            # to put there. follow.reset() takes it away again next match.
            self.follow.tail = 1

        # Every usable poll, complete or not: the report's status line stays
        # live this way, and a hotkey always redraws from a fresh reading
        # rather than from whatever was on screen when the build finished.
        self._last = (self.build.display_index(villagers, cursor_time,
                                               believed_age,
                                               clicked_through),
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
        # One clock reading for all three questions. Asking time.monotonic()
        # separately for the index, the mode and the countdown could land
        # them either side of the hold expiring, and a panel saying "resuming
        # in 1s" about a hold that has already gone is a small lie.
        now = time.monotonic()
        # self.auto_index() rather than the argument, which is the reading's
        # own index: once the build is done the panel's resting place is the
        # report, and only this knows that. The argument stays the reading's
        # answer for everything below that is entitled to ask where the GAME
        # is rather than where the panel is looking.
        index = self.follow.effective_index(self.auto_index(), now)
        mode = self.follow.mode(now)
        holding_for = self.follow.seconds_left(now)
        hint = self.resume_hint
        no_key = None
        if self.pace.complete and mode == follow.FOLLOWING:
            # Resting on the last card after the build finished. Not a
            # cursor state - follow.mode cannot know the build ended - so
            # it is substituted here, where both facts are in hand. The
            # note names the NEXT-STEP key: it leads to the report, and
            # that is the one thing the resting panel is quietly hiding.
            # With no key to name it offers the way to get one instead;
            # only this branch needs that, because MANUAL cannot be
            # reached without a working hotkey in the first place.
            mode = follow.DONE
            hint = self.step_hint
            no_key = self.step_trouble

        # One past the last card is a slot with no step in it, and once
        # the build is complete the only thing forward of the cards is
        # the report - so the dead slot shows it too, and the first
        # next-step press after BUILD DONE lands where the note promised.
        past_the_cards = (self.pace.complete
                          and self.build.active_step_at(index) is None)

        if self.follow.on_tail(index) or past_the_cards:
            # Parked on the report. Its status line carries the live clock
            # and villager count, so a finished build still shows the game
            # going on around it.
            self.panel.show_report(
                self.report.summary(self.build), self.build.name,
                f"{report.format_time(game_time)}   {villagers} villagers")
            self._emit(index, auto_index, mode, villagers, game_time,
                       delta, per_resource, population, holding_for)
            return

        active = self.build.active_step_at(index)

        # Only the ASSUMPTION half here - where the build has got to. The
        # sightings were applied in tick(), which is the only place that
        # sees each event once; see there.
        #
        # It follows the READING and never the cursor either way. Pressing
        # "next step" three times must not credit three steps of work - the
        # same reason pace, the report and the recorder all stay on
        # auto_index too.
        self.checklist.observe(auto_index)
        item_states = ()
        if active is not None:
            item_states = self.checklist.states(index + 1, len(active.items))

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
            resume_hint=hint,
            seconds_left=holding_for,
            item_states=item_states,
            villager_gap=self._villager_gap,
            no_key=no_key,
        )
        self._emit(index, auto_index, mode, villagers, game_time, delta,
                   per_resource, population, holding_for)

    def _emit(self, index, auto_index, mode, villagers, game_time, delta,
              per_resource, population, holding_for):
        """Announce this state to the preview.

        Its own method because the report path announces too. Before this the
        overlay went silent on the feed the moment a build completed, which
        froze the preview on whatever step it last heard about and left its
        clicks dead - so neither window could be moved for the rest of the
        match.
        """
        # Whole-number time and pace so the emitter's change-check works:
        # float jitter would otherwise make every poll look "new".
        self.feed.emit({
            "usable": True,
            "idx": index,
            # Where the GAME is, as opposed to where the panel is looking.
            # The preview ticks its checklist off this, not off "idx".
            "auto": auto_index,
            # What the HUD says about the age: [current, target, filled]
            # or null. The preview shows the same age the panel does rather
            # than working one out of its own.
            "age": ([self.ages.age,
                     self._last_age.target if self._last_age else None,
                     None if self._last_age is None
                     else self._last_age.progress]
                    if self.ages.age is not None else None),
            # Items the feed CONFIRMED. Assumptions are not sent - both
            # windows derive those from "auto" by the same rule - but a
            # sighting is a fact only this process can have.
            "ticks": self.checklist.observed_ticks(),
            "mode": mode,
            "vills": villagers,
            "t": int(game_time),
            "pace": None if delta is None else round(delta),
            "res": per_resource or None,
            "pop": population,
            # What the panel is showing, not a second opinion - see
            # statefeed.py. self.panel.alerts is the list AFTER the panel's
            # own trimming, so the preview cannot show a band the overlay
            # decided against.
            "alerts": [[text, severity]
                       for text, severity in self.panel.alerts],
            # So the preview can count down too, rather than working out a
            # deadline of its own from a mode string.
            "hold": holding_for,
        })


class DemoController(Hideable):
    """Replays a match with no game running, so the panel can be checked."""

    def __init__(self, panel, build, speed=20, build_stem="demo",
                 follow_state=None):
        self.panel = panel
        self.build = build
        self.speed = speed
        self.moment = 0
        self.last = max(s.time for s in build.steps if s.time is not None)
        self.pace = pace.PaceTracker(build)
        # Demo mode ticks items off as it walks the build, so the checklist
        # can be watched with no game running - the same reason it feeds the
        # preview below. Assumptions only, like the live path.
        self.checklist = checklist.Checklist(build)
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
        # The demo's own per-game state, same registry as the live path -
        # this list is why the replay loop cannot forget to reset something
        # the live path resets. No production/ages/houses: the demo has no
        # game to read them from.
        self.fresh_each_game = (self.pace, self.follow, self.checklist)
        self.resume_hint = resume_hint()
        self._last = None

    def auto_index(self):
        return -1 if self._last is None else self._last[0]

    def refresh(self):
        if self._last is not None:
            self._render(*self._last)

    def renew_recorder(self):
        """A fresh recorder into the SAME file, unlike the live override.

        The demo loops forever, and one file per loop filled the author's
        stats folder with a phantom game a minute. One demo, one file: the
        recorder starts over but the path stays, so however long the demo
        runs it leaves exactly one <stamp>_demo.json behind - enough to
        exercise the whole stats pipeline, which is the only reason demo
        mode writes statistics at all.
        """
        self.recorder, _ = start_recorder(self.build_stem, self.build.name)

    def tick(self):
        # Each tick advances game time by however many seconds the chosen
        # speed implies, so a whole match plays out in about a minute.
        self.moment += self.speed * (POLL_INTERVAL_MS / 1000.0)
        if self.moment > self.last + 60:
            # The demo's clock is its own, not an object with a reset() -
            # so it is put back to zero here, beside the registry walk.
            self.start_fresh_game()
            self.moment = 0

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
        now = time.monotonic()          # one reading, as in LiveController
        index = self.follow.effective_index(auto_index, now)
        mode = self.follow.mode(now)
        holding_for = self.follow.seconds_left(now)
        self.checklist.observe(auto_index)
        active = self.build.active_step_at(index)
        item_states = ()
        if active is not None:
            item_states = self.checklist.states(index + 1, len(active.items))
        self.panel.show_step(
            self.build,
            villagers,
            moment,
            self.build.active_step_at(index),
            self.build.following_step_at(index),
            delta,
            follow_mode=mode,
            resume_hint=self.resume_hint,
            seconds_left=holding_for,
            item_states=item_states,
        )
        self.feed.emit({
            "usable": True,
            "idx": index,
            "auto": auto_index,
            "mode": mode,
            "vills": villagers,
            "t": int(moment),
            "pace": None if delta is None else round(delta),
            "res": None,
            "pop": None,
            # Demo mode publishes them too, so the preview's bands can be
            # seen without a game - the same reason the demo scripts alerts
            # onto the panel at all.
            "alerts": [[text, severity]
                       for text, severity in self.panel.alerts],
            # So the preview can count down too, rather than working out a
            # deadline of its own from a mode string.
            "hold": holding_for,
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
        # The blue urge band, in its own window so all three severities can
        # be told apart at a glance: red flashing, blue flashing, then the
        # still soft one.
        if 340 <= self.moment < 400:
            found.append(("CLICK UP — Feudal Age", alerts.URGE))
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

    For an overlay reported to vibrate. place_panel runs at startup, again
    when a match is found, and now whenever a live settings change resizes
    the panel - but nothing moves it between those, so this answers the
    question
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


class LiveSession(Hideable):
    """The overlay's whole life in live mode: waiting, then following.

    This exists because the panel now appears BEFORE the game does. Loom
    used to find the game window and then a match with two open-ended
    blocking loops, and only call panel.show() afterwards - so a player who
    started Loom first saw nothing at all until a match began, which looks
    exactly like a program that failed to launch.

    Showing the window earlier is not enough by itself. Those loops ran
    before app.exec(), so with no event loop turning, a mapped window never
    gets a paintEvent: it would have been a white ghost over the menus for
    as long as the wait lasted. So the waiting happens HERE, one step per
    timer tick, inside the event loop that is already running.

    Two things fall out of that which are worth having on purpose. The
    launcher's Stop now works during the wait - stopline queues app.quit()
    onto the event loop, which did nothing at all while main() was blocked,
    so stopping was left to runner.py escalating to terminate() and then
    kill(), with aboutToQuit and the final statistics write never running.
    And the hotkeys can be registered before a match, which is what makes it
    possible to hide a panel that is sitting over the menus.

    Everything downstream - the timer, the hotkeys, the quit hook - talks to
    this object rather than to the controller, because the controller does
    not exist yet when they are wired up.
    """

    def __init__(self, panel, build, app, follow_state, build_stem):
        self.panel = panel
        self.build = build
        self.app = app
        self.follow = follow_state
        self.build_stem = build_stem

        self.hud = reader.HudReader()
        self.controller = None      # until a match is found
        self.hidden = False
        self._stage = None
        self._checked_passthrough = False
        self._looks = 0                 # failed looks for a HUD
        self._warned_unreadable = False

        self.show_waiting(overlay.WAITING_FOR_GAME)
        # The launcher reads this as "no game to follow, browsing allowed",
        # so its build preview is usable from the moment Loom starts rather
        # than from the first poll of a match.
        print(statefeed.encode({"usable": False}))

    # ---- waiting -------------------------------------------------------

    def show_waiting(self, stage):
        """Put the build on the panel with the banner for this stage."""
        if stage == self._stage:
            return
        self._stage = stage
        self.panel.show_pregame(self.build, stage)

    def tick(self):
        """One turn of the timer: acquire, or follow."""
        if self.controller is not None:
            self.controller.tick()
            return

        try:
            if self.hud.window is None:
                # Check once and come back; connect() also loads every
                # template when it succeeds, which is a few hundred
                # milliseconds and deserves a tick of its own rather than
                # being bolted onto a search that usually finds nothing.
                if not self.hud.connect(wait_seconds=0):
                    return
                print("Waiting for a match to start...")
                self.show_waiting(overlay.WAITING_FOR_MATCH)
                return
            if not self.hud.find_hud():
                self._still_looking()
                return
        except capture.CaptureError as problem:
            # Not being able to read the screen is a condition Loom
            # understands, not a bug in it: the game may be in exclusive
            # fullscreen, minimised, or mid-restart. Said once and then
            # kept quiet about, because the next attempt is one poll away.
            #
            # This used to quit. That was carried over from when the wait
            # was a blocking loop in front of the event loop, where the
            # only thing an unreadable screen could mean was a startup
            # that had failed - and it turned a passing hiccup into an
            # overlay the player had to start again. There is no case for
            # switching Loom off while it waits: waiting IS the feature.
            if not self._warned_unreadable:
                self._warned_unreadable = True
                print(f"Cannot read the game yet: {problem}")
                print("Still watching - Loom will pick the match up as soon "
                      "as it can read the screen.")
            self._still_looking()
            return

        self._start_following()

    def _still_looking(self):
        """One more failed look at a game that has not shown a HUD yet.

        Two things happen on a schedule here, and neither of them is giving
        up. Loom keeps trying until it is stopped, because there is no
        version of "waiting for a match" that is improved by ceasing to
        watch for one.
        """
        self._looks += 1

        # Ask which window the game is in again. connect() answered that
        # once, and a handle that was right at the main menu is not always
        # right by the time a match is running - see reader.reacquire_window.
        if self._looks % REACQUIRE_EVERY_NTH_LOOK == 0:
            if self.hud.reacquire_window():
                print("Reconnected to the game window - reading that from "
                      "now on.")

        # And say something useful, once, to a player who is sitting in a
        # match wondering why nothing has happened. reader.find_hud already
        # explains a HUD it nearly recognised; this is for the case where it
        # recognised nothing at all, which that note stays silent about.
        if self._looks == LOOKS_BEFORE_EXPLAINING:
            print("No match detected yet. If one really is on screen, the "
                  "likely causes are the in-game HUD scale, the resolution, "
                  "or a UI mod whose artwork Loom has no templates for - see "
                  "the launcher's How to use page. Loom will keep watching "
                  "either way.")

    def _start_following(self):
        """A match is on screen: hand over to the live controller."""
        print(f"HUD found (match {self.hud.hud['score']:.3f}, "
              f"scale {self.hud.hud['scale']:.2f})")

        # The panel has been sitting at the fallback origin at scale 1.0.
        # Now that the game window has been measured it can go where it
        # belongs, under the bar at the size the bar is actually drawn.
        display = capture.open_display()
        window = capture.find_game_window(display)
        origin_x, origin_y, area_width, _ = capture.window_geometry(
            window, display)
        place_panel(self.panel, origin_x, origin_y, area_width,
                    hud_scale=self.hud.hud["scale"],
                    screens=screen_rects(self.app))
        # The real numbers now, replacing the startup guess against the
        # primary screen.
        self.placed_against = (origin_x, origin_y, area_width,
                               self.hud.hud["scale"])

        # The real overlay gets a real forensic log; the tests' controllers
        # default to the no-op. Its path is printed once so "where is the
        # log" has an answer even when this console survives.
        session_log = debuglog.SessionLog()
        self.controller = LiveController(
            self.panel, self.build, self.hud, build_stem=self.build_stem,
            follow_state=self.follow, log=session_log)
        print(f"Overlay running on '{self.build.name}'. "
              f"{stopline.quit_hint()}.")
        if session_log.path is not None:
            print(f"Session log: {paths.for_display(session_log.path)}")

    # ---- what the hotkeys and the quit hook need -----------------------

    def auto_index(self):
        return 0 if self.controller is None else self.controller.auto_index()

    def refresh(self):
        if self.controller is not None:
            self.controller.refresh()

    def following_yet(self):
        # Overrides Hideable's cheerful default: before a match there is no
        # controller, so no step to move and nothing to redraw from.
        return self.controller is not None

    def reviewing(self):
        return self.controller is not None and self.controller.reviewing()

    def finish(self):
        if self.controller is not None:
            self.controller.finish()


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
    requests = LauncherRequests()
    stopline.watch(lambda: QMetaObject.invokeMethod(
        app, "quit", Qt.ConnectionType.QueuedConnection),
        on_toggle_hidden=requests.requested.emit,
        on_settings=requests.settings_changed.emit)

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
        print(f"Demo mode at {args.speed}x. {stopline.quit_hint()}.")
    else:
        # The panel goes up NOW, showing the build with its numbers at zero
        # and a banner saying what it is waiting for. Finding the game and
        # then a match happens on the timer below, inside the event loop -
        # see LiveSession for why it cannot happen before it.
        print("Waiting for the Age of Empires II window... "
              f"({stopline.quit_hint()})")
        controller = LiveSession(panel, build, app, follow_state,
                                 build_stem=args.build)

    # Demo mode assumes a 1.0 HUD; live mode has not measured one yet and
    # starts from the same guess against the primary screen, then places
    # itself properly against the game window once a match is found.
    place_panel(panel, origin_x, origin_y, area_width, hud_scale=1.0,
                screens=screen_rects(app))
    # Kept so a live size change can put the panel back against the same
    # corner it was measured from - see Hideable.apply_appearance.
    controller.placed_against = (origin_x, origin_y, area_width, 1.0)

    # "No overlay" in the build preview is a REMEMBERED preference about how
    # the panel starts, not the same thing as whether it is hidden right now.
    # Someone playing from the preview on a second monitor should not have to
    # hide the panel by hand every time they press Start; the Hide button and
    # Ctrl+Shift+0 stay the this-session version and are never written down.
    #
    # Demo and placement modes never get here, and deliberately: pressing
    # "Overlay demo" or "Place overlay" is asking to LOOK at the panel, and
    # obeying a preference that hides it would make both buttons look broken.
    if config.overlay_disabled() and not args.demo:
        controller.begin_hidden()
    else:
        panel.show()

    # Now that there is something to hide, the launcher's Hide button has
    # somewhere to land. Its requests were being read from stdin before
    # this and simply went nowhere, which is the right answer for a button
    # pressed in the split second before the panel existed.
    requests.controller = controller

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
