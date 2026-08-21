"""
Loom — Windows capture probe (development tool).

Goal: answer the questions that decide the whole Windows port, before any of
Loom's own modules gain a Windows branch.

    python -m tools.windows_probe                  # find the game, try everything
    python -m tools.windows_probe --list           # just enumerate windows
    python -m tools.windows_probe --fragment Note  # aim at some other window
    python -m tools.windows_probe --save           # write frames to captures/
    python -m tools.windows_probe --background 6   # capture while NOT frontmost

The questions, in the order it answers them:

  1. Which of the candidate paths returns REAL pixels from a Direct3D game
     window? This is the one that decides everything else. BitBlt and
     PrintWindow are the cheap, pull-shaped option that would fit x11.py's
     shape exactly - and they classically hand back a black rectangle for a
     D3D swap chain, which is precisely what AoE2:DE is. Windows Graphics
     Capture goes through the compositor and should not care. "Should" is
     what the macOS notes said about window levels too, so this measures it.

  2. Does capture keep working when the game is NOT the foreground window?
     That is macOS's worst limitation - the game must be frontmost or frames
     simply stop - and it is the single biggest thing Windows could do better.
     --background answers it: it counts down, I click away, and it captures.

  3. What size do the pixels come back at, and does that match what Qt will
     think the window is? Windows display scaling makes "pixels" and "points"
     two different numbers, exactly as macOS did. Loom's contract already
     separates window_size (capture pixels) from window_geometry (Qt points);
     this reports both and the DPI that relates them, because getting it
     backwards would silently shift the anchor scale and degrade digit
     recognition instead of failing loudly.

  4. Do the existing templates match the Windows HUD, and at what scale? The
     templates were cut on Linux under Proton, which renders through the same
     engine, so they SHOULD match - but a HUD found at 1.4x rather than 1.0x
     means the display-scaling question above was answered wrong.

Nothing here is imported by Loom. It is a throwaway diagnostic that earns its
place by making the backend a port of something already proven, rather than a
guess I would otherwise have to debug through three layers at once.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import ctypes
import re
import statistics
import sys
import time

import numpy as np

from loom import anchor, hud, paths, queue

WINDOW_NAME_FRAGMENT = "Age of Empires II"

# How many samples to time each path over. Small: this is a shape-of-the-
# answer measurement, not a benchmark.
SAMPLES = 20

# Below this fraction of non-zero bytes I call a frame black. Not zero,
# because a real capture of a dark loading screen is not all zeroes either,
# and a "black" frame from a failed D3D grab is exactly 0.0%.
REAL_PIXEL_FRACTION = 0.05


# ---------------------------------------------------------------------------
# DPI. This must happen before ANY window rectangle is asked for.
# ---------------------------------------------------------------------------

def become_dpi_aware():
    """Tell Windows this process reports real pixels, not scaled fictions.

    A process that has not said this is "DPI unaware", and Windows lies to it
    for compatibility: GetWindowRect comes back in virtualised coordinates
    scaled by the display setting, so a 2560-pixel-wide window measures 1707
    at 150%. Every pixel constant in Loom is anchored to real pixels, so
    reading a scaled number would move the anchor scale and quietly ruin digit
    recognition - the exact failure the pixel-constant rule in CLAUDE.md is
    about.

    Qt6 sets per-monitor-v2 awareness for itself, but loom_read.py and the
    coach never construct a QApplication, so the capture backend cannot rely
    on somebody else having done this.

    -4 is DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Returns what it managed.
    """
    try:
        # Windows 10 1703+. Preferred: it is per-monitor and handles a window
        # being dragged between monitors of different scaling.
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
                ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except (AttributeError, OSError):
        pass
    try:
        # Windows 8.1+. 2 is PROCESS_PER_MONITOR_DPI_AWARE.
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass
    return "none"


def window_dpi(hwnd):
    """The DPI this window is being displayed at. 96 means 100% scaling."""
    try:
        return int(ctypes.windll.user32.GetDpiForWindow(ctypes.c_void_p(hwnd)))
    except (AttributeError, OSError):
        return 96


# ---------------------------------------------------------------------------
# Finding the game
# ---------------------------------------------------------------------------

def process_name(pid):
    """The executable name behind a process id, or "" if it cannot be read."""
    try:
        import win32api
        import win32con
        import win32process
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            return win32process.GetModuleFileNameEx(handle, 0).split("\\")[-1]
        finally:
            win32api.CloseHandle(handle)
    except Exception:
        return ""


def list_windows():
    """Every visible top-level window with a title and a real size.

    Returns dicts of hwnd/title/exe/pid/rect. The filtering matters: Windows
    is full of invisible zero-size top-level windows that would bury the one
    interesting line in noise.
    """
    import win32gui
    import win32process

    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width < 200 or height < 200:
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        found.append({"hwnd": hwnd, "title": title, "pid": pid,
                      "exe": process_name(pid),
                      "rect": (left, top, width, height)})

    win32gui.EnumWindows(visit, None)
    return found


def find_game(fragment=WINDOW_NAME_FRAGMENT):
    """The game's window, chosen by title fragment and confirmed by exe.

    A title fragment alone is a guess - "Age of Empires II" matches a browser
    tab reading the wiki just as happily as the game. The executable name is
    the corroboration, which is the same reasoning identify_hud uses when it
    refuses to name a HUD skin on the evidence of one icon.
    """
    candidates = [w for w in list_windows()
                  if fragment.lower() in w["title"].lower()]
    if not candidates:
        return None
    # Prefer a window whose process actually looks like the game, then the
    # largest - the game's main window dwarfs any tool window it owns.
    game = [w for w in candidates if "aoe" in w["exe"].lower()]
    pool = game or candidates
    return max(pool, key=lambda w: w["rect"][2] * w["rect"][3])


def client_size(hwnd):
    """The window's drawable area in real pixels, without borders or title."""
    import win32gui
    _, _, width, height = win32gui.GetClientRect(hwnd)
    return width, height


def is_foreground(hwnd):
    import win32gui
    return win32gui.GetForegroundWindow() == hwnd


# ---------------------------------------------------------------------------
# What a captured frame is actually worth
# ---------------------------------------------------------------------------

def real_pixels(frame):
    """Did this frame come back with picture in it, or is it a black hole?"""
    if frame is None or frame.size == 0:
        return False
    return float(np.count_nonzero(frame)) / frame.size > REAL_PIXEL_FRACTION


def describe_frame(frame):
    """Is this real pixels or a black rectangle? Returns a one-line verdict.

    A black frame is the failure this whole probe exists to detect, and it is
    the one that must never reach the reader as pixels: it looks exactly like
    "the HUD is not on screen" while actually meaning "this capture path does
    not work here".
    """
    if frame is None:
        return "no frame"
    nonzero = float(np.count_nonzero(frame)) / frame.size
    return (f"{frame.shape[1]}x{frame.shape[0]} "
            f"mean={frame.mean():6.2f} max={frame.max():3d} "
            f"nonzero={nonzero:6.1%} "
            f"{'REAL PIXELS' if real_pixels(frame) else 'BLACK - unusable'}")


def hud_verdict(frame, templates, wood_templates):
    """Does Loom's own anchor search find a HUD in this frame, and at what scale?

    This is the end-to-end question. A path can return perfectly real pixels
    and still be useless if what it returns is the desktop rather than the
    game, or the game at a scale outside anchor.py's sweep (0.5x-2.0x,
    falling through to 4.0x when nothing is found in it).
    """
    if frame is None:
        return "no frame to search"
    try:
        found = anchor.identify_hud(frame, templates,
                                    wood_templates=wood_templates)
    except Exception as problem:
        return f"anchor search failed: {type(problem).__name__}: {problem}"
    if found is None:
        return "no HUD found"
    profile = found.get("profile")
    name = profile.name if profile is not None else "?"
    extra = ""
    if "anchor_score" in found:
        extra = (f" (pop {found['anchor_score']:.3f}, "
                 f"wood {found['wood_score']:.3f})")
    return (f"{name} score={found['score']:.3f} "
            f"scale={found['scale']:.3f}{extra}")


# ---------------------------------------------------------------------------
# Candidate 1 — Windows Graphics Capture, via the windows-capture package
# ---------------------------------------------------------------------------

class WgcSession:
    """A running WGC stream over one window, newest frame kept.

    Push-shaped, like the macOS ScreenCaptureKit backend and unlike X11: the
    compositor hands over frames as it composites them, so there is nothing to
    "poll" - there is only whatever arrived most recently.

    The copy in the callback is not optional. windows-capture's frame_buffer
    is documented as a zero-copy view over a natively mapped frame, so it is
    valid only until the callback returns; keeping the view instead of a copy
    would read freed memory at some later, unrelated moment.
    """

    def __init__(self, hwnd):
        from windows_capture import WindowsCapture

        self.frame = None
        self.count = 0
        self.arrivals = []
        self._last = None
        self._control = None

        self._capture = WindowsCapture(
            cursor_capture=False,     # the cursor is not part of the HUD
            draw_border=False,        # no yellow "being captured" border
            window_hwnd=hwnd,
        )

        @self._capture.event
        def on_frame_arrived(frame, capture_control):
            now = time.perf_counter()
            if self._last is not None:
                self.arrivals.append(now - self._last)
            self._last = now
            self.frame = frame.frame_buffer[:, :, :3].copy()   # BGRA -> BGR
            self.count += 1

        @self._capture.event
        def on_closed():
            pass

    def start(self):
        self._control = self._capture.start_free_threaded()

    def stop(self):
        try:
            if self._control is not None:
                self._control.stop()
        except Exception:
            pass

    def wait_for_frame(self, timeout=5.0):
        deadline = time.perf_counter() + timeout
        while self.frame is None and time.perf_counter() < deadline:
            time.sleep(0.01)
        return self.frame


def try_wgc(hwnd, samples=SAMPLES):
    """Capture with Windows Graphics Capture. Returns (frame, notes)."""
    notes = []
    try:
        session = WgcSession(hwnd)
    except ImportError:
        return None, ["windows-capture is not installed"]
    except Exception as problem:
        return None, [f"could not create the stream: "
                      f"{type(problem).__name__}: {problem}"]

    try:
        started = time.perf_counter()
        session.start()
        frame = session.wait_for_frame()
        if frame is None:
            return None, ["started, but no frame arrived within 5s"]
        notes.append(f"first frame after {time.perf_counter() - started:.3f}s")

        # Let it run, so the arrival interval means something.
        time.sleep(1.0)
        if session.arrivals:
            intervals = sorted(session.arrivals)
            index = max(0, int(len(intervals) * 0.95) - 1)
            notes.append(
                f"{session.count} frames, interval median "
                f"{statistics.median(intervals) * 1000:.1f}ms "
                f"p95 {intervals[index] * 1000:.1f}ms")

        # What Loom actually does per poll: crop a small region out of the
        # newest frame. That is the cost that matters, not the frame rate.
        newest = session.frame
        height, width = newest.shape[:2]
        box = (width // 2, height // 2, 200, 40)
        timings = []
        region = None
        for _ in range(samples):
            begin = time.perf_counter()
            region = session.frame[box[1]:box[1] + box[3],
                                   box[0]:box[0] + box[2]].copy()
            timings.append(time.perf_counter() - begin)
        notes.append(f"200x40 region crop median "
                     f"{statistics.median(timings) * 1e6:.0f}us "
                     f"(shape {None if region is None else region.shape})")

        return session.frame.copy(), notes
    finally:
        session.stop()


# ---------------------------------------------------------------------------
# Candidate 2 — pywin32 BitBlt / PrintWindow
# ---------------------------------------------------------------------------

def try_gdi(hwnd, method, samples=SAMPLES):
    """Capture with GDI. method is "bitblt" or "printwindow".

    This is the path that would fit Loom's existing shape best - pull-based
    like x11.py, so capture_region could fetch just the few small bands the
    reader actually reads instead of converting a whole 4K frame. That is why
    it is measured rather than dismissed: if it works, it is the cheaper
    design. The expectation is that it does not, because a Direct3D swap chain
    is not in the window's GDI device context and copying from it yields
    black.

    PW_RENDERFULLCONTENT (3) is the flag that made PrintWindow work for
    DirectComposition windows in Windows 8.1+; it is worth its own row here
    because it succeeds in cases plain BitBlt does not.
    """
    import win32con
    import win32gui
    import win32ui

    width, height = client_size(hwnd)
    if width <= 0 or height <= 0:
        return None, ["the window has no client area"]

    notes = []
    window_dc = save_dc = memory_dc = bitmap = None
    try:
        window_dc = win32gui.GetWindowDC(hwnd)
        save_dc = win32ui.CreateDCFromHandle(window_dc)
        memory_dc = save_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(save_dc, width, height)
        memory_dc.SelectObject(bitmap)

        timings = []
        for _ in range(samples):
            begin = time.perf_counter()
            if method == "printwindow":
                # 3 = PW_RENDERFULLCONTENT
                ok = ctypes.windll.user32.PrintWindow(
                    ctypes.c_void_p(hwnd), memory_dc.GetSafeHdc(), 3)
                if not ok:
                    return None, ["PrintWindow returned 0 (refused)"]
            else:
                memory_dc.BitBlt((0, 0), (width, height), save_dc, (0, 0),
                                 win32con.SRCCOPY)
            timings.append(time.perf_counter() - begin)

        notes.append(f"full {width}x{height} grab median "
                     f"{statistics.median(timings) * 1000:.1f}ms")

        raw = bitmap.GetBitmapBits(True)
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4)
        return frame[:, :, :3].copy(), notes
    except Exception as problem:
        return None, [f"{type(problem).__name__}: {problem}"]
    finally:
        # GDI handles are a fixed system resource; leaking them in a loop is
        # how a long-running reader eventually stops being able to draw.
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        for dc in (memory_dc, save_dc):
            try:
                if dc is not None:
                    dc.DeleteDC()
            except Exception:
                pass
        try:
            if window_dc is not None:
                win32gui.ReleaseDC(hwnd, window_dc)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Candidate 3 — DXGI desktop duplication, via dxcam
# ---------------------------------------------------------------------------

def try_dxcam(hwnd, samples=SAMPLES):
    """Capture the screen region the window occupies, with DXGI duplication.

    Duplication copies the desktop, not a window, so the window's rectangle
    has to be turned into screen coordinates and anything overlapping the
    game lands in the frame too. It is included because it is the path that
    classically DOES work for fullscreen D3D games, and a working ugly answer
    beats an elegant black rectangle.
    """
    import win32gui

    try:
        import dxcam
    except ImportError:
        return None, ["dxcam is not installed"]

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    camera = None
    try:
        camera = dxcam.create(output_color="BGR")
        if camera is None:
            return None, ["dxcam.create returned None (no output device)"]

        timings, frame = [], None
        for _ in range(samples):
            begin = time.perf_counter()
            grabbed = camera.grab(region=(left, top, right, bottom))
            timings.append(time.perf_counter() - begin)
            if grabbed is not None:
                frame = grabbed

        notes = [f"region grab median "
                 f"{statistics.median(timings) * 1000:.1f}ms"]
        if frame is None:
            notes.append("every grab returned None - duplication produced no "
                         "new desktop frame (nothing on screen changed, or "
                         "the game is in exclusive fullscreen)")
        return (None if frame is None else np.ascontiguousarray(frame)), notes
    except Exception as problem:
        return None, [f"{type(problem).__name__}: {problem}"]
    finally:
        try:
            if camera is not None:
                camera.release()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def report_window(window, awareness):
    hwnd = window["hwnd"]
    left, top, width, height = window["rect"]
    client_w, client_h = client_size(hwnd)
    dpi = window_dpi(hwnd)
    scale = dpi / 96.0

    virtual_w = ctypes.windll.user32.GetSystemMetrics(78)
    virtual_h = ctypes.windll.user32.GetSystemMetrics(79)

    print(f"  title      {window['title']!r}")
    print(f"  hwnd       {hwnd}   pid {window['pid']}   exe {window['exe']}")
    print(f"  dpi        {dpi} ({scale:.2f}x display scaling), "
          f"process awareness: {awareness}")
    print(f"  window     {width}x{height} at ({left}, {top})   [real pixels]")
    print(f"  client     {client_w}x{client_h}                 [real pixels]")
    print(f"  Qt points  {round(width / scale)}x{round(height / scale)} at "
          f"({round(left / scale)}, {round(top / scale)})")
    print(f"  desktop    {virtual_w}x{virtual_h} virtual screen")
    print(f"  foreground {is_foreground(hwnd)}")
    if abs(scale - 1.0) > 0.01:
        print("  NOTE: display scaling is not 100%. window_size must return "
              "the real pixel numbers above and window_geometry the Qt "
              "points, or the anchor scale shifts and digits degrade.")


def main():
    parser = argparse.ArgumentParser(
        description="Probe the Windows capture paths against a live game.")
    parser.add_argument("--list", action="store_true",
                        help="enumerate visible windows and stop")
    parser.add_argument("--fragment", default=WINDOW_NAME_FRAGMENT,
                        help="window title fragment to aim at")
    parser.add_argument("--save", action="store_true",
                        help="write each path's frame into captures/")
    parser.add_argument("--background", type=int, metavar="SECONDS",
                        help="count down this many seconds before capturing, "
                             "so the game can be sent behind another window")
    parser.add_argument("--samples", type=int, default=SAMPLES)
    args = parser.parse_args()

    if sys.platform != "win32":
        print(f"This probe is Windows-only; this is {sys.platform!r}.")
        return 2

    awareness = become_dpi_aware()

    if args.list:
        print("Visible top-level windows:\n")
        for window in sorted(list_windows(), key=lambda w: -w["rect"][2]):
            _, _, width, height = window["rect"]
            print(f"  {width:>5}x{height:<5} {window['exe']:<24} "
                  f"{window['title'][:60]}")
        return 0

    window = find_game(args.fragment)
    if window is None:
        print(f"No window matching {args.fragment!r}. Is the game running?")
        print("Try: python -m tools.windows_probe --list")
        return 1

    print("=" * 72)
    print("THE WINDOW")
    print("=" * 72)
    report_window(window, awareness)

    if args.background:
        print(f"\nSend the game behind something else. Capturing in "
              f"{args.background}s...")
        for remaining in range(args.background, 0, -1):
            print(f"  {remaining}...", end="\r", flush=True)
            time.sleep(1)
        print("  capturing now.        ")
        print(f"  game is foreground: {is_foreground(window['hwnd'])}")

    print()
    print("=" * 72)
    print("THE CANDIDATE PATHS")
    print("=" * 72)

    templates = {profile: anchor.load_template(profile)
                 for profile in hud.PROFILES}
    wood_templates = {profile: queue.load_wood_template(profile)
                      for profile in hud.PROFILES}

    attempts = [
        ("windows-capture (WGC)",
         lambda: try_wgc(window["hwnd"], args.samples)),
        ("pywin32 BitBlt",
         lambda: try_gdi(window["hwnd"], "bitblt", args.samples)),
        ("pywin32 PrintWindow",
         lambda: try_gdi(window["hwnd"], "printwindow", args.samples)),
        ("dxcam (DXGI duplication)",
         lambda: try_dxcam(window["hwnd"], args.samples)),
    ]

    verdicts = []
    for name, attempt in attempts:
        print(f"\n--- {name} ---")
        try:
            frame, notes = attempt()
        except Exception as problem:
            frame, notes = None, [f"raised {type(problem).__name__}: {problem}"]
        for note in notes:
            print(f"  {note}")
        print(f"  frame:  {describe_frame(frame)}")
        verdict = hud_verdict(frame, templates, wood_templates)
        print(f"  HUD:    {verdict}")
        verdicts.append((name, frame, verdict))

        if args.save and frame is not None:
            import cv2
            paths.CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
            # The whole name, not its first word: "pywin32 BitBlt" and
            # "pywin32 PrintWindow" both start with "pywin32" and one was
            # overwriting the other's frame.
            stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            out = paths.CAPTURES_DIR / f"probe_{stem}.png"
            cv2.imwrite(str(out), frame)
            print(f"  saved:  {out}")

    print()
    print("=" * 72)
    print("VERDICT")
    print("=" * 72)
    usable = [(name, verdict) for name, frame, verdict in verdicts
              if real_pixels(frame)]
    if not usable:
        print("  Nothing returned usable pixels. If the game is in exclusive")
        print("  fullscreen, try its Windowed Fullscreen mode and re-run.")
        return 1
    for name, verdict in usable:
        print(f"  USABLE   {name:<26} HUD: {verdict}")
    print()
    print(f"  -> loom/capture/windows.py should be built on: {usable[0][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
