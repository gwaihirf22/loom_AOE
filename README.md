<p align="center">
  <img src="images/loom_logo.png" alt="Loom" width="280">
</p>

# Loom

A live, on-screen build-order assistant for **Age of Empires II: Definitive
Edition**. Loom watches the game's HUD by screen capture, reads the **total
villager count** and the **game clock** off it several times a second, and uses
those two numbers to drive a build order — showing the step to do now, the step
after it, whether you are on pace, and how your villagers are spread across
food, wood, gold and stone versus what the build wants.

#### Video demo: [https://youtu.be/IjrvbCo6lIQ](https://youtu.be/IjrvbCo6lIQ)

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

There *are* hotkeys, and they are the exception that proves the point: they nudge
the step when a reading has drifted, and then Loom goes back to following the game
on its own after ten seconds. You can also switch following off entirely if you
would rather drive — and then the panel says **MANUAL** across the top for as long
as it lasts, because an overlay that has quietly stopped tracking your game while
looking exactly as it always does is the one thing this must never do.

It does this **without touching the game**. Loom reads pixels from the screen
and draws a window on top. It never injects into, modifies, or reads the memory
of the game process — so it is not a cheat and cannot be mistaken for one.

![A close-up of the overlay panel](images/overlay-panel.png)

*The panel reads at a glance: the step to do now, the one after it, and a
villagers-per-resource row where each resource is its own colour. Here the build
wants 7 on wood but only 4 are there, so it is flagged; the rest match.*

---



## Which platforms it runs on

| | Linux | Windows | macOS |
|---|---|---|---|
| Reading the HUD | ✅ | ✅ | ⚠️ ~1–2s behind |
| Overlay | ✅ | ✅ | ❌ not over fullscreen |
| Statistics + graphs | ✅ | ✅ | ✅ |
| APM tracking | ✅ | ✅ | ❌ not yet |

Install guides: **[Linux](docs/install-linux.md)** ·
**[Windows](docs/install-windows.md)** · **[macOS](docs/install-macos.md)**.
The detail behind every cell, and why, is in
[docs/platform-support.md](docs/platform-support.md).

Everything that is not capture or overlay — the build-order engine, pace, the
queue reader, notifications, statistics — is plain Python and OpenCV and
behaves identically everywhere.

---



## Trying it without the game

You do not need the game to see Loom work: **every front end runs in a demo or
simulated mode with no game required**:

```bash
python3 -m venv .venv          # Windows: py -m venv .venv
source .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
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
"behind" warning appear, exactly as they would over a live game. The launcher
(`python loom_app.py`) works without the game too — its developer mode runs the
same demo, and the build preview follows it.

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

### Which HUD is on screen (`hud.py`)

The game's own bar and a UI mod's bar draw the same numbers in different art at
different spacings, so a template cut from one does not find the other — Loom
spent a while insisting a perfectly visible stock HUD was not there, because the
anchor scored 0.74 against a 0.80 gate. A **HUD profile** is one skin's anchor
templates plus the offsets and glyph metrics that belong with them; Loom tries
each at startup and keeps whichever the pixels choose. Both the stock HUD and
the Anne_HK Better UI mod are supported, and a third skin is one entry plus two
small images.

Two things that were not obvious. An anchor may contain **nothing that changes**
— and that includes the *civilization*, because the resource bar's border art is
drawn per civ. A first attempt included the bar texture around the icon to make
the two skins easier to tell apart; it scored 1.00 on the civ it was measured on
and 0.59 on Portuguese, whose bar is pale stone where the other was dark wood.
The icons and their black boxes are shared art and identical across civs; the
scenery around them is not.

And because every skin draws that same shared art, one icon is a **weak**
discriminator: the two anchors sit about 0.02 apart on each other's HUDs, which
is a coin toss, not a decision. So Loom asks a second icon — it checks the wood
icon at the scale the population icon proposes and scores the pair by its weaker
half. A skin genuinely on screen has both icons where it expects them at one
size; a wrong skin has to explain one and then finds nothing where the other
should be. That takes the margin from 0.02 to at least 0.27 on every frame
measured.

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
Reading them needed a colour mask plus a connected-component cleanup to drop the
bar's highlight lines — a plain brightness threshold is no use, because the HUD
bar's own highlights are bright too.

*Which* colour mask turned out to be a property of the HUD skin rather than of
the game: the mod prints these numbers in yellow **below** each icon, while the
stock bar stamps them in white **inside** the icon's box, over the artwork. So
the number's position comes from the profile, and the reader tries yellow first
and then a "white and colourless" test — the second one works over artwork
because skin tones are warm and cloth is saturated, while a digit is neither.

### The production queue (`queue.py`, `production.py`, `alerts.py`)

The HUD's global queue strip shows what every building is training, as tinted
slot icons. Loom reads the slots — tint, unit identity by template matching,
group count — and `production.py` turns the stream into believed state: are
the Town Centers working, is production housed, how many TCs exist (the exact
count comes from the game's own "Town Center built" notification;
queue evidence only corroborates). `alerts.py` decides how loudly each fact
deserves to be said: an idle TC starts obnoxious and tapers off as the economy
matures; housed always shouts; pop-capped stays silent because it usually
means production is maxed out. All thresholds and switches are the player's,
from the launcher.

### The game's own words (`notifications.py`)

The game prints notification lines — "--Town Center Built--", "--Knight
Created--", attack warnings — and this is the one place it states facts
outright rather than Loom inferring them from pixels. Two readers share the
feed: a handful of whole-phrase templates drive the live logic, and
`glyphs.py` reads **any** line as text using a harvested character set — the
digit-template idea applied to the whole alphabet, so every building, unit
and technology event lands in the statistics without a template per phrase,
an OCR engine, or an AI backend. A line the font cannot fully read is
dropped and its crop saved, and one `tools/build_notification_font.py`
command turns it into coverage.

### The payoff screen (`report.py`)

When the build completes, the overlay flips from instructions to a report:
how the build went — completion time against a perfect run, TC idle seconds,
villagers lost (with raid attribution from the attack notifications),
milestone timings. The same data feeds the post-game statistics.

### Showing it (`overlay.py`) — and the tooltip discovery

The overlay is a frameless, click-through, always-on-top panel. Getting it to
draw over a full-screen game took a real experiment. An ordinary always-on-top
window drew over every other window but lost to the game — KDE puts a focused
full-screen window in a stacking layer that outranks it. The fix, found by trying
several window types, is to make the overlay a **tooltip-type** window, which
sits in a higher layer still (the one the system volume popup uses), and to run
it through XWayland. Both are load-bearing and commented as such in the code so
they are not "tidied away" later.

Making it genuinely *click-through* took a second discovery of the same shape.
Qt's `WA_TransparentForMouseEvents` only makes the widget ignore mouse events it
is handed; it tells the X server nothing, so the server went on routing the
pointer into the overlay's window. Age of Empires II runs under Proton and
confines the cursor to its own window, as every full-screen RTS does — and it
loses that confinement the moment the pointer crosses out, so brushing the panel
threw the mouse onto the second monitor mid-match. The fix is the window flag
`WindowTransparentForInput`, which Qt implements through the X SHAPE extension:
it empties the window's *input region*, so the server never considers the pointer
to have entered at all. Measured with `python-xlib`, the region goes from the
full panel rectangle to `[]`. `loom/passthrough.py` asks the X server that same
question at startup and warns if the answer ever changes back.

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
- **The overlay is transparent to the X server, not just to Qt.** Anything less
and a game that confines the mouse cursor loses that confinement whenever the
pointer touches the panel. This makes Loom *less* intrusive, not more: it stops
the overlay intercepting pointer crossings that belonged to the game.

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

The same everywhere:

- Python 3.10+
- Age of Empires II: Definitive Edition
- In-game **HUD scale at 100%** (Options → Interface) — Loom warns if it
  measures otherwise; recognition degrades away from 100%, and below about 90%
  the HUD may not be found at all. (The slider tends to report 99% however it
  is set; that 1% is well inside the tolerance.)
- The **stock HUD** or the **Anne_HK Better UI** mod. Loom knows both and works
  out which is on screen by itself — see "Which HUD is on screen" above. Another
  UI mod that replaces the resource-bar artwork needs its own profile; Loom says
  so rather than waiting silently, naming the closest skin and its score.

  The launcher's **How to use** button says the same thing inside the app.

  Two mods pair well with Loom (recommended, never required):
  [Anne_HK — Better UI](https://www.ageofempires.com/mods/details/3762), the
  layout Loom was built against, and
  [the transparent-UI mod](https://www.ageofempires.com/mods/details/2532),
  which clears the per-civ border artwork that causes most reading trouble —
  though it does not yet cover every civ, the newest least of all.

Per platform — the display mode the game needs, where settings are kept, and
what is not supported yet — follow the guide for yours:

- **[Linux](docs/install-linux.md)** — XWayland, Proton, **Full screen** mode.
  Verified on Bazzite / KDE Plasma / Wayland.
- **[Windows](docs/install-windows.md)** — Windows 10 1903+, **Windowed
  Fullscreen** recommended.
- **[macOS](docs/install-macos.md)** — paused and known-degraded; read the
  limitations first.

```bash
pip install -r requirements.txt
python loom_overlay.py            # over a running game
python loom_coach.py              # the same, in the terminal
```

Resolution and the HUD-scale slider do not need configuring — Loom detects the
HUD at whatever size it is drawn.

### The launcher

```bash
python loom_app.py
```

One window instead of four terminal tabs: pick a build order from the library
in `builds/` (each row shows the build's own name, civilisation, author and
step count), start and stop the overlay, and adjust the alerts — the villager
counts where the idle-TC warning softens and silences, and on/off switches
for the TC-idle, housed and pre-emptive HOUSE SOON warnings. Settings are
saved to `config.json` the moment they change, and apply the next time the
overlay starts. The HOUSE SOON threshold is also settable: how much population
space remaining should raise the pre-emptive warning — raise it if you boom
hard enough to keep getting housed at the default 4.

The **build preview** is its own window: four step cards stacked vertically —
the step just done, the current step highlighted, and the two after it — each
showing the same information the overlay shows, including the
villagers-per-resource targets for that point in the build. Being a normal
window, it is sized by normal window management, and **resizing it scales the
cards**: drag it bigger on the second monitor and everything grows with it.
The size persists. With no game running it is a study aid: click a card or
scroll to browse the whole build. While the overlay is running a game, the
preview follows the step the player is actually on (the overlay reports its
state as sentinel lines on its own stdout, which the launcher already
streams), and only the current card shows the live readings — game time,
villager count, have/want per resource, and pace. Live wins: clicks go dead
while following, and browsing resumes the moment there is no usable reading
to follow (menus, the pre-match wait, or the overlay stopping). The **Show
build preview** checkbox (on by default) and the window's own close button
both hide it, and stay in sync.

**Place overlay**, next to the Start/Stop buttons, opens the overlay's
placement mode: drag the panel where you want it over the game, close it, and
the position saves. It is disabled while the overlay is running — a new
position applies on the next start anyway.

The **Overlay size** box holds two independent knobs, as percentages.
*Overall size* grows the whole panel uniformly — geometry, writing, icons.
*Text size* grows only the writing (and the panel's height to give the taller
lines room), never its width, so bigger text does not widen the overlay's
footprint on the game — long instructions shorten with an ellipsis instead.
Both apply the next time the overlay starts, like every other setting.

### Statistics

Every game writes one JSON file to `stats/`: the build-completion report,
a post-game summary (game length, peak villagers, villager deaths with raid
attribution, TC efficiency, housed time, when each unit and technology first
appeared in the production queue), and a per-second timeline. The overlay
writes it at build completion, every thirty game-seconds after that, and on
any exit — including being stopped from the launcher. The **Statistics**
button opens past games with three tabs: the build report, the post-game
summary, and graphs (villagers and population, the pace meter, and APM)
drawn with plain QPainter. Two honesty notes baked into the UI: queue rows
are *first sightings*, not produced counts (the queue hides duplicate groups
and never reports completion), and "game length" means the last usable
reading, because the game never announces its end.

**Track APM** runs a background counter alongside the overlay: keystrokes
and clicks per minute, bucketed every five seconds and aligned to the game
clock in the stats file. The privacy contract is structural, not a promise:
the counter selects X11 *raw* input events whose payload is never decoded —
it counts that a key was pressed and cannot know which. It also only sees
the XWayland world the game lives in; input to native Wayland windows never
reaches it. Raw APM only — eAPM would require knowing what the actions
were, which is exactly what this refuses to see.

A **Developer mode** checkbox reveals the debug tools: the overlay's demo
mode, the terminal coach's simulator, the misread logger, the
frame grabber, and a button that runs the test suite with its output
streaming into the window. Everything the launcher starts runs as a child
process, and closing the launcher takes its children with it. (The demo
overlay drives the build preview too, so the follow behaviour can be watched
without a game.)

The launcher is an ordinary Wayland window; only the overlay itself runs
through XWayland (see the capture section for why).

---



## Tests

```bash
python -m pytest tests/ -q
```

(or the **Run tests** button in the launcher's developer mode, which streams
the same run into the window.)

Over two hundred tests, and they cover the *logic*, not the computer vision, because that is
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
running game, and `loom_read.py` serves as the manual check. The one Qt thing
that *is* tested is the overlay's window flags, because they are pure data:
`tests/test_overlay_flags.py` asserts, with no display and no `QApplication`,
that the panel is still a tooltip window and still transparent for input, and
that placement mode is neither. Both of those were found by experiment, and both
look like clutter to a later reader.

---



## Project layout

```
loom_app.py             the launcher (start here)
loom_overlay.py         the overlay (main entry point)
loom_coach.py           the same coaching logic, in a terminal
loom_read.py            raw readout of the two HUD numbers (diagnostic)
loom/                   everything that gets imported
  paths.py              file locations: shipped assets, and the player's own
  capture/              reading pixels out of the game window, per OS
    x11.py              Linux, macos.py macOS, windows.py Windows
  hud.py                HUD skins: which templates and offsets belong together
  anchor.py             finding the HUD by template matching
  digits.py             digit recognition by template matching
  filters.py            rejecting misreads
  session.py            game started / resumed / tracking lost
  reader.py             the whole read pipeline behind one class
  build_order.py        loading builds, current step, pace inputs
  pace.py               how far behind the build order you are
  resources.py          villagers-per-resource, read off the HUD
  queue.py              reading the global production queue off the HUD
  production.py         believed production state (idle TCs, housed)
  alerts.py             how loudly each production event deserves
  notifications.py      the game's own event lines, read as phrases
  glyphs.py             the notification font, read letter by letter
  report.py             the build-complete report (the payoff screen)
  gamestats.py          per-game statistics, one JSON file per match
  apm.py                aligning APM buckets to the game clock
  overlay.py            the on-screen panel
  passthrough.py        asks the OS whether the overlay really is click-through
  launcher.py           the launcher window
  browser.py            the launcher's build preview: a stack of step cards
  statsview.py          the statistics window: past games, tabs, graphs
  follow.py             is the panel following the game, and where it looks
  hotkeys/              registering global hotkeys, per OS
  statefeed.py          overlay state as sentinel lines on its own stdout
  stopline.py           the launcher's "please stop", back down on stdin
  runner.py             running the other Loom programs as children
  config.py             saved settings: overlay position, alerts, build
tools/                  development scripts, never imported
  grab_frames.py        screenshot grabber: run_<time>_<skin>[_<label>]/
  index_captures.py     rewrites captures/INDEX.md from the folder names
  capture_smoketest.py  the Wayland capture test (documents why mss is unused)
  overlay_test.py       does always-on-top survive a fullscreen game, and is
                        the panel really click-through?
  apm_counter.py        counts keys and clicks per bucket - never which key
  windows_probe.py      which Windows capture path actually returns pixels
  replay_queue.py       replays captured frames through the full stack
  tc_debug.py           live view of what the Town Centre tracker believes
  build_queue_templates.py  cuts queue icon templates from game artwork
templates/              reference images used for matching
  pop_icon.png          the population icon, as the Anne_HK mod draws it
  stock/                the same anchors as the unmodded game draws them
  digits/               labelled 0-9 glyphs (shared: it is the game's font)
  queue/                unit icons for reading the production queue
builds/                 build orders as JSON
stats/                  per-game statistics files (gitignored)
captures/               frames grabbed while playing (gitignored); INDEX.md
                        is generated, so rename a folder to describe it
images/                 resource icons for overlay
tests/                  the test suite
CHANGELOG.md            version history; 1.0.0 is the Windows release
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

The build-step icons in `master_aoe2_images/` are game art. Age of
Empires II © Microsoft Corporation. Loom was created under Microsoft's
["Game Content Usage Rules"](https://www.xbox.com/en-US/developers/rules)
using assets from Age of Empires II, and it is not endorsed by or
affiliated with Microsoft.

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
