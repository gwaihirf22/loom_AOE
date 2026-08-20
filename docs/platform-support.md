# Which platforms Loom runs on

Loom reads the game's HUD by screen capture and draws an overlay on top of it.
Both of those are the most OS-specific things a program can do, so what works
varies by platform. This page is the honest version; each install guide repeats
only the part that applies to it.

| | Linux | Windows | macOS |
|---|---|---|---|
| **Reading the HUD** | ✅ | ✅ | ⚠️ works, ~1–2s behind |
| **Overlay** | ✅ | ✅ | ❌ not over fullscreen |
| **Statistics + graphs** | ✅ | ✅ | ✅ |
| **APM tracking** | ✅ | ✅ | ❌ not yet |
| **Demo / simulate modes** | ✅ | ✅ | ✅ |
| **Status** | primary | primary | paused |

Everything that is not capture or overlay — the build-order engine, pace, the
queue reader, notifications, statistics — is plain Python and OpenCV and behaves
identically everywhere. The [test suite](../tests) runs headless on all three.

## Linux

The original platform, developed on Bazzite / KDE Plasma / Wayland.

Capture reads the game's **XWayland** window directly with python-xlib.
Screenshotting the desktop returns black on Wayland; reading the game's own
window does not. The game runs through Proton in **Full screen** mode.

The overlay's click-through is real: its window flags empty the X11 input
region, verified at startup by asking the X server rather than trusting Qt.
That matters more than it sounds — a panel the pointer can enter breaks the
game's cursor confinement, and the mouse walks onto another monitor mid-match.

APM tracking works here and only here, reading raw X input events. It counts
keys and clicks and records nothing about which ones.

→ [Install guide](install-linux.md)

## Windows

Capture uses **Windows Graphics Capture**, the API Windows itself uses for
window sharing. The obvious alternative — GDI's `BitBlt` — returns a pure black
frame for a Direct3D game, which AoE2:DE is; that was measured, not assumed
(see [`tools/windows_probe.py`](../tools/windows_probe.py)).

Two things Windows does better than macOS:

- **The game does not have to be in front.** WGC keeps delivering frames for a
  backgrounded window. On macOS, backgrounding the game stops capture dead.
- **Display scaling is handled.** Loom declares per-monitor DPI awareness and
  keeps capture pixels and Qt points apart, so a 125% or 150% display reads and
  places the overlay correctly.

Reading the HUD is verified at **1920x1080 and 2560x1440**, on the stock bar
and on Anne_HK, against recorded games rather than by eye: every band - the
clock, the villager count, the population display and the production queue -
is replayed frame by frame at both sizes.

The overlay sits above the game in fullscreen - measured at 2560x1440, with
click-through confirmed by asking Windows for the window's `WS_EX_TRANSPARENT`
bit. There is no windowed-only restriction here, unlike macOS.

APM tracking works, using **Raw Input** rather than a keyboard hook, and
counting inside the overlay rather than as a separate process. It reads which
device an event came from and whether a button went down - never which key.
`SetWindowsHookEx` is deliberately not used: it is the mechanism keyloggers
use and what antivirus software looks for.

→ [Install guide](install-windows.md)

## macOS

**Paused, and known-degraded.** Reading works against Feral Interactive's
native port, but a poll costs about a second under game load, so Loom trails the
game by one to two seconds. Both of Apple's scheduling levers were tried and
neither moved that number; the remaining path is making the per-poll work
smaller. Linux on a far weaker machine at the same 4K display trails only ~2
game-seconds.

Two hard limits, both measured:

- **The overlay cannot float above the game's fullscreen Space.** Every window
  level and collection behaviour was tried. Windowed play only.
- **The game must be frontmost.** macOS only composites the front window, so
  backgrounding the game stops frames. Loom blanks rather than serving a frozen
  clock.

Validated only with the game at the display's native 4K. Rendering below it
upscales the HUD past the anchor search's ceiling; Loom says so when it happens.

→ [Install guide](install-macos.md)

## Adding another platform

The capture seam is one package —
[`loom/capture/`](../loom/capture/README.md) — that picks a backend by
`sys.platform`. A new platform is one module implementing six functions plus one
line in a table. Nothing downstream changes.
