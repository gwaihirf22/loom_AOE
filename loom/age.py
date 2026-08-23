"""
Loom — which age the player is in, and whether they are advancing to the next.

The HUD carries the answer twice, and the two disagree in a useful way. Next
to the resource bar sits a CREST with a roman numeral, and beside it the age's
NAME in words. Measured across both HUD skins, both resolutions, live games
and replays:

    crest   name            meaning
    II      Feudal Age      in Feudal, not advancing
    II      Castle Age      CLICKED UP, advancing to Castle
    III     Castle Age      arrived

The crest is the age you are IN; the name is the age you are advancing TO,
and behind the name a red bar fills as the research runs. So the click and
the completion are both directly readable - no notification to wait for, no
inference from the production queue, and no timing.

WHY THIS READS THE CREST AND NOT THE WORDS. The name is text, and this game
re-lays text out by resolution rather than scaling one master: measured on
one game captured twice, the age name is 1.45x larger at 2560x1440 where the
frame is only 1.33x larger. A template cut at one resolution and resized to
another scores 0.82 correlation and 0.64 ink overlap against the real thing -
it would probably pass, with no margin at all, which is exactly the shape of
the bug that once minted two phantom Town Centres at 0.598 against a 0.600
gate. Reading words would need a harvest per rendering, forever, including
any future 4K one.

The crest is ARTWORK, and artwork does scale - the queue's several hundred
icons already resize across resolutions and work. So this reads the crest,
takes "advancing" from the red bar, and never needs the words: the age being
advanced to is always the current one plus one. Nothing here needs a capture
at a resolution it has not seen.

AND THE CREST IS SHARED BETWEEN SKINS, which was worth checking rather than
assuming, because the FRAME around it is not - Anne_HK draws a grey metal
shield where stock draws a gold one, and the bar behind them differs per
civilization as well as per skin. Cut tight to the crest and one template set
serves both: measured, the Imperial crest scores 1.000 against itself,
0.857 against the same age on the other skin, 0.960 at the other resolution,
and 0.30 against a different age. Four templates, not eight.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np

from . import paths

# The ages, as the build-order format numbers them (Step.age uses these).
DARK, FEUDAL, CASTLE, IMPERIAL = 1, 2, 3, 4
NAMES = {DARK: "Dark Age", FEUDAL: "Feudal Age",
         CASTLE: "Castle Age", IMPERIAL: "Imperial Age"}
FILE_NAMES = {"dark_age": DARK, "feudal_age": FEUDAL,
              "castle_age": CASTLE, "imperial_age": IMPERIAL}

# Below this correlation the crest is not believed and the answer is "no
# reading", as everywhere else in Loom.
#
# Re-measured over 224 frames from six games covering both skins, both
# resolutions and a replay, which moved it: the first guess of 0.6 came
# from a handful of frames and was too loose. Across the corpus the RIGHT
# crest never scores below 0.857 and the best WRONG one reaches 0.719 - so
# 0.6 would have let a wrong crest through on its own, with only the
# margin below standing in the way. 0.78 sits in the gap between them.
#
# Mistaking one age for another is expensive twice over: the age is a floor
# on which step is shown, so a wrong one jumps the panel forward, and it
# takes two more agreeing looks to come back.
MIN_CREST_SCORE = 0.78

# How much better the winner must be than the runner-up. The four crests
# share a frame and a background, so a low absolute score with two ages
# close together means the band is showing something else entirely - a
# fade, a menu, a mid-transition redraw - and the honest answer is none.
#
# Measured on the same corpus: the tightest real frame had a margin of
# 0.163 and the median was 0.400, so this has better than a factor of two
# in hand at its worst.
MIN_CREST_MARGIN = 0.08

# The scales tried around the anchor's measurement. The crest is artwork
# and resizes cleanly, but the anchor measures the POPULATION icon and the
# two need not track each other to the last percent - the notification
# templates need the same bracket for the same reason.
SCALE_BRACKET = (-0.02, 0.0, 0.02)


def load_crests(directory=None):
    """{age: [(image, harvest_scale), ...]} from templates/age/.

    Named "<age>@<scale>.png", the same scheme the notification phrases
    use, so a second cut can be added if some future rendering ever needs
    one without disturbing the first. A bare name means 1.0.
    """
    directory = directory or (paths.TEMPLATES_DIR / "age")
    crests = {}
    for path in sorted(glob.glob(str(directory / "*.png"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        name, _, harvested_at = stem.partition("@")
        age = FILE_NAMES.get(name)
        if age is None:
            continue
        try:
            harvest_scale = float(harvested_at) if harvested_at else 1.0
        except ValueError:
            continue                    # a scale that is not a number is
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)   # not a guess to make
        if image is not None:
            crests.setdefault(age, []).append((image, harvest_scale))
    return crests


def read_age(band_bgr, crests, scale):
    """Which age's crest is in this band: (age, score), or (None, 0.0).

    `band_bgr` is the profile's age_band crop and `scale` the anchor's
    measured HUD scale. Searches rather than compares at a fixed offset:
    the crest sits a few pixels differently in each skin's frame, and a
    band roomy enough to hold both means the offsets need not be measured
    per skin at all.
    """
    if band_bgr is None or band_bgr.size == 0 or not crests:
        return None, 0.0
    band = cv2.cvtColor(band_bgr, cv2.COLOR_BGR2GRAY)

    best = {}
    for age, variants in crests.items():
        for image, harvest_scale in variants:
            for delta in SCALE_BRACKET:
                factor = (scale + delta) / harvest_scale
                sized = _sized(image, factor)
                if (sized.shape[0] > band.shape[0]
                        or sized.shape[1] > band.shape[1]):
                    continue
                score = float(cv2.matchTemplate(
                    band, sized, cv2.TM_CCOEFF_NORMED).max())
                if score > best.get(age, -1.0):
                    best[age] = score
    if not best:
        return None, 0.0

    ranked = sorted(best.items(), key=lambda pair: -pair[1])
    age, score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else -1.0
    if score < MIN_CREST_SCORE or score - runner_up < MIN_CREST_MARGIN:
        return None, score
    return age, score


_sized_cache = {}


def _sized(image, factor):
    """A crest resized once per factor, not once per poll."""
    key = (id(image), round(factor, 3))
    cached = _sized_cache.get(key)
    if cached is None:
        if abs(factor - 1.0) < 0.005:
            cached = image
        else:
            # INTER_AREA shrinks well and grows badly, degenerating toward
            # nearest neighbour; INTER_CUBIC is the other way round.
            cached = cv2.resize(
                image, None, fx=factor, fy=factor,
                interpolation=(cv2.INTER_AREA if factor < 1.0
                               else cv2.INTER_CUBIC))
        _sized_cache[key] = cached
    return cached


# The red bar that fills behind the age's name while the research runs. A
# COLOUR, not a shape, so it needs no template and no per-rendering cut.
#
# Measured on the bar the game draws: it is a saturated red on a background
# that is either near-black stone (stock) or dark wood (the mod), and the
# age name over it is white or gold. So "red" is the discriminator, and the
# fraction of the band that is red is what separates advancing from not.
RED_MIN = 90            # the red channel of the bar, at its dimmest
RED_DOMINANCE = 45      # ...and how far it must lead the other two
MIN_RED_FRACTION = 0.12


def advancing(band_bgr):
    """Is the age-up progress bar showing? True, False, or None if unread.

    None rather than False when there is nothing to look at, because "I did
    not see the bar" is not "the bar is not there" - the rule this project
    keeps relearning. A caller that wants a boolean has to decide what an
    unknown means for it.
    """
    if band_bgr is None or band_bgr.size == 0:
        return None
    blue, green, red = (band_bgr[:, :, 0].astype(int),
                        band_bgr[:, :, 1].astype(int),
                        band_bgr[:, :, 2].astype(int))
    bar = ((red >= RED_MIN)
           & (red - green >= RED_DOMINANCE)
           & (red - blue >= RED_DOMINANCE))
    return bool(bar.mean() >= MIN_RED_FRACTION)


def progress(band_bgr):
    """How far the bar has filled, 0.0-1.0, or None when it is not showing.

    The bar fills left to right, so the fraction of COLUMNS carrying red is
    the honest measure - counting pixels would have the age name's own
    letters eat into it.
    """
    if band_bgr is None or band_bgr.size == 0:
        return None
    blue, green, red = (band_bgr[:, :, 0].astype(int),
                        band_bgr[:, :, 1].astype(int),
                        band_bgr[:, :, 2].astype(int))
    bar = ((red >= RED_MIN)
           & (red - green >= RED_DOMINANCE)
           & (red - blue >= RED_DOMINANCE))
    if bar.mean() < MIN_RED_FRACTION:
        return None
    columns = bar.any(axis=0)
    return float(np.count_nonzero(columns)) / max(1, len(columns))


class AgeReading:
    """What the HUD says about the age, for one poll.

    `age` is the age the crest shows - the one you are IN - or None when it
    could not be read. `advancing` says the progress bar is up, so the click
    has happened; `target` is what it is advancing to, which is always the
    next age and needs nothing read to know.
    """

    __slots__ = ("age", "score", "advancing", "progress")

    def __init__(self, age=None, score=0.0, advancing=None, progress=None):
        self.age = age
        self.score = score
        self.advancing = advancing
        self.progress = progress

    @property
    def target(self):
        """The age being advanced to, or None when not advancing."""
        if not self.advancing or self.age is None or self.age >= IMPERIAL:
            return None
        return self.age + 1

    def __repr__(self):
        state = NAMES.get(self.age, "unread")
        if self.advancing and self.target:
            share = "" if self.progress is None else f" {self.progress:.0%}"
            state += f" -> {NAMES[self.target]}{share}"
        return f"<AgeReading {state}>"


def read(crest_band_bgr, bar_band_bgr, crests, scale):
    """One poll's age reading from the two bands."""
    age, score = read_age(crest_band_bgr, crests, scale)
    return AgeReading(age, score,
                      advancing(bar_band_bgr), progress(bar_band_bgr))


# The events an age tracker reports, and what they mean.
CLICKED = "clicked"     # the research started: the bar appeared
REACHED = "reached"     # it finished: the crest changed

# The production queue's names for the age-up researches, as the ages they
# advance to. The queue is the SECOND witness to a click: the banner's red
# bar fills left to right, so for the first stretch of a 130-second
# research the red fraction sits under its threshold and the bar reader
# honestly answers "not advancing" - while the research is sitting right
# there in the queue. Measured live: the CLICK UP band kept flashing after
# the click, for exactly as long as the fill needed to reach the gate.
QUEUE_IDENTITIES = {"feudal_age": FEUDAL, "castle_age": CASTLE,
                    "imperial_age": IMPERIAL}

# How many agreeing looks before a change is believed. The same rule the
# production tracker uses and for the same reason: a reading is one glance,
# and one glance can be wrong - a fade, a menu, a mid-transition redraw. Two
# is enough because nothing can get stuck: the belief is always two agreeing
# looks away from the truth.
LOOKS_TO_BELIEVE = 2


class AgeTracker:
    """Turns a stream of age readings into "clicked" and "reached" events.

    Pure state and arithmetic - it never touches the screen - so it tests
    with made-up readings, like session.py and production.py.

    WHY THIS EXISTS when the production queue already sees the age-up: the
    queue sees the CLICK, because the research sits in it, and that has
    worked for months. What it cannot see is the finish. A queue item that
    disappears has either completed or been cancelled, and those look
    identical; the notification feed announces the completion but Loom reads
    somewhere between a quarter and three-quarters of its lines, and in a
    real 43-minute game it caught one age-up of two. The crest changing is
    neither inferred nor announced - it is the game's own statement of what
    age you are in, and it is on screen for the rest of the match.
    """

    def __init__(self, looks_to_believe=LOOKS_TO_BELIEVE):
        self.looks_to_believe = looks_to_believe
        self.age = None             # believed current age
        self.advancing = False      # believed to be researching an age-up
        self.clicked_at = {}        # age -> game time the bar appeared
        self.reached_at = {}        # age -> game time the crest changed
        self._streaks = {}

    def update(self, reading, game_time, queued_target=None):
        """One poll. Returns a list of (event, age) pairs, usually empty.

        `queued_target` is the age the production queue shows researching
        this poll (FEUDAL/CASTLE/IMPERIAL), or None when it shows no
        age-up. The queue and the banner's red bar are two witnesses to
        the same click, and THIS is where they meet - everything
        downstream (the CLICK UP band above all) reads the reconciled
        belief, so no two parts of Loom can disagree about whether an
        age-up is running.

        The reconciliation is asymmetric, and the asymmetry is the whole
        point. The queue SHOWING an age-up is positive evidence - the
        research is sitting in the game's own production list - and it
        outranks the bar, whose red-fraction gate is honestly blind for
        the first stretch of a research (the fill has not reached it
        yet). The queue showing NOTHING is not evidence of absence: the
        queue reader misses slots, and "I did not read it" is not "it is
        not there". So an empty queue must leave the bar's answer -
        True, False or None - to stand alone, and both witnesses silent
        must stay None, no news. The two-look debounce below guards the
        queue's occasional junk identity the same way it guards a
        misread crest.
        """
        if reading is None or game_time is None:
            return []
        events = []
        events += self._settle("age", reading.age, game_time)
        advancing_seen = (True if queued_target is not None
                          else reading.advancing)
        events += self._settle("advancing", advancing_seen, game_time)
        return events

    def _settle(self, what, value, game_time):
        """Believe a value once it has repeated, then report the change."""
        if value is None:
            return []                      # not read: no news, not a change
        current = getattr(self, what)
        if value == current:
            self._streaks.pop(what, None)
            return []
        streak = self._streaks.get(what, (value, 0))
        streak = (value, streak[1] + 1) if streak[0] == value else (value, 1)
        self._streaks[what] = streak
        if streak[1] < self.looks_to_believe:
            return []
        self._streaks.pop(what, None)
        setattr(self, what, value)

        if what == "advancing":
            # The bar appearing is the click. The bar going away is not an
            # event of its own - either the crest changed (which reports
            # itself) or the research was cancelled, and those look the
            # same from here, so nothing is claimed about it.
            if value and self.age is not None and self.age < IMPERIAL:
                target = self.age + 1
                self.clicked_at.setdefault(target, game_time)
                return [(CLICKED, target)]
            return []

        # The crest changed. Going UP is an age reached; anything else is a
        # new match starting, which is the caller's business, not an age-up.
        if current is not None and value == current + 1:
            self.reached_at.setdefault(value, game_time)
            return [(REACHED, value)]
        return []

    @property
    def clicked_through(self):
        """The highest age whose click is PROVEN, or None before any
        reading.

        Proven three ways, any one sufficient: the bar was seen for it
        (clicked_at), the crest reached it (arriving proves the click even
        when the bar read was missed - nothing may stick on a missed
        reading), or the crest simply shows it (a mid-match start in
        Castle Age proves every click below). The current age itself
        counts as clicked-through: being IN an age means nothing below it
        is still waiting for a click.
        """
        proven = list(self.clicked_at) + list(self.reached_at)
        if self.age is not None:
            proven.append(self.age)
        return max(proven) if proven else None

    def reset(self):
        """Forget everything. Call when a new match starts."""
        self.__init__(self.looks_to_believe)

