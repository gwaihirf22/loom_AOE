"""
Loom — build orders.

Loads a build order from JSON and answers the questions the overlay needs:
which step am I on, what is next, and am I ahead or behind?

This module is pure logic. It never touches the screen, so it can be tested
with made-up numbers and no game running.

The file format is the one used by RTS Overlay, because that is what the AoE2
community actually shares build orders in. Any build downloaded from that
ecosystem should load here unchanged. Fields per step:

    villager_count   how many villagers you should have
    age              1 Dark, 2 Feudal, 3 Castle, 4 Imperial
    time             "M:SS" or "H:MM:SS" - game time, as shown on the clock
    resources        villagers on food / wood / gold / stone
    notes            instructions, "|" separating one main action from extras
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import re

from . import paths

# Community build orders embed icon references like
# "@animal/Boar_aoe2DE.webp@" in their notes. They are useful later for
# drawing icons in the overlay, but for now I want plain readable text.
ICON_PATTERN = re.compile(r"@([^@]+)@")

RESOURCE_NAMES = ("food", "wood", "gold", "stone")

# A Town Center trains a villager in about twenty-five seconds. That is the
# finest resolution any pace measurement can honestly claim, which is why the
# "on pace" band in the UI is roughly that wide.
VILLAGER_INTERVAL_SECONDS = 25


def parse_time(text):
    """'7:30' -> 450 seconds. Also accepts 'H:MM:SS'. None if unparseable."""
    if not text:
        return None

    parts = str(text).strip().split(":")
    try:
        numbers = [int(p) for p in parts]
    except ValueError:
        return None

    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
        return hours * 3600 + minutes * 60 + seconds
    return None


def format_time(seconds):
    """450 -> '7:30'. Handles negatives, which pace deltas produce."""
    if seconds is None:
        return "--:--"
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


# Words that appear in icon file names but mean nothing to a player.
#
# "alpha" and "upg" are rendering and revision markers the icon libraries
# leave on some files, and they were being read out loud: builds importing
# @age/FeudalAgeIconDE_alpha.png@ printed "Feudal Age Alpha" on the panel,
# and @barracks/ManAtArmsUpgDE.webp@ printed "Man At Arms Upg".
ICON_NOISE_WORDS = {"de", "aoe2", "aoe2de", "icon", "alpha", "upg"}

# The few names that do not tidy up into anything a player would recognize.
ICON_ALIASES = {
    "male vill": "Villager",
    "female vill": "Villager",
    "vill": "Villager",
    "berry bush": "Berries",
    "towncenter": "Town Center",
}


def icon_to_words(token):
    """Turn an icon path into readable words.

    '@animal/Boar_aoe2DE.webp@'    -> 'Boar'
    '@resource/MaleVillDE.webp@'   -> 'Villager'
    '@age/FeudalAgeIconDE.webp@'   -> 'Feudal Age'

    Real build orders name these files inconsistently: some use underscores,
    some CamelCase, and the '_aoe2DE' game tag appears in several spellings.
    So rather than stripping suffixes one at a time, I break the name into
    words and throw away the ones that carry no meaning.
    """
    name = token.split("/")[-1]
    name = re.sub(r"\.[A-Za-z0-9]+$", "", name)          # drop the file extension
    # Hyphens separate words exactly as underscores do - the libraries use
    # both, sometimes in the same file name. Without this,
    # @unique_unit/ConquistadorIcon-DE.png@ stayed one unsplittable word and
    # the noise filter below could not reach the "Icon" or the "DE" inside
    # it, so the panel read "Conquistadoricon De".
    name = name.replace("_", " ").replace("-", " ")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)  # split CamelCase

    words = [w for w in name.split() if w.lower() not in ICON_NOISE_WORDS]
    cleaned = " ".join(words).strip()

    return ICON_ALIASES.get(cleaned.lower(), cleaned.title())


def strip_icons(text):
    """Replace every @icon@ token in a note with readable words."""
    return ICON_PATTERN.sub(lambda match: icon_to_words(match.group(1)), text)


def parse_segments(text):
    """Split a note into renderable pieces, KEEPING the icon tokens.

    Returns a list of ("text", words) and ("icon", token) segments, where
    token is the path inside the @...@ markers. The overlay draws the icon
    segments as actual pictures - the build file already says exactly which
    image each concept uses, so nothing has to guess what a "villager" looks
    like. A front end with no image for a token falls back to
    icon_to_words(token) and loses nothing but the picture.
    """
    segments = []
    cursor = 0
    for match in ICON_PATTERN.finditer(text):
        before = text[cursor:match.start()]
        if before.strip():
            segments.append(("text", before.strip()))
        segments.append(("icon", match.group(1)))
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        segments.append(("text", tail.strip()))
    return segments


def split_notes(notes):
    """Turn a list of note strings into (details, footnotes).

    Build orders separate the actions of one step with "|", so "Build 2
    Houses | First 6 to Sheep" arrives as two pieces.

    THE SPLIT IS NOT A RANKING, and this function's name says it is. That
    was the original reading - "the main instruction first, extras after" -
    and it is wrong about real builds. Measured across the thirteen shipped
    ones (150 steps, 344 pieces): 38 steps lead with a piece that is not an
    instruction at all but a heading ("Before Feudal Age", "In Castle Age"),
    and 20 of the 58 age-up mentions sit in a LATER piece. So the front ends
    were drawing a section label at 15pt bold and the age-up click - the most
    consequential moment in the build - at 10pt grey underneath it.

    `Step.items` is the honest shape and is what the overlay and the preview
    draw. This stays because report.py, buildcheck.py and the coach ask
    genuinely different questions of a step's text, none of them about size.
    """
    pieces = _note_pieces(notes)
    if not pieces:
        return "", []
    return strip_icons(pieces[0]), [strip_icons(p) for p in pieces[1:]]


def split_note_segments(notes):
    """split_notes, but each piece as parse_segments output.

    Kept in lockstep with split_notes by sharing _note_pieces, so the
    overlay's icon view and the coach's word view always describe the same
    instruction.
    """
    pieces = _note_pieces(notes)
    if not pieces:
        return [], []
    return parse_segments(pieces[0]), [parse_segments(p) for p in pieces[1:]]


def _note_pieces(notes):
    """The raw "|"-separated pieces of a step's notes, tokens intact."""
    pieces = []
    for note in notes or []:
        for piece in note.split("|"):
            if piece.strip():
                pieces.append(piece.strip())
    return pieces


# Build milestones the queue reader can also observe, mapped from the words
# a step uses to the queue identity name. Used by the build-complete report
# to compare "when the build wanted it" with "when it was actually seen".
MILESTONE_WORDS = {
    "loom": "loom",
    "town watch": "town_watch",
    "town patrol": "town_patrol",
    "wheelbarrow": "wheelbarrow",
    "hand cart": "hand_cart",
    "feudal age": "feudal_age",
    "feudal": "feudal_age",
    "castle age": "castle_age",
    "imperial age": "imperial_age",
}


def milestone_targets(build):
    """When the build expects each observable milestone: {identity: seconds}.

    Matches on the step's words (details and footnotes), so it works whether
    the build file wrote "Click Feudal Age" or used an @icon@ token that
    normalised to the same words. Longer phrases match before their prefixes
    ("castle age" before "castle"), and the first step mentioning a
    milestone wins - later mentions are reminders, not the instruction.
    """
    targets = {}
    phrases = sorted(MILESTONE_WORDS, key=len, reverse=True)
    for step in build.steps:
        text = " ".join([step.details] + step.footnotes).lower()
        for phrase in phrases:
            identity = MILESTONE_WORDS[phrase]
            if identity in targets:
                continue
            if phrase in text and step.time is not None:
                targets[identity] = step.time
    return targets


def extra_villagers(build, villagers, game_time, age=None,
                    clicked=None):
    """How many villagers the player has made BEYOND the build's ask.

    This only counts during a HOLD: a stretch where the build repeats the
    same villager count across consecutive steps, which is how build orders
    write "stop training" (a Town Centre cannot train while an age
    researches). Exceeding the count there is the classic slip - an extra
    villager queued right before the age-up click, sliding it 25-40s.

    Outside a hold, a count above the active step's is NOT overproduction:
    a player running fifteen seconds ahead grows the count past the next
    checkpoint's number before its timestamp arrives, and the first version
    of this function flagged exactly that - "+1 VILL" on every slightly
    -ahead build. Ahead is the pace meter's story, not this one's.

    The second rule: more villagers than ANY step of the current age asks
    for. The hold test alone missed a real surplus in a live game - a
    player behind the clock, whose cursor had not reached the hold window
    yet, at 18 villagers in a build whose Dark Age never asks past 17. The
    ahead-of-schedule defense above does not apply there: the steps that
    ask for more are all gated behind an age the crest says has not
    arrived, so no amount of being ahead makes the count legitimate. Only
    with an age reading - without one this would be a guess.
    """
    if villagers is None or game_time is None:
        return 0
    surplus = 0
    active = build.active_step(villagers, game_time, age, clicked)
    completed = build.completed_step(villagers, game_time, age, clicked)
    if (active is not None and completed is not None
            and active.villager_count == completed.villager_count):
        surplus = max(0, villagers - active.villager_count)

    if age is not None:
        asks = [step.villager_count for step in build.steps
                if step.age is not None and step.age <= age]
        if asks:
            surplus = max(surplus, villagers - max(asks))
    return surplus


def held_by_age(build, villagers, game_time, age, clicked=None):
    """Is the age ceiling the ONLY thing holding the cursor back?

    True exactly when villagers and the clock have both moved past steps
    the crest says the player cannot be on yet. That is the moment the
    build is waiting on an age-up and nothing else - which is when a
    "click up" reminder is worth a band and any other time it is noise.

    False when there is no crest reading: with no age the cursor is not
    being held, and a reminder built on a missing reading would be a guess
    wearing a reading's clothes.
    """
    if age is None or villagers is None or game_time is None:
        return False
    return (build.current_index(villagers, game_time)
            > build.current_index(villagers, game_time, age, clicked))


class Step:
    """One line of a build order."""

    def __init__(self, raw):
        self.villager_count = int(raw.get("villager_count", 0))
        self.age = int(raw.get("age", 1))
        self.time = parse_time(raw.get("time"))

        resources = raw.get("resources") or {}
        self.villagers = {name: int(resources.get(name, 0)) for name in RESOURCE_NAMES}

        # Every "|"-separated piece of the notes, as EQUAL items - the shape
        # the front ends draw. See split_notes for why the first piece is not
        # a headline; in a third of the shipped steps it is a section label
        # with the real instructions beneath it.
        #
        # Two views of the same list, kept in lockstep by sharing
        # _note_pieces: `items` is words only (the coach, the report, any
        # front end with no picture library), `items_segments` keeps the
        # @icon@ tokens so the overlay and the preview can draw the game's
        # own artwork inline.
        pieces = _note_pieces(raw.get("notes"))
        self.items = [strip_icons(piece) for piece in pieces]
        self.items_segments = [parse_segments(piece) for piece in pieces]

    # ---- the old view of the same list ----------------------------------
    #
    # Properties rather than stored fields so there is exactly one list and
    # it cannot drift. Everything that still asks in these terms is asking a
    # question that has nothing to do with display size - report.py wants all
    # the words of a step, buildcheck.py wants to know whether a step says
    # anything at all, the coach lays out a terminal.

    @property
    def details(self):
        """The step's first item, or "" for a step with no notes."""
        return self.items[0] if self.items else ""

    @property
    def footnotes(self):
        """Every item after the first."""
        return self.items[1:]

    @property
    def details_segments(self):
        return self.items_segments[0] if self.items_segments else []

    @property
    def footnotes_segments(self):
        return self.items_segments[1:]

    def assigned_villagers(self):
        """How many villagers this step accounts for across all resources."""
        return sum(self.villagers.values())

    def __repr__(self):
        return f"<Step vc={self.villager_count} t={format_time(self.time)} {self.details[:30]!r}>"


class BuildOrder:
    """A whole build order, and the questions Loom asks of it."""

    def __init__(self, data):
        self.name = data.get("name", "Unnamed build")
        self.civilization = data.get("civilization", "Generic")
        self.author = data.get("author", "")
        self.source = data.get("source", "")

        self.steps = [Step(raw) for raw in data.get("build_order", [])]

    # ---- loading -------------------------------------------------------

    @classmethod
    def load(cls, path):
        """Load a build order from a JSON file."""
        with open(path, encoding="utf-8") as handle:
            return cls(json.load(handle))

    @classmethod
    def load_by_name(cls, name):
        """Load builds/<name>.json."""
        found = paths.find_asset("builds", f"{name}.json")
        if found is None:
            # Name the place a build order can be ADDED, not the place Loom
            # keeps its own - somebody looking for this message wants to know
            # where to put a file, and in an installed copy the shipped
            # folder is read-only.
            raise FileNotFoundError(
                f"no build order called {name!r}. Build orders live in "
                f"{paths.DATA_DIR / 'builds'} or beside Loom's own.")
        return cls.load(found)

    # ---- where am I ----------------------------------------------------

    def current_index(self, villager_count, game_time, age=None,
                      clicked=None):
        """Which step is the player on? Returns -1 before the first step.

        villager_count, time and age each only ever increase down the list,
        so "steps reached by villagers", "steps reached by time" and "steps
        reached by age" are each a prefix of the list. The current step is
        wherever the SHORTEST of those prefixes ends.

        That is what makes repeated villager counts work. A Fast Castle sits at
        22 villagers for three separate steps, because a Town Center cannot
        train villagers while researching an age. Villager count alone cannot
        tell those apart; adding the time constraint can.

        `age` is the age read off the crest, or None when there is no
        reading - and None means NO CEILING, because "I did not read it" is
        not "you are not there yet". Where a reading exists it is the
        strongest constraint of the three: a step that says "In Castle Age"
        cannot be the current step while the crest shows Feudal, however
        many villagers exist and however late the clock runs. Measured on a
        real game: one accidental villager (23 against a build that never
        asks past 22) exhausted the villager prefix, the build's ideal step
        times ran ahead of the real age-ups, and the cursor walked to the
        end of the build while the player was still in Feudal. The crest
        read every transition that game; it was just never asked.

        `clicked` is the highest age whose age-up click is proven
        (AgeTracker.clicked_through), or None for no gate. The click-up is
        PART of the last step of its age - "In Feudal: build Market...
        click Castle Age" is one card, and it is not done until the click
        happens. Without this the card vanished the moment its ideal time
        passed: the player reached Feudal, and the panel showed the
        Castle Age card while the market, the blacksmith and the click
        were all still on their hands. A step is only gated when the NEXT
        step's age is higher, so builds and ages without a boundary are
        untouched, and reaching an age proves its click even when the bar
        read was missed - the gate cannot stick.
        """
        reached = -1
        for index, step in enumerate(self.steps):
            if step.villager_count > villager_count:
                break
            if step.time is not None and step.time > game_time:
                break
            if age is not None and step.age > age:
                break
            if clicked is not None and index + 1 < len(self.steps):
                following = self.steps[index + 1]
                if following.age > step.age and clicked < following.age:
                    break
            reached = index
        return reached

    def display_index(self, villager_count, game_time, age=None,
                      clicked=None):
        """Which step should be ON SCREEN. Not the same question.

        current_index is the honest progression through the build, and it
        is what anything judging PROGRESS must use. This is what to show,
        and the age is allowed to move it forward.

        Why it needs to. current_index takes the shorter of two prefixes,
        so a player who is ahead of the build has exhausted the villager
        one and the clock becomes the only thing binding - measured, 22
        villagers and 30 villagers at 10:00 give the identical step. That
        is most extreme when Loom is started mid-match, where the whole
        villager prefix goes at once. The age fixes it as a FLOOR, never a
        third constraint: the answer is already too low, so another ceiling
        would make the very case it exists for worse.

        WHAT THE AGE PROVES IS NARROW, and worth stating exactly because a
        wider claim is tempting and wrong. Being in Castle Age proves the
        player clicked up through Feudal, so the build's earlier steps are
        behind them ON THE BUILD'S TIMELINE. It does not prove they carried
        those steps out: clicking up needs the resources and, for most
        civilizations, two Feudal buildings, and everything else in a build
        order is optional. A player can click up early having skipped
        things or late having done extra.

        That is exactly why this is a separate method rather than an
        argument to current_index. Handing the floored answer to the
        checklist would strike every earlier instruction through as
        assumed-done the moment an age arrived - including ones Loom was
        watching for and never saw. Two questions, two names, and the wrong
        one cannot be passed by accident.

        The CEILING, by contrast, lives in current_index itself and is
        inherited here: not having reached an age is honest progression
        evidence in a way that having reached one is not. The floor and the
        ceiling cannot fight - the floor only raises past steps of ages the
        crest has left BEHIND, which the ceiling never binds on.
        """
        reached = self.current_index(villager_count, game_time, age,
                                     clicked)
        if age is None:
            return reached
        for index, step in enumerate(self.steps):
            if step.age < age:
                reached = max(reached, index)
        return reached

    def completed_step(self, villager_count, game_time, age=None,
                       clicked=None):
        """The last step already finished. Context, not an instruction."""
        index = self.current_index(villager_count, game_time, age, clicked)
        return self.steps[index] if index >= 0 else None

    def active_step(self, villager_count, game_time, age=None,
                    clicked=None):
        """The step to be working on right now. This is what to SHOW.

        Note this is the first step *not yet* completed, which reads oddly
        until you see how build orders are written. A step labelled
        "villager_count: 10, Next 4 Villagers to Wood" describes what you do
        while going FROM the previous count TO ten - so it becomes actionable
        the moment you pass the previous step, not when you reach ten.

        Showing the last *completed* step instead puts the player permanently
        one instruction behind their own hands. That was the first version of
        the overlay, and it felt laggy for exactly that reason.
        """
        index = self.current_index(villager_count, game_time, age,
                                   clicked) + 1
        return self.steps[index] if index < len(self.steps) else None

    def following_step(self, villager_count, game_time, age=None,
                       clicked=None):
        """The step after the active one, so the player can read ahead."""
        index = self.current_index(villager_count, game_time, age,
                                   clicked) + 2
        return self.steps[index] if index < len(self.steps) else None

    # ---- the same three, from an index somebody else worked out ---------
    #
    # These exist because the step shown is no longer always the step the
    # reading implies: a player can nudge it with a hotkey, and loom/follow.py
    # owns that decision. Taking the index as an argument keeps that decision
    # OUT of here - this module still knows nothing but the build order, and
    # holds no state, which is what makes it testable with fake numbers.

    def step_at(self, index):
        """The step at an index, or None outside the build.

        Indices are in current_index() semantics: -1 means "before the first
        step", which is a real position rather than an error - it is where a
        match starts.
        """
        if index is None or index < 0 or index >= len(self.steps):
            return None
        return self.steps[index]

    def active_step_at(self, index):
        """The step to work on, given the last completed one. See active_step."""
        return self.step_at(None if index is None else index + 1)

    def following_step_at(self, index):
        """The step after the active one, given the last completed one."""
        return self.step_at(None if index is None else index + 2)

    # ---- am I on pace --------------------------------------------------

    def target_time(self, villager_count):
        """When does the build expect this many villagers? None if unknown.

        Deliberately NOT the current step's time: the current step is always
        one the player has already reached, so measuring against it could only
        ever say "behind" and never "ahead".

        Community build orders group villagers, so there may be a step at 11
        and the next at 13 with nothing for 12. I interpolate between the two
        bracketing steps rather than refusing to answer.
        """
        timed = [s for s in self.steps if s.time is not None]
        if not timed:
            return None

        # Before the build's first checkpoint there is nothing to compare
        # against, so I say so rather than inventing a number. Every count
        # below the first step would otherwise map to the same time and give
        # a meaningless answer.
        if villager_count < timed[0].villager_count:
            return None
        if villager_count == timed[0].villager_count:
            return timed[0].time

        for earlier, later in zip(timed, timed[1:]):
            if earlier.villager_count < villager_count <= later.villager_count:
                span = later.villager_count - earlier.villager_count
                if span <= 0:
                    return later.time
                fraction = (villager_count - earlier.villager_count) / span
                return earlier.time + fraction * (later.time - earlier.time)

        return timed[-1].time

    def expected_villagers(self, game_time):
        """How many villagers the build expects by now. None if unknown.

        The inverse of target_time. Used to judge pace, and to simulate a
        player who is following the build exactly.
        """
        timed = [s for s in self.steps if s.time is not None]
        if not timed:
            return None

        if game_time <= timed[0].time:
            return timed[0].villager_count

        for earlier, later in zip(timed, timed[1:]):
            if earlier.time < game_time <= later.time:
                span = later.time - earlier.time
                if span <= 0:
                    return later.villager_count
                fraction = (game_time - earlier.time) / span
                gained = later.villager_count - earlier.villager_count
                return earlier.villager_count + fraction * gained

        return timed[-1].villager_count

    # ---- checking a build file ----------------------------------------

    def validate(self):
        """Return a list of human-readable problems. Empty means it looks fine.

        These are warnings rather than errors: a real build order can
        legitimately break the villager-sum rule while villagers are away
        constructing something. I would rather explain what looks odd than
        refuse to load somebody's file.
        """
        problems = []

        if not self.steps:
            problems.append("build has no steps")
            return problems

        previous_time = None
        previous_count = None

        for number, step in enumerate(self.steps, start=1):
            if step.time is None:
                problems.append(f"step {number}: missing or unreadable time")
            elif previous_time is not None and step.time < previous_time:
                problems.append(
                    f"step {number}: time {format_time(step.time)} goes backwards"
                )
            else:
                previous_time = step.time

            if previous_count is not None and step.villager_count < previous_count:
                problems.append(
                    f"step {number}: villager count drops from "
                    f"{previous_count} to {step.villager_count}"
                )
            previous_count = step.villager_count

            assigned = step.assigned_villagers()
            if assigned != step.villager_count:
                problems.append(
                    f"step {number}: villagers on resources add up to {assigned} "
                    f"but villager_count is {step.villager_count} "
                    f"(fine if some are away building)"
                )

            if not step.details:
                problems.append(f"step {number}: no instructions")

        return problems


def available_builds():
    """Every build order in builds/, loaded and ready to describe.

    Returns (builds, problems): builds is a sorted list of (stem, BuildOrder)
    pairs, where the stem is what --build and load_by_name expect; problems
    is a list of human-readable strings for files that would not load. One
    corrupt download must not hide the rest of the library, so each file
    gets its own try - the launcher lists what it can and reports the rest.
    """
    builds = []
    problems = []
    for path in sorted(paths.asset_files("builds", "*.json").values()):
        try:
            builds.append((path.stem, BuildOrder.load(path)))
        except (OSError, json.JSONDecodeError, AttributeError, KeyError,
                TypeError, ValueError) as error:
            # Not just JSON errors: a file holding valid JSON of the wrong
            # shape (a list, a string) fails inside BuildOrder instead.
            problems.append(f"{path.name}: {error}")
    return builds, problems


# ---------------------------------------------------------------------------
# Finding one build in a growing library
# ---------------------------------------------------------------------------
#
# Importing a build takes seconds now, so the library grows, and a flat list
# ordered by filename stops answering the question a player actually has:
# "what can I play as Mongols?". These are the pure half of that - the
# launcher owns the widgets, this owns the rules, and so the rules can be
# tested without a display.

# Builds written for no particular civilization. The format's own default,
# and six of the thirteen builds shipped the day this was written.
GENERIC_CIVILIZATION = "Generic"


def civilization_names(build):
    """Every civilization a build claims, always as a tuple.

    The field is a plain string in every build I have seen, but the format
    allows a list, and a list would otherwise render as "['Mayans',
    'Aztecs']" in the picker and match nothing a player typed. Absorbing
    that here costs three lines and keeps it out of everything downstream.
    """
    value = build.civilization
    if isinstance(value, str):
        return (value,) if value else (GENERIC_CIVILIZATION,)
    if isinstance(value, (list, tuple)):
        names = tuple(str(name) for name in value if str(name).strip())
        return names or (GENERIC_CIVILIZATION,)
    return (GENERIC_CIVILIZATION,)


def civilization_label(build):
    """The civilizations as one readable string, for a row or a dialog."""
    return "/".join(civilization_names(build))


def civilizations(pairs):
    """Every civilization present in a library, for a filter's drop-down.

    Derived from the builds rather than from a list of the game's civs:
    a hard-coded list would offer forty civilizations with nothing behind
    thirty-nine of them, and would age every time the game adds one.

    Generic leads, because it is the one entry that is not a civilization -
    it is the builds that work for all of them.
    """
    found = set()
    for _stem, build in pairs:
        found.update(civilization_names(build))
    generic = [GENERIC_CIVILIZATION] if GENERIC_CIVILIZATION in found else []
    rest = sorted(name for name in found if name != GENERIC_CIVILIZATION)
    return generic + rest


def _haystack(stem, build):
    """Everything about a build that a typed word may match.

    The stem is in there because it is the file the player saved and may be
    the only name they remember - "malay_fast_elephants" finds it even
    though the build calls itself something else inside.
    """
    return " ".join((build.name, build.author or "", stem,
                     *civilization_names(build))).lower()


def matches_civilization(build, civilization):
    """Would a player of this civilization use this build?

    A specific civilization matches its own builds AND the Generic ones,
    because a Generic build is by definition playable as that civ - and
    hiding them would hide most of any real library. Asking for Generic
    itself is the narrow question, and answers narrowly.
    """
    if not civilization:
        return True
    names = civilization_names(build)
    if civilization in names:
        return True
    return (civilization != GENERIC_CIVILIZATION
            and GENERIC_CIVILIZATION in names)


def filtered_builds(pairs, query="", civilization=None, keep=None):
    """The (stem, build) pairs a filtered picker should show, in order.

    Every word of the query has to match somewhere, so "hera arena" narrows
    rather than widens.

    `keep` is a stem that stays in the result whatever the filter says. That
    is not a convenience: it is the picker's current choice, and a filter
    that could drop it would let the drop-down silently move to a different
    build, so Start would run something the player never picked. Same class
    of failure as a panel that quietly stops following the game.
    """
    words = query.lower().split()
    shown = []
    for stem, build in pairs:
        if stem == keep:
            shown.append((stem, build))
            continue
        if not matches_civilization(build, civilization):
            continue
        haystack = _haystack(stem, build)
        if all(word in haystack for word in words):
            shown.append((stem, build))
    return shown
