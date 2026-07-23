# Loom

A live, on-screen build-order assistant for **Age of Empires II: Definitive Edition**.

Loom reads two numbers off the game's HUD — **total villager count** and **game
time** — using screen capture and template matching, then shows the current and
next build-order step in a transparent overlay, along with an "on pace / behind"
meter.

Unlike existing build-order overlays (RTS Overlay, Border), which require you to
advance steps yourself with hotkeys, Loom **syncs to the real game state** and
advances itself. It knows when you have fallen behind.

Loom does not modify, inject into, or read the memory of the game process. It
only reads pixels from the screen and draws a window on top.

---

## Status

Milestones 1 and 3 are complete. The coach runs in a terminal today; the
overlay is next.

| Milestone | Description | State |
|---|---|---|
| 1 | Read villager count + game time reliably | **done** |
| 2 | New-game reset (done); config + calibration deferred | partial |
| 3 | Build-order engine (pure logic) | **done** |
| 4 | Transparent click-through overlay | **done** |
| 5 | Build editor, per-resource display, polish | not started |

**Working so far:** Loom finds the game window, locates the HUD at any
resolution, reads the game clock and villager count several times a second, and
uses them to drive a build order — showing the current step, the next step, and
how far ahead or behind you are.

Validated across 121 captured frames with no detected misreads: the villager
count rose 3 -> 22 in twenty clean single steps, and the clock advanced without
a single regression.

Costs about **1% of one CPU core**: anchoring the HUD takes ~830 ms but happens
once at startup, after which each poll reads only two small regions in ~3 ms.

New-game detection is done (`loom/session.py`): Loom distinguishes a fresh match
from alt-tabbing back into the same one, so the build order restarts only when
it should.

Milestone 3 is done: build orders load in the community format, and
`loom_coach.py` shows the current step, the next step and the pace, live or
simulated.

**Next:** Milestone 4 — the transparent click-through overlay. Config
persistence and manual calibration are deferred: automatic anchoring is working
reliably, so both are optimisations rather than needs.

### Planned: per-resource villager counts

The HUD also shows, under each resource icon, how many villagers are gathering
that resource. Reading these lets Loom tell a player something no existing
overlay can: *"your build wants 4 on wood, you have 1."* For a beginner, knowing
whether your villager distribution matches the build is one of the hardest
things to judge.

This needs four more anchor templates (wood, food, gold, stone icons). The
number sits at the same relative position inside each icon's box as the villager
count does inside the population box, so the reading code is unchanged.

These counts will be **advisory only** — they never drive build-order progress.
See the design notes for why, and for how alerts should behave so they help
rather than nag.

---

## Build orders

Build orders are JSON files in `builds/`. Loom uses the same format as
[RTS Overlay](https://github.com/CraftySalamander/RTS_Overlay), which is what
the Age of Empires II community already shares build orders in.

That means **a build downloaded from the community library works unchanged** —
drop the `.json` file into `builds/` and Loom will play it. Verified against
Hera's Arena Fast Castle Boom, which loads and runs with no conversion.

Each step looks like this:

```json
{
  "villager_count": 21,
  "age": 1,
  "time": "7:30",
  "resources": { "food": 14, "wood": 7, "gold": 0, "stone": 0 },
  "notes": ["Next 4 @resource/MaleVillDE.webp@ to @resource/Aoe2de_wood.webp@"]
}
```

Loom normalises this on load: `"7:30"` becomes 450 seconds, the `@...@` icon
tokens become readable words ("Villager", "Wood"), and each note is split on
`|` into one headline instruction plus any extra actions.

Build orders shipped with Loom are its own. Community build orders are
GPL-licensed, so they are not redistributed here — download them yourself and
drop them in.

---

## Requirements

- Python 3.10+
- Linux with X11 or XWayland (see *Platform notes*)
- Age of Empires II: Definitive Edition

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install mss opencv-python numpy python-xlib PyQt6
pip install pytest        # only needed to run the tests
```

---

## Supported game configuration

Loom currently expects:

- **Display Mode: Full screen** (AoE2's "Full desktop" mode requires all
  monitors to be the same resolution and form a rectangle, which many setups
  cannot satisfy).
- **No mods that replace the resource-bar icons.** Loom finds the HUD by
  matching the population icon's artwork. A mod that changes that artwork will
  break detection. (A future milestone will allow user-supplied templates.)

Resolution and the in-game HUD scale slider do *not* need to be configured —
Loom detects the HUD at whatever size it is drawn.

---

## Platform notes

Loom captures a specific window, not the whole screen, so multi-monitor layouts
and window position do not matter.

On **Wayland**, applications are not permitted to read the screen, so the usual
screen-capture route returns black frames. Loom works on Wayland *because* AoE2
runs under Proton as an **XWayland** client, which means it has a real X window
whose pixels can be read directly. Verified on Bazzite (KDE Plasma, Wayland).

Because the desktop is composited, each window renders into its own offscreen
buffer. Loom therefore keeps reading the game correctly even when the game is
unfocused or another window covers it — which also means Loom's own overlay
cannot block its view of the HUD. This would not hold on an X11 desktop running
without a compositor.

The overlay runs through XWayland (`QT_QPA_PLATFORM=xcb`) and asks to be a
tooltip-type window. Both are necessary on KDE: Wayland clients may not raise
themselves above other windows, and an ordinary always-on-top window still
loses to a focused full-screen game. A tooltip window sits in a higher stacking
layer - the same one KDE's own volume popup uses.

Windows and macOS support is planned but not yet implemented.

---

## Scripts

Entry points and development tools. Run everything from the project root.

### `capture_smoketest.py`

Captures the primary monitor with `mss` and reports whether the image is black.
Kept as a record: on Wayland this **fails by design**, which is what led to the
per-window capture approach.

```bash
python -m tools.capture_smoketest
```

### `grab_frames.py`

Saves timestamped PNG frames of the AoE2 window while you play, to build a
corpus for tuning detection offline.

```bash
python -m tools.grab_frames        # a frame every 2 seconds
python -m tools.grab_frames 3.0    # a frame every 3 seconds
```

Each run writes into its own timestamped folder under `captures/`, so runs
cannot overwrite each other. Frames are large (~5 MB each) and are not
committed to version control.

### `loom_overlay.py` — the overlay

Draws the current build-order step on top of the game: a frameless,
click-through, always-on-top panel below the resource bar.

```bash
python loom_overlay.py                      # live, over a running game
python loom_overlay.py --build fast_castle
python loom_overlay.py --demo               # no game needed, replays a match
python loom_overlay.py --place              # drag it where you want it, then close
```

`--place` opens the panel as an ordinary window so it can be dragged, and saves
the position to `config.json`. It has to be a different kind of window, because
the overlay proper is click-through and a click-through window can never
receive a drag. The position is stored as an offset from the game window's
corner rather than a desktop coordinate, so it survives resolution changes.

It sets `QT_QPA_PLATFORM=xcb` itself, so there is no environment variable to
remember. See *Platform notes* for why that and the tooltip window type are
both required on KDE.

### `loom_coach.py` — the build-order coach

Shows the current step, the next step, and whether you are on pace. This is
the same job the overlay will do, done in a terminal first so the logic can be
trusted before any UI exists.

```bash
python loom_coach.py                      # live, against a running game
python loom_coach.py --build fast_castle  # choose a build from builds/
```

It also runs without the game, replaying a whole match in under a minute:

```bash
python loom_coach.py --simulate
python loom_coach.py --simulate --scenario behind
python loom_coach.py --simulate --scenario stall --speed 40
```

The three scenarios are `perfect` (exactly on schedule), `behind` (villagers
arriving late throughout) and `stall` (production stops dead, as if the Town
Centre were forgotten). Simulation makes "does it show the right step at the
right time?" a ten-second check rather than a sixteen-minute game.

### `loom_read.py` — the raw reader

Prints the game clock and total villager count, with no build order or advice.
Kept as a diagnostic: when the coach looks wrong, this answers the first
question — is Loom reading the screen correctly at all?

```bash
python loom_read.py
```

It shows both the filtered values it believes and the raw readings behind them,
so you can watch the filters working.

### `anchor.py`

Locates the population icon in a frame using multi-scale template matching and
computes the two read regions from it. Writes an annotated debug image so the
detection can be checked by eye.

```bash
python -m loom.anchor captures/frame_0001.png
```

Green box = the matched icon. Magenta = villager-count region.
Orange = clock search band.

---

## Tests

```bash
python -m pytest tests/ -q
```

The tests cover the logic, not the computer vision, because that is where
every bug so far has been: the reader has been correct since it was written,
while the reasoning on top of it has been wrong repeatedly.

Most tests pin down a failure that actually happened, and say so — the
villager count that stuck at 22 forever when a new game started, the clock
filter that silently assumed how often it was polled, the pace number that
crept upward every second, the overlay that showed the step you had already
finished instead of the one to do next. They exist to stop those coming back
when the code later looks over-cautious and invites simplifying.

`tests/test_digits.py` needs no captured frames: it feeds each glyph template
back through the classifier, which catches a corrupted or mislabelled template
immediately.

Capture and the Qt drawing are not unit tested. Those need a live X server and
a running game, and `loom_read.py` already serves as the manual check.

---

## Layout

```
loom_overlay.py         the overlay (main entry point)
loom_coach.py           the same thing in a terminal
loom_read.py            raw readout of the two HUD numbers (diagnostic)
loom/                   everything that gets imported
  paths.py              file locations, derived from the source tree
  capture.py            reading pixels out of the game window
  anchor.py             finding the HUD, and the debug visualisation
  digits.py             digit recognition by template matching
  filters.py            rejecting misreads
  session.py            game started / resumed / tracking lost
  reader.py             the whole read pipeline behind one class
  build_order.py        loading builds, current step, pace
  overlay.py            the on-screen panel
  pace.py               how far behind the build order you are
  config.py             saved settings, e.g. where the overlay sits
tools/                  development scripts, never imported
  grab_frames.py        screenshot grabber for building a test corpus
  capture_smoketest.py  Wayland capture test (documents why mss is unused)
templates/              reference images used for matching
  pop_icon.png          the population icon artwork
  digits/               labelled 0-9 glyphs
builds/                 build orders as JSON
captures/               captured frames (scratch, not committed)
tests/
```

Anything under `loom/` is a module other code imports. Anything under `tools/`
is a script that is only ever run directly. Keeping those apart stops
development scratch from cluttering the part of the project that matters.

Paths come from `loom/paths.py`, which derives them from the location of the
source rather than the current working directory — so Loom can be started from
anywhere.

---

## Credits

Built by **Paul Blake** as a CS50 final project.

Developed with AI assistance (Claude), used as a pair programmer, tutor and
debugger. CS50 permits this for the final project provided it is cited, and it
is cited at the top of every source file as well as here.

The architecture, design decisions, testing and direction are mine. Every
significant choice in this project came out of testing it against a real game
and deciding what the results meant — among them:

- reading the game's own window rather than the screen, after screen capture
  turned out to return black under Wayland
- keeping total villager count as the only signal that advances the build,
  because per-resource counts swing wildly whenever villagers are re-tasked
- removing a read filter that rejected "impossible" jumps, once it turned out
  to freeze the villager count for an entire session
- reporting how far behind the player is only when a villager arrives, rather
  than recomputing it continuously, so the number holds still instead of
  creeping
- treating the age text on the HUD as the moment the player *clicks* an age
  up, not the moment they arrive in it

The reasoning behind each of these, including the wrong turns, is written up in
my working notes.
