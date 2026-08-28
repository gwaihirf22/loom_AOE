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
| **Packaged install** | ✅ Flatpak | ✅ zip with `.exe` | ❌ source only |
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

APM tracking reads raw X input events, in a child process. It counts keys
and clicks and records nothing about which ones. (Windows counts the same
thing by a different mechanism — see below — so this is no longer the only
platform that has it.)

**Installing is one command.** Loom is packaged as a Flatpak, so there is no
Python to set up: the runtime brings its own. Unlike the Windows `.exe` the
Linux package is installed from SOURCE rather than frozen, which is what
lets the APM child process start at all — a frozen build has no mode to
launch it with. Everything the build needs is in
[`packaging/linux/`](../packaging/linux/README.md), whose README carries the
argument for each sandbox permission the package asks for.

One of those permissions is X11, and the reason is the paragraph above: the
desktop-screenshot route returns black on Wayland, so Loom must read the
game's own X window.

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
and on Anne_HK, against recorded games rather than by eye: the clock, the
villager count, the population display, the production queue and the age
crest are all replayed frame by frame at both sizes.

**The notification feed is the exception, and at 1920x1080 it is still
patchy.** The game does not draw that feed by scaling one master - at 1080p
it lays the text out at a smaller point size - and Loom's character set was
harvested at 2560x1440. Coverage has been cut at 1080p since, and the
labelled corpus for that rendering went from 74 events read to 137 of 275,
but it is not whole: a capital "H" loses its crossbar at that size and
segments as two separate bars, so "--House Built--" can still read
"--llouse Built--". Two features depend on the feed - the checklist's
**green "observed" ticks** and the **Town Centre count** the idle-TC
warning is built on - so at 1080p expect some items not to tick off and
the occasional event to be missed. A missed line is a gap, never a wrong
answer. **2560x1440 remains the fully-verified resolution for the feed.**

The numbers at 1080p are settled: the digit templates are cut at that
rendering now, and the clock, villager count and population read 99-100%
on recorded games at that size.

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
upscales the HUD, which the anchor search should now reach; if it does not,
Loom says so and names the HUD scale slider.

→ [Install guide](install-macos.md)

## Adding another platform

The capture seam is one package —
[`loom/capture/`](../loom/capture/README.md) — that picks a backend by
`sys.platform`. A new platform is one module implementing six functions plus one
line in a table. Nothing downstream changes.

Shipping to that platform is a second, separate job: a `packaging/<os>/`
folder, and a decision about whether the app is frozen there or installed
from source. Those two answers are not related — Windows freezes and Linux
does not, for reasons written up in
[`packaging/linux/README.md`](../packaging/linux/README.md).
