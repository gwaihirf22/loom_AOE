"""
Loom — turning a key the player pressed into a binding keyspec can read.

The settings window used to ask for "Ctrl+Shift+Q" typed into a text field.
This is the translation that lets them press it instead.

The load-bearing test is the last one. Both OS backends already have a test
asserting that every key in keyspec.KEYS has an entry in their table, for a
reason test_hotkeys_selector states plainly: a key with no entry is "a
binding a player can save in the launcher and which then silently never
fires on that platform". A key with no QT entry is the same fault one step
earlier - a binding the player cannot even ask for. So this file carries the
same guard, derived from the same list.

No QApplication is built. Qt.Key is an enum, and reading it needs no display
- the same reasoning as the window-flag tests.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from PyQt6.QtCore import Qt

from loom.hotkeys import keyspec, qtkeys

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
WIN = Qt.KeyboardModifier.MetaModifier
NONE = Qt.KeyboardModifier.NoModifier


# ---- what a press turns into --------------------------------------------


def test_a_plain_combination_reads_back_as_itself():
    assert qtkeys.binding_for(Qt.Key.Key_Q.value, CTRL | SHIFT) == "Ctrl+Shift+Q"


def test_the_modifier_order_is_canonical_whatever_order_they_are_held():
    """keyspec compares bindings as strings for conflict detection, so two
    spellings of one combination would read as two different bindings and a
    clash would go unnoticed."""
    one = qtkeys.binding_for(Qt.Key.Key_Q.value, CTRL | SHIFT)
    other = qtkeys.binding_for(Qt.Key.Key_Q.value, SHIFT | CTRL)

    assert one == other == "Ctrl+Shift+Q"


def test_all_four_modifiers_come_out_in_the_grammars_order():
    assert qtkeys.binding_for(Qt.Key.Key_A.value,
                              SHIFT | WIN | ALT | CTRL) == "Ctrl+Alt+Shift+Win+A"


def test_the_windows_key_is_called_win_not_meta():
    """Qt names its modifiers after the Mac; keyspec names them after the
    keycaps a Windows or Linux player is looking at."""
    assert qtkeys.binding_for(Qt.Key.Key_A.value, WIN) == "Win+A"


@pytest.mark.parametrize("key, expected", [
    (Qt.Key.Key_F1, "Ctrl+F1"),
    (Qt.Key.Key_F24, "Ctrl+F24"),
    (Qt.Key.Key_0, "Ctrl+0"),
    (Qt.Key.Key_Space, "Ctrl+Space"),
    (Qt.Key.Key_Tab, "Ctrl+Tab"),
    (Qt.Key.Key_Escape, "Ctrl+Escape"),
    (Qt.Key.Key_Delete, "Ctrl+Delete"),
    (Qt.Key.Key_Up, "Ctrl+Up"),
])
def test_the_awkward_keys_come_through(key, expected):
    assert qtkeys.binding_for(key.value, CTRL) == expected


def test_enter_is_the_main_return_key_not_the_numpad_one():
    """Qt has both Key_Return and Key_Enter; the numpad is not in the
    vocabulary at all, so Enter has to mean the big one."""
    assert qtkeys.binding_for(Qt.Key.Key_Return.value, ALT) == "Alt+Enter"


# ---- what it declines ---------------------------------------------------


@pytest.mark.parametrize("key", [Qt.Key.Key_Control, Qt.Key.Key_Shift,
                                 Qt.Key.Key_Alt, Qt.Key.Key_Meta])
def test_a_modifier_on_its_own_is_not_an_answer(key):
    """Qt sends a key event for Ctrl itself, not only for the letter after
    it. A capture field wants to keep waiting, not record "Ctrl"."""
    assert qtkeys.binding_for(key.value, CTRL) is None
    assert qtkeys.is_modifier_only(key.value)


@pytest.mark.parametrize("key", [Qt.Key.Key_CapsLock, Qt.Key.Key_NumLock,
                                 Qt.Key.Key_Print, Qt.Key.Key_Pause,
                                 Qt.Key.Key_VolumeUp])
def test_a_key_outside_the_vocabulary_is_declined(key):
    """Neither OS backend has a table entry for these, so a binding naming
    one would save and then never fire. Better to refuse the press."""
    assert qtkeys.binding_for(key.value, CTRL) is None
    assert not qtkeys.is_modifier_only(key.value)


def test_declining_is_distinguishable_from_still_waiting():
    """Both return None, and the caller has to say different things about
    them - "keep going" against "that key cannot be bound"."""
    assert qtkeys.is_modifier_only(Qt.Key.Key_Shift.value)
    assert not qtkeys.is_modifier_only(Qt.Key.Key_CapsLock.value)


# ---- policy belongs to keyspec, not here --------------------------------


def test_a_key_with_no_modifier_is_translated_not_refused():
    """This function translates; keyspec decides. Its refusal explains that
    the game would lose the key, which is the message worth showing - so a
    bare press has to reach it rather than being swallowed here.
    """
    bare = qtkeys.binding_for(Qt.Key.Key_Q.value, NONE)

    assert bare == "Q"
    assert "no modifier" in keyspec.problem(bare)


# ---- the progress text --------------------------------------------------


def test_the_modifiers_show_themselves_gathering():
    assert qtkeys.describe(CTRL | SHIFT) == "Ctrl+Shift+..."


def test_nothing_held_says_nothing():
    assert qtkeys.describe(NONE) == ""


# ---- the guard the backends already have --------------------------------


def test_every_key_in_the_grammar_can_be_pressed():
    """Derived from keyspec.KEYS, not typed out here.

    A key added to the vocabulary later fails this until it is mapped,
    rather than being quietly unreachable - which is exactly the guarantee
    test_hotkeys_selector gives for the Windows and X11 tables, one step
    further down.
    """
    reachable = set(qtkeys.key_table().values())

    missing = [key for key in keyspec.KEYS if key not in reachable]
    assert not missing, f"no Qt key maps to: {missing}"


def test_every_mapped_key_round_trips_through_the_grammar():
    """The translation has to produce something keyspec accepts, for all 86
    of them - not only the handful anyone would think to try."""
    for code, name in qtkeys.key_table().items():
        binding = qtkeys.binding_for(code, CTRL)
        assert binding == f"Ctrl+{name}"
        assert keyspec.normalise(binding) == binding


# ---- what Shift does to the key's NAME ----------------------------------
#
# The bug this corpus exists for: Qt reports the character a key PRODUCES,
# not the key that was struck, so with Shift held Key_9 arrives as
# Key_ParenLeft and Key_Semicolon as Key_Colon. None of those are in
# keyspec.KEYS, so binding_for declined every one - 21 of the 86 keys in the
# vocabulary were uncapturable the moment a player held Shift - among them
# Ctrl+Shift+Minus, which Loom now ships as the default for toggle_hidden.
#
# These triples are MEASURED, not derived: real Ctrl+Shift chords put through
# SendInput on Windows 11 with the US layout, printing what Qt handed the
# widget. That matters, because deriving them from the same table the code
# uses would be a test agreeing with its subject rather than checking it.
#
# The two marked below are the exceptions and are labelled rather than
# quietly mixed in - see the note under the table.
SHIFT_PRESS = {
    # keyspec name: (Qt key the widget receives, event.nativeVirtualKey())
    "1": (Qt.Key.Key_Exclam, 0x31),
    "2": (Qt.Key.Key_At, 0x32),
    "3": (Qt.Key.Key_NumberSign, 0x33),
    "4": (Qt.Key.Key_Dollar, 0x34),
    "5": (Qt.Key.Key_Percent, 0x35),
    "6": (Qt.Key.Key_AsciiCircum, 0x36),
    "7": (Qt.Key.Key_Ampersand, 0x37),
    "8": (Qt.Key.Key_Asterisk, 0x38),
    "9": (Qt.Key.Key_ParenLeft, 0x39),
    "0": (Qt.Key.Key_ParenRight, 0x30),          # inferred - see below
    "Minus": (Qt.Key.Key_Underscore, 0xBD),
    "Equal": (Qt.Key.Key_Plus, 0xBB),
    "Comma": (Qt.Key.Key_Less, 0xBC),
    "Period": (Qt.Key.Key_Greater, 0xBE),
    "Slash": (Qt.Key.Key_Question, 0xBF),
    "Backslash": (Qt.Key.Key_Bar, 0xDC),
    "Semicolon": (Qt.Key.Key_Colon, 0xBA),
    "Quote": (Qt.Key.Key_QuoteDbl, 0xDE),
    "LeftBracket": (Qt.Key.Key_BraceLeft, 0xDB),
    "RightBracket": (Qt.Key.Key_BraceRight, 0xDD),
    "Backtick": (Qt.Key.Key_AsciiTilde, 0xC0),
    "Tab": (Qt.Key.Key_Backtab, 0x09),
}

# "0" is the one row that could not be delivered, and it earns a paragraph
# because the reason turned out to be a SECOND fault rather than a flaw in
# the harness. Ctrl+Shift+0 never reaches Qt at all: in one run Shift+0
# arrived as Key_ParenRight and Ctrl+Shift+1 as Key_Exclam while Ctrl+Shift+0
# produced no key event whatsoever, and the author confirmed the same from a
# real keyboard. RegisterHotKey still reports the combination free, so
# Windows is consuming the keystrokes rather than another program owning
# them - which is exactly why it could be REGISTERED and used while being
# impossible to type into the settings window. That is what moved the
# toggle_hidden default off it.
#
# So this row's entry is taken from the layout instead: ToUnicodeEx says
# Shift+VK_0 is ")" here, and Qt's key code for an ASCII printable is its
# codepoint. Said out loud rather than left to look measured, because a value
# that was reasoned about sitting in a table of readings is the same fault as
# an assumption dressed as a reading.


@pytest.mark.parametrize("name", sorted(SHIFT_PRESS))
def test_shift_does_not_stop_a_key_being_captured(name):
    """The regression. Every one of these declined before."""
    key, native = SHIFT_PRESS[name]

    assert qtkeys.binding_for(key.value, CTRL | SHIFT,
                              native=native) == f"Ctrl+Shift+{name}"


@pytest.mark.parametrize("name", sorted(SHIFT_PRESS))
def test_the_shifted_row_alone_gets_there_without_a_native_code(name):
    """X11 has no native code to offer - Qt sets nativeVirtualKey to the
    keysym, which Shift renames in exactly the same way - so the US shifted
    row has to carry Linux on its own. Loom is developed on a dual boot and
    a fix that only works on one half is half a fix."""
    key, _native = SHIFT_PRESS[name]

    assert qtkeys.binding_for(key.value, CTRL | SHIFT) == f"Ctrl+Shift+{name}"


def test_the_table_holds_exactly_what_was_measured():
    """Walks the corpus against the shipped table rather than sampling it.

    An entry dropped from either side fails here, which a handful of
    parametrised cases would not - the same reason the chart tests walk the
    registry instead of agreeing with a hand-written list.
    """
    assert set(qtkeys.shifted_table().values()) == set(SHIFT_PRESS)


def test_the_native_code_wins_where_the_two_disagree():
    """The shifted row is US, and a German player pressing Ctrl+Shift+8 gets
    "(" - which that row reads as the 9 key. Recording a DIFFERENT key from
    the one pressed is worse than refusing the press, so the exact answer is
    preferred wherever the platform has one.

    Asserted through name_for so it holds on Linux too, where native_names
    is empty by design and the row is all there is.
    """
    paren_left = Qt.Key.Key_ParenLeft.value

    assert qtkeys.name_for(paren_left) == "9"          # the row's answer
    assert qtkeys.name_for(paren_left, native=0x38) == "8" \
        if qtkeys.native_names() else True


def test_the_native_table_is_the_one_the_hotkey_gets_registered_with():
    """Not a second copy of the vocabulary: it inverts windows.virtual_key,
    which is the pure function the Windows backend registers through. So a
    key the field captures and the key the OS is later asked for cannot
    disagree - and because that function touches no OS, this holds from
    Linux as well.
    """
    from loom.hotkeys import windows

    codes = {name: windows.virtual_key(name) for name in keyspec.KEYS}

    assert len(set(codes.values())) == len(keyspec.KEYS), "two keys, one code"


def test_every_binding_loom_ships_as_a_default_can_be_pressed_back_in():
    """The property that would have caught this on the day it landed.

    Ctrl+Shift+0 shipped as toggle_hidden's default and could be USED but
    never re-entered once cleared - a setting the program offers and its own
    settings window cannot produce. Walking DEFAULT_HOTKEYS rather than a
    list typed in here is what covers its replacement, Ctrl+Shift+Minus,
    without anyone remembering this file - and it would have failed on the
    old default too, which is the point.
    """
    from loom import config

    unreachable = []
    for action, binding in config.DEFAULT_HOTKEYS.items():
        spec = keyspec.parse(binding)
        modifiers = Qt.KeyboardModifier.NoModifier
        for modifier in spec.modifiers:
            modifiers |= {"Ctrl": CTRL, "Shift": SHIFT,
                          "Alt": ALT, "Win": WIN}[modifier]
        # What Qt would really deliver: the shifted name where Shift renames
        # the key, the plain one otherwise.
        if "Shift" in spec.modifiers and spec.key in SHIFT_PRESS:
            key, native = SHIFT_PRESS[spec.key]
        else:
            key, native = getattr(Qt.Key, f"Key_{spec.key}"), None
        got = qtkeys.binding_for(key.value, modifiers, native=native)
        if got != binding:
            unreachable.append(f"{action}: {binding} pressed back as {got}")

    assert not unreachable, "\n".join(unreachable)


def test_the_native_table_is_windows_only_and_that_is_not_tidiness():
    """An X keysym and a Windows virtual key are different numbers for
    different things, and they COLLIDE. Keysym 0x2D is minus; virtual key
    0x2D is Insert. Consulting a Windows table on Linux would read a pressed
    "-" as Insert and save a binding for a key nobody touched - silently,
    and in the one direction that is worse than refusing.

    So the emptiness is load-bearing, and it is asserted rather than left to
    a comment. Qt sets nativeVirtualKey to the keysym on xcb, which Shift
    renames exactly as it renames the Qt key, so there is nothing there to
    correct with anyway.
    """
    import sys

    from loom.hotkeys import windows

    table = qtkeys.native_names()
    if sys.platform == "win32":
        assert set(table) == {windows.virtual_key(key) for key in keyspec.KEYS}
    else:
        assert table == {}, "a Windows table would misread X keysyms"

    # The collision itself, so the reason survives even where the branch
    # cannot run: these two really are the same number.
    assert windows.virtual_key("Insert") == 0x2D
