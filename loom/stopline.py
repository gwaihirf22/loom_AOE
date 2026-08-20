"""
Loom — asking a child to stop, as a line on its stdin.

The mirror image of statefeed: that module carries the overlay's state up to
the launcher on stdout, and this one carries "please stop" back down on stdin.
Same pipe, already owned by the launcher, already open.

Nothing but the sentinel stops a child. End-of-stream deliberately does not -
read_until_stop explains what that cost when an earlier draft let it.

Why it has to exist. The launcher stopped children with QProcess::terminate,
whose docstring in runner.py says "terminate() sends SIGTERM, which kills a
Python child even while it is blocked". True on Linux, and on Windows simply
not what happens: terminate() posts WM_CLOSE to the process's top-level
windows, and a console child has none while the overlay's window is a ToolTip
that Qt does not treat as the last window. Measured on Windows before writing
this - the overlay ignored terminate() entirely and was killed by the 2-second
escalation instead, 2.01s later, with aboutToQuit never running.

What that costs is not tidiness. loom_overlay wires aboutToQuit to
controller.finish, which is the final statistics write and the placement-mode
offset save. Stopping the overlay from the launcher silently threw away the
stats for the match that had just been played.

So the polite request travels as data instead of as a signal. This module is
deliberately pure - threading and a file object, no Qt - so the whole protocol
is testable with a StringIO and no processes at all. The one thing it cannot
do for the caller is quit a Qt application from this thread; on_stop is
invoked on the reader thread, and it is the caller's job to hop to the GUI
thread (loom_overlay uses QMetaObject.invokeMethod, which is designed for
exactly that).

Note that this does NOT replace SIGTERM on Linux, where runner still sends it.
Both arrive, whichever lands first wins, and the Linux path behaves exactly as
it always did.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys
import threading

# The whole line, newline excluded. Unlike statefeed's sentinel this is not a
# prefix with a payload after it - there is only one thing to say - so it is
# compared whole and a line that merely starts with it is not a stop request.
SENTINEL = "LOOM_STOP"


def encode():
    """The exact bytes to write to a child's stdin to ask it to stop."""
    return (SENTINEL + "\n").encode("ascii")


def is_stop_line(line):
    """Is this line from stdin a stop request?"""
    return line.strip() == SENTINEL


def quit_hint():
    """How to stop this program, phrased for wherever it is running.

    A child started from a terminal is quit with Ctrl+C. A child started by
    the launcher is a windowed process whose stdout is a pipe: there is no
    console, so there is no Ctrl+C to press, and the only way out is the
    button that sends the stop line this module defines.

    Loom told everyone the first thing regardless. A tester read "(Ctrl+C to
    quit)" in the launcher's output pane, pressed it, watched nothing happen
    and filed it as a broken hotkey - which was fair, because the program had
    said so. The behaviour was right the whole time and only the sentence was
    wrong.

    Here rather than in a new module because the launcher's Stop button IS the
    stop line: the mechanism and the sentence describing it belong together.
    """
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return "Ctrl+C to quit"
    except (AttributeError, OSError, ValueError):
        # A detached or already-closed stdout answers none of this. Falling
        # through names the button, which is true in every case where asking
        # was impossible - nothing without a console has a Ctrl+C.
        pass
    return "press Stop in the Loom launcher to quit"


def read_until_stop(stream, on_stop):
    """Read lines until a stop request arrives, then call on_stop once.

    Returns True if it stopped because it was asked to, False if the stream
    ended or broke first.

    ONLY the sentinel stops the child. End-of-stream deliberately does not,
    and the first draft of this got that wrong: it treated EOF as a stop
    request too, reasoning that a launcher going away without asking should
    also bring the overlay down. What that actually did was kill any overlay
    started without a stdin - `python loom_overlay.py < /dev/null`, a
    backgrounded shell job, a desktop shortcut - because EOF arrives
    instantly and the panel exited before it had drawn anything. Measured:
    "Demo mode at 20.0x" followed immediately by "Stopped."

    That is a far worse failure than the one it was guarding against. An
    orphaned overlay after a launcher CRASH is a window somebody closes; an
    overlay that will not start unless it was spawned by the launcher is a
    program that does not work. The orphan case is covered anyway on Linux,
    where SIGTERM still arrives, and the launcher always sends the sentinel
    explicitly before closing the pipe.

    Anything that is not the sentinel is ignored rather than an error.
    Nothing else writes to a child's stdin today, and inventing a failure for
    a line nobody sent would be another way to kill the overlay by accident.
    """
    try:
        for line in stream:
            if is_stop_line(line):
                on_stop()
                return True
    except (ValueError, OSError):
        # A closed or already-torn-down stdin. Not an error worth reporting
        # from a background thread nobody is watching, and not a reason to
        # stop - see above.
        pass
    return False


def watch(on_stop, stream=None):
    """Watch stdin for a stop request, on a daemon thread.

    Daemon, so a child that exits for any other reason is never held open by
    this thread sitting in a blocking read.

    on_stop is called on the reader thread. Callers with a Qt event loop must
    marshal it across themselves - see the module docstring.
    """
    thread = threading.Thread(
        target=read_until_stop,
        args=(sys.stdin if stream is None else stream, on_stop),
        name="loom-stopline",
        daemon=True)
    thread.start()
    return thread
