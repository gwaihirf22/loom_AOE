"""Ground truth for a game, read out of the recorded game file.

Every gate Loom has scores ONE crop. `notif_report --check` asks whether a
single band of pixels reads back as the right words; `digit_report --check`
asks whether a single clock crop is the right number. Neither can see the
class of bug that actually hurt: a reader that answers correctly most of the
time and wobbles the rest passes every per-frame gate and still turns one
lingering `--Barracks Built--` into eleven barracks.

This is the missing axis. The game writes down what it was told to do, so a
recorded game says how many barracks there really were, when each age was
started, and how many villagers were queued. That is the ruler for whole-run
counts.

Three things to know before trusting a number out of here:

* **The body is read, not the header.** The header is a metadata blob of
  civilisations and player names, and `mgz` is behind on its layout - it
  cannot parse save version 67.2 or 68.0 at all (`--header-note` explains
  the one-line fix). The body is framed separately: the first four bytes
  give the header's length and I seek straight past it. Measured over every
  recorded game on this machine, 42 of 42 at save version 68.0 parse this
  way, including the eleven whose header does not.

* **These are ORDERS, not completions.** The game records the command a
  player issued. A foundation can be cancelled, a building destroyed while
  it goes up, a technology cancelled seconds after it starts - in the game
  I built this against, `Loom` was ordered twice, at 06:13 and 08:22,
  because the first was cancelled. The notification feed announces
  COMPLETIONS. Those are different events, and the distance between them is
  exactly the kind of gap that must not be papered over: an order is
  evidence about intent, not about the world. See `expected_notifications`.

* **Each source answers a DIFFERENT question, and they barely overlap.**
  The recorded game says when a building was PLACED. The notification feed
  says when it was BUILT. The production queue says when something was
  QUEUED - and buildings never enter the queue at all, so it says nothing
  about them. Three clocks on three different moments.

  What follows from that is the useful part. Placement time is not a rival
  reading of the same fact the feed reports, so it is not evidence for or
  against the notification reader; subtract it FROM the completion and it
  gives how long the building took, which is otherwise something Loom would
  have to infer. Where the recorded game does act as a ruler is the QUEUE:
  a player's order to train is exactly what the queue reader claims to see.
  And completions are the ruler for the notification reader. Aiming either
  ruler at the other reader measures nothing.

* **A recorded game holds BOTH players' orders.** Everything the opponent
  did is in here, including what fog would hide. That is fine for a gate run
  offline against a finished game and it is cheating if it ever reaches a
  live overlay. Nothing in this file is imported by `loom/`, and it should
  stay that way.

Usage:

    python -m tools.replay_truth "<path to .aoe2record>"
    python -m tools.replay_truth "<path>" --json     # for a gate to read
    python -m tools.replay_truth x --header-note     # the mgz gap, explained
"""

import argparse
import collections
import io
import json
import os
import struct
import sys

from loom.replay import (AGE_TECHS, BUILDINGS, TECHNOLOGIES, Truth, UNITS,
                         harvest, operations)


HEADER_NOTE = """\
mgz 1.8.51 - the latest, released 2026-02 - stops at save version 66.3 and
cannot read a 67.2 or 68.0 header. The game is on 68.0.

The cause is one extra `de_string` appended to each player record. mgz reads
the record to its end and then expects the next player's `dlc_id` where a
string marker now sits, so it fails on the FIRST player of eight. Three
different player shapes confirm it: a human record ends at +107, an AI one
at +207, an empty slot at +239, and there is an empty `de_string` at every
one of those bytes. Skip it and the next player's `ai_type` lands exactly
where it should.

The fix is two lines, after the `save >= 64.3` read in
`mgz.fast.header.parse_de`:

    if save >= 67.2:
        de_string(data)

Measured against this machine's archive: 67.2 goes from 0/6 files to 6/6,
68.0 from 0/6 to 5/6, and nothing older changes. The eleven 68.0 files still
failing raise `IndexError` further on and are a second, separate gap.

None of this touches the body, so none of it touches the numbers this tool
reports. It is worth sending upstream, not worth waiting for."""


# Object and technology ids the game uses. I have only put in what this
# archive corroborates - the first-placement times line up with a real build
# order (house at 00:06, lumber camp at 01:55, mill at 04:10, barracks at
# 07:33, farms from 09:40) and the age technologies land at the ages that
# game reached. An id I have not confirmed is reported by NUMBER rather than
# guessed at a name, because a wrong name here would be a fabricated fact in
# the one file whose whole job is to be trusted.
# Subjects this file names but refuses to rule on.
#
# A farm is reseeded on the tile it already occupies and rebuilt after it is
# destroyed, and an order stream cannot tell either of those from a farm
# raised for the first time - this game shows 60 orders across 57 distinct
# tiles and there is no way in here to know how many of them printed a line.
# So farms are harvested and reported like everything else, and deliberately
# left OUT of the ruler. A number nobody can check is worse than an absent
# one, because it would be believed at exactly the same weight as a number
# that can be.
NOT_RULED_ON = {"farm"}


def expected_notifications(truth):
    """How many times should the game's feed have printed each phrase?

    This is the number Loom's counts are measured against, so it has to be
    derived from orders honestly rather than optimistically. Return a dict
    of phrase subject -> count, e.g. {"barracks": 3, "town_center": 2}.
    """
    notifications = {}  # name -> count, the answer
    skipped = []        # ids I have no name for (we will want to have names for all ids for this to work on all games)
    # Process buildings
    for building_id, placements in truth.builds.items():
        if building_id not in BUILDINGS:
            #remember it in skipped
            skipped.append(building_id)
            continue
        name = BUILDINGS[building_id]
        if name in NOT_RULED_ON:
            continue
        count = len(placements)
        notifications[name] = notifications.get(name, 0) + count
    # Process researches
    for tech_id, times in truth.researches.items():
        if tech_id not in TECHNOLOGIES:
            skipped.append(tech_id)
            continue
        name = TECHNOLOGIES[tech_id]
        notifications[name] = notifications.get(name, 0) + 1    #repeated orders in technologies means it had to have been cancelled
    return notifications, skipped


def mmss(second):
    return f"{second // 60:02d}:{second % 60:02d}"


def describe(truth):
    lines = [f"player {truth.player} | game ran {mmss(truth.duration)}"]

    lines.append("\n  buildings PLACED (an order, not a completion)")
    rows = sorted(truth.builds.items(), key=lambda kv: kv[1][0][0])
    for ident, placements in rows:
        spots = {(x, y) for _second, x, y in placements}
        lines.append(f"    {truth.name_of_building(ident):<16} "
                     f"orders {len(placements):>3}   distinct spots "
                     f"{len(spots):>3}   first {mmss(placements[0][0])}")

    lines.append("\n  technologies ORDERED")
    for ident, times in sorted(truth.researches.items(),
                               key=lambda kv: kv[1][0]):
        when = ", ".join(mmss(t) for t in times)
        again = "   <- ordered twice, so one was cancelled" if len(times) > 1 else ""
        lines.append(f"    {truth.name_of_tech(ident):<20} {when}{again}")

    lines.append("\n  units QUEUED (an upper bound on units trained)")
    for ident, total in truth.queued.most_common():
        lines.append(f"    {truth.name_of_unit(ident):<16} {total}")

    counts, skipped = expected_notifications(truth)
    lines.append("\n  what the feed SHOULD have printed (the ruler)")
    for name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {name:<16} {count}")
    if NOT_RULED_ON:
        lines.append(f"    -- not ruled on: {', '.join(sorted(NOT_RULED_ON))}")
    if skipped:
        # Said out loud rather than dropped. A hand-curated list of ids fails
        # SILENTLY - the glyphs.KNOWN_WORDS lesson - so the ruler reports what
        # it could not name instead of quietly measuring less than it claims.
        lines.append(f"    -- {len(skipped)} ids have no name here and were "
                     f"left out: {', '.join(str(i) for i in sorted(skipped))}")

    ages = truth.ages()
    if ages:
        lines.append("\n  ages STARTED (add the research time for arrival)")
        for name, second in sorted(ages.items(), key=lambda kv: kv[1]):
            lines.append(f"    {name:<16} {mmss(second)}")
    return "\n".join(lines)


def as_json(truth):
    return {
        "player": truth.player,
        "duration": truth.duration,
        "buildings_placed": {
            truth.name_of_building(ident): {
                "orders": len(placements),
                "distinct_spots": len({(x, y) for _s, x, y in placements}),
                "first": placements[0][0],
                "times": [second for second, _x, _y in placements],
            }
            for ident, placements in truth.builds.items()
        },
        "technologies_ordered": {
            truth.name_of_tech(ident): times
            for ident, times in truth.researches.items()
        },
        "units_queued": {
            truth.name_of_unit(ident): total
            for ident, total in truth.queued.items()
        },
        "ages_started": truth.ages(),
    }


def main():
    parser = argparse.ArgumentParser(
        description="ground truth for a game, from its recorded game file")
    parser.add_argument("replay", help="path to an .aoe2record file")
    parser.add_argument("--player", type=int, default=1,
                        help="player number to report on (default 1)")
    parser.add_argument("--json", action="store_true",
                        help="print machine-readable output for a gate")
    parser.add_argument("--header-note", action="store_true",
                        help="explain the mgz gap and its fix, then stop")
    args = parser.parse_args()

    if args.header_note:
        print(HEADER_NOTE)
        return 0
    if not os.path.exists(args.replay):
        print(f"no such recorded game: {args.replay}", file=sys.stderr)
        return 1

    truth = harvest(args.replay, args.player)
    if args.json:
        print(json.dumps(as_json(truth), indent=2, sort_keys=True))
    else:
        print(os.path.basename(args.replay))
        print(describe(truth))
    return 0


if __name__ == "__main__":
    sys.exit(main())
