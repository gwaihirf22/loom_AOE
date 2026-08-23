"""
Loom — how far behind the build order the player is.

The number this produces is the one thing on the overlay a player watches out
of the corner of their eye, so it has to behave sensibly. In particular it
must not creep upward on its own.

The rule, in one sentence: **you are as far behind as you were when you got
your current villager, and no further - unless something you should have done
by now has not happened.**

So a player who fell thirty seconds behind and is now producing villagers at
the right rate reads a steady "30s", not a number that climbs and resets. If
their Town Center goes idle, or they are late clicking an age up, it starts
climbing again, because now something really is slipping.

Why this needs to remember things: an earlier version recomputed the delta
from scratch every poll. Villagers arrive in discrete jumps but time is
continuous, so the answer sawtoothed between "on pace" and "half a villager
behind" forever. Measuring the arrival *events* instead of sampling the gap
removes that entirely.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from . import build_order


class PaceTracker:
    """Follows one game and reports how far behind the build order it is."""

    def __init__(self, build):
        self.build = build
        self.reset()

    def reset(self):
        """Start again. Called when a new game begins."""
        self._villagers = None
        self._delta_on_arrival = None
        self.complete = False

    def update(self, villager_count, game_time, age=None,
               clicked=None):
        """Feed in one reading. Returns seconds behind, or None if unknown.

        Negative means ahead. None means there is nothing to compare against
        yet, which is honest for the opening seconds of a game.

        `age` is the crest's answer, passed through to the step lookup as a
        ceiling. Without it the build could "complete" while the player was
        still an age short of the last step - measured live: an accidental
        23rd villager exhausted the villager constraint and the panel
        showed the report card at 13:30 with Castle Age still ninety
        seconds of research away.
        """
        if villager_count is None or game_time is None:
            return None

        # Once the last step is done there is nothing left to be late
        # against, so the meter retires for the rest of the game. Latched
        # rather than recomputed: villager deaths could pull the count back
        # under the final step's target, and a "finished" build coming back
        # from the dead to nag about pace would help nobody.
        if self.complete:
            return None
        if self.build.active_step(villager_count, game_time, age,
                                  clicked) is None:
            self.complete = True
            return None

        # A new villager: this is the only moment the "how late am I" question
        # has a crisp answer, so record it and hold on to it.
        if villager_count != self._villagers:
            self._villagers = villager_count
            if build_order.extra_villagers(self.build, villager_count,
                                           game_time, age, clicked) > 0:
                # A villager trained INTO A HOLD (the build repeats a count
                # across an age-up, meaning stop training) is not a
                # checkpoint - the build never requested it. Scoring it
                # against an interpolated target flipped the meter to AHEAD
                # while the click slid 25-40 seconds late. Keep the previous
                # arrival delta; the overdue term below carries the truth.
                # Villagers arriving merely AHEAD of a normal checkpoint's
                # time still score - ahead is real there.
                pass
            else:
                expected = self.build.target_time(villager_count)
                self._delta_on_arrival = (None if expected is None
                                          else game_time - expected)

        overdue = self._overdue(villager_count, game_time, age, clicked)

        # Report whichever is worse: how late the last villager was, or how
        # overdue the current instruction is. Taking the maximum means the
        # number holds steady while things are merely late, and climbs only
        # while something is actively slipping.
        if self._delta_on_arrival is None:
            # Nothing to compare against yet. Only speak up if the very first
            # instruction is already overdue.
            return overdue if overdue is not None and overdue > 0 else None

        if overdue is None:
            return self._delta_on_arrival
        return max(self._delta_on_arrival, overdue)

    def player_time(self, game_time):
        """The game clock on the PLAYER'S schedule, for the step cursor.

        The fundamental decision (2026-08-22): the cursor follows the
        player, not the build's ideal timings - Loom's audience is
        someone learning the game, and a cursor that marches on the
        author's clock abandons exactly the player it exists for. The
        ideal times still judge: pace, the recorder and the report all
        stay on true game time.

        So this returns game_time shifted by how late the player's
        villagers are actually arriving, which is what makes a step
        become "current" when THEY reach it rather than when a perfect
        player would have. Two traps decide the shape:

        * Only `_delta_on_arrival` may feed the shift - it is measured
          from villager arrival events and holds steady while the player
          is merely late. The overdue term grows a second per second
          while production stalls, so shifting by the full delta would
          hold the clock still and the cursor would never advance again:
          a read filter that gets stuck, the oldest rule here.
        * The shift only ever DELAYS. A player running ahead is carried
          forward by the villager count on its own; letting a negative
          delta advance the clock too would credit being ahead twice.

        None (no arrival measured yet, or no clock) means no shift.
        """
        # Carry the build's clock forward by how late the player's villagers are.
        if self._delta_on_arrival is None:
            return game_time
        return game_time - max(0, self._delta_on_arrival)

    def _overdue(self, villager_count, game_time, age=None,
                 clicked=None):
        """How long past due the current instruction is. Negative if not due.

        This is what makes an idle Town Center show up: the next step's time
        passes, no villager appears to reset the arrival delta, and this term
        grows. It also catches being late to click an age up, since that is a
        step with a time like any other.
        """
        step = self.build.active_step(villager_count, game_time, age,
                                      clicked)
        if step is None or step.time is None:
            return None
        return game_time - step.time
