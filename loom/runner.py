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

from . import paths, statefeed

# How long a child gets to exit politely after SIGTERM before SIGKILL.
# Two seconds is plenty: every child is a small Python program whose default
# SIGTERM action is immediate death; the escalation only matters if one is
# ever wedged inside a C call.
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
        # sys.executable is the venv python the launcher itself runs under.
        # -u is load-bearing: without it Python buffers stdout when it is a
        # pipe, and the output pane sits dead until the child exits.
        self._process.start(sys.executable, ["-u"] + list(self.args))

    def stop(self):
        """Ask the child to exit; force it if that takes too long.

        terminate() sends SIGTERM, which kills a Python child even while it
        is blocked waiting for the game window. The QTimer escalation is
        deliberately not waitForFinished(): that would freeze the launcher
        UI for as long as the child dawdles.
        """
        if not self.is_running():
            return
        process = self._process
        process.terminate()
        QTimer.singleShot(KILL_AFTER_MS, lambda: self._force_kill(process))

    def is_running(self):
        return (self._process is not None
                and self._process.state() != QProcess.ProcessState.NotRunning)

    # ---- internals -----------------------------------------------------

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

        The polite path first, then a bounded wait, then the axe. This is
        the one place waiting is acceptable - nothing is left to freeze -
        and it guarantees no orphaned overlay outlives the launcher.
        """
        if not self.is_running():
            return
        self._process.terminate()
        if not self._process.waitForFinished(KILL_AFTER_MS):
            self._process.kill()
            self._process.waitForFinished(1000)
