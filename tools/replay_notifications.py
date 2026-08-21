"""
Loom — replay the notification feed over capture runs (development tool).

Written for the question this cannot answer from one machine: does the
watcher count the right number of Town Centres when several are built close
together? The Windows side of this dual boot has exactly one capture run
containing a "--Town Center Built--" line at all, and one TC proves nothing
about two.

    python -m tools.replay_notifications captures/run_... --expect 3
    python -m tools.replay_notifications captures/run_* --sweep
    python -m tools.replay_notifications captures/run_... --looks 3

--expect turns it into a pass/fail check against what actually happened in
the game, which only the player knows. Say how many Town Centres were built
(counting the one you start with is up to you - the tool counts LINES, and
the starting TC never prints one).

Two things this measures that a single run of the overlay cannot.

LOOK DENSITY. --looks feeds each captured frame to the watcher more than
once, because the live overlay does: a capture is a couple of game-seconds
per frame while the overlay polls every 300ms and reads this feed every
third poll. That difference used to change the answer - a lingering line
re-fired at two looks per frame and not at one - which is how a rate
dependency hid in the guards for so long. If a run gives different counts at
different --looks, that is a bug and not a tuning problem.

REARM_SECONDS. --sweep re-runs each capture at several values and prints
what each one counts, because that constant is a straight trade between two
failures. Too low and detection flicker looks like a line leaving, so one
lingering line is counted twice - two phantom Town Centres, and the idle
alert nags for the rest of the game. Too high and a genuinely new line
inside the window is never counted - a missed Town Centre, which matters
most for one that was built and is sitting idle, because the queue cannot
see that one at all and the notification is the only evidence there is.
Real multi-TC captures are the only way to choose honestly.

The game clock is read from each frame rather than counted, so the game
seconds these constants are written in are the game's own - at 1.7x speed a
wall-clock estimate would be wrong by most of the window.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os

import cv2

from loom import anchor, digits, hud, notifications, queue, reader

# What --sweep tries. Spread around the shipped value rather than centred on
# it: the interesting question is how far it can move before a real capture
# changes its answer.
SWEEP = (0, 4, 6, 8, 10, 14)

# Phrases worth reporting on. The other two are single events a game, so
# their counts are not informative; town_center_built is the one that feeds
# a running total and the one that has been wrong.
WATCHED = "town_center_built"


def locate(run_dir):
    """The HUD profile and scale for a run, from its first readable frames."""
    pop_templates = {p: anchor.load_template(p) for p in hud.PROFILES}
    wood_templates = {p: queue.load_wood_template(p) for p in hud.PROFILES}
    for path in sorted(glob.glob(os.path.join(run_dir, "*.png")))[:30]:
        frame = cv2.imread(path)
        if frame is None:
            continue
        found = anchor.identify_hud(frame, pop_templates,
                                    wood_templates=wood_templates)
        if found and found["score"] >= reader.MIN_ANCHOR_SCORE:
            return found
    return None


def frames_of(run_dir, located, digit_templates):
    """Every frame as (game_time, panel), skipping unreadable ones.

    The clock is read per frame because these constants are in GAME
    seconds. Frames whose clock will not read are kept with the previous
    time rather than dropped: the feed is still on screen, and dropping
    them would invent absence that the live reader never saw.
    """
    profile, scale = located["profile"], located["scale"]
    gate = reader.min_glyph_width(scale, profile)
    # The HUD does not move within a run, so the bands found once are the
    # bands for every frame - and re-anchoring per frame would only add a
    # second failure mode to a tool whose job is to be trusted.
    x1, y1, x2, y2 = located["clock_band"]
    out = []
    last_time = 0.0
    for path in sorted(glob.glob(os.path.join(run_dir, "*.png"))):
        frame = cv2.imread(path)
        if frame is None:
            continue
        band = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
        seconds = None
        if band.size:
            seconds, _score = digits.read_clock_seconds(
                band, digit_templates, gate)
        if seconds is not None:
            last_time = float(seconds)
        height, width = frame.shape[:2]
        px1, py1, px2, py2 = notifications.panel_region(width, height)
        out.append((os.path.basename(path), last_time,
                    frame[py1:py2, px1:px2].copy()))
    return out


def replay(frames, scale, looks_per_frame, rearm_seconds=None):
    """Feed a run through a fresh watcher; return the TC events it reports.

    Between one captured frame and the next, game time is interpolated
    across the extra looks, so a denser poll rate does not also become a
    faster clock.
    """
    original = notifications.REARM_SECONDS
    if rearm_seconds is not None:
        notifications.REARM_SECONDS = rearm_seconds
    watcher = notifications.NotificationWatcher()
    fired = []
    try:
        for index, (name, moment, panel) in enumerate(frames):
            following = (frames[index + 1][1] if index + 1 < len(frames)
                         else moment)
            step = (following - moment) / max(1, looks_per_frame)
            for look in range(looks_per_frame):
                at = moment + look * step
                for event in watcher.watch(panel, scale, at):
                    if event == WATCHED:
                        fired.append((name, at))
    finally:
        notifications.REARM_SECONDS = original
    return fired


def sightings(frames, scale):
    """How reliably the phrase is recognised, and how long the gaps are.

    Reported alongside the counts because a count only means something once
    the reading under it is known to be solid: the guards in watch() are all
    built on previous sightings, so at half detection they are not weaker,
    they are absent.
    """
    watcher = notifications.NotificationWatcher()
    on_screen = seen = 0
    gaps, current = [], None
    for _name, moment, panel in frames:
        grey = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
        bands = notifications.text_line_bands(grey, scale)
        # Present in the pixels, whether or not the ink gate lets it in.
        there = False
        for band in bands:
            top, bottom = band
            for index in range(len(watcher.templates[WATCHED])):
                for delta in notifications.SCALE_BRACKET:
                    sized = watcher._template_at(WATCHED, index, scale + delta)
                    pad = max(int(round(notifications.STRIP_PAD * scale)),
                              sized.shape[0] - (bottom - top))
                    strip = grey[max(0, top - pad):bottom + pad]
                    if (sized.shape[0] > strip.shape[0]
                            or sized.shape[1] > strip.shape[1]):
                        continue
                    _, score, _, _ = cv2.minMaxLoc(cv2.matchTemplate(
                        strip, sized, cv2.TM_CCOEFF_NORMED))
                    if score >= notifications.MIN_PHRASE_SCORE:
                        there = True
        recognised = any(watcher._phrase_in_band(grey, band, WATCHED, scale)
                         for band in bands)
        if there:
            on_screen += 1
            if recognised:
                seen += 1
                if current is not None:
                    gaps.append(moment - current)
                    current = None
            elif current is None:
                current = moment
        else:
            current = None
    return on_screen, seen, sorted(gaps, reverse=True)


def report(run_dir, arguments, digit_templates):
    located = locate(run_dir)
    label = os.path.basename(run_dir.rstrip("/\\"))
    if located is None:
        print(f"{label}: no HUD found, skipped")
        return None
    scale = located["scale"]
    frames = frames_of(run_dir, located, digit_templates)
    if not frames:
        print(f"{label}: no readable frames")
        return None

    span = frames[-1][1] - frames[0][1]
    print(f"\n{label}: {located['profile'].name} at scale {scale:.3f}, "
          f"{len(frames)} frames spanning {span:.0f} game seconds")

    on_screen, seen, gaps = sightings(frames, scale)
    if on_screen:
        print(f"   the TC line is in the pixels on {on_screen} frames and is "
              f"recognised on {seen} ({100.0 * seen / on_screen:.0f}%)")
        if gaps:
            print(f"   longest runs of MISSED looks while it was on screen, "
                  f"in game seconds: {[round(g) for g in gaps[:5]]}")
            print(f"   -> REARM_SECONDS must stay above "
                  f"{max(gaps):.0f}s or flicker will read as absence")
    else:
        print("   the TC line never appears in this run - nothing to count")
        return None

    if arguments.sweep:
        print("   REARM_SECONDS sweep (Town Centres counted):")
        for value in SWEEP:
            counts = [len(replay(frames, scale, looks, value))
                      for looks in (1, 2, 3)]
            steady = "" if len(set(counts)) == 1 else "   <-- RATE DEPENDENT"
            print(f"      {value:3d}s : {counts} at 1/2/3 looks per "
                  f"frame{steady}")
        return None

    fired = replay(frames, scale, arguments.looks)
    print(f"   counted {len(fired)} Town Centre line(s) at "
          f"{arguments.looks} look(s) per frame:")
    for name, moment in fired:
        print(f"      {name} at game time {moment:.0f}s")
    return len(fired)


def main():
    parser = argparse.ArgumentParser(
        description="Replay the notification watcher over capture runs.")
    parser.add_argument("runs", nargs="+", help="capture run directories")
    parser.add_argument("--expect", type=int, default=None,
                        help="how many TC lines the game really printed; "
                             "makes this a pass/fail check")
    parser.add_argument("--looks", type=int, default=2,
                        help="looks per captured frame (default 2, roughly "
                             "what the live overlay does)")
    parser.add_argument("--sweep", action="store_true",
                        help="try several REARM_SECONDS instead of counting "
                             "once, and flag any run whose answer depends on "
                             "the poll rate")
    arguments = parser.parse_args()

    digit_templates = digits.load_digit_templates()
    counted = []
    for run in arguments.runs:
        if os.path.isdir(run):
            counted.append(report(run, arguments, digit_templates))

    if arguments.expect is None:
        return 0
    total = sum(c for c in counted if c is not None)
    print(f"\nexpected {arguments.expect} Town Centre line(s), "
          f"counted {total}")
    if total == arguments.expect:
        print("PASS")
        return 0
    print("OVERCOUNTED - a lingering line is being counted twice"
          if total > arguments.expect else
          "UNDERCOUNTED - a real line is not being counted")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
