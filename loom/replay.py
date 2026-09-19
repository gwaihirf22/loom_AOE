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
# The record IS there and I have not been allowed to read it yet. Its own
# answer because the alternative was reporting it as NONE, which is a claim
# about the world manufactured out of a refusal to read - the same fault as
# absence dressed as empty, and it made a nine-second race look exactly
# like a match that was never recorded. A caller seeing this can wait; a
# caller seeing NONE has nothing to wait for.
NOT_YET = "not yet"


# Escape hatch, the same convention as LOOM_DATA_DIR: name the folder that
# holds the recorded games. os.pathsep-separated, because a player with two
# Steam libraries has two of them and picking one for them would be a guess.
#
# This used to be the ONLY answer for a game outside the default library,
# on the reasoning that parsing Steam's own library manifest would make a
# shipped feature depend on the shape of another program's config file.
# That reasoning was sound on Windows and wrong on Linux, and the reason is
# worth keeping because it is a general one: the same feature has a
# different shape per platform.
#
# On Windows the records live in %USERPROFILE%\Games, so which drive the
# GAME sits on never mattered - the author's own Windows machine keeps its
# library on a separate HDD and auto-location has always worked there. On
# Linux they live INSIDE the Proton prefix, which lives in whichever
# library holds the game. So on Linux "which library" is not a detail, it
# is the whole question, and declining to ask it meant a player with the
# game on a second drive got silence.
#
# steam_libraries reads that manifest now. The variable stays, because a
# manifest can be missing or a folder can be somewhere Steam never heard
# of. Inside a Flatpak it is also how the player points Loom at the folder
# they opened a sandbox hole for - see docs/install-linux.md.
RECORDS_DIR_ENV = "LOOM_RECORDS_DIR"

# The path below a Proton prefix, which is a Windows filesystem, so it is
# character-for-character what Windows itself uses. 813780 is AoE2 DE's
# Steam app id.
_GAMES_UNDER_HOME = Path("Games") / "Age of Empires 2 DE"
_PREFIX_TAIL = (Path("steamapps") / "compatdata" / "813780" / "pfx"
                / "drive_c" / "users" / "steamuser" / _GAMES_UNDER_HOME)

# Every `"path"  "<somewhere>"` in Steam's library manifest. A whole VDF
# parser would be the wrong tool: the file is a nested key-value format and
# the only thing wanted from it is a flat list of library roots, so reading
# just that one key means a change to any other part of the format cannot
# break this. A missing or unreadable file is not an error either - see
# steam_libraries - because "Steam is not installed in this shape" has
# always been an ordinary answer here.
_LIBRARY_PATH = re.compile(r'"path"\s*"([^"]+)"')

# Where Steam keeps that manifest, below a Steam root.
_LIBRARY_MANIFEST = Path("steamapps") / "libraryfolders.vdf"


def steam_libraries(steam_root):
    """Every library folder a Steam installation lists, that one's own included.

    Returns [] when the manifest is absent or says nothing - a Steam that is
    not installed here, or not installed in this shape, and neither is a
    failure worth raising over. The paths are returned unchecked, on the
    same contract as search_paths: naming somewhere is a place to LOOK.
    """
    try:
        text = (Path(steam_root) / _LIBRARY_MANIFEST).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [Path(found) for found in _LIBRARY_PATH.findall(text)]


def search_paths(os_name=None):
    """Where this OS keeps recorded games.

    Returned whether or not they exist - a caller listing nothing wants to
    be able to say WHERE it looked, and "no records found" is a different
    message from "the game is not installed here".

    That contract is what makes it safe to list a Steam that may not be
    installed in that shape: naming a directory here is a place to LOOK,
    never a claim that anything is in it.

    Takes the OS as an argument for the same reason paths.data_home does:
    the Linux answer is the one that keeps growing, and I want it checked
    from whichever machine happens to be running the suite rather than only
    on the boot that already works.
    """
    os_name = os.name if os_name is None else os_name
    found = []

    # The override first, because a player who named a folder has answered
    # the question the guesses below are guessing at. The guesses still
    # follow: they cost nothing, and dropping them would turn a mistyped
    # variable into "you have no recorded games" rather than a short list
    # that visibly does not contain the one they meant.
    override = os.environ.get(RECORDS_DIR_ENV)
    if override:
        found.extend(Path(part) for part in override.split(os.pathsep) if part)

    if os_name == "nt":
        home = os.environ.get("USERPROFILE")
        if home:
            found.append(Path(home) / _GAMES_UNDER_HOME)
    else:
        home = Path.home()
        steams = [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            # Steam installed as a Flatpak, which is how a large share of
            # Bazzite and Steam Deck players have it. Its whole home is
            # redirected, so none of the paths above exist and Loom
            # reported "no recorded games" on a machine full of them.
            (home / ".var" / "app" / "com.valvesoftware.Steam"
             / ".local" / "share" / "Steam"),
        ]
        # The default libraries first, then every library those installs
        # name. Order matters only for what a "where I looked" list reads
        # like: the common answer should be at the top.
        found.extend(steam / _PREFIX_TAIL for steam in steams)
        for steam in steams:
            found.extend(library / _PREFIX_TAIL
                         for library in steam_libraries(steam))
        # The native macOS port keeps the whole Windows-shaped user tree
        # inside its own VFS - confirmed on disk on the development Mac,
        # holding that machine's real recorded games, with Feral's own
        # capital-O "Age Of Empires II" spelling. Harmless on Linux, where
        # the directory simply does not exist and is skipped unread. The
        # CrossOver route is deliberately NOT guessed at: bottle names vary
        # per install, and LOOM_RECORDS_DIR is the documented answer there.
        found.append(home / "Library" / "Application Support"
                     / "Feral Interactive" / "Age Of Empires II" / "VFS"
                     / "User" / _GAMES_UNDER_HOME)

    # A library manifest lists its own Steam root, so the default libraries
    # come back twice on every ordinary install. De-duplicated by the path
    # as written rather than as resolved, because this list is what Loom
    # SAYS it looked in and a symlink is a different sentence to a reader -
    # records() is where sameness on disk is decided, and it resolves.
    unique = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique


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


def records(roots=None, now=None, settled_only=True):
    """Every readable recorded game, oldest first.

    A record with no timestamp in its name is skipped rather than guessed
    at: without a start there is nothing to match against, and an mtime
    alone would put every old file in the running.

    `settled_only=False` includes the ones Loom is not allowed to read yet
    - the match in progress, and the file the game finished writing seconds
    ago. It exists for ONE caller: `match`, which needs to know that a
    record covering this session exists before it is entitled to say none
    does. Dropping them here was how a nine-second race came back as "no
    recorded game was running then", so a caller that wants them must ask
    and every other caller keeps the refusal.
    """
    found = []
    # One record must not be found twice, and on an ordinary Linux install
    # it was. search_paths names both ~/.steam/steam and
    # ~/.local/share/Steam because either can be the real one - but Steam's
    # own default layout makes the first a SYMLINK to the second, so both
    # are the same directory and every record came back as two.
    #
    # The damage landed nowhere near here. `match` is forbidden from
    # guessing between two records covering one session, so it correctly
    # refused every single game: "2 recorded games were running then". The
    # guard was working perfectly on a question it should never have been
    # asked, which is why this looked like matching being broken rather
    # than listing. Measured on this machine: 168 records reported, 84 real.
    #
    # Roots are collapsed before the walk so the same tree is not scanned
    # twice, and files are checked again after it because roots can overlap
    # without being equal - one nested inside another resolves differently
    # and still yields the same file.
    seen_roots = set()
    seen_files = set()
    for root in (roots if roots is not None else search_paths()):
        root = Path(root)
        if not root.is_dir():
            continue
        try:
            key = root.resolve()
        except OSError:
            key = root
        if key in seen_roots:
            continue
        seen_roots.add(key)
        for path in root.rglob("*.aoe2record"):
            try:
                real = path.resolve()
            except OSError:
                real = path
            if real in seen_files:
                continue
            seen_files.add(real)
            started = started_at(path)
            if started is None:
                continue
            if settled_only and not is_finished(path, now):
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

    And it distinguishes NOT_YET from NONE, which is the whole reason the
    unsettled records are fetched at all. The game finishes writing its
    record and the overlay exits about nine seconds later - measured, on
    this machine - against a thirty-second settle window. Reporting that as
    "no recorded game was running then" is a statement about the world
    built out of a refusal to read, and it made a race that one retry fixes
    look like a match nobody recorded.
    """
    when = session_started_at(stats_path)
    if when is None:
        return Match(None, NONE, "the stats file has no timestamp to match on",
                     None)
    looking = (records(now=now, settled_only=False) if available is None
               else available)
    inside = [r for r in looking if contains(r, when)]
    # The refusal moved here from `records` so that ONE function decides
    # what a caller is told. `is_finished` is still the boundary and still
    # says no; the difference is that "no" now arrives with its reason.
    waiting = [r for r in inside if not is_finished(r.path, now)]
    inside = [r for r in inside if is_finished(r.path, now)]
    if not inside:
        if waiting:
            # One sentence for however many are waiting: they are all the
            # same answer, and refusal_reason already phrases it for a
            # person ("try again in a few seconds").
            return Match(None, NOT_YET,
                         refusal_reason(waiting[0].path, now)
                         or "that record cannot be read yet", None)
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

from .replay_ids import (BUILDINGS, QUEUE_TECHS, QUEUE_UNITS,  # noqa: E402
                         TECHNOLOGIES)

UNITS = {
    83: "villager",
}

AGE_TECHS = {101: "feudal_age", 102: "castle_age", 103: "imperial_age"}

# Every icon family the dataset can name AND the queue reader has a template
# for. The set exists to keep two different silences apart: an identity in
# here that a game never ordered is a MISREAD, while an identity outside it
# cannot be spoken about at all - the record has no name for it, or the
# reader has no picture of it. Scoring the second as either right or wrong
# would be inventing an answer, so it is counted separately and reported.
QUEUE_KNOWN = frozenset(QUEUE_UNITS.values()) | frozenset(QUEUE_TECHS.values())


class Truth:
    """What the game says one player ordered."""

    def __init__(self, player):
        self.player = player
        self.builds = collections.defaultdict(list)       # id -> [(sec, x, y)]
        self.researches = collections.defaultdict(list)   # id -> [sec]
        self.queued = collections.Counter()               # unit id -> total
        # Everything a single production building was told to do, in order.
        # object_id -> [(second, "unit"|"technology", id, amount)]
        #
        # The record identifies buildings by the object id its commands
        # carry, which is how a Town Centre becomes findable at all: an
        # object that trains villagers or researches Loom is a Town Centre,
        # and there is no other way to know. Counting totals threw that
        # away - `queued` above is a Counter with no when and no where,
        # which cannot say whether a building was working at 09:38.
        self.orders = collections.defaultdict(list)
        self.duration = 0

    def name_of_building(self, ident):
        return BUILDINGS.get(ident, f"building_{ident}")

    def name_of_tech(self, ident):
        return TECHNOLOGIES.get(ident, f"technology_{ident}")

    def name_of_unit(self, ident):
        return UNITS.get(ident, f"unit_{ident}")

    def queue_subjects(self):
        """Every icon family the QUEUE reader could honestly show, per game.

        Two vocabularies meet here and they are not the same one. The record
        counts in numeric ids; the queue reader names icon templates. An id
        with no template is left OUT rather than named, because the reader
        can never say it - so its absence from a reading is a fact about the
        template set and not evidence of a misread.

        Used to ask whether a slot's identity is one the player ever ordered.
        A reading outside this set is a misread; a reading inside it may
        still be wrong, and the timing checks are what look at that.
        """
        found = set()
        for ident in self.queued:
            if ident in QUEUE_UNITS:
                found.add(QUEUE_UNITS[ident])
        for ident in self.researches:
            if ident in QUEUE_TECHS:
                found.add(QUEUE_TECHS[ident])
        return found

    def unverifiable_orders(self):
        """Ids the player ordered that the queue reader has no template for.

        Reported rather than hidden. This is the number that says how much
        of a game the identity check simply cannot speak about, and a check
        whose blind spot is unmeasured is the KNOWN_WORDS failure waiting to
        happen again.
        """
        return ({i for i in self.queued if i not in QUEUE_UNITS}
                | {i for i in self.researches if i not in QUEUE_TECHS})

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
            for oid in _objects_in(data):
                truth.orders[oid].append(
                    (second, "technology", data["technology_id"], 1))
        elif kind in ("DE_QUEUE", "MAKE"):
            amount = data.get("amount", 1)
            truth.queued[data["unit_id"]] += amount
            for oid in _objects_in(data):
                truth.orders[oid].append(
                    (second, "unit", data["unit_id"], amount))
    return truth


def _objects_in(data):
    """Which building an order was given to, however the op spells it.

    DE_QUEUE and RESEARCH carry `object_ids` (a selection, so a list);
    MAKE carries a single `building_id`. Same fact, two spellings, and
    reading only one of them loses a whole command type - MAKE is 207 of
    the 722 production orders in the game this was measured on.
    """
    ids = data.get("object_ids")
    if ids:
        return list(ids)
    single = data.get("building_id")
    return [single] if single is not None else []


# A building that trains villagers or researches a Town Centre technology
# IS a Town Centre. The record never says so directly - it identifies
# buildings only by the object id its commands carry - so this is the only
# route from a recorded game to "how many Town Centres existed at 09:38",
# which is the question a whole class of Loom bug turns on.
TC_UNIT_IDS = {83}                                  # villager
TC_TECH_IDS = {22, 101, 102, 103, 213, 249, 8, 280}  # loom, ages, wheelbarrow,
                                                     # hand cart, town watch,
                                                     # town patrol


def town_centres(truth):
    """{object id: first second it was seen working}, for Town Centres only.

    First ORDER, not first existence: a Town Centre that is built and never
    used is invisible here. That direction is the safe one - it under-counts
    rather than inventing, so it can convict Loom of believing in a Town
    Centre too many and never of missing one.
    """
    found = {}
    for oid, orders in truth.orders.items():
        for second, kind, ident, _amount in orders:
            if (kind == "unit" and ident in TC_UNIT_IDS) or \
                    (kind == "technology" and ident in TC_TECH_IDS):
                found[oid] = min(found.get(oid, second), second)
    return found


def town_centres_at(truth, when):
    """How many Town Centres the record proves were working by `when`."""
    return sum(1 for first in town_centres(truth).values() if first <= when)


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

