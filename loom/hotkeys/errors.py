"""
Loom — the one failure type the hotkey backends raise.

Its own module for the same reason capture/errors.py is: the backends raise
it and __init__.py imports them, which would otherwise be a circular import.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.


class HotkeyError(Exception):
    """A hotkey could not be registered, or the platform cannot register any.

    Callers are expected to CARRY ON after catching this. Hotkeys are a
    convenience laid over a program whose whole point is that it advances
    itself; an overlay that refused to start because a key combination was
    already taken would be a worse bug than the missing feature.
    """
