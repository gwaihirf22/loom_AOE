"""
Loom — which of a step's items have been done.

A build step is a list of instructions, and a player works through that list
rather than performing it as one act. This is the record of which ones have
happened: pure state plus arithmetic over a stream of events, no Qt and no
pixels, so it tests with made-up event names exactly like session.py and
production.py do.

TWO KINDS OF TICK, AND THEY MUST NOT LOOK ALIKE.

    OBSERVED   the game said so. "--Mill Built--" means the Mill is built.
    ASSUMED    the build moved past the step and nothing contradicted it.

The distinction is the whole honesty of the feature. An assumed tick is a
guess, and this project's oldest rule is that a guess wearing the same mark
as a reading is worse than no mark at all - a wrong villager count silently
desynchronises everything, and a checklist that confidently claims you built
a Mill you never built is the same failure in a new place. So the two states
are carried separately here and drawn differently by both front ends.

WHAT CAN BE OBSERVED comes from the build file itself, not from parsing
English. 331 of the 344 note pieces in the shipped builds carry an @icon@
token naming the exact entity - @mill/Mill_aoe2de.webp@, @age/FeudalAgeIconDE@
- and glyphs.parse_event turns a read notification line into the same
vocabulary of slugs. So "Build a @mill/Mill_aoe2de.webp@" lines up with
"built:mill" with nothing in between to guess wrong. Items whose only tokens
are villagers, resources or animals have no observable completion and are
assume-only; see ASSUME_ONLY_FOLDERS.

TICKS DO NOT HAVE TO ARRIVE IN ORDER. Players adapt, misclick, and do things
early; a Mill built two steps before the build asks for one is still a Mill.
So an event ticks the first item anywhere in the build that wants it and has
not been ticked yet, and state is kept for the whole game and never cleared -
which is what lets the preview cross something off on a card the player has
already scrolled past.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import re

from . import build_order, glyphs

# Castle Age, numbered as the build format numbers ages. Not imported from
# loom.age to keep this module free of cv2 - the number is the format's.
CASTLE_AGE = 3

# The three states of an item. NOT_DONE is None so a bare truth test reads
# naturally at the call site and an unknown step index answers the same way
# as an unticked item.
NOT_DONE = None
ASSUMED = "assumed"
UNCONFIRMED = "unconfirmed"
OBSERVED = "observed"

# UNCONFIRMED is the fourth state and the reason there are four rather than
# three. Once the build has passed a step, "assumed" covers two situations
# that are not alike at all:
#
#   ASSUMED      the build moved past it and Loom could never have known
#                either way. "Next 3 Villager to Wood" is never announced.
#   UNCONFIRMED  the build moved past it, Loom WAS watching for it, and
#                never saw it. A Mill the feed never reported.
#
# Only the second is news, and it is what the preview draws attention to on
# a card the player has scrolled past.
#
# It must never be phrased as "you did not do this", and the visual weight
# has to match that. Loom reads the notification feed at somewhere between
# a quarter and three-quarters of lines depending on the rendering, so a
# missing sighting is genuinely weak evidence - this is the "I did not read
# it is not it is not there" rule, and absence here has no positive
# evidence behind it at all. The word is UNCONFIRMED for that reason.

# Icon folders whose contents the game never announces as a completion.
#
# A villager, a pile of wood and a boar are all things a step can be ABOUT
# without there being any moment the game reports. "--Villager Created--"
# fires every twenty-five seconds all game and says nothing about whether
# "Next 3 Villager to Wood" was carried out; "--Boar Found--" fires when the
# boar is SEEN, which is not when it is lured. Items like these are
# assume-only rather than wrongly observable, because a tick that means
# nothing is worse than no tick.
ASSUME_ONLY_FOLDERS = {"resource", "animal"}

# Subjects the player produces continuously, far beyond what any build
# writes down. Community build orders are PACING GUIDES, not scripts - the
# author's example: Arena Fast Conqs asks for one house before Castle Age
# and a real game needs six - so a global ledger over these lies almost
# immediately: the game's fourth routine house would credit a step the
# player has not reached. These credit only inside a WINDOW around the
# current step instead; a sighting outside every window credits nothing.
#
# Houses only, by the author's ruling (2026-08-22). Everything else stays
# global on his game knowledge: production buildings (barracks, stable,
# archery range) number one to three before Castle Age and most builds end
# there; camps and TCs likewise; a technology exists once per game, ever.
# Farms were the other candidate and the question turned out not to
# arise - the game never announces one, so they are not watched at all
# now; see IGNORED_SUBJECTS.
COMMODITY_SUBJECTS = {"house"}

# Subjects that are never watched, because a watched subject the feed can
# never report makes its whole item unfinishable - the item sits amber for
# the rest of the game with no way to complete, taking any subject sharing
# that item down with it. Two reasons put a subject here, and the walls
# satisfy both.
#
# Walls, by the author's standing ruling: situational - built, or not, as
# the map demands - so a build mentioning one is giving advice, not setting
# a task to verify. The live game that proved it: "builds 2 House,
# Palisade Wall gaps to the Town Center" made the wall a required subject,
# and since no wall event ever came, the houses could never tick either.
#
# FARMS, because the game does not announce them at all. This was assumed
# the other way when the checklist was written ("farms complete close
# together, so the feed dedups them and they will often sit amber"), and
# that assumption was wrong: the labelled corpus holds 900 lines from
# eight games and every announced building in it - twenty-two kinds,
# houses 20 times, mills 15, markets 8 - with ZERO farm lines. Farms are
# the most-built structure in a long game, so eight games without one is
# not a sampling gap. The game says "--Farm Exhausted--" when one runs
# out, which is a different event about a different moment, and there is
# no "--Farm Built--" for it to be confused with. Eight items across the
# shipped builds were waiting on a line that is never printed.
#
# Fish traps join them on the same reasoning, one step less certain: no
# shipped build asks for one, so nothing changes today, and they are the
# water farm in every respect that matters here.
IGNORED_SUBJECTS = {"palisade_wall", "stone_wall", "fortified_wall", "wall",
                    "farm", "fish_trap"}

# The ages, whose completions are once-per-game facts. When one arrives,
# EVERY item naming it becomes true at once - "Click Feudal Age", "Before
# Feudal Age" and "In Feudal Age" are three views of the same moment, not
# three deliverables. First-item-only crediting blocked a live game twice:
# the click item consumed the single age event, and "In Feudal Age: build
# Market with 2 Villager, Blacksmith with 1" could never complete even
# with the market and blacksmith both read.
AGE_SUBJECTS = {"feudal_age", "castle_age", "imperial_age"}

# The same ages as the numbers the build format uses (Step.age), so a
# card's own age can be compared against the age an item asks for. The
# spelled-out dict rather than an import from loom.age keeps this module
# free of cv2, which loom.age needs for the crest.
AGE_NUMBERS = {"feudal_age": 2, "castle_age": 3, "imperial_age": 4}

# A token preceded by a preposition is a PLACE, not an ask: "gaps to the
# Town Center", "Move 3 Villager from Town Center", "far from Lumber
# Camp". Measured against the whole library: five mentions change, four
# plainly locations wrongly watched before this. The fifth is the trap the
# age exemption below exists for - "click up to Feudal Age" is the ask
# itself, and an age is never a place.
LOCATION_BEFORE = re.compile(
    r"(to|under|at|near|from|behind|beside|around|toward|towards)"
    r"\s+(the\s+|your\s+)?$", re.IGNORECASE)

# The same idea one step along: a token preceded by "use" or "with" is the
# INSTRUMENT the step is worked with, not a thing to deliver. "Use Market
# to build 2nd Town Center" asks for a Town Centre; the Market is where
# the stone gets sold and was built eight minutes earlier.
#
# It cost a live game exactly what the location rule was written for. The
# market's one event had already been spent crediting the step that DID
# ask for it, so this item wanted a second market line that was never
# coming - and the second Town Centre, read cleanly off the feed at 22:46,
# could not tick the item it belonged to. Failing shut, as always: the
# player builds the thing and the card stays amber.
#
# Audited against the whole library, as the location rule was: four
# mentions match, and every one is an instrument. Two are this same
# "Use Market to build 2nd Town Center", one is "scout with Scout
# Cavalry", and the fourth ("sell with the stone") names a resource,
# which was never watchable anyway.
INSTRUMENT_BEFORE = re.compile(
    r"(use|uses|using|with)\s+(the\s+|your\s+)?$", re.IGNORECASE)

# The window, in steps around current_index (the last COMPLETED step): the
# just-completed card, the active card, and the next one - the three the
# preview shows, and one card of working-ahead, which is how players
# actually play. Wider forgives more early work but weakens what a green
# tick means.
COMMODITY_WINDOW = 2

# Naming a thing is not asking for it to be MADE, and getting that wrong is
# the worst error this module can make: a confident green tick on an
# instruction the player never carried out.
#
# "Eat it under the @town_center@" names a Town Centre because the Town
# Centre is WHERE to eat, and you cannot build one in the Dark Age anyway.
# It ticked green off a "--Town Center Built--" line. So did "Move 5
# Villager from @town_center@ to Wood". And "Next 2 Villager to Wood (still
# only 1 @lumber_camp@)" names a Lumber Camp precisely to say you are NOT
# building a second one.
#
# So the default is inverted: an item is assume-only unless something says
# it is an instruction to make, research or train the thing. The verbs
# below are that something. Failing to recognise a phrasing costs a faded
# tick where a green one was possible; the opposite costs a lie, and the
# whole feature rests on those two not looking alike.
#
# Community builds are written by people, so the list is what those people
# actually wrote - "Hammer 4 House" and "Get Heavy Plow later" are both
# real lines from the shipped builds, and both mean build and research.
MAKE_VERBS = re.compile(
    r"\b(add|build|builds|building|create|creates|get|hammer|make|makes|"
    r"place|places|produce|production|research|researches|seed|seeds|"
    r"train|trains|up)\b", re.IGNORECASE)


def token_subject(token):
    """One @icon@ token as the slug the notification feed would use.

    '@mill/Mill_aoe2de.webp@'      -> 'mill'
    '@age/FeudalAgeIconDE.webp@'   -> 'feudal_age'

    Shares icon_to_words with the drawing code on purpose: whatever the
    panel calls a thing is what is matched against, so a token that reads
    correctly on screen cannot silently match nothing.
    """
    return glyphs.slugify(build_order.icon_to_words(token))


# The count an instruction asks for is its own trailing number: the text
# piece immediately before a token ends "Build 2", "Hammer 4", "Seed 4".
# A bare digit only - "build 2nd Lumber Camp" ends in "2nd", which names
# WHICH camp rather than how many, and is one camp.
TRAILING_COUNT = re.compile(r"(\d+)\s*$")

# A count past this is a typo or a misparse, not an instruction. No shipped
# build asks for more than 6 of anything in one item.
MAX_ITEM_COUNT = 20


def item_evidence(segments):
    """What would prove this item done: ((subject, count), ...) in order.

    `segments` is one entry of Step.items_segments. Empty means the item has
    no observable completion at all and can only ever be assumed.

    EVERY observable token, WITH ITS COUNT, and the item is done only when
    each has been seen that many times. Both halves were learned from real
    games. The first draft took only the first token, so "Build 2 House,
    then Mill at Berries" never matched a Mill; the second took every token
    but counted each once, so "Build 2 House" went green on the FIRST
    "--House Built--" - a confident claim that two houses stood when one
    did. The author watched it happen. The count comes from the build file
    itself, so nothing is inferred: the number the author wrote is the
    number of sightings the item costs.

    Failing stays in the safe direction: an item not fully done stays
    unticked, where the alternative reports a job half finished as
    complete. That includes the genuinely unknowable case - two farms
    finishing in the same feed redraw print ONE line, because the game
    never reprints a line that is still on screen - which undercounts, and
    the item honestly stays amber rather than guessing the second farm.
    """
    counts = {}
    order = []
    previous_text = ""
    for kind, value in segments:
        if kind == "text":
            previous_text = value
            continue
        folder = value.split("/")[0]
        if folder in ASSUME_ONLY_FOLDERS:
            previous_text = ""
            continue
        subject = token_subject(value)
        if subject in IGNORED_SUBJECTS:
            previous_text = ""
            continue
        if (subject and folder != "age"
                and (LOCATION_BEFORE.search(previous_text + " ")
                     or INSTRUMENT_BEFORE.search(previous_text + " "))):
            previous_text = ""
            continue          # a place or a tool, not a thing to deliver
        if subject:
            found = TRAILING_COUNT.search(previous_text)
            count = int(found.group(1)) if found else 1
            count = max(1, min(count, MAX_ITEM_COUNT))
            if subject not in counts:
                order.append(subject)
            counts[subject] = counts.get(subject, 0) + count
        previous_text = ""
    if not order:
        return ()
    if not asks_for_it(segments):
        return ()
    return tuple((subject, counts[subject]) for subject in order)


def asks_for_it(segments):
    """Does this item ask for the thing to be MADE, or merely name it?

    Three ways to say yes, and everything else is no:

    * a making verb somewhere in the words (see MAKE_VERBS);
    * an @age/...@ token, because an age is only ever named to talk about
      advancing to it, and Loom reads that off the HUD directly;
    * the item being essentially nothing BUT the token - "Double Bit Axe &
      Horse Collar" is a bare list of technologies under a step that is
      plainly telling you to research them, and there is no verb to find.
    """
    words = " ".join(value for kind, value in segments if kind == "text")
    if MAKE_VERBS.search(words):
        return True
    if any(kind == "icon" and value.split("/")[0] == "age"
           for kind, value in segments):
        return True
    # Nothing but the tokens, give or take punctuation and a count.
    leftover = re.sub(r"[0-9&,/()+.\-]", " ", words).strip()
    return not leftover


def _same_subject(subject, wanted):
    """The key in `wanted` naming the same thing as `subject`, or None.

    Same words, same order, boundaries ignored: "scout_cavalry" and
    "scoutcavalry" are one subject whose two sources drew the underscores
    differently. Ignoring boundaries cannot conflate two genuinely
    different subjects unless their letters are identical in sequence -
    at which point they were the same words all along.
    """
    flat = subject.replace("_", "")
    for name in wanted:
        if name.replace("_", "") == flat:
            return name
    return None


def event_subject(name):
    """The slug a feed event is ABOUT, or None for events about nothing.

    glyphs.parse_event writes "built:mill", "researched:loom" and the one
    legacy name "town_center_built"; "attacked" and the unclassified
    "line:..." catch-all are about no particular entity and answer None.
    """
    if name == "town_center_built":
        return "town_center"
    kind, separator, subject = name.partition(":")
    if not separator or kind == "line":
        return None
    return subject or None


class Checklist:
    """Which items of which steps are done, for one game.

    Feed it every poll: the step the reading has reached, and whatever the
    notification feed said. Ask it for the state of any item at any time.
    """

    def __init__(self, build):
        self.build = build
        # (step index, item index) -> ASSUMED | OBSERVED. Absent is NOT_DONE.
        self._state = {}
        # Every item that could be observed, in build order, as
        # (step index, item index, subjects). Built once: the build does not
        # change during a game, and an event otherwise costs a walk of every
        # segment of every item.
        self._watchable = []
        for step_index, step in enumerate(build.steps):
            for item_index, segments in enumerate(step.items_segments):
                wanted = item_evidence(segments)
                if wanted:
                    self._watchable.append(
                        (step_index, item_index, dict(wanted)))
        # The same thing as a set, for state() - which is asked once per
        # item per repaint and must not walk the list.
        self._watchable_index = {(step, item)
                                 for step, item, _s in self._watchable}
        # (step index, item index) -> the subjects of that item seen so far.
        # An item goes OBSERVED when this catches up with what it wants.
        self._seen = {}
        # The last step the build has moved past. Everything up to here is
        # ASSUMED unless it was actually observed.
        #
        # A view rather than an accumulated fact, and that is deliberate:
        # it FOLLOWS the index both ways, so a hotkey scrolled backwards or
        # a new match starting un-assumes by itself, with nothing to reset
        # and no way to leave the last game's guesses lying on the cards.
        # Observed ticks are the opposite - they are facts, they accumulate,
        # and reset() is the only thing that clears them.
        self._through = -1

    # ---- feeding -------------------------------------------------------

    def observe(self, current_index, events=()):
        """One poll. `current_index` is in build_order.current_index terms -
        the last step already reached, -1 before the first.

        Events are applied BEFORE the assumptions, so an item the game
        announced in the same poll that its step completed is recorded as
        observed rather than assumed. The two are not interchangeable and
        the stronger one has to win whichever order they arrive in.
        """
        for name in events:
            self._apply_event(name, current_index)
        self._assume_through(current_index)

    def _apply_event(self, name, current_index=None):
        """Credit this event to the first item still waiting for it.

        First rather than nearest-to-now: a build that asks for two Houses
        in different steps gets one credited per "--House Built--", in
        order, and a player who built one early has genuinely built the
        first one. One event credits ONE item, so two Houses take two lines.

        The item only becomes OBSERVED once every subject it names has
        arrived AS MANY TIMES as it asks - "Build 2 House" costs two
        "--House Built--" lines, and the count is the build file's own
        number rather than a guess. An item whose count is full wants
        nothing more, so the event moves on to the next candidate: the
        third house line, with "Build 2 House" full, credits the later
        "Build 1 House" - which is what lets work done early tick a step
        further down, without one sighting ever paying for two
        instructions.
        """
        subject = event_subject(name)
        if subject is None:
            return
        if subject in AGE_SUBJECTS:
            # An age arriving makes every mention of it true at once - see
            # AGE_SUBJECTS. No early return: the click item, the "Before"
            # header and the "In" header all fill together.
            for step_index, item_index, wanted in self._watchable:
                if subject not in wanted:
                    continue
                key = (step_index, item_index)
                seen = self._seen.setdefault(key, {})
                seen[subject] = max(seen.get(subject, 0), wanted[subject])
                if all(seen.get(name_, 0) >= count
                       for name_, count in wanted.items()):
                    self._state[key] = OBSERVED
            return
        windowed = subject in COMMODITY_SUBJECTS
        for step_index, item_index, wanted in self._watchable:
            # Word boundaries are the one thing the two sides of this
            # comparison never agreed to share: the item's subject comes
            # from an icon FILENAME and the event's from the game's own
            # line, and "Scoutcavalry_aoe2DE.webp" vs "--Scout Cavalry
            # Created--" made an item no event could ever credit. Both
            # spell the same words in the same order, so the comparison
            # drops the boundaries rather than trusting either side to
            # have drawn them - which fixes the whole filename category,
            # not the one file that happened to be noticed.
            name_ = _same_subject(subject, wanted)
            if name_ is None:
                continue
            if windowed and current_index is not None:
                # A commodity sighting may only credit an item near the
                # current step. The game's routine houses outnumber the
                # build's written ones, so a distant match is far more
                # likely to be supply than the instruction - and crediting
                # it would tick a card the player has not reached off a
                # house they built to avoid being housed.
                if not (current_index <= step_index
                        <= current_index + COMMODITY_WINDOW):
                    continue
            key = (step_index, item_index)
            seen = self._seen.setdefault(key, {})
            if seen.get(name_, 0) >= wanted[name_]:
                continue         # this item is full of these; try the next
            seen[name_] = seen.get(name_, 0) + 1
            if all(seen.get(name, 0) >= count
                   for name, count in wanted.items()):
                self._state[key] = OBSERVED
            return

    def _assume_through(self, current_index):
        """Note how far the build has moved. Everything before it is assumed.

        The coarsest honest rule there is. The file format times STEPS, not
        items, so anything finer - "the third item of five is due 40% of the
        way through" - would be precision Loom invented rather than read.

        THE FINAL STEP IS NEVER ASSUMED PAST. The build "completes" by
        count, clock and age, but the last card's work has no gate to
        prove it - and under the resting design the panel STAYS on that
        card while the player does it. Grading its items as passed the
        moment the cursor arrived showed "train your Unique Unit" struck
        as if done and the castle as missed while the stone was still
        being quarried (the author, mid-game: "I didn't miss it yet - I
        just got here"). So the last card's items stay open until Loom
        actually observes them, and the unobservable ones simply stay
        open - the player, not the cursor, finishes the last card.
        """
        if current_index is None:
            self._through = -1
            return
        self._through = min(int(current_index), len(self.build.steps) - 2)

    def reset(self):
        """Forget everything. Call when a new match starts - a checklist
        carrying the last game's ticks is the same silent desynchronisation
        a stale step cursor would be."""
        self._state.clear()
        self._seen.clear()
        self._through = -1

    # ---- asking --------------------------------------------------------

    def state(self, step_index, item_index):
        """NOT_DONE, ASSUMED or OBSERVED for one item.

        Observed beats assumed whichever arrived first: a fact does not stop
        being a fact because the build has since moved past its step.
        """
        recorded = self._state.get((step_index, item_index))
        if recorded is not None:
            return recorded
        if step_index > self._through:
            return NOT_DONE
        # Passed. Was this something Loom could have seen?
        if (step_index, item_index) in self._watchable_index:
            return UNCONFIRMED
        return ASSUMED

    def states(self, step_index, count):
        """The states of a step's first `count` items, in order.

        The shape both front ends want: they are drawing a list and need one
        state per row, and a step index outside the build answers all
        NOT_DONE rather than raising - the preview draws empty slots above
        the first step and below the last.
        """
        return [self.state(step_index, item) for item in range(count)]

    def click_prerequisites_settled(self, step_index):
        """The verdict that gates a CLICK UP band, or None for no verdict.

        The author's rule: anything VERIFIABLE before the card's own
        age-up ask must be done before Loom urges the click - the game
        will refuse a click without its prerequisite buildings anyway -
        and anything after the ask, or on later cards, never gates.

        The ask is the first item wanting an age ABOVE the card's own.
        Above, not any: a card's "In Feudal Age:" heading wants
        feudal_age too, and the heading is where the card IS, not where
        it is going. Mistaking it for the ask made every item "after the
        click" and the gate empty. The ask item itself never gates -
        card 8's ask also wants castle_age, which cannot be observed
        until the click succeeds, so counting it held the gate shut
        forever and a patience clock decided everything.

        "No verdict" and "not done" stay different answers: the band
        goes quiet on False and only on False.
        """
        try:
            own_age = self.build.steps[step_index].age
        except IndexError:
            return None
        ask_item = None
        for step, item, subjects in self._watchable:
            if step != step_index:
                continue
            if any(AGE_NUMBERS.get(subject, 0) > own_age
                   for subject in subjects):
                ask_item = item
                break
        if ask_item is None:
            return None
        verdicts = [self._state.get((step, item)) == OBSERVED
                    for step, item, _subjects in self._watchable
                    if step == step_index and item < ask_item]
        if not verdicts:
            return None
        return all(verdicts)

    def missed(self, step_index):
        """Item indices of a passed step that were never actually observed.

        What the preview shows on a card behind you: not "you did these",
        but "nothing ever confirmed these". Only meaningful for items that
        COULD have been observed, so assume-only items never appear here -
        they were never going to produce evidence and listing them would be
        a complaint about the format rather than about the player.
        """
        if step_index > self._through:
            return []       # not passed yet: nothing has been missed
        return [item_index
                for step, item_index, _subjects in self._watchable
                if step == step_index
                and self._state.get((step, item_index)) != OBSERVED]

    def observed_ticks(self):
        """Every item the GAME confirmed, as sorted [step, item] pairs.

        What travels between the overlay and the preview. Assumptions do not
        need to: they are a pure function of the step index and both windows
        have it. An observed tick is a fact only the process reading the
        notification feed can know, so it is the only thing worth sending -
        and there are at most a couple of dozen in a whole build.
        """
        return sorted([step, item]
                      for (step, item), state in self._state.items()
                      if state == OBSERVED)

    def apply_observed(self, pairs):
        """Mark these [step, item] pairs observed. The receiving half of
        observed_ticks; replaces whatever was believed before, so a reset in
        the sending process arrives as a reset here."""
        for key in [k for k, v in self._state.items() if v == OBSERVED]:
            del self._state[key]
        for step, item in pairs or ():
            self._state[(int(step), int(item))] = OBSERVED

    def anything_unconfirmed(self, step_index):
        """Is there anything on this step the feed never confirmed?

        What the preview asks to decide whether a passed card is worth
        drawing attention to at all.
        """
        return bool(self.missed(step_index))

class HouseEvidence:
    """Count completed houses from the population cap, before Castle Age.

    The notification feed misses houses BY DESIGN: the game never reprints
    a line that is still on screen, so two houses finishing within one
    line's lifetime print one line. Measured in the author's drush games -
    the opening "Build 2 House" produced one "--House Built--" every time,
    and no reader, however precise, can read a line that was never drawn.

    The population cap is the second witness, and before Castle Age it is
    an EXACT one: a house is +5 cap, and nothing else can raise the cap at
    all until Castle Age unlocks Town Centres and Castles. Measured on the
    same game, the cap stepped 5->10 at 0:19 and 10->15 at 0:26 - the
    second step being precisely the house the feed never printed.

    The two witnesses are reconciled by MAX, never added - the same rule
    production.py uses for Town Centres, because the same house produces
    both signals. update() returns how many house completions are newly
    evidenced this poll, beyond everything already credited by either
    channel.

    After Castle Age the cap channel goes quiet on its own: a +5 could be
    a Town Centre, so it proves nothing about houses, and only the feed
    counts from there. Cap DECREASES (a house razed in a raid) lower the
    baseline, so a rebuilt house counts again - it is a new house.
    """

    def __init__(self):
        self._baseline = None      # cap when counting started
        self._cap = None           # last believed cap
        self._from_cap = 0         # houses evidenced by cap steps
        self._from_feed = 0        # houses evidenced by the feed
        self._credited = 0         # what has been handed out so far

    def update(self, cap, age, feed_houses):
        """One poll: the believed cap, the believed age, and how many
        built:house events the feed produced this poll. Returns how many
        NEW house completions to credit."""
        self._from_feed += feed_houses

        counting = age is not None and age < CASTLE_AGE
        if cap is not None:
            if self._cap is None:
                self._baseline = cap
            elif cap > self._cap:
                if counting:
                    self._from_cap += (cap - self._cap) // 5
            elif cap < self._cap:
                # Houses lost. The next one built is a new house, so the
                # floor moves down with the cap rather than waiting for it
                # to climb back past old ground.
                pass
            self._cap = cap

        evidenced = max(self._from_cap, self._from_feed)
        new = max(0, evidenced - self._credited)
        self._credited = evidenced
        return new

    def reset(self):
        self.__init__()


# The feed's own age-completion events, which the crest supersedes.
AGE_EVENTS = {2: "researched:feudal_age", 3: "researched:castle_age",
              4: "researched:imperial_age"}


def merged_age_events(game_events, ages_reached):
    """The event list with age completions RECONCILED to the crest.

    The author watched this gap live: the panel said "queued" at the click
    (the queue reader saw it), the age completed, and the build's Feudal
    Age item sat amber to the end of the game. The feed's
    "--Feudal Age Research Complete--" line is long, wraps at some
    renderings, and often never reads - which is the exact reason the age
    CREST reader was built, and it read every transition in every capture
    with not one frame unread. But its REACHED events went only to the
    statistics; the checklist never heard.

    Same shape as merged_house_events and for the same reason: one age-up
    produces both signals, so the feed's copies are dropped and the
    crest's verdict stands in for them. An age the crest reports and the
    feed also read must arrive once, not twice - twice would green a
    SECOND item naming the same age, and there is usually one ("19 pop
    Feudal Age" and "Before Feudal Age" both carry the age token).

    `ages_reached` is the ages whose REACHED fired this poll, from
    age.AgeTracker.
    """
    kept = [event for event in game_events
            if event not in AGE_EVENTS.values()]
    kept += [AGE_EVENTS[age] for age in ages_reached if age in AGE_EVENTS]
    return kept


def merged_house_events(game_events, new_houses):
    """The event list to hand the checklist, houses RECONCILED not added.

    HouseEvidence.update already counted the feed's own built:house events
    into its total, so its return value is the whole answer for houses -
    and the feed's copies must be dropped, or one house arrives twice. That
    is not hypothetical: the author built ONE house in a 42-second test
    game, the feed line and the credit both went through, and "Build 2
    House" ticked green. The replay that had "verified" the feature only
    exercised update() and never this combining step, which is exactly why
    the combining step lives here, named and tested, instead of inline in
    the overlay.
    """
    return ([event for event in game_events if event != "built:house"]
            + ["built:house"] * new_houses)

