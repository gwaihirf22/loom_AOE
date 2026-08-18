"""
Loom — the overlay's state, as lines on its own stdout.

The launcher shows a live build preview, but it and the overlay are separate
processes (the overlay owns its own QApplication), so the preview needs the
overlay's readings somehow. The channel already exists: the launcher spawns
the overlay and streams its stdout line by line. So the overlay prints its
state as one compact JSON line behind a sentinel, and the launcher routes
those lines to the preview instead of the log pane. One producer, one
consumer, a pipe the launcher already owns - a socket or shared memory would
buy nothing but moving parts.

The payload keeps build_order.py's own semantics: "idx" is in current_index()
terms - the last COMPLETED step, -1 before the first - so the wire format
never needs translating. It is the step the overlay is ACTUALLY showing,
which is usually what the reading implies and is the player's own cursor when
they have nudged it with a hotkey; either way it is the same semantics, so
consumers need no special case.

"mode" says which of those it is - "following", "holding" or "manual", from
loom/follow.py - so a second window cannot claim to be following the game
while the panel says it is not. Compact keys because this rides a pipe once a
second for a whole match:

    {"usable": true, "idx": 4, "mode": "following", "vills": 13, "t": 251,
     "pace": -8, "res": {"food": 7, ...} | null, "pop": [14, 20] | null}

The APM counter borrows this same channel with a payload of its own,
{"apm": {"wall": ..., "keys": ..., "clicks": ...}}, which the launcher tells
apart by the key rather than by any type field.

A reading the overlay cannot use is announced as exactly {"usable": false},
once - the launcher takes that as "no game to follow, browsing allowed".
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

# Starts a state line; everything else on stdout is human log output. The
# trailing space is part of the sentinel, so a word that merely begins with
# LOOM_STATE can never be mistaken for one.
SENTINEL = "LOOM_STATE "


def encode(payload):
    """One state line, sentinel plus compact JSON."""
    return SENTINEL + json.dumps(payload, separators=(",", ":"), sort_keys=True)


def is_state_line(line):
    """Is this stdout line a state line rather than log output?"""
    return line.startswith(SENTINEL)


def decode(line):
    """The payload of a state line, or None for anything else.

    None rather than an exception for mangled JSON: a torn or corrupted line
    is not worth crashing a UI over, and the next good line is at most a
    second away.
    """
    if not is_state_line(line):
        return None
    try:
        payload = json.loads(line[len(SENTINEL):])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


class StateEmitter:
    """Prints state lines, skipping repeats.

    The overlay polls every 300ms but the payload only changes about once a
    second (game time is whole seconds, pace is rounded), so comparing to the
    last emitted payload keeps the pipe quiet without any timer logic. The
    same check collapses "no usable reading" to a single line however long
    the menus take.
    """

    def __init__(self):
        self._last = None

    def emit(self, payload):
        if payload == self._last:
            return
        self._last = payload
        # The overlay runs under python -u, so this reaches the launcher now
        # rather than when a buffer happens to fill.
        print(encode(payload))
