"""
Loom — every reader, scored per resolution, across the whole capture corpus.

    python -m tools.reader_report --every 10    # a fast sampled pass
    python -m tools.reader_report               # the real one, cached
    python -m tools.reader_report --report      # print from the cache only

Every gate in this project scores ONE reader. digit_report scores the digits
on four matched runs, notif_report scores the feed against a labelled corpus,
queue_report scores the queue against recorded games. None of them splits by
RESOLUTION, and nothing puts the readers side by side - so "how is Loom doing
at 1080p" has never had an answer.

That gap sits on top of the mistake this project has made more often than any
other. The pixel-constant rule is first in CLAUDE.md because it has bitten at
least seven times: min_glyph_width skipping a 3px "1" so 18 villagers read as
8 on 189 frames of 300; max_glyph_width floored at its full-size value so it
never scaled DOWN; digit templates compared in a shape the screen never drew;
a notification phrase cut at 1440p scoring 0.598 against ITSELF at 1080p,
under its own 0.6 gate. Every one was a resolution effect and every one was
found by accident.

COVERAGE AND ACCURACY ARE DIFFERENT QUESTIONS AND ARE NEVER ONE COLUMN.
"How often did this band produce an answer" needs no witness and is available
for every reader. "How often was that answer right" needs something outside
Loom to disagree with, and only some readers have one. Averaging the two is
exactly how a reader that gets identity right and idleness wrong scores 50%
and looks mediocre instead of broken - queue_report's own docstring says so,
and this report keeps them apart for the same reason.

WHAT EACH READER IS MEASURED AGAINST:

  anchor        coverage only - "was the HUD found" is the measurement
  clock         self-consistency: a game clock only goes forwards, and it
                cannot jump further than the capture interval carries it
  villagers     the record's villager ORDERS, as a CEILING. Compared as a
                GAIN rather than a total, because starting villagers vary by
                civilisation and a hand-written table of that is the
                allowlist failure waiting to happen
  population    self-consistency: current must not exceed the cap
  age           the record's age ORDER plus durations.py's research time,
                which lands on the same game clock Loom reads
  queue         the record's orders, via queue_report's own helpers
  idle TCs      the record's Town Centre work windows
  resources     NOTHING, by design - advisory display, never a sync signal
  notifications not here - notif_report owns the labelled corpus
  APM           NOTHING. replay.command_rate counts what the game ACTED on
                and apmwin counts keystrokes; replay.py's own docstring says
                they are never blended and never share a name. A percentage
                here would be an invented ruler

THE CACHE IS STAMPED WITH THE READER GENERATION. A row carries the commit it
was measured at, the way gamestats stamps a session, and a row from a
different commit is re-measured rather than reused. reader_sweep.py exists
because pooling files from either side of a reader fix reports bugs that were
fixed hours earlier; this must not repeat that.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import bisect
import collections
import glob
import hashlib
import json
import os
import statistics
import struct
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import age as age_reader  # noqa: E402
from loom import durations, paths, production, replay  # noqa: E402
from tools import framebands, queue_report  # noqa: E402

CACHE = paths.CAPTURES_DIR / "reader_stats"

# Computed once per invocation - every cached row is stamped with it
# and a row stamped differently is re-measured rather than reused.
FINGERPRINT = None

# How far past the run's OWN typical step a clock reading may jump before it
# is called a misread.
#
# Not a constant in seconds, and the first draft was - which made it the
# pixel-constant mistake in a new place. grab_frames takes an interval as an
# argument (default 2s wall, and the game runs at up to 1.7x), --every
# multiplies it, and load shedding stretches it further. A fixed budget
# therefore says something different about every run it is applied to.
#
# So the budget comes from the run: the MEDIAN forward step is what this
# capture actually does, and a misread is not a slightly long gap, it is a
# wrong leading digit - which moves the answer by 60 or 600 seconds, not by
# a factor of two. The floor stops a run whose median step is 1s from
# flagging every ordinary pause.
CLOCK_JUMP_MULTIPLE = 10
CLOCK_JUMP_FLOOR = 60

# How far a NEW GAME drops the clock. The same value queue_report uses, and
# for the same reason: a backwards clock is evidence about the ALIGNMENT
# before it is evidence about the reader, and a restart is not a misread.
NEW_GAME_DROP = 60


def reader_fingerprint():
    """What code and what templates produced a cached row.

    The commit alone is not enough and the first draft used it. Between
    commits the reader changes constantly, so a stamp that only moves when
    something is committed hands back numbers measured by code that no
    longer exists - the reader-generation failure reader_sweep.py was
    written for, arriving from the other direction.

    So this hashes the reading code itself and the template set it matches
    against. Cheap enough to do per invocation: the sources are a few
    hundred kilobytes and the templates are hashed by name, size and mtime
    rather than by content.
    """
    digest = hashlib.sha1()
    sources = sorted((paths.PROJECT_ROOT / "loom").glob("*.py"))
    sources += [paths.PROJECT_ROOT / "tools" / "framebands.py",
                paths.PROJECT_ROOT / "tools" / "reader_report.py",
                paths.PROJECT_ROOT / "tools" / "queue_report.py"]
    for path in sources:
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing")
    for path in sorted((paths.TEMPLATES_DIR).rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.name}{stat.st_size}{stat.st_mtime_ns}"
                          .encode())
    return digest.hexdigest()[:16]


def frame_size(path):
    """A PNG's dimensions, from its header. Cheaper than decoding it."""
    with open(path, "rb") as handle:
        return struct.unpack(">II", handle.read(26)[16:24])


def skin_of(run):
    """The HUD skin the author named the folder for.

    grab_frames names a run run_<date>_<time>_<skin>[_<label>], so the skin
    is the FOURTH field. The first draft took the third and reported the
    capture time as the skin for every run in the corpus - harmless only
    because nothing grouped by it yet, which is exactly how a field like
    this stays wrong.
    """
    parts = os.path.basename(run).split("_")
    return parts[3].split("-")[0] if len(parts) > 3 else "?"


# ---- one run --------------------------------------------------------------

def measure_run(run_dir, record_path, kit, every=1):
    """One pass over a run's frames, reading every band.

    ONE pass, not one per reader. The cost here is the PNG decode - 83ms a
    frame at 1440p against a few ms for the readers - so every tool that
    re-walks the corpus for its own band pays the whole price again.
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))[::every]
    row = {
        "run": os.path.basename(run_dir),
        "skin": skin_of(run_dir),
        "every": every,
        "commit": paths.build_commit(),
        "reader": FINGERPRINT,
        "record": os.path.basename(record_path) if record_path else None,
        "frames": 0, "anchored": 0,
        "clock_read": 0, "clock_backwards": 0, "clock_jumps": 0,
        "villagers_read": 0, "population_read": 0, "population_over_cap": 0,
        "age_read": 0, "resources_read": 0,
    }
    if not frames:
        row["empty"] = True
        return row
    row["width"], row["height"] = frame_size(frames[0])

    reading = framebands.RunReader(kit, want_queue=True, want_age=True,
                                   want_resources=True)
    tracker = production.ProductionTracker()

    truth = replay.harvest(record_path) if record_path else None
    ordered = truth.queue_subjects() if truth else set()
    centres = replay.town_centres(truth) if truth else {}
    busy = queue_report.busy_windows(truth, centres) if truth else {}
    ends = (queue_report.record_duration(record_path)
            + queue_report.RECORD_END_GRACE) if record_path else None

    scales = []
    queue_counts = collections.Counter()
    ages_seen = {}
    age_seen_before = None
    villager_first = villager_first_clock = None
    villager_max = villager_max_clock = None
    previous = furthest = None
    steps = []
    left_the_game = False

    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        row["frames"] += 1
        bands = reading.read(image)
        if not bands.anchored:
            continue
        row["anchored"] += 1
        scales.append(bands.scale)

        when = bands.clock
        if when is not None:
            row["clock_read"] += 1
            if previous is not None:
                if when < previous - NEW_GAME_DROP:
                    # A new game, not a misread. Discard the history rather
                    # than fold two matches into one series.
                    previous = furthest = None
                elif when < previous:
                    row["clock_backwards"] += 1
                else:
                    steps.append(when - previous)
            previous = when
            furthest = when if furthest is None else max(furthest, when)

        # Has this run left the match its record describes? A capture run
        # outlives the game it was paired with more often than one would
        # think - grab_frames keeps going into the next match - and the
        # record knows nothing about what came after. Decided HERE rather
        # than beside the queue, because the villager check below needs it
        # too: measuring a second game's villagers against the first game's
        # orders reported one run as gaining 94 villagers against 7 ordered,
        # which is a fault in the measurement and not in the reader.
        if truth is not None and when is not None and not left_the_game:
            if when > ends or (furthest is not None
                               and when < furthest - NEW_GAME_DROP):
                left_the_game = True

        if bands.villagers is not None:
            row["villagers_read"] += 1
            if not left_the_game:
                if villager_first is None:
                    villager_first = bands.villagers
                    villager_first_clock = when
                if villager_max is None or bands.villagers > villager_max:
                    villager_max, villager_max_clock = bands.villagers, when

        if bands.population is not None:
            row["population_read"] += 1
            if bands.pop_cap and bands.population > bands.pop_cap:
                row["population_over_cap"] += 1

        if bands.age is not None and bands.age.age is not None:
            row["age_read"] += 1
            # An arrival is only recorded when the TRANSITION was witnessed -
            # this run must have seen a lower age first. A capture that
            # starts after the age-up shows Castle on its opening frame,
            # which is not the crest arriving early, it is the recording
            # arriving late; scoring it as an arrival makes every such run
            # look like the impossible case the floor check is hunting for.
            if age_seen_before is not None and bands.age.age > age_seen_before:
                for slug, value in age_reader.FILE_NAMES.items():
                    if value == bands.age.age and slug not in ages_seen \
                            and when is not None:
                        ages_seen[slug] = when
            if age_seen_before is None or bands.age.age > age_seen_before:
                age_seen_before = bands.age.age

        if bands.per_resource:
            row["resources_read"] += 1

        tracker.update(when, bands.slots)

        # The queue, scored the way queue_report scores it - its helpers,
        # not a second copy of its reasoning.
        if truth is None or bands.slots is None:
            continue
        if left_the_game:
            queue_counts["beyond"] += 1
            continue
        queue_counts["frames"] += 1
        for slot in bands.slots:
            if slot.identity is None:
                continue
            queue_counts["slots"] += 1
            family = queue_report.record_name(slot.identity)
            if family not in replay.QUEUE_KNOWN:
                queue_counts["unknown"] += 1
            elif family not in ordered \
                    and family in queue_report.replay_ids.QUEUE_UPGRADED:
                queue_counts["upgraded"] += 1
            else:
                queue_counts["checkable"] += 1
                if family not in ordered:
                    queue_counts["misread"] += 1
        if when is not None and tracker.idle_tcs > 0:
            queue_counts["idle_frames"] += 1
            exist = replay.town_centres_at(truth, when)
            allowed = exist - queue_report.working_at(busy, when)
            if exist and tracker.idle_tcs > allowed:
                queue_counts["idle_impossible"] += 1

    # Judged after the fact, against what this run's own pace turned out to
    # be. Doing it inside the loop would need a budget guessed in advance.
    if steps:
        typical = statistics.median(steps)
        budget = max(CLOCK_JUMP_MULTIPLE * typical, CLOCK_JUMP_FLOOR)
        row["clock_jumps"] = sum(1 for step in steps if step > budget)
        row["clock_step_median"] = typical
        row["clock_steps"] = len(steps)

    if scales:
        # A summary, not one entry per frame: the HUD does not
        # resize mid-match, and one run here has 22343 frames.
        row["scale_median"] = round(statistics.median(scales), 3)
        row["scale_range"] = [round(min(scales), 3),
                              round(max(scales), 3)]

    row["queue"] = dict(queue_counts)
    row["clock_furthest"] = furthest
    row["ages_seen"] = ages_seen

    if truth is not None:
        row["record_duration"] = queue_report.record_duration(record_path)
        row["ages_ordered"] = truth.ages()
        # The villager check, as a GAIN inside a window rather than a total.
        # Starting villagers vary by civilisation, and a table of that would
        # be a hand-curated list whose failure is silent; a gain needs no
        # such table, and deaths only ever push Loom's number DOWN, so the
        # inequality still holds.
        if (villager_first is not None and villager_max is not None
                and villager_first_clock is not None
                and villager_max_clock is not None):
            times = replay.trained_times(record_path)
            # Orders from the START of the game, not from the start of the
            # window. A villager ALIVE at the end of the window was ordered
            # at least a training time earlier, and longer if it queued
            # behind something - so counting only orders placed inside the
            # window undercounts the ceiling and accuses the reader of
            # over-reading when it did nothing of the kind. Measured: one
            # run read a gain of 8 against 7 orders in-window and 14 from
            # the start, which is comfortably within.
            #
            # Weaker than a tight bound and that is the right trade. This
            # check exists to catch a reader inventing villagers; a ceiling
            # that can never be crossed innocently is worth more than a
            # tight one that cries wolf.
            lo = 0
            hi = bisect.bisect_right(times, villager_max_clock)
            row["villager_gain"] = villager_max - villager_first
            row["villager_orders_in_window"] = hi - lo
            # The window itself, kept so a later correction to this check
            # can be made at report time instead of costing another full
            # pass over the corpus. This one already cost one.
            row["villager_window"] = [villager_first_clock,
                                      villager_max_clock]
            row["villager_first"] = villager_first
            row["villager_max"] = villager_max
    return row


# ---- the corpus -----------------------------------------------------------

def cached(run, every):
    """A previous row, if it was measured by THIS reader and this sampling."""
    path = CACHE / f"{run}.json"
    if not path.exists():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if row.get("reader") != FINGERPRINT or row.get("every") != every:
        return None
    return row


def measure_corpus(every=1, only=None, verbose=True):
    """Every capture run, measured or read back from the cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    runs = sorted(glob.glob(str(paths.CAPTURES_DIR / "run_*")))
    pairs = queue_report.read_pairs()
    kit = framebands.Kit()
    rows = []
    for run_dir in runs:
        run = os.path.basename(run_dir)
        if only and only not in run:
            continue
        row = cached(run, every)
        if row is None:
            record = pairs.get(run)
            if record and not os.path.exists(record):
                record = None
            row = measure_run(run_dir, record, kit, every)
            (CACHE / f"{run}.json").write_text(json.dumps(row),
                                               encoding="utf-8")
            if verbose:
                print(f"  measured {run}")
        elif verbose:
            print(f"  cached   {run}")
        rows.append(row)
    return rows


# ---- reporting ------------------------------------------------------------

def spread(values):
    """Median and range, as a string. Empty when nothing was measured."""
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0]:.1f}%"
    return (f"{statistics.median(values):5.1f}%  "
            f"{min(values):.0f}-{max(values):.0f}")


def rate(row, key, over="anchored"):
    base = row.get(over) or 0
    return 100.0 * row.get(key, 0) / base if base else None


def by_resolution(rows):
    groups = collections.defaultdict(list)
    for row in rows:
        if row.get("empty") or not row.get("frames"):
            continue
        groups[(row.get("width"), row.get("height"))].append(row)
    return dict(sorted(groups.items(), reverse=True))


# The coverage bands, as a registry rather than a hand-list. A band added
# to measure_run and forgotten here would simply not appear, which is the
# drift CLAUDE.md names: walk the registry, do not agree with it.
COVERAGE = (
    ("anchor", "anchored", "frames"),
    ("clock", "clock_read", "anchored"),
    ("villagers", "villagers_read", "anchored"),
    ("population", "population_read", "anchored"),
    ("age crest", "age_read", "anchored"),
    ("resources", "resources_read", "anchored"),
)

# The accuracy measures. Each takes the rows for ONE resolution and returns
# a string, because they do not share a shape - a rate, a count of
# impossibles and a runs-ok tally are three different kinds of answer and
# forcing them into one column would be the averaging mistake this whole
# report exists to avoid.
ACCURACY = (
    ("clock backwards", lambda rows: total(rows, "clock_backwards")),
    ("clock jumps", lambda rows: total(rows, "clock_jumps")),
    ("pop over cap", lambda rows: total(rows, "population_over_cap")),
    ("queue identity", lambda rows: queue_accuracy(rows)),
    ("idle TC impossible", lambda rows: idle_share(rows)),
    ("villager ceiling", lambda rows: villager_ceiling(rows)),
    ("age under floor", lambda rows: age_error(rows)),
)


def total(rows, key):
    """A raw count across runs, with the sample it came out of."""
    seen = sum(r.get(key, 0) for r in rows)
    base = sum(r.get("clock_read", 0) for r in rows)
    return f"{seen}  of {base}" if base else "-"


def report(rows, feed=None):
    groups = by_resolution(rows)
    if not groups:
        print("nothing measured")
        return
    keys = list(groups)
    header = f"{'band':<20}" + "".join(f"{f'{w}x{h}':>22}" for w, h in keys)

    generations = {r.get("reader") for r in rows if r.get("reader")}
    samplings = {r.get("every") for r in rows}
    if len(generations) > 1 or len(samplings) > 1:
        print()
        print(f"WARNING: these rows come from {len(generations)} different"
              f" readers and {len(samplings)} samplings.")
        print("Pooling reader generations reports bugs that were fixed hours"
              " ago - re-run without --report.")

    print()
    print("COVERAGE - how often each band produced an answer at all.")
    print("Over ANCHORED frames, because a menu with no HUD is not a reader")
    print("failure and counting it as one hides the real number. Median")
    print("across runs, then the per-run range - the range is the")
    print("consistency answer and a pooled mean would hide it.")
    print()
    print(header)
    print(f"{'runs':<20}" + "".join(f"{len(groups[k]):>22}" for k in keys))
    for band, key, over in COVERAGE:
        print(f"{band:<20}" + "".join(
            f"{spread([rate(r, key, over) for r in groups[k]]):>22}"
            for k in keys))

    print()
    print("ACCURACY - how often the answer was RIGHT, against a witness")
    print("outside Loom. A dash means no witness exists there, which is a")
    print("different thing from a zero.")
    print()
    print(header)
    for label, measure in ACCURACY:
        print(f"{label:<20}" + "".join(
            f"{measure(groups[k]):>22}" for k in keys))

    if feed:
        print()
        print(f"{'feed (notif corpus)':<20}"
              + "".join(f"{feed.get(k, '-'):>22}" for k in keys))

    print()
    print("NOT MEASURABLE, and named rather than omitted - a reader")
    print("missing from a table reads as an oversight, one named as")
    print("unmeasurable reads as a decision.")
    print("  resources   advisory display by design; no witness exists and")
    print("              none is wanted - per-resource counts never advance")
    print("              build-order state")
    print("  APM         replay.command_rate counts what the game ACTED on,")
    print("              apmwin counts keystrokes. Different quantities that")
    print("              replay.py says are never blended and never share a")
    print("              name; a percentage here would invent a ruler")
    if not feed:
        print("  feed        not scored in this run. It CAN be - the corpus")
        print("              is labelled and splits by resolution - but it")
        print("              re-reads 3024 crops, so pass --feed")

    print()
    print("WHAT THE CORPUS CANNOT SAY")
    scored = [r for r in rows if not r.get("empty") and r.get("frames")]
    unpaired = sum(1 for r in scored if not r.get("record"))
    print(f"  {unpaired} of {len(scored)} runs have no paired recorded game,")
    print("  so they contribute coverage and no accuracy at all.")
    print("  4K is absent entirely - there are no 4K captures, and it is the")
    print("  resolution least like the two that are here.")
    print("  The queue's witness asks 'was this ever ordered', not 'is this")
    print("  what the cell shows', so a research named as its own unit in a")
    print("  game that trained that unit still scores as correct.")


def feed_by_resolution():
    """The notification corpus score, grouped by the resolution it was cut at.

    Reuses notif_report's own scorer rather than reading the corpus again:
    it returns per-run totals and the corpus is organised by source run, so
    the resolution is the source run's frame size.
    """
    from tools import notif_report
    body, _ = notif_report.report()
    per_run = notif_report.scores("\n".join(body))
    groups = collections.defaultdict(lambda: collections.Counter())
    for run, totals in per_run.items():
        frames = sorted(glob.glob(str(paths.CAPTURES_DIR / run
                                      / "frame_*.png")))
        if not frames:
            continue
        size = frame_size(frames[0])
        for key, value in totals.items():
            groups[size][key] += value
    out = {}
    for size, totals in groups.items():
        if totals["count"]:
            out[size] = (f"{100 * totals['event'] / totals['count']:5.1f}%"
                         f"  n={totals['count']}")
    return out


def queue_accuracy(rows):
    checkable = sum(r.get("queue", {}).get("checkable", 0) for r in rows)
    misread = sum(r.get("queue", {}).get("misread", 0) for r in rows)
    if not checkable:
        return "-"
    return f"{100 * (1 - misread / checkable):5.2f}%  n={checkable}"


def idle_share(rows):
    frames = sum(r.get("queue", {}).get("idle_frames", 0) for r in rows)
    bad = sum(r.get("queue", {}).get("idle_impossible", 0) for r in rows)
    if not frames:
        return "-"
    return f"{100 * bad / frames:5.1f}%  n={frames}"


def villager_ceiling(rows):
    """How many runs read MORE villagers than the record ordered.

    One-sided on purpose: orders bound the count from above only, because a
    villager can be ordered and cancelled, or born and killed.
    """
    checked = [r for r in rows if "villager_gain" in r]
    if not checked:
        return "-"
    over = sum(1 for r in checked
               if r["villager_gain"] > r["villager_orders_in_window"])
    return f"{len(checked) - over}/{len(checked)} runs ok"


def age_error(rows):
    """When the crest changed, against the earliest it possibly could have.

    The record says when the age was ORDERED; durations.py says the research
    time, which is exact and does not divide among villagers. Their sum is a
    FLOOR, not a target: the research queues behind whatever the Town Centre
    was already doing, so arriving late is ordinary and arriving late by a
    lot just means a busy Town Centre.

    So the number that matters is how often it comes in UNDER the floor,
    which is impossible - the same three-way answer tools/measure_durations
    gives, and for the same reason. A negative here is a fault in the crest
    reader, the pairing, or the table, and nothing else can explain it.
    """
    errors = []
    for row in rows:
        ordered = row.get("ages_ordered") or {}
        seen = row.get("ages_seen") or {}
        for slug, when_ordered in ordered.items():
            if slug not in seen or slug not in durations.TECHNOLOGIES:
                continue
            errors.append(seen[slug]
                          - (when_ordered + durations.TECHNOLOGIES[slug]))
    if not errors:
        return "-"
    impossible = sum(1 for e in errors if e < 0)
    # The median is worth printing beside the count, because it is the
    # strongest single result this report produces: three independent
    # things - the crest reader, durations.py's table and the record's own
    # timestamps - have to be right together for it to land near zero.
    return (f"{statistics.median(errors):+.0f}s med, "
            f"{impossible} under /{len(errors)}")


def main():
    global FINGERPRINT
    FINGERPRINT = reader_fingerprint()
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--every", type=int, default=1,
                        help="measure every Nth frame")
    parser.add_argument("--run", default=None, help="one run only")
    parser.add_argument("--report", action="store_true",
                        help="print from the cache without measuring")
    parser.add_argument("--feed", action="store_true",
                        help="also score the notification corpus, which "
                             "re-reads 3024 labelled crops")
    arguments = parser.parse_args()

    if arguments.report:
        rows = []
        for path in sorted(CACHE.glob("*.json")):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    else:
        rows = measure_corpus(arguments.every, arguments.run)
    report(rows, feed_by_resolution() if arguments.feed else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
