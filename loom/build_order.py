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
ICON_NOISE_WORDS = {"de", "aoe2", "aoe2de", "icon"}

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
    name = name.replace("_", " ")
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

    Build orders put the main instruction first and separate extra actions
    with "|", so "Build 2 Houses | First 6 to Sheep" becomes a headline plus
    one footnote. That gives the overlay something short to show large and the
    rest to show small, without needing an extra field in the file.
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


def extra_villagers(build, villagers, game_time):
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
    """
    if villagers is None or game_time is None:
        return 0
    active = build.active_step(villagers, game_time)
    completed = build.completed_step(villagers, game_time)
    if active is None or completed is None:
        return 0
    if active.villager_count != completed.villager_count:
        return 0
    return max(0, villagers - active.villager_count)


class Step:
    """One line of a build order."""

    def __init__(self, raw):
        self.villager_count = int(raw.get("villager_count", 0))
        self.age = int(raw.get("age", 1))
        self.time = parse_time(raw.get("time"))

        resources = raw.get("resources") or {}
        self.villagers = {name: int(resources.get(name, 0)) for name in RESOURCE_NAMES}

        self.details, self.footnotes = split_notes(raw.get("notes"))
        # The same lines with their @icon@ tokens preserved, for front ends
        # that can draw pictures. details/footnotes stay the words-only view.
        self.details_segments, self.footnotes_segments = \
            split_note_segments(raw.get("notes"))

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

    def current_index(self, villager_count, game_time):
        """Which step is the player on? Returns -1 before the first step.

        Both villager_count and time only ever increase down the list, so
        "steps reached by villagers" and "steps reached by time" are each a
        prefix of the list. The current step is wherever the SHORTER of those
        two prefixes ends.

        That is what makes repeated villager counts work. A Fast Castle sits at
        22 villagers for three separate steps, because a Town Center cannot
        train villagers while researching an age. Villager count alone cannot
        tell those apart; adding the time constraint can.
        """
        reached = -1
        for index, step in enumerate(self.steps):
            if step.villager_count > villager_count:
                break
            if step.time is not None and step.time > game_time:
                break
            reached = index
        return reached

    def completed_step(self, villager_count, game_time):
        """The last step already finished. Context, not an instruction."""
        index = self.current_index(villager_count, game_time)
        return self.steps[index] if index >= 0 else None

    def active_step(self, villager_count, game_time):
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
        index = self.current_index(villager_count, game_time) + 1
        return self.steps[index] if index < len(self.steps) else None

    def following_step(self, villager_count, game_time):
        """The step after the active one, so the player can read ahead."""
        index = self.current_index(villager_count, game_time) + 2
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
