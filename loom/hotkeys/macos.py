"""
Loom — global hotkeys, the macOS backend.

Carbon RegisterEventHotKey, and deliberately nothing more capable than that.

The OS does the matching, exactly as Windows does for RegisterHotKey: I hand
macOS a key code, a modifier mask and an id, and get back "id 2 fired".
There is no key stream, and no way for this module to learn about a key it
did not ask for. The alternative - a Quartz event tap - would see every
keystroke on the machine and needs the Accessibility permission to do it,
which is both the wrong shape for the privacy contract and a worse install
story. RegisterEventHotKey needs no permission at all.

Carbon is old but this corner of it is not going anywhere: it is the API
Apple's own docs still point at for global hotkeys, every mainstream hotkey
library sits on it, and the whole framework is reachable from ctypes with
nothing to install - the symbols were checked on this machine before this
module was written. pyobjc wraps no Carbon framework, so ctypes is not a
workaround here, it is the only dependency-free route.

Delivery needs no thread and no native event filter: Qt's Cocoa event
dispatcher runs the main run loop, and the main run loop is what dispatches
Carbon events - so the handler installed here fires on the GUI thread and
`on_action` is called directly. That keeps the cross-platform contract
(on_action always runs on the GUI thread) with less machinery than either
other backend.

Only kEventHotKeyPressed is registered. Verified live on this machine:
holding a combination down fires the pressed event ONCE - macOS does not
auto-repeat hotkey events the way an unguarded Windows registration does -
so there is no equivalent of MOD_NOREPEAT to ask for and nothing to
suppress.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ctypes

from .errors import HotkeyError
from . import keyspec

# Note what is NOT done at module scope: loading the Carbon framework. Tests
# import every backend on every platform to check the contract, so this
# module has to stay loadable on Linux and Windows - the CDLL load lives
# inside _carbon(). The ctypes structs and tables below are pure data and
# import anywhere.

CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"

# Carbon's modifier masks. These are the Carbon-era values, not the Cocoa
# NSEvent ones - RegisterEventHotKey predates NSEvent and kept its own.
CMD_KEY = 0x0100
SHIFT_KEY = 0x0200
OPTION_KEY = 0x0800
CONTROL_KEY = 0x1000

# keyspec names mean the PHYSICAL key: "Ctrl" is the key labelled control,
# and "Win" is the platform's command-ish modifier, which on a Mac is the
# command (⌘) key. keyspec already spells "cmd" and "command" as aliases
# for Win, so a binding typed either way lands here correctly.
MODIFIER_FLAGS = {
    "Ctrl": CONTROL_KEY,
    "Alt": OPTION_KEY,
    "Shift": SHIFT_KEY,
    "Win": CMD_KEY,
}

# What macOS already knows another program is using.
EVENT_HOT_KEY_EXISTS = -9878

# Handler results. Returning "not handled" for an id that is not mine lets
# the event continue to whoever it does belong to.
NO_ERR = 0
EVENT_NOT_HANDLED = -9874

# kEventHotKeyPressed. (Released is 6, unused - see the module docstring.)
HOT_KEY_PRESSED = 5


def fourcc(text):
    """A four-character Carbon code as the UInt32 the framework wants.

    Carbon identifies event classes and parameter types by packing four
    ASCII bytes big-endian into an integer - 'keyb' IS the constant
    kEventClassKeyboard, there is no separate value.
    """
    return int.from_bytes(text.encode("ascii"), "big")


SIGNATURE = fourcc("LOOM")            # marks a registration as this program's
KEYBOARD_CLASS = fourcc("keyb")       # kEventClassKeyboard
DIRECT_OBJECT = fourcc("----")        # kEventParamDirectObject
HOT_KEY_ID_TYPE = fourcc("hkid")      # typeEventHotKeyID

# Carbon virtual key codes are POSITIONS on the ANSI layout, like Windows
# virtual keys - kVK_ANSI_A is the key that prints A on a US keyboard,
# whatever the active layout prints there. Unlike Windows they are not
# contiguous for letters, digits or function keys, so all three are listed
# rather than computed. Apple keyboards stop at F20 and have no Insert key,
# so F21-F24 and Insert have no code to give; virtual_key refuses them and
# the refusal lands in the listener's failures list, named per binding.
KEY_CODES = {
    "A": 0x00, "B": 0x0B, "C": 0x08, "D": 0x02, "E": 0x0E, "F": 0x03,
    "G": 0x05, "H": 0x04, "I": 0x22, "J": 0x26, "K": 0x28, "L": 0x25,
    "M": 0x2E, "N": 0x2D, "O": 0x1F, "P": 0x23, "Q": 0x0C, "R": 0x0F,
    "S": 0x01, "T": 0x11, "U": 0x20, "V": 0x09, "W": 0x0D, "X": 0x07,
    "Y": 0x10, "Z": 0x06,
    "0": 0x1D, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "5": 0x17,
    "6": 0x16, "7": 0x1A, "8": 0x1C, "9": 0x19,
    "F1": 0x7A, "F2": 0x78, "F3": 0x63, "F4": 0x76, "F5": 0x60,
    "F6": 0x61, "F7": 0x62, "F8": 0x64, "F9": 0x65, "F10": 0x6D,
    "F11": 0x67, "F12": 0x6F, "F13": 0x69, "F14": 0x6B, "F15": 0x71,
    "F16": 0x6A, "F17": 0x40, "F18": 0x4F, "F19": 0x50, "F20": 0x5A,
    # Named keys, keycap-truth like the rest of keyspec: "Backspace" is the
    # big delete key (kVK_Delete - Apple names it after the other platform's
    # key, confusingly) and "Delete" is forward delete (kVK_ForwardDelete).
    "Space": 0x31, "Tab": 0x30, "Enter": 0x24, "Escape": 0x35,
    "Backspace": 0x33, "Delete": 0x75,
    "Home": 0x73, "End": 0x77, "PageUp": 0x74, "PageDown": 0x79,
    "Left": 0x7B, "Right": 0x7C, "Down": 0x7D, "Up": 0x7E,
    "Minus": 0x1B, "Equal": 0x18, "Comma": 0x2B, "Period": 0x2F,
    "Slash": 0x2C, "Backslash": 0x2A, "Semicolon": 0x29, "Quote": 0x27,
    "LeftBracket": 0x21, "RightBracket": 0x1E, "Backtick": 0x32,
}


def virtual_key(key):
    """The Carbon virtual key code for one of keyspec's key names.

    Pure, and separate from anything that touches the OS, so the whole table
    is checkable from Linux and Windows.
    """
    code = KEY_CODES.get(key)
    if code is None:
        raise HotkeyError(f"no macOS virtual key for {key!r}")
    return code


def modifier_flags(spec):
    """The Carbon modifier bitmask for a KeySpec."""
    flags = 0
    for modifier in spec.ordered_modifiers():
        flags |= MODIFIER_FLAGS[modifier]
    return flags


class EventHotKeyID(ctypes.Structure):
    """Passed BY VALUE to RegisterEventHotKey and read back in the handler."""
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("event_class", ctypes.c_uint32), ("kind", ctypes.c_uint32)]


# OSStatus handler(EventHandlerCallRef, EventRef, void *userData). The
# callback object built from this must be kept referenced for as long as the
# handler is installed - see Listener.
HANDLER_TYPE = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_void_p)

_carbon_library = None


def _carbon():
    """The Carbon framework, loaded once, with every signature declared.

    ctypes assumes C int for anything undeclared, which truncates pointers -
    the same lesson loom/passthrough.py records for the Windows side - so
    every function used gets explicit argtypes and restype before anything
    calls it.
    """
    global _carbon_library
    if _carbon_library is not None:
        return _carbon_library

    carbon = ctypes.CDLL(CARBON_PATH)
    carbon.GetEventDispatcherTarget.restype = ctypes.c_void_p
    carbon.GetEventDispatcherTarget.argtypes = []
    carbon.RegisterEventHotKey.restype = ctypes.c_int32
    carbon.RegisterEventHotKey.argtypes = [
        ctypes.c_uint32, ctypes.c_uint32, EventHotKeyID, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
    carbon.UnregisterEventHotKey.restype = ctypes.c_int32
    carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
    carbon.InstallEventHandler.restype = ctypes.c_int32
    carbon.InstallEventHandler.argtypes = [
        ctypes.c_void_p, HANDLER_TYPE, ctypes.c_ulong,
        ctypes.POINTER(EventTypeSpec), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p)]
    carbon.RemoveEventHandler.restype = ctypes.c_int32
    carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
    carbon.GetEventParameter.restype = ctypes.c_int32
    carbon.GetEventParameter.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p]
    _carbon_library = carbon
    return carbon


class Listener:
    """The registrations made for one run, and how to give them back.

    `failures` is a list of (action, binding, reason) for combinations that
    could not be registered. It is data rather than an exception because one
    unavailable combination must not stop the others from working - the
    caller prints it and carries on.

    The private fields are lifetime, not bookkeeping: `_callback` is the
    ctypes function object Carbon holds a bare pointer to, and `_hotkey_refs`
    and `_handler_ref` are what unregistration needs. If the callback were
    garbage collected while the handler is installed, the next keypress
    would call freed memory.
    """

    def __init__(self):
        self.actions = {}          # hotkey id -> action name
        self.failures = []
        self._hotkey_refs = {}     # hotkey id -> EventHotKeyRef
        self._handler_ref = None
        self._callback = None
        self._next_id = 1

    def stop(self):
        """Unregister everything and remove the handler.

        Giving the combinations back matters: while Loom holds them, no other
        program - including the game - can see those keys.
        """
        carbon = _carbon()
        for hotkey_id, ref in list(self._hotkey_refs.items()):
            try:
                carbon.UnregisterEventHotKey(ref)
            except Exception:
                pass
        self._hotkey_refs.clear()
        self.actions.clear()

        if self._handler_ref is not None:
            try:
                carbon.RemoveEventHandler(self._handler_ref)
            except Exception:
                pass
            self._handler_ref = None
        self._callback = None


def _make_callback(listener, on_action):
    """Build the Carbon event handler that turns a hotkey event into an action.

    Only the EventHotKeyID is read out of the event - the id THIS program
    registered, stamped with its own signature - so nothing here learns
    anything else about the keyboard.
    """
    carbon = _carbon()

    def handle(_call_ref, event, _user_data):
        hotkey = EventHotKeyID()
        status = carbon.GetEventParameter(
            event, DIRECT_OBJECT, HOT_KEY_ID_TYPE, None,
            ctypes.sizeof(hotkey), None, ctypes.byref(hotkey))
        if status != NO_ERR or hotkey.signature != SIGNATURE:
            return EVENT_NOT_HANDLED
        action = listener.actions.get(hotkey.id)
        if action is None:
            return EVENT_NOT_HANDLED
        try:
            on_action(action)
        except Exception as problem:
            # A handler that raises must not take the event loop with it.
            print(f"hotkey {action!r} failed: "
                  f"{type(problem).__name__}: {problem}")
        return NO_ERR

    return HANDLER_TYPE(handle)


def listen(bindings, on_action):
    """Register every binding and start delivering actions. Returns a handle.

    bindings is {action: "Ctrl+Shift+Q"}. A disabled or unparseable binding is
    skipped; a combination another program already owns is recorded in
    handle.failures. Only a total inability to register anything raises.
    """
    from PyQt6.QtCore import QCoreApplication

    # The main run loop is the delivery channel, and Qt's event loop is what
    # runs it - same dependency the Windows filter has on the message pump.
    application = QCoreApplication.instance()
    if application is None:
        raise HotkeyError(
            "hotkeys need a running QApplication to deliver their events")

    carbon = _carbon()
    target = carbon.GetEventDispatcherTarget()
    listener = Listener()

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
        ref = ctypes.c_void_p()
        status = carbon.RegisterEventHotKey(
            key, flags, EventHotKeyID(SIGNATURE, hotkey_id), target, 0,
            ctypes.byref(ref))
        if status == NO_ERR:
            listener.actions[hotkey_id] = action
            listener._hotkey_refs[hotkey_id] = ref
            continue

        if status == EVENT_HOT_KEY_EXISTS:
            reason = ("another program is already using it, so macOS will "
                      "not share it")
        else:
            reason = f"macOS refused it (error {status})"
        listener.failures.append((action, binding, reason))

    if listener.actions:
        listener._callback = _make_callback(listener, on_action)
        handler_ref = ctypes.c_void_p()
        spec = EventTypeSpec(KEYBOARD_CLASS, HOT_KEY_PRESSED)
        status = carbon.InstallEventHandler(
            target, listener._callback, 1, ctypes.byref(spec), None,
            ctypes.byref(handler_ref))
        if status != NO_ERR:
            # Registered keys with no way to hear them fire are worse than
            # none at all: give them back and say what happened.
            listener.stop()
            raise HotkeyError(
                f"macOS refused the hotkey event handler (error {status})")
        listener._handler_ref = handler_ref

    return listener


def stop(handle):
    """Stop listening and hand every combination back to the system."""
    if handle is not None:
        handle.stop()
