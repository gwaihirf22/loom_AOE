"""
Loom — the one place game events pass through on the way to consumers.

The readers each see part of what happens: the QUEUE reader sees what is
being produced, the NOTIFICATION reader sees what the game announced. They
overlap - both witness an archer, a bow saw, a Feudal Age - so anything
that simply concatenates them counts the same event twice. That is the
mistake this module exists to make impossible to repeat, and it is the
mistake Loom has already paid for once: adding the Town Centre feed line
to the queue's high-water count invented a third Town Centre in a live
game.

**What this is, and what it deliberately is not.** It is not a pub/sub
bus. A bus routes; this DECIDES. Handing a subscriber a bare event would
strip the thing Loom cares most about - which witness saw it, and whether
it was read or inferred - at the exact seam where that matters most. So
every event here carries its witness, and reconciliation happens in one
tested place rather than inside whichever consumer needed it first.

**Right now it is close to a pass-through, on purpose.** Neither reader is
complete yet, so the data coming out is only as good as what goes in. The
point of building the pipe first is that the SHAPE is settled: statistics
consumes reconciled sightings, and when the readers get better, and when
the reconciliation grows real rules, nothing downstream changes. Plot what
we have; the seam is where the cleaning will land.

The rules that are already settled, from three hand-written reconciliations
elsewhere in the codebase (production.register_tc_built against the queue
high-water, AgeTracker's crest against the queue's age research, the
checklist's population-cap witness against the feed's house lines):

* reconcile by MAX, never by addition;
* a witness that did not see something has said NOTHING - absence needs
  its own positive evidence;
* counts stay a FLOOR. The queue hides duplicate groups and never reports
  a completion; the feed can be outpaced by the polls. Neither can say
  how many were made, so neither is allowed to pretend to.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from collections import namedtuple

from .queue import kind_of

# Who saw it.
QUEUE = "queue"      # the production queue showed it
FEED = "feed"        # the game announced it in its own message feed
RECORD = "record"    # the game's own log of what it was TOLD to do

# RECORD is not a third pair of eyes on the same thing. The other two watch
# the screen; this one reads the match's own command log after it has
# ended. Three differences follow, and every rule below exists to keep them
# from being quietly averaged away:
#
#   * it answers a DIFFERENT QUESTION. The record says a building was
#     PLACED, the feed says it was BUILT, the queue says it was QUEUED.
#     Placement is not a rival reading of completion.
#   * it holds ORDERS, so its counts are a CEILING. A foundation can be
#     cancelled and a research abandoned. A ceiling can convict a reader
#     that over-fires and can never convict one that under-fires.
#   * it is not evidence Loom could have had at the time.
#
# So it NEVER overrides. It lands in fields of its own - `ordered` and
# `ceiling` - and `disagreements()` reports the difference rather than
# resolving it. The reason is the one that decided the ruling: overwriting
# a reading with truth destroys the signal that the reader needs fixing.
# A stats corpus silently corrected against the record would have hidden a
# twenty-minute clock misread that the disagreement is exactly how we
# found.

# One thing one witness saw, at one moment.
Event = namedtuple("Event", "t subject kind action witness")

# What the witnesses agree happened to one subject over a whole game.
# `count` is a FLOOR and None when nobody could count at all; `first` and
# `last` are game times; `witnesses` is the set that saw it.
# `ordered` and `ceiling` belong to the RECORD alone and are deliberately
# NOT merged with `times` and `count`: they are a different moment and a
# different kind of number. A subject only the record saw has first, last,
# count of None - which is not a gap in the data, it is the finding.
Sighting = namedtuple("Sighting",
                      "subject kind first last count witnesses times "
                      "ordered ceiling")
Sighting.__new__.__defaults__ = ((), None)


# Queue identities that are just villagers. The timeline already tells that
# story second by second, so listing them among the things that happened
# adds a row nobody reads. Mirrors gamestats, which has always left them
# out of `queued` for the same reason.
VILLAGERS = {"villager_male", "villager_female"}


def queue_sightings(game):
    """(when, subject) for everything the QUEUE says was produced.

    TWO SOURCES, AND THE NEWER ONE WINS WHERE IT EXISTS.

    `queued` is the old record: the first time anything was named in a
    slot, believed on one glance. That is how nine things that never
    happened reached a real Post-game page, and how one game listed 65
    subjects of which 45 were never produced - a fleet of dragon ships and
    fireships in a Fast Castle, and campaign technologies no random map can
    hold.

    `episodes` is what loom/episodes.py decided: one production followed
    across polls and named ONCE by vote, from every look that watched it.
    Measured across 39 paired capture runs, that removes 79% of the
    phantoms.

    A file written before episodes existed has only `queued`, and gets it -
    an old game is not improved by being shown less than it recorded.

    REFUSED EPISODES CONTRIBUTE NOTHING HERE, AND THAT IS A GAP. An episode
    the vote would not name carries subject: null, which means "this was
    watched and I decline to name it" - a refusal, not an absence. It
    belongs on the page as REFUSED, and cannot be an Event because an Event
    is about a subject and this one has no name. The rows are in the stats
    file waiting for the panel that will draw them; until then a person
    sees fewer rows than Loom actually knows about, which is the honest
    direction to be wrong in but is still wrong.
    """
    episodes = game.get("episodes")
    if episodes is None:
        return sorted((when, subject)
                      for subject, when in (game.get("queued") or {}).items())
    # ONE sighting per subject, at the earliest episode that named it -
    # deliberately the same shape `queued` had. Episodes can now say how
    # MANY times a thing was produced, which `queued` never could, but
    # turning that on changes what every consumer downstream is counting
    # and belongs in its own change with its own measurement.
    first = {}
    for episode in episodes:
        subject = episode.get("subject")
        when = episode.get("started")
        if not subject or when is None or subject in VILLAGERS:
            continue
        if subject not in first or when < first[subject]:
            first[subject] = when
    return sorted((when, subject) for subject, when in first.items())


def from_recording(game):
    """Every event a recorded game holds, from both witnesses.

    `game` is the "game" section of a stats file. Nothing is merged here -
    this is the raw arrival, one Event per thing one witness saw once.
    """
    found = []
    for when, subject in queue_sightings(game):
        found.append(Event(when, subject, kind_of(subject), "sighted", QUEUE))
    # The feed: one event per line read, named "<action>:<subject>".
    # Lines with no subject (attacked, wild_animals) are game-state
    # events rather than things happening to a subject, and belong to
    # whatever consumer wants them raw.
    for when, name in (game.get("events") or []):
        action, separator, subject = str(name).partition(":")
        if not separator:
            continue
        found.append(Event(when, subject, kind_of(subject), action, FEED))
    return sorted(found, key=lambda event: (event.t, event.subject))


def reconcile(events):
    """Fold events into one Sighting per subject, newest knowledge first.

    The one rule applied so far, and it is the settled one: witnesses are
    UNIONED, times are taken at their extremes, and the count comes from
    the feed's sightings ALONE - never from adding the queue's, which
    would double every subject both readers saw.

    This is where the real cleaning will go when the readers are good
    enough to deserve it. Everything downstream already reads its output,
    so that work will not ripple.
    """
    merged = {}
    per_witness = {}
    for event in events:
        per_witness.setdefault(event.subject, {}).setdefault(
            event.witness, []).append(event.t)
        found = merged.get(event.subject)
        if found is None:
            record = event.witness == RECORD
            merged[event.subject] = Sighting(
                event.subject, event.kind,
                # A subject ONLY the record holds has no screen time at
                # all, and says so. None here is the finding, not a gap.
                None if record else event.t, None if record else event.t,
                1 if event.witness == FEED else None, {event.witness}, ())
            continue
        count = found.count
        if event.witness == FEED:
            count = (count or 0) + 1
        if event.witness == RECORD:
            # The record's clock is a different moment, so it may not move
            # first or last. Only its own fields.
            merged[event.subject] = found._replace(
                witnesses=found.witnesses | {RECORD})
            continue
        first = event.t if found.first is None else min(found.first, event.t)
        last = event.t if found.last is None else max(found.last, event.t)
        merged[event.subject] = found._replace(
            first=first, last=last, count=count,
            witnesses=found.witnesses | {event.witness})
    for subject, sighting in merged.items():
        # `times` is ONE witness's list, never both concatenated. Both see
        # an archer, so merging them would report two archers where one
        # was trained - the same never-add rule the count already obeys.
        # The feed wins when it saw anything, because it announces each
        # occurrence while the queue only shows what is in progress.
        seen = per_witness[subject]
        ordered = tuple(sorted(seen.get(RECORD) or ()))
        merged[subject] = sighting._replace(
            times=tuple(sorted(seen.get(FEED) or seen.get(QUEUE) or ())),
            ordered=ordered,
            # A CEILING, and named one. It is how many times the player
            # ORDERED this - cancellations included - so it bounds what
            # can have happened without stating what did.
            ceiling=len(ordered) or None)
    return sorted(merged.values(), key=lambda s: (_when(s), s.subject))


def _when(sighting):
    """Something to sort by, for a sighting that may have no screen time."""
    if sighting.first is not None:
        return sighting.first
    return sighting.ordered[0] if sighting.ordered else 0


# A backwards step no bigger than this is the clock WOBBLING, not the
# game starting again. Measured across 252 recorded games: real seams
# jump back hundreds of seconds (529 to 7), while jitter is one to three.
SEAM_TOLERANCE_SECONDS = 5


def usable_run(times):
    """(start, stop) of the longest stretch whose clock only goes forward.

    A recorded series can hold the same game time twice, and 26 of 252
    recorded games do. Twenty of them are one clean SEAM - the clock
    climbing to 8:49, resetting to 0:07 and climbing again - which is the
    session detector missing a restart and letting two games share a
    file. Drawn as one series that is two lines crossing each other, and
    it is why the APM chart appeared to have two APM lines.

    Neither line is wrong; the file is. So rather than inventing a merged
    truth, this picks the longest self-consistent run and leaves the
    caller to say how much was set aside. Longest because it is the most
    complete pass, and ties go to the later one because that is the game
    still being played when the file was written.

    Small backward steps do NOT split a run: the clock is read off the
    screen and wobbles by a second or two, and treating that as a new
    game would shred every series into fragments.
    """
    if not times:
        return 0, 0
    best = (0, 1)
    start = 0
    for index in range(1, len(times) + 1):
        ended = index == len(times)
        if not ended and times[index] >= times[index - 1] - \
                SEAM_TOLERANCE_SECONDS:
            continue
        if index - start >= best[1] - best[0]:
            best = (start, index)
        start = index
    return best



def from_record(truth):
    """Every order the recorded game holds, as Events.

    `truth` is a loom.replay.Truth - one player's commands out of a match
    that has ENDED. Buildings and technologies carry their own order times;
    trained units are a running total in the record with no per-unit time,
    so they arrive as a count with no clock and are counted, not timed.
    """
    found = []
    for ident, placements in (truth.builds or {}).items():
        subject = truth.name_of_building(ident)
        for when, _x, _y in placements:
            found.append(Event(when, subject, kind_of(subject),
                               "placed", RECORD))
    for ident, times in (truth.researches or {}).items():
        subject = truth.name_of_tech(ident)
        for when in times:
            found.append(Event(when, subject, kind_of(subject),
                               "researched", RECORD))
    return sorted(found, key=lambda event: (event.t, event.subject))


# What a disagreement between Loom and the record means.
OVERFIRED = "overfired"    # Loom read MORE than the player ever ordered
UNREAD = "unread"          # the player ordered it and Loom never saw it

Disagreement = namedtuple("Disagreement", "subject kind read ceiling verdict")


def disagreements(sightings):
    """Where Loom and the recorded game do not agree, stated not resolved.

    This is the whole point of carrying a third witness. The author's
    ruling is that the record is the Bible and differences are LOGGED
    rather than reconciled, and the reason is sharper than deference: the
    gap between what Loom read and what the game recorded is the only
    signal that a reader needs fixing. Overwriting one with the other
    throws it away.

    Only ONE direction is proof. Reading more than was ever ordered cannot
    happen, so it convicts the reader. Reading FEWER is not proof of
    anything - a foundation can be cancelled and a research abandoned, so
    the record's count is a ceiling and falling under a ceiling is allowed.
    It is still worth reporting, and it is reported as a different verdict
    wearing a different name, because collapsing "provably wrong" into
    "possibly missed" would be the same failure as an assumption dressed
    up as a reading.
    """
    found = []
    for sighting in sightings:
        if sighting.ceiling is None:
            continue                       # the record says nothing here
        read = sighting.count
        if read is not None and read > sighting.ceiling:
            found.append(Disagreement(sighting.subject, sighting.kind,
                                      read, sighting.ceiling, OVERFIRED))
        elif not read:
            found.append(Disagreement(sighting.subject, sighting.kind,
                                      read or 0, sighting.ceiling, UNREAD))
    return found


def with_record(game, truth):
    """The whole pipe with the recorded game joined in.

    for_statistics without a record stays exactly what it was, so every
    existing file and every caller is unaffected.
    """
    events = from_recording(game)
    if truth is not None:
        events = sorted(events + from_record(truth),
                        key=lambda event: (event.t, event.subject))
    return reconcile(events)



# A thing ordered and not finished within this long was almost certainly
# cancelled, destroyed while it went up, or never reached by its builder.
# Generous on purpose: the point is to refuse an absurd pairing, not to
# judge a slow one. A Wonder is 3500 villager-seconds and a lone villager
# walking across a map adds minutes to that.
LONGEST_PLAUSIBLE_BUILD = 900

Duration = namedtuple("Duration", "subject kind ordered completed seconds")


def build_durations(sightings):
    """How long each thing took, from being ORDERED to being ANNOUNCED.

    The one honest use of the record's placement times. On their own they
    are no evidence about the notification reader - a placement is not a
    rival reading of a completion - but SUBTRACTED FROM a completion they
    measure something Loom cannot otherwise know at all: how long the
    player's building actually took to go up.

    WHAT THIS NUMBER CONTAINS, because it is not just build time. The
    clock starts when the order is issued, so it includes the villager
    WALKING to the site, any pause before work begins, and the work
    itself divided among however many builders helped. It is therefore
    always at least the build time and usually more, and it is not
    comparable between games without knowing all three.

    Placements are consumed IN ORDER against completions. A build names
    "house" many times, and crediting every house with the first
    completion ever seen is the global-ledger mistake that once reported
    an item 580 seconds early.
    """
    found = []
    for sighting in sightings:
        if not pairing_is_sound(sighting):
            continue
        completions = list(sighting.times)
        for placed in sighting.ordered:
            while completions and completions[0] < placed:
                # A completion before this order belongs to an earlier one
                # of the same thing, already paired or never ordered.
                completions.pop(0)
            if not completions:
                break                      # ordered, never announced
            took = completions[0] - placed
            if took > LONGEST_PLAUSIBLE_BUILD:
                continue                   # cancelled, or a foreign pairing
            if took < floor_for(sighting.subject, sighting.kind):
                # Physically impossible, so the PAIRING is wrong rather
                # than the building being fast. Measured on a real game:
                # "loom took 0s" and "feudal_age took 1s", both against a
                # notification reader that was over-firing at the time.
                completions.pop(0)
                continue
            found.append(Duration(sighting.subject, sighting.kind,
                                  placed, completions.pop(0), took))
    return sorted(found, key=lambda d: d.ordered)



def pairing_is_sound(sighting):
    """May this subject's orders be matched to its completions at all?

    Only when Loom read EXACTLY as many completions as the player ordered.
    Anything else and the pairing drifts, silently and cumulatively: one
    missed completion shifts every later order onto the next one's finish,
    so the errors grow rather than cancelling.

    Measured on a game whose notification reader was over-firing, houses
    came back at 268, 480, 436, 542 and 521 seconds - numbers that look
    like data and are pure drift. Refusing them costs the statistic on a
    lossy game and is the only honest option: a duration is a difference
    between two readings, so it is no better than the WORSE of the two,
    and there is no way to tell a drifted pair from a slow build by
    looking at it.

    This is deliberately strict about commodities - houses, farms, the
    things a build names many times - which are exactly where drift does
    the most damage and where a wrong number is least likely to look
    wrong.
    """
    if not sighting.ordered or not sighting.times:
        return False
    return sighting.count == sighting.ceiling


def floor_for(subject, kind):
    """The fastest this thing could possibly have been, in game seconds.

    Only technologies have one, and the reason is the asymmetry in
    loom/durations.py: a BUILDING's time divides among however many
    villagers help, so three builders finish a house in a third of the
    listed time and no floor from that table is safe. RESEARCH does not
    divide - a Town Centre researches Loom at one speed whatever else is
    happening - so its listed time is a genuine floor, and anything under
    it proves the pairing wrong rather than the player fast.
    """
    if kind != "technology":
        return 0
    from .durations import build_or_research_time
    return build_or_research_time(subject)


def of_kind(sightings, wanted):
    """Just the sightings of one kind - "unit", "technology", "unknown"."""
    return [sighting for sighting in sightings if sighting.kind == wanted]


def for_statistics(game):
    """The whole pipe, end to end: a recorded game to its sightings."""
    return reconcile(from_recording(game))
