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

import ast
import pathlib
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
    assert arguments == ["-u", "-X", "utf8", "loom_overlay.py", "--demo"]


def test_unbuffered_is_not_optional(monkeypatch):
    """Without -u, Python buffers stdout when it is a pipe and the launcher's
    output pane sits dead until the child exits."""
    monkeypatch.setattr(entry, "frozen", lambda: False)

    assert entry.argv_for(["loom_coach.py"])[1][0] == "-u"


def test_the_child_speaks_the_encoding_the_launcher_reads(monkeypatch):
    """ChildProcess._read decodes UTF-8, so the child has to write it.

    A child's stdout is a pipe, and Python encodes a pipe with the locale
    codec - cp1252 on Windows. Measured in the packaged build: Coach
    simulate died on its first arrow with UnicodeEncodeError before printing
    a single line, which from the launcher was indistinguishable from the
    button being broken.
    """
    monkeypatch.setattr(entry, "frozen", lambda: False)

    arguments = entry.argv_for(["loom_coach.py"])[1]

    assert arguments[:3] == ["-u", "-X", "utf8"]


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


def test_can_run_answers_what_argv_for_would_raise_about(monkeypatch):
    """The question a button asks before it lets itself be pressed.

    argv_for raising is correct, but it raised inside a Qt slot, where PyQt6
    aborts the process - and with console=False there was nowhere for the
    traceback to go. From the outside Loom just closed. can_run is how the
    launcher finds out first.
    """
    monkeypatch.setattr(entry, "frozen", lambda: True)

    assert entry.can_run(["loom_overlay.py", "--demo"]) is True
    assert entry.can_run(["loom_coach.py", "--simulate"]) is True
    assert entry.can_run(["loom_read.py"]) is True
    assert entry.can_run(["-m", "tools.grab_frames"]) is False
    assert entry.can_run(["-m", "pytest", "tests/"]) is False
    assert entry.can_run([]) is False


def test_a_clone_can_run_everything(monkeypatch):
    """The other half: nothing is greyed out where there IS a source tree
    and an interpreter, or developing Loom would mean developing without
    the developer tools."""
    monkeypatch.setattr(entry, "frozen", lambda: False)

    assert entry.can_run(["-m", "pytest", "tests/"]) is True
    assert entry.can_run(["loom_overlay.py"]) is True


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


# ---- the crash reporter has to cover the children too ----------------------

def test_the_crash_reporter_is_installed_before_the_mode_dispatch():
    """The children are the processes that crash in front of a player.

    loom_app.main dispatches --mode with a `return`, so anything set up after
    that line covers the launcher alone. The crash reporter was added there,
    below it - which left the overlay, the coach and the readout, the only
    three that ever run unattended over a game, with no reporter at all. A
    capture failure in the packaged overlay came out as PyInstaller's raw
    "Unhandled exception in script" dialog thrown over the match.

    Checked with the AST rather than by reading, for the same reason
    tests/test_apmwin.py checks the privacy claim that way: a comment saying
    "before" is exactly what was already there when this was wrong.
    """
    source = (pathlib.Path(__file__).resolve().parent.parent
              / "loom_app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")

    def line_of_excepthook():
        for node in ast.walk(main):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Attribute)
                            and t.attr == "excepthook" for t in node.targets)):
                return node.lineno
        raise AssertionError("loom_app.main no longer installs sys.excepthook")

    def line_of_mode_return():
        for node in ast.walk(main):
            # `if mode is not None: return entry.run(...)`
            if isinstance(node, ast.If):
                for inner in node.body:
                    if isinstance(inner, ast.Return) and any(
                            isinstance(call, ast.Attribute)
                            and call.attr == "run"
                            for call in ast.walk(inner)):
                        return node.lineno
        raise AssertionError("loom_app.main no longer dispatches on --mode")

    assert line_of_excepthook() < line_of_mode_return(), (
        "sys.excepthook must be installed BEFORE the --mode dispatch returns, "
        "or no child process ever gets it")
