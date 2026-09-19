"""
Loom — technologies on a timeline, coloured by who saw them.

`events.reconcile` folds every witness into one Sighting per subject. This
turns the technology ones into marks a chart can draw: when it landed, and
WHICH WITNESSES agreed it happened.

WHY THE THIRD COLOUR EARNS ITS PLACE. Loom now has two independent
witnesses for a technology. Its own readers watch the screen - the
production queue shows the research in progress, the notification feed
announces it complete - and the recorded game holds what the player
actually ordered. A subject both of them named is a qualitatively stronger
fact than one either named alone, and until now nothing said so anywhere a
person looks.

That is the same distinction the step checklist draws between OBSERVED and
ASSUMED, and the same one the queue's episode vote was built to protect.
This is that rule arriving where it can be seen.

THE STATE THAT IS NOT A STATE, and the reason this module exists rather
than three lines inside the chart. The record's vocabulary is FINITE: its
id tables can only speak about the subjects in `replay.QUEUE_KNOWN`. A
technology outside that set is not a technology the record disagrees
about - it is one the record has no word for. Drawing it as a disagreement
would be inventing a conflict out of a gap in a lookup table, which is the
same fault as reading a failed match as proof of absence.

So `BOTH` means both witnesses named it. `LOOM_ONLY` means Loom saw it and
the record did not, WHETHER OR NOT the record could have. `RECORD_ONLY`
means the game was told to and Loom never saw it happen - the one state
that is actually about a reader missing something.

No Qt here on purpose, the way production.py and episodes.py hold their
logic: a chart is hard to test and this is not.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from collections import namedtuple

from . import events
from .queue import TECHNOLOGY

# Which witnesses named a subject. Three states, and the difference between
# the last two is the whole point of the chart.
BOTH = "both"                # Loom saw it AND the record names it
LOOM_ONLY = "loom"           # Loom saw it; the record does not name it
RECORD_ONLY = "record"       # ordered, and Loom never saw it happen

# One technology, ready to draw. `when` is game seconds; `speakable` says
# whether the RECORD could have named this subject at all, which is what
# keeps "the record has no word for it" from reading as "the record
# disagrees".
Mark = namedtuple("Mark", "subject when state speakable")


def _seen_by_loom(sighting):
    """Did anything watching the SCREEN see this?

    The queue and the feed are two readers of one screen, so either one
    counts. The record is not a third pair of eyes on the same thing - it
    is read afterwards from the match's own command log - which is why it
    is asked about separately and never averaged in.
    """
    return bool(sighting.witnesses & {events.QUEUE, events.FEED})


def when_of(sighting):
    """When to put the mark, or None if neither witness can say.

    Loom's observed time wins where there is one, and the record's ordered
    time stands in where there is not. The same rule the build-order
    timeline already applies, for the same reason: the observed moment is
    a fact about the game, and the ordered moment is a statement of intent
    that may never have completed.
    """
    if sighting.first is not None:
        return sighting.first
    if sighting.ordered:
        return min(sighting.ordered)
    return None


def state_of(sighting):
    """Which of the three states this sighting is in."""
    loom = _seen_by_loom(sighting)
    record = events.RECORD in sighting.witnesses
    if loom and record:
        return BOTH
    return LOOM_ONLY if loom else RECORD_ONLY


def marks(sightings, speakable=None):
    """Every technology worth drawing, in time order.

    `speakable` is the set of subjects the record's id tables can name -
    `replay.QUEUE_KNOWN` in practice, injected rather than imported so
    this module stays free of the record reader and testable without one.
    Passing None means "no opinion", and every mark then reports
    speakable=True; a caller that cannot say must not have this module
    guessing on its behalf.

    A subject with no time from either witness is DROPPED, not drawn at
    zero. A mark at 0:00 is a claim that something happened at the start
    of the game, and nothing here knows that.
    """
    found = []
    for sighting in sightings:
        if sighting.kind != TECHNOLOGY:
            continue
        when = when_of(sighting)
        if when is None:
            continue
        found.append(Mark(sighting.subject, when, state_of(sighting),
                          True if speakable is None
                          else sighting.subject in speakable))
    return sorted(found, key=lambda mark: (mark.when, mark.subject))


def tally(marks_list):
    """{state: how many}, for a key that can say what it is showing.

    Every state is present even at zero, so a legend built from this shows
    the same three rows on every game. A row that vanishes when its count
    is zero makes an absence look like a category that does not exist.
    """
    counted = {BOTH: 0, LOOM_ONLY: 0, RECORD_ONLY: 0}
    for mark in marks_list:
        counted[mark.state] += 1
    return counted
