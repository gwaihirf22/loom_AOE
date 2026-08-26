"""How a recording session stops its children.

The whole point of dev_session is that the artefacts survive - the overlay's
final statistics write happens in `aboutToQuit`, and anything that kills the
process outright throws it away. From a terminal that never showed up,
because Ctrl+C reaches the children through the console's process group and
they shut themselves down before this code runs at all. Under the launcher
there is no console, so this code IS the shutdown, and on Windows
`subprocess.terminate()` is `TerminateProcess` - already a hard kill.

So these tests are about the ORDER of escalation, not about tidiness.
"""

import io
import subprocess

from loom import stopline
from tools import dev_session


class FakeChild:
    """A subprocess.Popen stand-in that records how it was stopped."""

    def __init__(self, honours_the_line=True, honours_terminate=True,
                 already_stopped=False):
        self.honours_the_line = honours_the_line
        self.honours_terminate = honours_terminate
        self.running = not already_stopped
        self.stdin = io.BytesIO()
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.running else 0

    def wait(self, timeout=None):
        if self.terminated and self.honours_terminate:
            self.running = False
        elif self.stdin.getvalue() and self.honours_the_line:
            self.running = False
        if self.running:
            raise subprocess.TimeoutExpired("fake", timeout)
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.running = False


def test_a_child_that_honours_the_line_is_never_signalled():
    child = FakeChild(honours_the_line=True)
    outcome = dev_session.ask_to_stop(child)
    assert child.stdin.getvalue() == stopline.encode()
    assert not child.terminated and not child.killed
    assert "asked" in outcome


def test_a_child_that_ignores_the_line_is_terminated():
    child = FakeChild(honours_the_line=False, honours_terminate=True)
    outcome = dev_session.ask_to_stop(child)
    # Asked FIRST, signalled only after. The other order is what loses the
    # statistics file on Windows.
    assert child.stdin.getvalue() == stopline.encode()
    assert child.terminated and not child.killed
    assert "terminat" in outcome


def test_a_child_that_ignores_everything_is_killed_last():
    child = FakeChild(honours_the_line=False, honours_terminate=False)
    outcome = dev_session.ask_to_stop(child)
    assert child.terminated and child.killed
    assert "killed" in outcome


def test_a_child_that_already_stopped_is_left_alone():
    child = FakeChild(already_stopped=True)
    outcome = dev_session.ask_to_stop(child)
    assert child.stdin.getvalue() == b""
    assert not child.terminated and not child.killed
    assert "already" in outcome


def test_a_child_with_no_stdin_pipe_is_still_stopped():
    # grab_frames is given DEVNULL rather than a pipe: it has nothing to save
    # and must not be able to read the stop line meant for dev_session
    # itself. It still has to be stopped.
    child = FakeChild(honours_the_line=False, honours_terminate=True)
    child.stdin = None
    outcome = dev_session.ask_to_stop(child)
    assert child.terminated
    assert "terminat" in outcome
