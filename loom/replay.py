"""
Loom — finding the recorded game that belongs to a match Loom watched.

The game writes an `.aoe2record` for every match: a complete log of every
command every player issued. It is the only ruler Loom has that is not one
of Loom's own readers, which is exactly what makes it worth having - and
exactly why it has to be handled carefully.

**The house rule, and this module is where it moved.** CLAUDE.md said a
recorded game may be read only AFTER the game has ended, that nothing under
`loom/` may open one, and that if replay-derived statistics ever ship the
boundary moves to "only after the session has ended, and not one step
further". This is that move. The game writes `rec.aoe2record` continuously
WHILE playing and it parses perfectly, so the rule cannot be left to good
intentions: `is_finished` refuses that name and refuses a file still being
written. Reading a live record would not be a better reader, it would be a
maphack, because the file contains everything fog would have hidden.

**Two halves, and the first one is the risky one.** Locating and matching
a record is decidable from filenames and mtimes, so it is testable with no
game, no mgz and no pixels - and picking the WRONG file is the whole risk
of replay-derived statistics. Reading the body came second and is the
easier half. `tools/replay_truth.py` is now a front end over this rather
than an owner of it, so the development tool and the shipped feature can
never drift into reading records two different ways.

**What comes out is ORDERS, not events.** The game records the command a
player issued. A foundation can be cancelled and a research abandoned, so
a count out of here is a CEILING - enough to convict a reader that
over-fires, not enough to convict one that under-fires. And three sources
answer three different questions: the record says a building was PLACED,
the notification feed says it was BUILT, the queue says it was QUEUED.
Placement is not a rival reading of completion.

WHY THE MATCH IS A CONTAINMENT TEST. A record's FILENAME carries the game's
start (`... @2026.08.25 010700`) and its mtime is the end. Loom's stats
filename carries when Loom started. Measured over 265 recorded games and
315 records on the author's machine:

    nearest start time, within 10 minutes .......... 51 matched
    Loom's session falls INSIDE the record's window . 56 matched, 4 ambiguous

Containment wins, and it is corroborated by a second quantity nothing here
computes - the duration in the record's body:

    2026-08-22_233714   loom 14:24   replay 14:24   +0s
    2026-08-24_114745   loom 50:38   replay 50:38   +0s
    2026-08-25_010714   loom 55:45   replay 36:00   -1185s

When Loom tracked a whole game the durations agree EXACTLY. The third is
not a bad match - it is a Loom clock that misread, caught because the
record disagreed. That is the whole argument for this feature in one line,
and it is why `confidence` reports the gap rather than hiding it.

These numbers are here because "nearest timestamp" is the obvious
implementation and someone will simplify back to it otherwise.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import collections
import datetime
import io
import os
import re
import struct
import sys
from collections import namedtuple
from pathlib import Path

# The game's own name for the match in progress. Never opened - see the
# module docstring. It is a real file that parses, which is the danger.
LIVE_RECORD = "rec.aoe2record"

# A record still being appended to is a game still being played. Anything
# younger than this may simply be mid-write, so it is not offered.
SETTLED_SECONDS = 30

# How far before a record's start a Loom session may have begun and still
# belong to it. Loom is usually started AFTER the match (median 19 seconds
# after), but it can be running first and acquire the HUD as the game loads.
EARLY_START_GRACE = datetime.timedelta(minutes=3)

# A record's timestamp lives in its filename, in the game's own format:
#   SP Replay v101.103.48987.0 @2026.08.25 010700.aoe2record
#   MP Replay v101.102.12638.0 #(78174) @2023.03.14 144700 (5).aoe2record
STAMP = re.compile(r"@(\d{4})\.(\d{2})\.(\d{2}) (\d{2})(\d{2})(\d{2})")

# Loom's own stats filename: 2026-08-25_010714_fast_castle.json
STATS_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})")

Record = namedtuple("Record", "path started ended")

# How well a record matches a session, and WHY. `gap` is the record's
# duration minus Loom's, in seconds, or None when the body has not been
# read - it is the corroborating number, deliberately reported rather than
# folded into a single score.
Match = namedtuple("Match", "record confidence why gap")

CERTAIN = "certain"          # one record contains the session
AMBIGUOUS = "ambiguous"      # more than one does
NONE = "none"                # no record contains it


def search_paths():
    """Where this OS keeps recorded games.

    Returned whether or not they exist - a caller listing nothing wants to
    be able to say WHERE it looked, and "no records found" is a different
    message from "the game is not installed here".
    """
    found = []
    if os.name == "nt":
        home = os.environ.get("USERPROFILE")
        if home:
            found.append(Path(home) / "Games" / "Age of Empires 2 DE")
    else:
        # Steam Play keeps a Windows filesystem inside the Proton prefix,
        # so the path below it is identical. 813780 is AoE2 DE's app id.
        home = Path.home()
        for steam in (home / ".steam" / "steam", home / ".local" / "share" / "Steam"):
            found.append(steam / "steamapps" / "compatdata" / "813780" / "pfx"
                         / "drive_c" / "users" / "steamuser" / "Games"
                         / "Age of Empires 2 DE")
    return found


class GameStillRunning(Exception):
    """Raised rather than reading a match that has not ended.

    Its own type, not a ValueError, because callers must be able to tell
    it from a truncated or foreign file: one is "this file is broken" and
    the other is "Loom will not look at this yet", and a user needs to be
    told completely different things.
    """


def refusal_reason(path, now=None):
    """Why this record may not be read, or None if it may.

    A sentence rather than a flag, because the two refusals mean
    different things to a person: one is the match they are playing right
    now, the other is a game that ended moments ago and is worth trying
    again in a few seconds.
    """
    path = Path(path)
    if path.name.lower() == LIVE_RECORD:
        return ("that is the game being played right now - Loom can only "
                "read a match after it has ended")
    try:
        age = (now or _now()) - datetime.datetime.fromtimestamp(
            path.stat().st_mtime)
    except OSError:
        return "that file cannot be read"
    if age.total_seconds() < SETTLED_SECONDS:
        return ("the game is still writing that file - try again in a few "
                "seconds, once it has finished")
    return None


def is_finished(path, now=None):
    """May this record be read at all?

    Two refusals, and only the first is about tidiness. `rec.aoe2record` is
    the match in progress, and a record still being written is one too.
    Reading either would be reading a game that has not ended.
    """
    return refusal_reason(path, now) is None


def started_at(path):
    """When the game began, from the record's filename. None if unstamped."""
    found = STAMP.search(Path(path).name)
    return datetime.datetime(*map(int, found.groups())) if found else None


def session_started_at(stats_path):
    """When Loom began recording, from its stats filename."""
    found = STATS_STAMP.match(Path(stats_path).name)
    return datetime.datetime(*map(int, found.groups())) if found else None


def records(roots=None, now=None):
    """Every readable recorded game, oldest first.

    A record with no timestamp in its name is skipped rather than guessed
    at: without a start there is nothing to match against, and an mtime
    alone would put every old file in the running.
    """
    found = []
    for root in (roots if roots is not None else search_paths()):
        root = Path(root)
        if not root.is_dir():
            continue
        for path in root.rglob("*.aoe2record"):
            started = started_at(path)
            if started is None or not is_finished(path, now):
                continue
            try:
                ended = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                continue
            found.append(Record(path, started, ended))
    return sorted(found, key=lambda r: r.started)


def contains(record, when):
    """Was Loom's session running inside this record's match?"""
    return record.started - EARLY_START_GRACE <= when <= record.ended


def match(stats_path, available=None, now=None):
    """Which recorded game is this stats file's match?

    Never returns a best guess. A session inside two records' windows is
    AMBIGUOUS and says so, because picking the nearer one would be Loom
    guessing at exactly the seam where a wrong answer would attach one
    game's truth to another game's readings.
    """
    when = session_started_at(stats_path)
    if when is None:
        return Match(None, NONE, "the stats file has no timestamp to match on",
                     None)
    inside = [r for r in (records(now=now) if available is None else available)
              if contains(r, when)]
    if not inside:
        return Match(None, NONE, "no recorded game was running then", None)
    if len(inside) > 1:
        return Match(None, AMBIGUOUS,
                     f"{len(inside)} recorded games were running then", None)
    return Match(inside[0], CERTAIN, "one recorded game was running then", None)


def corroborate(found, loom_duration, record_duration):
    """Second the match with a quantity the filenames cannot fake.

    The gap is REPORTED, never used to reject. A record that disagrees is
    evidence about Loom's clock as much as about the match - the 08-25 file
    in the module docstring is a Loom misread, not a mismatched record - and
    deciding which is which is not this function's business.
    """
    if record_duration is None or loom_duration is None:
        return found
    return found._replace(gap=record_duration - loom_duration)


def _now():
    return datetime.datetime.now()


# ---- reading the body -------------------------------------------------
#
# The HEADER is never touched. It is a metadata blob of civilisations and
# player names framed separately from the body: the first four bytes give
# its length and I seek straight past it. mgz has been behind on that
# layout - the released 1.8.51 cannot parse a 67.2 or 68.0 header at all -
# and the body reads fine regardless, 42 of 42 records measured.
#
# `mgz` is imported INSIDE the function that needs it. Most runs of Loom
# never open a record, and a frozen build should not carry the import cost
# for a feature the player may not use.
#
# A NOTE ON THE NAME TABLES BELOW. They used to be sixteen buildings and
# four technologies, hand-corroborated against one archive, and everything
# else fell through to `building_42` / `technology_101`. That fall-through
# is still deliberate and still right: an unknown id that names ITSELF is
# visible in the statistics window and one row from being correct, where an
# unknown id quietly folded into a neighbouring name is a hand-curated
# allowlist failing silently, which this codebase has paid for before.
#
# What changed is how much has to fall through. The tables are now
# GENERATED - see tools/build_id_table.py - and the twenty hand-derived
# entries became the cross-check rather than the table. All twenty agree
# with the generated file exactly, names included, which is what earned it
# its trust. UNITS stays hand-written: only the villager matters here,
# because the HUD counts villagers directly and the feed cannot keep up
# with unit lines anyway.

from .replay_ids import BUILDINGS, TECHNOLOGIES  # noqa: E402

UNITS = {
    83: "villager",
}

AGE_TECHS = {101: "feudal_age", 102: "castle_age", 103: "imperial_age"}


class Truth:
    """What the game says one player ordered."""

    def __init__(self, player):
        self.player = player
        self.builds = collections.defaultdict(list)       # id -> [(sec, x, y)]
        self.researches = collections.defaultdict(list)   # id -> [sec]
        self.queued = collections.Counter()               # unit id -> total
        self.duration = 0

    def name_of_building(self, ident):
        return BUILDINGS.get(ident, f"building_{ident}")

    def name_of_tech(self, ident):
        return TECHNOLOGIES.get(ident, f"technology_{ident}")

    def name_of_unit(self, ident):
        return UNITS.get(ident, f"unit_{ident}")

    def ages(self):
        """When each age was ORDERED, in game seconds. Not when it arrived."""
        found = {}
        for ident, name in AGE_TECHS.items():
            if self.researches.get(ident):
                found[name] = min(self.researches[ident])
        return found


def operations(path):
    """Walk the body, yielding (game seconds, action name, payload).

    The body starts at `header_len` and I never touch what is before it.
    SYNC operations carry a millisecond delta, so accumulating them gives the
    game clock - the same coordinate Loom reads off the HUD, which is what
    makes these events comparable to a capture run at all. Note this is the
    GAME clock and so it already accounts for pauses and for the 1.7x speed,
    exactly as the never-count-with-a-wall-clock rule requires.
    """
    from mgz import fast                      # lazy: see module notes
    from mgz.fast import Operation

    # THE BOUNDARY IS ENFORCED HERE, at the one place bytes are read,
    # rather than wherever a caller happened to look the file up.
    #
    # It used to live in records(), the automatic finder - and harvest()
    # does not go through records(), so the statistics window's file
    # picker walked straight past it. A user browsing to rec.aoe2record
    # mid-match would have had it parsed: both players' orders, including
    # everything the fog was hiding.
    #
    # A safeguard every caller has to remember to ask for is a
    # hand-curated list, and it fails the same silent way. On the door of
    # the function that opens the file, every caller inherits it -
    # including ones neither of us has written yet.
    refused = refusal_reason(path)
    if refused:
        raise GameStillRunning(refused)

    with open(path, "rb") as handle:
        header_len, = struct.unpack("<I", handle.read(4))
        handle.seek(header_len)
        body = io.BytesIO(handle.read())

    fast.meta(body)
    elapsed = 0
    while True:
        try:
            op_type, payload = fast.operation(body)
        except EOFError:
            return
        except Exception as problem:      # a truncated or unknown tail
            print(f"  ! body stopped at {elapsed // 1000}s: "
                  f"{type(problem).__name__}", file=sys.stderr)
            return
        if op_type is Operation.SYNC:
            elapsed += payload[0]
        elif op_type is Operation.ACTION:
            kind, data = payload
            yield elapsed // 1000, kind.name, data


def harvest(path, player=1):
    """Collect one player's orders out of a recorded game."""
    truth = Truth(player)
    for second, kind, data in operations(path):
        truth.duration = max(truth.duration, second)
        if data.get("player_id") != player:
            continue
        if kind == "BUILD":
            truth.builds[data["building_id"]].append(
                (second, data["x"], data["y"]))
        elif kind == "RESEARCH":
            truth.researches[data["technology_id"]].append(second)
        elif kind in ("DE_QUEUE", "MAKE"):
            truth.queued[data["unit_id"]] += data.get("amount", 1)
    return truth


# Commands the game acted on, bucketed the same way the keystroke counter
# buckets keystrokes, so the two series are directly comparable rather
# than merely adjacent.
COMMAND_BUCKET_SECONDS = 5

# Actions that are the AI thinking rather than a person acting. A skirmish
# opponent issues WORK and AI_ORDER by the tens of thousands - measured,
# 32993 and 8477 in one game against a human's 2477 commands total - and
# counting those as "actions per minute" would put the AI at 925 and
# invite a comparison that means nothing.
#
# Only ever applied to a player the header says is an AI. A human's WORK
# commands are real work.
AI_ONLY_ACTIONS = {"AI_ORDER", "WORK", "GAME"}


def command_rate(path, player=1, drop_ai_noise=False):
    """Actions per minute from the game's own log, on the game clock.

    A DIFFERENT QUANTITY from apmwin.py's, and the difference is the
    point. That counts keystrokes and clicks at the keyboard - every
    repeat, every hotkey mashed twice, every misclick. This counts what
    the game actually ACTED on. Neither is wrong and neither is the other,
    so they are never blended and never share a name: the gap between them
    is wasted input, which is a thing worth showing a player about
    themselves and which nothing else can show, because no replay tool
    sees the keyboard and nothing at the keyboard sees the game.

    Returns the same shape apm.align does, so the chart draws both series
    the same way.
    """
    counted = collections.Counter()
    total = 0
    for second, kind, data in operations(path):
        if data.get("player_id") != player:
            continue
        if drop_ai_noise and kind in AI_ONLY_ACTIONS:
            continue
        counted[second // COMMAND_BUCKET_SECONDS] += 1
        total += 1
    if not counted:
        return None
    buckets = sorted(counted)
    return {
        "t": [b * COMMAND_BUCKET_SECONDS for b in buckets],
        "apm": [counted[b] * (60 / COMMAND_BUCKET_SECONDS) for b in buckets],
        "commands_total": total,
        "bucket_seconds": COMMAND_BUCKET_SECONDS,
    }


# What a player is called when the game did not record a name. Skirmish
# opponents arrive with an empty string, which must not print as a blank
# row - "" is the absence of a name, not a name.
UNNAMED = "an unnamed opponent"


def civilisation_name(ident, dataset=100):
    """"Ethiopians" for 25. None when nothing knows.

    Read from aocref's shipped dataset rather than hand-listed here. A
    hand table of 59 civilisations would drift the moment an expansion
    lands and would fail the way every hand-curated list in this project
    has failed - silently, on the entry nobody checked.
    """
    try:
        import json
        import aocref
        path = (Path(aocref.__file__).parent / "data" / "datasets"
                / f"{dataset}.json")
        civs = json.loads(path.read_text(encoding="utf-8"))["civilizations"]
    except Exception:
        return None
    found = civs.get(str(ident))
    return found.get("name") if found else None


def summary(path):
    """The header: who played, as what, on which map, and who won.

    NONE OF THIS IS ON THE HUD. A stats file has never been able to say
    whether the game was won, because winning is not drawn anywhere Loom
    reads. It is the cheapest large thing the recorded game offers.

    Refused by the same rule as the body - a match still being played is
    not readable, whichever end of the file is being asked - and the
    header is only reachable at all through the fork pinned in
    requirements.txt; the released mgz cannot parse save version 68.0.

    Returns None rather than raising on a header that will not parse, so
    a game still enriches with its body when its header is beyond us.
    """
    refused = refusal_reason(path)
    if refused:
        raise GameStillRunning(refused)
    try:
        from mgz.summary import Summary          # lazy: see module notes
        with open(path, "rb") as handle:
            found = Summary(handle)
        dataset = (found.get_dataset() or {}).get("id", 100)
        players = []
        for player in found.get_players():
            name = (player.get("name") or "").strip()
            players.append({
                "name": name or UNNAMED,
                "named": bool(name),
                "civilisation": civilisation_name(
                    player.get("civilization"), dataset),
                "civilisation_id": player.get("civilization"),
                "winner": player.get("winner"),
                # mgz's own single-figure eAPM. Kept beside Loom's series
                # rather than instead of it: this is one number for the
                # whole game, and it agrees - 37 against 37 measured
                # independently - which is worth having as a check.
                "eapm": player.get("eapm"),
                "rating": player.get("rate_snapshot"),
                # NOT trusted to mean anything. A skirmish AI comes back
                # human=True with no name and 798 eAPM, so this is
                # recorded as read and never used to decide who is a
                # person.
                "flagged_human": player.get("human"),
            })
        return {
            "map": (found.get_map() or {}).get("name"),
            "diplomacy": (found.get_diplomacy() or {}).get("type"),
            "difficulty": (found.get_settings() or {}).get("difficulty",
                                                           (None, None))[1],
            "completed": found.get_completed(),
            "players": players,
        }
    except GameStillRunning:
        raise
    except Exception:
        # A header this mgz cannot read is not a reason to lose the body.
        return None


# The villager, as the record numbers it. One id, because the record
# records the ORDER and the game decides the villager's sex afterwards.
VILLAGER_UNIT_ID = 83


def trained_times(path, player=1, unit_id=VILLAGER_UNIT_ID):
    """When each of one unit type was ORDERED, in game seconds.

    A queue of five is five entries at the same second, because five were
    asked for - the record says `amount`, and collapsing that to one would
    undercount every batch.

    Note what this is NOT. It is orders, so it counts a villager the
    moment the player clicked, not when it walked out; it keeps ones that
    were cancelled or died in the Town Centre when it fell; and it says
    nothing about any of them dying afterwards. Against the villager count
    Loom reads off the HUD - which is who is ALIVE - the gap is losses
    plus whatever is still in a queue. That gap is the interesting part
    and it is why the two are drawn as two lines rather than reconciled.
    """
    found = []
    for second, kind, data in operations(path):
        if data.get("player_id") != player or kind not in ("DE_QUEUE", "MAKE"):
            continue
        if data.get("unit_id") != unit_id:
            continue
        found.extend([second] * max(1, int(data.get("amount", 1))))
    return sorted(found)

