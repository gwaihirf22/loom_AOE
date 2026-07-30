"""
Loom — replay a capture run through the production pipeline (development tool).

Frames show what the game did; this shows what Loom BELIEVED at each frame:
the clock and population as read, every queue slot as classified, tracker
events as they fire, and the alert that would have been on screen. Debugging
becomes "find the line where belief diverges from the picture, then open that
one frame" - and re-running the same capture after a fix proves the fix
against identical input.

    python -m tools.replay_queue captures/run_20260724_182337
    python -m tools.replay_queue captures/run_.../ --events-only

The output is plain text on stdout, made to be piped, grepped, and pasted.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os

import cv2

from loom import (alerts, anchor, build_order, config, digits, filters,
                  notifications, pace, production, queue, report)
from loom.build_order import BuildOrder


def find_scale(frame_paths, pop_template):
    """Anchor once, like the live reader does at find_hud time."""
    for path in frame_paths:
        frame = cv2.imread(path)
        if frame is None:
            continue
        found = anchor.locate_regions(frame, pop_template)
        if found is not None and found["score"] >= 0.8:
            return found
    return None


def describe(slots):
    """One compact fragment per occupied slot."""
    if slots is None:
        return "queue=unreadable"
    if not slots:
        return "queue=EMPTY"
    parts = []
    for slot in slots:
        text = slot.identity or "?"
        if slot.count is not None:
            text += f"x{slot.count}"
        if slot.tint == "green" and slot.progress is not None:
            text += f":{slot.progress:.0%}"
        elif slot.tint:
            text += f":{slot.tint}"
        else:
            text += ":wait"
        parts.append(text)
    return "queue=[" + " ".join(parts) + "]"


def main():
    parser = argparse.ArgumentParser(
        description="Replay captured frames through Loom's queue pipeline.")
    parser.add_argument("run_dir", help="directory of frame_*.png captures")
    parser.add_argument("--events-only", action="store_true",
                        help="print only frames where an event fired or the "
                             "alert changed")
    parser.add_argument("--build", default=None,
                        help="build order for the end-of-run report "
                             "(default: the configured active build)")
    args = parser.parse_args()

    frame_paths = sorted(glob.glob(os.path.join(args.run_dir, "frame_*.png")))
    if not frame_paths:
        print(f"No frame_*.png in {args.run_dir}")
        return

    pop_template = anchor.load_template()
    regions = find_scale(frame_paths, pop_template)
    if regions is None:
        print("No frame with a readable HUD anchor in this run.")
        return
    scale = regions["scale"]
    digit_templates = digits.load_digit_templates()
    min_glyph_width = max(4, int(6 * scale))

    reader = queue.QueueReader()
    tracker = production.ProductionTracker()
    watcher = notifications.NotificationWatcher()
    policy = alerts.IdleTcPolicy()
    build = BuildOrder.load_by_name(args.build or config.active_build())
    pacer = pace.PaceTracker(build)
    stats = report.BuildReport()
    cx1, cy1, cx2, cy2 = regions["clock_band"]
    px1, py1, px2, py2 = regions["population"]
    vx1, vy1, vx2, vy2 = regions["villagers"]

    # The same clock filter the live reader runs: it holds the believed
    # time through a frame whose clock band was unreadable. Without it the
    # replay is STRICTER than reality - a real Town Centre firing was once
    # missed here only because the raw clock failed on exactly the frames
    # where its line sat at the bottom of the stack.
    clock_filter = filters.StableClock(max_step=30, required_repeats=2)

    last_alert = (None, None)
    for path in frame_paths:
        frame = cv2.imread(path)
        if frame is None:
            continue

        raw_clock, _ = digits.read_clock_seconds(
            frame[cy1:cy2, cx1:cx2], digit_templates, min_glyph_width)
        clock = clock_filter.update(raw_clock)
        pop = digits.read_population(
            frame[py1:py2, px1:px2], digit_templates, min_glyph_width)
        pop = pop if pop[0] is not None else None
        villagers, _ = digits.read_count(frame[vy1:vy2, vx1:vx2],
                                         digit_templates, min_glyph_width)

        # Like the live reader, only look at the feed while the HUD is up -
        # a full-screen menu replaces the whole scene.
        game_events = []
        if raw_clock is not None or villagers is not None:
            nx1, ny1, nx2, ny2 = notifications.panel_region(
                frame.shape[1], frame.shape[0])
            game_events = watcher.watch(frame[ny1:ny2, nx1:nx2], scale,
                                        clock)
        for name in game_events:
            if name == "town_center_built":
                tracker.register_tc_built()

        slots = reader.read(frame, scale)
        events = tracker.update(clock, slots)
        events = [f"game:{name}" for name in game_events] + events
        active = alerts.production_alerts(tracker, None, policy, clock, pop)

        delta = pacer.update(villagers, clock)
        extra = build_order.extra_villagers(build, villagers, clock)
        stats.update(clock, tracker, delta, slots, game_events,
                     extra=extra, villagers=villagers)
        if pacer.complete:
            stats.complete(clock)

        interesting = bool(events) or active != last_alert
        last_alert = active
        if args.events_only and not interesting:
            continue

        name = os.path.basename(path)
        pop_text = "--/--" if pop is None else f"{pop[0]}/{pop[1]}"
        line = (f"{name}  {filters.format_time(clock)}  pop {pop_text:>7}  "
                f"{describe(slots)}")
        if events:
            line += "  >>> " + ", ".join(events)
        for text, severity in active:
            line += f"  [ALERT {severity}: {text}]"
        print(line)

    print(f"\n=== build report ({build.name}"
          f"{'' if stats.completed_at else ' - build not completed'}) ===")
    for label, value, good in stats.summary(build):
        verdict = {True: "  [good]", False: "  [bad]", None: ""}[good]
        print(f"  {label:<22} {value}{verdict}")


if __name__ == "__main__":
    main()
