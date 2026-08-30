"""
Loom — how long the player's own buildings actually take.

`loom/durations.py` carries the GAME's numbers: one-villager build times
and exact research times. They are right about the game and wrong about
almost every player, because a building's time divides among however many
villagers help and nobody builds with one. Measured on this machine, a
Castle listed at 200 seconds came in at 75, 123 and 175 across nine games
- somebody who puts two or three villagers on a Castle really does build
them in half the book time, every time.

That matters because the build report judges an item against a WINDOW,
and the window is padded by the expected build time. Padding it with a
number nobody plays at makes a prompt player read late.

    python -m tools.measure_durations            # report only
    python -m tools.measure_durations --write    # keep it

WHAT THIS MAY AND MAY NOT OVERRIDE, which is the whole design.

BUILDINGS: yes. The book number is a ceiling nobody plays at, and the
player's own median is the better prediction of their next one.

TECHNOLOGIES: never. Research does not divide, so the book value IS the
truth - and a measured figure can only be that plus queue time, because a
Blacksmith already busy makes the next technology wait and the wait is
indistinguishable from the research here. The evidence for that is the
minimums: across 61 games they match the table EXACTLY for horse_collar,
gold_mining, bodkin_arrow, fletching, ballistics, husbandry, iron_casting,
bracer, wheelbarrow and every armour line. Where a minimum sits above the
table - hand_cart 111 against 55, chain_barding_armor 134 against 60 -
that is a technology this player has never once researched at an idle
building, not a table that is wrong. Both are reported so the difference
can be looked at; neither is written.

The measurement itself comes from `events.build_durations`, which already
refuses a subject Loom did not count exactly right: pairing orders to
completions in sequence drifts as soon as one completion is missed, and a
drifted pair looks exactly like a slow build.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import json
import statistics

from loom import durations, events, paths, statsview


def gather():
    """{(subject, kind): [seconds, ...]} from every enriched game."""
    found = collections.defaultdict(list)
    games = 0
    for path in sorted(paths.STATS_DIR.glob("*.json")):
        data = statsview.load_stats(path)
        if not (data or {}).get("record"):
            continue
        games += 1
        sightings = events.with_record(
            data.get("game") or {},
            statsview.truth_from_section(data["record"]))
        for taken in events.build_durations(sightings):
            found[(taken.subject, taken.kind)].append(taken.seconds)
    return found, games


def personal_times(samples):
    """{building: seconds} worth keeping, and why the rest were not.

    The MEDIAN rather than the mean: one Castle begun with a single
    villager while the rest went up with three would drag a mean towards
    a game that happened once, and the question being asked is what
    usually happens.
    """
    kept, thin = {}, {}
    for (subject, kind), values in samples.items():
        if kind == "technology":
            continue
        if len(values) < durations.ENOUGH_SAMPLES:
            thin[subject] = len(values)
            continue
        kept[subject] = round(statistics.median(values))
    return kept, thin


def report(samples, games, kept, thin):
    print(f"{games} games with a recorded game attached\n")
    print("BUILDINGS - the player's own median replaces the book number")
    print(f"  {'subject':<18}{'n':>3}{'min':>6}{'median':>8}{'max':>6}"
          f"{'book':>7}")
    for (subject, kind), values in sorted(samples.items()):
        if kind == "technology" or subject not in kept:
            continue
        book = durations.build_or_research_time(subject, personal=False)
        print(f"  {subject:<18}{len(values):>3}{min(values):>6}"
              f"{kept[subject]:>8}{max(values):>6}{book or '-':>7}")
    if thin:
        print(f"\n  not enough games yet (need {durations.ENOUGH_SAMPLES}): "
              + ", ".join(f"{s}x{n}" for s, n in sorted(thin.items())))

    print("\nTECHNOLOGIES - never overridden; the MINIMUM should match the"
          " book,\nand anything above it is a building that was already"
          " busy")
    for (subject, kind), values in sorted(samples.items()):
        if kind != "technology" or len(values) < 2:
            continue
        book = durations.build_or_research_time(subject, personal=False)
        low = min(values)
        if not book:
            note = "no book value"
        elif low == book:
            note = "confirms the table"
        elif low > book:
            note = f"never seen idle (+{low - book}s)"
        else:
            # Impossible: research cannot finish faster than it takes.
            # Worth shouting about rather than averaging away.
            note = f"UNDER the table by {book - low}s - look at this"
        print(f"  {subject:<22}{len(values):>3} min {low:>4}  book"
              f" {book or '-':>4}   {note}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split(".")[0])
    parser.add_argument("--write", action="store_true",
                        help="save the building times beside the statistics")
    arguments = parser.parse_args(argv)

    samples, games = gather()
    if not samples:
        print("No game has a recorded game attached yet - nothing to"
              " measure. Attach one on the Reader accuracy tab.")
        return 0
    kept, thin = personal_times(samples)
    report(samples, games, kept, thin)

    if not arguments.write:
        print("\n(report only - pass --write to keep it)")
        return 0
    path = paths.DATA_DIR / durations.MEASURED_PATH_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"games": games, "buildings": kept}, indent=1) + "\n",
        encoding="utf-8")
    print(f"\nwrote {len(kept)} building times to "
          f"{paths.for_display(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
