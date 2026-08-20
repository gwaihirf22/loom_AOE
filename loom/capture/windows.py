"""
Loom — screen capture, the Windows backend.

Reads pixels out of the game's window with Windows Graphics Capture, the same
API the OS itself uses for window sharing. loom/capture/__init__.py picks
between this and its siblings by platform.

Why WGC and not the obvious GDI route. BitBlt and PrintWindow are pull-shaped
and would have fitted x11.py's design exactly - ask for a rectangle, get a
rectangle, read only the few small bands the reader actually wants. Measured
against a live composited window (tools/windows_probe.py), BitBlt returned a
frame that was 0.0% non-zero: pure black. That is what a Direct3D swap chain
looks like through a GDI device context, and AoE2:DE is a Direct3D game.
PrintWindow with PW_RENDERFULLCONTENT did return real pixels, but at 20ms a
grab against WGC's 2 microsecond region crop. So the choice was not close.

This backend is therefore shaped like macos.py, not like x11.py: push, not
pull. The compositor hands over frames as it draws them and there is nothing
to "poll" - there is only whatever arrived most recently. Everything that
follows from that (one stream per window, cache the newest frame, serve black
rather than stale) is the macOS design, and the reasoning there applies
unchanged here.

One thing Windows does better than macOS, and it matters: WGC keeps delivering
frames for a window that is NOT in the foreground. On macOS the game must be
frontmost or the frames simply stop, which is that port's worst limitation.
Measured here on a backgrounded window: 99.9% real pixels. Loom can be used
with the game behind another window.

The other Windows-specific hazard is display scaling, handled in
_become_dpi_aware and window_geometry below.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ctypes
import functools
import threading
import time

import numpy as np

from .errors import CaptureError

# Note what is NOT imported here: ctypes.wintypes, which would be the natural
# way to spell HWND. On anything but Windows importing it raises ValueError,
# and tests/test_capture_selector.py imports every backend on every platform
# to check the contract - catching ImportError, which a ValueError is not. So
# this module must stay importable on Linux and macOS, and a window handle is
# spelled ctypes.c_void_p instead. Everything genuinely Windows-only
# (win32gui, windows_capture, ctypes.windll) is imported inside a function.

# Matched case-insensitively against the window title. The X11 backend looks
# for exactly this string; keeping the same fragment means both platforms
# recognise the game by the same rule.
WINDOW_NAME_FRAGMENT = "Age of Empires II"

# What the game's executable is called, lowercased, used only to corroborate a
# title match. AoE2:DE ships AoE2DE_s.exe as the game itself; matching the
# "aoe" stem covers the launcher and any future renaming.
GAME_EXE_FRAGMENT = "aoe"

# How long a cached frame may go unrefreshed before it stops being evidence.
# Past this the window is not being composited - minimised, or the game has
# stopped drawing - and serving the last frame would freeze the game clock,
# which the read filters would then believe. A frozen clock is a wrong
# reading; a black frame is an admitted gap. Same constant and same reasoning
# as macos.STALE_AFTER.
#
# Note the difference from macOS while trusting the same number: ScreenCapture
# Kit is asked for a fixed 10fps, where WGC delivers when the window's content
# CHANGES. A game redraws continuously, so a running game refreshes this far
# faster than the timeout; a window that has genuinely stopped being drawn is
# exactly what should blank.
STALE_AFTER = 1.5

# How long to wait for the stream's first frame before calling it a failure.
FIRST_FRAME_TIMEOUT = 5.0


def _translates_errors(function):
    """Re-raise this function's Windows failures as CaptureError.

    The contract promises one error type whatever the backend, so callers can
    degrade to "no reading" without importing anything Windows-specific to
    know what went wrong. This matters most for the failure it makes
    survivable: the game exits mid-session, its window handle stops resolving,
    and every later capture fails. Without this that climbs out of the poll
    timer and takes the overlay with it.
    """
    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CaptureError:
            raise
        except Exception as problem:
            raise CaptureError(
                f"Windows capture failed in {function.__name__}: "
                f"{type(problem).__name__}: {problem}") from problem
    return wrapper


# ---------------------------------------------------------------------------
# Display scaling
# ---------------------------------------------------------------------------

def _become_dpi_aware():
    """Tell Windows this process reports real pixels, not scaled fictions.

    A process that has not said this is "DPI unaware", and Windows lies to it
    for compatibility: GetWindowRect comes back scaled by the display setting,
    so a 2560-pixel-wide window measures 1707 at 150%. Every pixel constant in
    Loom is anchored to real pixels, and CLAUDE.md's rule is that a pixel
    constant which does not scale with the HUD is a latent bug. A capture
    backend reporting virtualised sizes would move the anchor scale under
    every one of them at once, and - the part that makes it dangerous - it
    would not fail. It would read slightly wrong digits with full confidence.

    Qt6 sets per-monitor-v2 awareness for itself, but loom_read.py and
    loom_coach.py never construct a QApplication, so this cannot rely on
    somebody else having done it. Inside the overlay Qt has usually got here
    first, and the call simply returns false - already set is not an error,
    which is why nothing here raises.

    -4 is DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2.
    """
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        # Windows 8.1+. 2 is PROCESS_PER_MONITOR_DPI_AWARE.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _dpi_scale(hwnd):
    """How many real pixels this window draws per Qt point. 1.0 at 100%."""
    try:
        dpi = int(ctypes.windll.user32.GetDpiForWindow(
            ctypes.c_void_p(hwnd)))
    except (AttributeError, OSError):
        return 1.0
    return points_per_pixel(dpi)


def points_per_pixel(dpi):
    """Turn a DPI into the divisor that converts real pixels to Qt points.

    Split out from _dpi_scale so the arithmetic can be tested without a
    window: 96 DPI is 100% scaling and must come back exactly 1.0, and a
    nonsense DPI of 0 must not divide the geometry into infinity.
    """
    return (dpi / 96.0) if dpi else 1.0


# ---------------------------------------------------------------------------
# The stream over one window
# ---------------------------------------------------------------------------

class GameWindow:
    """A live Windows Graphics Capture stream over one window.

    Holds the newest frame delivered by the capture thread, and hands out
    copies of it. The lock is real: frames arrive on the library's own thread
    while the reader's poll timer asks for them on the main thread.
    """

    def __init__(self, hwnd):
        self.hwnd = hwnd
        self._frame = None
        self._frame_at = 0.0
        self._closed = False
        self._lock = threading.Lock()
        self._control = None
        self._capture = None

    # ---- lifecycle -----------------------------------------------------

    def start(self):
        """Begin streaming, and do not return until a frame has landed.

        Waiting for the first frame means callers never meet a window that
        exists but has no pixels yet, which would otherwise look exactly like
        a HUD that cannot be found.
        """
        self._control = self._start_stream()

        if not self._await_first_frame():
            self.stop()
            raise CaptureError(
                "the capture stream started but delivered no frames within "
                f"{FIRST_FRAME_TIMEOUT:.0f}s. The window is probably "
                "minimised, or the game is in exclusive fullscreen - try the "
                "game's Windowed Fullscreen display mode.")

    def _start_stream(self):
        """Start the capture, giving up the borderless request if it is refused.

        Returns the capture control. Raises CaptureError if no arrangement
        starts at all.

        draw_border=False asks the OS to drop the yellow "this window is being
        captured" edge, and setting it is GraphicsCaptureSession.IsBorderRequired
        - an API Windows 11 has and Windows 10 does not. On Windows 10 the
        request does not degrade: start_free_threaded raises "Toggling the
        capture border is not supported by the Graphics Capture API on this
        platform", nothing catches it, and Loom dies before its first frame with
        a traceback about a cosmetic preference. Reported from Windows 10 Pro
        22H2; invisible here, because this machine is Windows 11.

        So the request is an attempt, not a requirement. The library's own
        default of None means "leave the border alone", which is what a machine
        that cannot turn it off was always going to do anyway.

        Retried rather than guarded by a build-number check on purpose: which
        servicing build first carried IsBorderRequired is not something I am
        confident about from documentation, and this backend was written by
        measuring rather than by reading - see the module docstring. A retry
        adapts to the machine. A version check adapts to my belief about it.
        """
        try:
            from windows_capture import WindowsCapture
        except ImportError as missing:
            raise CaptureError(
                "the windows-capture package is not installed, so Loom cannot "
                "read the screen on Windows. Install it with: "
                "pip install -r requirements.txt") from missing

        # Preferred first, then the one every machine can do.
        attempts = (False, None)
        first_problem = None
        for draw_border in attempts:
            self._capture = self._build_capture(WindowsCapture, draw_border)
            try:
                control = self._capture.start_free_threaded()
            except Exception as problem:
                if first_problem is None:
                    first_problem = problem
                continue
            if draw_border is None and first_problem is not None:
                print("Windows will draw a yellow capture border around the "
                      "game: hiding it needs an API this version of Windows "
                      "does not have. Loom reads the HUD exactly the same.")
            return control

        raise CaptureError(
            f"could not start a capture stream on the game window: "
            f"{type(first_problem).__name__}: {first_problem}"
        ) from first_problem

    def _build_capture(self, WindowsCapture, draw_border):
        """One configured WindowsCapture with its two callbacks attached.

        Separate from _start_stream because a refused border request means
        building the whole thing again: the setting is fixed at construction
        and the callbacks are registered on the instance.
        """
        capture = WindowsCapture(
            cursor_capture=False,     # the cursor is not part of the HUD
            draw_border=draw_border,  # False asks for no yellow edge; see above
            window_hwnd=self.hwnd,
        )

        @capture.event
        def on_frame_arrived(frame, capture_control):
            # The copy is not optional. frame_buffer is a zero-copy view over
            # a natively mapped frame, valid only until this callback returns;
            # keeping the view would read freed memory at some later and
            # entirely unrelated moment. The [:, :, :3] drops BGRA's alpha,
            # leaving the plain BGR that OpenCV and the rest of Loom expect.
            # The library has already undone the row stride, so this is a
            # straight (height, width, 4) array and no de-padding is needed.
            picture = frame.frame_buffer[:, :, :3].copy()
            with self._lock:
                self._frame = picture
                self._frame_at = time.monotonic()

        @capture.event
        def on_closed():
            # The window went away. Recorded rather than raised, because this
            # fires on the capture thread where nothing could catch it.
            with self._lock:
                self._closed = True

        return capture

    def _await_first_frame(self, timeout=FIRST_FRAME_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None:
                    return True
                if self._closed:
                    return False
            time.sleep(0.01)
        return False

    def stop(self):
        try:
            if self._control is not None:
                self._control.stop()
        except Exception:
            # Stopping is best-effort by nature: the usual reason it fails is
            # that the thing being stopped has already gone.
            pass
        finally:
            self._control = None

    def is_alive(self):
        with self._lock:
            return not self._closed

    # ---- reading -------------------------------------------------------

    def latest(self):
        """The newest frame, or black once the stream has gone quiet.

        Returning black rather than raising is a deliberate choice about which
        failure Loom can survive, and it is macos.GameWindow.latest's choice
        for the same reasons. Nothing catches an exception around the poll
        loop, so raising here would take the overlay down the first time the
        game minimised. Black instead fails the anchor match, which the reader
        already understands as "no reading" and shows as "waiting for the
        game" - an honest admitted gap on a path exercised since milestone one.

        Serving the STALE frame would be the real mistake: the game clock
        would stop moving, and a value that repeats is believed by design, so
        Loom would report a frozen clock as real game time.
        """
        with self._lock:
            frame = self._frame
            age = time.monotonic() - self._frame_at

        if frame is None:
            raise CaptureError(
                "no frame has ever arrived from the game window")
        if age > STALE_AFTER:
            height, width = frame.shape[:2]
            return np.zeros((height, width, 3), dtype=np.uint8)
        return frame


# Every GameWindow ever started, by window handle.
#
# reader.connect() and loom_overlay both call open_display() and
# find_game_window() - the second only wants the window's geometry - so
# without this the overlay would run two streams over the same window, paying
# twice for the frames and the wake-ups. Same reasoning as macos._WINDOWS.
_WINDOWS = {}


# ---------------------------------------------------------------------------
# Finding the game
# ---------------------------------------------------------------------------

def _process_name(pid):
    """The executable name behind a process id, lowercased, or ""."""
    try:
        import win32api
        import win32con
        import win32process
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            name = win32process.GetModuleFileNameEx(handle, 0)
        finally:
            win32api.CloseHandle(handle)
        return name.split("\\")[-1].lower()
    except Exception:
        # Asking about somebody else's process is allowed to fail - a process
        # at a higher integrity level will refuse. Not knowing the executable
        # only costs the corroboration, so it must not cost the match.
        return ""


def _candidate_windows(fragment):
    """Every visible top-level window whose title contains the fragment."""
    import win32gui
    import win32process

    wanted = fragment.lower()
    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title or wanted not in title.lower():
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        found.append({"hwnd": hwnd, "area": width * height,
                      "exe": _process_name(pid)})

    win32gui.EnumWindows(visit, None)
    return found


def choose_window(candidates):
    """Which of the title matches is actually the game? None if there are none.

    A title fragment on its own is a guess: "Age of Empires II" matches a
    browser tab reading the wiki just as happily as the game. The executable
    name is the corroboration - the same move identify_hud makes when it
    refuses to name a HUD skin on the evidence of a single icon.

    If nothing matches by executable the title matches still stand, so an
    unexpected launcher or a renamed binary cannot make the game unfindable;
    the corroboration narrows the field when it can and never empties it.

    Largest wins within whichever pool survives: the game's main window dwarfs
    any tool window it owns.

    A pure function of a list of dicts, so the rule is testable without a
    desktop full of windows to arrange.
    """
    if not candidates:
        return None
    by_exe = [w for w in candidates if GAME_EXE_FRAGMENT in w.get("exe", "")]
    return max(by_exe or candidates, key=lambda w: w["area"])


@_translates_errors
def open_display():
    """Get the process ready to read pixels. Cheap, and starts nothing.

    There is no display server to connect to on Windows, so the only real work
    here is declaring DPI awareness - and that has to happen before any window
    rectangle is asked for anywhere in the process, which makes this the right
    place for it.

    Returns a plain marker object rather than None, because callers pass the
    result around and thread it back into window_geometry; None would be
    indistinguishable from "not connected".
    """
    _become_dpi_aware()
    return _Session()


class _Session:
    """Stands in for X11's Display. Windows has nothing to connect to."""

    def __repr__(self):
        return "<loom.capture.windows session>"


@_translates_errors
def find_game_window(session=None, fragment=WINDOW_NAME_FRAGMENT):
    """Find the game's window and start streaming from it.

    Returns a GameWindow, or None if the game is not running - None rather
    than an error, because callers poll this while waiting for the player to
    launch the game.

    Which of several title matches is the game is choose_window's decision;
    this function's own job is the stream and its reuse.
    """
    chosen = choose_window(_candidate_windows(fragment))
    if chosen is None:
        return None
    hwnd = chosen["hwnd"]

    existing = _WINDOWS.get(hwnd)
    if existing is not None:
        if existing.is_alive():
            return existing
        # The handle was reused, or the stream died. Either way the cached
        # one is no longer evidence of anything.
        existing.stop()
        del _WINDOWS[hwnd]

    game_window = GameWindow(hwnd)
    game_window.start()
    _WINDOWS[hwnd] = game_window
    return game_window


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------

@_translates_errors
def window_size(window):
    """(width, height) of the captured frame, in PIXELS.

    Pixels, not points, and the distinction is load-bearing - it is the same
    trap macos.window_size documents. reader.poll asks for this and hands it
    to notifications.panel_region, which works in fractions of it and then
    crops that rectangle out of a captured frame. At 125% display scaling,
    reporting points here would crop the notification panel at four-fifths
    scale into the top-left corner and quietly stop the game's event feed
    being read - no exception, just no events ever again.

    Taken from the frame itself rather than from GetClientRect, so it is by
    construction the size of the pixels callers are about to be given.
    """
    frame = window.latest()
    height, width = frame.shape[:2]
    return width, height


@_translates_errors
def window_geometry(window, session=None):
    """Where the window sits on the desktop: (x, y, width, height) in POINTS.

    Points here, where window_size is in pixels, because this answer is used
    to place a Qt window in desktop coordinates and Qt works in points.

    GetWindowRect returns real pixels, because open_display made this process
    DPI aware. Dividing by the window's own DPI scale converts to the points
    Qt will use. Asking the WINDOW for its DPI rather than the system means a
    multi-monitor desktop with different scaling per monitor still lands the
    overlay on the right one - which is the case this would otherwise get
    wrong, silently, only for people with mixed displays.
    """
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(window.hwnd)
    scale = _dpi_scale(window.hwnd)
    return (int(round(left / scale)), int(round(top / scale)),
            int(round((right - left) / scale)),
            int(round((bottom - top) / scale)))


@_translates_errors
def capture_region(window, x, y, width, height):
    """Part of the window as a BGR image, in capture-frame pixels.

    x and y are relative to the window's own top-left corner.

    A slice of the cached frame, so it costs almost nothing and - more
    importantly - every region read during one poll comes from the same
    instant. The slice is clipped to the frame rather than trusted: a region
    derived when the HUD was a different size would otherwise raise or come
    back short.
    """
    frame = window.latest()
    frame_height, frame_width = frame.shape[:2]

    x1 = max(0, min(int(x), frame_width))
    y1 = max(0, min(int(y), frame_height))
    x2 = max(x1, min(int(x + width), frame_width))
    y2 = max(y1, min(int(y + height), frame_height))
    return frame[y1:y2, x1:x2]


@_translates_errors
def capture_window(window):
    """The whole window as a BGR image."""
    return window.latest()
