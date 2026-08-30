"""
Loom — the queue's per-poll readings, decoded once and kept.

    python -m tools.pollcache            # harvest every paired run
    python -m tools.pollcache --run NAME # one run

WHY. Reading the queue out of a capture run costs a PNG decode per frame -
about 160ms at 1440p - and the corpus is roughly 30,000 frames. Every
question asked of it therefore costs forty minutes of the author's machine:
what threshold should the vote need, does position or progress lead, what
happens if the matcher allows two slots instead of one. Those are arithmetic
over readings, not new readings, and paying for the decode again to ask each
one is the tool's fault rather than the question's.

Fifteen full-corpus passes in a day, of which about a third were spent
re-deriving data that had not changed. tools/reader_report.py already
solved this for itself - one cached row per run, so re-reporting takes half
a second - and this is the same idea for the thing the queue work actually
needs, which is the readings themselves rather than a summary of them.

WHAT IS STORED. One row per poll: the game clock, and every occupied slot's
index, tint, progress, count, identity and score. That is exactly what
queue.QueueReader.read returns, so a consumer reconstitutes real
SlotReadings and runs against the same objects the live code would.

STAMPED WITH WHAT PRODUCED IT. The cache carries a hash of the reading code
and the template set, and a row stamped differently is re-harvested rather
than reused. Without that a rule gets scored against readings from a reader
that no longer exists - which is the failure reader_sweep.py was written
for, and which cost this project a wrong answer as recently as yesterday
when a clock-jump count of 19 survived the fix that took it to zero.

The stamp here is NARROWER than reader_report's on purpose. What determines
a slot reading is loom/ and the templates; reader_report also hashes its own
source because it measures things its own code computes. Two questions, two
answers, and neither is the other's.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import hashlib
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, notifications, paths, queue  # noqa: E402
from tools import framebands, queue_report  # noqa: E402

CACHE = paths.CAPTURES_DIR / "queue_polls"

# The modules whose code can change a SLOT READING. Deliberately NOT all of
# loom/: the whole point of this cache is that a rule can be tuned without
# re-decoding thirty thousand frames, and hashing every module would mean
# editing episodes.py threw away the decodes the cache exists to keep.
#
# A narrow list is the thing CLAUDE.md warns about - a hand-curated list
# fails silently - so tests/test_pollcache.py walks framebands' own imports
# and fails if one of them is missing here. The list is checked, not
# trusted.
READING_MODULES = ("anchor", "digits", "hud", "queue", "reader", "resources",
                   "age")

_fingerprint = None


def reading_fingerprint():
    """What code and what pictures produced these readings.

    loom/ and the templates, and nothing else: those are what a slot
    reading depends on. A tool changing its own reporting must not throw
    away thirty thousand decodes.
    """
    global _fingerprint
    if _fingerprint is None:
        digest = hashlib.sha1()
        sources = [paths.PROJECT_ROOT / "loom" / f"{name}.py"
                   for name in READING_MODULES]
        sources.append(paths.PROJECT_ROOT / "tools" / "framebands.py")
        for path in sources:
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"missing")
        for path in sorted(paths.TEMPLATES_DIR.rglob("*")):
            if path.is_file():
                stat = path.stat()
                digest.update(
                    f"{path.name}{stat.st_size}{stat.st_mtime_ns}".encode())
        _fingerprint = digest.hexdigest()[:16]
    return _fingerprint


FEED_CACHE = paths.CAPTURES_DIR / "feed_polls"

# What the FEED reader depends on, which is a different set of modules from
# what the queue reader depends on. Kept as its own cache with its own stamp
# for exactly that reason: changing the notification font must not throw
# away forty thousand queue readings, and changing a queue template must not
# throw away the feed.
FEED_MODULES = ("glyphs", "lines", "notifications")

_feed_fingerprint = None


def feed_fingerprint():
    """What code and what art produced these feed events."""
    global _feed_fingerprint
    if _feed_fingerprint is None:
        digest = hashlib.sha1()
        for name in FEED_MODULES:
            path = paths.PROJECT_ROOT / "loom" / f"{name}.py"
            try:
                digest.update(path.read_bytes())
            except OSError:
                digest.update(b"missing")
        for path in sorted(paths.TEMPLATES_DIR.rglob("*")):
            if path.is_file():
                stat = path.stat()
                digest.update(
                    f"{path.name}{stat.st_size}{stat.st_mtime_ns}".encode())
        _feed_fingerprint = digest.hexdigest()[:16]
    return _feed_fingerprint


def harvest_feed_run(run_dir, kit):
    """Every event the notification feed announced, on the game clock.

    Run exactly as loom/reader.py runs it: the phrase watcher first, then
    the glyph path contributing only vocabulary the phrase watcher does not
    own. The two keep independent cooldowns, so letting both claim the same
    line is how a duplicate town_center_built mints an imaginary Town
    Centre.
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return None
    reading = framebands.RunReader(kit)
    watcher = notifications.NotificationWatcher()
    text = glyphs.TextWatcher()
    events = []
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        bands = reading.read(image)
        if not bands.anchored or bands.clock is None:
            continue
        height, width = image.shape[:2]
        x1, y1, x2, y2 = notifications.panel_region(width, height)
        panel = image[max(0, y1):y2, max(0, x1):x2]
        if not panel.size:
            continue
        for event in watcher.watch(panel, bands.scale, bands.clock):
            events.append([bands.clock, event])
        claimed = set(watcher.templates or ())
        for event in text.watch(panel, bands.clock, bands.scale,
                                bands.skin):
            if event not in claimed:
                events.append([bands.clock, event])
    return {"run": os.path.basename(run_dir),
            "reader": feed_fingerprint(), "events": events}


def load_feed(run):
    """Cached feed events for one run, or None if absent or stale."""
    path = FEED_CACHE / f"{run}.json"
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if row.get("reader") != feed_fingerprint():
        return None
    return row


def path_for(run):
    return CACHE / f"{run}.json"


def harvest_run(run_dir, kit):
    """Decode one run and record every poll's queue."""
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return None
    reading = framebands.RunReader(kit, want_queue=True)
    polls = []
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        bands = reading.read(image)
        if not bands.anchored:
            # Recorded as a poll with no queue rather than dropped: "the HUD
            # was not found" and "the queue was empty" are different facts,
            # and a consumer that cannot tell them apart will conclude
            # production stopped when the game was merely in a menu.
            polls.append([bands.clock, None])
            continue
        polls.append([
            bands.clock,
            [[s.index, s.tint, s.progress, s.count, s.identity,
              s.identity_score] for s in (bands.slots or [])],
        ])
    return {"run": os.path.basename(run_dir),
            "reader": reading_fingerprint(),
            "polls": polls}


def load(run):
    """Cached polls for one run, or None if absent or from another reader."""
    path = path_for(run)
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if row.get("reader") != reading_fingerprint():
        return None
    return row


def slot_readings(row):
    """(clock, [SlotReading, ...] or None) per poll, as real objects.

    Rebuilt into SlotReading rather than handed over as lists so a consumer
    is running against the same shape the live reader produces. A tuple
    would work until the day someone adds a field.
    """
    for clock, slots in row["polls"]:
        if slots is None:
            yield clock, None
            continue
        yield clock, [queue.SlotReading(index, tint, progress, count,
                                        identity, score)
                      for index, tint, progress, count, identity, score
                      in slots]


def harvest_feed(only=None, verbose=True):
    """Every paired run's notification feed, decoded once."""
    FEED_CACHE.mkdir(parents=True, exist_ok=True)
    kit = framebands.Kit()
    done = []
    for run, record in sorted(queue_report.read_pairs().items()):
        if only and only not in run:
            continue
        run_dir = str(queue_report.CAPTURES / run)
        if not os.path.isdir(run_dir) or not os.path.exists(record):
            continue
        if load_feed(run) is not None:
            if verbose:
                print(f"  cached  {run} (feed)", flush=True)
            done.append(run)
            continue
        row = harvest_feed_run(run_dir, kit)
        if row is None:
            continue
        (FEED_CACHE / f"{run}.json").write_text(json.dumps(row),
                                                encoding="utf-8")
        if verbose:
            print(f"  decoded {run}: {len(row['events'])} feed events",
                  flush=True)
        done.append(run)
    return done


def harvest(only=None, verbose=True):
    """Every paired run, decoded once. Returns the runs now in the cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    kit = framebands.Kit()
    done = []
    for run, record in sorted(queue_report.read_pairs().items()):
        if only and only not in run:
            continue
        run_dir = str(queue_report.CAPTURES / run)
        if not os.path.isdir(run_dir) or not os.path.exists(record):
            continue
        if load(run) is not None:
            if verbose:
                print(f"  cached  {run}", flush=True)
            done.append(run)
            continue
        row = harvest_run(run_dir, kit)
        if row is None:
            continue
        path_for(run).write_text(json.dumps(row), encoding="utf-8")
        if verbose:
            print(f"  decoded {run}: {len(row['polls'])} polls", flush=True)
        done.append(run)
    return done


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--run", default=None, help="one run only")
    parser.add_argument("--feed", action="store_true",
                        help="harvest the notification feed instead, into "
                             "its own cache with its own stamp")
    arguments = parser.parse_args()
    if arguments.feed:
        runs = harvest_feed(arguments.run)
        total = sum(len(load_feed(r)["events"]) for r in runs
                    if load_feed(r))
        print()
        print(f"{len(runs)} runs, {total} feed events cached in "
              f"{paths.for_display(FEED_CACHE)}")
        return 0
    runs = harvest(arguments.run)
    total = 0
    for run in runs:
        row = load(run)
        if row:
            total += len(row["polls"])
    print(f"\n{len(runs)} runs, {total} polls cached in "
          f"{paths.for_display(CACHE)}")
    print("Every question about matching, thresholds or votes is now "
          "arithmetic over this,")
    print("rather than thirty thousand PNG decodes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
