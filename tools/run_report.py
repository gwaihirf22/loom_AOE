"""What Loom reported for a whole game, against what the game recorded.

Every other gate Loom has scores ONE crop of pixels. `notif_report --check`
asks whether a band reads back as the right words; `digit_report --check`
asks whether a clock crop is the right number. Both can pass on every single
frame while the count for the whole game is wrong, because the bug lives in
how frames relate to EACH OTHER - one lingering `--Barracks Built--` counted
eleven times across thirteen game seconds.

This is that missing axis, and it only became possible when the recorded
game turned out to be readable. See `tools/replay_truth.py`.

**Read the two columns differently.** They are not symmetrical and treating
them as if they were is how this tool would start lying:

* **Loom OVER the record is a fault, always.** The record's counts are
  placement ORDERS, which is a ceiling - a cancelled foundation prints no
  notification, and a destroyed one prints none either. Loom cannot honestly
  exceed it. Three barracks placed and fifteen reported is not a judgement
  call.

* **Loom UNDER the record is usually not a fault at all.** The game's
  notification feed has a hard ceiling of its own: it showed
  `--Villager Created--` at most 57 times in a game with 112 villagers on
  the HUD, because a line lingers and the next one replaces it. Under-
  reporting is the feed's limit far more often than the reader's.

So a run is judged on the first column. The second is context.

    python -m tools.run_report <stats.json> <replay.aoe2record>
    python -m tools.run_report --check <stats.json> <replay.aoe2record>
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import replay_truth  # noqa: E402

# How far Loom's game length may differ from the record's before the pair is
# not the same game at all. Matching is by duration because the two clocks
# are the same clock - both are the GAME's, not a wall clock - so a real
# match agrees exactly. Anything but a few seconds means a corrupt Loom
# clock or the wrong file.
SAME_GAME_SECONDS = 10


def loom_counts(stats_path):
    """What Loom said happened, as subject -> count, plus its duration."""
    with open(stats_path, encoding="utf-8") as handle:
        stats = json.load(handle)
    game = stats.get("game") or {}
    counts = collections.Counter()
    for entry in game.get("events") or []:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            continue
        _second, name = entry
        if ":" not in str(name):
            continue
        kind, subject = str(name).split(":", 1)
        counts[(kind, subject)] += 1
    return counts, int(game.get("duration") or 0)


def repeated_technologies(reported):
    """Technologies Loom claimed more than once. Every one is a fault.

    This needs no recorded game and no ground truth of any kind - it is
    arithmetic on Loom's own output. A technology completes at most once per
    game: it can be cancelled at any point before it finishes, but a
    cancelled research is never announced, and there is nothing to research
    a second time afterwards.

    It lives here rather than in a tool of its own because it was found by
    accident while comparing against the record - and the record could not
    speak to it at all, since those technology ids are not in the small
    table replay_truth carries. The check that needed no record found what
    the check that needed one had to leave out.
    """
    return {subject: count
            for (kind, subject), count in reported.items()
            if kind == "researched" and count > 1}


def compare(stats_path, replay_path, player=1):
    """Every subject the two disagree about, and by how much."""
    reported, duration = loom_counts(stats_path)
    truth = replay_truth.harvest(replay_path, player)
    ceiling, unnamed = replay_truth.expected_notifications(truth)

    rows = []
    for subject, limit in sorted(ceiling.items()):
        # A building's notification is "built"; a technology's is a research
        # line. Count whatever Loom filed under that subject either way.
        got = sum(n for (_kind, name), n in reported.items() if name == subject)
        rows.append((subject, got, limit))
    return {
        "rows": rows,
        "repeated_techs": repeated_technologies(reported),
        "loom_duration": duration,
        "replay_duration": truth.duration,
        "unnamed_ids": unnamed,
        "not_ruled_on": sorted(replay_truth.NOT_RULED_ON),
    }


def mmss(second):
    return f"{second // 60}:{second % 60:02d}"


def describe(result):
    lines = []
    loom, record = result["loom_duration"], result["replay_duration"]
    agree = abs(loom - record) <= SAME_GAME_SECONDS
    lines.append(f"  Loom ran {mmss(loom)}, the record says {mmss(record)}"
                 + ("  (same game)" if agree else
                    f"  <- {abs(loom - record)}s APART, so one of these is "
                    "wrong before any count is compared"))

    over = [r for r in result["rows"] if r[1] > r[2]]
    under = [r for r in result["rows"] if r[1] < r[2]]
    exact = [r for r in result["rows"] if r[1] == r[2]]

    lines.append(f"\n  OVER the record - a fault every time ({len(over)}):")
    if not over:
        lines.append("      none")
    for subject, got, limit in sorted(over, key=lambda r: r[2] - r[1]):
        lines.append(f"      {subject:<18} Loom {got:>3}   record {limit:>3}"
                     f"   +{got - limit}")

    lines.append(f"\n  under the record - usually the feed's limit, "
                 f"not the reader's ({len(under)}):")
    for subject, got, limit in sorted(under, key=lambda r: r[1] - r[2])[:12]:
        lines.append(f"      {subject:<18} Loom {got:>3}   record {limit:>3}")

    repeats = result["repeated_techs"]
    lines.append(f"\n  technologies claimed more than once - "
                 f"impossible, and needs no record to know it "
                 f"({len(repeats)}):")
    if not repeats:
        lines.append("      none")
    for subject, count in sorted(repeats.items(), key=lambda kv: -kv[1]):
        lines.append(f"      {subject:<24} {count} times")

    lines.append(f"\n  agreed exactly ({len(exact)}): "
                 + (", ".join(s for s, _g, _l in exact) or "none"))

    if result["not_ruled_on"]:
        lines.append(f"\n  not ruled on: {', '.join(result['not_ruled_on'])}")
    if result["unnamed_ids"]:
        lines.append(f"  {len(result['unnamed_ids'])} record ids have no name "
                     "here and were left out of the comparison entirely")
    return "\n".join(lines)


def faults(result):
    """The complaints that make a run fail. Empty means it passed."""
    problems = []
    loom, record = result["loom_duration"], result["replay_duration"]
    if abs(loom - record) > SAME_GAME_SECONDS:
        problems.append(f"Loom's clock says {mmss(loom)} where the record "
                        f"says {mmss(record)} - {abs(loom - record)}s apart")
    for subject, count in sorted(result["repeated_techs"].items()):
        problems.append(f"{subject}: claimed {count} times, and a "
                        "technology completes at most once in a game")
    for subject, got, limit in result["rows"]:
        if got > limit:
            problems.append(f"{subject}: Loom reported {got}, the game was "
                            f"only ordered to build {limit}")
    return problems


def main():
    parser = argparse.ArgumentParser(
        description="compare one Loom game against the game's own record")
    parser.add_argument("stats", help="a stats/*.json file Loom wrote")
    parser.add_argument("replay", help="the matching .aoe2record")
    parser.add_argument("--player", type=int, default=1)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if Loom over-reported anything")
    arguments = parser.parse_args()

    for path in (arguments.stats, arguments.replay):
        if not os.path.exists(path):
            print(f"no such file: {path}", file=sys.stderr)
            return 2

    result = compare(arguments.stats, arguments.replay, arguments.player)
    print(os.path.basename(arguments.stats))
    print(describe(result))

    problems = faults(result)
    if arguments.check:
        if not problems:
            print("\n  nothing over the record")
            return 0
        print(f"\n  {len(problems)} fault(s):")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
