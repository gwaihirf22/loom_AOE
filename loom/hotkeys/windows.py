"""
Loom — global hotkeys, the Windows backend.

RegisterHotKey, and deliberately nothing more capable than that.

Windows itself does the matching: Loom hands the OS a set of combinations and
an id for each, and gets back "id 2 fired". There is no key stream, no hook,
and no way for this module to learn about a key it did not ask for. That is
the same shape of guarantee tools/apm_counter.py makes about counting - built
in, not promised - and it is why this is RegisterHotKey rather than
SetWindowsHookEx, which would be easier, would see every keystroke on the
machine, and is the mechanism keyloggers use.

MOD_NOREPEAT is on every registration. Without it, holding the key down
repeats the hotkey at the keyboard's repeat rate, so leaning on the "next
step" combination would race through the whole build order.

WM_HOTKEY is delivered to the message queue of the thread that registered,
which is the GUI thread; Qt's event dispatcher peeks it and offers it to
native event filters before dispatching, so a QAbstractNativeEventFilter on
the application sees it. Registration must therefore happen on the same
thread that runs the Qt event loop.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ctypes

from .errors import HotkeyError
from . import keyspec

# Note what is NOT imported at module scope: ctypes.wintypes, which raises
# ValueError off Windows, and PyQt6. Tests import every backend on every
# platform to check the contract, so this module has to stay loadable on
# Linux and macOS - everything Windows-only lives inside a function.

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
# Windows 7+. Stops the key repeating while held - see the module docstring.
MOD_NOREPEAT = 0x4000

MODIFIER_FLAGS = {
    "Ctrl": MOD_CONTROL,
    "Alt": MOD_ALT,
    "Shift": MOD_SHIFT,
    "Win": MOD_WIN,
}

# What Windows already knows another program is using.
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

# Virtual key codes for the named keys. Letters and digits are their ASCII
# values and F1-F24 are contiguous from 0x70, so those are computed rather
# than listed.
NAMED_KEYS = {
    "Space": 0x20, "Tab": 0x09, "Enter": 0x0D, "Escape": 0x1B,
    "Backspace": 0x08, "Insert": 0x2D, "Delete": 0x2E,
    "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
    "Minus": 0xBD, "Equal": 0xBB, "Comma": 0xBC, "Period": 0xBE,
    "Slash": 0xBF, "Backslash": 0xDC, "Semicolon": 0xBA, "Quote": 0xDE,
    "LeftBracket": 0xDB, "RightBracket": 0xDD, "Backtick": 0xC0,
}


def virtual_key(key):
    """The Windows virtual key code for one of keyspec's key names.

    Pure, and separate from anything that touches the OS, so the whole table
    is checkable from Linux.
    """
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        return ord(key)
    if key.startswith("F") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return 0x70 + number - 1
    code = NAMED_KEYS.get(key)
    if code is None:
        raise HotkeyError(f"no Windows virtual key for {key!r}")
    return code


def modifier_flags(spec):
    """The MOD_* bitmask for a KeySpec, with MOD_NOREPEAT always set."""
    flags = MOD_NOREPEAT
    for modifier in spec.ordered_modifiers():
        flags |= MODIFIER_FLAGS[modifier]
    return flags


class Listener:
    """The registrations made for one run, and how to give them back.

    `failures` is a list of (action, binding, reason) for combinations that
    could not be registered. It is data rather than an exception because one
    unavailable combination must not stop the others from working - the
    caller prints it and carries on.
    """

    def __init__(self):
        self.actions = {}          # hotkey id -> action name
        self.failures = []
        self._filter = None
        self._next_id = 1

    def stop(self):
        """Unregister everything and stop filtering.

        Giving the combinations back matters: while Loom holds them, no other
        program - including the game - can see those keys.
        """
        user32 = ctypes.windll.user32
        for hotkey_id in list(self.actions):
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        self.actions.clear()

        if self._filter is not None:
            try:
                from PyQt6.QtCore import QCoreApplication
                application = QCoreApplication.instance()
                if application is not None:
                    application.removeNativeEventFilter(self._filter)
            except Exception:
                pass
            self._filter = None


def _make_filter(listener, on_action):
    """Build the native event filter that turns WM_HOTKEY into an action.

    Defined in a function because QAbstractNativeEventFilter cannot be
    subclassed at module scope without importing PyQt6, and this module must
    stay importable on Linux.
    """
    from PyQt6.QtCore import QAbstractNativeEventFilter

    class HotkeyFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            # Qt hands over a void* to the MSG. Only the message id and
            # wParam are read - wParam is the id THIS program registered, so
            # nothing here learns anything about the keyboard.
            try:
                if event_type != b"windows_generic_MSG":
                    return False, 0
                msg = ctypes.c_void_p(int(message))
                message_id = ctypes.c_uint.from_address(
                    msg.value + _MESSAGE_OFFSET).value
                if message_id != WM_HOTKEY:
                    return False, 0
                hotkey_id = ctypes.c_size_t.from_address(
                    msg.value + _WPARAM_OFFSET).value
            except Exception:
                return False, 0

            action = listener.actions.get(hotkey_id)
            if action is None:
                return False, 0
            try:
                on_action(action)
            except Exception as problem:
                # A handler that raises must not take the event loop with it.
                print(f"hotkey {action!r} failed: "
                      f"{type(problem).__name__}: {problem}")
            # Not consumed: returning False leaves Qt's own handling intact,
            # and nothing else is listening for WM_HOTKEY anyway.
            return False, 0

    return HotkeyFilter()


# Offsets into the MSG struct on 64-bit Windows:
#   HWND   hwnd     8 bytes
#   UINT   message  4 bytes, then 4 bytes of padding before the pointer-sized
#                   wParam is aligned
#   WPARAM wParam   8 bytes
# Spelled out rather than built with ctypes.Structure so that this module does
# not need wintypes at import time.
_MESSAGE_OFFSET = ctypes.sizeof(ctypes.c_void_p)
_WPARAM_OFFSET = _MESSAGE_OFFSET + ctypes.sizeof(ctypes.c_void_p)


def listen(bindings, on_action):
    """Register every binding and start delivering actions. Returns a handle.

    bindings is {action: "Ctrl+Shift+Q"}. A disabled or unparseable binding is
    skipped; a combination another program already owns is recorded in
    handle.failures. Only a total inability to register anything raises.
    """
    from PyQt6.QtCore import QCoreApplication

    application = QCoreApplication.instance()
    if application is None:
        raise HotkeyError(
            "hotkeys need a running QApplication to deliver their messages")

    listener = Listener()
    user32 = ctypes.windll.user32

    for action in sorted(bindings):
        binding = bindings[action]
        if keyspec.is_disabled(binding):
            continue
        try:
            spec = keyspec.parse(binding)
            flags = modifier_flags(spec)
            key = virtual_key(spec.key)
        except (ValueError, HotkeyError) as reason:
            listener.failures.append((action, binding, str(reason)))
            continue

        hotkey_id = listener._next_id
        listener._next_id += 1
        if user32.RegisterHotKey(None, hotkey_id, flags, key):
            listener.actions[hotkey_id] = action
            continue

        code = ctypes.get_last_error() or ctypes.GetLastError()
        if code == ERROR_HOTKEY_ALREADY_REGISTERED:
            reason = ("another program is already using it, so Windows will "
                      "not share it")
        else:
            reason = f"Windows refused it (error {code})"
        listener.failures.append((action, binding, reason))

    if listener.actions:
        listener._filter = _make_filter(listener, on_action)
        application.installNativeEventFilter(listener._filter)

    return listener


def stop(handle):
    """Stop listening and hand every combination back to the system."""
    if handle is not None:
        handle.stop()
