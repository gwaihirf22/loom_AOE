"""
Loom — game session tracking.

The reader gives me two numbers. This turns them into a sense of *what is
happening*: whether a game is running, whether a new one just started, and
whether I have lost sight of the one I was watching.

That matters because the build order needs to know when to start over. A new
game means step 1 again; alt-tabbing back into the same game does not.

Everything here works on the *filtered* values from filters.py, never the raw
readings. The filters already refuse to believe a surprising value until a
second reading confirms it, so by the time a change reaches this class it has
been checked. Putting the noise rejection at the bottom means everything above
it can just trust its inputs.
"""

import time

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# The two states a session can be in.
WAITING = "waiting"    # no HUD: a menu, a loading screen, or the game is shut
IN_GAME = "in_game"    # HUD readable, a match is being tracked

# The events this class reports. Milestone 3 will act on these.
GAME_STARTED = "game_started"     # a new match: reset the build order to step 1
GAME_RESUMED = "game_resumed"     # same match as before: pick up where it is

# Deliberately NOT called "game ended". All I actually know is that the HUD
# stopped being readable, and that happens for four different reasons: an ESC
# pause, an alt-tab, quitting to the menu, or the game closing. Naming it after
# what I can observe keeps me from implying knowledge I do not have.
TRACKING_LOST = "tracking_lost"

# How far the clock must go BACKWARDS before it means a new match rather
# than a reading that wobbled.
#
# Imported rather than written again, and that is the point: loom/events.py
# measured it across 252 recorded games - real seams jump back hundreds of
# seconds (529 to 7) while jitter is one to three - and two modules asking
# the same question with two numbers is how they drift apart. One of them
# had no number at all, which is the same fault at its limit.
from .events import SEAM_TOLERANCE_SECONDS  # noqa: E402

# How long a lower clock must HOLD before it counts as a new match.
#
# Wall seconds, not polls. A count of looks is not a duration: the poll
# rate drops under load, and a threshold measured in looks quietly stops
# meaning what it did. Measured on a 1080p session log (issue #14), 42 of
# 44 misread excursions lasted two wall seconds or less, so three has room
# on both sides - a real match holds forever and is declared three seconds
# late, which nobody can see.
NEW_GAME_HOLD_SECONDS = 3

# Above this many villagers, a backward clock with the count UNMOVED is a
# misread rather than a match - nobody starts a game with a dozen
# villagers already made. Below it the two are genuinely indistinguishable
# (a restart at 3 villagers looks like a wobble at 3 villagers), and the
# hold above is what separates them there.
#
# Generous on purpose: the largest opening count any civilisation gets is
# well under this, so it refuses nothing real.
NEW_GAME_VILLAGERS = 6


class GameSession:
    """Tracks whether a game is running, and reports when that changes.

    Feed it the filtered clock and villager count once per poll. It returns an
    event string when something noteworthy happens, or None when nothing has.
    """

    def __init__(self, polls_before_lost=10, new_game_clock_limit=60):
        # How many unreadable polls before I report that I have lost sight of
        # the HUD. At ~3 polls a second, 10 polls is about three seconds -
        # long enough to ignore a brief flicker.
        self.polls_before_lost = polls_before_lost

        # If Loom starts up and the clock is already below this, I treat it as
        # a fresh game rather than one joined in progress. Only used when I
        # have no earlier reading to compare against.
        self.new_game_clock_limit = new_game_clock_limit

        self.state = WAITING
        self.game_time = None
        self.villagers = None

        # The last clock value I saw, deliberately kept even while WAITING.
        # This is what lets me tell a new game from an alt-tab: I compare
        # across the gap rather than guessing from the new value alone.
        self._last_game_time = None
        self._unreadable_polls = 0
        # A candidate new match: the clock value we dropped FROM, and when
        # that drop was first seen. Both None when nothing is pending.
        self._new_game_since = None
        self._dropped_from = None

    def update(self, game_time, villagers, now=None):
        """Feed in one poll. Returns an event string, or None.

        `now` is wall-clock seconds, for the new-game hold below. It is an
        argument rather than a call to time.monotonic() inside so a test
        can drive a whole session without sleeping through it.
        """
        if game_time is None:
            return self._handle_unreadable()
        now = time.monotonic() if now is None else now

        self._unreadable_polls = 0

        if self.state == WAITING:
            event = self._start_tracking(game_time)
        else:
            # Already tracking. A clock that jumps backwards means a new game
            # began without the HUD ever vanishing for long enough to notice.
            #
            # FAR ENOUGH backwards. Any step at all used to count, and a
            # single wobbly reading therefore started a new game and wrote a
            # permanent stats file - which is how a ten-minute match came
            # back as seventy-five files (issue #12). The reader is good
            # enough now that it rarely wobbles; that makes this guard cheap
            # rather than unnecessary.
            event = self._maybe_new_game(game_time, villagers, now)

        self.state = IN_GAME
        self.game_time = game_time
        self.villagers = villagers
        self._last_game_time = game_time
        return event

    def _maybe_new_game(self, game_time, villagers, now):
        """Is this backward clock a new match, or a misread? Corroborate.

        The clock going back USED to be the whole test, and it is not
        evidence enough. Measured on a 1080p player's own session log
        (issue #14): the digits are drawn about 6x12 there and lose their
        thin strokes, so 8 read as 0, 3 as 1, 7 as 6 and - once, costing
        three minutes - 4 as 1. Forty-four of his 784 readings went
        backwards and every one restarted his build order.

        A failed reading is evidence about the READER as much as about
        the world. So a new match now has to agree with itself twice.

        FIRST, IT HAS TO PERSIST. A misread bounces back within a poll;
        a real match keeps counting up from its new low. Measured, 42 of
        those 44 excursions lasted two wall seconds or less.

        Held in WALL seconds rather than polls, deliberately. A count of
        looks is not a duration - the poll rate drops under load and a
        threshold measured in looks quietly stops meaning what it did.

        SECOND, THE VILLAGERS HAVE TO AGREE. A new match resets
        everything, not just the clock. 43 of those 44 had the villager
        count sitting still across the drop, and the two that outlasted
        any hold - the clock stuck at 01:00 for 96 seconds while the game
        ran to 05:40 - had ELEVEN villagers on the board, which no fresh
        match has. One witness going backwards while the other stands
        still is a reading fault, not a match.

        That second test is the one that catches the stuck clock, where
        persistence cannot: it held for 96 seconds and a real game would
        too.
        """
        if self._last_game_time is None:
            return None

        # Held against the value the clock dropped FROM, not against the
        # previous reading. A new match ticks UP from its own start, so
        # comparing each reading with the one before it stops seeing a
        # drop after the first poll and would clear the candidate before
        # the hold ever expired - the match would be one poll of evidence
        # forever. What "still lower" means is lower than the game we
        # were watching.
        if self._dropped_from is not None:
            if game_time >= self._dropped_from - SEAM_TOLERANCE_SECONDS:
                self._dropped_from = None    # climbed back: a misread
                self._new_game_since = None
                return None
        elif self._last_game_time - game_time > SEAM_TOLERANCE_SECONDS:
            self._dropped_from = self._last_game_time
            self._new_game_since = now
        else:
            return None

        # A match that just began cannot already have a dozen villagers,
        # so a backward clock beside a large count is the reader.
        #
        # Asked as "is this count possible for a fresh match" rather than
        # "did the count move", which was the first version and is the
        # weaker question. His clock stuck at 01:00 for 96 seconds with
        # eleven villagers; the moment the count merely CHANGED - eleven
        # to nine, two villagers lost to a raid - "it moved" was satisfied
        # and a new game was declared in the middle of his match. Nine is
        # no more a fresh start than eleven is.
        #
        # Checked every poll, so a stuck clock never graduates however
        # long it sticks. And it cannot wedge a real restart: the
        # candidate stays pending, so the match is declared the moment the
        # villager count does fall to something a match can start with.
        if villagers is not None and villagers > NEW_GAME_VILLAGERS:
            return None

        if now - self._new_game_since < NEW_GAME_HOLD_SECONDS:
            return None
        self._dropped_from = None
        self._new_game_since = None
        return GAME_STARTED

    def _handle_unreadable(self):
        """No HUD this poll. Decide whether I have really lost sight of it."""
        if self.state != IN_GAME:
            return None

        self._unreadable_polls += 1
        if self._unreadable_polls < self.polls_before_lost:
            # Probably just a menu or a fade. Keep believing the game is on.
            return None

        self.state = WAITING
        self.game_time = None
        self.villagers = None
        # Note I keep _last_game_time: it is exactly what I need to recognize
        # the next game as new rather than resumed.
        return TRACKING_LOST

    def _start_tracking(self, game_time):
        """The HUD just appeared. Is this a new game, or one already running?"""
        # The reliable test: did the clock go backwards? A match that was at
        # 10:00 and is now at 0:30 must be a different match. This works no
        # matter how long loading took, which a "is the clock near zero?" test
        # does not.
        if self._last_game_time is not None:
            if game_time < self._last_game_time:
                return GAME_STARTED
            return GAME_RESUMED

        # Nothing to compare against, so this is the first game since Loom
        # started. Near the beginning means treat it as a fresh start.
        if game_time <= self.new_game_clock_limit:
            return GAME_STARTED
        return GAME_RESUMED

    def is_in_game(self):
        """True when a match is being tracked right now."""
        return self.state == IN_GAME
