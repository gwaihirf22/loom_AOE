"""
Loom — macOS capture probe (development tool).

Goal: answer the questions that decide the whole macOS port, before any of
Loom's own modules are restructured.

    python -m tools.macos_probe                 # find the game, capture, report
    python -m tools.macos_probe --native        # no downscale: capture raw pixels
    python -m tools.macos_probe --list          # just enumerate windows
    python -m tools.macos_probe --fragment Chess  # aim at some other window

The questions, in the order it answers them:

  1. Will macOS let this process capture at all? Screen Recording is a TCC
     permission granted to whichever binary launched Python - a terminal, or
     an IDE - never to Python itself.
  2. Can the game's window be found, and by what? Feral's port is
     com.feralinteractive.ageofempires2, and a bundle id is exact where a
     window title is a guess.
  3. What pixel scale comes back? This Mac's display is 2x, and Loom's anchor
     search only looks between 0.5x and 2.0x. A native-resolution capture
     therefore lands on the very edge of what anchor.py can find, which is the
     single biggest risk in the port. --native exists to see that failure
     happen rather than take my word for it.
  4. Do the existing Linux-cut templates still match the Feral HUD, and how
     well? That decides whether templates have to be re-cut.

Nothing here is imported by Loom. It is a throwaway diagnostic that earns its
place by making stage 2 a port of something already proven, rather than a
guess I would have to debug through three layers at once.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import ctypes
import os
import statistics
import sys
import time

import cv2
import numpy as np

import objc
import Quartz
from AppKit import NSApplication, NSScreen, NSWorkspace
from Foundation import NSRunLoop, NSDate
import ScreenCaptureKit as SCK

from loom import anchor, digits, hud, notifications, paths, queue, reader

# Teach pyobjc what the screenshot callback actually hands back.
#
# Without this, the CGImageRef argument arrives as an untyped PyObjCPointer:
# the call "succeeds", and then CGImageGetWidth reads the wrong memory and
# reports an image 27 pixels wide by 18377782700456017919 tall. Declaring the
# argument as an object (@) makes pyobjc bridge it as a real CGImageRef, and
# the same call then reports 1920x1080. A silent wrong answer from a capture
# is precisely the failure Loom is built to refuse, so this is not optional
# tidying - it is the difference between pixels and nonsense.
objc.registerMetaDataForSelector(
    b"SCScreenshotManager",
    b"captureImageWithFilter:configuration:completionHandler:",
    {"arguments": {4: {"callable": {
        "retval": {"type": b"v"},
        "arguments": {0: {"type": b"^v"}, 1: {"type": b"@"}, 2: {"type": b"@"}},
    }}}},
)

# Connect to the window server before touching ScreenCaptureKit.
#
# SCContentFilter aborts the whole process with
#   Assertion failed: (did_initialize), function CGS_REQUIRE_INIT
# when it is built from a plain command-line process, because CoreGraphics
# has no connection to the window server until something asks for one.
# sharedApplication() is what establishes it. Loom's overlay gets this free
# from Qt, but loom_read.py is a bare terminal program, so the capture path
# cannot assume somebody else has already done it.
#
# Policy 2 is NSApplicationActivationPolicyProhibited: no Dock icon, no menu
# bar, no stealing focus from the game. A diagnostic that made the game lose
# focus every time it ran would be its own bug.
NSApplication.sharedApplication().setActivationPolicy_(2)

# Feral Interactive's macOS port. The bundle id is stable across updates and
# unambiguous; the window title is neither, so it is only the fallback.
GAME_BUNDLE_ID = "com.feralinteractive.ageofempires2"

# Deliberately lower-cased and compared case-insensitively: Loom's X11 reader
# looks for "Age of Empires II", and Feral capitalises it "Age Of Empires II".
# A case-sensitive substring match would miss the game entirely.
GAME_NAME_FRAGMENT = "age of empires"

# Below this mean brightness a frame is not a reading, it is a refusal - macOS
# hands back black rather than erroring when the capture is not permitted.
# Same threshold tools/capture_smoketest.py uses on Linux.
BLACK_FRAME_BRIGHTNESS = 1.0

# How long to let an async ScreenCaptureKit call finish before giving up.
CALLBACK_TIMEOUT = 5.0


def wait_for_callback(box, timeout=CALLBACK_TIMEOUT):
    """Pump the run loop until an async ScreenCaptureKit callback has fired.

    Every SCK entry point is asynchronous and delivers its answer on a
    dispatch queue. A plain sleep would deadlock: the callback needs the main
    run loop to be running to be delivered at all. So I spin the run loop in
    short slices and watch a one-element list the handler writes into.

    Returns True if the callback fired, False if the timeout passed.
    """
    deadline = time.monotonic() + timeout
    while not box and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(
            NSDate.dateWithTimeIntervalSinceNow_(0.01))
    return bool(box)


def host_binary():
    """Which application macOS will attach the Screen Recording grant to.

    The grant follows the process that launched Python, not Python, so naming
    it is the difference between an actionable message and a baffling one.
    macOS sets __CFBundleIdentifier for processes launched from a bundled app,
    which is how a terminal inside an IDE is told apart from Terminal.app.

    The bundle id alone is useless to a human - "com.todesktop.230313mzl4w4u92"
    is Cursor - so I ask LaunchServices for the app it belongs to and report
    the name that will actually appear in System Settings.
    """
    bundle_id = os.environ.get("__CFBundleIdentifier", "")
    if not bundle_id:
        return f"the app running {sys.executable}"

    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(
        bundle_id)
    if url is None:
        return bundle_id
    return f"{url.lastPathComponent()} ({bundle_id})"


def check_permission():
    """Has this process been granted Screen Recording? Returns True/False.

    CGPreflightScreenCaptureAccess asks without prompting, so a refusal can be
    reported clearly instead of the user meeting a system dialog with no
    explanation attached to it.
    """
    if Quartz.CGPreflightScreenCaptureAccess():
        return True

    print("Screen Recording permission is NOT granted.")
    print(f"  macOS attaches this grant to: {host_binary()}")
    print("  Grant it in System Settings > Privacy & Security > Screen &")
    print("  System Audio Recording, then RESTART that app - the permission")
    print("  is only re-read at launch.")
    print()
    print("Asking macOS to prompt now...")
    # This raises the system dialog the first time only; afterwards it is a
    # silent no, which is why the instructions above are printed regardless.
    Quartz.CGRequestScreenCaptureAccess()
    return False


def shareable_content():
    """Every capturable window macOS is willing to tell this process about.

    Returns an SCShareableContent, or None.

    onScreenWindowsOnly is FALSE, and that is the whole reason this function
    has a comment. "On screen" means on the ACTIVE Space, and a game running
    full screen on macOS gets a Space of its own - so with the flag set true
    the game is not merely at the bottom of the list, it is absent entirely.
    Measured: 46 windows and no game with it on, 393 windows and all eight of
    the game's with it off. The cost is a much longer list to search, which is
    a search problem; the flag was a correctness problem.
    """
    box = []
    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        True, False, lambda content, error: box.append((content, error)))

    if not wait_for_callback(box):
        print("error: ScreenCaptureKit never answered - no window list.")
        return None

    content, error = box[0]
    if error is not None:
        print(f"error: ScreenCaptureKit refused: {error}")
        return None
    return content


def describe(window):
    """One window as a line of text, for the listing."""
    app = window.owningApplication()
    bundle = app.bundleIdentifier() if app else "?"
    name = app.applicationName() if app else "?"
    frame = window.frame()
    return (f"  [{window.windowID():>6}] {name:<28.28} {bundle:<44.44} "
            f"{int(frame.size.width):>5}x{int(frame.size.height):<5} "
            f"title={window.title()!r}")


def window_area(window):
    """A window's area in square points, for picking the biggest."""
    size = window.frame().size
    return size.width * size.height


def largest(windows):
    """The biggest of several windows, or None if there are none.

    The game owns EIGHT windows: the playfield plus a 500x500, a 312x237, and
    five 1728x33 slivers. Only one of them has the HUD on it, and it is by far
    the largest - 1728x1084 against 1728x33 for the runners-up. Area is
    therefore a decisive test where "the first one returned" is a coin flip,
    since the list comes back in no documented order.
    """
    return max(windows, key=window_area) if windows else None


def find_game_window(content, fragment=None):
    """The game's playfield window, by bundle id first and title second.

    Bundle id is an exact identity that survives the game being renamed or
    localised, so it is tried first and its matches are narrowed by area. The
    fragment path exists for aiming the probe at some other window to test the
    pipeline with no game running.
    """
    windows = list(content.windows())

    if fragment is None:
        owned = [w for w in windows
                 if w.owningApplication()
                 and w.owningApplication().bundleIdentifier() == GAME_BUNDLE_ID]
        if owned:
            return largest(owned)

    # Matched case-insensitively on purpose. Loom's X11 reader looks for
    # "Age of Empires II"; the Mac app's own name capitalises it differently
    # in places, and a case-sensitive substring would miss the game outright.
    needle = (fragment or GAME_NAME_FRAGMENT).lower()
    matches = []
    for window in windows:
        app = window.owningApplication()
        haystacks = [window.title() or ""]
        if app:
            haystacks.append(app.applicationName() or "")
        if any(needle in text.lower() for text in haystacks):
            matches.append(window)
    return largest(matches)


def image_to_bgr(image):
    """A CGImage as a BGR numpy array, which is what OpenCV works in.

    CoreGraphics will not hand over its pixels directly, so I draw the image
    into a bitmap context whose memory I own. kCGImageAlphaPremultipliedFirst
    with kCGBitmapByteOrder32Little is the incantation for BGRA byte order on
    a little-endian machine - the same layout X11's ZPixmap gives on Linux,
    which is why the rest of Loom needs no idea where the frame came from.
    """
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
    # Drop alpha, leaving BGR.
    return pixels[:, :, :3].copy()


# ScreenCaptureKit's "failed to start" error. It is what comes back when the
# target window is not being composited - the game sitting behind a maximised
# window, or on a Space that is not the active one.
SC_ERROR_FAILED_TO_START = -3811


class CaptureRefused(RuntimeError):
    """macOS declined to capture, with a reason worth printing."""


def resolve_size(spec, frame):
    """Turn a --size spec into (width, height) in pixels, or None for default.

    None means "do not call setWidth_/setHeight_ at all", which leaves
    ScreenCaptureKit to pick - worth measuring separately, because its choice
    may already be the game's own render target.

    The specs, and why each is worth a measurement:
      points  the window's size in POINTS. On this 2x display that is HALF the
              backing store, so SCK downscales the game's render before Loom
              ever sees it. This was the accidental default and it shrank every
              glyph; it stays only as the control.
      native  the backing store, points x the display's scale factor. The
              honest "what the window actually is" answer.
      2x      shorthand for twice the point size.
      WxH     an explicit size, for asking about the game's own render
              resolution (1920x1200 here) which matches neither of the above.
    """
    width, height = int(frame.size.width), int(frame.size.height)

    if spec in (None, "default"):
        return None
    if spec == "points":
        return width, height
    if spec == "2x":
        return width * 2, height * 2
    if spec == "native":
        # NSScreen's backingScaleFactor is the points-to-pixels ratio; asking
        # the screen rather than assuming 2 keeps this right on a 1x display
        # or a mixed-DPI desktop.
        screens = NSScreen.screens()
        scale = max((s.backingScaleFactor() for s in screens), default=1.0)
        return int(width * scale), int(height * scale)
    if "x" in spec.lower():
        parts = spec.lower().split("x")
        return int(parts[0]), int(parts[1])
    raise ValueError(f"unrecognised --size {spec!r}")


def capture_once(window, size=None):
    """Capture one window and return (bgr_image, point_size).

    size is (width, height) in pixels, or None to let ScreenCaptureKit choose.
    Nothing here assumes a relationship between the window's point size and
    the pixels the game renders: on this machine the window is 1728x1084
    points, the backing store is 3456x2168, and the game renders 1920x1200 -
    three different numbers, and asking for the wrong one silently resamples
    the HUD before any of Loom's readers see it.
    """
    frame = window.frame()
    filter_ = SCK.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)

    config = SCK.SCStreamConfiguration.alloc().init()
    if size is not None:
        config.setWidth_(int(size[0]))
        config.setHeight_(int(size[1]))
    # The cursor is not part of the HUD and would sit on top of the numbers.
    config.setShowsCursor_(False)

    box = []
    SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
        filter_, config, lambda image, error: box.append((image, error)))

    if not wait_for_callback(box):
        raise CaptureRefused("ScreenCaptureKit never delivered a frame")

    image, error = box[0]
    if error is not None:
        if error.code() == SC_ERROR_FAILED_TO_START:
            raise CaptureRefused(
                "macOS refused to capture this window (SCStream -3811).\n"
                "  The window is not being composited right now. Measured "
                "cause: the game\n"
                "  is behind another window or on an inactive Space. Bring "
                "the game to the\n"
                "  front and run this again - capture starts working the "
                "moment it is visible.")
        raise CaptureRefused(f"capture failed: {error}")
    if image is None:
        raise CaptureRefused("capture returned no image")

    return image_to_bgr(image), (frame.size.width, frame.size.height)


def report_anchor(image):
    """Try Loom's own HUD anchor against this frame and say how it went.

    Returns the locate_regions dict, or None. This is the question templates
    cut on Linux cannot answer in advance: the Feral port draws the same game,
    but nothing guarantees the population icon is pixel-identical after a
    different renderer and a different scaler.
    """
    try:
        templates = {profile: anchor.load_template(profile)
                     for profile in hud.PROFILES}
        wood = {profile: queue.load_wood_template(profile)
                for profile in hud.PROFILES}
    except FileNotFoundError as missing:
        print(f"anchor: {missing} - cannot test the HUD.")
        return None

    # Every HUD skin gets a try, so a weak score means "none of the art Loom
    # knows fits this" rather than "the one skin it knew did not".
    # identify_hud keeps the derived read regions, so the thirty-one-scale
    # sweep happens once here and the debug image reuses it.
    found = anchor.identify_hud(image, templates,
                                wood_templates=wood)
    if found is None:
        print("anchor: NO MATCH at all.")
        return None

    score, scale = found["score"], found["scale"]
    verdict = "good" if score >= 0.8 else "WEAK - templates likely need re-cutting"
    print(f"anchor: hud={found['profile'].name} score={score:.3f} "
          f"scale={scale:.2f} at {found['icon'][:2]}  [{verdict}]")

    if scale >= 1.95:
        print("        scale is at the top of anchor.COARSE_SCALES (2.0) - the")
        print("        search cannot refine past it. Capture downscaled.")
    return found


def save(image, stem, found=None):
    """Write the frame, and a debug copy with the anchor drawn on, to captures/."""
    os.makedirs(paths.CAPTURES_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    path = os.path.join(paths.CAPTURES_DIR, f"{stem}_{stamp}.png")
    cv2.imwrite(path, image)
    print(f"saved: {path}")

    if found is not None:
        marked = anchor.draw_debug(image, found)
        debug_path = os.path.join(paths.CAPTURES_DIR, f"{stem}_{stamp}_debug.png")
        cv2.imwrite(debug_path, marked)
        print(f"saved: {debug_path}  (green box should sit on the pop icon)")


# How tall the HUD's glyphs have to be before Loom can read them.
#
# Measured, not guessed. The statistic is the 90th percentile of connected
# component heights in the top strip - robust to the antialiasing specks and
# the occasional fused blob that either tail would be distorted by:
#
#   tests/data/clock/live_dark_bar_1295.png   p90 = 15px   reads correctly
#   this Mac, 1728x1084                       p90 =  8px   reads nothing
#   this Mac, 1920x1080                       p90 =  7px   reads nothing
#
# The digit templates are 14x20, so a 15px glyph normalises nearly 1:1 while
# an 8px one is being invented on the way up. Below the threshold a "0" loses
# its top and bottom arcs and splits into two slivers while its neighbours
# fuse, so the clock reads nothing however well the band is placed. 13 sits in
# open water between the two measured populations.
GOOD_GLYPH_HEIGHT = 13
REFERENCE_GLYPH_HEIGHT = 15

# Components smaller than this are antialiasing specks, not characters.
MIN_GLYPH_AREA = 6


def glyph_heights(image):
    """Heights of the character-like shapes across the top-centre strip.

    Uses the outline test from notifications.text_line_bands - a text pixel is
    a bright pixel NEXT TO a very dark one - rather than plain brightness. The
    top bar is ornate carved chrome every bit as bright as the font, so a
    brightness gate measures the frame's woodwork and returns a comfortable
    answer that has nothing to do with the text. Measured: the gate reported
    38px "text" on a frame whose glyphs are 8px.
    """
    height, width = image.shape[:2]
    region = image[0:int(height * 0.06), int(width * 0.25):int(width * 0.85)]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)

    bright = (gray > notifications.TEXT_BRIGHT).astype(np.uint8)
    dark = (gray < notifications.TEXT_OUTLINE_DARK).astype(np.uint8)
    # Dilating the dark mask is what makes this "next to" rather than "on top
    # of": the font's outline surrounds each stroke instead of sharing pixels
    # with it.
    near_dark = cv2.dilate(dark, np.ones((3, 3), np.uint8))
    mask = (bright & near_dark).astype(np.uint8) * 255

    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return sorted(int(stats[i][cv2.CC_STAT_HEIGHT]) for i in range(1, count)
                  if stats[i][cv2.CC_STAT_AREA] >= MIN_GLYPH_AREA)


def report_text_scale(image):
    """Is the in-game HUD big enough for Loom to read? Returns True/False.

    This is the check that would have saved a long hunt. The clock band can be
    perfectly placed and still read nothing, because the glyphs themselves are
    below the size the digit templates can classify. Where the clock IS and
    whether it is legible are separate problems, and this one is settled in
    the game's settings rather than in Loom.
    """
    heights = glyph_heights(image)
    if not heights:
        print("hud text: found no character shapes in the top strip.")
        return False

    p90 = int(np.percentile(heights, 90))
    print(f"hud text: {len(heights)} character shapes, 90th-percentile height "
          f"{p90}px (a frame that reads correctly measures "
          f"{REFERENCE_GLYPH_HEIGHT}px)")

    if p90 >= GOOD_GLYPH_HEIGHT:
        return True

    print()
    print(f"  PROBLEM: the HUD glyphs are {p90}px and Loom needs about "
          f"{GOOD_GLYPH_HEIGHT}px.")
    print("  At this size a '0' loses its top and bottom arcs and splits into two")
    print("  slivers while its neighbours fuse, so the clock reads nothing however")
    print("  well the band is placed. Upscaling cannot restore detail the frame")
    print("  never captured - this one is settled in the game, not in Loom.")
    print()
    print("  FIX: raise the in-game HUD/interface scale to roughly "
          f"{REFERENCE_GLYPH_HEIGHT / max(p90, 1):.1f}x its current setting,")
    print("  then re-run this probe.")
    return False


def report_readings(image, found):
    """Actually try to read the three numbers. Returns {name: bool}.

    The whole point of the exercise. An anchor score and a glyph height are
    proxies; whether digits.read_* returns a value is the thing itself, and
    proxies have already misled me twice. This runs the same calls
    reader.poll() makes, against the same regions, so a pass here means the
    real pipeline would pass.
    """
    templates = digits.load_digit_templates()
    # Ask the runtime for this rather than recomputing it. A probe that works
    # out its own width is not testing Loom, it is testing a lookalike - which
    # is precisely how a broken formula survived: tests/test_clock_themes.py
    # hands in hand-picked widths, so the live path drifted without failing.
    min_glyph_width = reader.min_glyph_width(found["scale"])
    height, width = image.shape[:2]

    def crop(box):
        x1, y1, x2, y2 = box
        return image[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]

    villagers, vill_score = digits.read_count(
        crop(found["villagers"]), templates, min_glyph_width)
    population = digits.read_population(
        crop(found["population"]), templates, min_glyph_width)
    clock, clock_score = digits.read_clock_seconds(
        crop(found["clock_band"]), templates, min_glyph_width)

    print(f"  min_glyph_width {min_glyph_width} (from anchor scale)")
    print(f"  villagers  -> {_verdict(villagers)}"
          f"{f'  score {vill_score:.2f}' if villagers is not None else ''}")
    print(f"  population -> {_verdict(population[0] is not None and population)}")
    print(f"  clock      -> "
          f"{_verdict(clock if clock is None else format_clock(clock))}"
          f"{f'  score {clock_score:.2f}' if clock is not None else ''}")

    return {"villagers": villagers is not None,
            "population": population[0] is not None,
            "clock": clock is not None}


def _verdict(value):
    """A reading as text: the value, or a loud NO READING."""
    return "NO READING" if value in (None, False) else str(value)


def _mark(ok):
    """One summary-table cell. "NO" shouts; "yes" does not."""
    return "yes" if ok else "NO"


def format_clock(seconds):
    """Total seconds as HH:MM:SS, so a wrong clock is obvious at a glance."""
    return (f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"
            f":{seconds % 60:02d}  ({seconds}s)")


def save_corpus_frame(image, label):
    """Keep a full frame as a test fixture, named for the setup it shows.

    tests/data holds only crops today, which is why a resolution assumption
    could sit in anchor.py unnoticed for so long: nothing in the suite has ever
    seen a whole HUD at a second scale.

    The label matters as much as the frame size, and this refuses to guess it.
    Frame size alone does NOT identify a HUD: the same 1728x1084 window scored
    0.79 on the anchor with the stock resource panel and 0.97 with the Anne_HK
    Better Resource Panel mod, because the templates were cut from the modded
    art. Two fixtures differing only by a mod, saved under one name, silently
    overwrite each other - which is exactly what happened once before this
    argument was made to carry the setup.
    """
    frames_dir = os.path.join(paths.PROJECT_ROOT, "tests", "data", "frames")
    os.makedirs(frames_dir, exist_ok=True)

    height, width = image.shape[:2]
    path = os.path.join(frames_dir, f"hud_{width}x{height}_{label}.png")
    if os.path.exists(path):
        print(f"corpus: WARNING - overwriting {os.path.basename(path)}")
    cv2.imwrite(path, image)
    print(f"corpus: wrote {path}")
    return path


def time_captures(window, size, rounds=20):
    """How long a capture takes, median of several - Loom polls every 300ms."""
    times = []
    for _ in range(rounds):
        start = time.monotonic()
        capture_once(window, size=size)
        times.append((time.monotonic() - start) * 1000)

    median = statistics.median(times)
    budget = "fits the 300ms poll" if median < 150 else "TOO SLOW for a 300ms poll"
    print(f"timing: median {median:.0f}ms over {rounds} captures "
          f"(min {min(times):.0f}, max {max(times):.0f})  [{budget}]")


def activate_game():
    """Bring the game to the front, because macOS will not capture it otherwise.

    A window that is not composited cannot be captured (see
    SC_ERROR_FAILED_TO_START). During a real match the game IS the front
    window, so this is only papering over the fact that running a probe from a
    terminal necessarily puts something else in front.
    """
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == GAME_BUNDLE_ID:
            app.activateWithOptions_(0)
            # The window server needs a moment to composite the newly frontmost
            # window; capturing immediately still gets the -3811 refusal.
            time.sleep(2.0)
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Loom macOS capture probe")
    parser.add_argument("--list", action="store_true",
                        help="list capturable windows and stop")
    parser.add_argument("--size", action="append", default=None,
                        metavar="SPEC",
                        help="capture size: default, points, native, 2x, or "
                             "WxH. Repeat to compare several in one run; "
                             "omitted means sweep default/points/native.")
    parser.add_argument("--no-activate", action="store_true",
                        help="do not bring the game to the front first "
                             "(capture will fail unless it already is)")
    parser.add_argument("--fragment", default=None,
                        help="match a window by title fragment instead of the "
                             "game's bundle id")
    parser.add_argument("--rounds", type=int, default=20,
                        help="how many captures to time (0 to skip)")
    parser.add_argument("--save-frame", metavar="LABEL", default=None,
                        help="also keep this frame as a test fixture under "
                             "tests/data/frames/, labelled with the setup it "
                             "shows - mod, HUD slider, game resolution. "
                             "e.g. --save-frame annehk_slider100_1920x1200")
    args = parser.parse_args()

    if not check_permission():
        return 1

    print("Screen Recording: granted.")

    content = shareable_content()
    if content is None:
        return 1

    windows = list(content.windows())
    print(f"ScreenCaptureKit lists {len(windows)} capturable windows.")

    if args.list:
        for window in windows:
            print(describe(window))
        return 0

    if not windows:
        # An empty list from a process that passed preflight means the grant
        # is stale: it was given to an earlier launch of the host app.
        print("error: zero windows. The permission is probably stale - restart "
              f"{host_binary()} so it re-reads the grant.")
        return 1

    window = find_game_window(content, args.fragment)
    if window is None:
        target = args.fragment or f"bundle id {GAME_BUNDLE_ID}"
        print(f"Could not find a window matching {target}. Is the game running?")
        print("Run with --list to see what is capturable.")
        return 1

    print("Found the target window:")
    print(describe(window))

    if not args.no_activate and args.fragment is None:
        print("Bringing the game to the front (macOS will not capture a "
              "window it is not compositing)...")
        activate_game()

    # Sweeping several sizes in ONE run matters: the HUD is live, so comparing
    # runs taken minutes apart compares different game states as much as
    # different capture sizes.
    specs = args.size or ["default", "points", "native"]
    summary = []

    for spec in specs:
        print()
        print("=" * 68)
        try:
            size = resolve_size(spec, window.frame())
            image, points = capture_once(window, size=size)
        except (CaptureRefused, ValueError) as problem:
            print(f"--- size {spec}: {problem}")
            continue

        height, width = image.shape[:2]
        brightness = float(image.mean())
        asked = "SCK's choice" if size is None else f"{size[0]}x{size[1]}"
        print(f"--- size {spec!r} (asked for {asked})")
        print(f"    window {points[0]:.0f}x{points[1]:.0f} points  ->  "
              f"captured {width}x{height}  "
              f"({width / points[0]:.3f}x the point size)")
        print(f"    mean brightness {brightness:.1f}")

        if brightness < BLACK_FRAME_BRIGHTNESS:
            # This is the failure the whole permission dance exists to catch,
            # and it must never be mistaken for "the HUD is not visible".
            print("    BLACK FRAME - a refusal, not a reading. Restart "
                  f"{host_binary()} after granting permission.")
            continue

        found = report_anchor(image)
        report_text_scale(image)

        if found is None:
            print("    no anchor, so no regions to read from.")
            summary.append((spec, f"{width}x{height}", None, None))
            continue

        reads = report_readings(image, found)
        summary.append((spec, f"{width}x{height}", found, reads))
        save(image, f"macos_probe_{spec.replace('x', '_')}", found)

    print()
    print("=" * 68)
    print("SUMMARY - which capture size can Loom actually read?")
    print(f"  {'size':<10} {'pixels':<12} {'anchor':<8} {'vill':<6} "
          f"{'pop':<6} {'clock':<6}")
    for spec, pixels, found, reads in summary:
        if reads is None:
            print(f"  {spec:<10} {pixels:<12} {'-':<8} {'-':<6} {'-':<6} {'-':<6}")
            continue
        print(f"  {spec:<10} {pixels:<12} {found['score']:<8.3f} "
              f"{_mark(reads['villagers']):<6} {_mark(reads['population']):<6} "
              f"{_mark(reads['clock']):<6}")

    if args.save_frame is not None and summary:
        # The last size swept is the frame still in hand; label carries which.
        save_corpus_frame(image, f"{args.save_frame}_{specs[-1]}")

    if args.rounds and summary:
        print()
        time_captures(window, resolve_size(specs[-1], window.frame()),
                      rounds=args.rounds)

    return 0


if __name__ == "__main__":
    sys.exit(main())
