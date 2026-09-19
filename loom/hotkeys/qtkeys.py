"""
Loom — one Qt key press, as a binding keyspec can read.

The settings window used to ask the player to TYPE "Ctrl+Shift+Q". This is
what lets them press it instead: a Qt key event goes in, the canonical
binding text comes out, and everything downstream - parsing, conflict
detection, both OS backends - carries on exactly as it did.

WHY THIS IS NOT IN keyspec.py. That module has no imports at all, and its
docstring says why: "the whole grammar is testable on any machine, which
matters here: Linux and Windows are a dual boot, so a rule that can only be
checked on one of them is a rule that gets checked half as often." Importing
Qt into it would spend exactly that. So the grammar stays pure and the Qt
translation lives beside it, in the same package, importing it rather than
being imported by it.

SHIFT CHANGES WHAT QT CALLS A KEY, which is the one thing here that has to
be measured rather than reasoned about - and it cost the digit row. Qt reports
the character the key PRODUCES, not the key that was struck, so with Shift
held Qt.Key_9 arrives as Key_ParenLeft, Minus as Key_Underscore, Semicolon as
Key_Colon, Comma as Key_Less and Tab as Key_Backtab. None of those are in
keyspec.KEYS, so every one of them was declined: 21 of the 86 keys in the
vocabulary were uncapturable the moment a player held Shift. Letters are
unaffected, because Shift+q is Q and Q is in the vocabulary, which is why
this looked like a digit-only fault.

Ctrl+Shift+0 was the case that found it, and it is NOT fixed here - it turned
out to be a second, unrelated fault living in the same keystroke. Windows
consumes that one combination before any program sees it: measured, Shift+0
arrives as Key_ParenRight and Ctrl+Shift+1 as Key_Exclam while Ctrl+Shift+0
in the same run produces no key event at all. Nothing in this module can name
a key that never arrives, so the default moved to Ctrl+Shift+Minus instead -
see config.DEFAULT_HOTKEYS. What IS fixed here is every other shifted key,
Ctrl+Shift+Minus among them.

Two answers to it, and the order matters:

  * nativeVirtualKey, on Windows. Measured, it is right in every case - and
    it is the SAME NUMBER windows.virtual_key registers, so what the field
    captures and what the OS is later asked for cannot disagree. That table
    is a pure function, so inverting it is checkable from Linux.
  * the shifted row, everywhere else. Built from the two rows a Shift toggles
    between rather than typed out as pairs, and resolved through keyspec's
    own parser, so nothing here is a second copy of the vocabulary.

The native code wins where there is one, because the shifted row is US and a
German player pressing Ctrl+Shift+8 gets "(" - which that table would read as
the 9 key. Recording a DIFFERENT key from the one pressed is worse than
refusing the press, so the exact answer is preferred wherever it exists.

WHAT IT REFUSES, and refusing is the point rather than a shortfall:

  * A press with no key yet - only Ctrl, only Shift. The field shows the
    modifiers gathering and waits, which turns keyspec's must-have-a-modifier
    rule from an error you read afterwards into a state you watch assemble.
  * A key outside keyspec.KEYS. There is no numpad, no media key, no
    PrintScreen or CapsLock in the vocabulary, because neither backend has a
    table entry for them. Saying so is better than a keypress that silently
    does nothing.

Both come back as None, and the caller says which happened - it knows whether
a key was pressed at all and this does not.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from . import keyspec

# Qt names its modifiers after the Mac; keyspec names them after the keycaps
# a Windows or Linux player is looking at. MetaModifier is the Windows key.
# Built lazily because loom/hotkeys must import on a machine with no Qt - the
# backends are already careful about this and the package's own tests import
# every module on every platform.
_MODIFIER_NAMES = (("ControlModifier", "Ctrl"), ("AltModifier", "Alt"),
                   ("ShiftModifier", "Shift"), ("MetaModifier", "Win"))

# The keys whose Qt spelling is not simply "Key_" plus the keyspec name.
# Everything else - the letters, the digits, the F-keys, Space, Tab, Home,
# the arrows - matches by name and needs no entry here.
_ODD_SPELLINGS = {
    "Enter": "Key_Return",          # Key_Enter is the numpad one
    "Backtick": "Key_QuoteLeft",
    "Quote": "Key_Apostrophe",
    "LeftBracket": "Key_BracketLeft",
    "RightBracket": "Key_BracketRight",
    "PageUp": "Key_PageUp",
    "PageDown": "Key_PageDown",
    "Minus": "Key_Minus",
    "Equal": "Key_Equal",
    "Comma": "Key_Comma",
    "Period": "Key_Period",
    "Slash": "Key_Slash",
    "Backslash": "Key_Backslash",
    "Semicolon": "Key_Semicolon",
}


# The two rows a Shift toggles between on a US keyboard, as rows rather than
# as pairs - so this is the layout itself rather than a transcription of it,
# and the pairing cannot slip by one the way a hand-written table can.
#
# Qt's key code for an ASCII printable IS its codepoint (Key_ParenLeft is
# 0x28 is ord("(")), which is what lets these be characters here and still
# match a Qt key event. Measured, not assumed.
_UNSHIFTED_ROW = r"`1234567890-=[]\;',./"
_SHIFTED_ROW = '~!@#$%^&*()_+{}|:"<>?'


def shifted_table():
    """{Qt key code: keyspec name} for the keys Shift renames.

    Each shifted character is resolved back to a vocabulary name through
    keyspec's OWN parser rather than a table written out here - it already
    knows that "-" is Minus and "`" is Backtick, and a second copy of that
    knowledge is a second thing to keep in step.
    """
    from PyQt6.QtCore import Qt

    table = {}
    for shifted, plain in zip(_SHIFTED_ROW, _UNSHIFTED_ROW):
        # A modifier only so parse() will accept it; the key is what I want.
        table[ord(shifted)] = keyspec.parse(f"Ctrl+{plain}").key
    # Not a character, so it cannot come out of the rows above: Shift turns
    # Tab into Backtab, and Tab is in the vocabulary.
    table[Qt.Key.Key_Backtab.value] = "Tab"
    return table


def native_names():
    """{native key code: keyspec name} for the platform actually running.

    Windows only, and empty elsewhere on purpose. A Windows virtual key is a
    POSITION - VK_9 is the 9 key whatever that key prints - so it survives a
    layout the shifted row above does not. X11 has no equivalent to offer
    here: Qt sets nativeVirtualKey to the keysym, which is shifted in exactly
    the same way the Qt key is, so there is nothing to correct with.

    Built by inverting windows.virtual_key, which is a pure function with no
    OS call in it, so this table is checkable from Linux.
    """
    import sys

    if sys.platform != "win32":
        return {}
    from . import windows

    return {windows.virtual_key(name): name for name in keyspec.KEYS}


def _qt_name(key):
    """What Qt calls a key keyspec calls `key`."""
    if key in _ODD_SPELLINGS:
        return _ODD_SPELLINGS[key]
    return f"Key_{key}"


def key_table():
    """{Qt key code: keyspec name} for every key the grammar knows.

    Derived from keyspec.KEYS rather than typed out, so a key added to the
    vocabulary is either mapped here or fails the test that walks the same
    list - the same discipline the two OS backends already follow, where a
    key with no table entry is "a binding a player can save in the launcher
    and which then silently never fires".
    """
    from PyQt6.QtCore import Qt

    table = {}
    for name in keyspec.KEYS:
        code = getattr(Qt.Key, _qt_name(name), None)
        if code is not None:
            table[code.value] = name
    return table


def modifiers_of(modifiers):
    """The keyspec modifier names carried by a Qt modifier flag set.

    On macOS the names are SWAPPED back. Qt exchanges Control and Meta
    there - a pressed ⌘ arrives as ControlModifier and a pressed ⌃ as
    MetaModifier, so that cross-platform Ctrl shortcuts land on the key Mac
    users reach for. keyspec means the physical keycap ("Ctrl" is the key
    labelled control, "Win" is the command-ish one), and the macos backend
    maps those to Carbon's controlKey and cmdKey - so without undoing Qt's
    swap here, the capture field would record the OPPOSITE modifier from
    the key the player actually pressed, and the registered chord would
    never match their fingers.
    """
    import sys

    from PyQt6.QtCore import Qt

    names = {name for attribute, name in _MODIFIER_NAMES
             if modifiers & getattr(Qt.KeyboardModifier, attribute)}
    if sys.platform == "darwin":
        swapped = {"Ctrl": "Win", "Win": "Ctrl"}
        names = {swapped.get(name, name) for name in names}
    return names


def is_modifier_only(key):
    """Is this press just a modifier being held, with no key yet?

    Qt delivers a key event for Ctrl itself, not only for the letter that
    follows it. A capture field wants to show "Ctrl+Shift+..." and keep
    waiting rather than treat that as an answer.
    """
    from PyQt6.QtCore import Qt

    return key in (Qt.Key.Key_Control.value, Qt.Key.Key_Alt.value,
                   Qt.Key.Key_Shift.value, Qt.Key.Key_Meta.value,
                   Qt.Key.Key_AltGr.value)


def name_for(key, native=None):
    """Which key in the vocabulary was struck, or None.

    `native` is the event's nativeVirtualKey, and passing it is what makes
    Shift+digit work on Windows regardless of layout. Omitting it falls back
    to the US shifted row, which is right on the layout Loom is developed and
    tested on and is better than declining the press outright.
    """
    name = key_table().get(key)
    if name is not None:
        return name
    # Only now, once Qt's own answer has failed: this key is one Shift
    # renamed. Nothing in the vocabulary collides with a shifted symbol, so
    # asking in this order cannot mask a good answer with a corrected one.
    if native is not None:
        name = native_names().get(native)
        if name is not None:
            return name
    return shifted_table().get(key)


def binding_for(key, modifiers, native=None):
    """The canonical binding for one key press, or None if it is not one.

    None means "not a binding, keep waiting or say why" - either a modifier
    being held on its own, or a key the grammar has no name for. The caller
    can tell the two apart with is_modifier_only.

    A binding is returned even when it has no modifier at all, because
    refusing that is keyspec's job and its message is the one worth showing:
    it explains that the game would lose the key. This function's contract is
    translation, not policy.
    """
    if is_modifier_only(key):
        return None
    name = name_for(key, native)
    if name is None:
        return None
    spec = keyspec.KeySpec(modifiers_of(modifiers), name)
    return keyspec.text(spec)


def _modifier_named(key):
    """The modifier name a modifier KEY stands for, or None.

    Qt's macOS Control/Meta exchange applies to these key codes exactly as
    it does to the flag set - a pressed ⌘ arrives as Key_Control - so the
    same swap modifiers_of makes is made here, or the building-a-chord
    display would name the wrong key under the player's finger.
    """
    import sys

    from PyQt6.QtCore import Qt

    control, meta = "Ctrl", "Win"
    if sys.platform == "darwin":
        control, meta = meta, control
    return {Qt.Key.Key_Control.value: control, Qt.Key.Key_Alt.value: "Alt",
            Qt.Key.Key_AltGr.value: "Alt", Qt.Key.Key_Shift.value: "Shift",
            Qt.Key.Key_Meta.value: meta}.get(key)


def describe(modifiers, key=None):
    """What to show while the player is still holding modifiers down.

    "Ctrl+Shift+..." rather than an empty field, so the combination is
    visibly being built rather than the press being ignored.

    `key` is the modifier key of THIS press, and including it is not a
    nicety. Qt reports the modifier state as it was BEFORE the key that
    caused the event: press Ctrl then Shift and the second event arrives
    with modifiers=Ctrl and key=Key_Shift. Reading only the flags would show
    "Ctrl+..." at the moment the player pressed Shift, so the display would
    trail their fingers by one key and look broken exactly while they were
    watching it.
    """
    held = modifiers_of(modifiers)
    named = _modifier_named(key) if key is not None else None
    if named:
        held = held | {named}
    if not held:
        return ""
    ordered = [name for name in keyspec.MODIFIERS if name in held]
    return "+".join(ordered) + "+..."
