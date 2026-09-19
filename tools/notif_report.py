"""
Loom — read the labelled corpus and account for every miss.

    python -m tools.notif_report                  # report + rewrite baseline
    python -m tools.notif_report --manifest OUT   # harvest manifest from misses

The reliability program's measuring stick. For every corpus run with a
labels.tsv (see tools/notif_corpus.py), every labelled crop is read and
scored:

    read        produced text
    understood  produced the LABELLED text - the target that matters
    event       parsed to the event the label implies

and every miss is CLASSIFIED, because each class has a different remedy:

    unread       no text at all - segmentation or gate failure
    merge        fewer glyph runs than the label needs (letters welded;
                 remedy: a merge_* template, like merge_t_hyphen)
    split        more runs than the label needs (a letter came apart;
                 remedy: _read_join, or segmentation constants)
    space        the letters are right and the spaces are not
    one-sub      exactly one letter wrong - a coverage gap or a near-twin
                 (remedy: harvest, or the runner-up repair)
    multi-sub    several letters wrong - a rendering the font barely knows
                 (remedy: harvest from this very crop; the label is the
                 transcription)
    vocabulary   read PERFECTLY and refused - a word the gate lacks
    wrong-event  understood but parsed to a different event (a parse bug)

The whole report is written to tools/notif_baseline.txt, which is
COMMITTED: any change to the reader re-runs this, and `git diff` on the
baseline is the review. That discipline exists because a plausible
improvement once read 14% more lines while finding FEWER events, and only
re-measuring events caught it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import re
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, paths
from loom import lines as line_reader  # noqa: E402
from tools.build_notification_font import expected_tokens  # noqa: E402

CORPUS_DIR = paths.CAPTURES_DIR / "notif_corpus"
BASELINE = paths.PROJECT_ROOT / "tools" / "notif_baseline.txt"


def classify_miss(text, truth, runs_found):
    """Which failure class a miss belongs to. See the module docstring."""
    if not text:
        needed = sum(2 if token == "DASHES" else 1
                     for token in expected_tokens(truth))
        if runs_found and runs_found < needed:
            return "merge"
        if runs_found and runs_found > needed:
            return "split"
        return "unread"
    if text == truth:
        return "wrong-event"
    flat_text = text.replace(" ", "")
    flat_truth = truth.replace(" ", "")
    if flat_text == flat_truth:
        return "space"
    if len(flat_text) == len(flat_truth):
        wrong = sum(1 for a, b in zip(flat_text, flat_truth) if a != b)
        return "one-sub" if wrong == 1 else "multi-sub"
    return "merge" if len(flat_text) < len(flat_truth) else "split"


# How wide a run may be, as a fraction of its own width, before --runs
# calls it out as suspicious. Only a hint for the eye: the real bound is
# glyphs.MERGED_RUN_FRACTION, and printing that comparison beside every run
# is the whole point - a run at 0.80 of the height with the bound at 0.85 is
# invisible in a text diff and obvious here.
def show_runs(crop, truth, text, font, skin=None):
    """One miss as pixels, with the run boundaries the reader chose.

    This exists because of the lesson this project keeps re-learning: before
    blaming the templates or the matcher, print the glyph the reader is
    actually comparing. Three separate 1080p faults were visible the moment
    the pixels were laid out as ASCII, and the same habit overturned a
    confident diagnosis of a fourth.

    A merge and an over-split are each obvious in one glance here and
    indistinguishable in the read text, which only ever says the letters
    came out wrong.
    """
    mask, runs = glyphs.segment_line(crop)
    height = mask.shape[0]
    if not runs:
        return [f"      (no runs at all: height {height})"]
    left = max(0, runs[0][0] - 2)
    right = min(mask.shape[1], runs[-1][1] + 2)

    out = [f"      truth {truth!r}", f"      read  {text!r}",
           f"      height {height}  runs {len(runs)}  "
           f"merge bound {height * glyphs.MERGED_RUN_FRACTION:.1f}px"]
    for row in mask[:, left:right]:
        out.append("      " + "".join("#" if value else " " for value in row))
    ruler = [" "] * (right - left)
    for start, end in runs:
        for x in range(start - left, end - left):
            ruler[x] = "-"
        ruler[start - left] = "["
        ruler[end - 1 - left] = "]"
    out.append("      " + "".join(ruler))

    # What each run classified as ON ITS OWN, which is what the reader
    # believed before any join or split repair was tried. A wrong letter
    # here is the font; a run boundary in the wrong place is segmentation,
    # and the two have completely different remedies.
    said = []
    for start, end in runs:
        glyph, aspect = glyphs.extract(mask, start, end)
        if glyph is None:
            said.append(("?", 0.0, end - start))
            continue
        char, score = glyphs.classify(glyph, aspect, font, skin=skin)
        said.append((char or "?", score, end - start))
    out.append("      per-run: " + "  ".join(
        f"{char}({width}px"
        + (" WIDE" if width >= height * glyphs.MERGED_RUN_FRACTION else "")
        + f",{score:.2f})"
        for char, score, width in said))
    return out


def read_labels(run_dir):
    labels_path = os.path.join(run_dir, "labels.tsv")
    if not os.path.exists(labels_path):
        return {}
    labels = {}
    with open(labels_path, encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[1].strip():
                labels[parts[0]] = parts[1].strip()
    return labels


def report(detail=None, kinds=None, only=None):
    """Score every labelled crop; the baseline lines and the misses.

    `detail`, when a list, collects the ASCII of every miss - narrowed to
    the miss `kinds` named, and to run folders containing `only`. The sink
    is threaded through THIS function rather than given its own walk of the
    corpus, so there is one answer to "how is a crop read" rather than two
    that can drift apart.
    """
    font = glyphs.load_font()
    lines = []
    misses_for_manifest = []
    for run_dir in sorted(glob.glob(str(CORPUS_DIR / "*"))):
        run = os.path.basename(run_dir)
        # The run folder's suffix names the skin the frames were captured
        # on (grab_frames names them), so the report reads each crop the
        # way the live reader would: with that skin's variants competing
        # and the other skin's quarantined. "unknown" runs read unskinned.
        skin = run.rsplit("_", 1)[-1]
        skin = skin if skin in ("annehk", "stock") else None
        labels = read_labels(run_dir)
        if not labels:
            continue
        totals = {"read": 0, "understood": 0, "event": 0, "wrong": 0}
        misses = []
        for name, truth in sorted(labels.items()):
            crop = cv2.imread(os.path.join(run_dir, name))
            if crop is None:
                continue
            _mask, runs = glyphs.segment_line(crop)
            text, _score = glyphs.read_line(crop, font, skin=skin)
            expected_event = glyphs.parse_event(truth)
            got_event = glyphs.parse_event(text) if text else None
            if text and got_event is None:
                # The same chain the live watcher runs: a read line that
                # parses to nothing gets one vocabulary-nearest repair
                # attempt, so the event metric measures the whole layer.
                repair = glyphs.nearest_event_repair(text)
                if repair is not None:
                    got_event = repair[0]
                if got_event is None:
                    # And the whole-line analyser, last, exactly as the
                    # watcher runs it - so this measures the whole chain.
                    matched = line_reader.nearest_line(text)
                    if matched is not None:
                        got_event = matched[1]
            if text:
                totals["read"] += 1
            if text == truth:
                totals["understood"] += 1
            if expected_event is not None and got_event == expected_event:
                totals["event"] += 1
            elif expected_event is not None and got_event is not None:
                # An event Loom STATED that the game never printed, counted
                # apart from a line it merely missed. The totals above score
                # both as zero, and that is the one distinction the
                # never-guess rule turns on: a missed line costs a stats
                # entry, an invented one poisons the checklist and the Town
                # Centre count. Keeping it in the committed baseline is what
                # makes `git diff` show a reader that has started guessing.
                totals["wrong"] += 1
            ok = (text == truth and (expected_event is None
                                     or got_event == expected_event))
            if text == truth and expected_event is None and got_event is None:
                # A labelled line that parses to nothing either way (an
                # attack-warning fragment, say) is understood and done.
                ok = True
            if not ok:
                kind = ("vocabulary"
                        if text == truth and got_event != expected_event
                        and got_event is None
                        else classify_miss(text, truth, len(runs)))
                misses.append((name, kind, truth, text or ""))
                misses_for_manifest.append(
                    (os.path.join(run_dir, name), truth))
                if (detail is not None
                        and (kinds is None or kind in kinds)
                        and (only is None or only in run)):
                    detail.append(f"  {run}/{name}  [{kind}]")
                    detail.extend(show_runs(crop, truth, text or "", font,
                                            skin=skin))
                    detail.append("")
        count = len(labels)
        lines.append(f"{run}: {count} labelled | "
                     f"read {totals['read']}/{count} | "
                     f"understood {totals['understood']}/{count} | "
                     f"event {totals['event']}/{count} | "
                     f"wrong {totals['wrong']}")
        for name, kind, truth, text in misses:
            lines.append(f"    {name}  [{kind:10}]  {truth!r}  read {text!r}")
    return lines, misses_for_manifest


SUMMARY = re.compile(
    r"^(?P<run>\S+): (?P<count>\d+) labelled \| read (?P<read>\d+)/\d+"
    r" \| understood (?P<understood>\d+)/\d+ \| event (?P<event>\d+)/\d+"
    r"(?: \| wrong (?P<wrong>\d+))?")


def scores(body):
    """{run: {metric: number}} parsed from a report body."""
    found = {}
    for line in body.splitlines():
        match = SUMMARY.match(line)
        if match:
            found[match.group("run")] = {
                key: int(match.group(key) or 0)
                for key in ("count", "read", "understood", "event", "wrong")}
    return found


def check(body):
    """Complaints about a fresh report against the committed baseline.

    An empty list means the change is safe to keep.

    The gate the reliability programme was missing, and why it was missing
    is the point. Every safeguard until now judged ONE line: does its glyph
    count reconcile, do its shapes match their labels, does it read itself
    back. All of those are local, and the failure they cannot see is a
    glyph cut from one line that breaks a DIFFERENT one.

    Measured, and it is why this exists: a 1080p harvest whose every line
    verified individually still put "--Fervor Research Complete--" - a line
    that same harvest had REFUSED - within reach of "--Bracer Research
    Complete--", a real technology that never happened. The per-line
    read-back guard is necessary and it is structurally blind here.
    Nothing smaller than the whole corpus can see it.

    Two rules, in the order they matter:

      * WRONG must never rise. An invented event is worse than a missing
        one, so this is a veto rather than a trade.
      * EVENTS must never fall. A plausible improvement once read 14% more
        lines while finding FEWER events, and only re-measuring events
        caught it.
    """
    if not BASELINE.exists():
        return ["no committed baseline to check against"]
    before = scores(BASELINE.read_text(encoding="utf-8"))
    after = scores(body)
    complaints = []
    for run, now in sorted(after.items()):
        was = before.get(run)
        if was is None:
            continue                  # a new corpus run has nothing to beat
        if now["wrong"] > was["wrong"]:
            complaints.append(
                f"{run}: INVENTED EVENTS {was['wrong']} -> {now['wrong']}")
        if now["event"] < was["event"]:
            complaints.append(
                f"{run}: events {was['event']} -> {now['event']}")
    for run in sorted(set(before) - set(after)):
        complaints.append(f"{run}: in the baseline but not measured now")
    return complaints


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest",
                        help="write a harvest manifest built from the misses")
    parser.add_argument("--check", action="store_true",
                        help="compare against the committed baseline and exit"
                             " non-zero on a regression, without rewriting it")
    parser.add_argument("--runs", nargs="?", const="", metavar="KINDS",
                        help="print every miss as PIXELS, with the run"
                             " boundaries the reader chose and what each run"
                             " classified as on its own. Optionally a"
                             " comma-separated list of miss kinds"
                             " (split,merge,one-sub,...)")
    parser.add_argument("--only", metavar="TEXT",
                        help="with --runs, only run folders naming this")
    arguments = parser.parse_args()
    detail = [] if arguments.runs is not None else None
    kinds = (set(filter(None, arguments.runs.split(",")))
             if arguments.runs else None)
    lines, misses = report(detail, kinds, arguments.only)
    if not lines:
        # A gate that cannot run must say so with its exit code - the same
        # repair digit_report needed: the corpus lives on one machine and
        # the repo is developed on three, and --check exiting 0 here read
        # as "no regression" where nothing was measured.
        print("no labelled corpus runs found - see tools/notif_corpus.py")
        if arguments.check:
            print("CHECK DID NOT RUN - the corpus is not on this machine, "
                  "so nothing was measured. Run the gate where the corpus "
                  "lives.")
            sys.exit(1)
        return
    body = "\n".join(lines) + "\n"
    print(body, end="")
    if detail is not None:
        # The pixels never touch the baseline: it is a committed file whose
        # git diff IS the review of a reader change, and burying that in
        # several hundred lines of ASCII would end the practice.
        print("")
        print(chr(10).join(detail))
        print(f"{len(detail)} lines of detail; the baseline was not written")
        return
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
    if arguments.manifest:
        with open(arguments.manifest, "w", encoding="utf-8") as fh:
            for path, truth in misses:
                fh.write(f"{path}\t{truth}\n")
        print(f"{len(misses)} misses -> harvest manifest {arguments.manifest}")


if __name__ == "__main__":
    main()
