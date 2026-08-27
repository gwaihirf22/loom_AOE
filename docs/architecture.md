# How Loom fits together

A map of the code, kept honest by a test.

**Every arrow means "feeds".** `anchor --> reader` says anchor's output is
what reader works from — and `tools/architecture.py` checks the source
really does show `reader` importing `anchor`. A box that names no real
module fails. An arrow that names no real import fails. A module that
appears on no diagram fails.

So this map may **simplify** — `paths` is imported by nearly everything and
drawing it would bury the picture, so it is deliberately left out — but it
can never **lie**, and it can never quietly fall behind. Run
`python -m tools.architecture --check` to ask.

---

## 1. Reading the screen

Pixels in one end, a judged reading out the other. This is the layer where
every design rule about never guessing lives.

```mermaid
flowchart LR
    game([the game on screen]) --> capture
    hud --> anchor
    resources --> anchor
    digits --> resources
    hud --> resources
    anchor --> queue
    digits --> queue
    hud --> queue
    resources --> queue
    digits --> glyphs
    glyphs --> lines
    lines --> glyphs
    capture --> reader
    anchor --> reader
    digits --> reader
    hud --> reader
    resources --> reader
    glyphs --> reader
    notifications --> reader
    queue --> reader
    filters --> reader
    age --> reader
    session --> reader
```

`anchor` finds the population icon and derives every other read region from
it, which is why almost nothing else needs to know a screen coordinate — and
why a pixel constant that does not scale with the anchor is a latent bug.

`glyphs` and `lines` point at each other on purpose. `glyphs` reads the
notification font letter by letter; `lines` holds the enumerated universe of
lines the game can print and snaps a wobbly reading onto the nearest real
one. Neither is complete alone.

`filters` is the gate that turns a reading into a belief, and `session`
decides whether this is the same game as last time.

### One cell, two readings, two questions

`queue` is the only reader that asks two independent questions of the same
pixels, and the distinction is load-bearing enough to draw.

A slot in the production queue carries a **shape** — which icon is this —
and a **colour** — is a wash drawn over it. Identity is matched against
grayscale templates; the wash is judged against the *same templates in
colour*, because a wash multiplies the icon underneath it and an unwashed
cell is 1:1:1 against its own picture however red its art happens to be.
Two readings of one asset, neither derived from the other. `icon_interior`
exists so both take the same crop: the templates are 40×40 assets cut for
exactly that region, and comparing one against a whole 47px cell misaligns
every pixel.

The result feeds `production`, which asks it two questions with **different
costs of being wrong**:

| question | evidence it takes | why |
|---|---|---|
| does another Town Centre **exist**? | untinted or green | a Town Centre that never existed is permanent — `_tcs_queue_high` is never lowered |
| is this Town Centre **working**? | anything but amber | erring busy costs one quiet moment |

Note there is no arrow from `queue` to `production` on any diagram above,
and there should not be: `production` never imports `queue`. It is handed
slot readings as plain data, which is what makes it testable without a
game — the same trade `reader` makes by staying free of Qt.

Drawn at function granularity, because none of this is module-shaped:

```mermaid
%% functions: loom/queue.py
flowchart LR
    find_wood_icon -- "where the strip starts" --> slot_boxes
    slot_boxes -- "one box per cell" --> count_occupied
    count_occupied -- "how many cells hold something" --> read
    read -- "cell in colour" --> classify_tint
    read -- "cell in colour" --> read_count
    without_numeral -- "the count digit belongs to neither picture" --> _identify_cached
    read -- "cell in grey" --> without_numeral
    load_icon_templates -- "grey: what SHAPE is this" --> _identify_cached
    _identify_cached -- "score and margin gates" --> identify
    icon_interior -- "the 40x40 region templates were cut for" --> classify_tint
    icon_interior -- "the SAME crop, or the template misaligns" --> wash_against_icon
    read -- "only where classify_tint said None" --> wash_against_icon
    identify -- "which icon this is" --> wash_against_icon
    load_icon_colour_templates -- "colour: is that shape multiplied" --> wash_against_icon
    classify_tint -- "green / amber / red / None" --> SlotReading
    wash_against_icon -- "None to red only, never the reverse" --> SlotReading
    identify -- "name" --> reconcile_identity_and_count
    read_count -- "batch depth" --> reconcile_identity_and_count
    reconcile_identity_and_count -- "a tech never shows a count" --> SlotReading
```

Read it as one crop going two ways and coming back as one slot. `identify`
asks a **shape** question of the grayscale templates; `wash_against_icon`
asks a **colour** question of the very same files loaded in colour. Neither
reading is derived from the other, which is the point — a wash multiplies
the icon underneath it, so an unwashed cell is 1:1:1 against its own
picture however red its art happens to be, and that is the only test that
separates a housed villager from a flame icon.

`icon_interior` feeds both branches for a reason worth stating twice: the
templates are 40×40 assets cut for exactly that region. When the wash test
was first wired in it compared against the whole 47px cell instead, which
misaligned every pixel and dragged one real wash's red ratio from 0.37 to
0.11 — under its own floor, hiding the single cell the change existed to
catch.

The one-way arrow matters too. `wash_against_icon` runs only after
`classify_tint` has already said `None`, so it can add a reading and never
overrule one — a confident wash is never second-guessed by a comparison
against a possibly-wrong identity, and an identity it cannot name yields
`None` rather than "not washed".

## 2. From a reading to the panel

```mermaid
flowchart LR
    build_order --> checklist
    glyphs --> checklist
    age --> alerts
    build_order --> alerts
    production --> alerts
    alerts --> overlay
    checklist --> overlay
    config --> overlay
    build_order --> overlay
    follow --> overlay
    steplayout --> overlay
    build_order --> pace
    build_order --> report
    production --> report
    age --> gamestats
    filters --> debuglog
    apm --> apmwin
    statefeed --> apmwin
    reader --> loom_overlay
    overlay --> loom_overlay
    production --> loom_overlay
    pace --> loom_overlay
    report --> loom_overlay
    gamestats --> loom_overlay
    debuglog --> loom_overlay
    apm --> loom_overlay
    apmwin --> loom_overlay
    passthrough --> loom_overlay
    placement --> loom_overlay
    hotkeys --> loom_overlay
    statefeed --> loom_overlay
    stopline --> loom_overlay
    capture --> loom_overlay
    entry --> loom_overlay
```

`loom_overlay` is the widest box on the map because it is the application:
it owns the poll loop and wires everything else together.

`checklist` is where OBSERVED and ASSUMED are kept apart — it takes
`build_order` for what a step expects and `glyphs` for what the game
actually announced, and never lets the second look like the first.

`follow` exists so the panel can say on its face when a hotkey has moved the
step by hand and it is no longer following the game.

## 3. The launcher and its children

The launcher never imports the applications. It spawns them, and talks to
them down a pipe.

```mermaid
flowchart LR
    entry --> runner
    statefeed --> runner
    stopline --> runner
    alerts --> config
    build_order --> buildcheck
    config --> about
    overlay --> about
    alerts --> browser
    build_order --> browser
    checklist --> browser
    config --> browser
    flowlayout --> browser
    overlay --> browser
    placement --> browser
    steplayout --> browser
    queue --> events
    age --> statsview
    build_order --> statsview
    checklist --> statsview
    durations --> statsview
    events --> statsview
    gamestats --> statsview
    overlay --> statsview
    queue --> statsview
    replay_ids --> replay
    replay --> statsview
    report --> statsview
    about --> launcher
    apm --> launcher
    browser --> launcher
    build_order --> launcher
    buildcheck --> launcher
    config --> launcher
    entry --> launcher
    flowlayout --> launcher
    hotkeys --> launcher
    overlay --> launcher
    placement --> launcher
    runner --> launcher
    statsview --> launcher
    launcher --> loom_app
```

`runner` is the seam: `entry` knows how to launch a child whether Loom is
frozen or running from source, `statefeed` carries the child's state up on
stdout, and `stopline` carries requests back down on stdin.

**`events` is the reconciler.** Its own docstring says it best — *"It is not
a pub/sub bus. A bus routes; this DECIDES."* It reconciles by **MAX, never
addition**, because its witnesses answer overlapping questions and adding
them would count one thing twice.

There are three witnesses now, and they do not answer the same question:

| witness | says | reaches events via |
|---|---|---|
| the production queue | what was **queued** — and never mentions buildings | `queue --> events` |
| the notification feed | what was **built** or **researched** | the readers, through `statsview` |
| the recorded game | what the player **ordered** | `replay --> statsview` |

That third one is why the table matters. The record says a building was
PLACED, the feed says it was BUILT, the queue says it was QUEUED: three
clocks on three different moments. A placement is not a rival reading of a
completion, so it can only ever be a **ceiling** — enough to convict a
reader that over-fires, never enough to convict one that under-fires. Used
the other way round, subtracted FROM a completion, it gives build duration,
which is what `durations` is for.

**Still a gap:** the live path does not go through `events`. It reconciles
after the game, from the stats file. The Town Centre decision the overlay
makes while you play is still a bare `max()` in `production.py`.

## 4. The two smaller applications

```mermaid
flowchart LR
    build_order --> loom_coach
    pace --> loom_coach
    reader --> loom_coach
    session --> loom_coach
    stopline --> loom_coach
    filters --> loom_read
    production --> loom_read
    reader --> loom_read
    session --> loom_read
```

`loom_coach` is the build order in a terminal; `loom_read` is a live readout
of the two HUD numbers and nothing else. Both sit on the same `reader`, which
is what makes `reader` worth keeping free of anything Qt.
