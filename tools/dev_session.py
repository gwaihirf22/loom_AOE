"""
Loom — one command that records everything a session can tell me.

    python -m tools.dev_session --label scouts-all-mods
    ...play...  Ctrl+C

Starts the overlay AND the frame capture together, stops them together,
and then gathers every artefact that session produced into the one folder
the frames are already in. Afterwards it prunes the frames with no game in
them and prints the read rates.

It exists because doing that by hand costs more attention than the game
does. The overlay writes its forensic log to the Loom data folder, the
statistics recorder writes to a third place under a fourth naming scheme,
the frames land under a timestamp that matches none of them, and the
capture keeps running for however long it takes to notice the game ended.
Four places, four names, and a tail of useless frames on the end of each -
which is a lot of bookkeeping to do correctly while also playing.

What lands in the run folder when this finishes:

    frame_0001.png ...      the capture, minus the frames with no HUD
    overlay.log             the per-poll forensic log for THIS session
    stats.json              the per-game statistics for THIS session
    session.txt             what was recorded, and the read rates

Nothing is moved out of its original home - the launcher's statistics
window still finds its files where it expects them. These are copies, so
the run folder is self-contained and can be reasoned about a month later
without cross-referencing anything.

Two things this got wrong at first, both worth keeping written down.

**The build order has to be passed through.** It was not, so the overlay ran
on its own default while the label said otherwise, and a whole recorded 1440p
game came out stamped `fast_castle` - the folder is still on disk with USED
WRONG BUILD ORDER in its name. The build is now an argument, it is printed
before the game starts, and it is written into session.txt, because the
symptom of getting it wrong is a file that looks perfectly fine.

**Stopping has to be asked for, not signalled.** From a terminal, Ctrl+C
reaches the overlay and the capture directly - they share the console's
process group - so the children shut down cleanly before this script even
notices. That safety net is a property of the CONSOLE, not of this code, and
it disappears the moment the launcher runs this as a child: no console, no
Ctrl+C, and `subprocess.terminate()` on Windows is `TerminateProcess`, a hard
kill with no chance to run `aboutToQuit`. That hook is the overlay's final
statistics write. So the stop travels as a line on stdin, the same request
the launcher sends its own children - see `loom/stopline.py` - and this
script watches its own stdin for exactly that line.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import paths, stopline  # noqa: E402

# How long a child gets to honour the stop line before it is signalled. The
# overlay's shutdown writes a statistics file, so this is generous.
STOP_GRACE_SECONDS = 10

# And how long after THAT before it is killed outright.
KILL_GRACE_SECONDS = 3


def newest_since(pattern, started):
    """The newest file matching `pattern` written since `started`."""
    found = [p for p in glob.glob(pattern) if os.path.getmtime(p) >= started]
    return max(found, key=os.path.getmtime) if found else None


def newest_run_since(started):
    """The capture folder grab_frames made for this session."""
    runs = [p for p in glob.glob(str(paths.CAPTURES_DIR / "run_*"))
            if os.path.isdir(p) and os.path.getmtime(p) >= started - 5]
    return max(runs, key=os.path.getctime) if runs else None


def ask_to_stop(child):
    """Ask a child to exit the way the launcher asks one, then escalate.

    A line on stdin first, because that is the only stage that works on
    Windows for a child with no window of its own. `terminate()` here is not
    the launcher's polite `QProcess::terminate` - for `subprocess` on Windows
    it is `TerminateProcess`, which is already a hard kill - so it comes only
    after the child has had its chance to save.
    """
    if child.poll() is not None:
        return "had already stopped"
    if child.stdin is not None:
        try:
            child.stdin.write(stopline.encode())
            child.stdin.flush()
        except OSError:
            pass                        # the pipe went first; escalate below
        try:
            child.wait(timeout=STOP_GRACE_SECONDS)
            return "stopped when asked"
        except subprocess.TimeoutExpired:
            pass
    child.terminate()
    try:
        child.wait(timeout=KILL_GRACE_SECONDS)
        return "needed terminating"
    except subprocess.TimeoutExpired:
        child.kill()
        return "had to be killed"


def main():
    parser = argparse.ArgumentParser(
        description="Record a Loom session: overlay, frames and every "
                    "artefact, in one folder.")
    parser.add_argument("--label", default="dev",
                        help="what this session is FOR; becomes part of the "
                             "capture folder's name")
    parser.add_argument("--build", default="fast_castle",
                        help="which build order the overlay runs (the stem of "
                             "a file in builds/). Getting this wrong is "
                             "invisible afterwards, so it is announced.")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="seconds between frames (default 2.0)")
    parser.add_argument("--no-prune", action="store_true",
                        help="keep the frames with no game in them")
    arguments = parser.parse_args()

    root = paths.PROJECT_ROOT
    started = time.time()
    print(f"build order: {arguments.build}")
    print(f"label:       {arguments.label}")
    print(f"\n{stopline.quit_hint()}, or press Stop task in the launcher\n")

    # The overlay owns a QApplication and the capture owns a grab loop, so
    # they have to be separate processes - the same reason the launcher
    # runs them as children rather than importing them.
    #
    # The overlay gets a stdin pipe because that is how it is asked to stop.
    # The capture gets DEVNULL rather than inheriting mine: a child sharing
    # my stdin could read the stop line meant for me and I would never see it.
    overlay = subprocess.Popen(
        [sys.executable, str(root / "loom_overlay.py"),
         "--build", arguments.build],
        cwd=str(root), stdin=subprocess.PIPE)
    frames = subprocess.Popen(
        [sys.executable, "-m", "tools.grab_frames", str(arguments.interval),
         "--label", arguments.label],
        cwd=str(root), stdin=subprocess.DEVNULL)

    # The launcher asks with a line; a terminal asks with Ctrl+C. Both end up
    # at the same place.
    asked_to_stop = threading.Event()
    stopline.watch(asked_to_stop.set)

    try:
        while (not asked_to_stop.is_set()
               and overlay.poll() is None and frames.poll() is None):
            time.sleep(0.5)
        if not asked_to_stop.is_set():
            print("\none of them stopped on its own - stopping the other")
        else:
            print("\nstop requested")
    except KeyboardInterrupt:
        print("\nstopping")
    for child, name in ((frames, "capture"), (overlay, "overlay")):
        print(f"  {name}: {ask_to_stop(child)}")

    run_dir = newest_run_since(started)
    if run_dir is None:
        print("no capture folder was created - was the game running?")
        return
    print(f"\ncapture: {os.path.basename(run_dir)}")

    # Gather. Each artefact is COPIED rather than moved, so everything that
    # already knows where to look still finds it.
    gathered = []
    log = newest_since(str(paths.DATA_DIR / "logs" / "overlay_*.log"), started)
    if log:
        shutil.copy2(log, os.path.join(run_dir, "overlay.log"))
        gathered.append(f"overlay.log   <- {os.path.basename(log)}")
    stats = newest_since(str(paths.STATS_DIR / "*.json"), started)
    if stats:
        shutil.copy2(stats, os.path.join(run_dir, "stats.json"))
        gathered.append(f"stats.json    <- {os.path.basename(stats)}")
        # The statistics recorder names its file after the build the overlay
        # actually ran. That makes it the one piece of evidence that can
        # contradict what I was ASKED to run, so I check it rather than
        # assume - this is the exact mismatch that cost a whole recorded game
        # and was only noticed by reading the filename days later.
        if arguments.build not in os.path.basename(stats):
            gathered.append(
                f"!! asked for build {arguments.build!r} but the overlay "
                f"recorded {os.path.basename(stats)!r} - these disagree")
    for line in gathered:
        print(f"  {line}")
    if not gathered:
        print("  (no overlay log or stats file appeared - did the overlay "
              "acquire the HUD?)")

    run_name = os.path.basename(run_dir)
    if not arguments.no_prune:
        print("\npruning frames with no game in them:")
        subprocess.run([sys.executable, "-m", "tools.prune_captures",
                        "--run", run_name, "--delete"], cwd=str(root))

    print("\nread rates:")
    rates = subprocess.run([sys.executable, "-m", "tools.read_rates",
                            run_name, "--every", "3"], cwd=str(root),
                           capture_output=True, text=True)
    print(rates.stdout, end="")

    with open(os.path.join(run_dir, "session.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(f"label: {arguments.label}\n")
        handle.write(f"build: {arguments.build}\n")
        handle.write(f"recorded: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        handle.write(f"interval: {arguments.interval}s\n\n")
        for line in gathered:
            handle.write(line + "\n")
        handle.write("\n" + rates.stdout)
    print(f"\neverything for this session is in {run_dir}")


if __name__ == "__main__":
    main()
