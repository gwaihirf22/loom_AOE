"""
Loom — reading the game, all in one place.

Everything needed to turn "the game is running" into "the player has N
villagers and the clock says T": find the window, locate the HUD, read the
digits, filter out misreads, and notice when a game starts or ends.

This exists so that more than one front end can use it. The terminal coach and
(later) the overlay both need exactly this and should not each have their own
copy of it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import time

from . import (anchor, capture, digits, filters, glyphs, notifications,
               queue, resources, session)

# Below this template-match score I assume the HUD is not visible - a menu, a
# loading screen, or the fade at the start of a match.
MIN_ANCHOR_SCORE = 0.8

# How many unreadable polls in a row before I go looking for the HUD again.
FAILURES_BEFORE_REANCHOR = 10


def min_glyph_width(scale):
    """The narrowest column run still worth treating as a character.

    Its job is to skip specks, so it has to stay BELOW the narrowest real
    digit at every HUD scale. "1" is that digit, and it is far thinner than
    its siblings - measured 7px against 12-13px for the rest in the same
    population band.

    This used to be int(6 * scale), which overtakes "1" as the HUD grows: at
    scale 1.37 it returned 8 and the reader skipped a 7px "1", turning a
    population of 19/25 into 9/25 and 18 villagers into 8. A silently halved
    villager count is the exact failure Loom is built to refuse, and it was
    reporting it confidently.

    The multiplier is deliberately gentle now. Measured against every clock
    fixture in tests/data/clock, all four read correctly at 2-5 while the old
    formula's value of 6 already broke two of them at scale 1.0 - so the
    tests were only passing because they hand in a gentler number than the
    runtime used. Nothing is lost by going low: specks are dropped by shape in
    digits._keep_text_shapes long before this, and anything that survives
    still has to beat digits.MIN_MATCH_SCORE to be called a digit.

    The floor is 3, not 4, for the same reason the multiplier came down. A
    shrunken HUD puts "1" at three and a half pixels, so a floor of 4 outgrows
    the digit exactly as the old multiplier did, only from the other end.
    """
    return max(3, int(4 * scale))


class Reading:
    """What one poll of the screen produced."""

    def __init__(self, villagers, game_time, event, hud_visible,
                 raw_villagers=None, raw_clock=None, per_resource=None,
                 queue_slots=None, population=None, raw_population=None,
                 game_events=None):
        # What Loom believes, after filtering.
        self.villagers = villagers
        self.game_time = game_time

        # A session event this poll, if any ("game_started", etc).
        self.event = event

        # Whether the digits could be read at all this poll.
        self.hud_visible = hud_visible

        # Villagers on each resource this poll: {name: count}. Advisory only -
        # shown to the player, never used to decide the build-order step. May be
        # missing resources, or empty, and that is fine.
        self.per_resource = per_resource or {}

        # Population as shown by the HUD: (current, cap), or None when it
        # could not be read. This is what "housed" is judged from - the
        # population indicator is the ground truth for housing, where the
        # queue's red wash turned out to be a lie factory (bare skin votes
        # red). Filtered like the villager count: seen twice to be believed.
        self.population = population

        # The global production queue, as a list of queue.SlotReading in
        # reading order, or None when the queue could not be read this poll.
        # None and [] mean different things: [] is a confirmed empty queue
        # (idle production - the thing worth shouting about), None is "could
        # not see it", which must never trigger an alert.
        self.queue = queue_slots

        # Phrases newly sighted in the game's own notification feed this
        # poll ("town_center_built", ...). Each appears exactly once per
        # on-screen appearance - the watcher debounces the lingering text.
        self.game_events = game_events or []

        # The unfiltered readings, useful for debugging.
        self.raw_villagers = raw_villagers
        self.raw_clock = raw_clock
        self.raw_population = raw_population   # (cur, cap) this poll, or None

    def is_usable(self):
        """True when there are two numbers worth acting on."""
        return self.villagers is not None and self.game_time is not None


class HudReader:
    """Reads the two HUD numbers from a running game, poll by poll."""

    def __init__(self):
        self.window = None
        self.hud = None

        self._display = None
        self._icon_template = None
        self._digit_templates = None
        self._resource_templates = None
        self._resource_regions = {}
        self._queue_reader = None
        self._notifications = None
        self._text_watcher = None

        self.last_population_band = None
        self._population_misses = 0

        self._villager_filter = filters.StableCount(required_repeats=2)
        self._clock_filter = filters.StableClock(max_step=30, required_repeats=2)
        # The (current, cap) pair filters as one value: both numbers come from
        # the same glyph run, so believing them separately could pair a fresh
        # current with a stale cap.
        self._population_filter = filters.StableCount(required_repeats=2)
        self._session = session.GameSession()

        self._failures_in_a_row = 0

    # ---- setting up ----------------------------------------------------

    def connect(self, wait_seconds=None, poll_interval=1.0):
        """Find the game window, waiting for it to appear if asked.

        wait_seconds=None means wait forever - the friendly mode for a
        companion tool that gets started before the game does. Pass 0 for the
        old check-once behaviour. Returns False only when a deadline was set
        and passed; Ctrl+C is the way out of the forever wait.
        """
        self._display = capture.open_display()
        deadline = None if wait_seconds is None else time.monotonic() + wait_seconds
        while True:
            self.window = capture.find_game_window(self._display)
            if self.window is not None:
                break
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(poll_interval)

        self._icon_template = anchor.load_template()
        self._digit_templates = digits.load_digit_templates()
        self._resource_templates = resources.load_resource_templates()

        # The queue reader is optional equipment: without its templates the
        # rest of Loom still works, it just cannot see production.
        try:
            self._queue_reader = queue.QueueReader()
        except FileNotFoundError as missing:
            print(f"Queue reading disabled: {missing}")
            self._queue_reader = None

        # So is the notification watcher: no phrase templates just means no
        # game events, never a crash. The text watcher is its glyph-path
        # sibling - an empty font means it stays quiet the same way.
        self._notifications = notifications.NotificationWatcher()
        self._text_watcher = glyphs.TextWatcher()
        return True

    def window_size(self):
        return capture.window_size(self.window)

    def find_hud(self):
        """Locate the HUD and work out where the numbers are.

        This is the slow step - a few hundred milliseconds - which is why it
        runs once rather than on every poll. The HUD does not move during a
        game, so the regions stay valid until something changes.
        """
        frame = capture.capture_window(self.window)
        found = anchor.locate_regions(frame, self._icon_template)

        if found is None or found["score"] < MIN_ANCHOR_SCORE:
            return False

        height, width = frame.shape[:2]
        self.hud = {
            "scale": found["scale"],
            "score": found["score"],
            "villagers": _clamp(found["villagers"], width, height),
            "clock": _clamp(found["clock_band"], width, height),
            "population": _clamp(found["population"], width, height),
            # Digits get thinner as the HUD shrinks, so the speck test has to
            # shrink with it - and must never overtake the narrowest digit.
            "min_glyph_width": min_glyph_width(found["scale"]),
        }

        # The resource icons sit in the same bar and do not move either, so
        # locate their number regions once too. A failure here is not fatal:
        # per-resource counts are a nicety, and the rest of Loom works without
        # them.
        self._resource_regions = resources.locate_regions(
            frame, self._resource_templates, found["scale"])

        # Loom reads best with the in-game HUD scale at 100%: every other
        # region scales off the anchor and follows along, but recognition
        # quality drops as icons shrink or grow away from the templates.
        # Say so once, honestly, rather than failing mysteriously later.
        if abs(found["scale"] - 1.0) > 0.05:
            print(f"note: HUD scale looks like ~{found['scale'] * 100:.0f}% "
                  "- Loom reads best at 100% (Options > Interface).")
        return True

    def wait_for_hud(self, poll_interval=2.0):
        """Keep looking for the HUD until a match is actually on screen.

        The window existing just means the game launched; the player may sit
        in menus or a lobby for minutes. Each find_hud attempt costs a few
        hundred milliseconds, so a couple of seconds between tries keeps the
        wait cheap without feeling slow to connect.
        """
        while not self.find_hud():
            time.sleep(poll_interval)

    # ---- reading -------------------------------------------------------

    def poll(self):
        """Read the screen once and return a Reading."""
        raw_villagers, _ = digits.read_count(
            self._read_region(self.hud["villagers"]),
            self._digit_templates,
            self.hud["min_glyph_width"],
        )
        raw_clock, _ = digits.read_clock_seconds(
            self._read_region(self.hud["clock"]),
            self._digit_templates,
            self.hud["min_glyph_width"],
        )

        villagers = self._villager_filter.update(raw_villagers)
        game_time = self._clock_filter.update(raw_clock)

        # The band crop is kept on the reader so a diagnostic front end can
        # save the exact pixels behind a failed read. One small array,
        # overwritten every poll - not a leak.
        self.last_population_band = self._read_region(self.hud["population"])
        raw_population = digits.read_population(
            self.last_population_band,
            self._digit_templates,
            self.hud["min_glyph_width"],
        )
        if raw_population[0] is None:
            raw_population = None
        population = self._population_filter.update(raw_population)

        # A stale population is worse than none: the filter holds its last
        # belief through unreadable polls (right for the villager count),
        # but population drives the HOUSED alerts - a "4/5" held for a
        # minute keeps shouting HOUSE NOW long after the real pop moved on.
        # After ~3 seconds of failed reads the belief expires; it returns
        # the moment a fresh pair is confirmed.
        if raw_population is None:
            self._population_misses += 1
            if self._population_misses >= 10:
                population = None
        else:
            self._population_misses = 0

        # Read each resource's yellow number. Advisory only, and unfiltered:
        # they change slowly enough that a rare misread is corrected on the
        # next poll, and nothing important depends on them.
        per_resource = {}
        for name, region in self._resource_regions.items():
            count = resources.read_one(
                self._read_region(region),
                self._digit_templates,
                self.hud["min_glyph_width"],
            )
            if count is not None:
                per_resource[name] = count

        # The global queue lives in the top-left corner, so only that strip
        # of the window gets captured for it. Skipped entirely when the HUD
        # numbers were unreadable - a menu is up, so the queue is not there.
        queue_slots = None
        if self._queue_reader is not None and (raw_clock is not None
                                               or raw_villagers is not None):
            strip_w, strip_h, _ = queue.strip_extent(self.hud["scale"])
            strip = capture.capture_region(self.window, 0, 0, strip_w, strip_h)
            queue_slots = self._queue_reader.read(strip, self.hud["scale"])

        # The notification feed: the game stating events as words. Only read
        # while the HUD is up, and only when there is a clock to drive the
        # de-duplication cooldown. Two readers share the one panel capture:
        # the phrase watcher (authoritative for the events live logic acts
        # on) and the glyph-path text watcher (the open vocabulary that
        # fills the statistics).
        game_events = []
        phrase_ready = (self._notifications is not None
                        and self._notifications.templates)
        text_ready = (self._text_watcher is not None
                      and self._text_watcher.font)
        if ((phrase_ready or text_ready) and game_time is not None
                and (raw_clock is not None or raw_villagers is not None)):
            width, height = capture.window_size(self.window)
            x1, y1, x2, y2 = notifications.panel_region(width, height)
            panel = capture.capture_region(self.window, x1, y1,
                                           x2 - x1, y2 - y1)
            if phrase_ready:
                game_events = self._notifications.watch(
                    panel, self.hud["scale"], game_time)
            if text_ready:
                # The phrase watcher OWNS the names it has templates for
                # (town_center_built, attacked, ...): the two watchers run
                # independent cooldowns, so the same on-screen line can
                # read as "new" to each on DIFFERENT polls - and a
                # duplicate town_center_built mints an imaginary TC. The
                # glyph path only contributes vocabulary the phrase
                # watcher does not cover.
                claimed = set(self._notifications.templates
                              if phrase_ready else ())
                for event in self._text_watcher.watch(panel, game_time):
                    if event not in claimed and event not in game_events:
                        game_events.append(event)

        # Tell the session tracker the truth about this poll. Passing on a
        # stale value would stop it ever noticing that the game went away.
        event = self._session.update(
            game_time if raw_clock is not None else None,
            villagers,
        )
        # A new match means the old game's lingering notifications are gone.
        if event == session.GAME_STARTED:
            if self._notifications is not None:
                self._notifications.reset()
            if self._text_watcher is not None:
                self._text_watcher.reset()

        hud_visible = raw_clock is not None or raw_villagers is not None
        if hud_visible:
            self._failures_in_a_row = 0
        else:
            self._failures_in_a_row += 1
            # Lost it for a while: the HUD may have moved, or a new game may
            # have started at a different resolution. Go and find it again.
            if self._failures_in_a_row >= FAILURES_BEFORE_REANCHOR:
                self.find_hud()
                self._failures_in_a_row = 0

        return Reading(villagers, game_time, event, hud_visible,
                       raw_villagers, raw_clock, per_resource, queue_slots,
                       population, raw_population, game_events)

    def _read_region(self, region):
        x1, y1, x2, y2 = region
        return capture.capture_region(self.window, x1, y1, x2 - x1, y2 - y1)


def _clamp(region, width, height):
    """Keep a region inside the window, so I never ask X for pixels that do
    not exist."""
    x1, y1, x2, y2 = region
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(x1 + 1, min(x2, width))
    y2 = max(y1 + 1, min(y2, height))
    return x1, y1, x2, y2
