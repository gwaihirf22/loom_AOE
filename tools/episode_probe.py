"""
Loom — how a producing queue item behaves across polls (development tool).

    python -m tools.episode_probe                 # the three cheap questions
    python -m tools.episode_probe --feed          # and what the feed said
    python -m tools.episode_probe --runs 6        # fewer runs, faster

THIS TOOL COMMITS TO NO RULE. It exists because the rule is not yet known,
and writing the tracker first would mean choosing a rule and then looking for
numbers that flatter it.

The problem it is measuring. The queue reader is right about 95% of the
readings it makes and the Post-game page still shows a wall of things that
never happened: 26 wrong readings out of 2,293 in one game, but NINE DISTINCT
wrong subjects, because `gamestats` records the first sighting of anything
the queue names, once, with no corroboration. One glance becomes a permanent
fact. Every other reader in Loom refuses that - filters believes a count that
repeats, notifications refuses an uncorroborated line, production debounces
every state change - and the queue is the one that does not.

The intended fix is to decide an EPISODE of production once, by vote, from
every poll that saw it. That needs a way to follow one item across polls, and
that is what this measures.

WHAT IS ALREADY KNOWN, from one run read by hand:

  slot 0 progress   0.62 0.79 0.87 -> 0.23 0.41 0.62 0.77 0.82 -> 0.21 ...
  slot 0 tint       G G N N G G G G G N N G G G G G N N G G G G G N N G

The green wash is a sawtooth and its reset is an item finishing. The untinted
polls sit at the top of each tooth - an item placed before its wash starts -
which settles a contradiction in the source: production.py says untinted at
the front means just-placed, classify_tint's docstring says it means waiting.
At the FRONT production.py is right.

WHAT IS NOT KNOWN, and what this answers:

  1. how far progress moves per poll, and how big a drop really means a reset
     rather than a wobble in the wash measurement;
  2. HOW OFTEN A PRODUCING CELL CHANGES POSITION while it is still producing.
     The queue shifts left as groups finish, and an episode split in two by a
     shift would vote twice and report one item as two. This is the number
     that decides whether tracking can key on position at all;
  3. how often two producers sit close enough in progress that matching them
     between polls is ambiguous - the failure mode of the obvious matcher;
  4. with --feed, what the notification feed said while an item produced,
     which is the second witness the corroboration step will lean on.

The matcher used HERE is deliberately naive - continue an item onto the cell
with the smallest progress not less than its own - because the point is to
measure how often that naive rule is ambiguous, not to be right. Its
ambiguity IS the measurement.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import glob
import os
import statistics
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, notifications, replay  # noqa: E402
from loom import episodes  # noqa: E402
from tools import framebands, queue_report  # noqa: E402

# A producing cell is green, or untinted AT THE FRONT of the strip. Away from
# the front, untinted is the waiting portrait and has produced nothing.
FRONT = 0

# A fall of at least this much is the wash starting over rather than the
# measurement wobbling. Chosen from the corpus histogram, not invented: at a
# HELD position 93% of changes land in +0.0..+0.2 and the falls spread from
# -0.05 down to -0.9, so the two populations separate here and nowhere near
# the forward mass.
RESET_FALL = 0.05

# The largest forward step a single poll can honestly carry. Above this the
# cell is not the same item creeping on - measured, the 95th percentile of a
# real step is 0.256.
MAX_STEP = 0.35


def producing(slots):
    """The cells that are actually making something, this poll.

    Amber is waiting and red is blocked; neither has produced anything, and
    both are most of what the strip holds. Restricting to producers is most
    of the reason this approach is cheaper than reading every cell.
    """
    out = []
    for slot in slots or []:
        if slot.tint == "green":
            out.append(slot)
        elif slot.tint is None and slot.index == FRONT:
            out.append(slot)
    return out


def continues(previous, current, tolerance):
    """Naive continuation: {previous index: current index}.

    An item's wash only ever grows, so its next reading is the smallest
    progress not below its own. Deliberately simple - what is being measured
    is how often this is AMBIGUOUS, and a cleverer matcher would hide that.

    Returns the mapping and the number of previous cells whose best two
    candidates were within `tolerance` of each other, which is the count of
    times this rule was guessing.
    """
    taken = set()
    mapping = {}
    ambiguous = 0
    for old in previous:
        if old.progress is None:
            continue
        forward = sorted(
            (new for new in current
             if new.index not in taken and new.progress is not None
             and new.progress >= old.progress - tolerance),
            key=lambda new: new.progress)
        if not forward:
            continue
        if len(forward) > 1 and abs(forward[1].progress
                                    - forward[0].progress) <= tolerance:
            ambiguous += 1
        mapping[old.index] = forward[0].index
        taken.add(forward[0].index)
    return mapping, ambiguous


def score_run(run_dir, record_path, kit):
    """Distinct subjects that survive, today's rule against the tracker's.

    Today: gamestats records the first sighting of anything the queue names,
    once, with no corroboration - so every distinct identity ever glimpsed
    becomes a permanent fact. That is the baseline being beaten.

    The tracker: only voted episodes count, and an episode too short to vote
    on contributes nothing.

    Counting DISTINCT SUBJECTS, not readings, because that is what a person
    reads off the Post-game page. The same game was 1.13% wrong by readings
    and nine wrong things by subjects.
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return None
    reading = framebands.RunReader(kit, want_queue=True)
    tracker = episodes.EpisodeTracker()
    ends = queue_report.record_duration(record_path) \
        + queue_report.RECORD_END_GRACE
    ordered = replay.harvest(record_path).queue_subjects()

    every_sighting = set()
    furthest = None
    left = False
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        bands = reading.read(image)
        if not bands.anchored:
            continue
        if bands.clock is not None and not left:
            if bands.clock > ends or (furthest is not None
                                      and bands.clock < furthest - 60):
                left = True
            furthest = (bands.clock if furthest is None
                        else max(furthest, bands.clock))
        if left:
            continue
        for slot in bands.slots or []:
            if slot.identity:
                every_sighting.add(queue_report.record_name(slot.identity))
        tracker.update(bands.clock, bands.slots)
    tracker.flush()

    # The LONGEST episode that would have named each subject, so the
    # threshold can be swept without another pass over the corpus. Reading
    # `tally` directly rather than `identity` on purpose: identity applies
    # MIN_POLLS_TO_VOTE, and the point here is to vary that.
    best_polls = {}
    for episode in tracker.closed:
        if not episode.tally:
            continue
        name = queue_report.record_name(episode.tally.most_common(1)[0][0])
        best_polls[name] = max(best_polls.get(name, 0), episode.polls)

    voted = {name for name, polls in best_polls.items()
             if polls >= episodes.MIN_POLLS_TO_VOTE}

    def split(names):
        known = {n for n in names if n in replay.QUEUE_KNOWN}
        return ({n for n in known if n in ordered},
                {n for n in known if n not in ordered})

    was_real, was_phantom = split(every_sighting)
    now_real, now_phantom = split(voted)
    return {"run": os.path.basename(run_dir),
            "was_real": was_real, "was_phantom": was_phantom,
            "now_real": now_real, "now_phantom": now_phantom,
            "best_polls": best_polls, "ordered": ordered}


def feed_reader():
    """The two watchers the live reader runs over the notification panel."""
    watcher = notifications.NotificationWatcher()
    text = glyphs.TextWatcher()
    return watcher, text


def probe_run(run_dir, record_path, kit, want_feed=False):
    """Every measurement, for one run."""
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return None
    reading = framebands.RunReader(kit, want_queue=True)
    ends = (queue_report.record_duration(record_path)
            + queue_report.RECORD_END_GRACE) if record_path else None

    steps = []            # progress change between consecutive polls
    drops = []            # how far a reset falls
    concurrent = collections.Counter()
    moved = collections.Counter()   # index change while still producing
    ambiguous = 0
    matched = 0
    feed_seen = collections.Counter()
    verdicts = collections.Counter()   # the POSITION-FIRST rule's outcomes

    watcher = text = None
    if want_feed:
        watcher, text = feed_reader()

    previous = []
    furthest = None
    left = False
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        bands = reading.read(image)
        if not bands.anchored:
            continue
        # The game-boundary rule, because a run outliving its record has
        # already fooled three measurements in this project.
        if bands.clock is not None and not left and ends is not None:
            if bands.clock > ends or (furthest is not None
                                      and bands.clock < furthest - 60):
                left = True
            furthest = (bands.clock if furthest is None
                        else max(furthest, bands.clock))
        if left:
            continue

        current = producing(bands.slots)
        concurrent[len(current)] += 1

        mapping, unsure = continues(previous, current, tolerance=0.06)
        ambiguous += unsure
        by_index = {slot.index: slot for slot in current}
        for old in previous:
            landed = mapping.get(old.index)
            if landed is None or old.progress is None:
                continue
            new = by_index[landed]
            if new.progress is None:
                continue
            matched += 1
            moved[landed - old.index] += 1
            steps.append(new.progress - old.progress)
        # Every SIGNED change at a held position, whatever the matcher made
        # of it. Kept separate from `steps` on purpose: `steps` is what the
        # matcher believed was one item continuing, and this is the raw
        # evidence the matcher was built on top of. If the two disagree the
        # matcher is inventing continuations.
        #
        # The question a threshold has to answer is whether these fall into
        # TWO populations - a wash creeping forward, and a wash starting
        # over - or one smear. Reporting a median over both together would
        # answer it by averaging, which is how a threshold gets chosen that
        # is bad at both.
        for old in previous:
            if old.progress is None:
                continue
            same = by_index.get(old.index)
            if same is not None and same.progress is not None:
                drops.append(same.progress - old.progress)

        # The candidate rule, scored directly: POSITION first, progress as
        # corroboration. The naive progress-first matcher above guesses in a
        # quarter of continuations once several buildings produce at once,
        # so it cannot be the rule - but it can measure its own ambiguity,
        # which is the argument for looking here instead.
        for old in previous:
            if old.progress is None:
                verdicts["no progress to judge"] += 1
                continue
            here = by_index.get(old.index)
            if here is not None and here.progress is not None:
                change = here.progress - old.progress
                if -RESET_FALL < change <= MAX_STEP:
                    verdicts["continued in place"] += 1
                    continue
                if change <= -RESET_FALL:
                    verdicts["reset in place (episode ended)"] += 1
                    continue
                verdicts["implausible jump in place"] += 1
                continue
            for shift in (-1, 1):
                near = by_index.get(old.index + shift)
                if near is not None and near.progress is not None:
                    change = near.progress - old.progress
                    if -RESET_FALL < change <= MAX_STEP:
                        verdicts[f"shifted {shift:+d}"] += 1
                        break
            else:
                verdicts["gone from the producing set"] += 1

        if want_feed and bands.clock is not None and bands.scale:
            height, width = image.shape[:2]
            x1, y1, x2, y2 = notifications.panel_region(width, height)
            panel = image[max(0, y1):y2, max(0, x1):x2]
            if panel.size:
                for event in watcher.watch(panel, bands.scale, bands.clock):
                    feed_seen[event] += 1
                claimed = set(watcher.templates or ())
                for event in text.watch(panel, bands.clock, bands.scale,
                                        None):
                    if event not in claimed:
                        feed_seen[event] += 1

        previous = current

    return {
        "run": os.path.basename(run_dir),
        "steps": steps, "drops": drops, "concurrent": concurrent,
        "moved": moved, "ambiguous": ambiguous, "matched": matched,
        "feed": feed_seen, "verdicts": verdicts,
    }


def score(kit, pairs, limit=0):
    """Distinct subjects kept and lost, across the paired corpus."""
    rows = []
    for run, record in sorted(pairs.items()):
        run_dir = str(queue_report.CAPTURES / run)
        if not os.path.isdir(run_dir) or not os.path.exists(record):
            continue
        found = score_run(run_dir, record, kit)
        if found is None:
            continue
        rows.append(found)
        print(f"  {found['run']}: real {len(found['was_real'])}->"
              f"{len(found['now_real'])}  phantom "
              f"{len(found['was_phantom'])}->{len(found['now_phantom'])}",
              flush=True)
        if limit and len(rows) >= limit:
            break
    if not rows:
        print("no paired runs to score")
        return 1
    was_real = sum(len(r["was_real"]) for r in rows)
    was_ph = sum(len(r["was_phantom"]) for r in rows)
    now_real = sum(len(r["now_real"]) for r in rows)
    now_ph = sum(len(r["now_phantom"]) for r in rows)
    print()
    print("DISTINCT SUBJECTS, which is what the Post-game page lists")
    print(f"  {'':22}{'real kept':>12}{'phantoms':>12}")
    print(f"  {'believe every glance':22}{was_real:>12}{was_ph:>12}"
          "   <- gamestats today")
    print(f"  {'voted episodes':22}{now_real:>12}{now_ph:>12}")
    if was_ph:
        print()
        print(f"  phantoms removed  {was_ph - now_ph} of {was_ph} "
              f"({100 * (was_ph - now_ph) / was_ph:.0f}%)")
    if was_real:
        print(f"  real lost         {was_real - now_real} of {was_real} "
              f"({100 * (was_real - now_real) / was_real:.0f}%)")
    print()
    print("  THE WHOLE CURVE, so a threshold is chosen and not guessed:")
    print(f"  {'polls required':>16}{'real kept':>12}{'phantoms':>10}"
          f"{'real lost':>12}")
    for threshold in (1, 2, 3, 4, 5, 6, 8):
        kept = phantoms = 0
        for r in rows:
            for name, polls in r["best_polls"].items():
                if polls < threshold or name not in replay.QUEUE_KNOWN:
                    continue
                if name in r["ordered"]:
                    kept += 1
                else:
                    phantoms += 1
        lost = was_real - kept
        mark = "  <- shipped" if threshold == episodes.MIN_POLLS_TO_VOTE else ""
        print(f"  {threshold:>16}{kept:>12}{phantoms:>10}"
              f"{lost:>7} ({100 * lost / max(1, was_real):.0f}%){mark}")

    print()
    print("  A real subject lost is a thing that genuinely produced and")
    print("  was only ever glimpsed. It is the price, and it is an error -")
    print("  a cheaper one than a phantom, but not a free one.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--runs", type=int, default=0,
                        help="stop after this many paired runs")
    parser.add_argument("--score", action="store_true",
                        help="score the EpisodeTracker against believing "
                             "every sighting, which is what gamestats does "
                             "today")
    parser.add_argument("--feed", action="store_true",
                        help="also read the notification panel, which is "
                             "slower and is the second witness")
    arguments = parser.parse_args()

    kit = framebands.Kit()
    pairs = queue_report.read_pairs()
    if arguments.score:
        return score(kit, pairs, arguments.runs)
    results = []
    for run, record in sorted(pairs.items()):
        run_dir = str(queue_report.CAPTURES / run)
        if not os.path.isdir(run_dir) or not os.path.exists(record):
            continue
        found = probe_run(run_dir, record, kit, arguments.feed)
        if found is None:
            continue
        results.append(found)
        print(f"  {found['run']}: {len(found['steps'])} continued readings",
              flush=True)
        if arguments.runs and len(results) >= arguments.runs:
            break

    if not results:
        print("no paired runs to probe")
        return 1

    steps = [s for r in results for s in r["steps"]]
    drops = [d for r in results for d in r["drops"]]
    moved = collections.Counter()
    concurrent = collections.Counter()
    for r in results:
        moved.update(r["moved"])
        concurrent.update(r["concurrent"])
    matched = sum(r["matched"] for r in results)
    ambiguous = sum(r["ambiguous"] for r in results)

    print(f"\n{len(results)} runs, {matched} continued readings\n")

    print("1. HOW FAR PROGRESS MOVES PER POLL")
    if steps:
        quantiles = statistics.quantiles(steps, n=20)
        print(f"   median {statistics.median(steps):.3f}   "
              f"5th {quantiles[0]:.3f}   95th {quantiles[-1]:.3f}   "
              f"max {max(steps):.3f}")
    if drops:
        print(f"\n   Every signed change at a held position ({len(drops)}), "
              f"as a histogram.")
        print("   A reset is separable only if this is TWO populations and "
              "not one smear:")
        buckets = collections.Counter()
        for change in drops:
            buckets[round(change * 10) / 10] += 1
        for edge in sorted(buckets):
            bar = "#" * max(1, round(40 * buckets[edge] / max(buckets.values())))
            print(f"   {edge:+.1f}  {buckets[edge]:6d}  {bar}")
        falls = [d for d in drops if d < -0.05]
        if falls:
            print(f"   falls past -0.05: {len(falls)}, "
                  f"largest {min(falls):.3f}, smallest {max(falls):.3f}")

    print("\n2. DOES A PRODUCING CELL CHANGE POSITION MID-EPISODE?")
    total = sum(moved.values()) or 1
    for delta in sorted(moved):
        share = 100 * moved[delta] / total
        note = "  <- stayed put" if delta == 0 else ""
        print(f"   {delta:+d} slots: {moved[delta]:6d}  {share:5.1f}%{note}")
    stayed = 100 * moved.get(0, 0) / total
    print(f"   Tracking may key on position only if this is overwhelmingly "
          f"0. It is {stayed:.1f}%.")

    verdicts = collections.Counter()
    for r in results:
        verdicts.update(r["verdicts"])
    if verdicts:
        print("\n2b. THE CANDIDATE RULE: POSITION FIRST, PROGRESS AS "
              "CORROBORATION")
        total_v = sum(verdicts.values())
        for name, n in verdicts.most_common():
            print(f"   {name:<34} {n:6d}  {100 * n / total_v:5.1f}%")
        print("   The naive matcher above makes progress primary and guesses")
        print("   in a quarter of continuations once several buildings work")
        print("   at once. This asks position first instead - which is what")
        print("   the same-index histogram in 1 was already answering.")

    print("\n3. HOW OFTEN IS THE NAIVE PROGRESS-FIRST MATCHER GUESSING?")
    print(f"   {ambiguous} of {matched} continuations had two candidates "
          f"within 0.06 ({100 * ambiguous / max(1, matched):.1f}%)")

    print("\n4. HOW MANY ITEMS PRODUCE AT ONCE")
    for count in sorted(concurrent):
        print(f"   {count} producing: {concurrent[count]:6d} polls")

    feed = collections.Counter()
    for r in results:
        feed.update(r["feed"])
    if feed:
        print("\n5. WHAT THE FEED ANNOUNCED (the second witness)")
        for event, n in feed.most_common(14):
            print(f"   {event:<40} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
