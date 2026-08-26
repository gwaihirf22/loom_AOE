"""
Loom — a second, independent reader for the notification corpus.

    python -m tools.notif_oracle --audit          # check the labels by eye
    python -m tools.notif_oracle --propose RUN    # label an unlabelled run

The corpus is the ruler every reader change is measured against, and until
now nothing had ever checked the ruler. Its labels are transcribed BY EYE,
and tools/notif_report.py rewrites its own baseline from them - so a
mistyped label silently becomes the target all future work optimises
toward. At 1920x1080 the text is fifteen pixels tall, which is exactly
where a human transcription is least trustworthy.

This reads the same crops with something built on entirely different
principles: RapidOCR, a pair of small ONNX neural nets, against Loom's
template matcher. Two readers sharing no code, no training and no
assumptions agreeing on a line is real evidence about that line.
Disagreeing means one of them is wrong and a person should look.

What it is NOT: ground truth, and not a reader for Loom. Measured over the
924-line corpus, RapidOCR reads 86.1% of lines exactly against the template
reader's 84.4% - but it WINS at 1080p (219 of 275 against 161) and LOSES at
1440p (401 of 453 against 438), so neither dominates. It costs about a
second a line against the template reader's 36ms, roughly 28x, which rules
it out of a 300ms poll budget by itself. And it always answers: there is no
"I could not read that" in an OCR engine, which is the never-guess rule
inverted. It belongs offline, as a witness.

RapidOCR is a development dependency only, never imported by loom/:

    ./.venv/Scripts/python.exe -m pip install rapidocr-onnxruntime
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import re
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, paths  # noqa: E402

CORPUS = paths.CAPTURES_DIR / "notif_corpus"


def load_engine():
    """The OCR engine, or None with an explanation."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("rapidocr-onnxruntime is not installed. It is a DEV dependency:")
        print("    ./.venv/Scripts/python.exe -m pip install"
              " rapidocr-onnxruntime")
        return None
    return RapidOCR()


# Every notification the game prints is framed by exactly two hyphens at
# each end, and its words are separated by exactly one space. The OCR's
# mistakes are overwhelmingly in that layout rather than in the letters -
# measured across the corpus: a doubled space in "Archery  Range", a
# dropped hyphen in "Mill Built-", an invented full stop in "Villager
# Created.". Repairing those is not guessing at content; it is writing the
# engine's answer in the game's own punctuation.
#
# Anything that changes a LETTER is deliberately not repaired. A missing
# space ("MarketBuilt") could be split by consulting the vocabulary, and
# that is precisely the sort of help that would make this witness agree
# with the reader it exists to check.
TRAILING_STOP = re.compile(r"[.,]+(?=-*$)")


def normalise(text):
    """The OCR's answer in the game's punctuation, letters untouched."""
    text = text.strip()
    if not text:
        return ""
    text = TRAILING_STOP.sub("", text)
    text = re.sub(r"\s+", " ", text)
    # One to four hyphens at either end become exactly two. A line with no
    # leading hyphens at all is left alone: that is a real disagreement
    # about whether the frame was seen, not a typo.
    text = re.sub(r"^-{1,4}\s*", "--", text)
    text = re.sub(r"\s*-{1,4}$", "--", text)
    return text.strip()


def read_with(engine, image):
    """One line as the OCR engine reads it, normalised."""
    result, _elapsed = engine(image)
    if not result:
        return ""
    return normalise(" ".join(str(box[1]) for box in result))


def labels_of(run_dir):
    found = {}
    path = os.path.join(run_dir, "labels.tsv")
    if not os.path.exists(path):
        return found
    for line in open(path, encoding="utf-8"):
        parts = line.rstrip("\n").split("\t")
        if len(parts) >= 2 and parts[1].strip():
            found[parts[0]] = parts[1].strip()
    return found


def skin_of(run):
    tail = run.rsplit("_", 1)[-1]
    return tail if tail in ("annehk", "stock") else None


def audit(engine, only=None):
    """Where the two readers and the typed label disagree.

    Three-way, and which pair disagrees says what to do about it:

      * both readers agree with each other but NOT the label -> the label
        is the thing most likely wrong. This is the case worth having.
      * the readers disagree and one matches the label -> an ordinary
        reader miss, already counted by notif_report.
      * all three differ -> the crop is bad, or the line is genuinely hard.
    """
    font = glyphs.load_font()
    suspect_total = audited = 0
    for run_dir in sorted(glob.glob(str(CORPUS / "*"))):
        run = os.path.basename(run_dir)
        if only and only not in run:
            continue
        labels = labels_of(run_dir)
        if not labels:
            continue
        skin = skin_of(run)
        agreed = 0
        flagged = []
        for name, label in sorted(labels.items()):
            image = cv2.imread(os.path.join(run_dir, name))
            if image is None:
                continue
            audited += 1
            seen = read_with(engine, image)
            template, _score = glyphs.read_line(image, font, skin=skin)
            if seen == label:
                agreed += 1
                continue
            if seen and seen == template:
                # Two readers with nothing in common, agreeing against the
                # label. That is the strongest evidence this tool can
                # produce, and it points at the transcription.
                flagged.append((name, label, seen))
        suspect_total += len(flagged)
        print(f"{run[:52]:52} {len(labels):4d} labelled | "
              f"OCR agrees {agreed:4d} | LABEL SUSPECT {len(flagged):3d}")
        for name, label, seen in flagged:
            print(f"    {name}  label {label!r}")
            print(f"        both readers say {seen!r}")
    print(f"\n{audited} labels audited | {suspect_total} where both readers"
          f" agree against the label")
    if suspect_total:
        print("Look at those by eye: a wrong label is a wrong target for"
              " every future measurement.")


def propose(engine, run):
    """Write labels_proposed.tsv for a run's unlabelled crops.

    Only lines where BOTH readers agree are proposed, and the file is
    never promoted to labels.tsv automatically. An agreed line is a strong
    candidate, not a fact, and promoting it is a person's decision -
    the corpus is what everything else is judged against.
    """
    run_dir = str(CORPUS / run)
    if not os.path.isdir(run_dir):
        print(f"no such corpus run: {run_dir}")
        return
    font = glyphs.load_font()
    skin = skin_of(run)
    have = labels_of(run_dir)
    crops = sorted(glob.glob(os.path.join(run_dir, "line_*.png")))
    proposed, disputed = [], 0
    for path in crops:
        name = os.path.basename(path)
        if name in have:
            continue
        image = cv2.imread(path)
        if image is None:
            continue
        seen = read_with(engine, image)
        template, _score = glyphs.read_line(image, font, skin=skin)
        if seen and seen == template:
            proposed.append((name, seen))
        else:
            disputed += 1
    out = os.path.join(run_dir, "labels_proposed.tsv")
    with open(out, "w", encoding="utf-8") as handle:
        for name, text in proposed:
            handle.write(f"{name}\t{text}\tagreed\n")
    print(f"{run}: {len(crops)} crops, {len(have)} already labelled")
    print(f"    {len(proposed)} proposed (both readers agree) -> {out}")
    print(f"    {disputed} left alone (the readers disagree - read by eye)")


def main():
    parser = argparse.ArgumentParser(
        description="An independent OCR witness for the notification corpus.")
    parser.add_argument("--audit", action="store_true",
                        help="check the typed labels against both readers")
    parser.add_argument("--propose", metavar="RUN",
                        help="propose labels for one run's unlabelled crops")
    parser.add_argument("--run", metavar="NAME",
                        help="limit --audit to runs matching NAME")
    arguments = parser.parse_args()
    if not (arguments.audit or arguments.propose):
        parser.print_help()
        return
    engine = load_engine()
    if engine is None:
        sys.exit(1)
    if arguments.audit:
        audit(engine, arguments.run)
    if arguments.propose:
        propose(engine, arguments.propose)


if __name__ == "__main__":
    main()
