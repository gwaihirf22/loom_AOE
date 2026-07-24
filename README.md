# Loom

A live, on-screen build-order assistant for **Age of Empires II: Definitive
Edition**. Loom watches the game's HUD by screen capture, reads the **total
villager count** and the **game clock** off it several times a second, and uses
those two numbers to drive a build order — showing the step to do now, the step
after it, whether you are on pace, and how your villagers are spread across
food, wood, gold and stone versus what the build wants.

#### Video demo: [https://youtu.be/gRb23-qxOxw](https://youtu.be/gRb23-qxOxw)

![Loom's overlay running over a live game](images/overlay-in-game.jpg)

*Loom's panel sitting on top of a real match — the current step, whether you're
on pace, and your villagers-per-resource versus what the build wants, all read
off the HUD while you play.*

### Why "Loom"?

Forgetting to research **Loom** — the cheap Town Centre technology that stops
your villagers dying to a boar or an early rush — is one of the oldest running
jokes in the Age of Empires II community. "Did you forget Loom?" is what you say
to someone who just lost a villager they did not need to.

This tool exists so I stop forgetting Loom, and everything else in the build I
mean to do and do not. The name is the bug it was written to fix.

---



## What makes it different

Build-order overlays already exist — RTS Overlay, Border — but they are all
*manual*: you press a hotkey to advance to the next step, and they have no idea
what is happening in your game. Websites like aoecompanion need a second monitor
and do not move with your game.

Loom's novel part is that it **reads the real game state and advances itself**.
It knows you just made your seventh villager, so it shows you the seventh-villager
instruction without being told. It knows the clock says 9:05 when the build
wanted you in Feudal Age by 9:00, so it tells you that you are behind. Nobody has
to press anything.

It does this **without touching the game**. Loom reads pixels from the screen
and draws a window on top. It never injects into, modifies, or reads the memory
of the game process — so it is not a cheat and cannot be mistaken for one.

![A close-up of the overlay panel](images/overlay-panel.png)

*The panel reads at a glance: the step to do now, the one after it, and a
villagers-per-resource row where each resource is its own colour. Here the build
wants 7 on wood but only 4 are there, so it is flagged; the rest match.*

---



## Trying it without the game

Age of Empires II traditionally runs on Windows, but I run on Linux under Proton.
This project was built and tested on a fairly specific setup (see *Running it for real*).
Because a marker almost certainly will not have the game installed, **every front
end runs in a demo or simulated mode with no game required**:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# The overlay, replaying a whole match on your desktop in about a minute:
python loom_overlay.py --demo

# The same logic in a terminal, with three scenarios:
python loom_coach.py --simulate                     # a player exactly on pace
python loom_coach.py --simulate --scenario behind   # villagers arriving late
python loom_coach.py --simulate --scenario stall    # production stops dead
```

`--demo` and `--simulate` feed the real build-order engine a made-up but
plausible game, so you can watch the step advance, the pace meter move, and the
"behind" warning appear, exactly as they would over a live game.

And the test suite runs anywhere:

```bash
python -m pytest tests/ -q
```

---



## How it works

Loom is a pipeline: **capture pixels → find the numbers → read the digits →
filter out mistakes → decide what to show**. Each stage is a small module under
`loom/`. This section walks the pipeline and explains the decisions that were
not obvious, because most of them were forced by something I discovered while
building rather than chosen up front.

### Reading the screen — and the Wayland problem (`capture.py`)

The first plan was to screenshot the screen with the `mss` library. The first
test came back **pure black**.

That is not a bug, it is a security decision. Under the old X11 display system
any program could read the pixels of any other — a keylogger-grade hole. Wayland
(the modern Linux display system this machine uses) closed it: an application may
only see its own windows. So a normal screenshot returns nothing.

The way in: Age of Empires runs under Proton, which draws the game through
**XWayland** — a compatibility X server inside the Wayland session. That means
the game has a real X window whose pixels *can* be read directly, even though the
screen as a whole cannot. `capture.py` finds that window and asks the X server
for its pixels with `XGetImage`. A useful side effect is that this works in
**window-relative coordinates**, so which monitor the game is on and where the
window sits never matter — a real problem on this two-monitor setup, solved for
free.

`capture_smoketest.py` (in `tools/`) is kept in the repo as a record: it is the
script that returned black, and it documents *why* `mss` is not used.

### Finding the numbers (`anchor.py`)

The two numbers are not at fixed pixel positions: they move and change size with
resolution and the in-game HUD-scale slider. Hardcoded rectangles are hopeless —
I proved that to myself when a crop that captured the whole resource bar at one
resolution sliced the digits clean off at another.

So Loom **anchors** instead. It has a small reference image of the population
icon and slides it over the top of the frame with OpenCV's `matchTemplate` at a
range of sizes (`cv2.TM_CCOEFF_NORMED`, which matches on pattern rather than
brightness, so it survives the HUD dimming). Wherever that scores highest is the
icon, and the villager count and clock are then read at known offsets from it.
The winning *scale* is reused to size those offsets, so the whole thing is
resolution-independent. One subtlety: the search is coarse-then-fine, because the
clock is far from the anchor and a small scale error there becomes a large
position error — offset error grows with distance.

### Recognising the digits (`digits.py`)

Rather than an OCR engine like Tesseract, Loom matches each digit against ten
small reference images. The HUD font is fixed and pixel-identical every frame, so
there are only ten possible shapes; matching them is both more accurate and far
faster than a general engine trained on prose, and it needs no system packages —
which matters on this immutable OS. The digit templates were cut from real
screenshots.

### Not trusting a single frame (`filters.py`)

OCR misfires occasionally. The filters stop one bad reading from poisoning the
rest of the game, and the two numbers need different rules:

- The **villager count** changes rarely, so a value must be seen twice in a row
before it is believed.
- The **clock** changes every poll, so requiring two identical readings would
freeze it. Instead it accepts sensible forward movement and demands
confirmation only for a surprise (a big jump, or the clock going backwards).

Crucially the count filter does **not** reject large jumps. An earlier version
did, and it froze the villager count at 22 for a whole session when a new game
started at 4. A briefly wrong reading fixes itself next poll; a permanently stuck
one silently desynchronises everything.

### Knowing what is happening (`session.py`)

Two numbers are not yet a sense of *events*. `session.py` turns them into "a game
started", "resumed", or "tracking lost". The interesting case is telling a brand
new match apart from alt-tabbing back into the same one — get it wrong and the
build order restarts under the player mid-game. Loom does it by remembering the
clock across the gap: if it comes back lower than it went away, the match is new.

### The build order (`build_order.py`)

A build order is a list of steps, each with a villager count, a game time, the
target villager distribution, and instructions. Given the live count and clock,
`build_order.py` answers which step is active. The trap: **villager count alone
is ambiguous**. A Fast Castle sits at 22 villagers for three separate steps,
because a Town Centre cannot train villagers while it researches an age. So Loom
identifies a step by villager count *and* time together — the first step not yet
satisfied by both.

It deliberately shows the first *unfinished* step, not the last completed one.
An early version showed the completed step and felt a beat behind the player's
hands the whole game.

### Are you on pace? (`pace.py`)

The pace number is the thing a player watches out of the corner of their eye, so
how it behaves matters more than any single value. It must not creep on its own.

The rule: *you are as far behind as you were when your current villager arrived,
and no further — unless something you should have done by now has not happened.*
A player who fell 30 seconds behind and is now producing at the right rate reads
a steady "30s", not a number ticking upward. An idle Town Centre makes it climb,
because now something really is slipping. This is measured at villager **arrival
events** rather than sampled every poll; sampling made the number sawtooth
forever, because villagers arrive in jumps while time is continuous.

### Villagers per resource (`resources.py`)

The HUD also shows, under each resource icon, how many villagers are gathering
it. Loom reads those too and compares them to the build's target, so a beginner
can see "the build wants 4 on wood, you have 1" — something no manual overlay can
do. These counts are **advisory only**: they never decide the build-order step,
because per-resource numbers swing wildly the instant villagers are re-tasked.
Reading them needed a colour mask (the numbers are yellow, and the wooden HUD bar
is bright enough to fool a plain brightness threshold) plus a connected-component
cleanup to drop the bar's highlight lines.

### Showing it (`overlay.py`) — and the tooltip discovery

The overlay is a frameless, click-through, always-on-top panel. Getting it to
draw over a full-screen game took a real experiment. An ordinary always-on-top
window drew over every other window but lost to the game — KDE puts a focused
full-screen window in a stacking layer that outranks it. The fix, found by trying
several window types, is to make the overlay a **tooltip-type** window, which
sits in a higher layer still (the one the system volume popup uses), and to run
it through XWayland. Both are load-bearing and commented as such in the code so
they are not "tidied away" later.

Because the overlay is click-through it can never be dragged, so repositioning it
(`--place`) opens it briefly as a normal window; its position is saved as an
offset from the game window's corner, not a desktop coordinate, so it survives a
resolution change.

The same engine feeds two other front ends: `loom_coach.py`, a terminal version
used to get the logic right before any UI existed, and `loom_read.py`, a bare
readout of the two numbers kept as a diagnostic for when something looks wrong.

---



## Design decisions worth calling out

- **Screen capture, not memory reading or replay parsing.** Memory reading is
Windows-centric, breaks every patch, and looks like a cheat. Replays are not
live and do not even store villager counts. Reading the HUD works on any OS and
touches nothing in the game.
- **Total villager count is the only signal that advances the build.**
Per-resource counts are advisory; game time is read from the screen and never
counted with a wall clock, because game speed is 1.7× in multiplayer and the
game can pause.
- **Never guess a reading.** If confidence is low, Loom reports no reading rather
than a wrong one. A wrong villager count silently desynchronises everything; an
admitted gap does not.
- **Use the community's build-order format**, so builds people already share load
unchanged, rather than inventing a schema.

More of the reasoning, including the wrong turns, is in my working notes, and the
tests below encode the specific bugs these decisions were made to prevent.

---



## Build orders

Build orders are JSON files in `builds/`. Loom uses the same format as
[RTS Overlay](https://github.com/CraftySalamander/RTS_Overlay), the format the
Age of Empires II community already shares builds in — so **a build downloaded
from the community library works unchanged**: drop the `.json` file into
`builds/`. Verified against Hera's Arena Fast Castle Boom, which loads and runs
with no conversion.

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
tokens become readable words ("Villager", "Wood"), and each note is split on `|`
into a headline instruction plus extras. The build shipped in `builds/` is my
own, with timings modelled on standard villager production; community builds are
GPL-licensed and are not redistributed here.

Optionally, dropping `wood.png`, `food.png`, `gold.png`, `stone.png` into an
`icons/` folder makes the overlay show the game's resource icons instead of the
words; without them it falls back to the full words, so there is never any doubt
which resource is which.

---



## Running it for real

- Python 3.10+
- Linux with XWayland (verified on Bazzite / KDE Plasma / Wayland)
- Age of Empires II: Definitive Edition, in **Full screen** mode, running through proton.
- No mods installed that replace the resource-bar icons (Loom matches their artwork)

```bash
pip install -r requirements.txt
python loom_overlay.py            # over a running game
python loom_coach.py              # the same, in the terminal
```

Resolution and the HUD-scale slider do not need configuring — Loom detects the
HUD at whatever size it is drawn.

---



## Tests

```bash
python -m pytest tests/ -q
```

64 tests, and they cover the *logic*, not the computer vision, because that is
where every bug so far has been: the reader has been correct since it was
written, while the reasoning on top of it went wrong repeatedly. Most tests pin
down a failure that actually happened, and say so in a comment — the villager
count that stuck at 22, the clock filter that silently assumed the poll rate, the
pace number that crept every second, the overlay that showed the finished step
instead of the next one. They exist so those cannot come back when the code later
looks over-cautious and invites simplifying. `test_digits.py` needs no
screenshots: it feeds each glyph template back through the classifier, catching a
corrupt or mislabelled one instantly.

Capture and the Qt drawing are not unit tested — they need a live X server and a
running game, and `loom_read.py` serves as the manual check.

---



## Project layout

```
loom_overlay.py         the overlay (main entry point)
loom_coach.py           the same coaching logic, in a terminal
loom_read.py            raw readout of the two HUD numbers (diagnostic)
loom/                   everything that gets imported
  paths.py              file locations, derived from the source tree
  capture.py            reading pixels out of the game window
  anchor.py             finding the HUD by template matching
  digits.py             digit recognition by template matching
  filters.py            rejecting misreads
  session.py            game started / resumed / tracking lost
  reader.py             the whole read pipeline behind one class
  build_order.py        loading builds, current step, pace inputs
  pace.py               how far behind the build order you are
  resources.py          villagers-per-resource, read off the HUD
  overlay.py            the on-screen panel
  config.py             saved settings, e.g. where the overlay sits
tools/                  development scripts, never imported
  grab_frames.py        screenshot grabber for building a test corpus
  capture_smoketest.py  the Wayland capture test (documents why mss is unused)
templates/              reference images used for matching
  pop_icon.png          the population icon artwork
  digits/               labelled 0-9 glyphs
builds/                 build orders as JSON
images/                 resource icons for overlay
tests/                  the test suite
```

Anything under `loom/` is imported; anything under `tools/` is only run
directly. Paths come from `loom/paths.py`, derived from the source location
rather than the working directory, so Loom runs from anywhere.

---



## Acknowledgements

Loom leans on work the Age of Empires II community has already done. It reads the
game and draws a panel; almost everything it knows about *what a good opening
looks like* came from elsewhere.

**[RTS Overlay](https://github.com/CraftySalamander/RTS_Overlay)** by
CraftySalamander (GPL-3.0) — Loom uses its build-order JSON format. Choosing it
over a format of my own is why community builds play in Loom unchanged. I
implemented the format by reading published build orders; no code from that
project is used here.

**[rtsbuilds](https://github.com/CraftySalamander/rtsbuilds)** (GPL-3.0) — the
library of community build orders in that format, which proved the
interoperability really works. Those builds are not redistributed here.

**Hera** — the Arena Fast Castle Boom credited to them in that library was the
build I tested against. Its source video: [https://youtu.be/JsTNM7j6fs4](https://youtu.be/JsTNM7j6fs4)

**[buildorderguide.com](https://www.buildorderguide.com/)** and
**[Sage of Empires](https://github.com/Mulliman/sage-of-empires)** — where many
community builds are written, and the earlier helper whose schema shaped Loom's
first draft before I moved to the RTS Overlay format.

Age of Empires II: Definitive Edition is developed by Forgotten Empires and
published by Xbox Game Studios. Loom is an unofficial fan-made tool, not
affiliated with or endorsed by them.

---



## Credits

Built by **Paul Blake** as a CS50 final project.

I used Anthropic's Claude to help with proper syntax, code organisation,
debugging, auditing and review. The design and code are my own work.
I cited at the top of every source file, as CS50 permits for
the final project. The code, architecture, design decisions, testing and direction are
mine; Every significant choice came out of testing the tool against a real
game and deciding what the results meant. Yet, many thousands of questions were asked
of Claude for finding documentation and showing me how to write snippets of code.