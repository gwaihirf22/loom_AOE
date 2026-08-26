"""
Loom — the statistics window: past games, a tab per subject.

One row per game in stats/, and the selected game's story split the way
Capture Age splits a match: Society, Economy, Technology, Military,
Score, APM - plus Loom's own two, the build report (the overlay's payoff
screen, preserved, with the pace curve under it) and the post-game data.
The tabs exist because the series do not share a scale: villagers run to
200, the pace delta swings either side of zero, APM reaches the hundreds.
One plot holding all of them can only lie about one of them.

One subject per tab, which is what makes tabs worth having. Three charts
stacked on one tab was where this started and it crowded all three.

Loom cannot fill every tab. It reads a HUD, not a replay file, so
resources gathered and score are simply not in any band it cuts, and a
first sighting in the production queue can never become a produced count.
Those tabs say so, and say what Loom would have to learn to read - a
roadmap the player can see beats a blank panel.

Every chart shares one thing: a vertical rule where each age arrived,
carrying that age's crest. The ARRIVAL only - the click is not drawn and
is no longer recorded (my ruling: it does not matter, and it was being
logged once per poll rather than once per transition, so an age-up grew
a picket fence of rules instead of one).

Drawn with plain QPainter. No charting library: these are line charts and
they keep the style of the overlay.

Everything read from disk is treated as untrusted: a corrupt or
foreign-schema file is listed as unreadable rather than crashing the
window, the same stance available_builds() takes with build files.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import math
from collections import namedtuple
import statistics
import pathlib

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (QColor, QFont, QKeySequence, QPainter, QPen,
                         QPixmap, QShortcut)
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog,
                             QHBoxLayout,
                             QInputDialog, QLabel, QLineEdit, QListWidget,
                             QListWidgetItem, QMenu, QMessageBox,
                             QPushButton, QScrollArea, QScrollBar,
                             QTabWidget, QVBoxLayout, QWidget)

from . import events, gamestats, paths, replay
from .age import CASTLE as AGE_CASTLE, DARK as AGE_DARK
from .age import FEUDAL as AGE_FEUDAL, FILE_NAMES as AGE_FILE_NAMES
from .age import IMPERIAL as AGE_IMPERIAL, NAMES as AGE_NAMES
from .age import REACHED as AGE_REACHED

# Which age a build item's subject names, so an age item can be
# answered by the crest instead of by a sighting of its research.
AGE_BY_SUBJECT = {"feudal_age": 2, "castle_age": 3,
                  "imperial_age": 4}
from .build_order import format_time
from .report import tc_time
from .overlay import (AHEAD_COLOR, BACKGROUND, BEHIND_COLOR, BORDER,
                      DIM_TEXT, FAINT_TEXT, ON_PACE_COLOR, TEXT,
                      load_step_icon)

APM_COLOR = QColor(200, 160, 235)
APM_RAW_COLOR = QColor(200, 160, 235, 70)   # the unsmoothed buckets, faint

# How many buckets the APM line is smoothed over. A bucket is 5 seconds
# and apm.align multiplies by 60/5, so ONE ACTION IS 12 APM - the series
# can only step in twelves, and ordinary play is a dozen-ish actions a
# bucket. That quantisation, not any measurement error, is the sawtooth:
# measured on a 56-minute game the median jump between neighbouring
# buckets was 72 against a median value of 132. A trailing window of 12
# (about a minute) takes that to 6.
APM_SMOOTHING = 12
IDLE_COLOR = QColor(214, 108, 92, 110)   # the idle-TC band, deliberately soft
# Pace is drawn NEUTRAL rather than red. It shares a frame with the
# build items now, and red already means "late" there - one colour
# for two meanings on one chart is a puzzle, not a signal. The axis
# already says which way is behind.
PACE_COLOR = QColor(162, 160, 180)
AGE_RULE_COLOR = QColor(226, 200, 130)   # the arrival: the game stated it

# How tall the crest sits on the rule, in pixels. The icons are portrait
# (78x100), so this is the height and the width follows.
CREST_SIZE = 30

# Layout, in pixels. These live here rather than inside _frame because the
# pointer has to do the same arithmetic in reverse - a click's x back into
# a game time - and two copies of a layout constant is how a crosshair
# ends up half a chart away from the line it claims to be reading.
MARGIN = 12
PLOT_LEFT_INSET = 44      # room for the value axis labels
PLOT_SIDE_INSET = 52      # that, plus a little air on the right
READOUT_HEIGHT = 18       # the strip above the charts

# The tightest the time axis will zoom. Half a minute of a game is already
# finer than the poll rate can honestly resolve, and without a floor the
# window collapses to zero and every position divides by nothing.
MIN_WINDOW_SECONDS = 30

# How far one notch of shift+wheel slides the window, as a fraction of
# what is on screen. A fifth moves enough to be worth the gesture and
# little enough to keep your place.
PAN_STEP = 0.2

# Which recorded series each chart is actually showing. The hover readout
# asks this so a tab only reports its own numbers - the APM tab has no
# business quoting a villager count nobody can see on it.
CHART_SERIES = {
    "villagers": ("villagers", "pop"),
    "idle_tcs": ("idle_tcs",),
    "pace": ("pace",),
    # The plan chart is not a time series - it has no value at
    # a hovered moment, so it contributes nothing to read out.
    "plan": (),
    "military": ("military",),
    "apm": ("apm",),
}


def hover_summary(values, keys=None):
    """One line describing the hovered moment.

    `keys` names which series to report - the ones this tab actually
    draws. Quoting a number from a chart that is not on screen invites
    the reader to look for a line that is not there. None means all of
    them, which is what a test wants and no tab does.

    `values` is what ChartView.values_at built: always "t" (the sample's
    own game time, not the pixel under the mouse), and then whichever of
    "villagers", "pop", "pop_cap", "pace", "idle_tcs" and "apm" that game
    actually recorded. A key is ABSENT when the series does not exist in
    this file, and present-but-None when it exists and that second had no
    reading - and those are different things: the first is "Loom never
    watched this", the second is "Loom looked and could not tell".

    Returns one plain string; the caller draws it.
    """
    def wanted(key):
        return keys is None or key in keys

    parts = [format_time(values["t"])]
    # A villager count Loom LOOKED at and could not read gets a dash
    # rather than silence. Dropping it would make an unread second look
    # exactly like a game that never recorded villagers at all, and those
    # are different things - which is the never-guess rule, and villagers
    # are the reading it is really about. The other series stay silent
    # when they have nothing: a missing pace is no pace to compute, not a
    # failed reading.
    if wanted("villagers") and "villagers" in values:
        count = values["villagers"]
        parts.append(f"{count} villagers" if count is not None
                     else "villagers —")
    if wanted("pace") and values.get("pace") is not None:
        parts.append(f"{values['pace']:.0f}s behind")
    if wanted("idle_tcs") and values.get("idle_tcs") is not None:
        parts.append(f"{values['idle_tcs']} idle TCs")
    if wanted("apm") and values.get("apm") is not None:
        parts.append(f"{values['apm']:.0f} APM")
    return " · ".join(parts)


# The game's own age icons, from the icon library. Preferred over the
# reader's templates for one reason: templates/age/ is cut for MATCHING -
# tight, carrying whatever HUD shading helped a correlation score - and it
# looks exactly like what it is when enlarged on a graph. These are drawn
# for display, with alpha.
#
# The library ships, so this normally succeeds. The fallback below is not
# ceremony: paths.SHARED_ASSET_DIRS treats the folder as add-your-own, so a
# player can shadow these files with their own, and a half-built bundle can
# be missing them entirely. A crest is decoration - its absence must cost a
# picture, never the window.
CREST_ICONS = {
    AGE_DARK: "DarkAgeIconDE_alpha.webp",
    AGE_FEUDAL: "FeudalAgeIconDE_alpha.webp",
    AGE_CASTLE: "CastleAgeIconDE_alpha.webp",
    AGE_IMPERIAL: "ImperialAgeIconDE_alpha.webp",
}

_crest_cache = {}


def crest_source(which):
    """The best available art for one age's crest, as a path or None.

    Icon library first, the reader's own templates second. Neither is
    guaranteed - the library is the player's and gitignored, and a
    template set could be missing from a half-built bundle - so callers
    must cope with None rather than assume a picture exists.
    """
    name = CREST_ICONS.get(which)
    if name:
        found = paths.find_asset("master_aoe2_images", f"age/{name}")
        if found is not None:
            return found
    for stem, age in AGE_FILE_NAMES.items():
        if age == which:
            for path in sorted(
                    (paths.TEMPLATES_DIR / "age").glob(f"{stem}*.png")):
                return path
    return None


def crest_pixmap(which, size=CREST_SIZE):
    """The age's crest at `size` pixels tall, or None when there is no art.

    Missing art degrades to a bare rule rather than taking the window
    down: a statistics window that still draws is more use than one that
    refuses because an optional icon was absent.
    """
    key = (which, size)
    if key in _crest_cache:
        return _crest_cache[key]
    found = None
    path = crest_source(which)
    if path is not None:
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            found = pixmap.scaledToHeight(
                size, Qt.TransformationMode.SmoothTransformation)
    _crest_cache[key] = found
    return found


def age_rule_style(what):
    """How an age rule is drawn: (color, width, dashed, show_crest), or
    None for a kind of age entry that is not drawn at all.

    Only REACHED is drawn. That is the crest changing, which is the game
    stating what age you are in - a fact, and the reason it earns the
    crest. CLICKED was drawn beside it for a while, on the theory that
    the gap between them shows the age-up; my ruling is that it does not
    matter enough to clutter every chart, and it is no longer recorded.

    Returning None rather than raising is what lets a file recorded
    BEFORE that ruling still open: those carry clicked entries, and the
    caller skips whatever it cannot style.
    """
    if what == AGE_REACHED:
        return (AGE_RULE_COLOR, 3, False, True)
    return None


# ---- reading the stats folder (pure, testable) --------------------------

def load_stats(path):
    """One stats file as a dict, or None if it is not one of ours.

    Tolerant on purpose: the folder is user-visible and a stray or corrupt
    file must not take the whole window down.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if (not isinstance(data, dict)
            or data.get("schema") not in gamestats.READABLE_SCHEMAS):
        return None
    if not isinstance(data.get("game"), dict):
        return None
    return data


def game_label(meta, game):
    """The row's text for one game.

    The player's own name for it when they have given one, otherwise one
    built from what the file knows. The date stays either way: the list is
    a history and chronology is how anyone finds anything in it.
    """
    own = str(meta.get("label") or "").strip()
    started = str(meta.get("started", ""))[:16].replace("T", " ")
    if own:
        return f"{started} — {own}"
    return (f"{started} — {meta.get('build_name', '?')}"
            f" — {format_time(game.get('duration', 0))}")


def rename_game(path, label):
    """Name one game, or clear the name with "" / None. True if written.

    The FILE NAME never changes. It carries the timestamp the list sorts
    by and is the only durable identity a game has - rename the file and
    the history reorders itself under the player. So the name they give
    lives in meta["label"], and everything else in the file is untouched.

    Rewrites the whole document, the way GameRecorder.write does, so what
    lands on disk is always complete and valid.
    """
    path = pathlib.Path(path)
    data = load_stats(path)
    if data is None:
        return False
    meta = dict(data.get("meta") or {})
    label = (label or "").strip()
    if label:
        meta["label"] = label
    else:
        meta.pop("label", None)
    data["meta"] = meta
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
            handle.write("\n")
    except OSError:
        return False
    return True


def delete_game(path):
    """Delete one game file. True if it is gone afterwards.

    Deliberately no trash folder. A hidden second copy of everything the
    player thought they deleted is its own surprise, and the caller asks
    for confirmation naming what will go. Failure is reported rather than
    raised: a file the player already removed by hand must not take the
    window down.
    """
    try:
        pathlib.Path(path).unlink()
    except OSError:
        return not pathlib.Path(path).exists()
    return True


def matches_filter(label, query):
    """Does one game's row text match what the player typed?

    `label` is the row as shown - the date, then either their own name for
    the game or its build name and length. `query` is whatever is in the
    filter box.

    EVERY word typed must appear somewhere, in any order, ignoring case.
    Plain substring matching would be simpler and worse: the row holds the
    date and the build name in a fixed order, so "arena 08-23" - the most
    natural thing to type when hunting one game out of 252 - would find
    nothing. Order-free means the player can type the two things they
    remember without remembering how the row is laid out.

    An empty query keeps everything, which falls out for free: "".split()
    is [] and all([]) is True. Worth knowing rather than guarding, since
    the caller skips filtering anyway.

    Returns True to keep the row, False to hide it.
    """
    haystack = label.lower()
    return all(word in haystack for word in query.lower().split())



def recorded_game_for(stats_path, data=None):
    """One line about the recorded game behind a stats file, or None.

    The stats window is where a person decides whether to pull the game's
    own record in, so this says what was found and how sure Loom is - never
    a bare "found it". An AMBIGUOUS answer is offered as a choice rather
    than resolved: picking the nearer of two overlapping matches would
    attach one game's truth to another game's readings.

    `data` is the loaded stats file when the caller already has it, so the
    duration can second the match. That gap is REPORTED and never used to
    reject: the one file where it disagreed by twenty minutes turned out to
    be a Loom clock misread, and a matcher that rejected on disagreement
    would have thrown away the evidence that found it.
    """
    found = replay.match(stats_path)
    if found.record is None:
        return None if found.confidence == replay.NONE else found.why
    if data:
        loom_seconds = (data.get("game") or {}).get("duration")
        found = replay.corroborate(found, loom_seconds, None)
    line = found.record.path.name
    if found.gap is not None and abs(found.gap) > SEAM_TOLERANCE:
        line += f" — but its length disagrees by {abs(found.gap)}s"
    return line


# How far two durations may differ before the disagreement is worth saying
# out loud. Loom's clock and the record's are measuring the same match, so
# anything past a few seconds is one of them being wrong.
SEAM_TOLERANCE = 5



def record_section(truth, record_path):
    """The recorded game, as the section a stats file carries.

    Named `record` and kept whole, beside the read sections rather than
    over them. Nothing here is merged into `game` or `timeline`: those are
    what Loom SAW, this is what the match was TOLD to do, and the value of
    keeping both is the difference between them.
    """
    return {
        "path": pathlib.Path(record_path).name,
        "duration": truth.duration,
        "builds": {truth.name_of_building(i): [p[0] for p in placements]
                   for i, placements in (truth.builds or {}).items()},
        "researches": {truth.name_of_tech(i): list(times)
                       for i, times in (truth.researches or {}).items()},
        "queued": {truth.name_of_unit(i): n
                   for i, n in (truth.queued or {}).items()},
        "ages": truth.ages(),
    }


def enrich_with_record(stats_path, record_path=None):
    """Attach the recorded game to a stats file. Returns what happened.

    The read sections are never touched, so this is undoable by deleting
    one key, and a file that has already been enriched is left alone
    rather than re-parsed.
    """
    data = load_stats(stats_path)
    if data is None:
        return "that file cannot be read"
    if data.get("record"):
        return "already has its recorded game"

    if record_path is None:
        found = replay.match(stats_path)
        if found.record is None:
            return found.why
        record_path = found.record.path

    try:
        truth = replay.harvest(record_path)
    except replay.GameStillRunning as refused:
        # Caught before the general case on purpose. "Loom will not look
        # at this yet" and "this file is broken" are different answers
        # and a person needs to be told which - this is the sentence that
        # explains the whole boundary, so it is passed through whole
        # rather than reduced to an exception name.
        return str(refused)
    except Exception as problem:            # a truncated or foreign file
        return f"could not read that recorded game: {type(problem).__name__}"

    data["record"] = record_section(truth, record_path)
    data["schema"] = gamestats.SCHEMA
    with open(stats_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=1)
        handle.write("\n")
    gap = truth.duration - (data.get("game") or {}).get("duration", 0)
    if abs(gap) > SEAM_TOLERANCE:
        return (f"added — but the lengths disagree by {abs(gap)}s, "
                "so one of the two clocks is wrong")
    return "added"


def truth_from_section(section):
    """A stand-in Truth built back out of a stored record section.

    So the accuracy report works from the stats file ALONE. Once enriched,
    a game can be compared against what the match was told to do with the
    .aoe2record deleted, moved, or on another machine.
    """
    class Stored:
        duration = section.get("duration", 0)
        builds = {name: [(t, 0, 0) for t in times]
                  for name, times in (section.get("builds") or {}).items()}
        researches = {name: list(times)
                      for name, times in (section.get("researches") or {}).items()}
        queued = dict(section.get("queued") or {})

        def name_of_building(self, ident):
            return ident

        def name_of_tech(self, ident):
            return ident

        def name_of_unit(self, ident):
            return ident

        def ages(self):
            return dict(section.get("ages") or {})
    return Stored()


def accuracy_rows(data):
    """What Loom read against what the match was told to do.

    Two lists that must not become one. Reading MORE than was ever ordered
    is impossible, so it convicts the reader. Reading fewer is allowed - a
    foundation gets cancelled, a research abandoned - so it is reported
    without being called an error.
    """
    section = (data or {}).get("record")
    if not section:
        return None
    sightings = events.with_record((data.get("game") or {}),
                                   truth_from_section(section))
    found = events.disagreements(sightings)
    return {
        "overfired": [d for d in found if d.verdict == events.OVERFIRED],
        "unread": [d for d in found if d.verdict == events.UNREAD],
        "durations": events.build_durations(sightings),
        "record": section,
    }




ACCURACY_NOTE = (
    "<p style='color: rgb(120,120,128);'>The recorded game holds every"
    " command the match was given, so it is the only ruler here that is not"
    " one of Loom's own readers. It says what was ORDERED, which is why the"
    " two lists mean different things.<br><br>"
    "<b>Read more than ordered</b> cannot happen. Nobody builds nine"
    " lumber camps having ordered two, so every row is a reader counting one"
    " thing twice.<br>"
    "<b>Ordered, never read</b> is not an error. A foundation can be"
    " cancelled and a research abandoned, so an order is a ceiling rather"
    " than an event - these rows are worth looking at and prove nothing on"
    " their own.</p>")


def accuracy_html(data, stats_path=None):
    """The reader-accuracy tab, as HTML."""
    rows = accuracy_rows(data)
    if rows is None:
        found = recorded_game_for(stats_path, data) if stats_path else None
        offer = (f"<p>A recorded game for this match appears to be"
                 f" <b>{found}</b>.</p>" if found else
                 "<p>No recorded game was found for this match. Watching an"
                 " existing replay writes no new record, which accounts for"
                 " most games with none.</p>")
        return ("<h3>Not compared yet</h3>" + offer +
                "<p>Use <b>Add recorded game</b> below to attach it.</p>"
                + ACCURACY_NOTE)

    over, unread = rows["overfired"], rows["unread"]
    took = rows.get("durations") or []
    parts = [f"<h3>Against <i>{rows['record']['path']}</i></h3>"]
    if not over and not unread:
        parts.append("<p>Everything Loom read agrees with what the match was"
                     " told to do.</p>")
    if over:
        parts.append(f"<h4>Read more than ordered — {len(over)}</h4>")
        parts.append(rows_as_html(
            [(d.subject, f"read {d.read}, ordered {d.ceiling}", False)
             for d in sorted(over, key=lambda d: d.ceiling - d.read)]))
    if unread:
        parts.append(f"<h4>Ordered, never read — {len(unread)}</h4>")
        parts.append(rows_as_html(
            [(d.subject, f"ordered {d.ceiling}", None)
             for d in sorted(unread, key=lambda d: -d.ceiling)]))
    if took:
        parts.append(f"<h4>How long things took — {len(took)}</h4>")
        parts.append(rows_as_html(
            [(d.subject, f"{d.seconds}s from ordered to announced", None)
             for d in took]))
        parts.append(
            "<p style='color: rgb(120,120,128);'>Ordered to announced, so it"
            " includes the villager walking there and however many helped"
            " build it - always at least the build time and usually more."
            " Only shown for things Loom read exactly as many of as were"
            " ordered: one missed completion shifts every later pairing"
            " onto the next one's finish, and the errors grow rather than"
            " cancelling.</p>")
    return "".join(parts) + ACCURACY_NOTE




# A forward jump larger than this, with no gap in the readings to explain
# it, is the clock having misread rather than time having passed. Measured:
# the two live cases were +1200s and +1201s, both the tens-of-minutes digit
# read as 5 instead of 3, and both inside a continuously-read stretch.
IMPLAUSIBLE_LEAP = 120


def clock_faults(data):
    """What is wrong with this file's clock, if anything.

    Reported, never repaired. The author's ruling and mine agree here for
    the same reason the record never overwrites a reading: a repaired
    corpus destroys the read-versus-truth gap, which is the only signal
    that a reader needs fixing. It was also how the single 24-minute
    window that produced 34 broken files was found at all - in repaired
    files there would have been nothing to notice.
    """
    times = ((data or {}).get("timeline") or {}).get("t") or []
    apm_times = ((data or {}).get("apm") or {}).get("t") or []
    faults = []
    leaps = [(times[i - 1], times[i]) for i in range(1, len(times))
             if times[i] - times[i - 1] > IMPLAUSIBLE_LEAP]
    if leaps:
        first, then = leaps[0]
        faults.append(f"the clock jumped from {mmss(first)} to {mmss(then)}"
                      f" with no gap in the readings")
    if any(apm_times[i] < apm_times[i - 1] for i in range(1, len(apm_times))):
        faults.append("the APM series runs backwards - usually TWO GAMES in"
                      " one overlay session rather than a misread, since the"
                      " timeline restarts for a new game and this series"
                      " used not to")
    duration = ((data or {}).get("game") or {}).get("duration") or 0
    if duration > 4 * 3600:
        faults.append(f"the game is recorded as {duration // 3600} hours long")
    return faults


def mmss(seconds):
    """Game seconds as m:ss, for saying where something went wrong."""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"




def clock_warning_html(data):
    """What is wrong with this file's clock, said above its numbers.

    Above rather than below: every row underneath is derived from the
    clock, so a reader who has already taken the duration as fact has
    been misled by the time a footnote reaches them.
    """
    faults = clock_faults(data)
    if not faults:
        return ""
    lines = "".join(f"<li>{fault}</li>" for fault in faults)
    return ("<p style='color: rgb(240,120,110);'><b>This file's clock needs"
            " care.</b> The numbers below are left exactly as they were"
            " recorded and are not repaired - what Loom read is evidence"
            " about the reader, and correcting it would destroy the only"
            " signal that says one needs fixing."
            f"<ul>{lines}</ul></p>")



def list_stats():
    """Every stats file, newest first: [(path, label, data-or-None)].

    Unreadable files still get a row - saying "unreadable" beats silently
    hiding a file the player can see in the folder.
    """
    if not paths.STATS_DIR.exists():
        return []
    rows = []
    for path in sorted(paths.STATS_DIR.glob("*.json"), reverse=True):
        # Demo replays write a stats file too - deliberately, so the whole
        # pipeline can be exercised with no game - but they are rehearsals,
        # not history, and a row per demo run would bury the real games.
        if path.stem.endswith("_demo"):
            continue
        data = load_stats(path)
        if data is None:
            rows.append((path, f"{path.name} — unreadable", None))
            continue
        label = game_label(data.get("meta", {}), data.get("game", {}))
        # Marked, not mended. A file whose clock misread keeps every number
        # it recorded and wears a warning, because the gap between what
        # Loom read and what happened is the evidence that finds reader
        # bugs - and a repaired corpus has none of it.
        if clock_faults(data):
            label += "  ⚠ check clock"
        rows.append((path, label, data))
    return rows


def tc_efficiency(game):
    """Fraction of TC capacity that was actually working, or None.

    Capacity = duration × the highest TC count seen; idle seconds come off
    it. An estimate, and labelled as one in the UI - the TC count is a
    high-water mark, so a lost TC flatters nobody.
    """
    # The span actually WATCHED, not the clock the game reached. A file
    # written before that distinction existed has no "observed" key, so it
    # falls back - those files are whole games, where the two agree.
    duration = game.get("observed") or game.get("duration") or 0
    tcs = game.get("tc_count") or 0
    idle = game.get("tc_idle_seconds") or 0
    if duration <= 0 or tcs <= 0:
        return None
    capacity = duration * tcs
    return max(0.0, min(1.0, 1 - idle / capacity))


def trimmed_to_one_game(series):
    """One recorded series cut down to a single pass of the clock.

    `series` is a timeline or apm block - parallel lists keyed by "t".
    Every column is cut to the same run, or the columns would no longer
    line up with each other, which is worse than the fault being fixed.

    Adds "set_aside": how many samples were dropped, so the window can
    SAY so. A chart that quietly discards half a file is worse than one
    that draws it wrong - at least the wrong one is visible.
    """
    if not series or not series.get("t"):
        return series
    start, stop = events.usable_run(series["t"])
    if (start, stop) == (0, len(series["t"])):
        return series
    trimmed = {key: (value[start:stop] if isinstance(value, list)
                     and len(value) == len(series["t"]) else value)
               for key, value in series.items()}
    trimmed["set_aside"] = len(series["t"]) - (stop - start)
    return trimmed


def rolling_median(times, values, window):
    """A trailing MEDIAN over `window` samples, aligned to `values`.

    A median, not a mean, and measured rather than assumed: on a
    56-minute game both settle the sawtooth equally (median jump 72 down
    to 6), but the mean leaves 8 points above the chart's ceiling where
    the median leaves 2. That is the difference that matters here - a
    mean DRAGS an outlier across the whole window, so one auto-repeat
    spike of 1212 lifts a full minute of line with it, while a median
    simply declines to be moved by it. Taking the highs and lows off is
    the entire job.

    Returns a list the same length, holding None wherever the input did,
    so a gap stays a gap and the drawing code breaks the line at it
    exactly as it does for the raw series.

    Two things it refuses to average across, both for the same reason -
    a mean is only meaningful over samples that belong together:

    * a TIME SEAM (the clock stepping backwards, a rewound replay or a
      restart the session detector missed). Averaging over one would
      carry the end of a game into the start of the next.
    * nothing else. A single missing bucket does NOT reset the window;
      one unread moment is not a discontinuity, and forgetting a minute
      of context over it would be its own distortion.
    """
    smoothed = []
    recent = []
    last_t = None
    for moment, value in zip(times, values):
        if last_t is not None and moment < last_t:
            recent = []
        last_t = moment
        if value is None:
            smoothed.append(None)
            continue
        recent.append(value)
        if len(recent) > window:
            recent.pop(0)
        smoothed.append(statistics.median(recent))
    return smoothed


def span_average(times, values, start, end):
    """The mean of the samples in [start, end), or None if there are none.

    A MEAN here, deliberately, where the line uses a median: this answers
    "how many actions a minute during the Feudal Age", and that question
    is asking for an average. The line's job is different - it is trying
    to show a shape without one spike bending it.
    """
    inside = [value for moment, value in zip(times, values)
              if value is not None and start <= moment < end]
    if not inside:
        return None
    return sum(inside) / len(inside)


PlanRow = namedtuple("PlanRow", "name token planned observed expected")

# A card's time is when the INSTRUCTION APPEARS, not a deadline. The
# author's ruling, and it is the difference between a useful chart and
# one that calls a well-played game late from end to end: the thing then
# has to be walked to, built or researched, and a person has to notice
# the card at all. So an item is judged against a WINDOW, and the window
# is derived from the build's own pacing rather than from a table of game
# durations - the next card's time already encodes how long the author of
# the build expected this one to take.
#
# The window is never drawn. It exists so the verdict is fair; showing it
# would be three marks per item where one is already dense.
EARLY_GRACE = 15          # doing it a little ahead is playing well
REACTION = 15             # noticing the card and walking there
LATE_TAIL = 30            # a card can be superseded before its work lands
LAST_CARD_TAIL = 90       # nothing follows the last card to bound it


def expected_window(planned, next_planned, shift=0, duration=0):
    """When an item could reasonably arrive: (start, end), game seconds.

    Opens slightly before the card, because being ahead of the build is
    not a fault. Closes after the NEXT card, because the game's pacing
    retires a card while its work is still in flight - some cards last
    seconds - and an item that lands then was done on time by any
    reasonable reading.

    `shift` moves the whole window later by however long the player was
    behind ENTERING THIS ITEM'S AGE. Half the build cannot be attempted
    before then: a Stable and a Double-Bit Axe are Feudal things, and
    calling them late against a Feudal that arrived three minutes after
    the build wanted it is blaming a player for the same delay twice.

    That is the cursor's rule in a second place - grade on the player's
    own clock - and it is clamped the same way, to only ever DELAY. A
    player who reached the age early could have done the work early, but
    marking them late for not having is not coaching.
    """
    planned += shift
    if next_planned is not None:
        next_planned += shift
    # The card says START; what Loom observes is a FINISH. So the whole
    # window sits after the thing's own build or research time, and both
    # ENDS move - which is what makes the author's "unless being early by
    # more than ten seconds" unnecessary. A player who acts promptly
    # lands inside the window rather than ahead of it, because the window
    # moved with the work rather than only stretching behind it.
    ready = planned + duration
    end = ready + LAST_CARD_TAIL if next_planned is None else next_planned
    return ready - EARLY_GRACE, max(end, ready + REACTION) + LATE_TAIL


def age_slips(build, game):
    """{age: how late the player ENTERED it}, seconds, never negative.

    The build's own first card of an age is when it expected to be in
    that age; the crest is when the player actually got there. Ages the
    crest never reported are absent - an unknown slip is not a zero one.
    """
    wanted = {}
    for step in build.steps:
        if step.time is not None:
            wanted[step.age] = min(wanted.get(step.age, step.time), step.time)
    slips = {}
    for entry in (game.get("ages") or []):
        if len(entry) >= 3 and entry[1] == AGE_REACHED and entry[2] in wanted:
            slips[entry[2]] = max(0, entry[0] - wanted[entry[2]])
    return slips


def _early_before(row):
    """The moment before which an item is genuinely EARLY.

    Never later than the card's own time, and that guard is the author's
    and it is right. The window is pushed later by two paddings of ours -
    the thing's build time, and however late the player was into the age -
    and without this, prompt play read "early by fifty seconds" purely
    because we had moved the goalposts. Early must mean ahead of what the
    BUILD asked for, never ahead of our estimate of it.
    """
    return min(row.expected[0], row.planned)


def plan_verdict(row):
    """"early", "on time", "late", or None when it was never seen."""
    if row.observed is None:
        return None
    if row.observed < _early_before(row):
        return "early"
    return "on time" if row.observed <= row.expected[1] else "late"


def plan_slip(row):
    """How far OUTSIDE its window an item landed, in seconds. 0 if in."""
    if row.observed is None:
        return None
    if row.observed < _early_before(row):
        return row.observed - _early_before(row)
    return max(0, row.observed - row.expected[1])

# How big the build items are drawn on the plan chart, and how much room
# a lane needs. Icons rather than names: a name is wide, and a dozen wide
# things sharing one time axis overlap into a smear - which is exactly
# what the first version did whenever the chart was short.
PLAN_ICON = 22
PLAN_LANE = PLAN_ICON + 4


def plan_versus_actual(stem, game):
    """[(name, planned, observed)] for every build item Loom could verify.

    The thing no other tool can draw, because no other tool knows which
    build you were following. Capture Age has your game; Loom has your
    plan as well, and the gap between the two IS the coaching.

    `stem` names the build order the recording says was loaded; `game` is
    the recorded game section. An item earns a row only when the
    checklist says it has an OBSERVABLE completion - item_evidence is the
    same judgement the overlay's green ticks use, so what appears here is
    exactly what Loom would have ticked, no looser. Items that could only
    ever have been assumed are left out rather than drawn as if they had
    been watched.

    `observed` is None when the item was never seen: that is a real
    answer and the drawing shows it as a plan with nothing against it,
    which is what "you did not do this, or Loom never read it" looks
    like. A row is never invented for an item with no planned time.
    """
    from . import build_order
    from .checklist import item_evidence, token_subject
    try:
        build = build_order.BuildOrder.load_by_name(stem)
    except (OSError, ValueError, FileNotFoundError):
        return []
    from .durations import build_or_research_time
    from .queue import TECHNOLOGY, kind_of
    # Sightings are CONSUMED in planned order. A build names "house" on
    # three different steps, and crediting each with the first house ever
    # seen reported the third one 580 seconds early - a global ledger over
    # a commodity lying, exactly as CLAUDE.md says it will. The second
    # house item takes the second house.
    unclaimed = {sighting.subject: list(sighting.times)
                 for sighting in events.for_statistics(game)}
    claimed_once = set()
    rows = []
    timed = [step for step in build.steps if step.time is not None]
    slips = age_slips(build, game)
    # The crest is the authority on when an age arrived - the queue
    # sees the age-up the moment it is CLICKED, which is why Feudal
    # kept reporting minutes before it landed.
    crest = {entry[2]: entry[0] for entry in (game.get('ages') or [])
             if len(entry) >= 3 and entry[1] == AGE_REACHED}
    for index, step in enumerate(timed):
        following = (timed[index + 1].time
                     if index + 1 < len(timed) else None)
        for segments in step.items_segments:
            evidence = item_evidence(segments)
            if not evidence:
                continue
            # An item wanting several things is done when the
            # SLOWEST is - they are built side by side by
            # different villagers, never end to end.
            takes = max(build_or_research_time(subject)
                        for subject, _ in evidence)
            window = expected_window(step.time, following,
                                     slips.get(step.age, 0), takes)
            # A technology happens once per game, ever, so a later step
            # naming it again is a heading ("In Feudal Age:"), not a
            # second research. Those get no row rather than a row saying
            # the age never arrived.
            if all(kind_of(subject) == TECHNOLOGY
                   for subject, _ in evidence):
                if any(subject in claimed_once for subject, _ in evidence):
                    continue
                claimed_once.update(subject for subject, _ in evidence)
            done, missing = None, False
            age_wanted = next((AGE_BY_SUBJECT[subject]
                               for subject, _ in evidence
                               if subject in AGE_BY_SUBJECT), None)
            if age_wanted is not None:
                # The crest changing IS the age arriving. Nothing else
                # Loom reads says so, and the research being sighted
                # only says somebody clicked.
                arrived = crest.get(age_wanted)
                rows.append(PlanRow(
                    AGE_NAMES.get(age_wanted, '?').lower(),
                    next((v for k, v in segments if k == 'icon'), None),
                    step.time, arrived, window))
                continue
            for subject, count in evidence:
                left = unclaimed.get(subject) or []
                if len(left) < count:
                    missing = True
                    continue
                # The item is done when its LAST part is, not its first.
                taken = left[:count]
                del left[:count]
                done = max(done or 0, taken[-1])
            total = sum(count for _, count in evidence)
            name = " + ".join(subject.replace("_", " ")
                              for subject, _ in evidence)
            if total > 1:
                name += f" ×{total}"
            # The picture must be of the thing being COUNTED, not the
            # first icon in the line. "Build 2 @villager@ to @lumber
            # camp@" leads with a villager, which item_evidence rightly
            # ignores - and taking the first token drew villagers where
            # lumber camps and houses belonged. So the token is chosen by
            # matching the evidence's own subject: the same judgement,
            # asked twice, cannot disagree with itself.
            token = next((value for kind, value in segments
                          if kind == "icon"
                          and token_subject(value) == evidence[0][0]), None)
            rows.append(PlanRow(name, token, step.time,
                                None if missing else done,
                                window))
    return rows


def span_integral(times, values, start, end):
    """What a per-second series accumulated over [start, end).

    For idle Town Centres this is the BILL for that age, in TC-seconds -
    each second counted once per idle TC - computed the same way the
    recorder computes the whole-game figure, guard and all: a gap wider
    than the sane interval is a seam, not thirty seconds of idleness.
    """
    total = 0.0
    for index in range(len(times) - 1):
        moment, value = times[index], values[index]
        if value is None or not start <= moment < end:
            continue
        elapsed = times[index + 1] - moment
        if 0 < elapsed <= 30:
            total += value * elapsed
    return total


def value_at(times, values, moment):
    """What a series read at `moment` - the last sample at or before it.

    At or before, never the nearest: "villagers when Feudal landed" must
    not be answered with a villager who arrived afterwards.
    """
    found = None
    for when, value in zip(times, values):
        if when > moment:
            break
        if value is not None:
            found = value
    return found


def age_spans(ages, last_moment):
    """[(age, start, end)] - which age the game was in, when.

    Built from ARRIVALS only. An entry says which age was REACHED, so the
    stretch before the first one belongs to the age below it: reaching
    Feudal at 7:06 means everything before 7:06 was the Dark Age.

    Empty when no age was ever read, which is honest - Loom cannot label
    a stretch it never identified.
    """
    arrivals = sorted((entry[0], entry[2]) for entry in ages or []
                      if len(entry) >= 3 and entry[1] == AGE_REACHED)
    if not arrivals:
        return []
    spans = []
    first_at, first_age = arrivals[0]
    if first_age - 1 >= AGE_DARK and first_at > 0:
        spans.append((first_age - 1, 0, first_at))
    for index, (moment, age) in enumerate(arrivals):
        end = (arrivals[index + 1][0] if index + 1 < len(arrivals)
               else last_moment)
        if end > moment:
            spans.append((age, moment, end))
    return spans


def nice_ticks(low, high, count=5):
    """Rounded axis tick values covering [low, high]."""
    if high <= low:
        return [low]
    raw = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in (1, 2, 5, 10):
        if raw <= step * magnitude:
            step = step * magnitude
            break
    else:
        step = 10 * magnitude
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step / 1000:
        ticks.append(round(value, 10))
        value += step
    return ticks


# ---- the tabs -----------------------------------------------------------

def rows_as_html(rows):
    """(label, value, good) rows as colored HTML for a QLabel."""
    colors = {True: ON_PACE_COLOR, False: BEHIND_COLOR, None: TEXT}
    parts = ["<table cellspacing='6'>"]
    for label, value, good in rows:
        color = colors.get(good, TEXT)
        parts.append(
            f"<tr><td style='color: rgb(160,160,168);'>{label}</td>"
            f"<td style='color: rgb({color.red()},{color.green()},"
            f"{color.blue()});'><b>{value}</b></td></tr>")
    parts.append("</table>")
    return "".join(parts)



def build_idle_row(build):
    """The idle time inside the BUILD window, or None if there is no build.

    The author's ruling, and he is right: a whole-game idle total is fine
    to have and it is not the interesting number. Idleness during the
    build is villagers the build order asked for and never got, and every
    one of them compounds for the rest of the match; the same seconds at
    minute forty are a game being played. So this row goes first, above
    the whole-game figures, and the whole-game ones become its context.

    build is the frozen BuildReport section - None when the build never
    completed, and there is then nothing honest to say.
    """
    if not build or build.get("tc_idle_seconds") is None:
        return None
    idle = build["tc_idle_seconds"]
    return ("TC idle time (during the build)", tc_time(idle), idle <= 10)


def game_rows(game, build=None):
    """The post-game summary as (label, value, good) rows."""
    rows = [("game length", format_time(game.get("duration", 0)), None),
            ("peak villagers", str(game.get("max_villagers", 0)), None),
            ("town centers (high water)", str(game.get("tc_count", 1)), None)]

    idle_row = build_idle_row(build)
    if idle_row is not None:
        rows.append(idle_row)

    efficiency = tc_efficiency(game)
    if efficiency is not None:
        rows.append(("TC efficiency (estimate)", f"{efficiency:.0%}",
                     efficiency >= 0.9))
    rows.append(("TC idle time (whole game)",
                 tc_time(game.get("tc_idle_seconds", 0)),
                 game.get("tc_idle_seconds", 0) <= 30))
    if game.get("housed_seconds", 0) > 0:
        rows.append(("housed", f"{game['housed_seconds']:.0f}s", False))
    if game.get("pop_capped_seconds", 0) > 0:
        rows.append(("at the pop cap",
                     f"{game['pop_capped_seconds']:.0f}s", None))

    deaths = game.get("deaths", [])
    if deaths:
        lost = sum(d[1] for d in deaths)
        raided = sum(d[1] for d in deaths if len(d) > 2 and d[2])
        note = f" ({raided} to raids)" if raided else ""
        rows.append((f"villagers lost", f"{lost}{note}", False))
    # Army losses by the only definition Loom can compute: population
    # minus villagers, falling. Reported separately from villagers rather
    # than summed, because they cost completely different things - a dead
    # villager is economy that stops compounding, a dead soldier is
    # resources already spent.
    army = game.get("army_losses", [])
    if army:
        lost = sum(d[1] for d in army)
        raided = sum(d[1] for d in army if len(d) > 2 and d[2])
        note = f" ({raided} during attacks)" if raided else ""
        rows.append(("army lost", f"{lost} pop{note}", False))
    if game.get("max_army"):
        rows.append(("peak army", f"{game['max_army']} pop", None))
    attacks = game.get("attacks", [])
    if attacks:
        rows.append((f"attacked ×{len(attacks)}",
                     ", ".join(format_time(t) for t in attacks[:5]), False))

    queued = game.get("queued", {})
    if queued:
        # First sightings in the production queue - NOT produced counts;
        # the queue hides duplicates and never reports completion.
        for identity, seen in sorted(queued.items(), key=lambda kv: kv[1]):
            rows.append((identity.replace("_", " "),
                         f"first queued {format_time(seen)}", None))
    alerts = game.get("alerts", [])
    if alerts:
        rows.append((f"alerts fired ×{len(alerts)}",
                     ", ".join(f"{a[1]} {format_time(a[0])}"
                               for a in alerts[:4]), None))

    # The notification feed, aggregated: "created:villager x23" reads
    # better than twenty-three rows, and the raw list stays in the file.
    # Labelled as what it is - lines SEEN in the feed - because events
    # can outpace the polls; this is a floor, not a census.
    events = game.get("events", [])
    if events:
        rows.append(("— seen in the notification feed —", "", None))
        grouped = {}
        for t, name in events:
            grouped.setdefault(name, []).append(t)
        for name, times in sorted(grouped.items(),
                                  key=lambda kv: kv[1][0]):
            label = name.replace(":", " ").replace("_", " ")
            if len(times) == 1:
                rows.append((label, f"at {format_time(times[0])}", None))
            else:
                rows.append((f"{label} ×{len(times)}",
                             f"first {format_time(times[0])}, "
                             f"last {format_time(times[-1])}", None))
    return rows


def kind_rows(game, wanted):
    """Sightings of one kind, as (label, value, good) rows.

    Reads the reconciled stream out of loom.events rather than reaching
    into the recorded file itself. That is the whole point of the pipe:
    when the readers improve and the reconciliation grows real rules, the
    rows here do not change - they are already written against what the
    handler emits rather than against what one reader happened to store.

    Each row says what is actually known: when the subject was first
    seen, how many sightings there were (a FLOOR - the queue hides
    duplicate groups and the feed can outrun the polls), and WHICH
    witnesses saw it. That last column is not decoration; a technology
    showing more than one sighting is a reader echoing, and this is where
    it becomes visible instead of averaging into a number nobody checks.

    Subjects nobody has classified land on neither tab rather than being
    guessed onto one; their number is reported so the gap is visible, and
    Post-game Data still lists every one.
    """
    sightings = events.for_statistics(game)
    rows = []
    for seen in events.of_kind(sightings, wanted):
        told = f"first {format_time(seen.first)}"
        if seen.count:
            told += f" · ×{seen.count} seen"
        told += f" · {'+'.join(sorted(seen.witnesses))}"
        rows.append((seen.subject.replace("_", " "), told, None))
    if not rows:
        rows.append(("nothing of this kind was sighted", "in this game",
                     None))
    elsewhere = sum(len(events.of_kind(sightings, kind))
                    for kind in ("unknown", "building", "animal"))
    if elsewhere:
        rows.append(("— not on this tab —",
                     f"{elsewhere} buildings and unclassified subjects,"
                     " listed under Post-game Data", None))
    return rows


KIND_NOTE = (
    "<p style='color: rgb(120,120,128);'>Sightings, not produced counts:"
    " the production queue hides duplicate groups and never reports a"
    " completion, and the notification feed can be outrun by the polls,"
    " so every count here is a floor. The two readers are separate"
    " witnesses to the same events and neither is complete yet - which"
    " is why each row names the ones that saw it. A technology sighted"
    " more than once is a reader echoing, not a second research."
    " MY losses are known - the population minus the villager count,"
    " falling - but the opponent's are not: their population is not on"
    " my HUD, and the recorded game holds orders rather than outcomes,"
    " so kills stay unknowable.</p>")


class ChartView(QWidget):
    """One tab's charts, stacked over a shared game-time axis.

    Which charts is the caller's choice - `charts` names them, so the
    Society tab and the APM tab are the same widget with different
    contents rather than two near-identical classes.
    """

    # name -> (title, method). The one place a chart is registered.
    CHARTS = {
        "villagers": ("villagers / population", "_draw_villagers"),
        "idle_tcs": ("idle town centers", "_draw_idle_tcs"),
        "pace": ("pace (seconds behind)", "_draw_pace"),
        "plan": ("the build order, said and done", "_draw_plan"),
        "military": ("military population", "_draw_military"),
        "apm": ("APM", "_draw_apm"),
    }

    # How far off plan still counts as on time, in seconds. The pace
    # meter's own tolerance, so the two never disagree about a step.
    ON_TIME_SECONDS = 15

    def __init__(self, charts=("villagers", "pace", "apm"),
                 combined=None, parent=None):
        super().__init__(parent)
        # A combined view draws every enabled chart as a LAYER of
        # one frame instead of stacking frames. The checkboxes go
        # on meaning what they meant - which data is drawn - and
        # the screen stops paying a frame, a title and two axes
        # for each of them.
        self.combined = combined
        self.charts = tuple(charts)
        self.enabled = list(charts)
        self.timeline = None
        self.apm = None
        self.apm_smooth = []
        self.set_aside = 0
        self.plan = []
        self._plan_marks = []
        self._pointer_text = None
        self.ages = []
        # The visible slice of game time. span None means the whole game,
        # which is the state "fit" returns to - kept as None rather than
        # as the current full width so that it stays fitted if the data
        # changes underneath it.
        self.left = 0.0
        self.span = None
        self._full = None               # full_span's answer, memoised
        self.hover_x = None
        self.hover_y = 0
        self.on_window_changed = None   # the scrollbar, when there is one
        self.setMouseTracking(True)
        # Low enough that a chart can share a tab with the report rows
        # above it (the Build report tab does exactly that) without the
        # rows being squeezed to nothing.
        self.setMinimumSize(480, 240)

    def show_game(self, data):
        self.timeline = trimmed_to_one_game(data.get("timeline")
                                            if data else None)
        self.apm = trimmed_to_one_game(data.get("apm") if data else None)
        self.set_aside = ((self.timeline or {}).get("set_aside", 0)
                          + (self.apm or {}).get("set_aside", 0))
        # What the build order said, against what the game showed. Costs
        # a file read, so it happens when the game is chosen rather than
        # once per paint.
        self.plan = plan_versus_actual(
            ((data or {}).get("meta") or {}).get("build"),
            (data or {}).get("game") or {}) if data else []
        # (t, what, age) straight from the recorder - "reached" now, and
        # "clicked" too in files older than that ruling. Files written
        # before the age reader existed have no key at all.
        self.ages = (data.get("game", {}) or {}).get("ages", []) if data else []
        # Smoothed once when the game is chosen, not once per paint - the
        # same lesson full_span() taught, and the readout has to agree
        # with the drawn line rather than recompute its own answer.
        self.apm_smooth = (
            rolling_median(self.apm["t"], self.apm["apm"], APM_SMOOTHING)
            if self.apm and self.apm.get("t") else [])
        self._full = None
        # A new game is a new clock: keeping the old window would open a
        # forty-minute match zoomed into a minute of a game that is gone.
        self.fit()

    # ---- the visible window ------------------------------------------

    def full_span(self):
        """The whole game's clock in seconds - the widest the window gets.

        The APM series and the timeline can end at different moments (one
        counter can outlive the other), so this is the furthest either
        reached, never one of them alone.

        MEMOISED, and that is not premature. Every plotted point asks _x
        for its position, _x asks window(), and window() asked this - so a
        max() over the whole series ran once PER POINT. Measured on a
        56-minute game: 10,132 calls in one chart's paint, 34 million
        comparisons, 300ms a frame. The answer cannot change without a new
        game, and show_game clears it.
        """
        if self._full is None:
            ends = []
            if self.timeline and self.timeline.get("t"):
                ends.append(max(self.timeline["t"]))
            if self.apm and self.apm.get("t"):
                ends.append(max(self.apm["t"]))
            self._full = max(ends) if ends else 1
        return self._full

    def window(self):
        """The visible slice as (lo, hi), always inside the game."""
        full = self.full_span()
        span = full if self.span is None else self.span
        span = max(MIN_WINDOW_SECONDS, min(span, full))
        left = max(0.0, min(self.left, full - span))
        return left, left + span

    def set_window(self, left=None, span=None, notify=True):
        """Move or resize the window, then clamp it back inside the game."""
        if span is not None:
            self.span = span
        if left is not None:
            self.left = left
        low, high = self.window()
        self.left, self.span = low, high - low
        if notify and self.on_window_changed:
            self.on_window_changed()
        self.update()

    def fit(self):
        """The whole game, no scrolling. Always one action away."""
        self.left, self.span = 0.0, None
        if self.on_window_changed:
            self.on_window_changed()
        self.update()

    def zoom_by(self, factor, anchor=None):
        """Zoom about a moment, keeping it under the same pixel.

        Anchoring on the cursor is what makes wheel-zoom feel like a map
        rather than a slideshow: the thing being looked at stays put.
        """
        low, high = self.window()
        if anchor is None:
            anchor = (low + high) / 2
        fraction = (anchor - low) / ((high - low) or 1)
        span = (high - low) / factor
        self.set_window(left=anchor - fraction * span, span=span)

    # ---- pointer -------------------------------------------------------

    def time_at(self, x):
        """The game time under a widget x, or None when off the plot."""
        plot_x = MARGIN + PLOT_LEFT_INSET
        plot_w = self.width() - 2 * MARGIN - PLOT_SIDE_INSET
        if plot_w <= 0 or not (plot_x <= x <= plot_x + plot_w):
            return None
        low, high = self.window()
        return low + ((x - plot_x) / plot_w) * (high - low)

    def wheelEvent(self, event):
        # Some platforms report a shifted wheel on the X axis instead of
        # the Y one, so take whichever arrived.
        step = event.angleDelta().y() or event.angleDelta().x()
        if not step:
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            # Shift+wheel scrolls sideways, which is the one gesture the
            # scrollbar could not give: a hand already on the wheel while
            # reading a zoomed stretch should not have to travel.
            # Wheel up goes back in time, as horizontal scrolling does
            # everywhere else.
            low, high = self.window()
            self.set_window(left=low - (step / 120) * (high - low) * PAN_STEP)
        else:
            self.zoom_by(1.25 if step > 0 else 1 / 1.25,
                         self.time_at(event.position().x()))
        event.accept()

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.hover_y = event.position().y()
        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.update()

    def paintEvent(self, event):
        # Cleared every paint: a chart claims the pointer while drawing,
        # and the claim must not outlive the frame that made it.
        self._pointer_text = None
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)

        timeline = self.timeline
        if not timeline or not timeline.get("t"):
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "no timeline in this game")
            return

        if self.combined:
            self._draw_combined(painter)
            self._draw_crosshair(painter)
            self._draw_hover(painter)
            return

        if self.combined:
            margin = MARGIN
            self._draw_readout(painter)
            self._draw_combined(
                painter, margin, margin + READOUT_HEIGHT,
                self.width() - 2 * margin,
                self.height() - READOUT_HEIGHT - 2 * margin, self.combined)
            self._draw_crosshair(painter)
            self._draw_hover(painter)
            return

        charts = []
        for name in self.charts:
            if name not in self.enabled:
                continue
            title, method = self.CHARTS[name]
            # APM only ran if the counter did; an empty frame labelled APM
            # says less than not offering the chart.
            if name == "apm" and not (self.apm and self.apm.get("t")):
                continue
            charts.append((title, getattr(self, method)))
        if not charts:
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "nothing to show - turn a series back on")
            return

        self._draw_readout(painter)
        margin = MARGIN
        usable = self.height() - READOUT_HEIGHT
        height = (usable - margin * (len(charts) + 1)) // len(charts)
        top = margin + READOUT_HEIGHT
        for title, draw in charts:
            draw(painter, margin, top, self.width() - 2 * margin, height,
                 title)
            top += height + margin
        self._draw_crosshair(painter)
        self._draw_hover(painter)

    def _draw_combined(self, painter):
        """Every enabled chart as a layer of one frame.

        Order matters and is fixed rather than following the checkboxes:
        the pace line goes down first and the build items over it, so the
        icons a pointer has to find are never buried under a series.
        """
        LAYERS = (("pace", "_layer_pace"), ("plan", "_layer_plan"),
                  ("villagers", "_layer_villagers"))
        drawn = [(name, method) for name, method in LAYERS
                 if name in self.charts and name in self.enabled]
        plot = self._frame(painter, MARGIN, MARGIN + READOUT_HEIGHT,
                           self.width() - 2 * MARGIN,
                           self.height() - MARGIN * 2 - READOUT_HEIGHT,
                           self.combined)
        key = []
        if any(name == "pace" for name, _ in drawn):
            key.append((PACE_COLOR, 2, "seconds behind"))
        if any(name == "plan" for name, _ in drawn):
            key.extend(self.PLAN_KEY)
        self._draw_key(painter, MARGIN, MARGIN + READOUT_HEIGHT,
                       self.combined, key)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        if not drawn:
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "nothing to show - turn a series back on")
            return
        for _, method in drawn:
            getattr(self, method)(painter, plot)

    def _draw_readout(self, painter):
        """The strip above the charts: where you are, and how to move.

        Permanently the instructions, never overwritten. The hovered
        numbers used to land here and covered them up - which taught the
        controls to anyone who never hovered and nobody else.
        """
        painter.setFont(QFont("sans", 9))
        painter.setPen(FAINT_TEXT)
        low, high = self.window()
        told = (f"{format_time(low)}–{format_time(high)}"
                "   ·   wheel zoom · shift+wheel pan · fit resets")
        if self.set_aside:
            # Never silently. A file whose clock runs the game twice is
            # two games sharing one recording, and the half not drawn has
            # to be admitted to rather than vanished.
            told += (f"   ·   {self.set_aside} samples set aside:"
                     " this file's clock runs the game twice")
        painter.drawText(MARGIN + PLOT_LEFT_INSET, READOUT_HEIGHT, told)

    def _draw_hover(self, painter):
        """The hovered moment's numbers, beside the crosshair.

        At the cursor rather than in the strip, because that is where the
        eye already is and because the strip has a permanent job. Flips
        to the left of the line near the right edge so it never runs off,
        and draws on its own background so a busy series underneath
        cannot swallow it.
        """
        if self.hover_x is None:
            return
        moment = self.time_at(self.hover_x)
        if moment is None:
            return
        keys = tuple(key for name in self.charts if name in self.enabled
                     for key in CHART_SERIES.get(name, ()))
        # `or ""` rather than trusting the summary: drawText raises on
        # None, and a raise inside paintEvent aborts the process.
        # A chart that claimed the pointer answers in place of the
        # general readout: one pointer, one answer, one box.
        told = self._pointer_text or hover_summary(
            self.values_at(moment), keys) or ""
        if not told:
            return
        painter.setFont(QFont("sans", 9))
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(told) + 10
        # Follows the cursor on both axes. A fixed height meant it always
        # sat on the age averages across the top of the APM chart, and a
        # tooltip that covers the same thing every time is furniture.
        top = max(READOUT_HEIGHT + 2,
                  min(int(self.hover_y) - 24, self.height() - 20))
        left = self.hover_x + 8
        if left + width > self.width() - MARGIN:
            left = self.hover_x - 8 - width
        painter.fillRect(int(left), top, width, 18, QColor(20, 20, 24, 220))
        painter.setPen(TEXT)
        painter.drawText(int(left) + 5, top + 13, told)

    def _draw_key(self, painter, x, y, title, entries):
        """A line sample and a word per series, beside the chart's title.

        Two lines on one chart with nothing naming them is a puzzle, and
        the APM chart's pair - what was read, and the average through it -
        is exactly the pair a reader must not mix up.
        """
        painter.setFont(QFont("sans", 8, QFont.Weight.Bold))
        left = x + 6 + painter.fontMetrics().horizontalAdvance(
            title.upper()) + 16
        painter.setFont(QFont("sans", 8))
        metrics = painter.fontMetrics()
        for color, width, label in entries:
            pen = QPen(color)
            pen.setWidth(width)
            painter.setPen(pen)
            painter.drawLine(int(left), y + 11, int(left) + 14, y + 11)
            painter.setPen(FAINT_TEXT)
            painter.drawText(int(left) + 19, y + 14, label)
            left += 19 + metrics.horizontalAdvance(label) + 14

    def _draw_crosshair(self, painter):
        """One vertical line through every chart at the hovered moment."""
        if self.hover_x is None or self.time_at(self.hover_x) is None:
            return
        pen = QPen(QColor(255, 255, 255, 70))
        pen.setWidth(1)
        painter.setPen(pen)
        x = int(self.hover_x)
        painter.drawLine(x, READOUT_HEIGHT, x, self.height() - MARGIN)

    def values_at(self, moment):
        """Every series at the sample nearest `moment`, as a dict.

        Nearest rather than interpolated: these are readings taken at
        particular seconds, and a number between two of them was never
        read. The key "t" is the sample's own time, which is what the
        readout should show - not the pixel the mouse happens to be on.
        """
        found = {"t": moment}
        timeline = self.timeline or {}
        times = timeline.get("t") or []
        if times:
            index = min(range(len(times)),
                        key=lambda i: abs(times[i] - moment))
            found["t"] = times[index]
            for key in ("villagers", "pop", "pop_cap", "pace", "idle_tcs"):
                series = timeline.get(key) or []
                if index < len(series):
                    found[key] = series[index]
            # Both halves must be read for the difference to mean
            # anything: a subtraction with one side missing is not a
            # small army, it is no answer.
            if (found.get("pop") is not None
                    and found.get("villagers") is not None):
                found["military"] = max(
                    0, found["pop"] - found["villagers"])
        if self.apm and self.apm.get("t"):
            apm_times = self.apm["t"]
            index = min(range(len(apm_times)),
                        key=lambda i: abs(apm_times[i] - moment))
            # The SMOOTHED value, so the readout says what the bold line
            # says. Reading out the raw bucket meant hovering a calm
            # stretch of line could report 1212, which is the reader's
            # auto-repeat artefact and not anything the player did.
            if index < len(self.apm_smooth):
                found["apm"] = self.apm_smooth[index]
        return found

    # Each chart: a titled box with a time axis and one or two series.

    def _frame(self, painter, x, y, width, height, title):
        painter.setPen(BORDER)
        painter.drawRect(x, y, width, height)
        painter.setPen(FAINT_TEXT)
        painter.setFont(QFont("sans", 8, QFont.Weight.Bold))
        painter.drawText(x + 6, y + 14, title.upper())
        # Inner plotting rect, leaving room for the title and axis labels -
        # and for half a crest, which straddles the plot's top edge.
        return x + PLOT_LEFT_INSET, y + 26, width - PLOT_SIDE_INSET, height - 46

    def _x(self, t, plot):
        """A game time as a pixel, through the current window.

        The single place time becomes horizontal position. Everything -
        series, ticks, age rules, idle blocks, the crosshair - goes
        through here, so zooming cannot move one of them and leave the
        rest behind.
        """
        plot_x, _, plot_w, _ = plot
        low, high = self.window()
        return plot_x + ((t - low) / ((high - low) or 1)) * plot_w

    def _time_axis(self, painter, plot_x, plot_y, plot_w, plot_h):
        painter.setFont(QFont("sans", 8))
        painter.setPen(FAINT_TEXT)
        low, high = self.window()
        plot = (plot_x, plot_y, plot_w, plot_h)
        # Ticks over the VISIBLE span, not the whole game: zoomed in, a
        # fixed set of whole-game ticks would leave the axis blank or
        # crowd every label into one corner.
        for tick in nice_ticks(low, high, 6):
            painter.drawText(int(self._x(tick, plot)) - 12,
                             plot_y + plot_h + 14, format_time(tick))

    def _draw_series(self, painter, points, color, plot, lo, hi, width=2):
        plot_x, plot_y, plot_w, plot_h = plot
        span = (hi - lo) or 1
        pen = QPen(color)
        pen.setWidth(width)
        painter.setPen(pen)
        # Zoomed in, most of the series is off both sides of the plot.
        # Clipping is what lets a line run in from off-screen and out
        # again: dropping the outside points instead would flatten the
        # first and last segments against the frame edge, inventing a
        # value that was never read.
        painter.save()
        painter.setClipRect(plot_x, plot_y, plot_w, plot_h)
        last = None
        last_t = None
        for t, value in points:
            if value is None:
                last = None
                last_t = None
                continue
            # Time running BACKWARDS is a recording seam - a rewound
            # replay, a restart the session detector missed - and a line
            # drawn across it slashes the whole plot (seen live: a series
            # that climbed to 20:27, reset to 0:07 and climbed again drew
            # a full-width streak). The seam breaks the line; both halves
            # still draw, honestly, where their clocks put them.
            # Only a real backwards STEP breaks the line. The clock is
            # read off the screen and wobbles a second or two; treating
            # that as a seam shredded the series into fragments.
            if last_t is not None and t < last_t - events.SEAM_TOLERANCE_SECONDS:
                last = None
            last_t = t
            px = self._x(t, plot)
            py = plot_y + plot_h - ((value - lo) / span) * plot_h
            # NOT clamped to the ceiling. Pegging was how a value past
            # the top stayed visible back when nothing clipped and a
            # series could paint into the chart below; setClipRect does
            # that job now. And pegging lies at length: a long run of
            # over-ceiling buckets drew a flat plateau along the lid that
            # read as a second, calmer series. Off the chart should look
            # off the chart.
            if last is not None:
                painter.drawLine(int(last[0]), int(last[1]), int(px), int(py))
            last = (px, py)
        painter.restore()

    def _value_axis(self, painter, plot, lo, hi):
        plot_x, plot_y, plot_w, plot_h = plot
        painter.setFont(QFont("sans", 8))
        span = (hi - lo) or 1
        for tick in nice_ticks(lo, hi, 4):
            py = plot_y + plot_h - ((tick - lo) / span) * plot_h
            painter.setPen(QColor(255, 255, 255, 20))
            painter.drawLine(plot_x, int(py), plot_x + plot_w, int(py))
            painter.setPen(FAINT_TEXT)
            painter.drawText(plot_x - 38, int(py) + 3, f"{tick:g}")

    def _draw_age_rules(self, painter, plot):
        """One crest-marked vertical rule per age arrival.

        Drawn under the series on purpose. These are context; the line the
        player came to read must stay on top of them.
        """
        plot_x, plot_y, plot_w, plot_h = plot
        if not self.ages:
            return
        low, high = self.window()
        for entry in self.ages:
            if len(entry) < 3:
                continue
            moment, what, which = entry[0], entry[1], entry[2]
            # Outside the window it is not drawn at all. A rule clamped to
            # the edge would claim an age arrived at the moment you happen
            # to have scrolled to.
            if moment is None or not low <= moment <= high:
                continue
            style = age_rule_style(what)
            if style is None:
                # An entry this code does not draw - a click recorded
                # before that stopped, or a kind from a later Loom. A
                # paint that
                # RAISES does not fail politely: PyQt cannot propagate a
                # Python exception out through C++, so the whole process
                # aborts (the same trap paths.for_display exists for).
                continue
            color, width, dashed, show_crest = style
            pen = QPen(color)
            pen.setWidth(width)
            if dashed:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            px = int(self._x(moment, plot))
            painter.drawLine(px, plot_y, px, plot_y + plot_h)
            if not show_crest:
                continue
            crest = crest_pixmap(which)
            if crest is not None:
                painter.drawPixmap(px - crest.width() // 2,
                                   plot_y - crest.height() // 2, crest)
            else:
                # No art: name the age rather than leaving a bare rule.
                painter.setFont(QFont("sans", 7, QFont.Weight.Bold))
                painter.drawText(px + 3, plot_y + 9,
                                 AGE_NAMES.get(which, "?")[:1])

    def _draw_idle_tcs(self, painter, x, y, width, height, title):
        """Idle Town Centres as a step band, where the AREA is the bill.

        Height is how many TCs were stopped, so the shaded area under the
        steps is exactly the TC-seconds the report grades - a tall thin
        block and a low wide one are visibly the same cost, which is the
        thing a number alone cannot show.

        Filled bars, not a line: a line between 1 and 3 draws a slope
        through 2, and there was never a moment at 2. The count steps.
        """
        timeline = self.timeline
        plot = self._frame(painter, x, y, width, height, title)
        plot_x, plot_y, plot_w, plot_h = plot
        idle = timeline.get("idle_tcs") or []
        hi = max(list(idle) + [1])
        self._value_axis(painter, plot, 0, hi)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        def bill(age, start, end):
            spent = span_integral(times_all, idle, start, end)
            return (f"{AGE_NAMES.get(age, '?')} {spent:.0f}s" if spent >= 1
                    else None)

        times_all = timeline["t"]
        whole = span_integral(times_all, idle, 0, self.full_span() + 1)
        self._draw_span_labels(painter, plot, bill,
                               f"game {whole:.0f}s" if whole >= 1 else None)
        if not any(idle):
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 9))
            painter.drawText(plot_x + 6, plot_y + 16,
                             "no Town Centre ever seen idle")
            return
        times = timeline["t"]
        low, high = self.window()
        # Coalesce neighbouring seconds that agree into one block first.
        # A 56-minute game holds thousands of one-second samples, and a
        # fillRect each cost 38ms a frame AND drew a comb of hairlines
        # where a single wide block is both faster and truer to what
        # happened - one stretch of idleness IS one stretch, not forty
        # separate ones that happen to touch.
        runs = []
        for index, count in enumerate(idle):
            if not count:
                continue
            start = times[index]
            # The row's belief held until the next row, so the block ends
            # there - and the same sane-interval guard the accumulators
            # use: a gap wider than that is a seam, not idleness.
            following = times[index + 1] if index + 1 < len(times) else start
            span = following - start
            if not 0 < span <= 30:
                span = 1
            if runs and runs[-1][2] == count and runs[-1][1] >= start:
                runs[-1][1] = start + span
            else:
                runs.append([start, start + span, count])
        painter.save()
        painter.setClipRect(plot_x, plot_y, plot_w, plot_h)
        for start, end, count in runs:
            if end < low or start > high:
                continue
            left = self._x(start, plot)
            block = max(1.0, self._x(end, plot) - left)
            top = plot_y + plot_h - (count / hi) * plot_h
            painter.fillRect(int(left), int(top), int(block),
                             int(plot_y + plot_h - top), IDLE_COLOR)
        painter.restore()

    def _draw_plan(self, painter, x, y, width, height, title):
        plot = self._frame(painter, x, y, width, height, title)
        self._draw_key(painter, x, y, title, self.PLAN_KEY)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        self._layer_plan(painter, plot)

    # The colours are the layer's whole vocabulary and none of them is
    # guessable, so they are named rather than legended somewhere else.
    PLAN_KEY = ((ON_PACE_COLOR, 2, "on time"), (BEHIND_COLOR, 2, "late"),
                (AHEAD_COLOR, 2, "early"), (FAINT_TEXT, 2, "never seen"))

    def _layer_plan(self, painter, plot):
        """The build's items, each drawn WHERE IT LANDED.

        No mark at the card's time and no line between the two any more,
        by the author's ruling: the plan's own time is not a fact about
        the game, and drawing it doubled every item and tripled the
        clutter on a chart that has to share a frame. The colour carries
        the judgement, and the pointer carries the detail.

        An item Loom never saw has no landing to draw, so it sits at the
        card's time in grey - the one case where the plan's time is all
        there is to show.
        """
        plot_x, plot_y, plot_w, plot_h = plot
        if not self.plan:
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 9))
            painter.drawText(plot_x + 6, plot_y + 16,
                             "no build order in this recording, or nothing"
                             " in it that Loom can verify")
            return
        # Kept after the paint: hit-testing needs the geometry the frame
        # actually used, and recomputing it outside would be a second
        # copy of the layout to drift out of step.
        marks = self._plan_marks = self._plan_layout(plot)
        painter.save()
        painter.setClipRect(plot_x, plot_y, plot_w, plot_h)
        painter.setFont(QFont("sans", 8))
        front = self._plan_under_pointer(marks)
        if front is not None:
            # REPLACES the readout rather than merely silencing it - and
            # by going through the same box it inherits the clamping, so
            # an item near the top of the frame no longer labels itself
            # off the edge of the widget.
            self._pointer_text = self._plan_label(front)
        # Everything else first, the pointed-at one last, so an icon
        # buried under three others surfaces whole rather than in slices.
        for mark in marks:
            if mark is not front:
                self._draw_plan_mark(painter, mark, faded=front is not None)
        if front is not None:
            self._draw_plan_mark(painter, front)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.restore()

    def _plan_layout(self, plot):
        """Where each build item sits: [(row, said_x, done_x, y)].

        Items are packed into LANES rather than given a row each. A lane
        takes the next item whose picture clears the last one already in
        it, so a build whose items are spread through the game uses one
        lane and a burst of them uses several - and the chart stays
        readable when it is short, which a row per item never did.

        When the lanes run past the frame they wrap and overlap on
        purpose. Overlapping is recoverable: the pointer brings one to
        the front. Shrinking the pictures until they all fit is not.
        """
        plot_x, plot_y, plot_w, plot_h = plot
        lanes = []
        marks = []
        for row in self.plan:
            said = self._x(row.planned, plot)
            done = None if row.observed is None else self._x(row.observed,
                                                             plot)
            left = min(said, done if done is not None else said) - PLAN_ICON
            right = max(said, done if done is not None else said) + PLAN_ICON
            index = next((n for n, edge in enumerate(lanes) if edge <= left),
                         len(lanes))
            if index == len(lanes):
                lanes.append(right)
            else:
                lanes[index] = right
            usable = max(1, int(plot_h // PLAN_LANE))
            y = plot_y + PLAN_LANE * (index % usable) + PLAN_LANE / 2
            marks.append((row, said, done, y))
        return marks

    def _plan_under_pointer(self, marks):
        """The item whose picture the pointer is over, or None."""
        if self.hover_x is None:
            return None
        found = None
        for mark in marks:
            _, said, done, y = mark
            if abs(self.hover_y - y) > PLAN_ICON / 2:
                continue
            for centre in (said, done):
                if centre is not None and abs(self.hover_x - centre) <= \
                        PLAN_ICON / 2:
                    found = mark          # the last drawn wins the pointer
        return found

    def _plan_label(self, mark):
        """What the pointed-at item says: what the build asked, when it
        actually landed, and the verdict against its window."""
        row = mark[0]
        told = f"{row.name} · card {format_time(row.planned)}"
        verdict = plan_verdict(row)
        if verdict is None:
            return told + " · never seen"
        slip = plan_slip(row)
        told += f" · {format_time(row.observed)} · {verdict}"
        return told if not slip else told + f" by {abs(slip):.0f}s"

    def _draw_plan_mark(self, painter, mark, faded=False):
        row, said, done, y = mark
        verdict = plan_verdict(row)
        colour = {None: FAINT_TEXT, "on time": ON_PACE_COLOR,
                  "late": BEHIND_COLOR, "early": AHEAD_COLOR}[verdict]
        pen = QPen(colour)
        pen.setWidth(2)
        painter.setPen(pen)
        where = done if done is not None else said
        icon = load_step_icon(row.token, PLAN_ICON) if row.token else None
        if icon is None:
            # No picture in the library: a filled mark, the same way the
            # overlay's step rows fall back to words.
            painter.setBrush(colour)
            painter.drawEllipse(int(where) - 5, int(y) - 5, 10, 10)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            return
        if faded:
            painter.setOpacity(0.55)
        painter.drawPixmap(int(where) - icon.width() // 2,
                           int(y) - icon.height() // 2, icon)
        painter.setOpacity(1.0)
        painter.setPen(pen)
        painter.drawRect(int(where) - icon.width() // 2 - 1,
                         int(y) - icon.height() // 2 - 1,
                         icon.width() + 1, icon.height() + 1)

    def _draw_military(self, painter, x, y, width, height, title):
        """Population minus villagers - the author's definition, and the
        only one Loom can honestly compute.

        It is not strictly military: monks, trade carts and fishing ships
        live in it too, and so does a scout. But it is every unit that is
        not a villager, it needs no new reading, and its SHAPE is the
        thing worth seeing - when an army started existing, and what it
        cost the villager line to make.

        Both halves must be read for the difference to mean anything, so
        a second with either missing is a gap rather than a zero. A
        subtraction with one side unread is not a small army.
        """
        timeline = self.timeline
        plot = self._frame(painter, x, y, width, height, title)
        pop = timeline.get("pop") or []
        vills = timeline.get("villagers") or []
        army = [None if (p is None or v is None) else max(0, p - v)
                for p, v in zip(pop, vills)]
        known = [value for value in army if value is not None]
        if not known:
            self._time_axis(painter, *plot)
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 9))
            painter.drawText(plot[0] + 6, plot[1] + 16,
                             "population and villagers were never both read")
            return
        hi = max(known + [10])
        self._draw_key(painter, x, y, title,
                       [(BEHIND_COLOR, 2, "population minus villagers")])
        self._value_axis(painter, plot, 0, hi)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)

        def peak_in(age, start, end):
            inside = [value for when, value in zip(timeline["t"], army)
                      if value is not None and start <= when < end]
            return (f"{AGE_NAMES.get(age, '?')} {max(inside)}" if inside
                    else None)

        self._draw_span_labels(painter, plot, peak_in, f"peak {max(known)}")
        self._draw_series(painter, zip(timeline["t"], army),
                          BEHIND_COLOR, plot, 0, hi)

    def _draw_villagers(self, painter, x, y, width, height, title):
        plot = self._frame(painter, x, y, width, height, title)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        self._layer_villagers(painter, plot, x, y, title)

    def _layer_villagers(self, painter, plot, x=None, y=None,
                         title=None):
        """The villager and population lines, over a plot someone
        else framed - so they can share one with the pace and the
        build items when the screen is short."""
        timeline = self.timeline
        villagers = [v for v in timeline["villagers"] if v is not None]
        caps = [c for c in timeline.get("pop_cap", []) if c is not None]
        hi = max(villagers + caps + [10])
        # Three lines in two greys and a green needs naming as much as
        # the APM pair did - more, since two of them are the same colour
        # family and the gap between them IS the military.
        if title is not None:
            # Only when this layer OWNS the frame. Sharing one, the key
            # and the axes belong to whoever framed it, or three layers
            # draw three keys over each other.
            key = [(ON_PACE_COLOR, 2, "villagers")]
            if caps:
                key += [(DIM_TEXT, 2, "population"), (FAINT_TEXT, 2, "cap")]
            self._draw_key(painter, x, y, title, key)
        self._value_axis(painter, plot, 0, hi)
        if caps:
            self._draw_series(painter,
                              zip(timeline["t"], timeline["pop_cap"]),
                              FAINT_TEXT, plot, 0, hi)
            self._draw_series(painter, zip(timeline["t"], timeline["pop"]),
                              DIM_TEXT, plot, 0, hi)
        self._draw_series(painter, zip(timeline["t"], timeline["villagers"]),
                          ON_PACE_COLOR, plot, 0, hi)

        def entered_with(age, start, end):
            # "How many vills on Feudal" is the most-quoted benchmark in
            # AoE2 coaching, and it is the count the age was ENTERED
            # with - the reading at the moment the crest changed.
            if start <= 0:
                return None
            count = value_at(timeline["t"], timeline["villagers"], start)
            return None if count is None else f"{AGE_NAMES.get(age, '?')} {count}"

        self._draw_span_labels(painter, plot, entered_with,
                               f"peak {max(villagers)}" if villagers else None)

    def _draw_pace(self, painter, x, y, width, height, title):
        plot = self._frame(painter, x, y, width, height, title)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        self._layer_pace(painter, plot)

    def _layer_pace(self, painter, plot):
        """The pace line, over a plot someone else framed.

        A layer rather than a chart, so it can share one frame with the
        build items - which is what the author asked for and what the
        screen has room for. The value axis belongs to whoever owns the
        numbers, and pace is the only one of the pair that has any.
        """
        timeline = self.timeline
        pace = [p for p in timeline.get("pace", []) if p is not None]
        if not pace:
            painter.setPen(FAINT_TEXT)
            painter.drawText(plot[0] + 6, plot[1] + 16, "no pace data")
            return
        lo, hi = min(pace + [0]), max(pace + [30])
        self._value_axis(painter, plot, lo, hi)
        # The zero line is the story: above it is behind, below is ahead.
        self._draw_series(painter, zip(timeline["t"], timeline["pace"]),
                          PACE_COLOR, plot, lo, hi)

    def _draw_apm(self, painter, x, y, width, height, title):
        plot = self._frame(painter, x, y, width, height, title)
        self._draw_key(painter, x, y, title,
                       [(APM_RAW_COLOR, 1, "read"),
                        (APM_COLOR, 2, "average")])
        values = [v for v in self.apm["apm"] if v is not None]
        # The axis tops out at a human ceiling. One absurd bucket (key
        # auto-repeat in files recorded before the counter learned to
        # ignore it - 1,476 APM is twenty-five actions a second) would
        # otherwise own the whole scale and flatten the real game to the
        # baseline. Buckets past the ceiling draw clipped at the frame
        # top: visibly off the chart, which is what they are.
        # The axis follows the SMOOTHED line, because that is the line
        # anyone reads. Scaling to the raw peaks squashed a whole game
        # into the bottom fifth to make room for one auto-repeat spike;
        # those now run off the top and clip, which is honest.
        smooth = [v for v in self.apm_smooth if v is not None]
        hi = min(max((smooth or values) + [60]) * 1.1, 400)
        self._value_axis(painter, plot, 0, hi)
        self._time_axis(painter, *plot)
        self._draw_age_rules(painter, plot)
        # Raw first and faint, the trailing mean bold over it. The mean is
        # a DERIVED number and must not be the only thing on screen - the
        # same rule that draws an assumed tick differently from an
        # observed one - and keeping the buckets visible means smoothing
        # can never hide a real spike, which is what a mean is prone to.
        self._draw_series(painter, zip(self.apm["t"], self.apm["apm"]),
                          APM_RAW_COLOR, plot, 0, hi, width=1)
        self._draw_series(painter, zip(self.apm["t"], self.apm_smooth),
                          APM_COLOR, plot, 0, hi)
        self._apm_span_labels(painter, plot)

    def _draw_span_labels(self, painter, plot, text_for, whole=None):
        """One number per age, centred over the stretch that age occupied.

        The author's layout, and the reason it is worth the trouble: the
        number is read in the PLACE it describes rather than in a legend
        somewhere else. Each chart says the per-age thing it is best
        placed to say - the villager count an age was entered with, the
        idle bill it ran up, the actions a minute it was played at.

        A label is skipped when its stretch is too narrow to hold it -
        zoomed in, or an age that lasted a minute - because a number
        overflowing into its neighbour's stretch would credit it to the
        wrong age.
        """
        plot_x, plot_y, plot_w, plot_h = plot
        painter.setFont(QFont("sans", 8))
        metrics = painter.fontMetrics()
        low, high = self.window()
        for age, start, end in age_spans(self.ages, self.full_span()):
            if end <= low or start >= high:
                continue
            text = text_for(age, start, end)
            if not text:
                continue
            left = max(self._x(start, plot), plot_x)
            right = min(self._x(end, plot), plot_x + plot_w)
            width = metrics.horizontalAdvance(text)
            if right - left < width + 8:
                continue
            painter.setPen(FAINT_TEXT)
            painter.drawText(int((left + right - width) / 2),
                             plot_y + 13, text)
        if whole:
            painter.setPen(TEXT)
            painter.drawText(
                plot_x + plot_w - metrics.horizontalAdvance(whole),
                plot_y - 4, whole)

    def _apm_span_labels(self, painter, plot):
        """Actions a minute, per age. A plain MEAN of the raw buckets:
        "actions per minute during the Castle Age" is asking for an
        average, while the median belongs to the line, whose job is
        showing a shape without one spike bending it."""
        times, values = self.apm["t"], self.apm["apm"]

        def text_for(age, start, end):
            average = span_average(times, values, start, end)
            return (None if average is None
                    else f"{AGE_NAMES.get(age, '?')} {average:.0f}")

        whole = span_average(times, values, 0, self.full_span() + 1)
        self._draw_span_labels(painter, plot, text_for,
                               None if whole is None else f"game {whole:.0f}")


class ChartTab(QWidget):
    """A tab's charts, with the controls that make them worth combining.

    Three things, all of which Capture Age has and all of which I wanted:
    a checkbox per series so one question can be asked at a time; zoom on
    the TIME axis with a scrollbar that appears as soon as the whole game
    stops fitting; and a fit button, because the way back to the whole
    picture must always be one action, never scrolled for.

    Zoom is deliberately horizontal only. Vertical zoom would let a series
    leave the frame, and these charts already peg an over-ceiling value at
    the top on purpose rather than letting it wander off.
    """

    def __init__(self, charts, note=None, combined=None,
                 parent=None):
        super().__init__(parent)
        self.view = ChartView(charts, combined=combined)
        self.view.on_window_changed = self._sync_scrollbar
        self._syncing = False

        controls = QHBoxLayout()
        self.boxes = {}
        for name in charts:
            box = QCheckBox(ChartView.CHARTS[name][0])
            box.setChecked(True)
            box.toggled.connect(self._toggled)
            controls.addWidget(box)
            self.boxes[name] = box
        controls.addStretch(1)
        for label, tip, action in (
                ("−", "zoom out", lambda: self.view.zoom_by(1 / 1.6)),
                ("+", "zoom in", lambda: self.view.zoom_by(1.6)),
                ("fit", "the whole game", self.view.fit)):
            button = QPushButton(label)
            button.setToolTip(tip)
            button.setFixedWidth(40)
            button.clicked.connect(action)
            controls.addWidget(button)

        self.scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.scrollbar.valueChanged.connect(self._scrolled)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view, stretch=1)
        layout.addWidget(self.scrollbar)
        # A tab can hold a real chart AND still admit what it cannot show
        # yet. Economy draws idle Town Centres and says in the same breath
        # that resources gathered are not readable - which beats either a
        # blank promise or a chart that implies the tab is finished.
        if note:
            label = QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet("color: rgb(140,140,148);")
            layout.addWidget(label)

    def show_game(self, data):
        self.view.show_game(data)
        self._sync_scrollbar()

    def _toggled(self):
        self.view.enabled = [name for name, box in self.boxes.items()
                             if box.isChecked()]
        self.view.update()

    def _scrolled(self, value):
        if self._syncing:
            return
        self.view.set_window(left=float(value), notify=False)

    def _sync_scrollbar(self):
        """Match the bar to the window, in whole game seconds.

        Guarded against feeding itself: setting the bar's value emits
        valueChanged, which would set the window, which would sync the
        bar. The flag makes the loop one-way.
        """
        full = self.view.full_span()
        low, high = self.view.window()
        span = high - low
        self._syncing = True
        try:
            self.scrollbar.setRange(0, max(0, int(full - span)))
            self.scrollbar.setPageStep(max(1, int(span)))
            self.scrollbar.setValue(int(low))
            # Hidden when the whole game already fits: a scrollbar that
            # cannot scroll is a control that lies about what it does.
            self.scrollbar.setVisible(span < full)
        finally:
            self._syncing = False


# What the tabs Loom cannot yet fill would hold, and what stands in the
# way. Written out rather than left blank on purpose: "coming soon" with
# no reason reads as neglect, and every one of these is blocked on a
# specific thing Loom would have to learn to READ - which makes this list
# a roadmap the player can see. Keep it honest; if one of these becomes
# possible, it stops being a promise and becomes a chart.
#
# Economy has already half-crossed that line: it draws idle Town Centres
# and reuses its blocker text below the chart as a note, because a tab can
# hold a real answer and an honest gap at the same time.
# Tabs Loom cannot fill yet, and WHY - one entry per tab that actually
# shows this panel. A test keeps the two in step, because this drifted
# once already: Economy, Technology and Military all sat here long after
# they had been built and filled, telling anyone who read the source that
# Loom could not do things it had been doing for weeks. Nothing showed
# them, so nothing contradicted them.
# A tab that EXISTS and is not finished, which is a different thing from a
# tab that does not exist. Keeping both in one dict is what let the other
# entries rot: Economy was here because its chart carries this as a note,
# and Technology and Military only looked like they belonged beside it.
PARTIAL = {
    "Economy": (
        "Loom reads how many villagers stand on each resource, not how"
        " much they have gathered - the totals are on the HUD but in no"
        " band Loom currently cuts, and the eco upgrade indicator has"
        " only ever been verified on one HUD skin at one resolution."
        " The recorded game does not help here either: it holds orders,"
        " and nobody orders a resource to arrive."),
}

COMING_SOON = {
    "Score": (
        "Score over the match, broken down by category.",
        "The score is on screen and Loom does not read it - no anchor,"
        " no band, no digits. It is the same job as the villager"
        " counter, on a different part of the HUD, and it is the last"
        " tab still waiting on a reader rather than on a decision."),
}


def coming_soon_html(tab):
    """The panel for a tab Loom cannot fill yet."""
    promise, blocker = COMING_SOON[tab]
    return (f"<p style='color: rgb(200,200,208);'><b>{promise}</b></p>"
            f"<p style='color: rgb(160,160,168);'>Not yet. {blocker}</p>"
            "<p style='color: rgb(120,120,128);'>Loom reads the HUD by"
            " screen capture rather than reading the game, so a"
            " statistic exists only once it can be seen and recognised."
            " Nothing here is guessed to fill a gap.</p>")


class StatsWindow(QWidget):
    """Past games on the left, the selected game's tabs on the right."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Loom — Statistics")
        self.resize(980, 640)

        # 252 recorded games, 194 of them under two minutes watched: the
        # history needs managing, not just reading. Filter to find, and
        # multi-select so clearing out a run of false starts is one
        # gesture rather than two hundred.
        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("filter — build name, date…")
        self.filter_box.setClearButtonEnabled(True)
        self.filter_box.textChanged.connect(self.refresh)

        self.games = QListWidget()
        self.games.setMaximumWidth(300)
        self.games.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.games.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.games.customContextMenuRequested.connect(self._context_menu)
        self.games.currentItemChanged.connect(self._show_selected)
        QShortcut(QKeySequence.StandardKey.Delete, self.games,
                  self._delete_selected)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: rgb(140,140,148);")

        self.game_tab = self._label_tab()
        # One subject per tab, which is the whole point of having tabs.
        # Society is the people; Economy is what the economy was doing,
        # which is where idle Town Centres belong (the author's call - they
        # started life beside the villagers and crowded them). Pace lives
        # in the Build report because it judges the BUILD and is
        # meaningless without the build order it is measured against.
        self.society = ChartTab(("villagers",))
        self.economy = ChartTab(("idle_tcs",), note=PARTIAL["Economy"])
        self.apm_charts = ChartTab(("apm",))
        build_page = self._build_page()

        self.tabs = QTabWidget()
        self.tabs.addTab(build_page, "Build report")
        self.tabs.addTab(self.society, "Society")
        self.tabs.addTab(self.economy, "Economy")
        self.tech_tab = self._label_tab()
        self.military_tab = self._label_tab()
        self.military_charts = ChartTab(("military",))
        self.tabs.addTab(self.tech_tab["scroll"], "Technology")
        self.tabs.addTab(self._stacked(self.military_charts,
                                       self.military_tab["scroll"]),
                         "Military")
        self.tabs.addTab(self._coming_soon_tab("Score"), "Score")
        self.tabs.addTab(self.apm_charts, "APM")
        self.tabs.addTab(self.game_tab["scroll"], "Post-game Data")
        # The recorded game is the only ruler here that is not one of
        # Loom's own readers, so what it disagrees with gets a tab of its
        # own rather than a paragraph at the bottom of another one.
        self.accuracy_tab = self._label_tab()
        self.add_record_button = QPushButton("Add recorded game")
        self.add_record_button.clicked.connect(self._add_record)
        page = QWidget()
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.accuracy_tab["scroll"], 1)
        column.addWidget(self.add_record_button)
        self.tabs.addTab(page, "Reader accuracy")

        history = QVBoxLayout()
        history.addWidget(self.filter_box)
        history.addWidget(self.games, stretch=1)
        history.addWidget(self.count_label)

        layout = QHBoxLayout(self)
        layout.addLayout(history)
        layout.addWidget(self.tabs, stretch=1)

    # ---- managing the history ------------------------------------------

    def _selected_paths(self):
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self.games.selectedItems()]

    def _context_menu(self, point):
        item = self.games.itemAt(point)
        if item is None:
            return
        chosen = self._selected_paths() or [item.data(Qt.ItemDataRole.UserRole)]
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        clear = menu.addAction("Use the build's name")
        menu.addSeparator()
        delete = menu.addAction(
            f"Delete {len(chosen)} games" if len(chosen) > 1 else "Delete")
        # Renaming is one game's business even when several are selected;
        # one name for four games would be a lie about all four.
        rename.setEnabled(len(chosen) == 1)
        clear.setEnabled(len(chosen) == 1)
        picked = menu.exec(self.games.mapToGlobal(point))
        if picked is rename:
            self._rename(item)
        elif picked is clear:
            rename_game(item.data(Qt.ItemDataRole.UserRole), "")
            self.refresh()
        elif picked is delete:
            self._delete(chosen)

    def _rename(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        data = load_stats(path) or {}
        current = str((data.get("meta") or {}).get("label") or "")
        name, accepted = QInputDialog.getText(
            self, "Rename this game", "Call this game:", text=current)
        if accepted:
            rename_game(path, name)
            self.refresh()

    def _delete_selected(self):
        chosen = self._selected_paths()
        if chosen:
            self._delete(chosen)

    def _delete(self, paths):
        """Confirm, then delete. The confirmation NAMES what will go -
        'delete 194 games' with no list is how somebody loses the one
        match they wanted to keep."""
        if len(paths) == 1:
            detail = pathlib.Path(paths[0]).name
        else:
            listed = "\n".join(pathlib.Path(p).name for p in paths[:8])
            more = len(paths) - 8
            detail = listed + (f"\n…and {more} more" if more > 0 else "")
        answer = QMessageBox.question(
            self, "Delete recorded games",
            f"Delete {len(paths)} recorded "
            f"{'game' if len(paths) == 1 else 'games'}?\n\n{detail}\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        for path in paths:
            delete_game(path)
        self.refresh()

    def _stacked(self, chart, rows):
        """A chart over a list of rows, sharing one tab. The Military tab
        wants both: the shape of an army over time, and the sightings
        that name what was in it."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(chart, stretch=3)
        layout.addWidget(rows, stretch=2)
        return page

    def _build_page(self):
        """The build's verdict, with its pace curve under it.

        The rows say what happened and the curve says when it went wrong,
        and they are the same subject - so one tab, rows above, chart
        below, rather than making the reader hold two tabs in their head.
        """
        self.build_tab = self._label_tab()
        self.pace_charts = ChartTab(
            ("plan", "pace"),
            combined="the build order, and the pace it kept")
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.build_tab["scroll"], stretch=2)
        layout.addWidget(self.pace_charts, stretch=3)
        return page

    def _coming_soon_tab(self, name):
        """A tab Loom cannot fill yet, saying what and why."""
        tab = self._label_tab()
        tab["label"].setText(coming_soon_html(name))
        return tab["scroll"]

    def _add_record(self):
        """Attach the match's own recorded game to the selected file.

        Offers the automatic match first and falls back to a file picker:
        the matcher is right about 56 of 60 games with a record and says
        AMBIGUOUS rather than guessing about the rest, so a person needs a
        way through when it declines to choose.
        """
        path = getattr(self, "_selected_path", None)
        if not path:
            return
        said = enrich_with_record(path)
        if not said.startswith("added"):
            chosen, _ = QFileDialog.getOpenFileName(
                self, f"Recorded game for this match — {said}",
                str(replay.search_paths()[0]),
                "Recorded games (*.aoe2record)")
            if not chosen:
                return
            said = enrich_with_record(path, chosen)
        data = load_stats(path)
        self.accuracy_tab["label"].setText(accuracy_html(data, path))
        self.add_record_button.setEnabled(not (data or {}).get("record"))
        QMessageBox.information(self, "Recorded game", said)


    def _label_tab(self):
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        label.setWordWrap(True)
        label.setMargin(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        return {"label": label, "scroll": scroll}

    def refresh(self, *_):
        """Re-read the stats folder, keeping the selection if possible.

        Takes and ignores an argument so it can be wired straight to the
        filter box's textChanged, which sends the new text.
        """
        selected = self.games.currentItem()
        keep = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        query = self.filter_box.text().strip()
        self.games.clear()
        total = 0
        for path, label, data in list_stats():
            total += 1
            if query and not matches_filter(label, query):
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.games.addItem(item)
            if keep == str(path):
                self.games.setCurrentItem(item)
        shown = self.games.count()
        self.count_label.setText(
            f"{shown} of {total} games" if shown != total
            else f"{total} games")
        if self.games.currentItem() is None and self.games.count():
            self.games.setCurrentRow(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _show_selected(self, current, _previous):
        if current is None:
            return
        data = load_stats(current.data(Qt.ItemDataRole.UserRole))
        if data is None:
            for tab in (self.build_tab, self.game_tab, self.tech_tab,
                        self.military_tab, self.accuracy_tab):
                tab["label"].setText("This file could not be read.")
            self.add_record_button.setEnabled(False)
            for tab in (self.society, self.economy, self.apm_charts,
                        self.pace_charts, self.military_charts):
                tab.show_game(None)
            return

        build = data.get("build")
        if build and build.get("rows"):
            self.build_tab["label"].setText(rows_as_html(build["rows"]))
        else:
            self.build_tab["label"].setText(
                "The build order was not completed in this game.")
        self.game_tab["label"].setText(
            clock_warning_html(data)
            + rows_as_html(game_rows(data.get("game", {}), build))
            + "<p style='color: rgb(120,120,128);'>Game length means the"
            " last usable reading - the game does not announce its end."
            " Queue rows are first sightings, not produced counts."
            " TC time counts each idle second once per idle Town Centre,"
            " so three stopped TCs bank three seconds a second - it is"
            " villager-making time lost, not wall time.</p>")
        game = data.get("game", {})
        self.tech_tab["label"].setText(
            rows_as_html(kind_rows(game, "technology")) + KIND_NOTE)
        self.military_tab["label"].setText(
            rows_as_html(kind_rows(game, "unit")) + KIND_NOTE)
        self._selected_path = current.data(Qt.ItemDataRole.UserRole)
        self.accuracy_tab["label"].setText(
            accuracy_html(data, self._selected_path))
        self.add_record_button.setEnabled(not data.get("record"))
        for tab in (self.society, self.economy, self.apm_charts,
                    self.pace_charts, self.military_charts):
            tab.show_game(data)
