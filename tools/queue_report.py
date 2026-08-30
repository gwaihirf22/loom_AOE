"""Score the queue reader against the recorded games (development tool).

Every other gate in this project scores ONE crop of pixels. The queue reader
had no gate at all, and it cost a game: 1907 of 2829 seconds billed as idle
Town Centre time with a single Town Centre on screen. It was calibrated by
eye, one frame at a time, which is how a reader that is right 97% of the time
looks perfect and behaves badly.

The recorded game is what makes a real score possible. Its commands carry
object ids, so a building's whole working life is recoverable on the game
clock - what it was told to make, and when:

    object 3713   first 00:02   villagers, loom, feudal, castle, imperial
    object 4881   first 18:18   villagers
    object 4908   first 19:25   villagers, wheelbarrow, hand cart

Two things are scored, and they are reported SEPARATELY on purpose. A reader
that gets identity right and idleness wrong averages 50% and is precisely the
reader that produced the bug above; one number would have hidden it.

**IDENTITY (a gate).** Every unit or technology the reader names in a slot
must be something the player actually ordered in that game. This needs no
timing at all, which makes it cheap and hard to argue with: a `fire_galley`
in a game with no dock is a misread, and so is a `swordman` in a game with no
barracks. It catches empty-cells-read-as-occupied in the same pass, because
whatever the reader invents for a cell of terrain has to come from somewhere.

The check speaks only about identities it can honestly rule on, and there
are THREE ways it cannot. An id with no template can never be read, so its
absence is a fact about the template set. An identity outside the dataset
cannot be checked at all. And an UPGRADED rung - a unit whose name is also
a technology name, so `Hussar` but not `Knight` - may have arrived by
research or free from a civilisation bonus, and the record can rule out
neither: Turks get Light Cavalry and Hussar with no command at all.

Each is counted and reported rather than quietly skipped, because a check
whose blind spot is unmeasured is the KNOWN_WORDS failure waiting to happen
in a new place. The civ bonuses are deliberately NOT written down: they move
every balance patch, so a table of them would be that same failure with a
timer on it. Declining to judge 46 of 110 unit identities is the price of
never lying about the other 64.

**IDLENESS (a reading list, not a gate).** For each Town Centre the record
proves existed, the spans it was given work for - using real research times,
so Castle Age holds a building for 160 seconds rather than a flat guess. A
frame where Loom claims MORE Town Centres idle than the record leaves room
for is a candidate false-idle.

Deliberately not a pass/fail: the record cannot see blocking, so a housed
Town Centre stalls legitimately and one frame is arguable. A CLUSTER is not.

The author's ruling is what licenses trusting the record this far: a research
can be cancelled and rarely is, so the record saying a thing happened is good
evidence it happened, and the record NOT saying it is good evidence it did
not. That authority stops at the edge of the match it recorded - see the
game-boundary note further down, which cost a run its whole score.

The corpus is frames and records - inputs and truth, never the reader's own
past answers. Scores are always produced by re-running the CURRENT reader,
because a stored reading measures the reader that produced it and calling
that today's score is the same fault as labelling a population with the
reader under suspicion.

    python -m tools.queue_report --pairs        # what pairs with what
    python -m tools.queue_report                # score the whole corpus
    python -m tools.queue_report --run NAME     # one run, with the faults
    python -m tools.queue_report --check        # gate a change
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import collections
import datetime
import glob
import json
import os
import re
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import (anchor, digits, durations, filters, hud,  # noqa: E402
                  paths, production, queue, replay, replay_ids)
from loom import reader as hud_reader  # noqa: E402

CAPTURES = paths.PROJECT_ROOT / "captures"
BASELINE = paths.PROJECT_ROOT / "tools" / "queue_baseline.txt"
PAIRS_FILE = CAPTURES / "PAIRS.tsv"

# How close a capture run's folder timestamp must be to a stats session's for
# them to be the same sitting. Both are stamped when the author presses
# record, within a second or two of each other; five minutes is generous
# enough to survive a slow start and tight enough that two sittings cannot
# collide.
SAME_SITTING = 300

# How long a unit keeps its building busy. Nothing on disk holds train
# times - durations.py is buildings and research only - so this is the
# villager's, which is the overwhelming majority of what a Town Centre
# makes, multiplied by the amount ordered.
TRAIN_SECONDS = 25

# The fallback when a technology has no measured duration. durations.py
# covers 56 of the 125 the queue reader can name, including every Town
# Centre one, so this is only reached for research that cannot happen at a
# Town Centre anyway.
ORDER_WINDOW = 25


def stats_sessions(directory=None):
    """Every stats file, as (started, duration, path)."""
    directory = directory or paths.STATS_DIR
    found = []
    for path in sorted(glob.glob(os.path.join(str(directory), "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
        except (OSError, ValueError):
            continue
        started = (record.get("meta") or {}).get("started")
        duration = (record.get("game") or {}).get("duration")
        if not started or not duration:
            continue
        try:
            when = datetime.datetime.fromisoformat(started)
        except ValueError:
            continue
        found.append((when, duration, path))
    return found


def record_files(roots=None):
    """Every recorded game on this machine, as (started, duration, path).

    Duration is read lazily by the caller: walking 324 record bodies to
    build a listing would cost minutes, and most of them are never needed.
    """
    # replay.records() already knows where these live and, more usefully,
    # refuses one the game is still writing. Reusing it means the corpus
    # cannot accidentally read a match in progress, which is the rule the
    # whole module exists to keep.
    return sorted((record.started, str(record.path))
                  for record in replay.records(roots))


def run_started(run_dir):
    stamp = re.match(r"run_(\d{8})_(\d{6})", os.path.basename(run_dir))
    if not stamp:
        return None
    return datetime.datetime.strptime(stamp.group(1) + stamp.group(2),
                                      "%Y%m%d%H%M%S")


def record_duration(path):
    """The last game second the body mentions."""
    last = 0
    for second, _kind, _data in replay.operations(path):
        last = max(last, second)
    return last


def propose_pairs(runs=None, sessions=None, records=None, verbose=False):
    """Which recorded game belongs to each capture run.

    Two steps, and the second one is why this lives here rather than in
    loom/replay.py. A capture run pairs with a STATS SESSION by timestamp -
    both are stamped when the author presses record. The session then pairs
    with a RECORD, and there are two ways:

      * the session ran DURING the match (a live game), which is what
        `replay.match` decides, and it refuses to guess between two.
      * the session ran long afterwards, because Loom was watching the
        replay. Containment cannot see that at all, and six of the most
        valuable runs in the corpus are exactly this: one game read seven
        times with different HUD skins, resolutions and eco mods.

    Exact game DURATION pairs the second kind. 3038 seconds to the second
    is not a coincidence, and seven sessions share it here.

    This is a development corpus, so it may accept a pairing the live path
    must not. `loom/replay.py` stays strict on purpose: `match` refuses to
    guess and `corroborate` reports the duration gap without ever matching
    on it.
    """
    runs = runs if runs is not None else sorted(
        glob.glob(str(CAPTURES / "run_*")))
    sessions = stats_sessions() if sessions is None else sessions
    records = record_files() if records is None else records

    durations = {}
    pairs = []
    for run in runs:
        when = run_started(run)
        if when is None:
            continue
        near = [(abs((s - when).total_seconds()), s, d, p)
                for s, d, p in sessions
                if abs((s - when).total_seconds()) <= SAME_SITTING]
        if not near:
            pairs.append((run, None, None, "no stats session at that time"))
            continue
        _gap, _s, loom_duration, _stats = min(near)

        live = [(abs((t - when).total_seconds()), p) for t, p in records
                if abs((t - when).total_seconds()) <= SAME_SITTING]
        if live:
            pairs.append((run, min(live)[1], "live", None))
            continue

        matches = []
        for stamp, path in records:
            # Causality, and it is not a nicety. A replay cannot be watched
            # before it is written, so a record stamped AFTER the capture run
            # is not a candidate however well its length agrees. Without
            # this, a run from 18 August paired with a record from the 22nd
            # purely on a duration collision - which would have attached one
            # game's truth to another game's readings, the exact fault
            # replay.match refuses to commit.
            if stamp > when:
                continue
            if path not in durations:
                try:
                    durations[path] = record_duration(path)
                except Exception:            # a record this build cannot read
                    durations[path] = None
                if verbose:
                    print(f"    read {os.path.basename(path)[-22:]}"
                          f" -> {durations[path]}")
            if durations[path] == loom_duration:
                matches.append(path)
        if len(matches) == 1:
            pairs.append((run, matches[0], "duration", None))
        elif matches:
            pairs.append((run, None, None,
                          f"{len(matches)} records share {loom_duration}s"))
        else:
            pairs.append((run, None, None, "no record of that length"))
    return pairs


def read_pairs(records=None):
    """The confirmed manifest, if the author has written one.

    Records are named by FILENAME, not by path. A path here would carry a
    home directory and a Steam id, tying the corpus to one machine - and
    this file already sits beside the frames in captures/, which is the
    least portable thing in the project. A name resolves against whatever
    record folder the running machine has.
    """
    if not PAIRS_FILE.exists():
        return {}
    if records is None:
        records = record_files()
    by_name = {os.path.basename(path): path for _started, path in records}
    found = {}
    for line in PAIRS_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        run, _, record = line.partition("\t")
        record = record.strip()
        if not record:
            continue
        # A full path still works, for a manifest written by hand.
        found[run.strip()] = by_name.get(record, record)
    return found


class Faults:
    """What one run got wrong, kept apart by kind."""

    def __init__(self, run):
        self.run = run
        self.frames = 0
        self.slots = 0
        self.checkable = 0          # identities the record can rule on
        self.unknown = 0            # identities outside the dataset
        self.upgraded = 0           # an upgraded rung the record cannot rule on
        self.never_ordered = collections.Counter()
        self.idle_frames = 0
        self.idle_while_ordered = 0
        self.beyond = 0             # frames after the record's game ended
        self.match = None           # map and civs, from the record's header

    @property
    def identity_score(self):
        if not self.checkable:
            return None
        return 1.0 - sum(self.never_ordered.values()) / self.checkable


# A capture run can outlive the game its record describes. `grab_frames` keeps
# going, so a run that starts during a five-minute game and is still running
# when the next match begins captures both - and the record knows nothing
# about the second one.
#
# Measured, and it is not rare: run_20260822_213718_annehk paired correctly by
# timestamp with a 309-second record and then read 996 frames of a 3376-second
# game. It scored 36%, and every one of those 1544 "misreads" was a real
# reading of a match the record had never heard of. Left alone it would have
# been baked into the baseline as the reader's fault.
#
# So scoring stops at the first frame that leaves the record's game. Two ways
# to leave: run past its end, or watch the clock go backwards into a new one.
NEW_GAME_DROP = 60          # a clock falling this far is a different match
RECORD_END_GRACE = 60       # the last command is not quite the last second


def busy_windows(truth, centres):
    """{Town Centre object: [(from, until), ...]} it was given work for.

    Two corrections to the first version of this, and the second was a bug
    in the check rather than a refinement.

    A REAL duration, not a flat guess. `durations.TECHNOLOGIES` carries
    exact research times - Castle Age is 160 seconds, not 25 - so a flat
    window let 135 seconds of legitimate work look like a gap and the check
    simply missed false idles there. Units keep an estimate, multiplied by
    the amount ordered, because nothing on disk holds train times; that
    estimate is the villager's, which is the overwhelming majority of what
    a Town Centre makes.

    And only TOWN CENTRE objects. The first version pooled every building's
    orders, so an archery range queueing archers made the Town Centre look
    busy and suppressed the very readings this is here to find.

    The author's ruling is what licenses trusting these at all: a research
    can be cancelled and rarely is, so the record saying a thing happened
    is good evidence it happened, and the record NOT saying it is good
    evidence it did not.
    """
    windows = collections.defaultdict(list)
    for oid in centres:
        for second, kind, ident, amount in truth.orders[oid]:
            if kind == "technology":
                name = replay_ids.QUEUE_TECHS.get(ident)
                # personal=False on purpose. durations.py now prefers this
                # PLAYER's measured times over the game's book numbers, and
                # that is right for a report about how he plays. This is a
                # tool comparing Loom against the game, so it wants what the
                # GAME does - its own docstring says as much. Research is
                # not overridden today, so the two agree; asking for the
                # right one now means they cannot silently diverge later.
                span = (durations.build_or_research_time(name, personal=False)
                        if name else 0)
                span = span or ORDER_WINDOW
            else:
                span = TRAIN_SECONDS * max(1, amount)
            windows[oid].append((second, second + span))
    return windows


def working_at(windows, when):
    """How many Town Centres the record says had work in hand."""
    return sum(1 for spans in windows.values()
               if any(start <= when <= end for start, end in spans))


def describe_match(record_path):
    """Map, difficulty and civilisations, from the record's own header.

    Because the folder name lies, and it is nobody's fault that it does. A
    run called `..._arenafastcastleboom` was played on ARABIA - the author
    ran an Arena build there deliberately, knowing the Hardest AI would let
    him get away with what a human never would. And many of the earlier
    recordings were troubleshooting the house and idle-TC alerts, where
    whichever build happened to be selected had nothing to do with what was
    being watched.

    So the build label on a capture run says what Loom was CONFIGURED with,
    never what the game was. Only the header knows the game, and it is
    printed here so nobody reading these scores infers a map from a folder.

    Best effort: the header is the half of a record that older mgz builds
    cannot always parse, and a run with an unreadable header is still
    perfectly scoreable from its body.
    """
    try:
        summary = replay.summary(record_path)
    except Exception:
        return None
    players = summary.get("players") or []
    civs = " v ".join(p.get("civilisation") or "?" for p in players[:2])
    return {"map": summary.get("map") or "?",
            "difficulty": summary.get("difficulty") or "?",
            "civs": civs}


def record_name(identity):
    """What the RECORD calls the thing Loom just read.

    Two translations, and they are the same shape: Loom holds identities the
    command log does not spell the same way.

    FAMILY is the reader's own - villager_male and villager_female are one
    villager to the game.

    The _upgrade suffix is build_queue_templates': the game names an upgrade
    technology exactly like the unit it produces, so Loom had to split
    crossbowman the research from crossbowman the unit to stop reporting one
    as the other. The record does NOT have that problem, because it files
    commands by id and by type - QUEUE_TECHS[100] and QUEUE_OBJECTS[24] are
    both "crossbowman" and it always knew which it meant. So the suffix comes
    off here, and QUEUE_UPGRADED then excuses it exactly as before.

    Kept out of queue.FAMILY on purpose. FAMILY protects the identity margin
    between pictures that COMPETE, and these do not: measured, an upgrade's
    art scores 0.045-0.666 against its unit's portrait. Putting a record's
    naming convention in there would overload it with a second meaning.
    """
    name = queue.FAMILY.get(identity, identity)
    if name.endswith(queue.UPGRADE_SUFFIX):
        base = name[:-len(queue.UPGRADE_SUFFIX)]
        if base in replay.QUEUE_KNOWN:
            return base
    return name


def score(run_dir, record_path, verbose=False):
    """Re-read every frame with the CURRENT reader and score it."""
    faults = Faults(os.path.basename(run_dir))
    faults.match = describe_match(record_path)
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return faults

    truth = replay.harvest(record_path)
    ordered = truth.queue_subjects()
    centres = replay.town_centres(truth)
    busy = busy_windows(truth, centres)

    pop_templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    wood_templates = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    found = None
    for path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        found = anchor.identify_hud(frame, pop_templates,
                                    wood_templates=wood_templates)
        if found is not None and found["score"] >= hud_reader.MIN_ANCHOR_SCORE:
            break
        found = None
    if found is None:
        return faults

    scale, profile = found["scale"], found["profile"]
    reader = queue.QueueReader(profile)
    tracker = production.ProductionTracker()
    digit_templates = digits.load_digit_templates()
    min_glyph = hud_reader.min_glyph_width(scale, profile)
    cx1, cy1, cx2, cy2 = found["clock_band"]
    clock = filters.StableClock(max_step=30, required_repeats=2)
    ends = record_duration(record_path) + RECORD_END_GRACE
    furthest = 0
    left_the_game = False

    for path in frames:
        frame = cv2.imread(path)
        if frame is None:
            continue
        raw, _ = digits.read_clock_seconds(frame[cy1:cy2, cx1:cx2],
                                           digit_templates, min_glyph)
        when = clock.update(raw)
        slots = reader.read(frame, scale)
        tracker.update(when, slots)

        # Has this run left the match the record describes? Once it has, it
        # never comes back - the frames after are a different game, and
        # scoring them says nothing about the reader. Counted, not silently
        # dropped: a corpus whose blind spot is unmeasured is the same fault
        # as a reader whose blind spot is unmeasured.
        if when is not None and not left_the_game:
            if when > ends or when < furthest - NEW_GAME_DROP:
                left_the_game = True
            furthest = max(furthest, when)
        if left_the_game:
            faults.beyond += 1
            continue

        if slots is None:
            continue
        faults.frames += 1

        for slot in slots:
            if slot.identity is None:
                continue
            faults.slots += 1
            family = record_name(slot.identity)
            if family not in replay.QUEUE_KNOWN:
                faults.unknown += 1
                continue
            # An UPGRADED rung the record does not mention is not a misread.
            # The record says what was ordered and the queue draws what will
            # emerge, and an upgrade can arrive free from a civilisation
            # bonus with no command at all. Turks get Light Cavalry and
            # Hussar for nothing, which is why this run's reader looked 455
            # readings wrong while being right to the second.
            if family not in ordered and family in replay_ids.QUEUE_UPGRADED:
                faults.upgraded += 1
                continue
            faults.checkable += 1
            if family not in ordered:
                faults.never_ordered[family] += 1
                if verbose:
                    print(f"    {os.path.basename(path)} slot {slot.index}: "
                          f"{slot.identity} was never ordered in this game")

        if when is None or tracker.idle_tcs <= 0:
            continue
        faults.idle_frames += 1
        # How many Town Centres COULD honestly be idle: the ones the record
        # proves existed, minus the ones it says had work in hand. Loom
        # claiming more idle than that is claiming a Town Centre stopped
        # while the record says it was working.
        exist = replay.town_centres_at(truth, when)
        could_be_idle = exist - working_at(busy, when)
        if exist and tracker.idle_tcs > could_be_idle:
            faults.idle_while_ordered += 1
            if verbose:
                print(f"    {os.path.basename(path)} {when // 60}:"
                      f"{when % 60:02d}: Loom says {tracker.idle_tcs} idle, "
                      f"the record allows at most {max(0, could_be_idle)}")
    return faults


# Smaller than this and a difference is floating-point noise rather than a
# reader that changed. Large enough to ignore a rounding wobble, small
# enough that a real regression cannot hide under it: the smallest genuine
# fall measured - opening every identity gate on one run - was 0.54 points,
# five thousand times this.
SCORE_NOISE = 0.0001


def regressed(now, was):
    """Did this run read WORSE than its baseline?

    A function rather than an inline comparison so it can be tested. A gate
    never proven able to fail is decoration, and that has to be provable
    without replaying eight hundred frames.
    """
    if now is None:
        return False            # nothing checkable is not a regression
    return now < was - SCORE_NOISE


def load_baseline():
    if not BASELINE.exists():
        return {}
    found = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, _, rest = line.partition("\t")
        found[name.strip()] = rest.strip()
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pairs", action="store_true",
                        help="show which record belongs to which capture run")
    parser.add_argument("--run", default=None,
                        help="score one run, naming every fault")
    parser.add_argument("--check", action="store_true",
                        help="fail if the identity score fell since the "
                             "baseline")
    parser.add_argument("--write-baseline", action="store_true",
                        help="record today's scores as the bar to beat")
    arguments = parser.parse_args()

    confirmed = read_pairs()
    if arguments.pairs:
        print("proposing pairings (this reads record bodies, give it a "
              "minute)...\n")
        for run, record, how, why in propose_pairs():
            name = os.path.basename(run)
            if record:
                print(f"  {name[:44]:<46}{how:<10}"
                      f"{os.path.basename(record)[-22:]}")
            else:
                print(f"  {name[:44]:<46}-         {why}")
        print(f"\nWrite the ones you trust to {PAIRS_FILE.name} as "
              f"'run<TAB>record path'.")
        return 0

    runs = ([str(CAPTURES / arguments.run)] if arguments.run
            else sorted(glob.glob(str(CAPTURES / "run_*"))))

    # Pair EVERY run in one pass. Doing it per run re-read all 323 record
    # bodies each time, because the duration cache lives inside the call -
    # an accidental O(runs x records) that took minutes and looked like the
    # scoring being slow rather than the pairing.
    unknown = [r for r in runs if os.path.basename(r) not in confirmed]
    found = dict(confirmed)
    if unknown:
        for run, record, _how, _why in propose_pairs(runs=unknown):
            if record:
                found[os.path.basename(run)] = record

    scored = []
    for run in runs:
        record = found.get(os.path.basename(run))
        if not record or not os.path.exists(record):
            continue
        scored.append(score(run, record, verbose=bool(arguments.run)))

    if not scored:
        print("no capture run could be paired with a recorded game")
        return 0

    print(f"{'run':<40}{'frames':>7}{'checkable':>10}{'misread':>8}"
          f"{'score':>8}{'idle?':>7}   the match, per its own header")
    for faults in scored:
        mis = sum(faults.never_ordered.values())
        shown = "-" if faults.identity_score is None \
            else f"{faults.identity_score:.1%}"
        match = faults.match or {}
        # The folder name says what Loom was configured with; only the
        # header says what the game was, and they disagree often enough to
        # print both side by side.
        about = (f"{match.get('map', '?')}, {match.get('civs', '?')}"
                 if match else "header unreadable")
        print(f"  {faults.run[:36]:<38}{faults.frames:>7}"
              f"{faults.checkable:>10}{mis:>8}{shown:>8}"
              f"{faults.idle_while_ordered:>7}   {about}")

    total_checkable = sum(f.checkable for f in scored)
    total_misread = sum(sum(f.never_ordered.values()) for f in scored)
    total_unknown = sum(f.unknown for f in scored)
    total_upgraded = sum(f.upgraded for f in scored)
    overall = (1.0 - total_misread / total_checkable) if total_checkable else 0
    print(f"\nIDENTITY  {total_checkable} slot readings the record can rule "
          f"on, {total_misread} name something never ordered  -> "
          f"{overall:.2%}")
    print(f"          {total_unknown} could not be checked at all (no "
          f"template, or outside the dataset)")
    print(f"          {total_upgraded} were an UPGRADED rung - research or a "
          f"free civ bonus, and the\n          record can rule out neither")
    worst = collections.Counter()
    for faults in scored:
        worst.update(faults.never_ordered)
    for name, count in worst.most_common(10):
        print(f"            {name:<28}{count:>6}")

    idle = sum(f.idle_while_ordered for f in scored)
    beyond = sum(f.beyond for f in scored)
    print(f"\nIDLENESS  {idle} frames where Loom called MORE Town Centres "
          f"idle than the\n          record allows - it says they had work "
          f"in hand. Not a verdict:\n          the record cannot see "
          f"blocking, so a housed Town Centre stalls\n          honestly and "
          f"one frame is arguable. A CLUSTER is the signal.")
    print(f"\nCORPUS    {beyond} frames fell outside their record's game and "
          f"were skipped.\n          A capture run can outlive the match it "
          f"was paired with, and the\n          record says nothing at all "
          f"about what came after.")

    if arguments.write_baseline:
        lines = ["# Queue reader scores. Written deliberately with",
                 "# --write-baseline; --check compares against these.",
                 f"# identity {overall:.4f} over {total_checkable} readings"]
        for faults in scored:
            if faults.identity_score is not None:
                lines.append(f"{faults.run}\t{faults.identity_score:.4f}\t"
                             f"{faults.idle_while_ordered}")
        BASELINE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {BASELINE}")
        return 0

    if arguments.check:
        baseline = load_baseline()
        if not baseline:
            print("\nno baseline yet - run --write-baseline first")
            return 1
        fell = []
        for faults in scored:
            if faults.run not in baseline or faults.identity_score is None:
                continue
            was = float(baseline[faults.run].split("\t")[0])
            if regressed(faults.identity_score, was):
                fell.append((faults.run, was, faults.identity_score))
        if fell:
            print("\nthe identity score FELL on:")
            for name, was, now in fell:
                print(f"  {name:<46}{was:.2%} -> {now:.2%}")
            return 1
        print("\nno run reads worse than its baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
