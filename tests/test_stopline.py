"""
Loom — the launcher's "please stop" line, tested without any processes.

loom/stopline.py is deliberately pure - a file object and a thread, no Qt -
so the protocol can be exercised with a StringIO. That matters because the
bug it fixes is invisible in a unit test of anything else: the launcher
pressed Stop, the overlay was killed two seconds later, and the only symptom
was a statistics file that never got its final write.

What is checked here is the protocol and the guarantees around it. That the
mechanism actually produces a clean exit was measured against a real child
process on Windows - 2.01s and a lost aboutToQuit before, 0.02s and a clean
one after - and that measurement lives in the commit message, because it
needs two processes and a window manager and does not belong in a suite that
must run headless on a CI runner.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import io

from loom import statefeed, stopline


def test_the_encoded_line_is_one_terminated_ascii_line():
    """It is written to a pipe, so it has to be bytes and it has to end."""
    encoded = stopline.encode()

    assert isinstance(encoded, bytes)
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1


def test_the_sentinel_round_trips():
    assert stopline.is_stop_line(stopline.encode().decode("ascii"))


def test_a_line_that_merely_starts_with_the_sentinel_is_not_one():
    """Unlike statefeed's sentinel this is the whole line, not a prefix with
    a payload after it, so a longer word must not be mistaken for it."""
    assert not stopline.is_stop_line("LOOM_STOPPING now")


def test_ordinary_output_is_not_a_stop_request():
    assert not stopline.is_stop_line("")
    assert not stopline.is_stop_line("waiting for the game")
    assert not stopline.is_stop_line(statefeed.encode({"usable": False}))


def test_surrounding_whitespace_does_not_hide_the_request():
    """A pipe can deliver \\r\\n on Windows, and a stop request that was not
    recognised because of a carriage return would be the same bug this
    module exists to fix, wearing a different hat."""
    assert stopline.is_stop_line("LOOM_STOP\r\n")
    assert stopline.is_stop_line("  LOOM_STOP  ")


def test_the_sentinel_cannot_be_confused_with_a_state_line():
    """Both ride the same pair of pipes between the same two processes, in
    opposite directions. Overlapping vocabularies would be a nasty bug."""
    assert not statefeed.is_state_line(stopline.SENTINEL)
    assert not stopline.is_stop_line(statefeed.SENTINEL)


# ---- the read loop --------------------------------------------------------

def test_the_request_stops_the_loop_and_reports_why():
    stopped = []
    ended = stopline.read_until_stop(
        io.StringIO("some log line\nLOOM_STOP\n"), lambda: stopped.append(1))

    assert ended is True, "should report that it was asked, not that it ended"
    assert stopped == [1]


def test_nothing_after_the_request_is_read():
    """The loop must not run on: the caller is quitting, and a later line
    triggering a second on_stop would be a double shutdown."""
    stream = io.StringIO("LOOM_STOP\nand more\n")
    calls = []

    stopline.read_until_stop(stream, lambda: calls.append(1))

    assert calls == [1]
    assert stream.readline() == "and more\n", "the loop read past the request"


def test_a_closed_stdin_does_NOT_stop():
    """The regression this file most needs to hold.

    The first draft treated EOF as a stop request, so that a launcher going
    away without asking would bring the overlay down too. What it actually
    did was kill any overlay started without a stdin - redirected from
    /dev/null, backgrounded as a shell job, launched from a desktop shortcut
    - because EOF arrives instantly. The panel exited before it had drawn
    anything: "Demo mode at 20.0x" and then "Stopped."

    An orphan after a launcher crash is a window somebody closes. An overlay
    that only runs when the launcher spawned it is a program that does not
    work.
    """
    stopped = []
    ended = stopline.read_until_stop(io.StringIO(""), lambda: stopped.append(1))

    assert ended is False
    assert stopped == [], "EOF must NOT be treated as a stop request"


def test_input_that_never_contains_the_sentinel_does_not_stop():
    stopped = []

    stopline.read_until_stop(io.StringIO("one\ntwo\nthree\n"),
                             lambda: stopped.append(1))

    assert stopped == []


def test_unexpected_input_is_ignored_rather_than_fatal():
    """Nothing else writes to a child's stdin today. Inventing a failure for
    a line nobody sent would be a way to kill the overlay by accident."""
    stopped = []
    stopline.read_until_stop(
        io.StringIO("hello\n\n{}\nLOOM_STOP\n"), lambda: stopped.append(1))

    assert stopped == [1]


def test_a_broken_stream_does_not_raise_out_of_the_thread():
    """This runs on a daemon thread nobody is watching, so an exception here
    would be both invisible and fatal to the stop path."""
    class Broken:
        def __iter__(self):
            return self

        def __next__(self):
            raise OSError("stdin went away")

    stopped = []
    ended = stopline.read_until_stop(Broken(), lambda: stopped.append(1))

    assert ended is False
    assert stopped == [], "a broken stream is not a stop request either"


def test_watch_runs_on_a_daemon_thread():
    """Not a daemon, and a child that exits for any other reason would hang
    forever on a blocking read of a stdin nobody is going to write to."""
    finished = []
    thread = stopline.watch(lambda: finished.append(1),
                            stream=io.StringIO("LOOM_STOP\n"))
    thread.join(timeout=5)

    assert thread.daemon
    assert not thread.is_alive()
    assert finished == [1]
