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


def reconcile_runs(mask, runs, tokens):
    """Split merged runs until there are as many as the text needs.

    Adjacent letters touch and segment as one run - "ty" in University,
    "lt" in Built - and align_tokens rightly refuses a line whose counts do
    not reconcile, because a shifted pairing would file every glyph after
    the merge under the wrong letter. That refusal threw away most of a
    harvest: measured on isolated, perfectly legible lines from a live
    game, only 3 of 18 aligned and the rest were short by one or two runs.

    The reader already solves this at read time with a pinch split, and the
    same geometry works here with better information: the transcription
    says exactly how many glyphs there should be, so a split is only kept
    while it moves the count TOWARD that number. The widest run goes first,
    since a merged pair is wider than either letter alone.

    Still refuses rather than guesses - if the counts cannot be reconciled,
    align_tokens gets the runs unchanged and says no.
    """
    needed = sum(2 if token == "DASHES" else 1 for token in tokens)
    height = mask.shape[0]
    runs = list(runs)
    # Each pass splits at most one run, so the loop cannot spin: either the
    # count rises or there is nothing left wide enough to cut.
    while len(runs) < needed:
        widest = max(range(len(runs)), key=lambda i: runs[i][1] - runs[i][0])
        start, end = runs[widest]
        pieces = glyphs._pinch_split(mask, start, end, height)
        if len(pieces) < 2:
            break
        runs[widest:widest + 1] = pieces
    return runs


# How much of a line's height a LETTER's ink must span. A hyphen is a thin
# bar floating at mid-height; every letter, even "i" and "l", reaches from
# somewhere near the cap line down to the baseline.
MIN_LETTER_INK_SPAN = 0.45

# ...and how little of it a hyphen's may. Measured on real lines: hyphens
# span 0.10-0.20 of the height, the shortest letter 0.55.
MAX_DASH_INK_SPAN = 0.35


def ink_span(mask, start, end):
    """What fraction of the line's height this run's ink covers."""
    rows = np.where(mask[:, start:end].max(axis=1) > 0)[0]
    if rows.size == 0:
        return 0.0
    return float(rows[-1] - rows[0] + 1) / mask.shape[0]


def pairs_are_plausible(mask, pairs):
    """Would any of these pairings file a hyphen as a letter, or vice versa?

    The guard that stops a shifted alignment corrupting the font, and it
    exists because one did. reconcile_runs below splits merged runs to make
    the counts add up, and on one line it split the wrong run - which slid
    every pairing along by one and filed the leading "-" of the frame under
    upper_K. The reader then classified the "--" of EVERY line as "K", so
    "--House Built--" came back as "KKHouse BuiltKK" and the two commonest
    events in a build order stopped being reported at all. Caught by reading
    the corpus back and finding house and mill gone, not by anything failing.

    Height separates the two cleanly where width cannot: a hyphen is a short
    bar at mid-height, while even the narrow letters reach from near the cap
    line to the baseline. So a letter with a hyphen's vertical footprint, or
    a dash with a letter's, means the alignment has slipped and the whole
    line is refused.
    """
    for (start, end), label in pairs:
        span = ink_span(mask, start, end)
        dash = label.startswith("punct_hyphen") or label == "punct_dashes"
        if dash and span > MAX_DASH_INK_SPAN:
            return False
        if not dash and label.startswith(("upper_", "lower_", "digit_"))                 and span < MIN_LETTER_INK_SPAN:
            return False
    return True


def next_variant(label):
    existing = globbing.glob(str(glyphs.FONT_DIR / f"{label}_*.png"))
    numbers = [int(os.path.splitext(p)[0].rsplit("_", 1)[1])
               for p in existing]
    return max(numbers, default=0) + 1


def already_covered(label, boxed, font):
    """Is a near-identical variant of this glyph already on file?"""
    for template, _aspect, _scale, _skin in font.get(label, []):
        if float((digits._normalize(boxed) * template).mean()) > 0.98:
            return True
    return False


_harvest_font = None

# The skin the current harvest is cutting from, for the tag.
HARVEST_SKIN = None


def harvest(line_bgr, text, source_name):
    """Cut one transcribed line into labelled glyph files."""
    mask, runs = glyphs.segment_line(line_bgr)
    tokens = expected_tokens(text)
    pairs = align_tokens(tokens, runs, mask.shape[0])
    if pairs is None:
        pairs = align_tokens(tokens,
                             reconcile_runs(mask, runs, tokens),
                             mask.shape[0])
    if pairs is not None and not pairs_are_plausible(mask, pairs):
        pairs = None            # the alignment slipped; refuse the line
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
    # Load the font once per process, not once per line. It is seconds of
    # work now that it holds a thousand-odd variants, and this runs for
    # every line of a manifest - a 265-line manifest timed out at ten
    # minutes doing little else. The in-process additions below keep the
    # cached copy honest between lines.
    global _harvest_font
    if _harvest_font is None:
        _harvest_font = glyphs.load_font()
    font = _harvest_font
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
        # Tag the variant with the rendering it came from, so it only ever
        # competes near that rendering. Untagged harvests are what made two
        # mass harvests read MORE and understand LESS across every run at
        # once: a 15px "l" resembles a 21px "i" more than it resembles its
        # own letter elsewhere. The tag is the line's height over the
        # full-scale line height - the same hint read_line derives, so the
        # two agree by construction.
        tag = round(mask.shape[0] / glyphs.LINE_HEIGHT_AT_FULL_SCALE, 2)
        # A skin in the tag quarantines the variant to that skin's
        # rendering - the two skins draw DIFFERENT FONTS at the same
        # scale, and stock letterforms competing on Anne_HK lines
        # took six 1440p reads down the day stock coverage landed.
        stem = (f"{label}@{HARVEST_SKIN}~{tag:.2f}" if HARVEST_SKIN
                else f"{label}@{tag:.2f}")
        path = glyphs.FONT_DIR / f"{stem}_{next_variant(stem)}.png"
        cv2.imwrite(str(path), column_slice)
        font.setdefault(label, []).append(
            (digits._normalize(boxed),
             column_slice.shape[1] / column_slice.shape[0], tag,
             HARVEST_SKIN))
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
        tokens = expected_tokens(text)
        candidate = (align_tokens(tokens, runs, mask.shape[0])
                     or align_tokens(tokens,
                                     reconcile_runs(mask, runs, tokens),
                                     mask.shape[0]))
        if candidate and pairs_are_plausible(mask, candidate):
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
    parser.add_argument("--skin", metavar="NAME",
                        help="HUD skin the lines come from; tags the"
                             " variants so they only compete there")
    args = parser.parse_args()
    if args.skin:
        global HARVEST_SKIN
        HARVEST_SKIN = args.skin

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
