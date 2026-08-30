"""
Loom — read every band of a saved frame, the way the live reader does.

Both tools/read_rates.py and tools/reader_report.py want the same thing: a
frame off disk, run through each reader exactly as loom/reader.py runs it,
with the answers kept apart band by band. That was one copy of the loop and
is about to be two, which is the failure this project has met more times
than any other - one question answered in two places, and the stricter
answer winning in silence. So it lives here, once.

The shape follows loom/reader.py rather than being invented: templates load
once into a Kit and are shared across every run; the things that are located
ONCE at HUD acquisition and then reused - the resource number regions, the
queue reader's own state - belong to a RunReader that lives for one run.
Getting that wrong is not just slow. The queue reader caches an identity per
slot and re-verifies it against the previous poll, so a fresh reader per
frame would measure a reader nobody runs.

What is deliberately NOT cached is the anchor. loom/reader.py finds the HUD
once and keeps it; here every frame is identified afresh, because "how often
was the HUD found at all" is one of the numbers being measured and a cached
answer would report 100% by construction.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import age as age_reader  # noqa: E402
from loom import anchor, digits, hud, queue, resources  # noqa: E402
from loom import reader as hud_reader  # noqa: E402


class Kit:
    """Every template the bands need, loaded once and shared across runs.

    Loading these per run is the difference between a measurement that
    finishes and one that is abandoned: the queue set alone is 527 images.
    """

    def __init__(self, crests=True):
        self.anchors = {p: anchor.load_template(p) for p in hud.PROFILES}
        self.woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
        self.glyphs = digits.load_digit_templates()
        self.resources = {p: resources.load_resource_templates(p)
                          for p in hud.PROFILES}
        # Empty when none are harvested, which reads as "no age" rather than
        # as an error - the same contract the notification font has.
        self.crests = age_reader.load_crests() if crests else {}


class BandReading:
    """What one frame gave up, band by band.

    Every field is None when that band did not read, and None means "I did
    not read it" - never "it is not there". Nothing here may be used as
    evidence of absence; that distinction has cost this project two phantom
    Town Centres by two different routes.
    """

    __slots__ = ("anchored", "skin", "scale", "clock", "villagers",
                 "population", "pop_cap", "age", "per_resource", "slots")

    def __init__(self):
        self.anchored = False
        self.skin = None
        self.scale = None
        self.clock = None            # game seconds
        self.villagers = None
        self.population = None
        self.pop_cap = None
        self.age = None              # an age.AgeReading
        self.per_resource = {}
        self.slots = None            # queue SlotReadings, or None


class RunReader:
    """One run's worth of reading. Construct it once, feed it frames.

    The optional bands are off by default because they are not free and not
    every caller reports them: the queue is the most expensive thing Loom
    does per poll, and locating the resource regions needs a frame in hand.
    """

    def __init__(self, kit, want_queue=False, want_age=False,
                 want_resources=False):
        self.kit = kit
        self.want_queue = want_queue
        self.want_age = want_age
        self.want_resources = want_resources
        self._queue = None
        self._resource_regions = None
        self._profile = None
        self._near_scale = None
        # How many times the full two-profile search had to run. One is the
        # opening acquisition; more than that means the HUD was lost and
        # re-found, which is worth knowing on its own.
        self.acquisitions = 0

    def _find_hud(self, image):
        """The HUD in this frame, or None. Re-anchored, then searched.

        The full anchor sweep is by far the most expensive thing here - 155ms
        at 1440p, measured, against 55ms for the whole queue reader - and
        loom/reader.py does not pay it per poll. It re-anchors with a scale
        hint, because the HUD does not resize mid-match, and that turns a
        full sweep into a nine-step one.

        Doing the same is not a shortcut, it is what Loom actually does. But
        a narrowed search that comes back empty must fall back to the full
        one IN THE SAME FRAME: otherwise a HUD that moved, or one frame of a
        menu, would poison every frame after it and "how often was the HUD
        found" - the number this is here to measure - would be understated
        by the optimisation meant to speed it up.
        """
        settled = self._profile
        if settled is not None and self._near_scale is not None:
            # One skin, one scale hint - the poll loom/reader.py actually
            # runs once the HUD is acquired. identify_hud otherwise searches
            # EVERY profile every frame, which is a thing Loom never does.
            found = anchor.identify_hud(
                image, {settled: self.kit.anchors[settled]},
                near_scale=self._near_scale,
                wood_templates={settled: self.kit.woods[settled]})
            if found and found["score"] >= hud_reader.MIN_ANCHOR_SCORE:
                return found
        found = anchor.identify_hud(image, self.kit.anchors,
                                    wood_templates=self.kit.woods)
        if not found or found["score"] < hud_reader.MIN_ANCHOR_SCORE:
            self._near_scale = None
            return None
        self.acquisitions += 1
        self._near_scale = found["scale"]
        return found

    def read(self, image):
        """Read every wanted band of one frame."""
        out = BandReading()
        found = self._find_hud(image)
        if not found:
            return out

        out.anchored = True
        profile, scale = found["profile"], found["scale"]
        out.skin, out.scale = profile.name, scale
        narrow = hud_reader.min_glyph_width(scale, profile)
        wide = hud_reader.max_glyph_width(scale, profile)

        def band(key):
            x1, y1, x2, y2 = found[key]
            return image[max(0, y1):y2, max(0, x1):x2]

        out.clock, _ = digits.read_clock_seconds(band("clock_band"),
                                                 self.kit.glyphs, narrow)
        out.villagers, _ = digits.read_count(band("villagers"),
                                             self.kit.glyphs, narrow)
        pop = digits.read_population(band("population"), self.kit.glyphs,
                                     narrow, wide)
        if pop:
            out.population, out.pop_cap = pop[0], pop[1]

        if self.want_age and self.kit.crests:
            out.age = age_reader.read(band("age"), band("age_bar"),
                                      self.kit.crests, scale)

        if self.want_resources:
            # Located once, like loom/reader.py does at HUD acquisition:
            # the icons do not move within a run, and re-locating them per
            # frame would cost more than every digit read put together.
            if self._resource_regions is None or profile is not self._profile:
                self._resource_regions = resources.locate_regions(
                    image, self.kit.resources[profile], scale, profile)
            for name, region in (self._resource_regions or {}).items():
                x1, y1, x2, y2 = region
                count = resources.read_one(
                    image[max(0, y1):y2, max(0, x1):x2],
                    self.kit.glyphs, narrow)
                if count is not None:
                    out.per_resource[name] = count

        if self.want_queue:
            # One reader for the whole run. It caches an identity per slot
            # and re-verifies it against the previous poll, so a fresh one
            # per frame would be measuring a reader nobody runs.
            if self._queue is None:
                self._queue = queue.QueueReader(profile)
            elif profile is not self._profile:
                self._queue.use_profile(profile)
            out.slots = self._queue.read(image, scale)

        self._profile = profile
        return out
