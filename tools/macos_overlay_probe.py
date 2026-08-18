"""
Loom — macOS overlay probe (development tool).

Answers ONE question before any of loom/overlay.py is touched:

    can a Qt window float above the game's full-screen Space on macOS,
    while the game stays composited?

    python -m tools.macos_overlay_probe            # the recommended settings
    python -m tools.macos_overlay_probe --sweep    # try every combination
    python -m tools.macos_overlay_probe --hold 30  # leave it up to look at

Both halves of that question matter. macOS gives a full-screen application a
Space of its own, and an ordinary window does not follow it there - which is
why Loom's overlay is unusable on this platform today, since playing in a
window that does not fill the screen is not really playing.

The second half is the trap. SCStreamError -3811 established that macOS will
not capture a window it is not compositing, so an overlay that wins the
foreground at the game's expense would BLIND Loom - trading a missing panel
for a missing HUD. A combination that shows the panel but stops the capture is
a failure, not a partial success.

Visibility is measured rather than eyeballed: the panel is painted a colour
nothing in Age of Empires uses, the whole display is then captured, and the
probe counts those pixels. "I think I can see it" is not evidence, and a
screenshot is saved either way so the answer can be checked by eye too.

Nothing here imports loom.overlay. The point is to test what macOS permits,
not what Loom currently asks for.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import ctypes
import os
import sys
import threading
import time

import cv2
import numpy as np
import objc
import Quartz
from AppKit import (NSApplication, NSScreenSaverWindowLevel,
                    NSStatusWindowLevel, NSPopUpMenuWindowLevel,
                    NSFloatingWindowLevel, NSNormalWindowLevel,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary,
                    NSWindowCollectionBehaviorTransient,
                    NSWindowCollectionBehaviorIgnoresCycle,
                    NSWorkspace)
import ScreenCaptureKit as SCK
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QApplication, QWidget

from loom import paths
from loom.capture import macos as backend

# The panel's colour, chosen because the game never draws it: pure magenta.
# Detection allows a little slack for any compositor blending.
PROBE_BGR = (255, 0, 255)
PROBE_TOLERANCE = 40

# Enough magenta pixels to mean "the panel is on screen" rather than "a stray
# pixel survived a rescale". The panel is 400x120, so a visible one shows tens
# of thousands.
VISIBLE_PIXELS = 2000

# The settings worth trying, worst to best guess. A level alone is not enough:
# without canJoinAllSpaces the window simply stays behind on the Space it was
# born in, however high it is raised.
COMBINATIONS = [
    ("normal, no behaviour",
     NSNormalWindowLevel, 0),
    ("floating",
     NSFloatingWindowLevel, 0),
    ("floating + canJoinAllSpaces",
     NSFloatingWindowLevel, NSWindowCollectionBehaviorCanJoinAllSpaces),
    ("status + canJoinAllSpaces + stationary",
     NSStatusWindowLevel,
     NSWindowCollectionBehaviorCanJoinAllSpaces
     | NSWindowCollectionBehaviorStationary),
    ("popup + joinAll + fullScreenAux + stationary",
     NSPopUpMenuWindowLevel,
     NSWindowCollectionBehaviorCanJoinAllSpaces
     | NSWindowCollectionBehaviorFullScreenAuxiliary
     | NSWindowCollectionBehaviorStationary),
    ("screensaver + joinAll + fullScreenAux + stationary + transient",
     NSScreenSaverWindowLevel,
     NSWindowCollectionBehaviorCanJoinAllSpaces
     | NSWindowCollectionBehaviorFullScreenAuxiliary
     | NSWindowCollectionBehaviorStationary
     | NSWindowCollectionBehaviorTransient
     | NSWindowCollectionBehaviorIgnoresCycle),
]


class ProbePanel(QWidget):
    """A deliberately garish rectangle. No Loom code, no Loom flags."""

    def __init__(self):
        super().__init__()
        # The same shape Loom's overlay asks for, minus the X11-specific
        # ToolTip choice, which exists to beat KWin's active layer and means
        # nothing here.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # Never take focus. Taking it would background the game, and macOS
        # stops compositing a backgrounded window - which is the failure this
        # whole probe exists to avoid.
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.resize(400, 120)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(*reversed(PROBE_BGR)))


def ns_window(widget):
    """The NSWindow behind a Qt widget.

    On macOS Qt's winId() is an NSView*, not a window, so the window has to be
    asked for. objc_object wraps the raw pointer back into something pyobjc
    will talk to.
    """
    view = objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId())))
    return view.window()


def apply_settings(widget, level, behaviour):
    """Put one combination of level and collection behaviour on the window."""
    window = ns_window(widget)
    if window is None:
        return False
    window.setLevel_(level)
    window.setCollectionBehavior_(behaviour)
    # Ordering front without activating is the whole trick: orderFrontRegardless
    # raises the window without making its application active, so the game
    # keeps the foreground and keeps being composited.
    window.orderFrontRegardless()
    return True


def game_application():
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == backend.GAME_BUNDLE_ID:
            return app
    return None


def capture_display():
    """The whole screen the game is on, as BGR - panel included if visible.

    Capturing the DISPLAY rather than the game's window is the point: a window
    capture would never contain the overlay, since the overlay is a different
    window. Only the composited desktop can answer "is the panel on top?".
    """
    content = backend._shareable_content()
    window = backend._pick_game_window(list(content.windows()))
    if window is None:
        return None, "the game window is not there"

    frame = window.frame()
    centre_x = frame.origin.x + frame.size.width / 2
    centre_y = frame.origin.y + frame.size.height / 2
    display = None
    for candidate in content.displays():
        bounds = candidate.frame()
        if (bounds.origin.x <= centre_x < bounds.origin.x + bounds.size.width
                and bounds.origin.y <= centre_y
                < bounds.origin.y + bounds.size.height):
            display = candidate
            break
    if display is None:
        return None, "could not tell which display the game is on"

    content_filter = (SCK.SCContentFilter.alloc()
                      .initWithDisplay_excludingWindows_(display, []))
    configuration = SCK.SCStreamConfiguration.alloc().init()
    configuration.setWidth_(display.width())
    configuration.setHeight_(display.height())
    configuration.setShowsCursor_(False)
    configuration.setPixelFormat_(Quartz.kCVPixelFormatType_32BGRA)

    result = backend._wait_async(
        lambda handler: SCK.SCScreenshotManager
        .captureImageWithFilter_configuration_completionHandler_(
            content_filter, configuration, handler))
    if result is None:
        return None, "the display capture never came back"
    image, error = result
    if error is not None:
        return None, f"the display capture failed: {error}"
    return _cgimage_to_bgr(image), None


def _cgimage_to_bgr(image):
    width = Quartz.CGImageGetWidth(image)
    height = Quartz.CGImageGetHeight(image)
    buffer = (ctypes.c_uint8 * (width * height * 4))()
    context = Quartz.CGBitmapContextCreate(
        buffer, width, height, 8, width * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(),
        Quartz.kCGImageAlphaPremultipliedFirst
        | Quartz.kCGBitmapByteOrder32Little)
    Quartz.CGContextDrawImage(
        context, Quartz.CGRectMake(0, 0, width, height), image)
    pixels = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 4)
    return pixels[:, :, :3].copy()


def count_probe_pixels(frame):
    """How many pixels of the panel's colour are on screen."""
    target = np.array(PROBE_BGR, dtype=np.int16)
    distance = np.abs(frame.astype(np.int16) - target).max(axis=2)
    return int((distance <= PROBE_TOLERANCE).sum())


def game_capture_still_works():
    """Can Loom still read the game while the panel is up?

    The half of the question that is easy to forget. A panel that shows itself
    by stealing the foreground has cost Loom the HUD.
    """
    try:
        session = backend.open_display()
        window = backend.find_game_window(session)
        if window is None:
            return False, "no game window"
        frame = backend.capture_window(window)
        if frame.max() == 0:
            return False, "frames stopped (window not composited)"
        return True, f"{frame.shape[1]}x{frame.shape[0]}"
    except Exception as problem:            # a probe reports, never crashes
        return False, str(problem)


def pump(app, seconds):
    """Let Qt draw, without handing it the program."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def try_combination(app, panel, name, level, behaviour, save_as=None):
    """Apply one combination and report what macOS did with it.

    Two measurements, and the first one is what makes the second mean
    anything. Before the game is brought forward, the panel should be plainly
    visible on an ordinary desktop; that is the CONTROL. Without it, a panel
    that simply failed to render would look exactly like macOS refusing to put
    it over the game, and the wrong conclusion is the easy one to draw.
    """
    print(f"\n--- {name}")
    if not apply_settings(panel, level, behaviour):
        print("    could not reach the NSWindow")
        return False
    pump(app, 1.0)

    control, problem = capture_display()
    if control is None:
        print(f"    display capture failed: {problem}")
        return False
    control_pixels = count_probe_pixels(control)
    if control_pixels < VISIBLE_PIXELS:
        print(f"    CONTROL FAILED: only {control_pixels} panel pixels before "
              "the game was even raised.")
        print("    The panel is not rendering, so this run says nothing about "
              "full screen.")
        return False
    print(f"    control (game not raised): {control_pixels} panel pixels - "
          "panel is on screen")

    game = game_application()
    if game is not None and not game.isActive():
        game.activateWithOptions_(0)
    pump(app, 2.5)
    # Order front again AFTER the Space switch, in case joining a Space needs
    # asking twice. Measured: it does not help, but a probe that never tried
    # would leave the doubt.
    window = ns_window(panel)
    if window is not None:
        window.orderFrontRegardless()
    pump(app, 1.5)

    frame, problem = capture_display()
    if frame is None:
        print(f"    display capture failed: {problem}")
        return False

    pixels = count_probe_pixels(frame)
    visible = pixels >= VISIBLE_PIXELS
    game_active = game.isActive() if game is not None else False
    readable, detail = game_capture_still_works()

    print(f"    with the game up:       {pixels} panel pixels  ->  "
          f"{'VISIBLE' if visible else 'not visible'}")
    print(f"    game still frontmost:   {game_active}")
    print(f"    game still capturable:  {readable} ({detail})")
    if window is not None:
        # macOS can claim the window is on the active Space while drawing
        # none of it, which is worth seeing rather than trusting.
        print(f"    NSWindow says: isVisible={window.isVisible()} "
              f"isOnActiveSpace={window.isOnActiveSpace()}")

    if save_as:
        os.makedirs(paths.CAPTURES_DIR, exist_ok=True)
        path = os.path.join(paths.CAPTURES_DIR, save_as)
        cv2.imwrite(path, cv2.resize(frame, None, fx=0.35, fy=0.35))
        print(f"    saved {path}")

    if visible and readable:
        print("    RESULT: this combination WORKS - panel over the game, "
              "HUD still readable.")
    elif visible and not readable:
        print("    RESULT: panel shows but the capture died - it took the "
              "foreground from the game.")
    return visible and readable


def main():
    parser = argparse.ArgumentParser(
        description="Can a Qt overlay float over a fullscreen macOS game?")
    parser.add_argument("--sweep", action="store_true",
                        help="try every combination, not just the best guess")
    parser.add_argument("--hold", type=float, default=0.0,
                        help="seconds to leave the panel up at the end, to look at")
    args = parser.parse_args()

    if not Quartz.CGPreflightScreenCaptureAccess():
        print("Screen Recording permission is not granted; the probe cannot "
              "measure visibility.")
        return 1

    if game_application() is None:
        print("The game is not running. Start it, put it in FULL SCREEN, and "
              "run this again -\nfullscreen is the case worth testing; "
              "windowed already works.")
        return 1

    app = QApplication(sys.argv)
    # Accessory, so showing a panel never makes Loom the active application.
    NSApplication.sharedApplication().setActivationPolicy_(1)

    panel = ProbePanel()
    screen = app.primaryScreen().geometry()
    panel.move(screen.x() + screen.width() // 2 - 200, screen.y() + 120)
    panel.show()
    pump(app, 0.5)

    combinations = COMBINATIONS if args.sweep else COMBINATIONS[-1:]
    worked = []
    for index, (name, level, behaviour) in enumerate(combinations):
        if try_combination(app, panel, name, level, behaviour,
                           save_as=f"overlay_probe_{index}.png"):
            worked.append(name)

    print("\n" + "=" * 68)
    if worked:
        print("Combinations that put the panel over the game AND left the "
              "HUD readable:")
        for name in worked:
            print(f"  * {name}")
    else:
        print("Nothing worked. If the game was genuinely full screen, that is "
              "a real answer:\nmacOS is not letting a Qt window join its "
              "Space, and windowed play stays the\nonly supported mode for "
              "now. Check the saved screenshots to confirm.")

    if args.hold:
        print(f"\nHolding the panel up for {args.hold:g}s...")
        pump(app, args.hold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
