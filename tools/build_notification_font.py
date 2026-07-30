"""
Loom — harvest notification-font glyphs from captured frames.

    python -m tools.build_notification_font <image> "--Mill Built--"
    python -m tools.build_notification_font --list <image>
    python -m tools.build_notification_font --manifest pairs.tsv

Self-labeling: say once what a line reads, and its characters are cut,
labelled and filed into templates/notification_font/ - the reader can then
read those characters in any line the game ever prints. <image> can be a
full capture frame (the notification panel is searched) or an
already-cropped line. --list saves every line band found in a frame to
captures/notif_lines/ for transcribing. The manifest form takes
image<TAB>text rows for batch harvesting.

The tool uses the same segmentation as the runtime reader, so the font is
built from glyphs cut exactly the way they will later be read. On any
mismatch between what it sees and what the text says, it refuses and
writes a debug image instead of guessing - a mislabelled glyph would
quietly corrupt every future read.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob as globbing
import os
import sys

import cv2
import numpy as np

from loom import digits, glyphs, notifications, paths


def panel_lines(image):
    """Line crops from an image: the panel region of a frame, or the image
    itself when it is already a strip. Strips still go through find_lines -
    a saved crop can turn out to hold two stacked lines."""
    if image.shape[0] <= 80:
        bands = glyphs.find_lines(image)
        if len(bands) <= 1:
            return [image]
        return [image[a:b] for a, b in bands]
    height, width = image.shape[:2]
    x1, y1, x2, y2 = notifications.panel_region(width, height)
    panel = image[y1:y2, x1:x2]
    return [panel[a:b] for a, b in glyphs.find_lines(panel)]


def expected_tokens(text):
    """The text as glyph labels, in order, spaces dropped.

    "--" becomes a DASHES marker: the framing dashes sometimes render as
    one joined stroke and sometimes as two separate hyphens (sub-pixel
    position decides), so alignment resolves each marker against the runs
    it actually finds - see align_tokens.
    """
    tokens = []
    i = 0
    while i < len(text):
        if text[i] == " ":
            i += 1
        elif text.startswith("--", i):
            tokens.append("DASHES")
            i += 2
        else:
            tokens.append(glyphs.label_for(text[i]))
            i += 1
    return tokens


def align_tokens(tokens, runs, line_height):
    """Pair tokens with runs, resolving each DASHES marker by run width.

    A joined "--" run is much wider than a single hyphen, so the run's
    width against the line height says which rendering this is: wide ->
    one punct_dashes glyph; narrow -> two punct_hyphen glyphs. Returns
    [(run, label), ...] or None when the counts cannot be reconciled.
    """
    pairs = []
    r = 0
    for token in tokens:
        if r >= len(runs):
            return None
        if token != "DASHES":
            pairs.append((runs[r], token))
            r += 1
            continue
        start, end = runs[r]
        if (end - start) >= 0.55 * line_height:
            pairs.append((runs[r], "punct_dashes"))
            r += 1
        else:
            if r + 1 >= len(runs):
                return None
            pairs.append((runs[r], "punct_hyphen"))
            pairs.append((runs[r + 1], "punct_hyphen"))
            r += 2
    return pairs if r == len(runs) else None


def next_variant(label):
    existing = globbing.glob(str(glyphs.FONT_DIR / f"{label}_*.png"))
    numbers = [int(os.path.splitext(p)[0].rsplit("_", 1)[1])
               for p in existing]
    return max(numbers, default=0) + 1


def already_covered(label, boxed, font):
    """Is a near-identical variant of this glyph already on file?"""
    for template, _aspect in font.get(label, []):
        if float((digits._normalize(boxed) * template).mean()) > 0.98:
            return True
    return False


def harvest(line_bgr, text, source_name):
    """Cut one transcribed line into labelled glyph files."""
    mask, runs = glyphs.segment_line(line_bgr)
    tokens = expected_tokens(text)
    pairs = align_tokens(tokens, runs, mask.shape[0])
    if pairs is None:
        debug = line_bgr.copy()
        for start, end in runs:
            cv2.rectangle(debug, (start, 0), (end, debug.shape[0] - 1),
                          (0, 255, 255), 1)
        out = paths.CAPTURES_DIR / f"font_mismatch_{source_name}.png"
        os.makedirs(paths.CAPTURES_DIR, exist_ok=True)
        cv2.imwrite(str(out), debug)
        print(f"REFUSED {source_name}: {len(runs)} glyphs seen for "
              f"{text!r} - debug at {out}")
        return 0

    os.makedirs(glyphs.FONT_DIR, exist_ok=True)
    font = glyphs.load_font()
    written = 0
    for (start, end), label in pairs:
        # Full line height, matching glyphs.extract - vertical position is
        # part of the glyph's identity (see extract's docstring).
        column_slice = mask[:, start:end]
        boxed = cv2.resize(column_slice,
                           (digits.GLYPH_WIDTH, digits.GLYPH_HEIGHT),
                           interpolation=cv2.INTER_AREA)
        if already_covered(label, boxed, font):
            continue
        path = glyphs.FONT_DIR / f"{label}_{next_variant(label)}.png"
        cv2.imwrite(str(path), column_slice)
        font.setdefault(label, []).append(
            (digits._normalize(boxed),
             column_slice.shape[1] / column_slice.shape[0]))
        written += 1
    print(f"{source_name}: {written} new glyph variants "
          f"({len(pairs) - written} already covered)")
    return written


def list_lines(image_path):
    """Save every line band in a frame for the human to transcribe."""
    image = cv2.imread(image_path)
    out_dir = paths.CAPTURES_DIR / "notif_lines"
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    count = 0
    for index, line in enumerate(panel_lines(image), start=1):
        out = out_dir / f"{stem}_line{index}.png"
        cv2.imwrite(str(out), line)
        count += 1
    print(f"{count} line(s) saved to {out_dir}")


def harvest_image(image_path, text):
    image = cv2.imread(image_path)
    if image is None:
        print(f"could not read {image_path}")
        return
    stem = os.path.splitext(os.path.basename(image_path))[0]
    lines = panel_lines(image)
    if not lines:
        print(f"{image_path}: no text lines found in the panel")
        return
    # A frame can hold several lines; harvest the one that aligns with the
    # transcription (harvesting the wrong line refuses anyway).
    for line in lines:
        mask, runs = glyphs.segment_line(line)
        if align_tokens(expected_tokens(text), runs, mask.shape[0]):
            harvest(line, text, stem)
            return
    harvest(lines[0], text, stem)     # let it refuse with a debug image


def main():
    parser = argparse.ArgumentParser(
        description="Harvest notification-font glyphs")
    parser.add_argument("image", nargs="?")
    parser.add_argument("text", nargs="?")
    parser.add_argument("--list", metavar="IMAGE",
                        help="save the lines found in IMAGE for transcribing")
    parser.add_argument("--manifest", metavar="TSV",
                        help="batch harvest: image<TAB>text per row")
    args = parser.parse_args()

    if args.list:
        list_lines(args.list)
    elif args.manifest:
        with open(args.manifest, encoding="utf-8") as handle:
            for row in handle:
                row = row.strip()
                if not row or row.startswith("#"):
                    continue
                image_path, _, text = row.partition("\t")
                harvest_image(image_path.strip(), text.strip())
    elif args.image and args.text:
        harvest_image(args.image, args.text)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
