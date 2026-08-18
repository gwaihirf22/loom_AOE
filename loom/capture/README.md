# `loom/capture` — reading pixels, per OS

This package is the only part of Loom that knows what operating system it is
running on. Everything else says `from loom import capture`, calls the same six
names, and never finds out.

```
__init__.py   picks a backend by sys.platform and re-exports it
errors.py     CaptureError, so backends can raise what __init__ imports
x11.py        Linux — the game's XWayland window, via python-xlib
windows.py    Windows — a Windows Graphics Capture stream
macos.py      macOS — a ScreenCaptureKit stream
```

## The contract

Six names. A backend that is missing one fails at the worst possible moment —
mid-match, the first time that particular call is reached — so
`tests/test_capture_selector.py` checks every backend has all of them.

| | |
|---|---|
| `open_display()` | connect to whatever hands out windows; returns an opaque handle |
| `find_game_window(session, fragment)` | the game's window, or `None` |
| `window_size(window)` | `(width, height)` in **capture pixels** |
| `window_geometry(window, session)` | `(x, y, width, height)` on the desktop, in **Qt points** |
| `capture_region(window, x, y, w, h)` | a BGR numpy array, `x`/`y` relative to the window |
| `capture_window(window)` | a BGR numpy array |

Two rules that are not obvious from the signatures:

**Pixels and points are different numbers.** `window_size` is what pixels you
are about to be handed; `window_geometry` is where to put a Qt window. On a
scaled display — a 2× Mac, a 125% Windows desktop — they differ, and conflating
them does not raise. It shifts the anchor scale under every pixel constant at
once and quietly degrades digit recognition.

**A failed capture must raise `CaptureError`, never return a black array.** A
black frame is an environment fault — a permission not granted, a window not
being composited — and if it reaches the reader as pixels it looks exactly like
"the HUD is not on screen right now". That is the difference between Loom saying
"I cannot see" and Loom quietly believing something wrong.

The one deliberate exception is a *stale* frame in the streaming backends, which
is served as black on purpose. Serving the last real frame would freeze the game
clock, and a value that repeats is believed by design — so Loom would report a
frozen clock as real game time. `macos.GameWindow.latest` argues this at length.

## Two shapes of backend

`x11.py` is **pull**: ask for a rectangle, get a rectangle, and only the small
bands the reader actually wants are ever fetched.

`windows.py` and `macos.py` are **push**: the compositor delivers frames as it
draws them, so there is nothing to poll — only whatever arrived most recently.
Both keep one stream per window in a module-level dict, because the reader and
the overlay each look the window up and would otherwise run two streams over the
same window.

How the game is *identified* is left to the backend rather than fixed here: X11
matches a window-title substring, macOS matches a bundle identifier, and Windows
matches the title and corroborates with the executable name.

## Importing never fails

`from loom import capture` works on a platform with no backend, and on a
platform whose backend's dependencies are missing. The problem is remembered and
raised at the moment somebody asks for pixels, where the message can say what is
wrong. Failing at import would take down anything that merely touches
`loom.reader` — including the test suite, which has no interest in capturing
anything.

`LOOM_CAPTURE_BACKEND` overrides the choice. It exists for tests, and for asking
a machine to load a backend it would not have picked.

## Adding a platform

1. Write the module with the six names above.
2. Add one line to `BACKENDS` in `__init__.py`.

That is the whole integration. Nothing downstream changes — which is not a
claim, it is what happened when Windows was added.

Keep anything OS-specific **inside a function**, not at module scope, so the
module still imports on other platforms. `windows.py` goes as far as avoiding
`ctypes.wintypes` for spelling `HWND`, because importing it raises `ValueError`
off Windows and the contract test imports every backend everywhere. That
discipline is what lets `tests/test_windows_capture.py` run on Linux and
`tests/test_capture_selector.py` prove the Linux backend still loads from a
Windows machine.

Before building on an API, measure it — `tools/windows_probe.py` and
`tools/macos_probe.py` exist because guessing here is expensive. The Windows
probe is what established that GDI's `BitBlt` returns pure black for a Direct3D
game window, which would otherwise have been discovered three layers deep.
