# How Loom works

The engineering story behind Loom, moved here from the README so the front
page can stay about using the app. This is the pipeline walked end to end -
capture, anchoring, digit recognition, filtering, the build-order logic -
and the decisions that were not obvious, most of them forced by something
discovered while building rather than chosen up front.

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
script that returned black, and it documents *why* `mss` is not used. It is
also the only thing in the tree that imports `mss`, which is why that library
is a development dependency and is not shipped in either package.

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

The hard case is the **Transparent UI** mod, which removes the HUD backdrop so
the clock is drawn straight onto the map. The background is then grass, stone or
a building, it changes every frame while the digits do not, and template matching
against it collapsed — across four recordings of one game the clock read 284/284
and 284/285 without the mod and 210/284 and 102/287 with it. What separates the
digits from the map is the one property that never varies: HUD digits are pure
white and terrain is not. Isolating on that first reads 287/287 and 284/284 on
the same recordings. `reader.py` measures the backdrop's luminance once at HUD
acquisition — a stock or Anne_HK backdrop reads 80–96, the same skins with the
mod read 144–146 — so the cost is one median per session rather than per poll.

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

A step's instructions are a **list**, not a headline with footnotes
(`Step.items`). The split used to be read as a ranking, and that is wrong
about real builds: counted across the thirteen shipped ones, 38 of 150 steps
lead with a heading rather than an action ("Before Feudal Age"), and 20 of
the 58 age-up mentions sit in a later piece. Both front ends also capped the
list at three, so the busiest step showed three of its seven instructions and
said nothing about the four it dropped.

### How big is a step? (`steplayout.py`)

Pure arithmetic, no Qt, because the overlay panel and the preview's cards
draw the same list and a box four pixels short clips an instruction while
looking perfectly fine. The concessions run in order: **grow** the box to
seven rows, **split** into columns if the widest item genuinely fits,
**shrink** the text, then **truncate and say so** with "+N more". Seven rows
covers every step of every shipped build with no shrinking at all.

### What has actually been done? (`checklist.py`)

Two states that must never look alike. An item is **observed** when the game
announced it in its own notification feed, and **assumed** when the build
merely moved past its step. What can be observed comes from the build file
itself: 331 of the 344 note pieces in the shipped builds carry an `@icon@`
token naming the exact entity, and `glyphs.parse_event` turns a read
notification line into the same vocabulary of slugs, so "Build a
`@mill/Mill_aoe2de.webp@`" lines up with `built:mill` with nothing in between
to guess wrong. Items whose only tokens are villagers, resources or animals
have no observable completion and are assume-only.

Ticks do not have to arrive in order — players adapt, misclick and do things
early — so an event credits the first item anywhere in the build that still
wants it, and an item wants **all** the entities it names before it counts as
done. "Build 2 House, then Mill at Berries" needs both.

That last rule is why **what an item is allowed to want** turned out to
matter more than the matching. An item waiting on something the feed can
never report is unfinishable, and it drags everything sharing that item down
with it. Three ways that happened, all found in real games and all now
closed: a build saying "Palisade Wall gaps to the Town Center" made a wall a
requirement and the houses beside it could never tick; **farms** were watched
for years' worth of builds although the game announces 22 kinds of building
and a farm is not one of them (it says "--Farm Exhausted--" when one runs
out, which is a different event); and "Use **Market** to build 2nd **Town
Center**" treated the Market as a second thing to build, when it is the tool
the step is worked with. So a noun after "to" or "from" is read as a *place*,
a noun after "use" or "with" as an *instrument*, and neither is a deliverable.

### Which age you are in (`age.py`)

The build order needs to know the age, and for a long time Loom inferred it
from the build itself — which is circular, and quietly misleads exactly the
player who has fallen behind. Click Feudal a minute late and the old code
would happily report ON PACE, because the clock had passed the build's "In
Feudal Age" step and it assumed the player had got there.

So Loom reads the **age crest** beside the resource bar instead. The crest is
artwork shared by both HUD skins, it is language-independent, and it changes
only when an age-up actually *completes* — which makes it the authority for
both halves of the question. The words beside it would have been the obvious
thing to read and are the wrong one: the text flips the instant you *click*,
so reading it announces "Feudal Age" while the player is still two minutes
away.

The crest is a **ceiling as well as a floor**: the checklist cannot assume
its way past an age boundary the crest has not confirmed, which is how one
stray villager once assumed half a build. The progress bar beside it says an
age-up is under way, and the production queue is a second witness to the
same click — the two meet inside `AgeTracker` and nowhere else, so
everything downstream reads one reconciled belief rather than working it out
again.

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

### Saying when it has stopped reading (`filters.py`, `session.py`, `debuglog.py`)

The read filters hold their last belief through unreadable polls, which is
right for a menu and dangerous everywhere else — a held number looks exactly
like a fresh one. A live game proved it: the villager count froze at 6 for
the whole match while the clock ran on, and nothing anywhere admitted it.

Two gaps caused that, and both are closed. `filters.ReadGap` measures how
long a band has gone unread **in game seconds** — never in polls, because
the poll rate is not a clock — so the panel can say `VILLAGERS UNREAD · 24s`
instead of wearing the stale number. And `session.TRACKING_LOST` had been
declared for months with nothing consuming it; losing sight of the game now
puts a soft band above the panel until the game comes back. The panel keeps
working either way. It just stops pretending.

Behind both sits a **forensic log** (`debuglog.py`): one line per poll, raw
readings printed beside believed ones, in the player's data directory with
the newest twenty sessions kept. That distinction — did the band read wrong,
or not read at all? — is the one the frozen-count investigation could not
make from outside, and the shipped `.exe` has no console for anything
printed to disappear into.

### The payoff screen (`report.py`)

When the build completes, the overlay flips from instructions to a report:
how the build went — completion time against a perfect run, TC idle seconds,
villagers lost (with raid attribution from the attack notifications),
milestone timings. The same data feeds the post-game statistics.

### The one ruler that is not Loom's own (`replay.py`)

Every other check in Loom scores one crop of pixels: is this the number 7, do
these pixels spell `--Barracks Built--`. That cannot catch a reader which is
right most of the time and wobbles the rest — a wobble is not a wrong frame, it
is a wrong *count* across frames. The game's own `.aoe2record` is the only
witness Loom has that is not one of Loom's readers, so after a match ends the
statistics are checked against it.

**It is read only after the game has ended, and the refusal lives at the door.**
The file logs every command *both* players issued, fog included, so reading a
live one would be a maphack regardless of what Loom did with it — and the game
writes `rec.aoe2record` continuously while playing, and it parses perfectly,
which is exactly why the rule cannot rest on nobody thinking to try. Two
refusals, raising `GameStillRunning`: that filename is the match in progress,
and a record whose mtime has not settled is one the game is still finishing.
They carry different messages because they mean different things — the second is
"try again in a few seconds", not a wall — and both are raised as their own
exception type so a caller cannot report them as a corrupt file. Declining to
look and failing to read need different sentences.

It is enforced where the bytes are opened rather than in the finder, and that
distinction was a real bug rather than a style preference. `is_finished` used to
have exactly one caller — the automatic finder — while `harvest()` opened the
file without it, so the statistics window's *Add recorded game* picker walked
straight past the guard. A safeguard only one caller applies is a safeguard the
next caller will not. Only `statsview.py` reaches this module at all; nothing on
the overlay's path can.

Matching a record to a session is a **containment** test, not a nearest-time
one: the record's filename carries the game's start and its mtime the end, so
the question is whether Loom's session falls inside that window. Measured over
265 sessions and 315 records, nearest-start-within-ten-minutes matched 51 and
containment matched 56 — and containment is corroborated by a quantity nothing
in the matcher computes, the duration in the record's body, which agrees to the
second when Loom tracked a whole game.

What comes out is **orders, not events**. A foundation can be cancelled and a
research abandoned, so a count from here is a *ceiling* — enough to convict a
reader that over-fires, not one that under-fires. And the three sources answer
three different questions: the record says a building was PLACED, the
notification feed says it was BUILT, the queue says it was QUEUED. Placement is
not a rival reading of completion; subtract it from one and you get build
duration instead.

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
(`--place`) reopens the same panel with `WindowTransparentForInput` left off and
the frame left off too: a title bar and a border are opaque chrome the real panel
does not have, and placement exists to judge a *translucent* card against the
game. Frameless means the window manager will not move it either, so the panel
carries the pointer itself (`mousePressEvent`). There is no close button either:
setting the position ends placement, Escape abandons it, and the launcher's Place
overlay button becomes Close placement, so there is a way out that does not
depend on the panel holding the focus.
Its position is saved as an offset from the game window's corner, not a desktop
coordinate, so it survives a resolution change. The saved position is the panel's *top-left*, and the panel grows
downward, so placement shows the build's busiest step with both alert bands up —
the largest the panel can ever get — and hangs a **Set Overlay Position** button
below that. Saving on a button press rather than on close makes closing a cancel,
and keeps the one control that ends the exercise out of the region whose size the
exercise is about. The Appearance settings reach it live over the same
`LOOM_SETTINGS` line a running overlay gets.

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

## Tests

```bash
python -m pytest tests/ -q
```

(or the **Run tests** button in the launcher's developer mode, which streams
the same run into the window.)

Over a thousand tests, and they cover the *logic*, not the computer vision, because that is
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
