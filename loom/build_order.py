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

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

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


def split_notes(notes):
    """Turn a list of note strings into (details, footnotes).

    Build orders put the main instruction first and separate extra actions
    with "|", so "Build 2 Houses | First 6 to Sheep" becomes a headline plus
    one footnote. That gives the overlay something short to show large and the
    rest to show small, without needing an extra field in the file.
    """
    pieces = []
    for note in notes or []:
        for piece in strip_icons(note).split("|"):
            piece = piece.strip()
            if piece:
                pieces.append(piece)

    if not pieces:
        return "", []
    return pieces[0], pieces[1:]


class Step:
    """One line of a build order."""

    def __init__(self, raw):
        self.villager_count = int(raw.get("villager_count", 0))
        self.age = int(raw.get("age", 1))
        self.time = parse_time(raw.get("time"))

        resources = raw.get("resources") or {}
        self.villagers = {name: int(resources.get(name, 0)) for name in RESOURCE_NAMES}

        self.details, self.footnotes = split_notes(raw.get("notes"))

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
        return cls.load(paths.BUILDS_DIR / f"{name}.json")

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
