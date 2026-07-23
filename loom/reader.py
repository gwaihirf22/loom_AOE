"""
Loom — reading the game, all in one place.

Everything needed to turn "the game is running" into "the player has N
villagers and the clock says T": find the window, locate the HUD, read the
digits, filter out misreads, and notice when a game starts or ends.

This exists so that more than one front end can use it. The terminal coach and
(later) the overlay both need exactly this and should not each have their own
copy of it.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

from . import anchor, capture, digits, filters, session

# Below this template-match score I assume the HUD is not visible - a menu, a
# loading screen, or the fade at the start of a match.
MIN_ANCHOR_SCORE = 0.8

# How many unreadable polls in a row before I go looking for the HUD again.
FAILURES_BEFORE_REANCHOR = 10


class Reading:
    """What one poll of the screen produced."""

    def __init__(self, villagers, game_time, event, hud_visible,
                 raw_villagers=None, raw_clock=None):
        # What Loom believes, after filtering.
        self.villagers = villagers
        self.game_time = game_time

        # A session event this poll, if any ("game_started", etc).
        self.event = event

        # Whether the digits could be read at all this poll.
        self.hud_visible = hud_visible

        # The unfiltered readings, useful for debugging.
        self.raw_villagers = raw_villagers
        self.raw_clock = raw_clock

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

        self._villager_filter = filters.StableCount(required_repeats=2)
        self._clock_filter = filters.StableClock(max_step=30, required_repeats=2)
        self._session = session.GameSession()

        self._failures_in_a_row = 0

    # ---- setting up ----------------------------------------------------

    def connect(self):
        """Find the game window. Returns False if the game is not running."""
        self._display = capture.open_display()
        self.window = capture.find_game_window(self._display)
        if self.window is None:
            return False

        self._icon_template = anchor.load_template()
        self._digit_templates = digits.load_digit_templates()
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
            # Digits get thinner as the HUD shrinks, so the "is this a colon
            # or a digit?" width test has to shrink with it.
            "min_glyph_width": max(4, int(6 * found["scale"])),
        }
        return True

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

        # Tell the session tracker the truth about this poll. Passing on a
        # stale value would stop it ever noticing that the game went away.
        event = self._session.update(
            game_time if raw_clock is not None else None,
            villagers,
        )

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
                       raw_villagers, raw_clock)

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
