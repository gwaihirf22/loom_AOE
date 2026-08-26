"""
Loom - every recorded game against what the match was actually told to do.

Every other gate scores ONE crop of pixels: `digit_report --check` asks
whether a clock crop is the right number, `notif_report --check` whether a
band of pixels spells the right words. Neither can see the bug that cost
the most, which lives in the relationship between consecutive crops - a
reader that answers correctly most of the time and wobbles the rest turns
one lingering `--Barracks Built--` into eleven barracks and passes every
per-frame gate on the way.

This is the missing axis, run over the whole stats folder. For each game
with a recorded game attached it asks: did Loom read MORE of a thing than
the player ever ordered? That cannot happen, so every such row is a reader
counting one thing twice.

    python -m tools.reader_sweep
    python -m tools.reader_sweep --since 2026-08-25   # after a reader fix
    python -m tools.reader_sweep --enrich             # attach records first

THREE THINGS THAT WOULD MAKE THIS LIE, AND WHAT IS DONE ABOUT EACH.

* **Mixing reader generations.** Three notification fixes landed on
  2026-08-25 and took one game from +16 over the record to 0. A sweep that
  pools files from both sides of that reports a bug that is already fixed,
  and someone spends a day on it. meta.loom cannot separate them - the
  version moves on releases, and every file written that day says 1.0.5 on
  both sides. So this groups by meta.commit where a file has one, falls
  back to a DATE boundary where it does not, and PRINTS the boundary it
  used. Ugly and honest beats tidy and wrong.

* **Counting the uncountable.** The game never prints `--Farm Built--` -
  eight games of corpus, 900 lines, zero of them - so a farm is in the
  record and can never be in the feed. Counting it would report a
  permanent 60-against-0 that is not a bug. `NOT_RULED_ON` is where that
  list lives and this defers to it rather than keeping a second one.

* **Trusting a broken clock.** 64 stats files hold a clock that misread.
  Their counts are not clock-derived and are mostly still usable, but the
  file is known-bad and saying so beside its numbers is cheaper than
  someone rediscovering it.

Only ONE direction is proof. Reading more than was ordered is impossible.
Reading FEWER is legal - a foundation can be cancelled, a research
abandoned - so the record's count is a CEILING, and `events.UNREAD` is
reported without being called an error.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections

from loom import events, paths, replay, statsview
from tools.replay_truth import NOT_RULED_ON


def sweep(since=None, enrich=False):
    """Every stats file that can be compared, and what it disagrees on."""
    rows = []
    for path in sorted(paths.STATS_DIR.glob("*.json")):
        data = statsview.load_stats(path)
        if data is None:
            continue
        if since and path.name[:10] < since:
            continue
        if enrich and not data.get("record"):
            statsview.enrich_with_record(path)
            data = statsview.load_stats(path)
        found = statsview.accuracy_rows(data)
        if found is None:
            continue
        over = [d for d in found["overfired"] if d.subject not in NOT_RULED_ON]
        unread = [d for d in found["unread"] if d.subject not in NOT_RULED_ON]
        rows.append({
            "path": path,
            "commit": (data.get("meta") or {}).get("commit"),
            "loom": (data.get("meta") or {}).get("loom", "?"),
            "clock": statsview.clock_faults(data),
            "over": over,
            "unread": unread,
        })
    return rows


def report(rows, since):
    """Print the sweep, generation by generation."""
    if not rows:
        print("No game has a recorded game attached yet. "
              "Run with --enrich, or use the statistics window's "
              "'Add recorded game' button.")
        return 0

    # The boundary is stated, always, because a reader who does not know
    # which generations are pooled cannot read the numbers under it.
    stamped = [r for r in rows if r["commit"]]
    print(f"{len(rows)} games compared against their recorded game")
    print(f"  grouped by      : {'meta.commit' if stamped else 'date'}"
          f" ({len(stamped)} of {len(rows)} carry a commit)")
    print(f"  date boundary   : {since or 'none - ALL generations pooled'}")
    if not since and not stamped:
        print("  ! files from before and after a reader change are in one"
              " bucket. Pass --since to separate them.")

    by_group = collections.defaultdict(list)
    for row in rows:
        by_group[row["commit"] or f"{row['loom']} (no commit)"].append(row)

    worst = collections.Counter()
    for group, games in sorted(by_group.items()):
        over = sum(len(r["over"]) for r in games)
        clean = sum(1 for r in games if not r["over"])
        print(f"\n{group}  -  {len(games)} games, {clean} with nothing"
              f" over the record")
        for row in games:
            if not row["over"] and not row["clock"]:
                continue
            note = "  [!] check clock" if row["clock"] else ""
            print(f"  {row['path'].name}{note}")
            for bad in sorted(row["over"], key=lambda d: d.ceiling - d.read):
                print(f"      {bad.subject:<18} read {bad.read:>3}"
                      f"   ordered at most {bad.ceiling:>3}")
                worst[bad.subject] += bad.read - bad.ceiling

    if worst:
        print("\nover-counted most, summed across every game:")
        for subject, excess in worst.most_common(10):
            print(f"  {subject:<20} +{excess}")
    else:
        print("\nNothing was read more times than it was ordered.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split(".")[0])
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                        help="only games recorded on or after this date - "
                             "use it to sweep one reader generation")
    parser.add_argument("--enrich", action="store_true",
                        help="attach recorded games to files that have none")
    arguments = parser.parse_args(argv)
    return report(sweep(arguments.since, arguments.enrich), arguments.since)


if __name__ == "__main__":
    raise SystemExit(main())
