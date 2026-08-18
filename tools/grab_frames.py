"""
Loom — screenshot grabber (development tool).

Saves PNG frames of the Age of Empires II window while you play, so I can tune
icon-anchoring and cut digit templates offline.

Run it as a module from the project root, so that `loom` can be imported:
    python -m tools.grab_frames                # every 2 seconds, until Ctrl+C
    python -m tools.grab_frames 1.0            # every 1 second
    python -m tools.grab_frames 3.0 --top 0.2  # save only the top 20% strip
    python -m tools.grab_frames --label 7tc-boom   # say what this run is FOR

Each run writes into its own folder under captures/, named

    run_<timestamp>_<hud skin>[_<label>]

The timestamp keeps runs apart and orders them; the skin is detected from the
frame rather than remembered, because which HUD is on screen is exactly the
thing that turned out to matter and exactly the thing easy to forget. The
label is optional and free text, and is worth the two seconds it costs: a
folder of 2000 frames whose only name is a timestamp is a folder nobody can
say anything about six weeks later without replaying it.

--top exists because full 1440p frames run 2-3 MB each, which adds up to
gigabytes across a whole game. Everything Loom reads off the HUD (resource bar,
global queue, idle bell, age, clock) sits in the top strip of the screen, so
for long queue-watching runs the strip is all I need. The cost: a cropped frame
loses the bottom-left building panel (the "Creating 99%" readout), which is the
ground truth for labelling what the TC is doing - so leave --top off for runs
meant for labelling.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import os
import time

import cv2

from loom import anchor, capture, hud, paths, queue


def slugify(text):
    """Free text into something safe to live in a path.

    One capture folder was called "run_20260731_152830_added multiple TCs. see
    notes in chat on screen". It said the right thing and no shell glob could
    touch it.
    """
    kept = [c.lower() if c.isalnum() else "-" for c in text.strip()]
    return "-".join("".join(kept).split("-")).strip("-") or "run"


def detect_skin(window):
    """Which HUD skin is on screen, for the folder name. "unknown" if none.

    Worth doing at capture time rather than replay time: the answer is only
    knowable while the game is up, and a run whose skin nobody recorded is a
    run that has to be re-identified every time it is opened.
    """
    try:
        frame = capture.capture_window(window)
    except capture.CaptureError:
        return "unknown"
    found = anchor.identify_hud(
        frame,
        {profile: anchor.load_template(profile) for profile in hud.PROFILES},
        wood_templates={profile: queue.load_wood_template(profile)
                        for profile in hud.PROFILES})
    if found is None or found["score"] < 0.8:
        return "unknown"
    return found["profile"].name


def main():
    parser = argparse.ArgumentParser(
        description="Save PNG frames of the AoE2 window while playing.")
    parser.add_argument("interval", nargs="?", type=float, default=2.0,
                        help="seconds between frames (default 2.0)")
    parser.add_argument("--top", type=float, default=None, metavar="FRACTION",
                        help="save only the top FRACTION of each frame, "
                             "e.g. 0.2 keeps the top fifth (default: full frame)")
    parser.add_argument("--label", default=None,
                        help="what this run is for, e.g. 7tc-boom - becomes "
                             "part of the folder name")
    args = parser.parse_args()
    interval = args.interval

    dpy = capture.open_display()
    window = capture.find_game_window(dpy)
    if window is None:
        print("Could not find the Age of Empires II window. Is the game running?")
        return

    width, height = capture.window_size(window)
    print(f"Found window: {width} x {height}")

    # Each run gets its own timestamped folder. An earlier version wrote
    # frame_0001.png every time, so a second run silently overwrote the frames
    # from the first one. A capture tool that destroys previous captures is a
    # trap, so runs are kept apart by construction.
    parts = [time.strftime("run_%Y%m%d_%H%M%S"), detect_skin(window)]
    if args.label:
        parts.append(slugify(args.label))
    run_directory = os.path.join(paths.CAPTURES_DIR, "_".join(parts))
    os.makedirs(run_directory, exist_ok=True)
    print(f"Saving a frame every {interval}s into {run_directory}/  —  Ctrl+C to stop.")

    frame_number = 0
    try:
        while True:
            image = capture.capture_window(window)
            if args.top is not None:
                # Keep just the top strip. NumPy slicing makes no copy until
                # imwrite reads it, so this costs nothing extra.
                image = image[:int(image.shape[0] * args.top), :]
            frame_number += 1
            path = os.path.join(run_directory, f"frame_{frame_number:04d}.png")
            cv2.imwrite(path, image)
            print(f"saved {path}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\nStopped. {frame_number} frames saved in {run_directory}/")


if __name__ == "__main__":
    main()
