"""
Loom — drop the frames a capture has nothing to say with.

    python -m tools.prune_captures                 # report; delete nothing
    python -m tools.prune_captures --delete        # actually remove them
    python -m tools.prune_captures --run NAME      # one run only

A capture is a frame every couple of seconds for a whole session, and a
lot of a session is not a game: the menu before it, the score screen
after, a pause in the middle. Those frames cost gigabytes and can never
teach anything, because Loom's whole input is the HUD and they have none.

Two things go, and the criterion for each is the game clock:

  * NO HUD AT ALL, before the game starts or after it ends - a menu or the
    post-game score screen. Nothing to read, ever.
  * A CLOCK ALREADY SEEN in this run - the game was paused, so the frame
    repeats a game-second that is already represented.

A no-HUD frame in the MIDDLE of a game is kept, and that exception is the
point rather than a detail. The game being on screen while the anchor
finds nothing is a bug, and the frame that shows one is the most valuable
in the run rather than the least - there is a capture folder here named
for exactly that failure. Nothing in the picture distinguishes the two
cases, because both are "the anchor found nothing"; what distinguishes
them is whether a game was running on either side. So the recorder keeps
everything and this decides later, on command, with the whole run in view.

Byte-equality is NOT the test, and that is worth recording because it is
the obvious thing to try. I measured it first: only 468 of 16626 frames
across the archive are byte-identical, 0.19GB of 102GB. A "static" screen
still animates water and idle units, so almost no two frames are equal.
Testing only the HUD strip instead finds nothing at all during play, for a
sharper reason - the clock is IN that strip and ticks every second, so no
two playing frames can ever match. Which is exactly why the clock is the
right thing to compare: it is the one part of the picture that says what
MOMENT this is, rather than what the screen happened to look like.

The rule this has to obey is "I did not read it" is not "it is not
there". A frame whose clock Loom cannot read is not garbage - it may be
the most interesting frame in the run, the one that shows why the reader
failed. So absence needs its own positive evidence, and here that is the
ANCHOR: no population icon anywhere in the frame means there is no HUD
and therefore no clock to have missed. An anchor found with an unreadable
clock is kept, always.

And a cheap "no" is never trusted. The per-frame check looks for the icon
only where the run has already put it, which is fast; anything it fails to
find is re-checked with the full multi-scale search before the frame can
be called garbage.
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

from loom import anchor, digits, hud, paths, queue, reader  # noqa: E402

# How far the population icon may have moved from where this run last saw
# it before the cheap check gives up and the full search is asked. The HUD
# does not move within a run - the window does not resize mid-game - so
# this only has to cover the match landing a pixel or two out.
NEARBY = 24

# A run whose frames are closer together than this in GAME seconds is left
# alone by the duplicate-clock rule. Two frames can then honestly share a
# clock second while the notification feed changes between them, and the
# second one would not be a repeat at all. grab_frames' default is a frame
# every two WALL seconds, which is two game seconds at 1x and more at the
# 1.7x of multiplayer, so this never fires on an ordinary capture - it is
# here so that a fast capture is not quietly thinned.
MIN_CADENCE_SECONDS = 2


def clock_of(image, found, glyph_templates):
    """The game clock in this frame, in seconds, or None if unread."""
    x1, y1, x2, y2 = found["clock_band"]
    band = image[max(0, y1):y2, max(0, x1):x2]
    if band.size == 0:
        return None
    seconds, _score = digits.read_clock_seconds(
        band, glyph_templates,
        reader.min_glyph_width(found["scale"], found["profile"]))
    return seconds


def _icon_is_where_it_was(image, template_gray, scale, box):
    """Cheap presence test: is the anchor icon still about here?

    One correlation over a small window at the one scale this run uses,
    against the full search's twenty-one scales over the whole strip.
    Only its POSITIVE answer is trusted - see the module docstring.
    """
    if box is None:
        return False
    x1, y1, x2, y2 = box
    height, width = image.shape[:2]
    top, left = max(0, y1 - NEARBY), max(0, x1 - NEARBY)
    bottom, right = min(height, y2 + NEARBY), min(width, x2 + NEARBY)
    window = image[top:bottom, left:right]
    if window.size == 0:
        return False
    sized = cv2.resize(template_gray, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA)
    if (sized.shape[0] > window.shape[0]
            or sized.shape[1] > window.shape[1] or sized.size == 0):
        return False
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY)
    scores = cv2.matchTemplate(gray, sized, cv2.TM_CCOEFF_NORMED)
    return float(np.max(scores)) >= reader.MIN_ANCHOR_SCORE


def survey(run_dir, templates, woods, glyph_templates):
    """What each frame of a run is: (frames, verdicts, cadence).

    verdicts maps a path to "no-hud", "repeat", or "keep".
    """
    frames = sorted(glob.glob(os.path.join(run_dir, "frame_*.png")))
    verdicts, clocks = {}, {}
    profile = scale = icon_box = None
    template_gray = None
    seen_seconds = []

    for path in frames:
        image = cv2.imread(path)
        if image is None:
            verdicts[path] = "keep"          # unreadable file: not my call
            continue

        found = None
        if profile is not None and _icon_is_where_it_was(
                image, template_gray, scale, icon_box):
            found = anchor.locate_regions(image, template_gray,
                                          near_scale=scale, profile=profile)
        if found is None or found.get("score", 0) < reader.MIN_ANCHOR_SCORE:
            # Either this run has not been anchored yet, or the cheap check
            # said no. Neither is evidence of absence, so ask properly.
            found = anchor.identify_hud(image, templates,
                                        wood_templates=woods)
        if found is None or found["score"] < reader.MIN_ANCHOR_SCORE:
            verdicts[path] = "no-hud"
            continue

        profile = found["profile"]
        scale = found["scale"]
        icon_box = found["icon"]
        template_gray = templates[profile]

        seconds = clock_of(image, found, glyph_templates)
        if seconds is None:
            # The HUD is there and I could not read its clock. That is a
            # reader failure, and the frame that shows one is worth more
            # than most - it is never thrown away.
            verdicts[path] = "keep"
            continue
        seen_seconds.append(seconds)
        if seconds in clocks:
            verdicts[path] = "repeat"
        else:
            clocks[seconds] = path
            verdicts[path] = "keep"

    # A no-HUD frame is only garbage where it belongs to the block before
    # the game or the block after it. One in the MIDDLE, with a HUD on
    # both sides, is the game still being on screen while the anchor was
    # lost - which is a bug, and the frame that shows it is the most
    # valuable thing in the run rather than the least. The captures folder
    # holds a run named for exactly that failure.
    #
    # Position is the only thing that separates the two cases. Both are
    # "the anchor found nothing", so no threshold, score or template can
    # tell them apart; what differs is whether a game was running either
    # side of the gap.
    playing = [index for index, path in enumerate(frames)
               if verdicts[path] in ("keep", "repeat")]
    rescued = 0
    if playing:
        first, last = playing[0], playing[-1]
        for index, path in enumerate(frames):
            if verdicts[path] == "no-hud" and first < index < last:
                verdicts[path] = "keep"
                rescued += 1

    ordered = sorted(set(seen_seconds))
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    cadence = float(np.median(gaps)) if gaps else 0.0
    return frames, verdicts, cadence, rescued


def main():
    parser = argparse.ArgumentParser(
        description="Report or remove capture frames with no game in them.")
    parser.add_argument("--run", metavar="NAME",
                        help="limit to run directories matching NAME")
    # Deleting is the non-default on purpose. The frames are the only
    # record of games that cannot be played again, so the tool's resting
    # state is to say what it WOULD do.
    parser.add_argument("--delete", action="store_true",
                        help="actually remove them (default: report only)")
    arguments = parser.parse_args()

    pattern = f"*{arguments.run}*" if arguments.run else "run_*"
    run_dirs = sorted(glob.glob(str(paths.CAPTURES_DIR / pattern)))
    if not run_dirs:
        print(f"no capture runs matching {pattern!r} in {paths.CAPTURES_DIR}")
        return

    templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    woods = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    glyph_templates = digits.load_digit_templates()

    total_frames = total_gone = 0
    total_bytes = 0
    for run_dir in run_dirs:
        if not os.path.isdir(run_dir):
            continue
        frames, verdicts, cadence, rescued = survey(run_dir, templates, woods,
                                                    glyph_templates)
        if not frames:
            continue
        no_hud = [p for p in frames if verdicts[p] == "no-hud"]
        repeats = [p for p in frames if verdicts[p] == "repeat"]
        thin = 0 < cadence < MIN_CADENCE_SECONDS
        if thin:
            # Captured faster than the clock ticks, so a shared second is
            # not necessarily a repeat. The no-HUD frames still go.
            repeats = []
        doomed = no_hud + repeats
        if len(doomed) >= len(frames):
            # Everything looks like garbage. That is a run with no game in
            # it at all, and emptying the folder silently would hide that -
            # better to say so and let a person delete the directory.
            print(f"{os.path.basename(run_dir)[:56]:56} "
                  f"{len(frames):5d} frames | NO GAME IN THIS RUN AT ALL "
                  f"- delete the folder by hand")
            continue
        size = sum(os.path.getsize(p) for p in doomed if os.path.exists(p))
        total_frames += len(frames)
        total_gone += len(doomed)
        total_bytes += size
        if doomed or rescued:
            note = "  (fast capture: repeats kept)" if thin else ""
            if rescued:
                # Worth saying out loud rather than burying: these frames
                # are the game on screen with the anchor lost, and a run
                # that has any is a run worth replaying.
                note += f"  [{rescued} MID-GAME ANCHOR LOSS kept]"
            print(f"{os.path.basename(run_dir)[:56]:56} "
                  f"{len(frames):5d} frames | {len(no_hud):5d} no-HUD"
                  f" + {len(repeats):5d} repeats | "
                  f"{size / 1e9:6.2f} GB{note}")
        if arguments.delete:
            # What went, written down. It cannot bring a frame back, but
            # "40GB was removed" and "these 6000 frames were removed, and
            # why each one" are very different things to have afterwards.
            with open(paths.CAPTURES_DIR / "pruned.tsv", "a",
                      encoding="utf-8") as record:
                for path in doomed:
                    why = "no-hud" if path in set(no_hud) else "repeat"
                    try:
                        os.remove(path)
                        record.write(f"{why}\t{path}\n")
                    except OSError as error:
                        print(f"    could not remove {path}: {error}")

    verb = "removed" if arguments.delete else "removable"
    share = 100.0 * total_gone / total_frames if total_frames else 0.0
    print(f"\n{total_frames} frames | {total_gone} {verb} ({share:.1f}%) | "
          f"{total_bytes / 1e9:.2f} GB")
    if not arguments.delete and total_gone:
        print("nothing was changed - re-run with --delete to remove them")


if __name__ == "__main__":
    main()
