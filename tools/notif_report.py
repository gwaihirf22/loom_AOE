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
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, paths  # noqa: E402
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


def report():
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
        totals = {"read": 0, "understood": 0, "event": 0}
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
            if text:
                totals["read"] += 1
            if text == truth:
                totals["understood"] += 1
            if expected_event is not None and got_event == expected_event:
                totals["event"] += 1
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
        count = len(labels)
        lines.append(f"{run}: {count} labelled | "
                     f"read {totals['read']}/{count} | "
                     f"understood {totals['understood']}/{count} | "
                     f"event {totals['event']}/{count}")
        for name, kind, truth, text in misses:
            lines.append(f"    {name}  [{kind:10}]  {truth!r}  read {text!r}")
    return lines, misses_for_manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest",
                        help="write a harvest manifest built from the misses")
    arguments = parser.parse_args()
    lines, misses = report()
    if not lines:
        print("no labelled corpus runs found - see tools/notif_corpus.py")
        return
    body = "\n".join(lines) + "\n"
    print(body, end="")
    BASELINE.write_text(body, encoding="utf-8")
    print(f"\nbaseline written to {BASELINE}")
    if arguments.manifest:
        with open(arguments.manifest, "w", encoding="utf-8") as fh:
            for path, truth in misses:
                fh.write(f"{path}\t{truth}\n")
        print(f"{len(misses)} misses -> harvest manifest {arguments.manifest}")


if __name__ == "__main__":
    main()
