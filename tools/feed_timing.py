"""How long the game leaves a notification line on screen.

Every timing constant in `notifications.py` and `glyphs.TEXT_COOLDOWN_SECONDS`
was measured with the game's notification duration set to its SHORTEST.
That setting is not the default, and most players will never touch it, so
constants tuned there silently over-count for everyone else - a line that
outlives a cooldown gets counted again, and one lingering `--Barracks Built--`
became eleven barracks across thirteen game seconds.

This measures the real thing, at whatever duration the game was actually set
to when the run was recorded.

**Read a BUSY period and you measure the panel, not the duration.** The feed
holds a few lines; when a new one arrives the oldest is pushed off, so during
a rush every line is cut short and the numbers come out far too low. A line
lives its full life only when nothing arrives to displace it. So this reports
the distribution AND, separately, only those appearances that ended while the
panel still had room - those are the ones that expired on their own.

**Two clocks, and they are not interchangeable.** The game's notification
duration is a real-time setting, but every constant it feeds is compared
against Loom's GAME clock, which runs at the game's speed - 1.7x in
multiplayer. Both are reported, along with the speed implied by the frames
themselves, because using the wrong one is a 70% error.

The resolution is the capture interval: a run grabbed every 2 wall seconds
cannot resolve a linger to better than that. It does not need to - the
question is whether the answer is nearer 10 seconds or nearer 40.

    python -m tools.feed_timing "run_2026*bul*"
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

from loom import (anchor, digits, glyphs, hud, notifications,  # noqa: E402
                  paths, queue, reader)

# A text missing from ONE look between two sightings is the reader blinking,
# not the line leaving and coming back. Measured elsewhere: a readable line
# is read on about 83% of looks, so a single-frame hole is the common case
# and welding across it is right. Two holes in a row is treated as gone -
# that is a 3% coincidence and it has to end somewhere.
BLINK_FRAMES = 1

# The panel is considered to have had ROOM if it held fewer than this many
# lines when an appearance ended. A line that expired with room to spare
# was not pushed off by anything, so it lived its whole life.
ROOM_BELOW_LINES = 4

# The game's speed, used only to turn game seconds into wall seconds. The
# notification duration is a real-time setting; every constant it feeds is
# compared against Loom's GAME clock. 1.7x is what multiplayer runs at and
# what the recorded games here report (1.69).
#
# It is NOT derived from the frames, though it looks like it could be:
# dividing game seconds per frame by the requested interval gave 2.00x,
# because grab_frames sleeps 2 seconds and then spends time capturing, so
# the real interval is longer than the one asked for. Measuring the game's
# speed with a clock that includes the measurer's own work is how you get a
# game running 18% faster than it does.
GAME_SPEED = 1.7


def looks(run_dir):
    """Every frame in a run, as (game seconds, the texts on the feed)."""
    templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    glyph_templates = digits.load_digit_templates()
    font = glyphs.load_font()

    for path in sorted(glob.glob(os.path.join(run_dir, "frame_*.png"))):
        image = cv2.imread(path)
        if image is None:
            continue
        found = anchor.identify_hud(image, templates, wood_templates=woods)
        if not found or found["score"] < reader.MIN_ANCHOR_SCORE:
            continue
        narrow = reader.min_glyph_width(found["scale"], found["profile"])
        x1, y1, x2, y2 = found["clock_band"]
        clock, _ = digits.read_clock_seconds(
            image[max(0, y1):y2, max(0, x1):x2], glyph_templates, narrow)
        if clock is None:
            continue
        px1, py1, px2, py2 = notifications.panel_region(image.shape[1],
                                                        image.shape[0])
        panel = image[py1:py2, px1:px2]
        bands = glyphs.find_lines(panel, scale=found["scale"])
        texts = set()
        for (top, bottom) in bands:
            text, _ = glyphs.read_line(panel[top:bottom], font,
                                       found["scale"], found["profile"].name)
            if text:
                texts.add(text)
        yield clock, texts, len(bands)


def appearances(timeline):
    """Each unbroken stretch a text was on the feed.

    Returns (text, first game second, last game second, lines on the panel
    when it ended). A stretch ends when the text has been ABSENT for more
    looks than a blink accounts for - never on elapsed time, because the
    poll rate is not a clock.
    """
    open_now = {}                 # text -> [first, last, misses, bands]
    done = []
    for clock, texts, bands in timeline:
        for text in list(open_now):
            if text in texts:
                open_now[text][1] = clock
                open_now[text][2] = 0
                open_now[text][3] = bands
            else:
                open_now[text][2] += 1
                if open_now[text][2] > BLINK_FRAMES:
                    first, last, _misses, at_end = open_now.pop(text)
                    done.append((text, first, last, at_end))
        for text in texts:
            if text not in open_now:
                open_now[text] = [clock, clock, 0, bands]
    for text, (first, last, _m, at_end) in open_now.items():
        done.append((text, first, last, at_end))
    return done


# A research line is the cleanest possible sample, and it is a fact about
# the GAME rather than a heuristic about the data: a technology completes at
# most once per game, so a research line can never be a reprint of itself.
# "Printed only once in this run" is a weaker version of the same idea - it
# is true of the sample rather than guaranteed by the rules - and a unit
# line can satisfy it by accident.
RESEARCH_MARK = "Research Complete"


def research_lines(found):
    """Appearances of a technology completing. Never a reprint, by the rules."""
    return [row for row in found if RESEARCH_MARK in row[0]]


def only_printed_once(timeline, found):
    """Appearances of a text the whole run only ever showed once.

    This is the correction that matters. A text that is printed AGAIN while
    the previous copy is still up looks, from outside, like one very long
    linger - `--Knight Created--` measured 231 game seconds that way, which
    is not a duration, it is thirty knights. A unique event cannot be a
    reprint, so its appearance is its real life.

    It is the same instruction as "find a quiet spot in the game", made
    exact: instead of picking a stretch of time by eye, pick the lines that
    provably had nothing to weld to.
    """
    seen = collections.Counter()
    for _clock, texts, _bands in timeline:
        for text in texts:
            seen[text] += 1
    once = {text for text, rows in collections.Counter(
        row[0] for row in found).items() if rows == 1}
    return [row for row in found if row[0] in once]


def measure(run_dir):
    timeline = list(looks(run_dir))
    if len(timeline) < 2:
        return None
    steps = [b[0] - a[0] for a, b in zip(timeline, timeline[1:])
             if 0 < b[0] - a[0] < 30]
    per_frame = sorted(steps)[len(steps) // 2] if steps else 0
    found = appearances(timeline)
    return {
        "unique": only_printed_once(timeline, found),
        "research": research_lines(found),
        "looks": len(timeline),
        "game_seconds_per_frame": per_frame,
        "appearances": found,
        "span": (timeline[0][0], timeline[-1][0]),
    }


def describe(name, result, interval):
    lines = [f"\n{name}"]
    per_frame = result["game_seconds_per_frame"]
    speed = GAME_SPEED
    first, last = result["span"]
    lines.append(f"  {result['looks']} looks over {(last-first)//60}:"
                 f"{(last-first)%60:02d} of game time | {per_frame}s of game "
                 f"clock per frame, so a real interval of "
                 f"{per_frame/GAME_SPEED:.1f}s against the {interval}s asked "
                 f"for")

    found = [a for a in result["appearances"] if a[2] > a[1]]
    if not found:
        lines.append("  no line was seen on two consecutive looks")
        return "\n".join(lines)

    quiet = [a for a in found if a[3] < ROOM_BELOW_LINES]

    def spread(rows, label):
        durations = sorted(row[2] - row[1] for row in rows)
        if not durations:
            lines.append(f"  {label}: none")
            return
        n = len(durations)
        pick = lambda f: durations[min(n - 1, int(n * f))]
        wall = (lambda g: g / speed) if speed else (lambda g: 0)
        lines.append(
            f"  {label} ({n}): game seconds median {pick(0.5)}  "
            f"p75 {pick(0.75)}  p90 {pick(0.9)}  max {durations[-1]}")
        lines.append(
            f"      the same in WALL seconds: median {wall(pick(0.5)):.0f}  "
            f"p90 {wall(pick(0.9)):.0f}  max {wall(durations[-1]):.0f}")

    unique = result["unique"]
    unique_quiet = [a for a in unique if a[3] < ROOM_BELOW_LINES]
    spread(found, "every appearance - welds reprints together, so too HIGH")
    spread(quiet, "ended with room on the panel")
    spread(unique, "printed only ONCE all run - cannot be a reprint")
    spread(unique_quiet, "printed once AND ended with room")
    research = result["research"]
    spread(research, "RESEARCH lines - once per game by the rules")
    spread([a for a in research if a[3] < ROOM_BELOW_LINES],
           "research AND ended with room  <- the answer")

    longest = max(unique or found, key=lambda row: row[2] - row[1])
    lines.append(f"  longest: {longest[0]!r} for {longest[2]-longest[1]}s of "
                 f"game clock, {(longest[2]-longest[1])/speed:.0f}s of wall "
                 f"clock" if speed else "")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="how long the game leaves a notification line up")
    parser.add_argument("runs", nargs="*", default=["run_*"],
                        help="run folder names or globs")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="wall seconds between captured frames "
                             "(grab_frames' default is 2.0)")
    arguments = parser.parse_args()

    matched = []
    for pattern in arguments.runs:
        matched += sorted(glob.glob(str(paths.CAPTURES_DIR / pattern)))
    if not matched:
        print("no runs matched", file=sys.stderr)
        return 1

    every = []
    research_every = []
    for run_dir in matched:
        if not os.path.isdir(run_dir):
            continue
        result = measure(run_dir)
        if result is None:
            continue
        print(describe(os.path.basename(run_dir), result, arguments.interval))
        every += [a for a in result["appearances"] if a[2] > a[1]]
        research_every += [a for a in result["research"] if a[2] > a[1]]

    if len(matched) > 1 and research_every:
        rows = sorted(a[2] - a[1] for a in research_every
                      if a[3] < ROOM_BELOW_LINES)
        if rows:
            n = len(rows)
            print(f"\nACROSS ALL RUNS - research lines that expired with room "
                  f"on the panel ({n}). This is the number to set the "
                  f"constants from:")
            print(f"  game seconds: median {rows[n//2]}  "
                  f"p75 {rows[min(n-1, int(n*0.75))]}  "
                  f"p90 {rows[min(n-1, int(n*0.9))]}  max {rows[-1]}")
            print(f"  wall seconds: median {rows[n//2]/GAME_SPEED:.0f}  "
                  f"p90 {rows[min(n-1, int(n*0.9))]/GAME_SPEED:.0f}  "
                  f"max {rows[-1]/GAME_SPEED:.0f}")
    if len(matched) > 1 and every:
        quiet = sorted(a[2] - a[1] for a in every if a[3] < ROOM_BELOW_LINES)
        if quiet:
            n = len(quiet)
            print(f"\nACROSS ALL RUNS, appearances that expired with room on "
                  f"the panel ({n}):")
            print(f"  game seconds: median {quiet[n//2]}  "
                  f"p90 {quiet[min(n-1, int(n*0.9))]}  max {quiet[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
