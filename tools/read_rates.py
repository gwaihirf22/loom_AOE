"""
Loom — how much of the HUD each reader actually got, run by run.

    python -m tools.read_rates run_20260824_124218_annehk
    python -m tools.read_rates run_2026082*            # a table across runs

Every reader is measured the way the live reader calls it, on saved
frames, so two captures of the SAME GAME on different HUD skins can be put
side by side. That comparison is the point: a difference between two
ordinary captures is always partly the game, and only a matched pair can
attribute anything to the HUD.

Matched runs are compared BY GAME CLOCK, never by frame number. Frame
numbers do not line up across recordings of one game - it depends when the
recording was started, and a pause during one of them shifts everything
after it. The clock is the only coordinate the runs share, and Loom reads
it off the screen, so it is the honest x-axis. --by-minute reports each
band per game minute so two runs can be read against each other whatever
their offset.

What matched runs still do NOT control for is the WORLD behind the HUD.
The camera is somewhere different each recording, so the notification
panel sits over different terrain and the resource bar over different
architecture. The events are the same and the skin is the variable, but
the ground is not, and a difference between two runs can still be the
ground. That is why a factorial set is worth the play time: an effect that
shows up on BOTH skins is an effect, and one that shows up on only one has
terrain as a live suspect.

What it reports per run: how often the anchor was found at all, then how
often each band read once it was. Rates are over ANCHORED frames rather
than all frames, because a menu with no HUD is not a reader failure and
counting it as one hides the real number - measured on a real pair, one
capture ran 141 frames longer into the score screen and looked like it had
a worse anchor for it.

The digit signature at the end is the useful part when something is wrong.
A reader that fails evenly across digits has a band or threshold problem;
one that fails on a single digit has a template or segmentation problem
with that shape. Measured on a 1920x1080 game: "1" appeared in 34.7% of
failed clock reads while "2", "5" and "7" appeared in none, which pointed
straight at the one glyph rather than at the band.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import glob
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import paths  # noqa: E402
from tools import framebands  # noqa: E402


def measure(run_dir, every=1, kit=None):
    """Read every band of every frame. Returns a dict of counts.

    The per-frame reading itself lives in tools/framebands.py, because
    tools/reader_report.py needs the same thing and a second copy of "how do
    you read a frame" is the one-question-two-places failure. kit is
    injectable so a caller measuring many runs loads the 527 queue templates
    once rather than once per run.
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))[::every]
    # No crests: this tool has never reported the age, and loading them for
    # a band nobody asks about is time spent on nothing.
    reading = framebands.RunReader(kit or framebands.Kit(crests=False))

    counts = collections.Counter()
    fail_digits = collections.Counter()
    ok_digits = collections.Counter()
    skins = collections.Counter()
    scales = []
    last = None
    # Per game MINUTE, so two recordings of one game can be read against
    # each other however their frame numbers line up. A frame whose clock
    # did not read is filed under the last minute I did read: the clock is
    # the only coordinate available, and a failure sits next to the
    # readings around it rather than nowhere.
    minutes = collections.defaultdict(collections.Counter)
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        counts["frames"] += 1
        bands = reading.read(image)
        if not bands.anchored:
            continue
        counts["anchored"] += 1
        skins[bands.skin] += 1
        scales.append(bands.scale)

        seconds = bands.clock
        if seconds is not None:
            counts["clock"] += 1
            last = seconds
            for char in f"{seconds // 3600:02d}{seconds // 60 % 60:02d}":
                ok_digits[char] += 1
        minute = last // 60 if last is not None else -1
        bucket = minutes[minute]
        bucket["frames"] += 1
        bucket["clock"] += 1 if seconds is not None else 0
        if seconds is None and last is not None:
            # The digits of the last time I DID read, as the best available
            # guess at what was on screen when this read failed. Not exact -
            # the clock has moved on - but the leading fields change slowly,
            # so the shapes involved are almost always the same ones.
            for char in f"{last // 3600:02d}{last // 60 % 60:02d}":
                fail_digits[char] += 1

        if bands.villagers is not None:
            counts["villagers"] += 1
            bucket["villagers"] += 1

        if bands.population is not None:
            counts["population"] += 1
            bucket["population"] += 1
    return counts, fail_digits, ok_digits, skins, scales, minutes


def main():
    parser = argparse.ArgumentParser(
        description="Per-band read rates for capture runs.")
    parser.add_argument("runs", nargs="+", help="run directory names")
    parser.add_argument("--every", type=int, default=1, metavar="N",
                        help="measure every Nth frame (default all)")
    parser.add_argument("--by-minute", action="store_true",
                        help="rates per GAME minute, so runs of one game "
                             "line up whatever their frame numbers are")
    arguments = parser.parse_args()

    matched = []
    for pattern in arguments.runs:
        matched.extend(sorted(glob.glob(str(paths.CAPTURES_DIR / pattern))))
    if not matched:
        print(f"no runs matching {arguments.runs}")
        return

    print(f"{'run':44} {'skin':7} {'frames':>7} {'anchor':>7} "
          f"{'clock':>7} {'vill':>7} {'pop':>7}")
    signatures = {}
    per_minute = {}
    for run_dir in matched:
        if not os.path.isdir(run_dir):
            continue
        name = os.path.basename(run_dir)
        counts, bad, good, skins, scales, minutes = measure(run_dir, arguments.every)
        anchored = counts["anchored"] or 1
        skin = skins.most_common(1)[0][0] if skins else "-"
        print(f"{name[:44]:44} {skin:7} {counts['frames']:7d} "
              f"{100*counts['anchored']/max(1, counts['frames']):6.1f}% "
              f"{100*counts['clock']/anchored:6.1f}% "
              f"{100*counts['villagers']/anchored:6.1f}% "
              f"{100*counts['population']/anchored:6.1f}%")
        signatures[name] = (bad, good)
        per_minute[name] = minutes

    for name, (bad, good) in signatures.items():
        rows = [(digit, bad[digit], good[digit]) for digit in "0123456789"
                if bad[digit] + good[digit]]
        if not any(b for _d, b, _g in rows):
            continue
        worst = sorted(rows, key=lambda row: -row[1] / max(1, row[1] + row[2]))
        summary = "  ".join(
            f"{digit}:{100*b/(b+g):.0f}%" for digit, b, g in worst[:6])
        print(f"\n{name[:44]}\n    share of a digit's appearances that fall "
              f"in a FAILED clock read:\n    {summary}")

    if arguments.by_minute:
        by_minute_table(per_minute)


def by_minute_table(per_minute):
    """Each run's clock rate per GAME minute, side by side.

    The x-axis is the game's own clock, so recordings of one game line up
    however far apart their frame numbers are and whatever was paused. A
    column that is poor in every run is the GAME being hard to read at that
    moment - a fight, a dark map, the feed full. A column poor in one run
    only is that run's HUD, which is the thing worth chasing.
    """
    runs = list(per_minute)
    covered = sorted({minute for run in runs for minute in per_minute[run]
                      if minute >= 0})
    if not covered:
        return
    print("\nclock read rate per GAME minute "
          "(frame numbers do not line up; the clock does)")
    width = max(len(name) for name in runs)
    header = "  ".join(f"{minute:>4}" for minute in covered)
    print(f"{'minute':{width}}  {header}")
    for name in runs:
        cells = []
        for minute in covered:
            bucket = per_minute[name].get(minute)
            if not bucket or not bucket["frames"]:
                cells.append("   -")
            else:
                cells.append(
                    f"{100*bucket['clock']/bucket['frames']:3.0f}%")
        print(f"{name[:width]:{width}}  " + "  ".join(cells))


if __name__ == "__main__":
    main()
