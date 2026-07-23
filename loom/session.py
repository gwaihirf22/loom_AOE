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

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

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

    def update(self, game_time, villagers):
        """Feed in one poll. Returns an event string, or None."""
        if game_time is None:
            return self._handle_unreadable()

        self._unreadable_polls = 0

        if self.state == WAITING:
            event = self._start_tracking(game_time)
        else:
            # Already tracking. A clock that jumps backwards means a new game
            # began without the HUD ever vanishing for long enough to notice.
            event = None
            if self._last_game_time is not None and game_time < self._last_game_time:
                event = GAME_STARTED

        self.state = IN_GAME
        self.game_time = game_time
        self.villagers = villagers
        self._last_game_time = game_time
        return event

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
