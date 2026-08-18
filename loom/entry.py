"""
Loom — running one of Loom's programs from a single executable.

Loom is four programs (the launcher, the overlay, the coach, the readout) and
the launcher starts the others as child processes, because each owns its own
main loop and the overlay owns a whole QApplication. From a clone that is
easy: run `sys.executable loom_overlay.py`. Frozen into a bundle it is
impossible, because `sys.executable` IS the bundle - there is no interpreter
to hand a script to, and the script is not on disk anyway.

The standard answer, and the one here: one executable that dispatches on a
`--mode` argument. `Loom.exe` is the launcher; `Loom.exe --mode overlay
--build fast_castle` is the overlay. runner.py builds whichever form fits the
installation, so nothing else has to know which one it is running under.

This works identically unfrozen, which is the point - it can be exercised
from a clone today rather than only after a bundle exists.

The mode is stripped from argv before the program's own parser sees it, so
each entry point keeps its own arguments and its own --help.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

# mode name -> (module to import, the script it replaces). The script name is
# kept so runner.py can translate one to the other without a second table.
MODES = {
    "overlay": ("loom_overlay", "loom_overlay.py"),
    "coach": ("loom_coach", "loom_coach.py"),
    "read": ("loom_read", "loom_read.py"),
}

# What runner.py passes today, mapped to the mode that does the same job.
SCRIPTS = {script: mode for mode, (_module, script) in MODES.items()}

MODE_FLAG = "--mode"


def windows_app_identity():
    """Tell Windows these processes are Loom, not python.exe.

    The taskbar groups windows and picks their icon by AppUserModelID, and
    a script's default identity is the interpreter's - so without this the
    taskbar shows the Python icon however thoroughly the windows set their
    own. Harmless everywhere else: off Windows there is no windll and this
    quietly does nothing. Call it before any window exists.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Loom")
    except (AttributeError, OSError):
        pass


def frozen():
    """Is this a bundle rather than a clone? PyInstaller sets sys.frozen."""
    return getattr(sys, "frozen", False)


def split_mode(argv):
    """Pull `--mode NAME` out of an argument list.

    Returns (mode or None, the remaining arguments). Accepts both
    `--mode overlay` and `--mode=overlay`, because one of them is what
    somebody will type.

    Only the FIRST occurrence is taken and the rest are left alone: a build
    order called "--mode" is not a real worry, but an argument that happens
    to follow one is, and quietly eating it would be worse than ignoring it.
    """
    rest = list(argv)
    for index, argument in enumerate(rest):
        if argument == MODE_FLAG and index + 1 < len(rest):
            return rest[index + 1], rest[:index] + rest[index + 2:]
        if argument.startswith(MODE_FLAG + "="):
            return (argument.split("=", 1)[1],
                    rest[:index] + rest[index + 1:])
    return None, rest


def argv_for(args):
    """The argv runner.py should start a child with, for this installation.

    `args` is what the launcher asks for today - ["loom_overlay.py",
    "--demo"], or ["-m", "tools.apm_counter"]. From a clone that is handed
    to the interpreter unchanged. Frozen, a known script becomes `--mode`
    on the bundle itself.

    Returns (program, arguments). Raises ValueError when frozen and the
    request has no mode - the developer tools are the case, and they are not
    in a bundle to run anyway, so saying so beats starting the launcher again
    by accident.
    """
    if not frozen():
        # -u is load-bearing: without it Python buffers stdout when it is a
        # pipe, and the launcher's output pane sits dead until the child exits.
        return sys.executable, ["-u"] + list(args)

    args = list(args)
    mode = SCRIPTS.get(args[0]) if args else None
    if mode is None:
        raise ValueError(
            f"{args!r} cannot be run from a packaged copy of Loom - it has no "
            f"--mode. Only {', '.join(sorted(MODES))} are built in.")
    return sys.executable, [MODE_FLAG, mode] + args[1:]


def run(mode, argv):
    """Run one of Loom's programs. Returns its exit code.

    The module is imported here rather than at the top because importing an
    entry point runs its module-level setup - loom_overlay picks a Qt
    platform plugin, for one - and doing that for three programs to run one
    would be both wasteful and wrong.
    """
    import importlib

    # The moral equivalent of the -u every unfrozen child is started with.
    # A frozen child has no interpreter flag, and a pipe makes stdout
    # block-buffered - measured: the launcher's output pane and the build
    # preview saw NOTHING until the child exited and the buffer flushed.
    # Line buffering restores the one property the statefeed protocol
    # needs: a line printed is a line delivered.
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass

    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; expected one of "
                         f"{', '.join(sorted(MODES))}")
    module_name, _script = MODES[mode]
    # The child's own parser reads sys.argv, so it has to look like the
    # program was started directly.
    sys.argv = [module_name] + list(argv)
    return importlib.import_module(module_name).main()
