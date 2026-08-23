"""
Loom — build a labelled corpus of notification lines from capture runs.

    python -m tools.notif_corpus collect <run> [<run> ...]
    python -m tools.notif_corpus propose <run> [--key <other run>]
    python -m tools.notif_corpus status

The reliability program's ground truth. `collect` pulls every DISTINCT
legible line out of a capture run into captures/notif_corpus/<run>/ with
numbered contact sheets for verification. `propose` writes
labels_proposed.tsv: the reader's own text where it parses to an event, a
nearest-phrase match against a companion run's events where one is given
(the same game captured at another resolution is an answer key), blank
where only eyes can say. Promoting proposals into labels.tsv is the
verification step, done by looking at the sheets - never automatically,
because a wrong label taints every measurement built on it.

What counts as a distinct legible line, and why each filter exists:

  * trimmed to the notification box - the capture reaches past it, and a
    speck of terrain past the edge is an extra glyph run;
  * isolated - the band reproduces as ONE line when re-found in its own
    crop, so what gets labelled is what the reader will actually see (a
    crop carrying a slice of its neighbour refuses at harvest and measures
    nothing);
  * deduped by run-width signature - the same phrase rendered at the same
    sub-pixel offset is one entry, so the corpus counts RENDERINGS rather
    than sightings and a lingering line does not weigh 30x.

Mid-fade and mid-scroll bands fail the isolation check and stay out: the
live watcher tolerates those by design, and counting them would bury the
signal under noise no reader could fix.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, notifications, paths  # noqa: E402

CORPUS_DIR = paths.CAPTURES_DIR / "notif_corpus"

# Where the frame captures live. The corpus OUTPUT always goes beside this
# checkout, but the frames may be elsewhere - the checklist branch works in
# a git worktree, and captures/ is gitignored so a worktree has none.
FRAMES_DIR = paths.CAPTURES_DIR

# A crop smaller than this is a fragment, not a line worth labelling.
MIN_WIDTH = 100
MIN_HEIGHT = 12

# How far (as a fraction of the phrase's length) a misread may sit from an
# answer-key phrase and still be proposed as it. Generous for a reader
# making single-letter substitutions, tight enough that a different phrase
# of similar length does not win - and it is only ever a PROPOSAL.
MAX_EDIT_FRACTION = 0.25


def trim_to_box(line):
    """Cut a line crop back to the notification box it is drawn on."""
    gray = cv2.cvtColor(line, cv2.COLOR_BGR2GRAY)
    dark_columns = (gray < glyphs.DARK_LEVEL).mean(axis=0) > 0.45
    if not dark_columns.any():
        return None
    edge = int(np.max(np.where(dark_columns))) + 1
    return line[:, :edge] if edge > 60 else None


def distinct_lines(run):
    """{signature: line_bgr} for every distinct legible line in a run."""
    files = sorted(glob.glob(str(FRAMES_DIR / run / "frame_*.png")))
    found = {}
    for path in files:
        frame = cv2.imread(path)
        if frame is None:
            continue
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = notifications.panel_region(width, height)
        panel = frame[y1:y2, x1:x2]
        for (a, b) in glyphs.find_lines(panel):
            line = trim_to_box(panel[a:b])
            if (line is None or line.shape[1] < MIN_WIDTH
                    or line.shape[0] < MIN_HEIGHT):
                continue
            if len(glyphs.find_lines(line)) != 1:
                continue                      # not isolated: skip
            mask, runs = glyphs.segment_line(line)
            if not (6 <= len(runs) <= 45):
                continue
            gray = cv2.cvtColor(line, cv2.COLOR_BGR2GRAY)
            darkness = (gray < glyphs.DARK_LEVEL).mean()
            if darkness < 0.35:
                continue
            key = tuple(end - start for start, end in runs)
            # Keep the darkest example of each signature: the cleanest box
            # behind the text is the one whose glyphs cut best.
            if key not in found or darkness > found[key][0]:
                found[key] = (darkness, line)
    return {key: line for key, (_darkness, line) in found.items()}


def collect(run):
    out = CORPUS_DIR / run
    os.makedirs(out, exist_ok=True)
    for old in glob.glob(str(out / "line_*.png")):
        os.remove(old)
    lines = distinct_lines(run)
    names = []
    for index, line in enumerate(lines.values(), start=1):
        name = f"line_{index:04d}.png"
        cv2.imwrite(str(out / name), line)
        names.append(name)
    _sheets(out, names)
    print(f"{run}: {len(names)} distinct lines -> {out}")
    return names


def _sheets(out, names, per_sheet=15):
    for old in glob.glob(str(out / "SHEET_*.png")):
        os.remove(old)
    for start in range(0, len(names), per_sheet):
        rows = []
        for name in names[start:start + per_sheet]:
            line = cv2.imread(str(out / name))
            clipped = line[:, :min(line.shape[1], 560)]
            scaled = cv2.resize(clipped, None, fx=3, fy=3,
                                interpolation=cv2.INTER_NEAREST)
            tag = np.zeros((scaled.shape[0], 150, 3), np.uint8)
            cv2.putText(tag, name[5:9], (4, scaled.shape[0] // 2 + 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            rows.append(np.hstack([tag, scaled]))
        width = max(r.shape[1] for r in rows)
        height = sum(r.shape[0] + 10 for r in rows)
        sheet = np.zeros((height, width, 3), np.uint8)
        y = 0
        for row in rows:
            sheet[y:y + row.shape[0], :row.shape[1]] = row
            y += row.shape[0] + 10
        cv2.imwrite(str(out / f"SHEET_{start // per_sheet + 1:02d}.png"),
                    sheet)


# Subjects whose printed form is not the naive Title Case of their slug.
# slugify strips punctuation, so back-forming cannot recover it - the first
# baseline flagged its own labels as misses because the reader had read
# "(Packed)" CORRECTLY and the label had not.
PRINTED_FORMS = {
    "trebuchet_packed": "Trebuchet (Packed)",
    "trebuchet_unpacked": "Trebuchet (Unpacked)",
    # The game hyphenates these; slugify flattened the hyphens away, and
    # the harvest guards refused every crop whose label lacked them - 172
    # refusals in one manifest, almost all this family.
    "two_handed_swordsman": "Two-Handed Swordsman",
    "two_man_saw": "Two-Man Saw",
    "man_at_arms": "Man-at-Arms",
}


def phrase_for(event):
    """The line the game printed, back-formed from a parsed event."""
    if event == "town_center_built":
        return "--Town Center Built--"
    kind, _, subject = event.partition(":")
    if not subject or kind == "line":
        return None
    words = PRINTED_FORMS.get(subject) or " ".join(
        word.capitalize() for word in subject.split("_"))
    return {"built": f"--{words} Built--",
            "created": f"--{words} Created--",
            "found": f"--{words} Found--",
            "researched": f"--{words} Research Complete--"}.get(kind)


def _distance(a, b):
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def key_phrases(key_run):
    """Every phrase a companion run's events say the game printed.

    Read from the key run's already-collected corpus crops - collect runs
    first, and re-walking a thousand frames to answer a question the crops
    already answer would double the cost of every propose.
    """
    font = glyphs.load_font()
    phrases = set()
    for path in glob.glob(str(CORPUS_DIR / key_run / "line_*.png")):
        line = cv2.imread(path)
        if line is None:
            continue
        text, _score = glyphs.read_line(line, font)
        event = glyphs.parse_event(text) if text else None
        phrase = phrase_for(event) if event else None
        if phrase:
            phrases.add(phrase)
    return phrases


def propose(run, key_run=None):
    out = CORPUS_DIR / run
    crops = sorted(glob.glob(str(out / "line_*.png")))
    if not crops:
        print(f"{run}: nothing collected - run collect first")
        return
    font = glyphs.load_font()
    phrases = key_phrases(key_run) if key_run else set()
    rows = []
    counts = {"read": 0, "key": 0, "blank": 0}
    for path in crops:
        line = cv2.imread(path)
        text, _score = glyphs.read_line(line, font)
        event = glyphs.parse_event(text) if text else None
        if event and phrase_for(event):
            rows.append((os.path.basename(path), phrase_for(event), "read"))
            counts["read"] += 1
            continue
        if text and phrases:
            best = min(phrases, key=lambda p: _distance(text, p))
            if _distance(text, best) <= len(best) * MAX_EDIT_FRACTION:
                rows.append((os.path.basename(path), best, "key"))
                counts["key"] += 1
                continue
        rows.append((os.path.basename(path), "", "blank"))
        counts["blank"] += 1
    with open(out / "labels_proposed.tsv", "w", encoding="utf-8") as fh:
        for name, label, source in rows:
            fh.write(f"{name}\t{label}\t{source}\n")
    print(f"{run}: {counts['read']} self-read, {counts['key']} answer-key, "
          f"{counts['blank']} need eyes -> labels_proposed.tsv")


def promote(run):
    """labels_proposed.tsv -> labels.tsv, behind a structural cross-check.

    Proposals are NOT trusted equally. A self-read label (the reader's own
    text parsed to an event) is strong - a misread landing on a valid
    phrase needs several coordinated errors. An answer-key label is a
    nearest-phrase guess and is exactly the class that once made a harvest
    read MORE and understand LESS, so it must also RECONCILE structurally:
    the crop's glyph-run count has to sit within the tolerance of what the
    proposed phrase needs (merged pairs make the count run one or two
    short; more than that means the match is wrong, not the rendering).
    Blanks never promote - they wait for eyes on the contact sheets.

    Every promoted row keeps its source column, so a later audit can ask
    "show me everything believed on the key's word alone".
    """
    from tools.build_notification_font import expected_tokens
    out = CORPUS_DIR / run
    proposed = out / "labels_proposed.tsv"
    if not os.path.exists(proposed):
        print(f"{run}: nothing proposed")
        return
    kept, culled, blank = [], 0, 0
    with open(proposed, encoding="utf-8") as fh:
        for row in fh:
            parts = row.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[1].strip():
                blank += 1
                continue
            name, label, source = parts[0], parts[1], parts[2]
            crop = cv2.imread(str(out / name))
            if crop is None:
                continue
            _mask, runs = glyphs.segment_line(crop)
            needed = sum(2 if token == "DASHES" else 1
                         for token in expected_tokens(label))
            # Merges shorten the run count; anything further out than a
            # couple of welds means the proposal does not fit these pixels.
            if not (needed - 3 <= len(runs) <= needed + 2):
                culled += 1
                continue
            kept.append((name, label, source))
    with open(out / "labels.tsv", "w", encoding="utf-8") as fh:
        for name, label, source in kept:
            fh.write(f"{name}\t{label}\t{source}\n")
    print(f"{run}: promoted {len(kept)}, culled {culled} (structure), "
          f"{blank} blank await eyes")


def status():
    for out in sorted(glob.glob(str(CORPUS_DIR / "*"))):
        run = os.path.basename(out)
        crops = len(glob.glob(os.path.join(out, "line_*.png")))
        labelled = 0
        labels = os.path.join(out, "labels.tsv")
        if os.path.exists(labels):
            with open(labels, encoding="utf-8") as fh:
                labelled = sum(1 for line in fh if line.strip())
        print(f"   {run}: {crops} crops, {labelled} labelled")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",
                        choices=["collect", "propose", "promote", "status"])
    parser.add_argument("runs", nargs="*")
    parser.add_argument("--key", help="companion run to use as answer key")
    parser.add_argument("--captures",
                        help="directory holding the run_* frame folders")
    arguments = parser.parse_args()
    if arguments.captures:
        global FRAMES_DIR
        import pathlib
        FRAMES_DIR = pathlib.Path(arguments.captures)
    if arguments.command == "status":
        status()
        return
    for run in arguments.runs:
        if arguments.command == "collect":
            collect(run)
        elif arguments.command == "promote":
            promote(run)
        else:
            propose(run, arguments.key)


if __name__ == "__main__":
    main()
