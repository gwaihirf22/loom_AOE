"""
Loom — running the other Loom programs as child processes.

The launcher never imports the overlay or the coach: each one owns its own
main loop (the overlay even owns a whole QApplication, and Qt allows only one
per process), so the only sane way to run them is as separate processes. That
makes process lifecycle the entire control surface - starting the overlay IS
"start", terminating it IS "stop" - and this module wraps exactly that.

QProcess rather than subprocess.Popen, because the launcher is a Qt app:
QProcess delivers child output and exit as signals on the GUI thread, so the
output pane updates live without any reader threads or polling.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from . import entry, paths, statefeed, stopline

# How long a child gets to act on the stop line before the signal follows.
# Measured, a child that is watching stdin exits in 20-40ms, so half a second
# is a wide margin. It only has to be long enough that a child which IS going
# to quit politely has finished doing so - see stop() for what happens when
# both requests land at once.
TERMINATE_AFTER_MS = 500

# How long a child gets after that before it is killed outright. Two seconds
# is plenty: every child is a small Python program, and the escalation only
# matters if one is ever wedged inside a C call.
KILL_AFTER_MS = 2000


class ChildProcess(QObject):
    """One Loom program running under the launcher.

    Emits output_line for every line the child prints (stderr merged in,
    prefixed with the label so a shared pane stays readable) and finished
    when it exits.

    The one exception: lines carrying the statefeed sentinel are machine
    data, not log output, and go out on state_line instead - raw and
    unprefixed, ready for statefeed.decode. This is the single structured
    channel out of a child; everything else on stdout is for human eyes.
    """

    output_line = pyqtSignal(str)
    state_line = pyqtSignal(str)        # a statefeed line, unprefixed
    finished = pyqtSignal(str, int)     # label, exit code

    def __init__(self, label, args, parent=None):
        """args go after the interpreter: e.g. ["loom_overlay.py", "--demo"]."""
        super().__init__(parent)
        self.label = label
        self.args = args
        self._process = None
        self._partial = ""              # an unterminated tail from the last read

    def start(self):
        if self.is_running():
            return
        self._process = QProcess(self)
        # Everything runs from the project root, same as the documented
        # command lines, so "-m tools.grab_frames" and friends resolve.
        self._process.setWorkingDirectory(str(paths.PROJECT_ROOT))
        # Interleave stderr into stdout: one pane, events in the order they
        # happened, tracebacks next to the prints that preceded them.
        self._process.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._finished)
        # What starts a child differs between a clone and a bundle - in a
        # bundle sys.executable IS Loom and there is no script to hand it -
        # so entry.argv_for answers that in one place. See loom/entry.py.
        program, arguments = entry.argv_for(self.args)
        self._process.start(program, arguments)

    def stop(self):
        """Ask the child to exit, and escalate only if asking did not work.

        Three stages, each one only reached if the last was ignored.

        First a stop line on stdin, which any child watching for it turns into
        a clean quit - see loom/stopline.py. This is the only stage that works
        on Windows, where terminate() posts WM_CLOSE to top-level windows: a
        console child has none, and the overlay's is a ToolTip. Measured, the
        overlay ignored terminate() completely and was killed by the
        escalation 2.01s later with aboutToQuit never running - and that hook
        is the final statistics write and the placement offset, thrown away
        every time Stop was pressed.

        Then terminate(), which on Linux is SIGTERM and kills a Python child
        even while it is blocked waiting for the game window. It is the only
        stage that reaches a child which does NOT watch stdin, such as the APM
        counter.

        Then kill().

        The stages are sequential rather than simultaneous, and that is not
        tidiness. Sending the line and terminate() together crashed the
        overlay on Windows with STATUS_STACK_BUFFER_OVERRUN: the child was
        already tearing its QApplication down in response to the line when
        WM_CLOSE arrived and was dispatched into it. The stats file still got
        written, so the symptom was only a nonzero exit code in the launcher's
        output pane - quiet, and exactly the kind of thing that gets explained
        away. Each stage now checks the child is still running first, so a
        child that quits politely is never signalled at all.

        The escalation is deliberately QTimer rather than waitForFinished():
        that would freeze the launcher UI for as long as the child dawdles.
        """
        if not self.is_running():
            return
        process = self._process
        self._ask_politely(process)
        QTimer.singleShot(TERMINATE_AFTER_MS, lambda: self._terminate(process))
        QTimer.singleShot(KILL_AFTER_MS, lambda: self._force_kill(process))

    def is_running(self):
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    def request_toggle_hidden(self):
        """Ask the overlay to hide its panel, or to bring it back.

        The same stdin pipe the stop line uses, but deliberately WITHOUT
        closeWriteChannel: that sends EOF, which is how stop makes itself
        certain, and here it would leave no way to ask a second time. This
        request is one the player may make over and over.

        A toggle rather than an explicit hide or show, because the overlay
        is the only thing that knows whether its window is up; asking it to
        flip whatever it has cannot leave the two sides disagreeing.
        """
        if self._process is None:
            return
        try:
            self._process.write(stopline.encode_toggle_hidden())
        except (RuntimeError, OSError):
            # The child may already be gone. Nothing to say about it: the
            # button's state comes back from the overlay, so a request that
            # never lands simply leaves the button where it was.
            pass

    # ---- internals -----------------------------------------------------

    def _ask_politely(self, process):
        """Write the stop line to the child's stdin and flush it through.

        closeWriteChannel after the write is what makes this reliable: it
        sends EOF, so a child that is watching stdin stops either because it
        read the sentinel or because its stdin ended. Both mean the same
        thing, and a child that watches neither is no worse off than before.

        Failure here is not worth reporting - the child may already be gone,
        and terminate() and the kill escalation are both still to come.
        """
        try:
            process.write(stopline.encode())
            process.closeWriteChannel()
        except (RuntimeError, OSError):
            pass

    def _terminate(self, process):
        """The second stage: only for a child that ignored the stop line."""
        if process.state() != QProcess.ProcessState.NotRunning:
            process.terminate()

    def _force_kill(self, process):
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()

    def _read(self):
        data = self._process.readAllStandardOutput().data().decode(
            "utf-8", errors="replace")
        # A read can end mid-line; hold the tail until its newline arrives
        # so a line is never emitted in two halves.
        text = self._partial + data
        lines = text.split("\n")
        self._partial = lines.pop()
        for line in lines:
            self._route(line)

    def _route(self, line):
        """One complete line to the right signal: state or human log.

        Routing happens only on whole lines - _read holds back any tail
        until its newline arrives - so a state line split across two pipe
        reads can never leak half-parsed into the log pane.
        """
        if statefeed.is_state_line(line):
            self.state_line.emit(line)
        else:
            self.output_line.emit(f"[{self.label}] {line}")

    def _finished(self, exit_code, exit_status):
        if self._partial:
            # The child's dying breath can be a state line with no newline;
            # route it like any other rather than dumping it in the pane.
            self._route(self._partial)
            self._partial = ""
        self.finished.emit(self.label, exit_code)

    def shutdown(self):
        """Blocking stop for launcher exit: the UI is going away anyway.

        The same three stages as stop(), and for the same reasons - see there
        - but waiting between them instead of scheduling. This is the one
        place waiting is acceptable, since nothing is left to freeze, and it
        guarantees no orphaned overlay outlives the launcher.
        """
        if not self.is_running():
            return
        self._ask_politely(self._process)
        if self._process.waitForFinished(TERMINATE_AFTER_MS):
            return
        self._process.terminate()
        if not self._process.waitForFinished(KILL_AFTER_MS):
            self._process.kill()
            self._process.waitForFinished(1000)
