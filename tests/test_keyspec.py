"""
Loom — the hotkey grammar, checked on every platform.

loom/hotkeys/keyspec.py is deliberately platform-free so this file runs
wherever the suite runs. That is the same discipline the capture backends
follow, and for the same reason: Linux and Windows are a dual boot, so a rule
that can only be checked on one of them gets checked half as often.

The rule worth the most attention is the one that refuses a binding with no
modifier. A global hotkey is taken from every other program including the
game, so a bare "Q" would quietly stop Q working in Age of Empires for as
long as Loom was running - a spectacular way to lose a match, and one nobody
would connect to a build-order overlay.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom.hotkeys import keyspec


# ---- the defaults have to parse -------------------------------------------

@pytest.mark.parametrize("binding", [
    "Ctrl+Shift+Q", "Ctrl+Shift+W", "Ctrl+Shift+R",
])
def test_the_shipped_defaults_are_valid(binding):
    """If a default ever stopped parsing, hotkeys would be dead on arrival
    for every new install and nothing else would say so."""
    assert keyspec.parse(binding).key in keyspec.KEYS


# ---- parsing ---------------------------------------------------------------

def test_modifiers_and_key_come_apart():
    spec = keyspec.parse("Ctrl+Shift+Q")

    assert spec.modifiers == {"Ctrl", "Shift"}
    assert spec.key == "Q"


def test_order_does_not_matter():
    """Two spellings of one chord must be one binding, or conflict detection
    would miss a player who bound the same combination twice."""
    assert keyspec.parse("Shift+Ctrl+Q") == keyspec.parse("Ctrl+Shift+Q")


def test_case_and_spacing_do_not_matter():
    assert keyspec.parse(" ctrl + SHIFT + q ") == keyspec.parse("Ctrl+Shift+Q")


@pytest.mark.parametrize("written, canonical", [
    ("control+shift+q", "Ctrl+Shift+Q"),
    ("super+f1", "Win+F1"),
    ("alt+esc", "Alt+Escape"),
    ("ctrl+option+return", "Ctrl+Alt+Enter"),
    ("shift+ctrl+pgdn", "Ctrl+Shift+PageDown"),
    ("ctrl+/", "Ctrl+Slash"),
])
def test_aliases_normalise_to_one_spelling(written, canonical):
    """Players type what their other tools taught them. Accepting the
    spellings and writing back one of them keeps the config file readable
    without arguing with anybody."""
    assert keyspec.normalise(written) == canonical


def test_canonical_order_is_stable():
    assert keyspec.normalise("shift+win+alt+ctrl+a") == "Ctrl+Alt+Shift+Win+A"


# ---- the rule that protects the game ---------------------------------------

def test_a_bare_key_is_refused():
    with pytest.raises(ValueError) as refused:
        keyspec.parse("Q")

    assert "no modifier" in str(refused.value)


def test_the_refusal_explains_what_it_would_cost():
    """The message is shown to a player, so it has to say why rather than
    just no - the reason (the game stops seeing that key) is not obvious."""
    message = str(pytest.raises(ValueError, keyspec.parse, "F5").value)

    assert "Age of Empires" in message


# ---- everything else that should fail --------------------------------------

@pytest.mark.parametrize("binding, expected", [
    ("", "some keys"),
    ("   ", "some keys"),
    (None, "some keys"),
    ("Ctrl+Shift", "no key"),
    ("Ctrl+", "empty piece"),
    ("Ctrl++Q", "empty piece"),
    ("Ctrl+Shift+NotAKey", "unknown key"),
    ("Ctrl+Q+W", "two keys"),
    ("Ctrl+Ctrl+Q", "twice"),
])
def test_broken_bindings_say_what_is_wrong(binding, expected):
    with pytest.raises(ValueError) as refused:
        keyspec.parse(binding)

    assert expected in str(refused.value)


# ---- disabled is a choice, not a fault -------------------------------------

@pytest.mark.parametrize("binding", ["", "   ", None])
def test_an_empty_binding_is_disabled_not_broken(binding):
    """A player switching one action off must be distinguishable from a
    player typing nonsense: one is a setting, the other is a complaint."""
    assert keyspec.is_disabled(binding)
    assert keyspec.problem(binding) is None


def test_a_real_binding_is_not_disabled():
    assert not keyspec.is_disabled("Ctrl+Shift+Q")
    assert keyspec.problem("Ctrl+Shift+Q") is None


def test_problem_reports_without_raising():
    """So a caller can check a whole settings dict without a try/except
    around every read."""
    assert "unknown key" in keyspec.problem("Ctrl+Shift+Nope")


# ---- two actions on one combination ----------------------------------------

def test_a_clash_is_found():
    """The OS reports nothing here - whichever registers first wins and the
    other silently never fires - so it has to be caught in the grammar."""
    clashes = keyspec.conflicts({"next_step": "Ctrl+Shift+W",
                                 "previous_step": "shift+ctrl+w"})

    assert clashes == [("next_step", "previous_step")]


def test_the_shipped_defaults_do_not_clash():
    from loom import config
    assert keyspec.conflicts(config.DEFAULT_HOTKEYS) == []


def test_disabled_actions_never_clash():
    """Two actions both switched off are not two actions on one key."""
    assert keyspec.conflicts({"a": "", "b": "", "c": None}) == []


def test_a_broken_binding_is_not_reported_as_a_clash():
    """problem() owns that complaint; reporting it twice in two vocabularies
    would just be noise."""
    assert keyspec.conflicts({"a": "Ctrl+Nope", "b": "Ctrl+Alt+Nope"}) == []


# ---- the vocabulary --------------------------------------------------------

def test_every_key_name_round_trips():
    """Every name the grammar accepts must survive being written out and read
    back, or a saved config could fail to load the binding it just saved."""
    for key in keyspec.KEYS:
        binding = f"Ctrl+{key}"
        assert keyspec.normalise(binding) == binding, key


def test_letters_digits_and_function_keys_are_all_there():
    assert "A" in keyspec.KEYS and "Z" in keyspec.KEYS
    assert "0" in keyspec.KEYS and "9" in keyspec.KEYS
    assert "F1" in keyspec.KEYS and "F24" in keyspec.KEYS
