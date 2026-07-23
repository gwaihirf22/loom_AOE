"""
Loom — the raw reader.

Prints the game clock and total villager count, several times a second,
straight off the game's HUD. No build order, no advice: just the two numbers
and the session events.

    python loom_read.py

This was the Milestone 1 deliverable and it stays as a diagnostic. When
something looks wrong in the coach or the overlay, this answers the first
question: is Loom reading the screen correctly at all?

It shows the filtered values alongside the raw ones, so the filters can be
watched doing their job.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

import sys
import time

from loom import filters, reader

POLL_INTERVAL = 0.3


def main():
    hud = reader.HudReader()

    if not hud.connect():
        print("Could not find the Age of Empires II window. Is the game running?")
        return

    width, height = hud.window_size()
    print(f"Found game window: {width} x {height}")

    print("Looking for the HUD...")
    if not hud.find_hud():
        print("Could not find the HUD. Are you in a game rather than a menu?")
        return

    print(f"HUD found (match {hud.hud['score']:.3f}, scale {hud.hud['scale']:.2f})")
    print("Reading. Press Ctrl+C to stop.\n")

    try:
        while True:
            reading = hud.poll()

            if reading.event is not None:
                # Events print on their own line and scroll past, while the
                # status line below keeps overwriting itself.
                print(f"\r>>> {reading.event:<14} "
                      f"at {filters.format_time(reading.game_time)}"
                      f"  villagers {reading.villagers}                 ")

            status = (
                f"time {filters.format_time(reading.game_time)}   "
                f"villagers {reading.villagers if reading.villagers is not None else '--':>3}   "
                f"| raw: {filters.format_time(reading.raw_clock)} / {reading.raw_villagers}      "
            )
            sys.stdout.write("\r" + status)
            sys.stdout.flush()

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
