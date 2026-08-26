"""
Loom — score the digit readers against saved games, and gate a change.

    python -m tools.digit_report                 # report + rewrite baseline
    python -m tools.digit_report --check         # fail on a regression

The sibling of tools/notif_report.py, and it exists for the same reason
that one does: the notification font had a committed baseline and a gate
for months while the DIGITS - the villager count, the clock, the
population - had neither, so a change to a template or a threshold could
only be judged by playing a game and squinting.

There is no transcription to check against here and none is needed. A
capture is a game, a game's clock only ever goes forwards, and the digit
readers are the only thing being changed - so what is measured is HOW MUCH
each band reads, per run, over anchored frames. A change that reads more
without reading anything impossible is an improvement.

Impossible is the important half, because "reads more" alone is exactly
how a bad template set gets accepted. Every reading is checked against the
game's own arithmetic:

  * the clock must not go BACKWARDS, and must not jump further than the
    capture interval could carry it;
  * the villager count must not swing wildly between neighbouring frames;
  * the population must not exceed its own cap.

Those are cheap and they are what separates a reader that improved from
one that started guessing. A wrong clock desynchronises the whole build,
and until now nothing measured it at all.

RUNS is the set of captures scored. It is deliberately small and fixed:
these are matched recordings of ONE game across both HUD skins with and
without the transparent-UI mod, so a change can be seen on all four at
once and a fix that helps one skin at another's expense cannot hide.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import re
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import anchor, digits, hud, paths, queue, reader, session  # noqa: E402

BASELINE = paths.PROJECT_ROOT / "tools" / "digit_baseline.txt"

# Every third frame: the readers do not change between neighbouring frames
# and this keeps a four-run sweep to minutes rather than an hour.
STRIDE = 3

# How far the clock may legitimately move between two SCORED frames before
# the jump is called impossible. The capture interval is two wall seconds
# and the game runs at up to 1.7x, so three frames is about ten game
# seconds; sixty is loose enough for a pause or a dropped frame and tight
# enough to catch a misread minutes field, which moves in sixties.
MAX_CLOCK_JUMP = 60

# A clock that falls back to near zero is a NEW GAME, not a misread, and
# calling it impossible was training the reader of this report to skip the
# impossible list - which is the one part of it that matters. The threshold
# is session.py's own, imported rather than copied so the two cannot drift:
# that module is what decides the same question live.
#
# Reported all the same, just not as a fault. A run that silently swallowed
# a restart would be hiding the most interesting frame in it.
NEW_GAME_CLOCK = session.GameSession().new_game_clock_limit

# Villagers can fall - boar kills, a drush, losing a fight - so this is not
# monotonic. It is a bound on the SIZE of a swing between scored frames.
MAX_VILLAGER_SWING = 10


def score_run(run_dir):
    """Read every band of a run. Returns (counts, complaints)."""
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))[::STRIDE]
    templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    glyph_templates = digits.load_digit_templates()

    counts = {"frames": len(frames), "anchored": 0, "clock": 0,
              "villagers": 0, "population": 0, "impossible": 0,
              "new games": 0}
    complaints = []
    last_clock = last_villagers = None
    for path in frames:
        image = cv2.imread(path)
        if image is None:
            continue
        found = anchor.identify_hud(image, templates, wood_templates=woods)
        if not found or found["score"] < reader.MIN_ANCHOR_SCORE:
            continue
        counts["anchored"] += 1
        narrow = reader.min_glyph_width(found["scale"], found["profile"])
        wide = reader.max_glyph_width(found["scale"], found["profile"])
        name = os.path.basename(path)

        x1, y1, x2, y2 = found["clock_band"]
        seconds, _ = digits.read_clock_seconds(
            image[max(0, y1):y2, max(0, x1):x2], glyph_templates, narrow)
        if seconds is not None:
            counts["clock"] += 1
            if last_clock is not None:
                step = seconds - last_clock
                if step < 0 and seconds <= NEW_GAME_CLOCK:
                    counts["new games"] += 1
                    # The villager count resets with the clock, so the swing
                    # on this same frame is the same event, not a second
                    # fault. Counting it twice made a restart look like two
                    # misreads and inflated every run containing one.
                    last_villagers = None
                    complaints.append(f"{name}: a NEW GAME started "
                                      f"({last_clock}s -> {seconds}s)")
                elif step < 0:
                    counts["impossible"] += 1
                    complaints.append(f"{name}: clock went BACKWARDS "
                                      f"{last_clock} -> {seconds}")
                elif step > MAX_CLOCK_JUMP:
                    counts["impossible"] += 1
                    complaints.append(f"{name}: clock JUMPED "
                                      f"{last_clock} -> {seconds}")
            last_clock = seconds

        x1, y1, x2, y2 = found["villagers"]
        count, _ = digits.read_count(
            image[max(0, y1):y2, max(0, x1):x2], glyph_templates, narrow)
        if count is not None:
            counts["villagers"] += 1
            if (last_villagers is not None
                    and abs(count - last_villagers) > MAX_VILLAGER_SWING):
                counts["impossible"] += 1
                complaints.append(f"{name}: villagers SWUNG "
                                  f"{last_villagers} -> {count}")
            last_villagers = count

        x1, y1, x2, y2 = found["population"]
        pop = digits.read_population(
            image[max(0, y1):y2, max(0, x1):x2], glyph_templates, narrow,
            wide)
        if pop and pop[0] is not None:
            counts["population"] += 1
            if pop[1] is not None and pop[0] > pop[1]:
                counts["impossible"] += 1
                complaints.append(f"{name}: population OVER CAP "
                                  f"{pop[0]}/{pop[1]}")
    return counts, complaints


def report(runs):
    lines = []
    for run_dir in runs:
        counts, complaints = score_run(run_dir)
        anchored = counts["anchored"] or 1
        lines.append(
            f"{os.path.basename(run_dir)}: {counts['frames']} scored | "
            f"anchored {counts['anchored']} | "
            f"clock {counts['clock']}/{anchored} | "
            f"villagers {counts['villagers']}/{anchored} | "
            f"population {counts['population']}/{anchored} | "
            f"impossible {counts['impossible']} | "
            f"new games {counts['new games']}")
        for complaint in complaints[:8]:
            lines.append(f"    {complaint}")
    return lines


SUMMARY = re.compile(
    r"^(?P<run>\S+): (?P<frames>\d+) scored \| anchored (?P<anchored>\d+)"
    r" \| clock (?P<clock>\d+)/\d+ \| villagers (?P<villagers>\d+)/\d+"
    r" \| population (?P<population>\d+)/\d+"
    r" \| impossible (?P<impossible>\d+)"
    r"(?: \| new games (?P<new_games>\d+))?")


def scores(body):
    found = {}
    for line in body.splitlines():
        match = SUMMARY.match(line)
        if match:
            found[match.group("run")] = {
                key: int(match.group(key)) for key in
                ("frames", "anchored", "clock", "villagers", "population",
                 "impossible")}
    return found


def check(body):
    """Complaints about a fresh report against the committed baseline.

    Two rules, in the order they matter, and they mirror the notification
    gate's: an IMPOSSIBLE reading must never rise, because a clock that
    goes backwards is worse than a clock that says nothing; and no band
    may read less than it did.
    """
    if not BASELINE.exists():
        return ["no committed baseline to check against"]
    before, after = scores(BASELINE.read_text(encoding="utf-8")), scores(body)
    complaints = []
    for run, now in sorted(after.items()):
        was = before.get(run)
        if was is None:
            continue
        if now["impossible"] > was["impossible"]:
            complaints.append(f"{run}: IMPOSSIBLE READINGS "
                              f"{was['impossible']} -> {now['impossible']}")
        for band in ("clock", "villagers", "population"):
            if now[band] < was[band]:
                complaints.append(
                    f"{run}: {band} {was[band]} -> {now[band]}")
    return complaints


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", nargs="*",
                        help="run names (default: whatever the baseline "
                             "already scores)")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed baseline and "
                             "exit non-zero on a regression")
    arguments = parser.parse_args()

    wanted = arguments.runs
    if not wanted and BASELINE.exists():
        wanted = list(scores(BASELINE.read_text(encoding="utf-8")))
    if not wanted:
        print("name the runs to score - there is no baseline yet")
        return
    runs = []
    for pattern in wanted:
        runs.extend(sorted(glob.glob(str(paths.CAPTURES_DIR / pattern))))
    runs = [r for r in runs if os.path.isdir(r)]
    if not runs:
        print(f"no capture runs matching {wanted}")
        return

    body = "\n".join(report(runs)) + "\n"
    print(body, end="")
    if arguments.check:
        complaints = check(body)
        if complaints:
            print("\nREGRESSION - the baseline is left alone:")
            for complaint in complaints:
                print(f"    {complaint}")
            sys.exit(1)
        print("\nno regression against the committed baseline")
        return
    BASELINE.write_text(body, encoding="utf-8")
    print(f"\nbaseline written to {BASELINE}")


if __name__ == "__main__":
    main()
