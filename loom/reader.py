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

import os
import time

from . import (anchor, capture, digits, filters, glyphs, hud, notifications,
               queue, resources, session)

# Below this template-match score I assume the HUD is not visible - a menu, a
# loading screen, or the fade at the start of a match.
MIN_ANCHOR_SCORE = 0.8

# Below this HUD scale the digits get thin enough to be worth warning about.
#
# The direction that matters. A HUD too LARGE is simply not found, which is
# loud and self-explaining; a HUD too small is found perfectly well and then
# misreads - the "18 villagers read as a confident 8" family - which is the
# exact failure the never-guess-a-reading rule exists for, and it is silent.
#
# 0.6 is chosen against what is known rather than measured at the boundary:
# 1920x1080 puts the HUD at ~0.74x and reads correctly, so the real edge is
# somewhere below that, and this sits between the two without claiming to be
# the edge itself. It only ever prints a note; nothing behaves differently.
SMALL_HUD_SCALE = 0.6

# One in how many failed acquisitions also looks for an oversize HUD.
#
# wait_for_hud retries twice a second and can run for the whole of a menu or a
# lobby, so anything it does every time is paid for minutes on end. 4 puts the
# extra sweep on a two-second cadence: a big HUD is found within two seconds of
# a match starting, which nobody notices, and the idle cost rises by a quarter
# of one sweep instead of a whole one.
LARGE_HUD_EVERY_NTH = 4

# How many unreadable polls in a row before I go looking for the HUD again.
FAILURES_BEFORE_REANCHOR = 10

# When one whole poll costs more than this, the machine is overloaded and the
# advisory readers drop to a slower cadence. Well above a healthy poll (about
# 100ms on the Linux box) and well below the pathology it exists for: under a
# 4K game's CPU load, polls measured 0.5-5.5 SECONDS, so the overlay was
# showing values read seconds ago - which the player experiences as the
# overlay running behind the game.
POLL_BUDGET = 0.5

# How often each advisory reader runs while overloaded: every Nth POLL, not
# every N seconds. The first version used wall-time intervals and shed
# nothing at all - with polls taking two seconds, a one-second interval has
# always expired by the next poll, so every reader ran every poll anyway.
# Counting polls throttles the WORK SHARE whatever the cadence is.
#
# The sync signals (villager count, clock, population) are never shed - they
# are cheap and they are the point. These three tolerate gaps by
# construction: a skipped queue poll returns None, which the production
# tracker already treats as "no news"; notification lines persist on screen
# for several seconds, and the watchers de-duplicate on their own cooldowns;
# per-resource counts are advisory display only.
ADVISORY_EVERY_NTH_POLL = {
    "queue": 2,
    "notifications": 3,
    "resources": 5,
}


def min_glyph_width(scale, profile=None):
    """The narrowest column run still worth treating as a character.

    The formula itself belongs to the HUD skin (see loom/hud.py): the stock
    bar draws a font about four fifths the size of the mod's, and the value
    below - correct for the mod for months - silently discarded stock's
    3px-wide slash, so population read as nothing on a legible HUD. The
    override and the reasoning stay here, where they have always been.

    Two jobs pull this number in opposite directions. In the clock band it is
    the colon test - runs narrower than it are the colons between HH:MM:SS -
    so it must sit ABOVE the colons. In every band it must sit BELOW the
    narrowest digit, which is "1", or real ones get silently skipped.

    max(4, int(6 * scale)) held both jobs for months of live play at scale
    ~1.0, and that service record is the evidence that matters. It has ONE
    measured failure: above scale ~1.15 it overtakes the "1", which does not
    grow as fast as the anchor icon does - at anchor scale 1.37 the formula
    said 8 while the "1" measured 7px, so a population of 19/25 read as 9/25
    and 18 villagers as 8, confidently. Hence the cap: never past 6, which
    stays under the thinnest "1" yet measured while every reading on the 4K
    frame that exposed the bug still parses (villagers, population and clock
    all read at width 6 there).

    A CAUTIONARY NOTE, expensively learned. An earlier version replaced the
    formula wholesale with int(4 * scale), justified by the clock fixtures in
    tests/data/clock reading at widths 2-5 while 6 broke two of them. Those
    fixtures are RESCALED screenshots - their own docstring says so - not
    native-size crops, so they describe smaller glyphs than the live game
    ever shows. The gentler value promptly caused live misreads on BOTH
    platforms: wrong villager counts, a clock that lagged as StableClock
    kept rejecting garbage, and an overlay that could no longer attach to a
    match in progress (mid-game clock values misread too often to ever
    confirm; only the mostly-zeros opening read reliably). Fixture evidence
    is not live evidence.

    LOOM_MIN_GLYPH_WIDTH overrides the answer outright, so the number can be
    A/B-tested against a live game in seconds rather than by editing code:

        LOOM_MIN_GLYPH_WIDTH=5 python loom_read.py

    Watch the `raw:` column and the villager-JUMP lines.
    """
    override = os.environ.get("LOOM_MIN_GLYPH_WIDTH")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return (profile or hud.DEFAULT).min_glyph_width(scale)


def max_glyph_width(scale, profile=None):
    """How wide one character may be before it is two characters touching.

    The sibling mistake to min_glyph_width, and found the same way. The
    population parser splits any run wider than this at its thinnest column,
    which is right for a slash brushing the digit after it and disastrous for
    a digit that is simply large: measured at HUD scale 1.48, a "4" is 15px
    and a "5" is 14px, so the fixed 13 halved both and "4/5" read as nothing.

    Never below the reference 13, so at HUD scale 1.0 and under this is
    exactly the constant that has always been used and nothing changes. Erring
    LARGE is the safe direction: too large merely leaves a genuine pair fused,
    which fails to classify and reports no reading, while too small carves a
    real digit into halves that classify as something else entirely.

    Like its sibling, the formula is the HUD skin's - a smaller font needs a
    smaller "too wide to be one glyph" mark.
    """
    return (profile or hud.DEFAULT).max_glyph_width(scale)


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
        self._icon_templates = {}
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
        # Whether the last poll could see the screen at all, so the complaint
        # is printed on the transition rather than three times a second.
        self._capture_failed = False

        # Whether the "your HUD is too big to read" note has been printed;
        # once is information, every two seconds is noise.
        self._warned_oversize = False
        # Same, for "something is there but no skin fits it well enough".
        self._warned_weak_anchor = False
        # Same again, for a HUD small enough that the digits get unreliable.
        self._warned_small_hud = False

        # How many times the common range has come up empty, so the search for
        # a larger HUD can run on its own slower cadence. See find_hud.
        self._large_hud_attempts = 0

        # Load shedding: how long the previous poll took, which poll this is,
        # and the poll number each advisory reader last ran on. See
        # POLL_BUDGET.
        self._last_poll_cost = 0.0
        self._poll_number = 0
        self._advisory_ran = {}
        self._per_resource_cache = {}

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

        # One anchor template per HUD skin. Which one the player is running is
        # not knowable in advance - mods only switch when the game restarts -
        # so both are loaded and find_hud lets the pixels decide.
        self._icon_templates = {profile: anchor.load_template(profile)
                                for profile in hud.PROFILES}
        # The second opinion that tells the skins apart - see identify_hud.
        self._wood_templates = {profile: queue.load_wood_template(profile)
                                for profile in hud.PROFILES}
        self._digit_templates = digits.load_digit_templates()
        # Per skin, like the anchors: the four icons are skin art too, and
        # the mod's food icon matches a stock bar at 0.62 out in the terrain.
        self._resource_templates = {
            profile: resources.load_resource_templates(profile)
            for profile in hud.PROFILES}

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

    def reacquire_window(self):
        """Look for the game window again, keeping the loaded templates.

        connect() latches onto whichever window answered when it ran, and
        that is not always the one a match ends up being drawn in. Start the
        overlay at the main menu, sit there a while, then begin a game and
        the HUD may never be found - not because the anchor search is wrong
        but because the frames being searched come from a window that is no
        longer the game's. Restarting the overlay fixed it, which is exactly
        the shape of a stale handle.

        Cheap on purpose, so the waiting loop can afford to call it: this
        re-runs the window search only. The templates cost a few hundred
        milliseconds to load and none of them depend on which window is on
        screen, so connect()'s work is deliberately not repeated.

        It heals a second failure for free, and that one may matter more:
        capture.find_game_window restarts a stream that has stopped
        delivering, so a session which died quietly - the thing restarting
        the overlay was really fixing - is rebuilt without the player having
        to notice or act.

        Returns True when what it found is not the object already in use,
        which covers both cases: a different window, and the same window
        behind a fresh stream.
        """
        if self._display is None:
            return False
        try:
            window = capture.find_game_window(self._display)
        except capture.CaptureError:
            # The game may be mid-restart, or a stream may have failed to
            # start. Neither is a reason to give up: the next attempt is
            # one poll away.
            return False
        if window is None:
            return False
        changed = window is not self.window
        self.window = window
        return changed

    def find_hud(self):
        """Locate the HUD and work out where the numbers are.

        This is the slow step - a few hundred milliseconds - which is why it
        runs once rather than on every poll. The HUD does not move during a
        game, so the regions stay valid until something changes.

        A capture failure here reads as "no HUD yet", not as an error. This is
        called from wait_for_hud, which is happy to keep waiting, and from the
        re-anchor path inside a poll, where raising would defeat the point of
        poll() guarding itself.
        """
        try:
            frame = capture.capture_window(self.window)
        except capture.CaptureError:
            return False

        # Re-anchoring can say what size the HUD was last time, which turns a
        # full scale sweep into a nine-step one. The HUD does not resize during
        # a match - changing the in-game scale needs an overlay restart anyway -
        # so the old scale is a good guess. It matters because this runs INSIDE
        # poll() on the re-anchor path: at 4K the full sweep took 15 seconds,
        # and the overlay is frozen for every one of them.
        known_scale = self.hud.get("scale") if self.hud else None

        # Once a skin has been identified, re-anchoring only re-checks that
        # one: the player cannot swap a UI mod without restarting the game, so
        # the answer cannot change inside a session. It still goes through
        # identify_hud rather than locate_regions, so that "score" means the
        # same thing on both paths - two icons agreeing, not one matching.
        known_profile = self.hud.get("profile") if self.hud else None
        candidates = ({known_profile: self._icon_templates[known_profile]}
                      if known_profile is not None else self._icon_templates)
        found = anchor.identify_hud(frame, candidates, known_scale,
                                    self._wood_templates)

        # A narrow sweep can miss if the HUD really did change size. Falling
        # back to the full hunt costs a slow poll once, where not falling back
        # would mean never finding the HUD again. The fallback re-opens the
        # skin question too - if the old skin no longer fits, another might.
        if (found is None or found["score"] < MIN_ANCHOR_SCORE) and known_scale:
            found = anchor.identify_hud(frame, self._icon_templates,
                                        wood_templates=self._wood_templates)

        # Still nothing in the common range: the HUD may simply be BIGGER than
        # that range looks. A 4K screen at the game's own 100% HUD scale puts
        # it near 2.6x, and Loom used to refuse a HUD it reads perfectly well.
        #
        # A discovery sweep answers "is it about this big?", and identify_hud
        # is then asked again with that as a near_scale - the cheap nine-step
        # path re-anchoring already uses - so the placing costs almost nothing.
        #
        # Every skin's template, not just the default one. The anchor is the
        # same game art in every skin and I expected one template to do; the
        # measurement says otherwise, because the bar AROUND the icon is not
        # shared. Anne_HK's anchor scores 0.68-0.79 on a stock HUD, under the
        # 0.80 this gate needs, so a default-only sweep found nothing at all on
        # exactly the skin most people run.
        #
        # Only every Nth attempt, and that is the whole reason this lives here
        # rather than inside find_icon. wait_for_hud calls this twice a second
        # for as long as the player sits in a menu; paying an extra sweep every
        # time took a blank 4K frame from 234ms to 500ms an attempt. The HUD
        # cannot change size mid-session anyway - that needs an overlay restart
        # - so looking every few seconds finds it just as surely for a fraction
        # of the idle cost.
        if found is None or found["score"] < MIN_ANCHOR_SCORE:
            self._large_hud_attempts += 1
            if self._large_hud_attempts % LARGE_HUD_EVERY_NTH == 0:
                for template in self._icon_templates.values():
                    bigger = anchor.larger_icon_scale(frame, template)
                    if bigger is None:
                        continue
                    found = anchor.identify_hud(
                        frame, self._icon_templates, bigger,
                        self._wood_templates)
                    if found is not None and found["score"] >= MIN_ANCHOR_SCORE:
                        break

        if found is None or found["score"] < MIN_ANCHOR_SCORE:
            # A HUD that is FOUND but scores under the gate used to be
            # indistinguishable from no HUD at all: wait_for_hud looped on the
            # silent False forever while "Waiting for a match to start..."
            # stayed on screen. That is exactly what the stock HUD did (0.74
            # against a 0.80 gate) before it had templates of its own, and
            # with no message there was nothing to go on. Said once, because
            # the answer cannot change while the same art is on screen.
            # Not until the oversize search has had a turn: on its 1-in-N
            # cadence the first few attempts at a big HUD land here, and
            # "a UI mod Loom has no templates for" is the wrong explanation
            # for a HUD that is merely large and about to be found.
            if (found is not None and not self._warned_weak_anchor
                    and self._large_hud_attempts >= LARGE_HUD_EVERY_NTH):
                self._warned_weak_anchor = True
                print(f"note: the closest HUD match was "
                      f"'{found['profile'].name}' at {found['score']:.2f}, "
                      f"under the {MIN_ANCHOR_SCORE:.2f} Loom needs. If a "
                      "match really is on screen, this usually means a UI mod "
                      "Loom has no templates for.")

            # Before giving the silent False that wait_for_hud will loop on
            # forever: is the HUD present but BIGGER than the search can see?
            # A game rendering below the display's resolution gets upscaled by
            # the compositor - 1080p on a 4K screen puts the HUD at ~2.9x -
            # and the difference between "no HUD yet" and "this HUD can never
            # be found" is a player's whole evening. Checked once, because the
            # answer cannot change without the game's settings changing.
            if not self._warned_oversize:
                oversize = anchor.icon_beyond_range(
                    frame, self._icon_templates[hud.DEFAULT])
                if oversize is not None:
                    self._warned_oversize = True
                    # The limit is READ from the constant, never typed. This
                    # message used to say "limit 2.0x" in words and would have
                    # started lying the moment the extended range landed.
                    print(f"note: the HUD appears ~{oversize:.1f}x the size "
                          f"Loom was measured against, past the "
                          f"{anchor.EXTENDED_SCALES[-1]:.1f}x it can search. "
                          "Lower the game's HUD scale (Options > Interface) "
                          "until Loom finds it. A game rendering below the "
                          "display's resolution is upscaled by the compositor "
                          "and can also land here, so matching the game's "
                          "resolution to the display is worth a try too.")
            return False

        height, width = frame.shape[:2]
        profile = found["profile"]
        self.hud = {
            "scale": found["scale"],
            "score": found["score"],
            "profile": profile,
            "villagers": _clamp(found["villagers"], width, height),
            "clock": _clamp(found["clock_band"], width, height),
            "population": _clamp(found["population"], width, height),
            # Digits get thinner as the HUD shrinks, so the speck test has to
            # shrink with it - and must never overtake the narrowest digit.
            # The skin sets the formula: its font may be a different size
            # relative to the anchor that scales it.
            "min_glyph_width": min_glyph_width(found["scale"], profile),
            # ...and wider as it grows, or the "two characters touching" split
            # starts cutting single large digits in half.
            "max_glyph_width": max_glyph_width(found["scale"], profile),
        }

        # A HUD small enough that the digits are getting thin. Said once, and
        # only ever advisory - Loom carries on and reads it. See
        # SMALL_HUD_SCALE for why small is the dangerous direction.
        #
        # This replaced a note that printed found["scale"] as a PERCENTAGE -
        # "HUD scale looks like ~68% - Loom reads best at 100%" - which was
        # reporting the wrong quantity. found["scale"] is the HUD's size
        # against a 2560x1440 reference, not the in-game slider. A 1920x1080
        # screen with the slider at 100% measures 0.74, so every 1080p player
        # was told they were at 74% and should go to 100%, which they already
        # were. The fixtures prove it: hud_1920x1080_100.png is named for a
        # slider at 100 and measures 0.68. The two numbers agree only at
        # 2560x1440, which is the resolution the reference was cut from.
        #
        # So this says the size it actually measured, and gives advice a
        # player can act on: the direction to move the slider.
        if found["scale"] < SMALL_HUD_SCALE and not self._warned_small_hud:
            self._warned_small_hud = True
            print(f"note: the HUD is measuring {found['scale']:.2f}x the size "
                  "Loom was built against, small enough that digits can be "
                  "misread. Raising the game's HUD scale (Options > Interface) "
                  "will make the readings steadier.")

        # The queue hangs off its own anchor in the same bar, and that anchor
        # is skin art too. Hand the reader the matching template and grid
        # origin, or it locates the strip against the wrong picture.
        if self._queue_reader is not None:
            self._queue_reader.use_profile(profile)

        # The resource icons sit in the same bar and do not move either, so
        # locate their number regions once too. A failure here is not fatal:
        # per-resource counts are a nicety, and the rest of Loom works without
        # them.
        self._resource_regions = resources.locate_regions(
            frame, self._resource_templates[profile], found["scale"], profile)

        return True

    def wait_for_hud(self, poll_interval=0.5):
        """Keep looking for the HUD until a match is actually on screen.

        The window existing just means the game launched; the player may sit
        in menus or a lobby for minutes, so this loop can run for a long
        time and must stay cheap.

        Half a second rather than the two it waited for months. A search
        costs 81 ms at 1920x1080 and 155 ms at 2560x1440, measured over
        capture runs at both - so even the slow case spends under a third of
        one core while waiting, and the player is not left watching nothing
        happen for two seconds after a match starts. The delay was
        indistinguishable from Loom failing to notice the game.
        """
        while not self.find_hud():
            time.sleep(poll_interval)

    # ---- reading -------------------------------------------------------

    def poll(self):
        """Read the screen once and return a Reading.

        A capture failure degrades to an unreadable poll instead of escaping.
        Nothing upstream catches anything: LiveController.tick calls this bare
        from a Qt timer, so one BadWindow when the game exits, or one refusal
        while the window is not being composited, used to take the overlay
        down mid-match. "No reading" is a state Loom already knows how to show.
        """
        started = time.monotonic()
        try:
            return self._poll_once()
        except capture.CaptureError as problem:
            return self._unreadable(problem)
        finally:
            self._last_poll_cost = time.monotonic() - started

    def _unreadable(self, problem):
        """A poll that could not see the screen at all.

        The session tracker still gets told, and told the truth: passing a
        stale value would stop it ever noticing the game went away, which is
        the whole reason it exists. Everything else comes back empty rather
        than held over.
        """
        # Say it once, on the way in and on the way out. At three polls a
        # second an unguarded print would bury every other line in the log.
        if not self._capture_failed:
            self._capture_failed = True
            print(f"capture unavailable: {problem}")

        self._failures_in_a_row += 1
        event = self._session.update(None, None)
        return Reading(None, None, event, False)

    def _advisory_due(self, name):
        """Whether an advisory reader should run this poll.

        On a healthy machine: always, exactly the behaviour Loom has always
        had. When the previous poll ran over POLL_BUDGET - measured, not
        guessed - each advisory reader runs only every Nth poll, so the poll
        spends its time on the sync signals instead of stretching to seconds
        and showing the player stale ones.
        """
        if self._last_poll_cost <= POLL_BUDGET:
            return True
        last = self._advisory_ran.get(name)
        if last is None or self._poll_number - last >= ADVISORY_EVERY_NTH_POLL[name]:
            self._advisory_ran[name] = self._poll_number
            return True
        return False

    def _poll_once(self):
        """Read the screen once, assuming capture works."""
        self._poll_number += 1
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
            self.hud["max_glyph_width"],
        )
        if raw_population[0] is None:
            raw_population = None
        population = self._population_filter.update(raw_population)

        # A stale population is worse than none: the filter holds its last
        # belief through unreadable polls (right for the villager count),
        # but population drives the HOUSED alerts - a "4/5" held for a
        # minute keeps shouting HOUSE SOON long after the real pop moved on.
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
        # next poll, and nothing important depends on them. Under load the
        # cache stands in - second-old advisory numbers beat a stretched poll.
        if self._advisory_due("resources"):
            per_resource = {}
            for name, region in self._resource_regions.items():
                count = resources.read_one(
                    self._read_region(region),
                    self._digit_templates,
                    self.hud["min_glyph_width"],
                )
                if count is not None:
                    per_resource[name] = count
            self._per_resource_cache = per_resource
        else:
            per_resource = self._per_resource_cache

        # The global queue lives in the top-left corner, so only that strip
        # of the window gets captured for it. Skipped entirely when the HUD
        # numbers were unreadable - a menu is up, so the queue is not there.
        queue_slots = None
        if (self._queue_reader is not None
                and (raw_clock is not None or raw_villagers is not None)
                and self._advisory_due("queue")):
            strip_w, strip_h, _ = queue.strip_extent(
                self.hud["scale"], self._queue_reader.profile.slot_one)
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
                and (raw_clock is not None or raw_villagers is not None)
                and self._advisory_due("notifications")):
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
            if self._capture_failed:
                self._capture_failed = False
                print("capture recovered.")
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
