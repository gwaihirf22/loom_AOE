"""
Loom — the APM counter: keystrokes and clicks per minute, counts only.

    python -m tools.apm_counter              # print buckets as they pass
    python -m tools.apm_counter --bucket 5   # bucket length in seconds

Counts input while the game plays, for the post-game statistics. It works
by selecting XInput2 RAW press events on the XWayland server's root window
- the same X server the game and the overlay live on.

THE PRIVACY CONTRACT, stated plainly: this program counts events and never
knows which key was pressed. That is not a promise of good behaviour, it is
how the code is built - python-xlib does not even decode the raw event
payload (the keycode lives in bytes this program never parses), and nothing
here selects the cooked events that would carry one. Counts in, numbers
out. It also only sees the XWayland world: input to native Wayland windows
never reaches this X server at all.

Run by the launcher alongside the overlay when "Track APM" is on; the
counts ride the LOOM_STATE channel like everything else. eAPM (effective
APM) is out of reach by design - judging which actions "count" would need
to know what they were, which is exactly what this refuses to see.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import select
import signal
import sys
import time

from Xlib import display as xdisplay
from Xlib.ext import ge, xinput

from loom import statefeed
from loom.apm import BUCKET_SECONDS

# XInput2 raw event type codes (Xlib.ext.xinput defines the constants; the
# masks below are what the selection asks for).
RAW_KEY_PRESS = xinput.RawKeyPress
RAW_BUTTON_PRESS = xinput.RawButtonPress


def main():
    parser = argparse.ArgumentParser(description="Loom APM counter")
    parser.add_argument("--bucket", type=float, default=BUCKET_SECONDS,
                        help="bucket length in wall seconds")
    args = parser.parse_args()

    try:
        dpy = xdisplay.Display()
    except Exception as error:
        print(f"apm: no X display ({error}); APM tracking disabled")
        return

    root = dpy.screen().root
    try:
        # Raw events are delivered on the root window regardless of which
        # window has focus - which under XWayland means: whenever any
        # XWayland client (the game) is focused.
        root.xinput_select_events([
            (xinput.AllMasterDevices,
             xinput.RawKeyPressMask | xinput.RawButtonPressMask)])
        dpy.sync()
    except Exception as error:
        print(f"apm: could not select raw input events ({error}); "
              "APM tracking disabled")
        return

    print(f"apm: counting (bucket {args.bucket:g}s, counts only - "
          "this program never sees which key)")

    running = [True]
    signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))

    keys = clicks = 0
    bucket_end = time.monotonic() + args.bucket
    fd = dpy.fileno()

    while running[0]:
        timeout = max(0.0, bucket_end - time.monotonic())
        try:
            readable, _, _ = select.select([fd], [], [], timeout)
        except InterruptedError:
            continue

        if readable:
            # Drain everything queued; each raw press is one action. The
            # event payload is deliberately left undecoded.
            while dpy.pending_events():
                event = dpy.next_event()
                if event.type != ge.GenericEventCode:
                    continue
                evtype = getattr(event, "evtype", None)
                if evtype == RAW_KEY_PRESS:
                    keys += 1
                elif evtype == RAW_BUTTON_PRESS:
                    clicks += 1

        now = time.monotonic()
        if now >= bucket_end:
            # Empty buckets are news too - zero APM during a game is real.
            print(statefeed.encode(
                {"apm": {"wall": round(now, 2),
                         "keys": keys, "clicks": clicks}}))
            keys = clicks = 0
            bucket_end = now + args.bucket

    print("apm: stopped")


if __name__ == "__main__":
    main()
