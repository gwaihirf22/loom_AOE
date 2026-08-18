"""
Loom — global hotkeys, the X11 backend.

XGrabKey on the root window, which is the X equivalent of RegisterHotKey: the
server routes that one combination to this client whoever has focus, and
tells us nothing else. Like the Windows backend, this cannot learn about a key
it did not ask for - it is not reading a key stream, it is being handed the
few combinations it grabbed.

This is the same X server the game and the overlay live on (XWayland), which
is why it works at all under Wayland - see loom/capture/x11.py for the same
reasoning about pixels.

UNVERIFIED. Written on Windows, where the X server it needs does not exist.
The test suite proves this module imports and that its keysym table is
right; it does NOT prove a grab actually fires, because that needs a running
X server and a keyboard. Check it against a real game before trusting it, and
treat the first run as a measurement rather than a formality.

Two X-specific traps are handled here because both produce a hotkey that
simply never fires, with no error anywhere:

  * A grab is for an EXACT modifier mask, so Ctrl+Shift+Q with Num Lock on is
    a different mask and does not match. Every combination of the "don't
    care" lock modifiers has to be grabbed separately.
  * Another client may already hold the grab. XGrabKey does not return an
    error synchronously - the server reports BadAccess later - so the failure
    is collected through an error handler rather than a return value.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import threading

from .errors import HotkeyError
from . import keyspec

# Everything X-specific is imported inside a function: tests import every
# backend on every platform to check the contract, and python-xlib may not be
# installed off Linux.

# keyspec's key names to X keysym names. Letters and digits are their own
# lowercase names, and F1-F24 are already right, so only the rest is listed.
KEYSYM_NAMES = {
    "Space": "space", "Tab": "Tab", "Enter": "Return", "Escape": "Escape",
    "Backspace": "BackSpace", "Insert": "Insert", "Delete": "Delete",
    "Home": "Home", "End": "End", "PageUp": "Prior", "PageDown": "Next",
    "Left": "Left", "Up": "Up", "Right": "Right", "Down": "Down",
    "Minus": "minus", "Equal": "equal", "Comma": "comma", "Period": "period",
    "Slash": "slash", "Backslash": "backslash", "Semicolon": "semicolon",
    "Quote": "apostrophe", "LeftBracket": "bracketleft",
    "RightBracket": "bracketright", "Backtick": "grave",
}


def keysym_name(key):
    """The X keysym name for one of keyspec's key names.

    Pure, so the whole table is checkable from a machine with no X server.
    """
    if len(key) == 1 and "A" <= key <= "Z":
        return key.lower()
    if len(key) == 1 and "0" <= key <= "9":
        return key
    if key.startswith("F") and key[1:].isdigit():
        number = int(key[1:])
        if 1 <= number <= 24:
            return key
    name = KEYSYM_NAMES.get(key)
    if name is None:
        raise HotkeyError(f"no X keysym for {key!r}")
    return name


def modifier_mask(spec, X):
    """The X modifier mask for a KeySpec.

    Mod1 is Alt and Mod4 is Super on every desktop this is likely to meet.
    Strictly those are conventions rather than guarantees - X maps modifiers
    per keyboard - but reading the real mapping needs a server, and getting
    it wrong costs a hotkey rather than anything worse.
    """
    masks = {"Ctrl": X.ControlMask, "Shift": X.ShiftMask,
             "Alt": X.Mod1Mask, "Win": X.Mod4Mask}
    mask = 0
    for modifier in spec.ordered_modifiers():
        mask |= masks[modifier]
    return mask


def lock_variants(X):
    """Every combination of the lock modifiers a grab must ignore.

    Num Lock, Caps Lock and Scroll Lock are part of the modifier mask, so a
    grab that does not account for them stops working the moment somebody
    presses Num Lock - silently, which is the worst way for a hotkey to fail.
    """
    ignored = (0, X.LockMask, X.Mod2Mask, X.Mod5Mask)
    variants = set()
    for first in ignored:
        for second in ignored:
            for third in ignored:
                variants.add(first | second | third)
    return sorted(variants)


class Listener:
    """The grabs made for one run, and the thread reading their events."""

    def __init__(self):
        self.actions = {}          # (keycode, base mask) -> action name
        self.failures = []
        self._display = None
        self._root = None
        self._grabs = []           # (keycode, full mask) actually grabbed
        self._thread = None
        self._running = False
        self._bridge = None

    def stop(self):
        """Ungrab everything and stop reading.

        Handing the combinations back matters as much as it does on Windows:
        while the grab is held, no other client sees those keys.
        """
        self._running = False
        if self._display is not None:
            try:
                for keycode, mask in self._grabs:
                    self._root.ungrab_key(keycode, mask)
                self._display.sync()
            except Exception:
                pass
        self._grabs = []
        self.actions.clear()

        # The reader thread wakes on its own timeout and sees _running False.
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                pass
            self._display = None
        self._bridge = None


def _make_bridge(on_action):
    """A QObject whose signal hops from the reader thread to the GUI thread.

    The X events arrive on a background thread, and Qt objects may only be
    touched from the thread that owns them. A queued signal is the supported
    way across, and doing it here rather than in the caller keeps the
    contract the same on every platform: on_action always runs on the GUI
    thread.
    """
    from PyQt6.QtCore import QObject, Qt, pyqtSignal

    class Bridge(QObject):
        fired = pyqtSignal(str)

        def __init__(self):
            super().__init__()
            self.fired.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
            self._on_action = on_action

        def _deliver(self, action):
            try:
                self._on_action(action)
            except Exception as problem:
                print(f"hotkey {action!r} failed: "
                      f"{type(problem).__name__}: {problem}")

    return Bridge()


def listen(bindings, on_action):
    """Grab every binding and start delivering actions. Returns a handle."""
    try:
        from Xlib import X, XK, error
        from Xlib import display as xdisplay
    except ImportError as missing:
        raise HotkeyError(
            f"hotkeys on Linux need python-xlib: {missing}") from missing

    try:
        connection = xdisplay.Display()
    except Exception as problem:
        raise HotkeyError(
            f"hotkeys need an X server (XWayland counts): {problem}") from problem

    listener = Listener()
    listener._display = connection
    listener._root = connection.screen().root
    variants = lock_variants(X)

    # XGrabKey reports a clash asynchronously, so failures are collected here
    # and matched up after a sync rather than read from a return value.
    refused = []
    connection.set_error_handler(
        lambda err, request: refused.append(err))

    for action in sorted(bindings):
        binding = bindings[action]
        if keyspec.is_disabled(binding):
            continue
        try:
            spec = keyspec.parse(binding)
            keysym = XK.string_to_keysym(keysym_name(spec.key))
            keycode = connection.keysym_to_keycode(keysym)
            if not keycode:
                raise HotkeyError(
                    f"this keyboard has no key for {spec.key!r}")
            mask = modifier_mask(spec, X)
        except (ValueError, HotkeyError) as reason:
            listener.failures.append((action, binding, str(reason)))
            continue

        before = len(refused)
        for extra in variants:
            listener._root.grab_key(keycode, mask | extra, 1,
                                    X.GrabModeAsync, X.GrabModeAsync)
            listener._grabs.append((keycode, mask | extra))
        connection.sync()

        if len(refused) > before:
            listener.failures.append(
                (action, binding,
                 "another program is already using it, so X will not share it"))
            continue
        listener.actions[(keycode, mask)] = action

    connection.set_error_handler(None)

    if not listener.actions:
        return listener

    listener._bridge = _make_bridge(on_action)
    listener._running = True

    def read_events():
        """Drain key presses until stopped. Daemon: never holds an exit up."""
        lock_bits = variants[-1]
        while listener._running:
            try:
                if connection.pending_events() == 0:
                    # A short sleep rather than a blocking read, so stop() is
                    # noticed promptly without needing to wake the connection
                    # by other means.
                    threading.Event().wait(0.02)
                    continue
                event = connection.next_event()
            except Exception:
                return
            if getattr(event, "type", None) != X.KeyPress:
                continue
            # Strip the lock bits back off so the mask matches what was
            # registered, whatever the state of Num Lock.
            base = event.state & ~lock_bits
            action = listener.actions.get((event.detail, base))
            if action is not None and listener._bridge is not None:
                listener._bridge.fired.emit(action)

    listener._thread = threading.Thread(
        target=read_events, name="loom-hotkeys", daemon=True)
    listener._thread.start()
    return listener


def stop(handle):
    """Stop listening and hand every combination back to the X server."""
    if handle is not None:
        handle.stop()
