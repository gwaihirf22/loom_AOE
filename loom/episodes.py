"""
Loom — one item's production, followed across polls and decided once.

queue.py answers "what does this cell look like right now". production.py
turns that into a sense of whether anything is working. This is the third
question, and the one nothing answered: WHAT DID THAT BUILDING ACTUALLY
MAKE.

WHY THIS EXISTS. The queue reader is right about 95% of the readings it
makes, and the Post-game page still showed a wall of things that never
happened. Both were true. Measured on one real game: 26 wrong readings out
of 2,293 - 1.13% - but NINE DISTINCT WRONG SUBJECTS, because gamestats
recorded the first sighting of anything the queue named, once, with no
corroboration. A phantom that flickered for a single poll was 1/2293 to the
gate and a permanent row on the page. Across the paired corpus: 305 phantom
subjects against 664 real ones.

Every other reader in Loom already refuses to believe one glance.
filters.py believes a count that repeats; notifications.py refuses an
uncorroborated line; production.py debounces every state change. The queue
was the one reader that took a single frame's word, and this is where it
stops.

WHAT AN EPISODE IS. A building shows at most one in-progress item in the
global queue: completely untinted the moment it is placed, then washing
green left to right, while anything queued behind it waits AMBER and
anything the population cannot fit turns RED. So an episode is one
producing cell followed from the poll it appears to the poll it finishes,
and AMBER AND RED ARE IGNORED ENTIRELY - a waiting item has produced
nothing and has no business becoming a fact.

HOW IT IS FOLLOWED, and every constant below is measured rather than
chosen. Over 39 paired capture runs and 30,672 transitions:

    continued in place              68.4%
    no progress to judge            12.0%   (untinted: the wash has not started)
    gone from the producing set     11.4%   (finished)
    reset in place                   3.5%   (finished, next item already begun)
    shifted -1                       3.0%   (the strip closed up)
    shifted +1                       1.3%
    implausible - refused            0.5%

POSITION IS THE STRONG SIGNAL AND PROGRESS CORROBORATES IT, and the first
draft had that backwards. Matching progress-first - continue an item onto
the nearest cell whose wash is at least as far along - is ambiguous in
23.5% of continuations, because several buildings produce at once (one item
in 7,494 polls, but two to four in ~4,800, and up to twenty-four) and their
washes sit at similar fractions constantly. A two-run sample said the
opposite, with 100% position stability and no ambiguity at all; only the
whole corpus showed it.

THE VOTE. An episode is decided ONCE, when it closes, from every poll that
saw it - not by its first glance and not by its last. `15 mangudai, 5
crossbowman, 1 caravel` decides mangudai, and REMEMBERS the other six,
because the tally is the sentence that explains the reader to its author.

An episode too short to vote on reports NO IDENTITY rather than its best
guess. That costs about 3% of real subjects - things that genuinely
produced and were only ever glimpsed - and removes about 60% of the
phantoms. An admitted gap is the cheaper error, and it is still an error.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import collections

# The front of the strip. An untinted cell here is an item just placed,
# before its wash starts; away from the front, untinted is the waiting
# portrait of a group whose turn has not come. production.py had this right
# and classify_tint's docstring did not - settled by reading one run's slot
# 0 by hand, where every untinted poll sits at the top of a sawtooth,
# immediately before the reset.
FRONT = 0

# A fall of at least this much is the wash starting over rather than the
# measurement wobbling. Measured, not chosen: at a held position 93% of
# changes land in +0.0..+0.2 and the falls spread from -0.05 down to -0.9,
# so the two populations separate here and nowhere near the forward mass.
RESET_FALL = 0.05

# The largest forward step one poll can honestly carry. The 95th percentile
# of a real step is 0.256, so this leaves room for a slow poll without
# admitting a jump that must be a different item.
MAX_STEP = 0.35

# How far along the strip an item may move between polls and still be the
# same item. The strip closes up when something ahead finishes, which is
# the -1 case at 3.0%; beyond one slot the evidence runs out.
MAX_SHIFT = 1

# How many polls an episode needs before its vote is believed. Swept across
# the paired corpus, in distinct SUBJECTS - what a person reads off the
# Post-game page - against the 715 real and 329 phantom that believing every
# glance produces:
#
#     polls   real kept   phantoms   marginal trade
#         1         647         89   (the vote alone)
#         2         640         68   7 real for 21 phantoms   3:1
#         3         626         52   14 real for 16 phantoms  ~1:1
#         4         603         38   23 real for 14 phantoms  worse than 1:1
#
# TWO IS THE KNEE. Past it the trade is break-even and then negative: more
# truth lost than noise removed.
#
# The first draft used three, carried over from a different measurement -
# how long a subject was sighted ANYWHERE, which sounded like the same
# quantity and is not. "Seen three times in the game" and "seen three times
# inside one tracked episode" differ, and the difference showed up as a real
# loss of 12% where 3% had been predicted.
#
# NOTE WHERE THE WIN ACTUALLY COMES FROM: at a threshold of one - no minimum
# at all - 240 of the 329 phantoms are already gone, because a phantom that
# flickers inside an episode is outvoted by the identity around it. The
# minimum is the smaller half of this.
#
# Counted in POLLS and not in seconds deliberately. Unlike a cooldown, this
# asks "how much evidence do I have", and evidence is counted in looks. A
# changing poll rate makes it stricter or looser about how LONG an episode
# must last, never about how much it saw.
MIN_POLLS_TO_VOTE = 2

# And how long it must have been on screen, in GAME seconds.
#
# THIS IS THE HALF THAT TRANSFERS, and leaving it out was very nearly a
# shipped bug. Everything above was calibrated on capture runs whose polls
# are 4.0 game seconds apart. The live overlay polls every 300ms - roughly
# TEN TIMES more often - so "two polls" means four seconds of persistence
# where it was measured and about six tenths of a second in a real game. A
# phantom that flickers twice would sail through a threshold measured to
# stop it.
#
# That is CLAUDE.md's own warning about thresholds counted in looks, and
# the tell it names - the answer changes when you change how often you look
# - was quoted in this file's comments while the mistake was being made.
#
# So both must hold, and whichever is scarcer binds: polls are evidence,
# seconds are persistence, and a rate change moves only one of them. Four
# seconds is what two corpus polls spanned, so this is the same strictness
# expressed in a unit that survives the move to 3.3Hz.
MIN_SECONDS_TO_VOTE = 4

# What the feed said about a closed episode.
CORROBORATED = "corroborated"      # the feed named the same thing
UNCORROBORATED = "uncorroborated"  # the feed named nothing here
CONTRADICTED = "contradicted"      # the feed named something else
REFUSED = "refused"                # too little evidence to name at all


def producing(slots):
    """The cells actually making something this poll.

    Amber is waiting and red is blocked. Neither has produced anything, and
    between them they are most of what the strip holds - which is why this
    is cheaper than reasoning about every cell, as well as more honest.
    """
    found = []
    for slot in slots or []:
        if slot.tint == "green":
            found.append(slot)
        elif slot.tint is None and slot.index == FRONT:
            found.append(slot)
    return found


class Episode:
    """One item's production, and everything seen while it produced."""

    __slots__ = ("started", "ended", "polls", "tally", "index", "progress",
                 "state", "count")

    def __init__(self, when, slot):
        self.started = when
        self.ended = when
        self.polls = 0
        # identity -> summed identity_score. Weighted rather than counted:
        # a poll that was sure carries more than one that barely decided.
        self.tally = collections.Counter()
        self.index = slot.index
        self.progress = slot.progress
        self.count = slot.count
        self.state = None
        self.saw(when, slot)

    def saw(self, when, slot):
        """Fold one more poll of this item into the episode."""
        # A poll whose clock did not read still counts as EVIDENCE - it saw
        # the item - but it cannot supply a time. So the times fill in from
        # the first and last polls that could actually say when they were,
        # and an episode seen only during a clock gap honestly has none.
        if when is not None:
            if self.started is None:
                self.started = when
            self.ended = when
        self.polls += 1
        self.index = slot.index
        if slot.progress is not None:
            self.progress = slot.progress
        if slot.count is not None:
            self.count = slot.count
        if slot.identity is not None:
            self.tally[slot.identity] += max(0.0, slot.identity_score or 0.0)

    @property
    def identity(self):
        """What this episode produced, decided by vote. None if refused.

        The never-guess rule, at the one place it can be applied cheaply:
        an episode nobody watched for long enough is not given its best
        guess, it is given no answer.
        """
        if self.polls < MIN_POLLS_TO_VOTE or not self.tally:
            return None
        if self.started is not None and self.ended is not None:
            if self.ended - self.started < MIN_SECONDS_TO_VOTE:
                return None
        # A clock that never read leaves the seconds unknowable, and an
        # unknowable number must not be treated as a failing one: the polls
        # still happened and are still evidence. Falling back to them is the
        # honest answer, and it is the same shape as everything else here -
        # "I could not tell" is not "it was not there".
        return self.tally.most_common(1)[0][0]

    @property
    def runner_up(self):
        """The identity that came second, or None. Kept because it is the
        interesting half of the tally when a vote is close."""
        ranked = self.tally.most_common(2)
        return ranked[1][0] if len(ranked) > 1 else None

    def __repr__(self):
        name = self.identity or "?"
        return (f"<Episode {name} {self.started}-{self.ended} "
                f"{self.polls} polls {dict(self.tally)}>")


def _continues(episode, slot):
    """Is this cell the same item the episode was following?

    Position first. Progress corroborates and never leads: with several
    buildings producing at once their washes sit at similar fractions, so a
    progress-first match guesses in a quarter of continuations.
    """
    if abs(slot.index - episode.index) > MAX_SHIFT:
        return False
    if episode.progress is None or slot.progress is None:
        # An untinted cell has no wash to compare. Position alone has to
        # answer, which is why this is only allowed at the front, where
        # untinted means just-placed rather than waiting.
        return True
    change = slot.progress - episode.progress
    return -RESET_FALL < change <= MAX_STEP


class EpisodeTracker:
    """Follows every producing cell and closes an episode when it ends.

    Pure logic over a stream of per-poll readings, like production.py and
    session.py, so it is testable without a game.
    """

    def __init__(self):
        self.open = []          # Episodes still producing
        self.closed = []        # every Episode that has ended

    def update(self, when, slots):
        """One poll. Returns the episodes that ENDED on this poll."""
        current = producing(slots)
        still_open = []
        finished = []
        claimed = set()

        for episode in self.open:
            # Nearest first, so an episode that stayed put is not stolen by
            # a neighbour it could also have moved onto.
            candidates = sorted(
                (slot for slot in current if id(slot) not in claimed),
                key=lambda slot: abs(slot.index - episode.index))
            for slot in candidates:
                if _continues(episode, slot):
                    episode.saw(when, slot)
                    claimed.add(id(slot))
                    still_open.append(episode)
                    break
            else:
                finished.append(episode)

        for slot in current:
            if id(slot) not in claimed:
                still_open.append(Episode(when, slot))

        self.open = still_open
        self.closed.extend(finished)
        return finished

    def flush(self, when=None):
        """Close everything still open. For the end of a game, where an
        item in progress is evidence too - it was seen, whatever became of
        it after the recording stopped."""
        finished = self.open
        self.open = []
        self.closed.extend(finished)
        return finished
