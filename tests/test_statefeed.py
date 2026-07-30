"""
Loom — tests for the overlay→launcher state channel.

Two processes agree on a line format here, so what matters is the contract:
a state line survives the round trip, ordinary log output is never mistaken
for state, a torn line degrades to "no payload" rather than an exception,
and the emitter stays quiet while nothing changes.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import statefeed

PAYLOAD = {"usable": True, "idx": 4, "vills": 13, "t": 251, "pace": -8,
           "res": {"food": 7, "wood": 5, "gold": 0, "stone": 0},
           "pop": [14, 20]}


def test_encode_decode_round_trip():
    line = statefeed.encode(PAYLOAD)
    assert statefeed.is_state_line(line)
    assert statefeed.decode(line) == PAYLOAD


def test_a_state_line_is_one_line():
    # The channel is line-oriented; an embedded newline would tear the frame.
    assert "\n" not in statefeed.encode(PAYLOAD)


def test_decode_ignores_ordinary_output():
    assert statefeed.decode("Demo mode at 20.0x. Ctrl+C to stop.") is None
    assert statefeed.decode("[overlay] LOOM_STATE {}") is None   # prefixed = log
    assert statefeed.decode("LOOM_STATEFUL nonsense") is None    # sentinel includes the space
    assert statefeed.decode("") is None


def test_decode_ignores_mangled_json():
    assert statefeed.decode('LOOM_STATE {"usable": tru') is None
    assert statefeed.decode('LOOM_STATE [1, 2, 3]') is None      # a dict or nothing


def test_emitter_prints_only_on_change(capsys):
    emitter = statefeed.StateEmitter()
    emitter.emit(dict(PAYLOAD))
    emitter.emit(dict(PAYLOAD))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert statefeed.decode(lines[0]) == PAYLOAD


def test_not_usable_emits_once_then_resumes(capsys):
    # However long the menus take, "no reading" is said once; the next real
    # reading speaks again.
    emitter = statefeed.StateEmitter()
    emitter.emit({"usable": False})
    emitter.emit({"usable": False})
    emitter.emit({"usable": False})
    emitter.emit(dict(PAYLOAD))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert statefeed.decode(lines[0]) == {"usable": False}
    assert statefeed.decode(lines[1]) == PAYLOAD
