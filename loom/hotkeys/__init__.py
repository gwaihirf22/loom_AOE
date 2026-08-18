"""
Loom — global hotkeys, and which backend does them.

The overlay says `from loom import hotkeys` and calls the same names whatever
the operating system. This module picks the implementation once, at import,
and re-exports it; nothing downstream knows or cares. Deliberately the same
shape as loom/capture/, because it is the same problem.

Why these have to be GLOBAL. The overlay is click-through - its window flags
carry WindowTransparentForInput, and loom/passthrough.py exists to prove it -
so it can never receive a Qt key event. While the player is in the game, the
game has focus. A hotkey that only worked when Loom had focus would only work
when Loom was useless.

The contract a backend must provide:

    listen(bindings, on_action)     start listening; returns a handle
    stop(handle)                    stop listening and give the keys back

`bindings` is {action_name: "Ctrl+Shift+Q"}; keyspec.py owns that grammar.
`on_action(action_name)` is called when a combination fires.

WHAT A BACKEND MAY LEARN, and this is a design constraint rather than a
detail: only that one of the combinations it registered has fired. On Windows
that is what RegisterHotKey gives - the OS does the matching and hands back
an id. Nothing here reads a key stream, and no backend may start doing so.
The APM counter makes the same kind of promise, structurally, in loom/apmwin.py
and tools/apm_counter.py; between them they are the only two places in Loom
that see input at all, and both are built so they cannot see more than they
need.

A global hotkey is SWALLOWED - the application that registers a combination
takes it from every other program, the game included. That is why keyspec
refuses a binding with no modifier, why every action can be switched off, and
why a registration failure is reported rather than swallowed.

Importing this module NEVER fails, even with no backend for the platform.
Failing at import would take down the overlay on a platform that simply has
no hotkeys yet; instead the problem is remembered and raised when somebody
tries to listen, where the message can say what is wrong.

LOOM_HOTKEY_BACKEND overrides the choice, for tests and for asking a machine
to load a backend it would not have picked.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import importlib
import os
import sys

from .errors import HotkeyError

# Which backend belongs to which platform. macOS is absent on purpose: it
# would need a Quartz event tap, and macOS is paused - see CLAUDE.md. The
# stub below turns that absence into an honest message rather than a crash.
BACKENDS = {
    "win32": "windows",
    "linux": "x11",
}

# The names every backend must define, and which this module re-exports.
CONTRACT = ("listen", "stop")


def backend_name():
    """The backend this machine should use, honouring the override."""
    return os.environ.get("LOOM_HOTKEY_BACKEND") or BACKENDS.get(sys.platform)


def load_backend(name=None):
    """Import a backend module by name. Raises HotkeyError if it cannot."""
    name = name or backend_name()
    if not name:
        raise HotkeyError(
            f"Loom has no hotkey backend for {sys.platform!r}, so the build "
            f"order can only be followed automatically. Known platforms: "
            f"{', '.join(sorted(BACKENDS))}.")
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ImportError as missing:
        raise HotkeyError(
            f"the {name!r} hotkey backend could not be loaded on "
            f"{sys.platform!r}: {missing}") from missing


def available():
    """Can this machine register hotkeys at all?

    Cheap and safe to call from the launcher, which wants to say so in its
    settings rather than let a player rebind keys that will never fire.
    """
    return backend is not None


def _stub(problem):
    """Stand in for a backend that is not available."""
    def unavailable(*_args, **_kwargs):
        raise problem
    return unavailable


try:
    backend = load_backend()
except HotkeyError as problem:
    backend = None
    for _name in CONTRACT:
        globals()[_name] = _stub(problem)
else:
    for _name in CONTRACT:
        globals()[_name] = getattr(backend, _name)
