"""
Loom — cut digit templates from capture runs (development tool).

Written to chase misreads on the stock HUD at 1920x1080, where glyphs scored
a median 0.32 against the shipped templates. It did not end up producing the
fix, and that is worth recording: laying the harvested glyphs out as ASCII
showed every one of them carrying a speck of banner art at the top, a blank
gap, then the digit squashed below. The villager band was reaching above the
number, and the row-trim was spanning from the speck down to the digit - so
the digits were being matched in a shape they never had on screen. Moving
the band's top edge fixed it (loom/hud.py), and no new template was needed.

The tool stays because it is how that was seen, and because the day a
genuinely new rendering does need templates, this is the way to cut them.

    python -m tools.build_digit_templates captures/run_... --bands villagers --list
    python -m tools.build_digit_templates captures/run_... --bands villagers         --write templates/digits/some_set --label 0=4 --label 1=1

--list prints every distinct shape as ASCII, numbered, with how many samples
back it. Nothing is written until --write, and then only the shapes named by
--label, because the one thing this must never do is guess: a mislabelled
template is not a missing read, it is a confident wrong number in every band
at every resolution.

Two lessons from using it, both measured:

  * Harvest from ONE band whose segmentation you have checked. A band that
    mis-segments offers fragments of digits as though they were digits, and
    the clock band at this size served up 2px slivers of hollow zeros.
  * An INCOMPLETE set is worse than none. Adding harvested templates for
    eight of the ten digits made the clock worse, not better - 76 backwards
    readings against 18 - because a "2" with no template of its own matches
    a harvested "3" of the same rendering family strongly enough to pass the
    gate. Unreadable is safe; confidently wrong is not.

Candidates are cut through the same chain the reader uses - find_column_runs,
is_character, extract_glyph - so what lands on disk is exactly the 14x20 form
classify_glyph compares against, not an approximation of it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import pathlib

import cv2
import numpy as np

from loom import anchor, digits, hud, queue, reader, resources

# Two glyphs count as the same shape above this correlation. 0.80 was picked
# by measurement: it collapses 1375 villager-band candidates into 25 shapes,
# which is few enough to read by eye and many enough that the "0" and "8" of
# the same band do not land in one bucket.
SAME_SHAPE = 0.80

# Banner art bleeds into the top rows of the stock villager band. Real digits
# there stand 9 or more rows; the decoration is 1 to 4.
MIN_GLYPH_ROWS = 6

# Shapes with fewer samples than this are noise - a smudge that appeared on
# one frame - and are not worth offering as a template.
MIN_SAMPLES = 5


def bands(found, resource_regions):
    """Every region worth cutting digits from, with how to binarise each -
    the four readers ink their numbers differently, and a template is only
    useful if it was cut through the same mask the reader will use."""
    out = [
        ("villagers", found["villagers"],
         lambda crop: digits.to_binary(crop, digits.ICON_BOX_THRESHOLD)),
        ("villagers_badge", found["villagers"],
         lambda crop: digits.white_mask(crop, digits.BADGE_WHITE)),
        ("clock", found["clock_band"],
         lambda crop: digits.white_mask(digits._fit_clock_rows(crop),
                                        digits.WHITE_STRICT)),
        ("population", found["population"], digits.yellow_pop_mask),
    ]
    for name, region in sorted(resource_regions.items()):
        # Both passes read_one tries, for the same reason it tries both:
        # the mod prints these yellow below the icon, stock stamps them
        # white inside it.
        out.append((f"resource_{name}", region, resources.yellow_mask))
        out.append((f"resource_{name}_white", region,
                    lambda crop: digits.white_mask(crop, digits.BADGE_WHITE)))
    return out


def candidates(run_dirs, wanted_bands=None):
    """Every glyph-shaped thing in the chosen bands of every frame."""
    pop_templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    wood_templates = {p: queue.load_wood_template(p) for p in hud.PROFILES}

    found_glyphs = []
    for run in run_dirs:
        paths = sorted(glob.glob(os.path.join(run, "*.png")))
        located = None
        for path in paths[:30]:
            frame = cv2.imread(path)
            if frame is None:
                continue
            candidate = anchor.identify_hud(frame, pop_templates,
                                            wood_templates=wood_templates)
            if candidate and candidate["score"] >= reader.MIN_ANCHOR_SCORE:
                located = candidate
                break
        if located is None:
            print(f"  {os.path.basename(run)}: no HUD found, skipped")
            continue

        profile = located["profile"]
        scale = located["scale"]
        gate = reader.min_glyph_width(scale, profile)
        # The resource crops are found by matching the four resource icons,
        # exactly as reader.find_hud does; without them this tool would miss
        # the band the author sees misreading alongside the villager count.
        try:
            resource_regions = resources.locate_regions(
                cv2.imread(paths[0]),
                resources.load_resource_templates(profile), scale, profile)
        except Exception:
            resource_regions = {}
        print(f"  {os.path.basename(run)}: {profile.name} at scale "
              f"{scale:.3f}, {len(paths)} frames")

        for path in paths:
            frame = cv2.imread(path)
            if frame is None:
                continue
            for name, region, binarise in bands(located, resource_regions):
                if wanted_bands and name not in wanted_bands:
                    continue
                x1, y1, x2, y2 = region
                crop = frame[y1:y2, x1:x2]
                if crop.size == 0:
                    continue
                try:
                    binary = binarise(crop)
                except Exception:
                    continue
                runs = digits.find_column_runs(binary)
                _boxes, tallest = digits._bar_context(binary, runs)
                for start, end in runs:
                    if not digits.is_character(binary, start, end, gate,
                                               tallest):
                        continue
                    box = digits._run_box(binary, start, end)
                    if box is None or box[1] < MIN_GLYPH_ROWS:
                        continue
                    glyph = digits.extract_glyph(binary, start, end)
                    if glyph is not None:
                        found_glyphs.append((name, glyph))
    return found_glyphs


def cluster(found_glyphs):
    """Group identical-looking glyphs. Returns the biggest groups first."""
    groups = []
    for band_name, glyph in found_glyphs:
        normalised = digits._normalize(glyph)
        for group in groups:
            if float((normalised * group["seed"]).mean()) > SAME_SHAPE:
                group["members"].append(glyph)
                group["bands"].add(band_name)
                break
        else:
            groups.append({"seed": normalised, "members": [glyph],
                           "bands": {band_name}})
    groups = [g for g in groups if len(g["members"]) >= MIN_SAMPLES]
    groups.sort(key=lambda g: -len(g["members"]))
    return groups


def average(glyphs):
    """The mean of a group, which is its cleanest single representative:
    the per-frame antialiasing noise averages out of it."""
    stack = np.stack([g.astype(np.float32) for g in glyphs])
    return np.clip(stack.mean(axis=0), 0, 255).astype(np.uint8)


def show(index, group, existing_templates):
    """Print one shape as ASCII, with what the current templates make of it.

    The existing guess is printed to inform, never to decide - it is the
    matcher that is wrong here, so its opinion is evidence about the bug
    rather than about the digit.
    """
    picture = average(group["members"])
    label, score = digits.classify_glyph(picture, existing_templates)
    print(f"\nshape {index}: {len(group['members'])} samples "
          f"from {', '.join(sorted(group['bands']))}")
    print(f"   current templates say {label} at {score:.2f}")
    for row in picture:
        print("      " + "".join("#" if v >= 128 else
                                 ("+" if v >= 60 else ".") for v in row))


def main():
    parser = argparse.ArgumentParser(
        description="Cut digit templates from capture runs.")
    parser.add_argument("runs", nargs="+", help="capture run directories")
    parser.add_argument("--list", action="store_true",
                        help="print every distinct shape and stop")
    parser.add_argument("--write", metavar="DIR",
                        help="write confirmed templates into this directory")
    parser.add_argument("--label", action="append", default=[],
                        metavar="SHAPE=DIGIT",
                        help="confirm that shape N is digit D; repeatable")
    parser.add_argument("--bands", default=None,
                        help="comma-separated band names to harvest from. "
                             "Worth using: a band whose segmentation is "
                             "shaky offers fragments of digits as if they "
                             "were digits, and a fragment saved as a "
                             "template is a permanent wrong answer.")
    arguments = parser.parse_args()

    print("reading capture runs...")
    wanted_bands = (set(arguments.bands.split(",")) if arguments.bands
                    else None)
    found_glyphs = candidates(arguments.runs, wanted_bands)
    print(f"\n{len(found_glyphs)} candidate glyphs")
    groups = cluster(found_glyphs)
    print(f"{len(groups)} distinct shapes with at least {MIN_SAMPLES} samples")

    existing = digits.load_digit_templates()
    if arguments.list or not arguments.label:
        for index, group in enumerate(groups):
            show(index, group, existing)
        if not arguments.label:
            print("\nNothing written. Read the shapes above, then re-run "
                  "with --write DIR and one --label SHAPE=DIGIT per shape "
                  "you are sure of.")
        return 0

    wanted = {}
    for pair in arguments.label:
        shape, _, digit = pair.partition("=")
        wanted[int(shape)] = int(digit)

    if not arguments.write:
        print("--label needs --write DIR")
        return 1

    out_dir = pathlib.Path(arguments.write)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for shape, digit in sorted(wanted.items()):
        if shape >= len(groups):
            print(f"no shape {shape}")
            return 1
        written.setdefault(digit, 0)
        written[digit] += 1
        name = f"{digit}_{written[digit]}.png"
        cv2.imwrite(str(out_dir / name), average(groups[shape]["members"]))
        print(f"wrote {name} from shape {shape} "
              f"({len(groups[shape]['members'])} samples)")

    missing = sorted(set(range(10)) - set(written))
    if missing:
        print(f"\nNOTE: no template for {missing}. The set is incomplete, so "
              "Loom will layer it over the default set rather than use it "
              "alone - see digits.load_digit_templates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
