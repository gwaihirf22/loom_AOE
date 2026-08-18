"""
Loom — whether the overlay is following the game, and where it is looking.

Everything else about a build order is DERIVED. `build_order.current_index`
recomputes the step from the villager count and the clock every time it is
asked, and holds nothing; that is why the module can claim to be pure logic.
A player pressing "next step" breaks that, because it is a fact about the
player rather than about the game, and it has to be remembered somewhere.

This is that somewhere, and it is deliberately small: state plus arithmetic,
no Qt, no OS, and the clock passed in, so the whole thing tests headless like
pace.py and apm.py do.

THE SHAPE OF THE FEATURE, which is the reason for the ten seconds. Loom's
whole claim is that it reads the game and advances itself - the README says
"Nobody has to press anything" and positions it against the overlays you drive
by hand. Hotkeys are a CORRECTION for when the reading has drifted, not a
mode of operation, and the automatic resume is what keeps that true: nudge the
step, get ten seconds to read it, and Loom takes over again without being
asked. The toggle exists for the player who genuinely wants to drive, and it
says so on the panel the whole time it is on, because an overlay that has
quietly stopped following the game while looking exactly the same is the one
thing this project must never ship.

Indices are in `current_index()` semantics throughout - the last COMPLETED
step, -1 before the first - so the +1 that turns it into the active step
works unchanged in overlay.py and in browser.live_focus.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import math

# What the panel is doing, as three names rather than two booleans.
FOLLOWING = "following"     # the game is driving; the normal case
HOLDING = "holding"         # a hotkey moved the step; auto resumes shortly
MANUAL = "manual"           # following is switched off until told otherwise


class FollowState:
    """Where the overlay is looking, and whether the game gets to move it.

    hold_seconds is how long a step hotkey suspends automatic following.
    Wall-clock seconds: the design rule that game time must be read from the
    screen and never counted is about READINGS, and this is a interface
    timeout - a player who asks for ten seconds means ten real ones, whatever
    the game speed, and it must expire while the game is paused too.
    """

    def __init__(self, hold_seconds=10, step_count=None):
        self.hold_seconds = hold_seconds
        self.step_count = step_count
        self.auto = True
        self.cursor = None          # in current_index() semantics, or None
        self.hold_until = None

    # ---- what the player does ------------------------------------------

    def next_step(self, auto_index, now):
        """Move one step forward and hold automatic following off briefly."""
        return self._move(1, auto_index, now)

    def previous_step(self, auto_index, now):
        """Move one step back and hold automatic following off briefly."""
        return self._move(-1, auto_index, now)

    def toggle(self):
        """Switch automatic following on or off, indefinitely.

        Turning it back on drops the cursor and the hold, so the panel snaps
        to the game immediately rather than sitting on a stale step until
        something else happens to change.
        """
        self.auto = not self.auto
        if self.auto:
            self.cursor = None
            self.hold_until = None
        return self.auto

    def reset(self):
        """Forget everything. Called when a new match starts.

        A cursor left pointing at step 20 of the last game would be exactly
        the silent desynchronisation the rest of Loom is built to avoid, so a
        new game gets a clean slate - including switching following back on,
        because the alternative is a player who toggled it off once quietly
        getting no help for the rest of the session.
        """
        self.auto = True
        self.cursor = None
        self.hold_until = None

    # ---- what the overlay asks -----------------------------------------

    def effective_index(self, auto_index, now):
        """The step index to display: the player's, or the game's.

        Expires the hold as a side effect, which is why it takes `now`. The
        overlay calls this once per poll, so the hold is checked as often as
        anything else changes.
        """
        if self._expired(now):
            self.cursor = None
            self.hold_until = None
        if self.cursor is None:
            return auto_index
        return self.cursor

    def mode(self, now):
        """FOLLOWING, HOLDING or MANUAL, for the panel to say out loud."""
        if not self.auto:
            return MANUAL
        if self.cursor is not None and not self._expired(now):
            return HOLDING
        return FOLLOWING

    def seconds_left(self, now):
        """How much of the hold remains, rounded up, or None when not holding."""
        if self.hold_until is None or self._expired(now):
            return None
        return max(1, int(math.ceil(self.hold_until - now)))

    # ---- internals -----------------------------------------------------

    def _move(self, direction, auto_index, now):
        """Step from wherever the panel is now, not from wherever the game is.

        Starting from the CURSOR when there is one is what makes two quick
        presses move two steps. Starting from auto_index every time would
        make the second press undo the first as soon as the reading had not
        changed, which is precisely when a player is pressing it.
        """
        start = auto_index if self.cursor is None else self.cursor
        self.cursor = self._clamp(start + direction)
        # An explicit toggle wins: while following is switched off there is
        # nothing to hold off, and starting a countdown would imply automatic
        # following is about to come back when it is not.
        if self.auto:
            self.hold_until = now + self.hold_seconds
        return self.cursor

    def _clamp(self, index):
        """Keep the cursor inside the build.

        The floor is -1, not 0: that is "before the first step" in
        current_index() semantics, and it is a real position - it is what the
        overlay shows at the start of a match.
        """
        if index < -1:
            return -1
        if self.step_count is not None and index > self.step_count - 1:
            return self.step_count - 1
        return index

    def _expired(self, now):
        return self.hold_until is not None and now >= self.hold_until
