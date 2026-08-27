"""Screen every recorded game for Town Centres Loom only imagined.

Every gate this project owns scores ONE crop of pixels - is this the number
7, do these pixels spell `--Barracks Built--`, is this cell washed. The bug
that cost the most lived between crops: a reader that is right 97% of the
time mints a phantom Town Centre every thirty frames, and `_tcs_queue_high`
is never lowered, so ONE bad frame bills the rest of the game.

Nothing measured that, and the data to measure it has been on disk since 18
August. Every stats file records `duration`, `tc_idle_seconds`, `tc_count`
and a per-second `idle_tcs` timeline; nothing has ever read them together.

    1.0.4  2026-08-22_18180  1281s   9025s idle   13 TCs believed
    1.0.4  2026-08-21_11151  1408s   7625s idle    8 TCs believed
    1.0.6  2026-08-26_14263  2829s   1907s idle    3 TCs believed

What to measure took a correction worth recording. The obvious number - idle
seconds over duration x tc_count - scores that last game at 22% and would NOT
have flagged the game this tool was written for, because the phantom existed
while there was ONE Town Centre and the count had reached three by the end.
Normalising by the final count hides exactly the case that matters.

The sharper signal is the LONGEST UNBROKEN idle episode. A real idle spell
ends when the player notices; a phantom cannot end, because the high-water
mark that invented it is never lowered, so only a genuine new Town Centre
starting to produce will clear it. Across 147 long games the median longest
episode is 24 seconds and the upper quartile 93. The game above ran 517
seconds without one recovering poll, and the worst 1.0.4 game ran 1206.

A player can lose a Town Centre for a minute while fighting. Five unbroken
minutes of TC IDLE is either a game worth reviewing or a reader worth fixing,
and both are worth opening.

This SCREENS, it does not gate. It cannot know how many Town Centres the
player really built - only `tools/replay_truth.py` can, from the recorded
game, and only for matches with a replay. A flag here means "open this one",
not "this is a bug".

    python -m tools.tc_audit                 # worst first
    python -m tools.tc_audit --all           # every game
    python -m tools.tc_audit --by-version    # did a release change anything
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import paths  # noqa: E402

# Longer than this without a single recovering poll and the game is worth
# opening. Taken from the distribution rather than from taste: over 147 long
# games the median longest episode is 24s and the upper quartile 93s, so this
# is about four times the quartile and catches 25 of the 147.
SUSPICIOUS_EPISODE = 300

# A game too short to contain an age-up cannot show the failure this looks
# for, and its ratios are noise - one stalled minute out of three is 33%.
MIN_DURATION = 600

# ...and one longer than this never happened. A misread clock has produced
# "games" of 36060 seconds, whose idle episodes are ten hours long and would
# otherwise sit at the top of every listing forever.
MAX_DURATION = 7200


def longest_episode(timeline):
    """The longest unbroken run of game seconds with a Town Centre idle.

    None when the record predates the timeline, which is not zero: a game
    that cannot be measured must not be reported as a clean one.
    """
    times, idle = timeline.get("t"), timeline.get("idle_tcs")
    if not times or not idle:
        return None
    longest, started = 0, None
    for when, count in zip(times, idle):
        if count > 0:
            started = when if started is None else started
            longest = max(longest, when - started)
        else:
            started = None
    return longest


def games(directory=None):
    """Every readable stats file, as the few fields this tool needs."""
    directory = directory or paths.STATS_DIR
    found = []
    for path in sorted(glob.glob(os.path.join(str(directory), "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            continue          # the player's directory; a half-written file
        game = record.get("game") or {}                    # must not stop it
        meta = record.get("meta") or {}
        duration = game.get("duration") or 0
        tcs = game.get("tc_count") or 0
        if not duration or not tcs or duration > MAX_DURATION:
            continue
        idle = float(game.get("tc_idle_seconds") or 0)
        found.append({
            "name": os.path.basename(path),
            "version": meta.get("loom", "?"),
            "hud": (meta.get("hud") or {}).get("profile", "?"),
            "duration": duration,
            "idle": idle,
            "tcs": tcs,
            # Context, not the test: the share of all Town-Centre time spent
            # idle. Reported because it is what one instinctively reaches
            # for, and shown beside the episode so the difference is visible.
            "share": idle / (duration * tcs),
            "episode": longest_episode(record.get("timeline") or {}),
        })
    return found


def suspicious(found):
    """Games worth opening: long enough to judge, and stuck idle."""
    return [game for game in found
            if game["duration"] >= MIN_DURATION
            and (game["episode"] or 0) >= SUSPICIOUS_EPISODE]


def by_version(found):
    """{version: (games, median episode)} over games long enough to judge."""
    buckets = {}
    for game in found:
        if game["duration"] >= MIN_DURATION and game["episode"] is not None:
            buckets.setdefault(game["version"], []).append(game["episode"])
    summary = {}
    for version, episodes in buckets.items():
        episodes.sort()
        summary[version] = (len(episodes), episodes[len(episodes) // 2])
    return summary


def _row(game):
    episode = "-" if game["episode"] is None else str(game["episode"]) + "s"
    return ("  {:<26}{:<7}{:<8}{:>6}{:>8.0f}{:>5}{:>8.0%}{:>10}".format(
        game["name"][:24], game["version"], game["hud"], game["duration"],
        game["idle"], game["tcs"], game["share"], episode))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--all", action="store_true",
                        help="every game, not only the suspicious ones")
    parser.add_argument("--by-version", action="store_true",
                        help="median longest idle episode per Loom version")
    parser.add_argument("--stats-dir", default=None,
                        help="where to read (default " + str(paths.STATS_DIR) + ")")
    arguments = parser.parse_args()

    found = games(arguments.stats_dir)
    where = arguments.stats_dir or paths.STATS_DIR
    if not found:
        print("no readable stats files in " + str(where))
        return 0

    judgeable = [g for g in found if g["duration"] >= MIN_DURATION]
    print("{} games, {} at least {}s and so long enough to judge".format(
        len(found), len(judgeable), MIN_DURATION))

    if arguments.by_version:
        print("\nmedian longest unbroken idle episode, per version:")
        summary = by_version(found)
        for version in sorted(summary):
            count, median = summary[version]
            print("  {:<8}{:>5} games   median {:>5}s".format(
                version, count, median))
        return 0

    shown = sorted(found if arguments.all else suspicious(found),
                   key=lambda game: -(game["episode"] or 0))
    if not shown:
        print("\nnothing suspicious - no long game held a Town Centre idle "
              "without recovering")
        return 0
    print("\n{} game(s), worst first{}".format(
        len(shown), "" if arguments.all
        else " (unbroken idle >= {}s)".format(SUSPICIOUS_EPISODE)))
    print("  {:<26}{:<7}{:<8}{:>6}{:>8}{:>5}{:>8}{:>10}".format(
        "game", "ver", "hud", "dur", "idle", "TCs", "share", "longest"))
    for game in shown:
        print(_row(game))
    print("\nA flag is a game to OPEN, not a bug. To decide, compare tc_count"
          "\nwith what the player really built - the recorded game knows, via"
          "\ntools/replay_truth.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
