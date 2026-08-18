"""
Loom — one executable, four programs.

The launcher starts the overlay, the coach and the readout as child
processes. From a clone that is `python loom_overlay.py`; from a bundle it
cannot be, because `sys.executable` IS the bundle and the script is not on
disk. loom/entry.py is the translation, and runner.py asks it rather than
building an argv itself.

This is the one distribution prerequisite that can be tested before a bundle
exists, because the dispatch works identically either way - which is most of
the reason for doing it as a `--mode` argument rather than something clever
at packaging time.

`frozen` is faked rather than detected: the point is to check the packaged
path from an unpackaged checkout, which is the only place it can be checked
at all right now.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

import pytest

from loom import entry


# ---- pulling the mode out of an argument list ------------------------------

def test_no_mode_leaves_everything_alone():
    """The launcher's own case: no --mode, so it is the launcher."""
    assert entry.split_mode(["--build", "arena"]) == (None, ["--build", "arena"])


def test_an_empty_argument_list_has_no_mode():
    assert entry.split_mode([]) == (None, [])


def test_the_mode_comes_out_and_the_rest_survives():
    """The child's own parser has to see its arguments unchanged, or every
    entry point would need to learn about --mode."""
    mode, rest = entry.split_mode(["--mode", "overlay", "--build", "arena"])

    assert mode == "overlay"
    assert rest == ["--build", "arena"]


def test_the_equals_spelling_works_too():
    assert entry.split_mode(["--mode=coach", "--simulate"]) == (
        "coach", ["--simulate"])


def test_a_mode_in_the_middle_is_still_found():
    mode, rest = entry.split_mode(["--demo", "--mode", "overlay", "--speed", "4"])

    assert mode == "overlay"
    assert rest == ["--demo", "--speed", "4"]


def test_a_trailing_mode_with_no_value_is_not_a_mode():
    """Better to start the launcher than to swallow the flag and dispatch to
    nothing."""
    assert entry.split_mode(["--mode"]) == (None, ["--mode"])


# ---- what runner.py should start -------------------------------------------

def test_from_a_clone_the_interpreter_runs_the_script(monkeypatch):
    monkeypatch.setattr(entry, "frozen", lambda: False)

    program, arguments = entry.argv_for(["loom_overlay.py", "--demo"])

    assert program == sys.executable
    assert arguments == ["-u", "loom_overlay.py", "--demo"]


def test_unbuffered_is_not_optional(monkeypatch):
    """Without -u, Python buffers stdout when it is a pipe and the launcher's
    output pane sits dead until the child exits."""
    monkeypatch.setattr(entry, "frozen", lambda: False)

    assert entry.argv_for(["loom_coach.py"])[1][0] == "-u"


def test_frozen_turns_a_script_into_a_mode(monkeypatch):
    monkeypatch.setattr(entry, "frozen", lambda: True)

    program, arguments = entry.argv_for(["loom_overlay.py", "--build", "arena"])

    assert program == sys.executable          # the bundle itself
    assert arguments == ["--mode", "overlay", "--build", "arena"]


@pytest.mark.parametrize("script, mode", [
    ("loom_overlay.py", "overlay"),
    ("loom_coach.py", "coach"),
    ("loom_read.py", "read"),
])
def test_every_program_the_launcher_starts_has_a_mode(script, mode,
                                                      monkeypatch):
    """A program the launcher can start but the bundle cannot run would be a
    button that works from a clone and not from a release."""
    monkeypatch.setattr(entry, "frozen", lambda: True)

    assert entry.argv_for([script])[1][:2] == ["--mode", mode]


def test_frozen_refuses_what_it_cannot_run(monkeypatch):
    """The developer tools are the case - "-m pytest" and friends are not in
    a bundle to run. Saying so beats launching a second launcher by accident,
    which is what dispatching to nothing would do."""
    monkeypatch.setattr(entry, "frozen", lambda: True)

    with pytest.raises(ValueError):
        entry.argv_for(["-m", "tools.grab_frames"])


def test_the_two_tables_agree():
    """SCRIPTS is derived from MODES, so they cannot drift - but a typo in
    MODES would still produce a mode nothing maps to."""
    assert set(entry.SCRIPTS.values()) == set(entry.MODES)
    for mode, (module, script) in entry.MODES.items():
        assert entry.SCRIPTS[script] == mode
        assert script == f"{module}.py"


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        entry.run("nonsense", [])


def test_the_launcher_is_not_a_mode():
    """Loom.exe with no --mode IS the launcher; giving it a mode as well
    would be a second way to spell the default."""
    assert "launcher" not in entry.MODES
    assert "app" not in entry.MODES
