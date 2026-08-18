"""
Loom — the APM counter on Windows: keystrokes and clicks per minute, counts only.

The Windows half of what tools/apm_counter.py does on Linux, and it makes the
same promise in the same way.

THE PRIVACY CONTRACT, stated plainly: this counts events and never knows
which key was pressed. As on Linux that is not a promise of good behaviour,
it is how the code is built. Three things make it structural rather than
aspirational, and all three are checkable by reading this file:

  1. It uses RAW INPUT, not SetWindowsHookEx. The hook API is easier and is
     the mechanism keyloggers use - it would see every keystroke on the
     machine, and it is what antivirus software looks for. Raw Input hands
     over a fixed record describing one device event.
  2. Of that record it reads exactly three things: which DEVICE TYPE it came
     from (keyboard or mouse), whether a keyboard event was a press or a
     release, and which mouse BUTTON-DOWN flags are set. That is enough to
     count actions and not enough to know anything else.
  3. RAWKEYBOARD.VKey - the field naming the key - is declared below because
     the struct layout requires it, and is never read. Grep for it: it
     appears once, in the layout, and nowhere else in Loom.

eAPM is out of reach by design, exactly as on Linux: judging which actions
"count" would need to know what they were, which is what this refuses to see.

It runs INSIDE THE OVERLAY rather than as its own process, unlike the Linux
counter. Raw Input needs a window and a message pump, and the overlay already
has both, so a second process would be pure overhead - and it would be a
process the launcher has to kill, which is how the Linux counter can lose its
last bucket. The counts ride the same LOOM_STATE channel the overlay already
prints on, in the same shape the launcher already understands, so nothing
downstream changed.

One measured difference from Linux worth knowing: XInput2 reports wheel
scrolls as button presses, so the Linux counter includes them. This one
counts the five real mouse buttons and not the wheel, because scrolling in
AoE2 is zoom and a scroll burst is not five actions.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ctypes
import time

from . import apm, statefeed

# Note what is NOT imported at module scope: ctypes.wintypes, which raises
# ValueError off Windows, and PyQt6. The structures below use fixed-width
# ctypes so they lay out identically on any 64-bit platform, which is what
# lets the whole file be imported and its arithmetic tested from Linux.

WM_INPUT = 0x00FF
RID_INPUT = 0x10000003

# HID usage page 1 is "generic desktop"; usage 6 is keyboard, 2 is mouse.
HID_USAGE_PAGE_GENERIC = 0x01
HID_USAGE_KEYBOARD = 0x06
HID_USAGE_MOUSE = 0x02

# Deliver events even when the target window is not in the foreground, which
# is the normal case here: the game has focus, not the overlay. This flag is
# why hwndTarget must be a real window rather than NULL.
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001

RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1

# RAWKEYBOARD.Flags: bit 0 set means this was the key coming back UP.
RI_KEY_BREAK = 0x01

# The button-down bits of RAWMOUSE.usButtonFlags. Down only - counting the
# release too would double every click - and no wheel; see the docstring.
RI_MOUSE_BUTTON_DOWN_MASK = (
    0x0001      # left
    | 0x0004    # right
    | 0x0010    # middle
    | 0x0040    # button 4
    | 0x0100    # button 5
)


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_uint16),
        ("usUsage", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("hwndTarget", ctypes.c_void_p),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_uint32),
        ("dwSize", ctypes.c_uint32),
        ("hDevice", ctypes.c_void_p),
        ("wParam", ctypes.c_void_p),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", ctypes.c_uint16),
        ("Flags", ctypes.c_uint16),
        ("Reserved", ctypes.c_uint16),
        # Declared because the layout of everything after it depends on it.
        # NEVER READ - this is the field that would say which key, and the
        # whole point of this module is that it does not find out.
        ("VKey", ctypes.c_uint16),
        ("Message", ctypes.c_uint32),
        ("ExtraInformation", ctypes.c_uint32),
    ]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [
        ("usFlags", ctypes.c_uint16),
        # The union in the Windows headers is a ULONG overlapping two
        # USHORTs; spelled out here as the two halves plus the alignment
        # gap, because only usButtonFlags is wanted.
        ("_alignment", ctypes.c_uint16),
        ("usButtonFlags", ctypes.c_uint16),
        ("usButtonData", ctypes.c_uint16),
        ("ulRawButtons", ctypes.c_uint32),
        ("lLastX", ctypes.c_int32),
        ("lLastY", ctypes.c_int32),
        ("ulExtraInformation", ctypes.c_uint32),
    ]


class _RAWINPUTDATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE), ("keyboard", RAWKEYBOARD)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUTDATA)]


def counts_one_action(record):
    """Does this raw input record count as one action? (keys, clicks) deltas.

    Pure, and the only place that decides what an "action" is, so the rule can
    be read in one place and tested without Windows.

    A key going down is one action; a key coming back up is the same action
    ending. A mouse record is one action per button-down flag it carries -
    plural because a record can report two buttons in one event - and zero for
    pure movement, which is why usButtonFlags is consulted at all.
    """
    if record.header.dwType == RIM_TYPEKEYBOARD:
        if record.data.keyboard.Flags & RI_KEY_BREAK:
            return 0, 0
        return 1, 0
    if record.header.dwType == RIM_TYPEMOUSE:
        pressed = record.data.mouse.usButtonFlags & RI_MOUSE_BUTTON_DOWN_MASK
        return 0, bin(pressed).count("1")
    return 0, 0


class Counter:
    """Counts raw input events into buckets and prints them as state lines."""

    def __init__(self, bucket_seconds=apm.BUCKET_SECONDS):
        self.bucket_seconds = bucket_seconds
        self.keys = 0
        self.clicks = 0
        self._filter = None
        self._timer = None
        self._registered = False

    # ---- counting ------------------------------------------------------

    def handle(self, raw_handle):
        """One WM_INPUT. Reads the record and adds to the current bucket."""
        record = RAWINPUT()
        size = ctypes.c_uint32(ctypes.sizeof(RAWINPUT))
        got = ctypes.windll.user32.GetRawInputData(
            ctypes.c_void_p(raw_handle), RID_INPUT, ctypes.byref(record),
            ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
        # -1 means the buffer was wrong; a device this does not model (a
        # gamepad, a tablet) falls through counts_one_action as zero.
        if got == ctypes.c_uint32(-1).value:
            return
        keys, clicks = counts_one_action(record)
        self.keys += keys
        self.clicks += clicks

    def flush(self):
        """Emit the bucket just ended and start a new one.

        Empty buckets are news too - zero APM during a game is real - so this
        prints unconditionally, exactly as the Linux counter does.

        Printed with statefeed.encode directly rather than through
        StateEmitter: the emitter suppresses identical consecutive payloads,
        which would silently eat two equal buckets in a row.
        """
        print(statefeed.encode({"apm": {"wall": round(time.time(), 2),
                                        "keys": self.keys,
                                        "clicks": self.clicks}}))
        self.keys = self.clicks = 0

    # ---- lifecycle -----------------------------------------------------

    def stop(self):
        """Stop counting, and tell Windows to stop sending events.

        The part-finished bucket is emitted on the way out. Running inside
        the overlay is what makes that possible: the Linux counter is a
        separate process that the launcher signals, so it can lose up to a
        whole bucket every time Stop is pressed.
        """
        if self.keys or self.clicks:
            self.flush()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._registered:
            try:
                _register(None, RIDEV_REMOVE)
            except Exception:
                pass
            self._registered = False
        if self._filter is not None:
            try:
                from PyQt6.QtCore import QCoreApplication
                application = QCoreApplication.instance()
                if application is not None:
                    application.removeNativeEventFilter(self._filter)
            except Exception:
                pass
            self._filter = None


def _register(hwnd, flags):
    """Ask Windows for (or give back) raw keyboard and mouse events."""
    devices = (RAWINPUTDEVICE * 2)()
    for index, usage in enumerate((HID_USAGE_KEYBOARD, HID_USAGE_MOUSE)):
        devices[index].usUsagePage = HID_USAGE_PAGE_GENERIC
        devices[index].usUsage = usage
        devices[index].dwFlags = flags
        devices[index].hwndTarget = hwnd
    return bool(ctypes.windll.user32.RegisterRawInputDevices(
        devices, 2, ctypes.sizeof(RAWINPUTDEVICE)))


def _make_filter(counter):
    """The native event filter that turns WM_INPUT into a count."""
    from PyQt6.QtCore import QAbstractNativeEventFilter

    # Offsets into MSG on 64-bit Windows: hwnd, then message, then wParam and
    # lParam. lParam carries the raw input handle for WM_INPUT.
    pointer = ctypes.sizeof(ctypes.c_void_p)

    class InputFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            try:
                if event_type != b"windows_generic_MSG":
                    return False, 0
                address = int(message)
                if ctypes.c_uint.from_address(address + pointer).value != WM_INPUT:
                    return False, 0
                lparam = ctypes.c_ssize_t.from_address(
                    address + pointer * 3).value
            except Exception:
                return False, 0
            try:
                counter.handle(lparam)
            except Exception:
                # Counting must never take the overlay down; a lost event is
                # a rounding error in a statistic.
                pass
            return False, 0

    return InputFilter()


def start(hwnd, bucket_seconds=apm.BUCKET_SECONDS):
    """Begin counting into buckets. Returns a Counter, or None if it cannot.

    hwnd must be a real window - RIDEV_INPUTSINK, which is what makes this
    work while the GAME has focus rather than the overlay, requires one.

    Never raises. APM is a statistic; losing it is not worth losing the
    overlay for.
    """
    from PyQt6.QtCore import QCoreApplication, QTimer

    application = QCoreApplication.instance()
    if application is None or not hwnd:
        print("apm: no window to attach to; APM tracking disabled")
        return None

    counter = Counter(bucket_seconds)
    try:
        if not _register(ctypes.c_void_p(int(hwnd)), RIDEV_INPUTSINK):
            code = ctypes.GetLastError()
            print(f"apm: Windows refused raw input (error {code}); "
                  f"APM tracking disabled")
            return None
    except Exception as problem:
        print(f"apm: could not ask for raw input ({problem}); "
              f"APM tracking disabled")
        return None
    counter._registered = True

    counter._filter = _make_filter(counter)
    application.installNativeEventFilter(counter._filter)

    timer = QTimer()
    timer.timeout.connect(counter.flush)
    timer.start(int(bucket_seconds * 1000))
    counter._timer = timer

    print(f"apm: counting (bucket {bucket_seconds:g}s, counts only - this "
          f"program never sees which key)")
    return counter
