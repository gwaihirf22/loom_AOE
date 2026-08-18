"""
Loom — where does the overlay's lag come from? (development tool)

    python -m tools.lag_probe          # play normally, Ctrl+C when done

The overlay on macOS runs several seconds behind the game clock during real
play. Every earlier measurement said the pipeline was fresh - but every one
was taken against a paused replay, with the game barely rendering. This probe
measures the same pipeline while the game is actually being played, because
GPU and CPU contention is the one condition the lag has been seen under and
never measured under.

It runs the REAL HudReader - the same polls the overlay makes - and logs one
line per poll. Four suspects, and the column that convicts each:

    stale frames     frame_age is large: ScreenCaptureKit is delivering
                     old pixels under load
    misreads         raw_clock is blank on many polls while frame_age is
                     small: the band is unreadable during motion, and the
                     believed clock advances in stale hops
    the filter       raw is fresh and sane but believed trails it
    poll cadence     wall_gap far above the 300ms the overlay polls at

behind_by is the headline number: how far the believed clock trails where the
clock SHOULD be, anchored at the first believed reading and advanced at the
speed the HUD itself displays (1.7 game-seconds per wall second). A flat
behind_by is a constant offset; a growing one is Loom falling behind. The
summary also fits the observed rate from the raw readings themselves, so a
match running at some other speed shows up as a rate disagreement rather than
as fake lag.

Misread polls keep their evidence: the clock band's pixels go to
captures/lag_probe_<stamp>/, at most one per second, same idiom as
loom_read --debug-pop. If misreads turn out to be the problem, the failing
pixels are already on disk.

The game must stay frontmost (macOS only composites - and so only captures -
the frontmost window), which is no constraint at all: the point is to measure
while playing.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import signal
import statistics
import sys
import time

import cv2

from AppKit import NSApplication

from loom import paths, reader

# The speed the HUD displays for a standard multiplayer match. Only the
# ANCHOR for behind_by - the summary separately fits the real rate from the
# readings, so a casual game at another speed is reported as a rate, not
# mistaken for lag.
ASSUMED_SPEED = 1.7

POLL_INTERVAL = 0.3


def frame_age(hud):
    """How old the capture backend's cached frame is, in seconds, or None.

    Reaches straight into the macOS backend's GameWindow. A diagnostic gets
    to do that; production code does not.
    """
    window = hud.window
    if hasattr(window, "_frame_at") and hasattr(window, "_lock"):
        with window._lock:
            return time.monotonic() - window._frame_at
    return None


def fitted_rate(samples):
    """Game-seconds per wall second, least-squares over (wall, raw) pairs.

    Fitting the rate from the data instead of trusting ASSUMED_SPEED is what
    stops a single-player game at another speed from masquerading as lag.
    """
    if len(samples) < 8:
        return None
    mean_w = sum(w for w, _ in samples) / len(samples)
    mean_c = sum(c for _, c in samples) / len(samples)
    var = sum((w - mean_w) ** 2 for w, _ in samples)
    if var == 0:
        return None
    cov = sum((w - mean_w) * (c - mean_c) for w, c in samples)
    return cov / var


def main():
    # Accessory: never steal focus. Taking it would background the game,
    # macOS would stop compositing it, and the probe would be measuring the
    # failure it caused.
    NSApplication.sharedApplication().setActivationPolicy_(1)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(paths.CAPTURES_DIR, f"lag_probe_{stamp}.tsv")
    band_dir = os.path.join(paths.CAPTURES_DIR, f"lag_probe_{stamp}")
    os.makedirs(paths.CAPTURES_DIR, exist_ok=True)

    hud = reader.HudReader()
    print("Waiting for the game window... (start a match and just play)")
    hud.connect()
    print("Waiting for the HUD...")
    hud.wait_for_hud()
    print(f"HUD found (scale {hud.hud['scale']:.2f}). Logging to {log_path}")
    print("Play normally for a couple of minutes, then Ctrl+C.\n")

    # Line-buffered, and SIGTERM becomes a clean stop. The first run of this
    # tool was ended with a plain kill while a poll was stuck in C code; the
    # summary never ran, the file never flushed, and sixty seconds of evidence
    # came back as an empty TSV. A measurement tool that loses its
    # measurements on an unclean exit is a trap.
    log = open(log_path, "w", encoding="utf-8", buffering=1)

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    log.write("wall\tgap_ms\tframe_age_ms\tpoll_ms\traw_clock\tbelieved\t"
              "raw_vills\tvills\tbehind_s\n")

    good = []              # (wall, raw_clock) for the rate fit
    gaps, ages, polls, behinds = [], [], [], []
    stale_polls = failed_reads = total_polls = 0
    last_band_save = 0.0
    last_wall = None
    # behind_by is measured against the LAST GOOD RAW reading extrapolated
    # forward, not against a fixed anchor. The first version anchored at the
    # run's start and assumed 1.7 forever, which manufactured lag out of
    # every pause (the real rate averages below 1.7) and exploded on a match
    # restart (the anchor stayed in the old match). Re-basing at each good
    # read means the number can only accumulate error across the gap since
    # the last read - which is exactly the staleness it exists to measure.
    last_raw = last_raw_wall = None
    start = time.monotonic()

    try:
        while True:
            wall = time.monotonic()
            gap = None if last_wall is None else wall - last_wall
            last_wall = wall

            age = frame_age(hud)
            t0 = time.monotonic()
            reading = hud.poll()
            poll_took = time.monotonic() - t0

            total_polls += 1
            raw = reading.raw_clock
            believed = reading.game_time

            if raw is None:
                # A stale frame is not a read failure: the screen genuinely
                # was not updating (game backgrounded, paused, loading), and
                # refusing it is the staleness policy doing its job. Only a
                # FRESH frame the reader could not parse is a defect, and
                # conflating the two made an earlier summary report "24%
                # misreads" that were 21-parts-in-22 black frames.
                if age is not None and age > 1.5:
                    stale_polls += 1
                else:
                    failed_reads += 1
                    # Keep the pixels that refused, at most one a second.
                    if (hud.hud is not None
                            and time.monotonic() - last_band_save > 1.0):
                        band = hud._read_region(hud.hud["clock"])
                        os.makedirs(band_dir, exist_ok=True)
                        cv2.imwrite(os.path.join(
                            band_dir, f"clock_{time.strftime('%H%M%S')}.png"),
                            band)
                        last_band_save = time.monotonic()
            else:
                good.append((wall, raw))

            behind = None
            if believed is not None and last_raw is not None:
                expected = last_raw + (wall - last_raw_wall) * ASSUMED_SPEED
                if expected - believed > 120:
                    # The projection has detached from reality - a match
                    # restart dropped the clock, or a long pause let the
                    # projection run on alone. Re-base at the next good read
                    # rather than record twenty minutes of fiction, which is
                    # what the first version did on a restart.
                    last_raw = last_raw_wall = None
                else:
                    behind = expected - believed
                    behinds.append((wall - start, behind))
            if raw is not None:
                last_raw, last_raw_wall = raw, wall

            if gap is not None:
                gaps.append(gap)
            if age is not None:
                ages.append(age)
            polls.append(poll_took)

            log.write(f"{wall - start:.2f}\t"
                      f"{'' if gap is None else f'{gap * 1000:.0f}'}\t"
                      f"{'' if age is None else f'{age * 1000:.0f}'}\t"
                      f"{poll_took * 1000:.0f}\t"
                      f"{'' if raw is None else raw}\t"
                      f"{'' if believed is None else believed}\t"
                      f"{'' if reading.raw_villagers is None else reading.raw_villagers}\t"
                      f"{'' if reading.villagers is None else reading.villagers}\t"
                      f"{'' if behind is None else f'{behind:.1f}'}\n")

            status = (f"\rbehind {behind:5.1f}s" if behind is not None
                      else "\rbehind   ---")
            sys.stdout.write(
                f"{status}   raw {str(raw):>6}  believed {str(believed):>6}  "
                f"frame_age {0 if age is None else age * 1000:4.0f}ms  "
                f"poll {poll_took * 1000:4.0f}ms   ")
            sys.stdout.flush()

            time.sleep(max(0.0, POLL_INTERVAL - (time.monotonic() - wall)))
    except KeyboardInterrupt:
        pass
    finally:
        log.close()

    print("\n\n--- what the numbers say " + "-" * 40)
    if polls:
        print(f"polls: {total_polls}   stale frames (game not updating - "
              f"backgrounded/paused/loading): {stale_polls}   "
              f"READ FAILURES on fresh frames: {failed_reads}")
    if gaps:
        print(f"poll gap:   median {statistics.median(gaps) * 1000:6.0f} ms   "
              f"max {max(gaps) * 1000:6.0f} ms   (overlay aims for 300)")
    if ages:
        print(f"frame age:  median {statistics.median(ages) * 1000:6.0f} ms   "
              f"max {max(ages) * 1000:6.0f} ms   (stream delivers every ~100)")
    if polls:
        print(f"poll cost:  median {statistics.median(polls) * 1000:6.0f} ms   "
              f"max {max(polls) * 1000:6.0f} ms")

    rate = fitted_rate(good)
    if rate is not None:
        print(f"observed clock rate: {rate:.2f} game-seconds per wall second "
              f"(behind_by assumed {ASSUMED_SPEED})")

    if behinds:
        values = [b for _, b in behinds]
        first_third = values[:max(1, len(values) // 3)]
        last_third = values[-max(1, len(values) // 3):]
        print(f"behind_by:  median {statistics.median(values):5.1f} s   "
              f"max {max(values):5.1f} s")
        print(f"            early median {statistics.median(first_third):5.1f} s"
              f"   late median {statistics.median(last_third):5.1f} s   "
              "(growing = falling behind; flat = constant offset)")

    print(f"\nfull log: {log_path}")
    if os.path.isdir(band_dir):
        count = len(os.listdir(band_dir))
        print(f"unreadable clock bands saved: {count} in {band_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
