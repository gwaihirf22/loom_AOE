"""
Loom — cut a notification phrase template from capture runs (development tool).

The game does not draw the notification feed by scaling one master rendering:
at 1920x1080 it lays the text out at a smaller point size, and the glyph shapes
are its own, not a shrunken copy of the 1440p ones. So a template harvested at
2560x1440 and resized down is compared against a shape the screen never drew -
the same fault the digit templates hit, for the same reason.

That is not a small effect. templates/notifications/town_center_built.png
compared against ITSELF at scale 0.735 scores 0.598 ink agreement, under the
0.6 gate: at 1080p a perfect, noise-free match could not pass. Live, the TC
line was on screen at correlation 0.81-0.92 on 46 looks and was recognised on
23 of them. Every echo guard in NotificationWatcher.watch is built on "was the
phrase sighted last look", so a coin-flip detector erases all of them, and one
lingering "--Town Center Built--" fired three times in a single game.

This tool cuts a template at the resolution the game actually draws it:

    python -m tools.cut_phrase_template captures/run_... --phrase town_center_built --list
    python -m tools.cut_phrase_template captures/run_... --phrase town_center_built --write

--list prints the harvest as ASCII beside the shipped template resized to the
same box, so the two can be compared by eye before anything is written. Read
them. The single cheapest lesson in this repo is that laying the pixels out
shows the fault immediately, and three separate bugs were visible the moment
somebody did.

Nothing is written without --write, and the file is named with the anchor scale
it was measured at (town_center_built@0.745.png), because that scale is what
notifications.py needs in order to resize it correctly for any other HUD size.

The harvest is an average of every frame that matched within --tolerance of the
best, each aligned to the best one first: per-frame antialiasing noise averages
out, and misaligned crops would otherwise blur the very outlines the ink gate
measures.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import pathlib

import cv2
import numpy as np

from loom import anchor, hud, notifications, paths, queue, reader

# Frames scoring within this of the best match join the average. Wide enough
# to gather a useful sample, tight enough that a half-faded line - which the
# feed draws while a message expires - cannot drag the outlines soft.
DEFAULT_TOLERANCE = 0.03

# How far a crop may be shifted to line it up with the best one before it is
# averaged in. The match location is already accurate to about a pixel; this
# only mops up the rounding.
ALIGN_SLACK = 2


def locate(run_dir):
    """The HUD profile and scale for a capture run, from its first frames."""
    pop_templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    wood_templates = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    for path in sorted(glob.glob(os.path.join(run_dir, "*.png")))[:30]:
        frame = cv2.imread(path)
        if frame is None:
            continue
        found = anchor.identify_hud(frame, pop_templates,
                                    wood_templates=wood_templates)
        if found and found["score"] >= reader.MIN_ANCHOR_SCORE:
            return found["profile"], found["scale"]
    return None, None


def best_match_in_frame(frame, phrase, watcher, scale):
    """The best-correlating crop of this phrase in one frame.

    Deliberately searches EVERY text band rather than just the bottom one:
    this is harvesting pixels, not deciding events, so where the line sits in
    the stack does not matter. Returns (score, harvest_scale, crop).
    """
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = notifications.panel_region(width, height)
    panel_gray = cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)

    best = None
    for top, bottom in notifications.text_line_bands(panel_gray, scale):
        for index in range(len(watcher.templates[phrase])):
            for delta in notifications.SCALE_BRACKET:
                sized = watcher._template_at(phrase, index, scale + delta)
                pad = max(int(round(notifications.STRIP_PAD * scale)),
                          sized.shape[0] - (bottom - top))
                strip = panel_gray[max(0, top - pad):bottom + pad]
                if (sized.shape[0] > strip.shape[0]
                        or sized.shape[1] > strip.shape[1]):
                    continue
                scores = cv2.matchTemplate(strip, sized,
                                           cv2.TM_CCOEFF_NORMED)
                _, score, _, where = cv2.minMaxLoc(scores)
                if best is not None and score <= best[0]:
                    continue
                crop = strip[where[1]:where[1] + sized.shape[0],
                             where[0]:where[0] + sized.shape[1]]
                best = (score, scale + delta, crop.copy())
    return best


def harvest(run_dirs, phrase, tolerance):
    """Every good look at this phrase across the runs, best first."""
    watcher = notifications.NotificationWatcher()
    if phrase not in watcher.templates:
        raise SystemExit(f"no template named {phrase}; have "
                         f"{sorted(watcher.templates)}")

    looks = []
    scale = None
    for run in run_dirs:
        profile, run_scale = locate(run)
        if profile is None:
            print(f"  {os.path.basename(run)}: no HUD found, skipped")
            continue
        scale = run_scale
        paths_in_run = sorted(glob.glob(os.path.join(run, "*.png")))
        print(f"  {os.path.basename(run)}: {profile.name} at scale "
              f"{run_scale:.3f}, {len(paths_in_run)} frames")
        for path in paths_in_run:
            frame = cv2.imread(path)
            if frame is None:
                continue
            found = best_match_in_frame(frame, phrase, watcher, run_scale)
            if found is not None and found[0] >= notifications.MIN_PHRASE_SCORE:
                looks.append(found)

    looks.sort(key=lambda look: -look[0])
    if not looks:
        return [], scale
    return [look for look in looks if look[0] >= looks[0][0] - tolerance], scale


def align(crop, reference):
    """Shift crop by up to ALIGN_SLACK to line it up with reference."""
    if crop.shape != reference.shape:
        return None
    padded = cv2.copyMakeBorder(crop, ALIGN_SLACK, ALIGN_SLACK, ALIGN_SLACK,
                                ALIGN_SLACK, cv2.BORDER_REPLICATE)
    scores = cv2.matchTemplate(padded, reference, cv2.TM_CCOEFF_NORMED)
    _, _, _, where = cv2.minMaxLoc(scores)
    return padded[where[1]:where[1] + reference.shape[0],
                  where[0]:where[0] + reference.shape[1]]


def average(looks):
    """The mean of the aligned crops - the cleanest single representative."""
    reference = looks[0][2]
    stack = [reference.astype(np.float32)]
    for _score, _scale, crop in looks[1:]:
        shifted = align(crop, reference)
        if shifted is not None:
            stack.append(shifted.astype(np.float32))
    mean = np.stack(stack).mean(axis=0)
    return np.clip(mean, 0, 255).astype(np.uint8), len(stack)


def as_ascii(image, indent="      "):
    return "\n".join(
        indent + "".join("#" if v >= 128 else ("+" if v >= 60 else ".")
                         for v in row)
        for row in image)


def show(harvested, shipped, phrase, harvest_scale, samples):
    """Print the harvest beside the shipped template, both at harvest size.

    Side by side is the point: the shipped rendering resized down is what the
    reader compares today, and seeing the two together is what makes the
    difference obvious rather than theoretical.
    """
    resized = cv2.resize(shipped, (harvested.shape[1], harvested.shape[0]),
                         interpolation=cv2.INTER_AREA)
    print(f"\n{phrase} harvested at scale {harvest_scale:.3f} from "
          f"{samples} frames, {harvested.shape[1]}x{harvested.shape[0]}")
    print(f"   ink agreement against the shipped template resized: "
          f"{notifications.ink_agreement(resized, harvested):.2f}")
    print("\n   SHIPPED template, resized to this box "
          "(what the reader compares today):")
    print(as_ascii(resized))
    print("\n   HARVESTED from the game's own rendering at this size:")
    print(as_ascii(harvested))


def main():
    parser = argparse.ArgumentParser(
        description="Cut a notification phrase template from capture runs.")
    parser.add_argument("runs", nargs="+", help="capture run directories")
    parser.add_argument("--phrase", required=True,
                        help="which phrase to cut, e.g. town_center_built")
    parser.add_argument("--list", action="store_true",
                        help="print the harvest as ASCII and stop")
    parser.add_argument("--write", action="store_true",
                        help="write it into templates/notifications/")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="score spread of frames joining the average")
    arguments = parser.parse_args()

    print("reading capture runs...")
    looks, _scale = harvest(arguments.runs, arguments.phrase,
                            arguments.tolerance)
    if not looks:
        print(f"\nno sighting of {arguments.phrase} cleared "
              f"{notifications.MIN_PHRASE_SCORE} in those runs.")
        return 1

    harvest_scale = looks[0][1]
    harvested, samples = average(looks)
    # Compare against the scale-1.0 harvest - the one that has been resized
    # down to do this job until now, and so the one worth seeing beside it.
    variants = notifications.NotificationWatcher().templates[arguments.phrase]
    shipped = max(variants, key=lambda variant: variant[1])[0]
    show(harvested, shipped, arguments.phrase, harvest_scale, samples)
    print(f"\n   best correlation {looks[0][0]:.2f}, "
          f"{len(looks)} frames within {arguments.tolerance}")

    if not arguments.write:
        print("\nNothing written. Compare the two pictures above, and re-run "
              "with --write only if the harvest is the phrase and is clean.")
        return 0

    out_dir = pathlib.Path(paths.TEMPLATES_DIR) / "notifications"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{arguments.phrase}@{harvest_scale:.3f}.png"
    cv2.imwrite(str(out_dir / name), harvested)
    print(f"\nwrote {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
