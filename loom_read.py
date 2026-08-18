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

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import os
import sys
import time

import cv2

from loom import filters, paths, production, reader, session

POLL_INTERVAL = 0.3


def format_pop(pair):
    return "--/--" if pair is None else f"{pair[0]}/{pair[1]}"


def queue_summary(slots, tracker, game_time):
    """One short status-line fragment describing production right now."""
    if slots is None:
        return "queue --"
    if not slots:
        idle_for = tracker.idle_duration(game_time)
        return f"queue EMPTY ({idle_for:.0f}s)" if tracker.idle else "queue empty?"

    front = slots[0]
    text = f"queue {len(slots)}: {front.identity or '?'}"
    if front.count is not None:
        text += f" x{front.count}"
    if front.tint == "green" and front.progress is not None:
        text += f" {front.progress:.0%}"
    elif front.tint:
        text += f" {front.tint}"
    if tracker.tcs_seen:
        text += f"  TCs {tracker.tc_busy}/{tracker.tcs_seen}"
    if tracker.blocked:
        text += f" [{tracker.blocked.upper()}]"
    return text


def main():
    parser = argparse.ArgumentParser(description="Loom raw HUD readout")
    parser.add_argument("--debug-pop", action="store_true",
                        help="save the population band crop whenever it fails "
                             "to read, for offline tuning")
    args = parser.parse_args()

    # Where failed population reads leave their evidence. Created lazily on
    # the first failure, so a clean session leaves nothing behind.
    misread_dir = os.path.join(paths.CAPTURES_DIR,
                               time.strftime("pop_misreads_%Y%m%d_%H%M%S"))
    last_misread_save = 0.0

    hud = reader.HudReader()

    # Loom is meant to be started before the game, so both waits are
    # open-ended: launch this, then go launch the game whenever.
    try:
        print("Waiting for the Age of Empires II window... (Ctrl+C to quit)")
        hud.connect()
        width, height = hud.window_size()
        print(f"Found game window: {width} x {height}")

        print("Waiting for a match to start...")
        hud.wait_for_hud()
    except KeyboardInterrupt:
        print("\nStopped.")
        return

    print(f"HUD found (match {hud.hud['score']:.3f}, scale {hud.hud['scale']:.2f})")
    print("Reading. Press Ctrl+C to stop.\n")

    tracker = production.ProductionTracker()
    last_population = None
    last_raw_villagers = None

    try:
        while True:
            reading = hud.poll()

            # The believed population changing is the moment worth verifying
            # against the game, so it gets its own scrolling line.
            if reading.population != last_population and reading.population is not None:
                print(f"\r>>> pop {format_pop(last_population)} -> "
                      f"{format_pop(reading.population)} "
                      f"at {filters.format_time(reading.game_time)}          ")
                last_population = reading.population

            # A readable HUD with an unreadable population band is exactly
            # the failure being hunted: keep the pixels, at most one per
            # second so a bad stretch cannot flood the disk.
            if (args.debug_pop and reading.hud_visible
                    and reading.raw_population is None
                    and hud.last_population_band is not None
                    and time.monotonic() - last_misread_save > 1.0):
                os.makedirs(misread_dir, exist_ok=True)
                stamp = time.strftime("%H%M%S")
                cv2.imwrite(os.path.join(misread_dir, f"band_{stamp}.png"),
                            hud.last_population_band)
                last_misread_save = time.monotonic()

            # A villager count that leaps is a misread, not a game event.
            # Villagers arrive one at a time and only ever fall by a few (a
            # boar kill, a drush), so a jump of several in one poll is the
            # reader inventing something - and because the count is the ONLY
            # sync signal, a wrong one desynchronises the whole build order.
            # Printed loudly with the raw value beside the believed one, so an
            # intermittent misread is caught in the log rather than by staring.
            if (reading.raw_villagers is not None
                    and last_raw_villagers is not None
                    and abs(reading.raw_villagers - last_raw_villagers) > 3):
                print(f"\r*** villager JUMP {last_raw_villagers} -> "
                      f"{reading.raw_villagers} (believed {reading.villagers}) "
                      f"at {filters.format_time(reading.game_time)}"
                      f"   min_glyph_width={hud.hud['min_glyph_width']}      ")
            if reading.raw_villagers is not None:
                last_raw_villagers = reading.raw_villagers

            if reading.event is not None:
                # Events print on their own line and scroll past, while the
                # status line below keeps overwriting itself.
                print(f"\r>>> {reading.event:<14} "
                      f"at {filters.format_time(reading.game_time)}"
                      f"  villagers {reading.villagers}                 ")
                if reading.event == session.GAME_STARTED:
                    # A new match means the old game's TCs never existed.
                    tracker.reset()

            for name in reading.game_events:
                print(f"\r>>> game says: {name:<20} "
                      f"at {filters.format_time(reading.game_time)}     ")
                if name == "town_center_built":
                    tracker.register_tc_built()

            for event in tracker.update(reading.game_time, reading.queue):
                print(f"\r>>> {event:<14} "
                      f"at {filters.format_time(reading.game_time)}          ")

            status = (
                f"time {filters.format_time(reading.game_time)}   "
                f"villagers {reading.villagers if reading.villagers is not None else '--':>3}   "
                f"pop {format_pop(reading.population):<7} "
                f"{queue_summary(reading.queue, tracker, reading.game_time):<36} "
                f"| raw: {filters.format_time(reading.raw_clock)} / {reading.raw_villagers}"
                f" / {format_pop(reading.raw_population)}      "
            )
            sys.stdout.write("\r" + status)
            sys.stdout.flush()

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
