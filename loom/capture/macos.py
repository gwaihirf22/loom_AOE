"""
Loom — screen capture, the macOS backend.

Reads pixels out of the game's window with ScreenCaptureKit. The game here is
Feral Interactive's native macOS port of AoE2:DE, so this is a real Cocoa/Metal
app and not a compatibility layer.

The shape of this backend differs from the X11 one in a way worth stating
plainly. X11 answers every request synchronously: ask for a rectangle, get
those pixels now. ScreenCaptureKit instead PUSHES frames at you from a stream.
So this module keeps a live stream per window and caches its newest frame, and
every capture call is served from that cache. Three consequences, all
deliberate:

  * A poll's eight or nine region reads all come from ONE frame, where X11
    would give each its own instant. That is strictly more correct - a villager
    count paired with a clock from 20ms later is exactly the kind of torn read
    that desynchronises a build order.
  * Frames arrive on a BACKGROUND thread. This is the first cross-thread data
    in Loom, hence the lock.
  * A capture costs nothing: it is a numpy slice, not a round trip.

Two measured facts drive the rest of the design.

CAPTURE AT NATIVE SIZE, always. The window here is 1728x1084 points, its
backing store is 3456x2168, and the game renders 1920x1200 - three different
numbers. Asking ScreenCaptureKit for the point size makes it downscale the
game's render before Loom sees a pixel, which halves the glyphs: measured, the
HUD text went from 21px to 7px and the clock became unreadable while the
villager count silently misread 18 as 8. Native is the only size that throws
nothing away.

NO RUN LOOP IS NEEDED. Every asynchronous ScreenCaptureKit call here completes
against a plain threading.Event, and stream frames arrive while the main thread
merely sleeps. That matters because Qt owns the main run loop inside the
overlay; Loom never has to touch it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import threading
import time

import numpy as np
import objc
import Quartz
from AppKit import NSApplication, NSScreen, NSWorkspace
from CoreMedia import CMSampleBufferGetImageBuffer, CMTimeMake
from Foundation import NSObject
import ScreenCaptureKit as SCK

from .errors import CaptureError

# Feral Interactive's macOS port. A bundle identifier is an exact identity that
# survives the app being renamed or localised, which a window title is not.
GAME_BUNDLE_ID = "com.feralinteractive.ageofempires2"

# Fallback when matching by title instead. Compared case-insensitively on
# purpose: the app spells it "Age Of Empires II" in places and the X11 backend
# looks for "Age of Empires II", and a case-sensitive substring would miss it.
GAME_NAME_FRAGMENT = "age of empires"

# How often the stream is asked to deliver. Loom polls every 300ms, so 10fps
# leaves headroom for a missed frame or two while costing almost nothing.
FRAMES_PER_SECOND = 10

# How long a cached frame may go unrefreshed before it stops being evidence.
# Fifteen missed frames at 10fps. Past this the window is not being composited
# - backgrounded, or on another Space - and serving the last frame would freeze
# the game clock, which the read filters would then believe. A frozen clock is
# a wrong reading; a black frame is an admitted gap.
STALE_AFTER = 1.5

# How long to wait for an asynchronous ScreenCaptureKit call to come back.
CALLBACK_TIMEOUT = 8.0

# ScreenCaptureKit's "failed to start" code, which is what a window that is not
# being composited returns.
SC_ERROR_FAILED_TO_START = -3811


def _wait_async(start, timeout=CALLBACK_TIMEOUT):
    """Run an async ScreenCaptureKit call and block until its handler fires.

    start is called with the completion handler. Returns the handler's
    arguments as a tuple, or None on timeout.

    A threading.Event rather than pumping NSRunLoop: measured, these handlers
    are delivered on a dispatch queue and do not need the main run loop turning
    - which is what lets this work unchanged inside the Qt overlay, where the
    main run loop belongs to Qt.
    """
    done = threading.Event()
    box = []

    def handler(*args):
        box.append(args)
        done.set()

    start(handler)
    if not done.wait(timeout):
        return None
    return box[0] if box else None


class _Session:
    """What open_display() hands back: proof that capture is permitted.

    Deliberately holds no stream and enumerates nothing. reader.connect() and
    loom_overlay both call open_display(), so anything expensive here would be
    paid for twice.
    """

    def __init__(self):
        self.opened_at = time.monotonic()


def _host_app():
    """The app macOS attaches the Screen Recording grant to.

    The grant follows whatever launched Python, never Python itself, so naming
    it is the difference between an actionable message and a baffling one. The
    bundle id alone is no help to a human - "com.todesktop.230313mzl4w4u92" is
    Cursor - so LaunchServices is asked for the name that will actually appear
    in System Settings.
    """
    import os

    bundle_id = os.environ.get("__CFBundleIdentifier")
    if not bundle_id:
        return "the application running Loom"
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(
        bundle_id)
    if url is None:
        return bundle_id
    return f"{url.lastPathComponent()} ({bundle_id})"


def open_display():
    """Connect to the window server and check Loom may read pixels.

    Cheap by design, and starts nothing.

    sharedApplication() is not decoration: building an SCContentFilter from a
    process with no window server connection aborts the whole program on a
    CoreGraphics assertion (CGS_REQUIRE_INIT) rather than raising. It is
    idempotent, so inside the overlay this simply returns the instance Qt has
    already made.

    The activation policy is the other half, and it is what makes Loom usable
    at all here. A default (Regular) app takes focus when it starts, which
    puts the GAME into the background - and macOS stops compositing a
    backgrounded window, so the capture stream immediately dries up. Measured:
    loom_read.py read the HUD perfectly on its first poll and then nothing
    ever again, because launching it had pushed the game behind the terminal.

    Accessory rather than Prohibited: an accessory app takes no focus and gets
    no Dock icon, but it may still show windows - which the overlay needs, and
    Prohibited would deny it.
    """
    application = NSApplication.sharedApplication()
    # 1 is NSApplicationActivationPolicyAccessory.
    application.setActivationPolicy_(1)

    # Two scheduling remedies were tried here for the ~1s polls under game
    # load and REMOVED after measurement: NSProcessInfo beginActivity (the
    # App Nap opt-out) and pthread_set_qos_class_self_np to user-interactive.
    # Neither moved the poll cost at all - 977ms median before, 957ms after,
    # noise. Whatever slows the polls, it is not something either of Apple's
    # documented scheduling levers touches, so do not re-add them expecting
    # otherwise; the remaining lever is making the per-poll work smaller.

    # Preflight asks without prompting, so a refusal can be explained rather
    # than arriving as a bare system dialog with no context attached.
    if not Quartz.CGPreflightScreenCaptureAccess():
        Quartz.CGRequestScreenCaptureAccess()
        raise CaptureError(
            "macOS has not granted Screen Recording permission.\n"
            f"  The grant attaches to {_host_app()}, not to Python.\n"
            "  Allow it in System Settings > Privacy & Security > Screen & "
            "System Audio\n"
            "  Recording, then RESTART that app - the permission is only "
            "re-read at launch.")
    return _Session()


def _shareable_content():
    """Everything macOS will let this process capture: windows and displays.

    onScreenWindowsOnly is FALSE, and that is the entire reason this has a
    comment. "On screen" means on the ACTIVE Space, and a game running full
    screen gets a Space of its own - so with the flag true the game is not
    merely far down the list, it is absent. Measured: 46 windows and no game
    with it on, 393 and all eight of the game's with it off.
    """
    result = _wait_async(
        lambda handler: SCK.SCShareableContent
        .getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
            True, False, handler))

    if result is None:
        raise CaptureError(
            "ScreenCaptureKit never answered when asked for the window list.")
    content, error = result
    if error is not None:
        raise CaptureError(f"ScreenCaptureKit refused the window list: {error}")
    if content is None:
        raise CaptureError("ScreenCaptureKit returned no window list.")

    if not list(content.windows()):
        # Passing preflight but seeing nothing means the grant is stale: it was
        # given to an earlier launch of the host app.
        raise CaptureError(
            "ScreenCaptureKit listed no windows at all. The Screen Recording "
            f"permission is probably stale - restart {_host_app()} so it "
            "re-reads the grant.")
    return content


def _area(window):
    size = window.frame().size
    return size.width * size.height


def _pick_game_window(windows, fragment=None):
    """The game's playfield window, or None.

    The game owns EIGHT windows: the playfield plus a 500x500, a 312x237 and
    five 1728x33 slivers. Only one carries the HUD and it is by far the largest
    - 1728x1084 against 1728x33 for the runners-up - so area decides it. The
    list comes back in no documented order, which makes "the first match" a
    coin flip.
    """
    if fragment is None:
        owned = [w for w in windows
                 if w.owningApplication()
                 and w.owningApplication().bundleIdentifier() == GAME_BUNDLE_ID]
        if owned:
            return max(owned, key=_area)

    needle = (fragment or GAME_NAME_FRAGMENT).lower()
    matches = []
    for window in windows:
        application = window.owningApplication()
        haystacks = [window.title() or ""]
        if application:
            haystacks.append(application.applicationName() or "")
        if any(needle in text.lower() for text in haystacks):
            matches.append(window)
    return max(matches, key=_area) if matches else None


def _display_scale(content, window):
    """Points-to-pixels for the display this WINDOW is on.

    Not the desktop's largest scale, which is the mistake this replaces. On a
    desk with a 2x built-in screen and a 1x 4K monitor, taking the maximum
    doubled a window that was already in real pixels: a 3840-point window was
    captured at 7680, which invents no detail and pushes the HUD to anchor
    scale 2.04 - past the 2.0 ceiling of anchor.COARSE_SCALES, where the
    search can no longer refine. Measured on exactly that desk.

    SCDisplay reports both its point frame and its pixel size, and its frame
    shares SCWindow's top-left-origin coordinate space, so the window can be
    matched to a display without going near NSScreen's bottom-left origin and
    the flip bugs that live there.
    """
    frame = window.frame()
    centre_x = frame.origin.x + frame.size.width / 2
    centre_y = frame.origin.y + frame.size.height / 2

    for display in content.displays():
        bounds = display.frame()
        if (bounds.origin.x <= centre_x < bounds.origin.x + bounds.size.width
                and bounds.origin.y <= centre_y
                < bounds.origin.y + bounds.size.height):
            if bounds.size.width:
                return float(display.width()) / float(bounds.size.width)

    # No display owned the window's centre - it may straddle two, or the list
    # may be empty. The main screen's factor is a better guess than 1.0, which
    # would silently halve the capture on a Retina Mac.
    main = NSScreen.mainScreen()
    return float(main.backingScaleFactor()) if main else 1.0


def bgra_to_bgr(raw, width, height, stride):
    """Padded 32BGRA bytes as a tight BGR array.

    Separated from the CoreVideo plumbing so it can be tested without a Mac,
    because getting it wrong does not raise - it produces a diagonally sheared
    image with a perfectly reasonable mean brightness, which is exactly the
    sort of wrong answer nothing downstream would question.

    stride is CVPixelBufferGetBytesPerRow and is NOT width * 4: CoreVideo pads
    each row out to a hardware-friendly width. The buffer therefore has to be
    reshaped by the STRIDE and then sliced back to the real width. The alpha
    channel goes, leaving the same BGR layout X11's ZPixmap produces - which is
    why nothing downstream needs to know where a frame came from.
    """
    pixels = np.frombuffer(raw, dtype=np.uint8)
    # copy() because callers unlock the underlying buffer the moment this
    # returns, and a numpy view over freed CoreVideo memory is a crash waiting
    # for some later poll.
    return pixels.reshape(height, stride // 4, 4)[:, :width, :3].copy()


def _buffer_to_bgr(pixel_buffer):
    """A CVPixelBuffer as a BGR numpy array, or None if it cannot be read."""
    Quartz.CVPixelBufferLockBaseAddress(pixel_buffer, 1)  # 1 = read-only
    try:
        width = Quartz.CVPixelBufferGetWidth(pixel_buffer)
        height = Quartz.CVPixelBufferGetHeight(pixel_buffer)
        stride = Quartz.CVPixelBufferGetBytesPerRow(pixel_buffer)
        base = Quartz.CVPixelBufferGetBaseAddress(pixel_buffer)
        if base is None:
            return None
        return bgra_to_bgr(base.as_buffer(stride * height),
                           width, height, stride)
    finally:
        Quartz.CVPixelBufferUnlockBaseAddress(pixel_buffer, 1)


class _StreamOutput(NSObject):
    """Receives frames from ScreenCaptureKit on a background thread."""

    def initWithWindow_(self, game_window):
        self = objc.super(_StreamOutput, self).init()
        if self is None:
            return None
        self._game_window = game_window
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample, kind):
        # 0 is SCStreamOutputTypeScreen. Audio buffers arrive here too if ever
        # enabled, and reshaping one as pixels would be nonsense.
        if kind != 0:
            return
        pixel_buffer = CMSampleBufferGetImageBuffer(sample)
        if pixel_buffer is None:
            return
        # Hand the buffer over as-is; conversion happens lazily when a poll
        # actually wants pixels. Converting here meant a 33MB copy ten times a
        # second on a machine the game is already saturating, for frames of
        # which polls consume two or three - measured, the lag probe caught
        # polls stretching to seconds under that contention. Holding ONE
        # buffer between callbacks is safe; hoarding them would starve
        # ScreenCaptureKit's pool and stall the stream.
        self._game_window._accept_buffer(pixel_buffer)


class GameWindow:
    """The game's window, and the live stream feeding frames from it.

    This is what find_game_window returns and what every other function in the
    contract takes. It owns the stream because the stream's lifetime is the
    window's: nothing else in Loom knows streams exist.
    """

    def __init__(self, window, scale=1.0):
        self._window = window
        self.window_id = int(window.windowID())

        frame = window.frame()
        self.point_size = (float(frame.size.width), float(frame.size.height))
        self.scale = scale
        # Capture at the window's real pixel size, never less. Asking for
        # fewer pixels makes ScreenCaptureKit downscale the game's render
        # before Loom sees it: measured, that took the HUD text from 21px to
        # 7px, made the clock unreadable, and turned 18 villagers into a
        # confidently reported 8.
        self.capture_size = (max(1, int(round(frame.size.width * scale))),
                             max(1, int(round(frame.size.height * scale))))

        self._lock = threading.Lock()
        self._frame = None
        self._frame_at = 0.0
        self._pending = None
        self._stream = None
        self._output = None

    # ---- the stream ----------------------------------------------------

    def _accept(self, frame):
        """Take a new, already-converted frame.

        The array is swapped in whole rather than written into, so a reader
        holding the previous one keeps a consistent picture instead of watching
        it change under them mid-poll.
        """
        with self._lock:
            self._frame = frame
            self._pending = None
            self._frame_at = time.monotonic()

    def _accept_buffer(self, pixel_buffer):
        """Take the newest raw CVPixelBuffer from the capture thread.

        Just a reference swap: the buffer is converted only if a poll asks for
        it, so frames nobody reads cost nothing. The replaced buffer's last
        reference drops here, which returns it to ScreenCaptureKit's pool.
        """
        with self._lock:
            self._pending = pixel_buffer
            self._frame_at = time.monotonic()

    def start(self):
        """Begin streaming, and wait for the first frame to prove it works."""
        configuration = SCK.SCStreamConfiguration.alloc().init()
        configuration.setWidth_(self.capture_size[0])
        configuration.setHeight_(self.capture_size[1])
        # Without this the stream delivers biplanar YUV, which reshapes into
        # plausible-looking garbage rather than failing - a mean brightness
        # that looks like an image but is not one.
        configuration.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)
        # The cursor is not part of the HUD and would sit over the numbers.
        configuration.setShowsCursor_(False)
        configuration.setMinimumFrameInterval_(CMTimeMake(1, FRAMES_PER_SECOND))

        content_filter = (SCK.SCContentFilter.alloc()
                          .initWithDesktopIndependentWindow_(self._window))
        self._output = _StreamOutput.alloc().initWithWindow_(self)
        self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, configuration, None)

        ok, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output, 0, None, None)
        if not ok:
            raise CaptureError(f"could not attach a stream output: {error}")

        result = _wait_async(self._stream.startCaptureWithCompletionHandler_)
        if result is None:
            raise CaptureError("ScreenCaptureKit never started the stream.")
        error = result[0]
        if error is not None:
            if error.code() == SC_ERROR_FAILED_TO_START:
                raise CaptureError(
                    "macOS will not capture this window (SCStream -3811).\n"
                    "  It is not being composited: the game is behind another "
                    "window, or on\n"
                    "  a Space that is not the active one. Bring the game to "
                    "the front and try\n"
                    "  again - capture starts working the moment it is "
                    "visible.")
            raise CaptureError(f"the capture stream failed to start: {error}")

        if not self._await_first_frame():
            raise CaptureError(
                "the capture stream started but delivered no frames. The "
                "window is probably not visible.")

    def _await_first_frame(self, timeout=CALLBACK_TIMEOUT):
        """Block until a frame lands, so callers never meet an empty window."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._frame is not None or self._pending is not None:
                    return True
            time.sleep(0.05)
        return False

    def stop(self):
        if self._stream is not None:
            self._stream.stopCaptureWithCompletionHandler_(lambda error: None)
            self._stream = None

    # ---- reading -------------------------------------------------------

    def latest(self):
        """The newest frame, or black once the stream has gone quiet.

        Returning black rather than raising is a deliberate choice about which
        failure Loom can survive. Nothing catches an exception around the poll
        loop, so raising here would take the overlay down the first time the
        player alt-tabbed. Black instead fails the anchor match, which the
        reader already understands as "no reading" and shows as "waiting for
        the game" - an honest admitted gap, on a path that has been exercised
        since the first milestone.

        Returning the STALE frame would be the real mistake: the game clock
        would stop moving, and a value that repeats is believed by design, so
        Loom would report a frozen clock as real game time.
        """
        with self._lock:
            pending = self._pending
            frame = self._frame
            age = time.monotonic() - self._frame_at

        if pending is not None:
            # Convert outside the lock: this is the 33MB copy, and holding
            # the lock through it would stall the capture thread's swap.
            converted = _buffer_to_bgr(pending)
            if converted is not None:
                frame = converted
                with self._lock:
                    # Another buffer may have landed meanwhile; only cache
                    # the conversion if it is still the newest thing known.
                    if self._pending is pending:
                        self._frame = converted
                        self._pending = None

        if frame is None or age > STALE_AFTER:
            width, height = self.capture_size
            return np.zeros((height, width, 3), dtype=np.uint8)
        return frame


# Every GameWindow ever started, by window id.
#
# reader.connect() and loom_overlay both call open_display() and
# find_game_window() - the second only wants the window's geometry - so without
# this the overlay would run two streams over the same window, paying twice for
# the frames and the wake-ups.
_WINDOWS = {}


def find_game_window(session, fragment=None):
    """Find the game's window and start streaming from it.

    Returns a GameWindow, or None if the game is not running - None rather than
    an error because callers poll this while waiting for the player to launch
    the game.
    """
    content = _shareable_content()
    window = _pick_game_window(list(content.windows()), fragment)
    if window is None:
        return None

    window_id = int(window.windowID())
    existing = _WINDOWS.get(window_id)
    if existing is not None:
        return existing

    game_window = GameWindow(window, _display_scale(content, window))
    game_window.start()
    _WINDOWS[window_id] = game_window
    return game_window


def window_size(window):
    """(width, height) of the captured frame, in PIXELS.

    Pixels, not points, and the distinction is load-bearing. reader.poll asks
    for this and hands it to notifications.panel_region, which works in
    fractions of it and then crops that rectangle out of a captured frame. On
    a 2x display, reporting points here would crop the notification panel at
    half scale into the top-left corner and quietly stop the game's event feed
    being read - no exception, just no events ever again.
    """
    frame = window.latest()
    height, width = frame.shape[:2]
    return width, height


def window_geometry(window, session=None):
    """Where the window sits on the desktop: (x, y, width, height) in POINTS.

    Points here, where window_size is in pixels, because this answer is used to
    place a Qt window in desktop coordinates and Qt works in points.

    SCWindow.frame() is already top-left-origin with y increasing downwards,
    the same convention Qt uses, so nothing is flipped. NSWindow and NSScreen
    frames are bottom-left-origin and are where the multi-display flip bug
    lives; they are deliberately not used.
    """
    frame = window._window.frame()
    return (int(frame.origin.x), int(frame.origin.y),
            int(frame.size.width), int(frame.size.height))


def capture_window(window):
    """The whole window as a BGR image."""
    return window.latest()


def capture_region(window, x, y, width, height):
    """Part of the window as a BGR image, in capture-frame pixels.

    A slice of the cached frame, so it costs nothing and - more importantly -
    every region read during one poll comes from the same instant. The slice is
    clipped to the frame rather than trusted: a region derived when the HUD was
    a different size would otherwise raise or come back short.
    """
    frame = window.latest()
    frame_height, frame_width = frame.shape[:2]

    x1 = max(0, min(int(x), frame_width))
    y1 = max(0, min(int(y), frame_height))
    x2 = max(x1, min(int(x + width), frame_width))
    y2 = max(y1, min(int(y + height), frame_height))
    return frame[y1:y2, x1:x2]
