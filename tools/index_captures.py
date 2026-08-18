"""
Loom — write captures/INDEX.md (development tool).

    python -m tools.index_captures

Capture runs accumulate faster than memory of them does. Eighty-eight
gigabytes of frames arrived in ten days, and by the end a folder called
run_20260726_192426 could only be described by replaying it - which costs
minutes and a lot of reading. This walks every run, measures what is cheap to
measure, and writes it down.

Everything here is DERIVED. The only hand-written part of a run's description
is the label in its own folder name, so there is one place to change it and no
index to keep in sync. Re-running this after a capture session is the whole
maintenance story.

Measured per run: frame count, resolution, HUD skin, the span of game clock
the frames cover, and disk size. Three frames are sampled rather than all of
them - a run's skin and resolution do not change mid-run, and sampling keeps
this to seconds instead of an hour.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os
import re

import cv2

from loom import anchor, digits, hud, paths, queue, reader

# What the eight-game Town Centre acceptance corpus expects, by folder
# timestamp. These numbers are the author's own count from playing the games -
# ground truth that exists nowhere in the frames themselves, so if it is not
# written down it is lost. A replay that disagrees with one of these is a
# regression, and that is the whole point of keeping the runs.
EXPECTED_TCS = {
    "20260731_152830": "7 real (frame cadence limits the replay to 5)",
    "20260731_123008": "3 real",
    "20260731_160909": "6+ real (cadence-limited to 5)",
    "20260731_161247": "4 real",
    "20260731_161610": "2 real",
    "20260801_121649": "1 real",
    "20260801_123106": "4 real",
    "20260801_145947": "5+ real, exact final count unconfirmed",
}


def describe(run_dir):
    """Measure one run. Returns a dict, or None if it holds no frames."""
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    if not frames:
        return None

    templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    glyphs = digits.load_digit_templates()

    skin, resolution, times = "unknown", "?", []
    # First, middle and last: enough to catch the skin and to bracket the
    # clock without opening two thousand files.
    for path in (frames[0], frames[len(frames) // 2], frames[-1]):
        image = cv2.imread(path)
        if image is None:
            continue
        resolution = f"{image.shape[1]}x{image.shape[0]}"
        found = anchor.identify_hud(image, templates, wood_templates=woods)
        if found is None or found["score"] < reader.MIN_ANCHOR_SCORE:
            continue
        skin = found["profile"].name
        x1, y1, x2, y2 = found["clock_band"]
        seconds, _ = digits.read_clock_seconds(
            image[max(0, y1):y2, max(0, x1):x2], glyphs,
            reader.min_glyph_width(found["scale"], found["profile"]))
        if seconds is not None:
            times.append(seconds)

    megabytes = sum(os.path.getsize(f) for f in frames) // (1024 * 1024)
    return {
        "name": os.path.basename(run_dir),
        "frames": len(frames),
        "resolution": resolution,
        "skin": skin,
        "clock": (f"{min(times) // 60}:{min(times) % 60:02d}"
                  f"-{max(times) // 60}:{max(times) % 60:02d}"
                  if times else "-"),
        "megabytes": megabytes,
    }


def label_of(name):
    """The hand-written part of a folder name, or "" when there is none."""
    match = re.match(r"run_\d{8}_\d{6}_[a-z]+_?(.*)$", name)
    return match.group(1).replace("-", " ") if match else ""


def main():
    runs = sorted(glob.glob(os.path.join(paths.CAPTURES_DIR, "run_*")))
    rows = [row for row in (describe(r) for r in runs if os.path.isdir(r))
            if row]

    lines = ["# Capture runs", "",
             "Written by `python -m tools.index_captures`; do not edit by",
             "hand. A run's description lives in its own folder name -",
             "`run_<timestamp>_<hud skin>[_<label>]` - so rename the folder to",
             "change what it says here.", "",
             f"{len(rows)} runs, "
             f"{sum(r['megabytes'] for r in rows) / 1024:.0f} GB.", "",
             "| run | frames | skin | game clock | size | what it is |",
             "|---|---|---|---|---|---|"]
    for row in rows:
        stamp = row["name"][4:19]
        note = label_of(row["name"])
        if stamp in EXPECTED_TCS:
            note = (f"**TC corpus** - {note or 'unlabelled'}; "
                    f"{EXPECTED_TCS[stamp]}")
        lines.append(
            f"| `{row['name']}` | {row['frames']} | {row['skin']} "
            f"| {row['clock']} | {row['megabytes']} MB "
            f"| {note or '_unlabelled_'} |")

    index = os.path.join(paths.CAPTURES_DIR, "INDEX.md")
    with open(index, "w") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"{len(rows)} runs -> {index}")


if __name__ == "__main__":
    main()
