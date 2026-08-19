# Changelog

All notable changes to Loom are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semantic versioning](https://semver.org/) with one project-specific
promise: **1.0.0 is the first release that also runs on Windows** — kept
on 2026-08-18.

## 1.0.1 — 2026-08-19

Everything the first day of 1.0.0 being in other people's hands turned up.
No new capability in the reader or the overlay: this is entirely about
getting a build order into Loom and having it look right when it arrives.

### Added

- **Import build.** A button on the launcher that takes a build-order JSON
  from anywhere, checks it, and copies it into your builds folder - then
  lists and selects it immediately, with no restart. Adding a build used to
  be an instruction rather than a feature: find a folder Loom had never
  created, make it yourself, drop a file in, start again.
- **The check behind it** (`loom/buildcheck.py`). "Will it load" is answered
  by loading it, so the answer cannot drift from the loader the launcher
  actually runs. Only a build Loom genuinely cannot use is refused; anything
  else is said plainly and left to the player, because the format belongs to
  the community and being pickier than the format would reject builds that
  work. It found a real one on the day it was written: a published build
  with 25 villagers spread across the resources at a step with 21.
- **Search and a civilization filter** on the build picker, because a
  library you can add to in seconds outgrows a flat list. The drop-down
  became a list that is always open, because typing into a search box whose
  results hide behind a click is not searching: every keystroke now shows
  its own answer, and Enter takes the top one. Asking for one
  civilization includes the Generic builds, which are playable as any civ -
  the count says how many, so it is never a surprise. Whatever is selected
  stays in the list whatever the filter says: a drop-down that could drop
  the current build would let Start run one nobody picked.
- **Open builds folder**, next to it, for everything the file picker is not.
- **Two How-to-use pages** - adding build orders, and finding one once the
  library is big enough to need finding.

### Fixed

- **Icon tokens are matched forgivingly.** buildorderguide's export writes
  `@resource/MaleVillDE.jpg@` where Loom's library holds the identical
  picture as `.webp`, so the first community build imported showed words on
  every instruction. The extension is now forgiven; the folder is not,
  because names repeat across folders and a confidently wrong picture is
  worse than plain words.
- **The builds folder is created.** `paths.user_asset_dir` existed for
  exactly this and was called by nothing.
- **Loom's own two build orders draw icons too.** Fast Castle and
  Uncounterable Fast Castle predate the icon library and were written in
  plain English, which looked like Loom failing on them.
- **The release zip unpacks on Linux and macOS.** PowerShell's
  Compress-Archive stored forward slashes in the zip's central directory
  and backslashes in every local header; Windows Explorer and Python read
  the former and unpacked it correctly, while unzip and Ark read the latter
  and produced 1600 files called `Loom\_internal\...` in one flat heap.
  `tools/package_windows.py` now builds the archive and refuses to return
  one whose local headers are wrong - the half nothing on Windows looks at.
- **A frozen bundle no longer writes into itself.** The overlay saves
  notification lines it cannot read for later study, and in a packaged copy
  that meant writing into the read-only program folder - one such crop
  shipped inside the 1.0.0 zip. Frozen, captures go to the player's data
  directory with their statistics.

### Changed

- **The README leads with using Loom rather than with how it was built.**
  The engineering walkthrough moved whole to `docs/how-it-works.md`; the
  Windows guide leads with the app and keeps running from source below it.
  Both now say what to do when Windows quarantines a fresh release, which
  is a machine-learning false positive on an unsigned file nobody has run
  yet.
- **The build-step icon library ships.** It was excluded from both the
  bundle and the public snapshot as game art, which quietly cost every user
  the pictures; it is redistributed under Microsoft's Game Content Usage
  Rules, with the notice in the README.

## 1.0.0 — 2026-08-18

The promise this number was reserved for: the first release that also runs
on Windows. Everything below shipped between 0.9.0 and today.

### Added

- **Build-order hotkeys, and the one piece of state Loom never had.**
  Ctrl+Shift+W and Q nudge the step forward and back; Ctrl+Shift+R stops
  and resumes following the game. The step keys are a *correction*, not a
  mode: they suspend automatic following for ten configurable seconds and
  then Loom picks the game back up by itself, which is what keeps "nobody
  has to press anything" true. Whenever the panel is not following it says
  so on its face — MANUAL, naming the key that resumes — because a manual
  cursor that looked identical to a synced one would be the same class of
  failure as a wrong villager count. All bindings are editable, each can be
  emptied, and there is one master switch; a registered combination is
  taken from the game, which is why none of that is negotiable. A fourth,
  launcher-owned key (unbound by default) starts and stops the overlay
  itself, and rebinding it applies live.

- **APM tracking on Windows**, counted inside the overlay with Raw Input —
  not a keyboard hook — reading only the device type and the press flag.
  `RAWKEYBOARD.VKey`, the field naming the key, is declared for the struct
  layout and never read, and a test checks that with the AST rather than
  trusting the comment.

- **Two overlay transparency sliders.** Background is true opacity of the
  dark card, 0% gone to 100% solid; text is a visibility scale whose
  midpoint is the designed look — below it fades, above it brightens every
  colour toward its vivid extreme for reading over bright terrain. Alert
  bands follow neither: they are alarms.

- **Place Overlay works without the game running**, measuring from the
  primary screen when no game window exists; a Reset position button
  forgets a bad spot; and a saved position that would land the overlay off
  every screen is ignored in favour of the default rather than obeyed —
  found the hard way, with a panel sitting invisibly at (742, -284). The
  default position moved to the top-right corner, under the game's bar,
  with the margin following the measured HUD scale.

- **The Loom banner**, as the window and taskbar icon for every window
  (with the AppUserModelID Windows needs to show it instead of Python's),
  in the launcher header, the How-to-use window, and the README.
  `images/loom.ico` carries the seven sizes packaging will want.

- **The How-to-use window grew to seven pages** — reading the panel,
  placing and appearance, settings and alerts — and now recommends the two
  companion mods (Anne_HK Better UI and the transparent-UI mod, linked,
  explicitly optional) and points at buildorderguide.com and the RTS
  Overlay web tool for finding and writing build orders. The How to use
  button moved to the launcher's top right, in blue.

- **Loom runs on Windows.** Reading, the overlay, the launcher, statistics
  and APM tracking all work, verified against live matches. The capture seam
  did exactly what it was built for — `loom/capture/windows.py` joined the
  table and nothing downstream changed.

  Capture is **Windows Graphics Capture**, chosen by measuring rather than
  reading. GDI's `BitBlt` would have fitted the pull-shaped Linux design
  perfectly and returns a frame that is 0.0% non-zero: that is what a
  Direct3D swap chain looks like through a GDI device context, and AoE2:DE is
  a Direct3D game. `PrintWindow` with `PW_RENDERFULLCONTENT` does work, at
  20ms a grab against WGC's 2µs region crop. `tools/windows_probe.py` is the
  throwaway diagnostic that established all of that before a line of the
  backend was written.

  Windows beats macOS on the limitation that matters most: **the game does
  not have to be in front.** WGC keeps delivering frames for a backgrounded
  window, where macOS stops compositing one and capture dries up.

  Display scaling is handled, and it turned out to be the pixel-constant rule
  wearing a new coat. A DPI-unaware process is *lied to* — `GetWindowRect`
  returns coordinates scaled by the display setting, so a 2560-pixel-wide
  window measures 1707 at 150% — and nothing raises. The anchor scale simply
  shifts under every pixel constant at once and digit recognition quietly
  degrades. Loom now declares per-monitor DPI awareness before asking for any
  rectangle, and keeps capture pixels and Qt points apart.

- **Per-OS install guides and a support matrix**, in `docs/`. What works on
  which platform was previously spread between the README, CLAUDE.md and two
  status notes, and the README's requirements section described Linux as
  though it were the only option. `docs/platform-support.md` is now the one
  place that answers it, and `loom/capture/README.md` is a map of the seam
  for anyone landing in that package.

- **A test suite that runs on every platform, on every push.**
  `.github/workflows/tests.yml` runs the 400-plus headless tests on Linux,
  Windows and macOS. Linux and Windows are now a dual boot on one machine, so
  "did this break the other OS?" used to cost a reboot; it costs a push.

- **`.gitattributes`.** Until now the line endings in any checkout depended on
  that machine's `core.autocrlf`, so one clone configured differently showed
  the whole tree as modified on the other OS. The rule travels with the
  repository now, and a CI job checks the committed bytes actually match it.
  Image formats are marked binary explicitly: a template is matched
  pixel-for-pixel, so one "converted" by a line-ending filter would misread
  silently rather than fail.

### Changed

- **Settings and match statistics have moved out of the source tree**, to
  `%APPDATA%\Loom` on Windows, `~/Library/Application Support/Loom` on macOS
  and the XDG directories on Linux. Keeping them beside the code was fine
  while Loom was only ever run from a git clone and wrong for every other
  way: installed, the tree is read-only; frozen into a one-file bundle it is
  a temporary directory deleted on exit, so a match's statistics would be
  written and then destroyed. This clears the first of the two prerequisites
  distribution has been waiting on.

  An existing clone's `config.json` and `stats/` are **copied** across on
  first run — copied and never over the top of anything, so a migration that
  turns out wrong leaves the originals reachable. Saved games only migrate
  while the new location is empty, so it cannot resurrect matches somebody
  deleted. `LOOM_DATA_DIR` overrides the lot.

  Captures deliberately stay in the tree: they are development scratch, not
  the player's data.

### Fixed

- **The stock HUD's narrow "1" no longer vanishes from readings.** It
  renders 3px wide against a 4px width gate and was silently skipped, so
  "21/30" read as "2/30" with total confidence — and stretched to the
  template box a bar defeats matching outright, so widening the gate could
  not fix it. A "1" is now recognised by shape, the way the slash already
  was.

- **A hollow digit no longer reads as two "1"s.** The bar rule's own
  regression, one day later: a harsh threshold eroded a "0" to its two side
  strokes and each read as a "1", so "10/15" became "111/15" and announced
  HOUSED five villagers early. Bars a sliver apart are merged back into the
  one glyph they are, and population plausibility now bounds how far
  current may exceed cap.

- **A Town Centre is no longer called idle the moment it starts working.**
  An item queued but not yet washed green showed no tint and no count, its
  identity score fell a hair under the gate, and the queue read empty. A
  slot that held a believed item one poll ago now corroborates the cell.

- **HOUSE NOW is now HOUSE SOON** — a build order already tells the player
  when to build a house, so the warning's job is a heads-up, not an order.

- **Stopping the overlay from the launcher no longer throws away the match's
  statistics on Windows.** `runner.stop` relied on `QProcess::terminate`
  being SIGTERM. On Windows it posts `WM_CLOSE` to top-level windows — a
  console child has none, and the overlay's is a ToolTip that Qt does not
  treat as the last window. Measured: the overlay ignored it entirely and was
  killed by the two-second escalation, with `aboutToQuit` never running. That
  hook is the final statistics write and the placement-mode offset save, so
  every press of Stop silently discarded the game just played.

  The polite request now travels as data instead of as a signal —
  `loom/stopline.py`, the mirror of `statefeed`: that carries the overlay's
  state up to the launcher on stdout, this carries "please stop" back down on
  stdin. Both requests are sent, so on Linux SIGTERM still lands first and
  nothing about that path changes. Measured after: a clean exit in 0.02s
  instead of a kill at 2.01s.

- **The click-through self-check answers on Windows.** It used to fall
  through to the X11 branch and print "cannot check click-through" — honest,
  useless, and for the one property whose failure is silent and costs a
  match. It now reads `WS_EX_TRANSPARENT`, which is the bit the window
  manager itself consults when deciding where a click lands.

- **`pyobjc` is declared in `requirements.txt`.** The macOS backend imported
  it and nothing installed it, so a fresh macOS install failed only when
  pixels were first requested rather than at install time.

- **A How-to-use window**, shown once on a fresh install and reachable any
  time from the launcher. It exists because nothing told a new player which
  HUD skins Loom can read, and getting that wrong looks exactly like the
  program being broken — Loom does not misread an unrecognised skin, it never
  finds the HUD at all and waits without explaining why. The page names the
  skins that work, the 100% HUD scale, and how to place the overlay. Pages
  are a plain list, so adding more is appending to it. The README said the
  *opposite* of the truth here — it listed "no mods that replace the
  resource-bar icons" as a requirement, which is precisely the mod Loom was
  built around — and now says what is actually true.

- **The build preview opens beside the launcher instead of behind it.** It
  was a parentless top-level window that nothing ever positioned, so
  stacking was the window manager's guess and the guess was usually "behind".
  It is now parented to the launcher — the one fix that behaves the same on
  X11, Wayland, macOS and Windows, none of which agree about whether a client
  may place its own windows — and positioned beside the launcher the first
  time, after which its position is remembered like its size already was.
  On Wayland the positioning may be ignored by the compositor; the parenting
  is what guarantees it stops hiding. The How-to-use window had the same bug
  the moment it was added — smaller than the launcher, so it opened perfectly
  hidden behind it — and got the same remedy.

- **The stock HUD can be read.** Every template Loom shipped was cut from the
  Anne_HK Better UI mod, so playing without it left the overlay on "Waiting
  for a match to start..." forever: the anchor search was FINDING the stock
  population icon and scoring it 0.743 against a 0.8 gate. `loom/hud.py`
  introduces a `HudProfile` — one skin's anchor templates plus the offsets and
  glyph metrics that go with them — and `reader.find_hud` picks between them
  by score at HUD acquisition. Re-anchoring afterwards only rechecks the skin
  it settled on; a UI mod cannot change without restarting the game.

  The encouraging half: given the right regions, the existing digit templates
  read the stock bar unchanged (clock 34, population 4/10, villagers 3 off the
  first live frame). This was a geometry port, not a recognition one.

  **An anchor may contain nothing that changes — including the civilization.**
  The first stock cut reached out into the wooden bar texture around the icon,
  because that chrome separated stock from the mod where the portrait alone
  did not. It scored 1.000 on the civ I measured and **0.59 on Portuguese**,
  whose bar is light stone and vines: the resource bar's border art is drawn
  per civ. Measured across two civs, the icon's own black box is
  pixel-identical (max channel difference 2) while the chrome around it
  differs on 83% of its pixels. Both stock templates are now exactly the icon
  box, stopping clear of the villager badge and the resource sub-count as
  well. Three stock civs are pinned in the tests.

  **One icon cannot name a skin, so two do.** Every skin draws the same game
  art, which is why the anchors resemble each other: the stock anchor scores
  0.91–0.95 on modded HUDs against the mod's own 0.93–0.97, a margin of about
  0.02. Deciding a whole session's read geometry on 0.02 is not deciding it.
  `identify_hud` now corroborates each candidate with that skin's wood icon at
  the scale its anchor proposes, and scores the pair by its weaker member — a
  skin really on screen has both icons where it expects them at one size. That
  turned 0.02 into ≥0.27 on every frame measured: three stock civs, 27 modded
  capture runs, both resolutions. True skin never below 0.91, wrong skin never
  above 0.71.

  Two more things measurement decided rather than taste. The glyph WIDTH
  metrics had to move into the profile: stock draws a font about four fifths
  the mod's size, its "/" is ~3px wide, and the shared minimum of 6 discarded
  it — `_parse_population` then found no slash and reported nothing at all on
  a perfectly legible HUD. Worse, it was value-dependent: "4/5" read fine at
  the mod's width and "4/10" did not, so the gap looked like flicker rather
  than a bug. And the stock clock band is deliberately wide: a snug band
  bracketed the clock at the scale it was measured at and clipped the last
  digit at 0.67, because the font does not shrink at quite the rate the icon
  art does.

  The queue came along, because it anchors off the wood icon and that is skin
  art too: the mod's template scores 0.682 on the stock bar and lands ~7px
  off, which would shift every slot box. `hud.STOCK` carries its own wood
  template and slot origin, fitted by the same edge-energy method as the
  mod's; `SLOT_PITCH` and `ROW_PITCH` stay shared, being the game's own grid.

  Per-resource villager sub-counts port too, and moved twice over: stock does
  not print that number below its icon, it stamps it white INSIDE the icon's
  box, bottom-right, in the same badge style the villager count uses. So the
  number strip is per-skin, the four resource icons needed stock cuts of their
  own, and `resources.read_one` gained the same colourless second pass the
  villager badge needs. Verified against both stock civs by eye (0/0/0/0 and
  0/2/0/0) with the modded path still reading 15/5/0/14.

  Regression evidence: the eight-game modded acceptance corpus replays to
  byte-identical Town Centre counts and notification fire times, through every
  round of this work. Live on a stock Portuguese game: skin identified at
  1.000, clock advancing, villagers, population, per-resource counts and the
  queue all reading.

- **`captures/` runs say what they are.** Folders are now
  `run_<timestamp>_<hud skin>[_<label>]` — the skin detected at capture time,
  because which HUD was on screen turned out to be exactly the thing that
  matters and exactly the thing nobody remembers. `tools/grab_frames.py`
  takes `--label` and slugifies it (one folder was called "run_20260731_152830
  _added multiple TCs. see notes in chat on screen", which said the right
  thing and no shell glob could touch it). `tools/index_captures.py` writes
  `captures/INDEX.md` from the folders themselves, so there is no second place
  to keep in sync — including the eight-game TC acceptance corpus and the real
  Town Centre counts, which existed only in a scratch file until now.

- **Capture is now per-OS.** `loom/capture.py` became `loom/capture/`, which
  picks a backend by platform: `x11.py` is the existing Linux code, moved
  unchanged. Every caller still says `from loom import capture`, so nothing
  downstream knows. `LOOM_CAPTURE_BACKEND` forces a choice, which is how the
  Linux backend can be checked from another machine — python-xlib is pure
  Python, so it still imports where it cannot connect. Importing the package
  never fails, even with no backend for the platform: the complaint is raised
  when something asks for pixels, not when a test imports `loom.reader`.
  `game_geometry` left `loom_overlay.py` to become `capture.window_geometry`,
  body unchanged.

- **macOS capture.** `loom/capture/macos.py` reads the game with
  ScreenCaptureKit, against Feral Interactive's native macOS port. It keeps a
  live stream per window and serves every read from its newest frame, so a
  poll's eight or nine region reads all come from ONE instant where X11 gives
  each its own — strictly more correct, since a villager count paired with a
  clock from 20ms later is exactly the torn read that desynchronises a build
  order. Verified against a live match: clock advancing, villager count
  correct, and the queue reader following villager production.

  Four things that had to be measured rather than assumed. Capture at the
  window's REAL pixel size — asking for its point size makes macOS downscale
  the game's render before Loom sees it, which took the HUD text from 21px to
  7px and turned 18 villagers into a confidently reported 8. Ask which display
  the window is on rather than the desktop's largest scale, or a 4K window on
  a 1x monitor gets doubled to 7680 wide and lands past the top of
  `anchor.COARSE_SCALES`. Pin the stream to 32BGRA or it delivers biplanar YUV
  that reshapes into plausible garbage. And take an Accessory activation
  policy: a normal app steals focus on launch, which backgrounds the GAME, and
  macOS stops compositing a backgrounded window — measured, Loom read the HUD
  perfectly once and then never again.

### Changed

- **The queue now counts Town Centres for the whole game, not the first
  two minutes.** Instrumented multi-TC test games proved the notification
  feed cannot carry the count alone: the game never reprints a line that
  is already on screen, so a TC completing while "--Town Center Built--"
  lingers announces nothing - a seven-TC game produced three line
  appearances. The queue's own drawing rules (verified in game) make it a
  safe witness at any time: each building shows at most one in-progress
  item, washing green as it works, while anything queued behind it waits
  amber (or red when the population cannot fit it), and villager batch
  depth is the white count on one icon, not extra slots. So the most
  GREEN TC items ever on screen at once is a floor on the TC count - a
  high-water mark, believed after holding one continuous game second,
  never lowered, still reconciled with the notification count by max().
  Green or untinted, the author's tested rule: an untinted item IS
  producing (a just-placed item whose wash is too young to classify), so
  counting it confirms a new TC seconds earlier - the residual same-type
  waiting-batch ambiguity is carried by the confidence gate, the
  three-read window, and the never-lowered high water, and the one game
  once blamed on it turned out to have that many real TCs. Corpus-
  verified: two undercounting games each recovered a TC, zero phantoms
  across seven games. Replay of a six-TC test game: queue evidence
  counted all six where notifications alone saw four.

- **A reprinted notification now fires however recent the last one.** TCs
  built ~18 game-seconds apart each reprint their line after the previous
  expires, but the sighting-refreshed cooldown swallowed every one after
  the first (measured: 3 built, 1 counted). A phrase absent from its
  bands for three consecutive looks now clears its cooldown - a reprint
  after real absence is by definition a new event. A lingering line is
  sighted every look and can never rearm itself, which is what makes this
  safe where shortening the cooldown was not: a 0.3-second cooldown
  experiment refired one lingering line thirteen times ("13 TCs IDLE").
  With both changes, replays of the four instrumented test games count
  6/≥6, 4/4, 2/2, and 5/7 TCs (the last: two TCs that never trained and
  whose completions the game deduped are invisible to every channel).

### Added

- **`python -m tools.tc_debug` - a live view of the TC tracker's
  thoughts.** Prints the believed TC count (both evidence channels),
  busy/idle, and every queue slot with identity, tint, score and margin -
  but only when something CHANGES, so pausing the game freezes the
  readout instead of burying it. Built for discrepancy hunting: watch it
  beside the game, pause the moment the numbers disagree with reality,
  and compare the frozen queue cell by cell. Slot markers show exactly
  which cells feed the TC total (#), which were refused as unconfident
  (*), and which count a TC as busy (b).

### Fixed

- **A HUD Loom cannot read now says so.** An anchor that was FOUND but scored
  under the gate was indistinguishable from no anchor at all: `wait_for_hud`
  looped on a silent `False` while "Waiting for a match to start..." stayed on
  screen with a match plainly running. That is exactly how the stock HUD
  presented, and there was nothing to go on. The closest skin and its score
  are now printed once, naming a UI mod as the likely cause.

- **The villager count no longer loses its tens column.** Caught live on the
  stock bar: the band read **"2" out of "12"** and reported it confidently.
  The badge is right-aligned, so it grows leftward as villagers are trained,
  and a band cut snug around the single digit of an opening position clips
  every later reading. Villagers are the only signal the build order advances
  on, which makes this the worst reading Loom can produce. The band now has
  room for three digits, and `digits.read_count` gained a second pass for
  skins that stamp the number onto the portrait rather than into a dark box —
  the white-and-colourless test, at its own threshold, because the badge font
  runs dimmer than the bar's and the shared 190 ate the base off a "2" and
  left a confident "7". The dark-box pass still runs first and unchanged.

- **The queue can no longer anchor itself a thousand pixels from the queue.**
  `MIN_WOOD_SCORE` gates how well the wood icon matched but never asked WHERE:
  measured, the mod's wood template matches the stock bar at x=1078 with 0.701
  — past the gate — which would have hung the whole slot grid off terrain and
  read confident nonsense. The icon is the leftmost thing in the resource bar
  or it is not the icon.

- **Amber never means producing - and the idle warning now knows it.** An
  amber queue item is either waiting behind its building's in-progress
  item or blocked at the population cap; in both cases nothing is being
  made. The busy accounting counted TC techs at any tint, so one TC
  holding wheelbarrow(red) + town_watch(amber) + imperial_age(amber) read
  as three busy TCs and a freshly built second TC sat idle with no
  warning - caught live with tools/tc_debug on its first outing. Amber
  items are now ignored by the idle logic entirely, amber-alone included:
  whatever the reason, that TC is not producing, and not producing is
  what the warning is for. (Green, untinted-young and red-blocked items
  each still count their building's front as busy.)

- **Every researchable tech now has the game's own icon as its template.**
  The Elite Skirmisher upgrade spent two games unrecognised - first
  minting a phantom TC as "villager_male 0.42", then showing as an honest
  "?" once the confidence gate landed. The game ships named, authoritative
  art for every tech (widgetui/textures/ingame/tech/, 300+ files), and the
  template builder now sweeps it wholesale; scenario-only junk and boxy
  tower art that stole fixture identities stays excluded. The sweep also
  solved a week-old mystery by exposing a mislabelled fixture: the
  "amber battering ram" cell that unrelated techs kept outscoring is
  actually an amber HUSSITE WAGON (author-identified) - nothing matched
  it well because nothing was it. It now anchors a hussite_wagon
  identity, cut from the live cell itself via the builder's new CELL:
  source type, and the techs excluded for "beating the ram" are back. The
  occupancy content gate now keys on the vetted identity rather than a
  raw score - with five hundred templates, junk always lucks past a fixed
  score against SOMETHING, but the clear-win and margin gates have
  already turned an unconvincing match into an honest None.

- **An unknown icon's least-bad guess can no longer prove a Town Centre.**
  Third occurrence in two days settled the pattern: an icon the template
  set does not know (this time an Armenian unit, before it the Husbandry
  horseshoe and an upgrade shield) takes whatever name matches least
  badly, and that guess lands on villagers and TC techs often enough that
  each occurrence minted a phantom TC. The fingerprint is a flat ranking:
  the Armenian cell read villager_male 0.42 with castle_age 0.42 right
  behind, while a real villager scores 0.6+ or beats every other identity
  family by 0.08+ (the two villager sexes count as one family - a female
  edging out the male is not ambiguity). Green items now prove a TC only
  with a confident identity (TC_EVIDENCE_SCORE / TC_EVIDENCE_MARGIN in
  production.py, margin carried through SlotReading); busyness
  deliberately ignores the gate - a misnamed green cell still proves
  something is producing, and erring busy is the safe direction. This is
  the durable answer to new-DLC icons: template coverage makes names
  right, but the count no longer depends on it.

- **The rearm guard could not see the whole stack, and refired lingering
  TC lines every ~10 seconds.** A sandbox game believed 8 TCs where 4
  existed: the game printed TC lines in exactly four pixel windows (three
  real completions, one echo), but the live watcher fired seven times -
  refires INSIDE continuous windows. A line rolled above the two bands it
  may fire from counted as "absent", rearmed its cooldown after three
  looks, and fired as a fresh arrival when the churn brought it back.
  Three changes close every path: absence now means gone from EVERY band
  (a rolled-up line is still on screen); a phrase seen anywhere on the
  previous look can never be a fresh arrival (the game never reprints a
  visible line); and an extra-band fire needs EXTRA_QUIET_SECONDS of
  fireable-band silence first, because the feed redisplays history above
  new messages WITHOUT a fade - measured: an echo resurfaced one-up 17
  game-seconds after its line expired, while the real sibling-arrival
  completion followed 81 seconds of silence. Replayed: the sandbox game
  counts exactly 4, every fire lands on a real completion, and the
  sibling case still fires.

- **Weak queue identities flapped, and the idle count danced with them.**
  The stale-cache fix made low-scoring cells re-rank every poll, so two
  similar portraits traded hair's-breadth wins (villager/monk around
  0.50) and the cell dropped out of the TC busy count each time the
  villager lost - "5, 6, 7 TCs idle" fluctuating while four TCs sat
  constantly queued. A challenger now takes an occupied slot only by
  beating the incumbent's current score by IDENTITY_HYSTERESIS (0.05);
  genuine content changes flip decisively (the militia case measured
  0.15) and coin-flips hold steady.

- **Research at OTHER buildings could still wear a TC identity.** The
  Husbandry horseshoe (no template) read as flemish_militia and an elite
  skirmisher upgrade shield read as villager_male - both green, both
  counted toward the TC total. The template builder now sweeps every
  production-building directory automatically (~250 templates), so
  whatever is researching has its right answer available, with an
  exclusion list holding two invariants: buildings/walls/towers never
  appear in the global queue (and their boxy frames made synthetic decor
  look "convincing", breaking the occupancy content gate), and any auto
  template that outscores a correct identity on a real fixture cell is a
  measured thief and stays out (farm, pasture, hull planking, arson).

- **A stale cached identity could dress a military batch as a villager -
  and mint a phantom TC.** Frame-verified in a live one-TC game: while
  Wheelbarrow researched (green, 0.90), the neighbouring slot read
  "villager_male, green, 0.45" for ten-plus seconds - but a fresh search
  on the same pixels ranked militia 0.64 over villager_male 0.49. The
  identity cache was the culprit: queue contents shift left as groups
  finish, and the re-verify check ("does the cached name still score
  within 0.12 of before?") let the militia batch inherit the previous
  occupant's villager name for its whole training run. Two green "TC
  items" sustained, and the high-water count never forgets. A cached name
  that cannot score clearly (CLEAR_IDENTITY_SCORE) now re-earns its slot
  through the full search, which picks the right unit. Replayed: the
  phantom game never shows a second green TC item; every multi-TC corpus
  game still counts the same.

- **A tech handover could mint a phantom Town Centre.** A live one-TC
  game counted two: the stats showed feudal_age and loom sighted four
  seconds apart with no Town-Center-Built event - the classic shape of
  Loom finishing as the age-up starts, both cells wearing green for a
  glance or two. The "sustained one game second" validation was
  satisfiable by exactly two glances under load shedding (queue reads
  arrive ~0.9s apart), so a transition artifact confirmed. Queue evidence
  now needs three game seconds AND three distinct reads, every read
  agreeing - transitions die, and real multi-TC evidence, which persists
  for minutes, never notices. Found alongside: the Loom and Wheelbarrow
  icons classify as amber/red BARE (their straw-and-wood art is warm
  enough to read as a wash), which is worth knowing when reading queued
  tech tints.

- **Units that look like villagers can no longer mint Town Centres.** An
  identity wins by default when the real unit's template is missing, and
  bare-chested or robed foot units lean villager once zoomed to 40px - a
  live longbow queue once raised the TC count. Auditing every image in
  the master library as a fake queue cell against the template set found
  30 units whose best match was a villager (Slinger 0.69, Temple Guard
  0.65, Janissary 0.63, the Champion line, Jaguar Warrior...) and 51
  images matching TC techs - the tech floor already blocked all 51. The
  26 queueable offenders (plus longbowman and the hoardings tech) now
  carry their own templates, built from the same library by the existing
  tool; re-auditing leaves only the villagers' own art, one building icon
  that can never be queued, and one scenario-only hero. The template set
  grew 67 -> 93; identification stays cached, so steady-state polling
  cost is unchanged, and every capture-corpus replay counts the same TCs
  as before.

- **Queue identities are vetted before they are believed.** Terrain that
  sneaks past the occupancy edge test used to take whatever icon matched
  least badly and wear it confidently - a landlocked Dark Age game read
  "galley" at 0.5 for hundreds of polls. Measured across the capture
  corpus: junk matches everything a little and nothing well (best
  0.46-0.52, runner-up 0.03-0.07 behind), real portraits win clearly
  (green villager margin 0.13) or carry a wash or count numeral that
  proves something is drawn there. Two gates in _identify_cached encode
  that: an identity with no corroborating wash or numeral must score 0.6+
  or beat the runner-up by 0.08, and a tech claim must score 0.7+
  regardless (every real tech icon reads 0.91+; a weak "tech" is a
  misread unit batch, and one once credited a TC with wheelbarrow
  research while a halberdier batch trained). Refused identities read as
  an honest None, which also ends the occupancy walk on junk cells.
  Age-up research needs no change: the age techs already count the TC
  as busy at any tint, and prove a TC exists only while green.

- **Civ decoration read as a blocked villager and quietly ate one idle TC.**
  Some civilizations drape artwork from the resource bar straight through
  the queue grid, and the red tapestry's saturated pattern classified as a
  RED wash - a permanently "blocked" phantom group that marked one TC busy
  forever, so a six-TC game peaked at "5 TCs IDLE". The occupancy content
  gate cannot catch this (a wash counts as content), so known decorations
  are now harvested as templates (templates/queue_decor/) and matched
  explicitly: a slot that matches a decoration ends the queue there. A
  real card drawn over the art covers its pattern, so decorated civs keep
  reading normally. One art harvested so far; other civs' drapes get
  added as captures surface them.

- **A whole game's Town Centres went uncounted, and the idle-TC story with
  them.** Replaying a captured 3-TC game frame by frame found the chain:
  the HUD ran a hair under 100% scale, and `notifications.watch` skipped
  template resizing inside a ±0.02 tolerance — across the 233px
  `town_center_built` template that 1-2% misalignment bled the match score
  to ~0.77 against the 0.8 gate, so every TC completion (and a real attack
  warning) went unseen. With `tcs_seen` stuck at 1, one queued villager
  anywhere marked "the TC" busy: the game's true 2,281 idle-TC-seconds
  recorded as 217, flickering "TC IDLE" where "3 TCs IDLE" belonged.
  Templates are now always resized and tried at a small bracket of scales
  around the anchor's measurement (the HUD font does not track the icon
  scale exactly; the steps must be finer than 1%), cached per scale.
  Verified by replay: both TC events fire at their true times, idle
  seconds land on the corrected figure exactly, and live fixtures from
  the failing game hold it in the test suite.

- **A Town Centre finishing beside another event was invisible by
  design.** The bottom-line rule (which exists to stop redisplayed
  history minting imaginary TCs) also discarded a TC line born one line
  up because a sibling event took the bottom of the same redraw —
  measured live: TC #3 arrived with Heavy Plow beneath it. The phrase now
  gets a guarded second line: it fires from one-up only when the feed
  never faded and the phrase was not there the look before — a
  redisplay-after-fade fails both tests, and an echo sighting still never
  touches the cooldown. Two TCs finishing together fire twice, and the
  overlay counts occurrences rather than membership.

- **The glyph text watcher read nothing, all game, at any HUD scale off
  the harvest pixels.** Two independent faults. Its line finder counted
  any bright pixel as ink, so sunlit terrain formed phantom bands and the
  bottom band was junk on almost every look (the misaligned crops piling
  up in captures/notif_unread were these); ink now has to sit beside the
  font's near-black outline, the same test the phrase watcher's band
  finder uses. And the font was overfit to its harvest rendering — a 2%
  resample dropped glyphs below the 0.8 gate and one failed glyph killed
  the whole line; the font now carries deduplicated resampled variants,
  one slightly-soft glyph per line is tolerated (KNOWN_WORDS remains the
  event-level backstop), and a digest-keyed cache keeps the bigger font
  at ~4ms steady per look. The line pitch constant also corrected 26→28,
  which had the splitter carving fused stacks into half-lines.

- **Queue slot crops read reference-pixel corners at any HUD scale.**
  `classify_tint`'s interior margin and `read_count`'s numeral corner
  were fixed pixel offsets on a scale-sized cell — at 150% they sampled
  the top-left quarter, at 75% they clipped nothing — and the count
  reader's speck gate was a hardcoded 5. All three now follow the scale,
  with the gate capped at its tuned reference value.

- **One in five live polls refused a perfectly legible clock.** The lag
  probe's saved bands made the failure replayable, and it was two defects.
  The clock band arrives far taller than its text at large HUD scales, and
  the shape filter keeps a glyph only if it spans 35% of the BAND height —
  35% of the measured 55px band is 19.25px, exactly glyph height, so
  whichever digits eroded a pixel short that frame silently vanished
  ("00:07:51" lost its 5). The band is now trimmed to its densest ink rows
  first (`_fit_clock_rows`), which makes the ratio safe by construction. And
  the Feral 4K renderer's "5" scored 0.54 against templates cut from another
  renderer, one half-point under the bar; it joined the set as a variant,
  cut from the failing band by the same code the runtime uses. After both:
  every legible band in the corpus reads its correct value, and four bands
  became fixtures. A leading-junk retry was tried alongside and removed
  once measurement showed the fitted band made it redundant.

- **The overlay froze for seconds at 4K whenever it re-anchored.** The
  anchor search swept 31 scales over the full-resolution strip and then 21
  more over the same strip; under live game load that cost 13–15 seconds,
  inside the poll. The coarse pass now runs on a half-size copy, the fine
  pass searches only around the coarse winner, and re-anchoring passes in
  the scale it already knows. Measured live: 0.27s, same answer on every
  corpus frame, held there by tests.

- **Stale capture frames on macOS.** The stream callback converted every
  33MB frame at 10fps on a machine the game was saturating, so the cached
  frame ran up to 5.8s old. The callback now swaps a buffer reference and
  conversion happens only when a poll asks — frame age fell to ~50ms. Under
  measured overload the reader also sheds its advisory work (queue,
  notifications, per-resource) to every-Nth-poll; sync signals are never
  shed, and a healthy machine sheds nothing.

### Known limitations on macOS

- Readings run ~1–2 seconds behind the game under load: a poll costs ~1s,
  and neither App Nap opt-out nor thread QoS promotion changed that (both
  measured, both removed). Acceptable for now; noted honestly.
- Validated only with the game rendering at the display's native 4K.
- The game must be frontmost (macOS composites only the front window), and
  the overlay cannot appear over the game's fullscreen Space — windowed
  play is the supported mode.

- **Population unreadable above HUD scale 1.0.** The sibling of the
  `min_glyph_width` bug below, and found the same way.
  `MAX_POP_GLYPH_WIDTH = 13` is the width above which a run is treated as two
  characters touching and split at its thinnest column. That is right for a
  slash brushing the digit after it, and wrong for a digit that is simply
  large: measured at HUD scale 1.48, a "4" is 15px and a "5" is 14px, so the
  fixed 13 halved both and "4/5" read as nothing at all.

  It is now `reader.max_glyph_width(scale)`, threaded through
  `digits.read_population`. Never below the reference 13, so at HUD scale 1.0
  and under this is exactly the constant that has always been used and nothing
  changes. Erring large is the safe direction: too large merely leaves a
  genuine pair fused, which fails to classify and reports no reading, while
  too small carves a real digit into halves that classify as something else.

- **A capture failure no longer takes the program down.** Nothing caught one:
  `LiveController.tick` calls `poll()` bare from a Qt timer, so quitting the
  game mid-session raised `Xlib.error.BadWindow` straight out of the timer and
  ended the overlay. Backends now translate their own platform's failures into
  one `CaptureError`, and `poll()` degrades to an unreadable Reading — telling
  the session tracker the truth so a vanished game is still noticed, and
  printing the complaint once on the transition rather than three times a
  second.

- **A silently halved villager count at large HUD scales.**
  `min_glyph_width` skips runs too narrow to be characters, but it was
  `int(6 * scale)` and "1" is far thinner than its siblings — 7px against
  12–13px in the same band. At HUD scale 1.37 the threshold reached 8 and
  deleted the "1": a population of 19/25 read as **9/25**, and 18 villagers
  as **8**, both reported confidently. A wrong villager count desynchronises
  the whole build order, so this was the exact failure the never-guess rule
  exists to prevent.

  The final formula is the old one with a ceiling: `max(4, int(6 * scale))`
  capped at 6, so at HUD scale 1.0 and below nothing changes from the value
  months of live play proved, and above it the threshold can never reach the
  7px "1" it was measured deleting.

  The first attempt replaced the formula wholesale with a gentler
  `int(4 * scale)`, justified by the clock fixtures reading at widths 2–5.
  That shipped a regression on both platforms — misread villager counts, a
  clock that lagged as the filter kept rejecting garbage, and an overlay
  that could no longer attach to a match in progress, because mid-game clock
  values misread too often to ever confirm while the mostly-zeros opening
  read fine. The flaw in the reasoning: those fixtures are RESCALED
  screenshots (their own docstring says so), carrying smaller glyphs than
  the live game ever shows. Fixture evidence is not live evidence, and the
  tests now pin the proven values instead of re-deriving them from
  fixtures. `LOOM_MIN_GLYPH_WIDTH` overrides the value at runtime so future
  disputes get settled against a live game in seconds, and `loom_read.py`
  now prints a loud `villager JUMP` line whenever the raw count leaps by
  more than 3 in one poll, so an intermittent misread is caught in the log
  rather than by staring.

- **The anchor search cost seconds at 4K, and froze the overlay with it.**
  `find_icon` swept 31 coarse scales over the full-resolution strip, then 21
  fine scales over the same full strip — on a 3840×2160 frame under live game
  load, 13–15 seconds, and it runs on the re-anchor path *inside* `poll()`,
  so every re-anchor stalled the overlay for that long. Two changes, neither
  allowed to move the answer: the coarse pass runs on a half-size copy (it
  only decides roughly what size), and the fine pass searches only around the
  coarse winner instead of re-scanning the whole strip. Re-anchoring also now
  passes the known scale in, turning the full hunt into a nine-step sweep,
  with a fallback to the full hunt if that scores badly. Measured live at 4K:
  first anchor 13.5s → well under a second on saved frames, re-anchor
  **0.27s** (was 7.5–15s). `tests/test_anchor_speed.py` holds the fast path
  to the naive one's answer on every corpus frame.

- **Notification reader vs a real 78-minute game**: wrapped two-line
  attack warnings now count from their fixed first line; fused line
  stacks split at row-ink valleys; terrain can no longer fake a text
  line; identical repeated events count via stack-signature semantics
  (was: 8 recorded villager creations in a 145-villager game); and two
  never-guess plugs — full "--…--" framing required, plus a game-word
  vocabulary gate that refuses confident misreads like "Slege Ram".

- **Imaginary Town Centres from notification echoes.** The game's feed
  redisplays recent history above every new message, so one real "--Town
  Center Built--" line kept resurfacing and re-firing past the cooldown —
  a Turks game counted 7 TCs where 4 existed. A phrase now fires only as
  the stack's BOTTOM line (fresh messages always arrive there; echoes sit
  above newer lines), found via outline-aware text bands that ignore
  bright terrain behind a translucent HUD. Echo sightings no longer touch
  the cooldown either, so an echo cannot mask a real later TC.
- **Queue-based TC evidence now closes after the opening.** Its one job
  is catching a multi-TC start; past 120 game-seconds only the
  notification feed may raise the TC count, so a persistent queue misread
  can no longer mint a phantom TC that nags for the rest of the game.
- **`tools/replay_queue.py` now runs the live reader's clock filter.**
  Passing the raw clock made replays stricter than reality — a real TC
  was missed only because the clock band failed on exactly the frames
  where its line sat at the bottom of the stack.

- **"+N VILL" fired on every slightly-ahead build.** The extra-villager
  badge and the pace clamp now bite only during a HOLD — consecutive build
  steps repeating a villager count, which is how builds write "stop
  training" across an age-up. Being early to a normal checkpoint reads as
  AHEAD again, as it should.
- **Tint semantics corrected by a frame-by-frame audit.** Amber marks any
  merely-waiting queue item (behind another item, or pop-blocked), so it no
  longer fires a "pop capped" event; red keeps its meaning: housed. Queued
  items are always already paid — the game has no "can't afford" state.
- **Techs never carry count digits — enforced.** A confident tech identity
  sheds a phantom count (the age shield's III strokes used to read as
  "x11"); a weak "tech" identity carrying a real count is a misidentified
  unit batch and is discarded — the mechanism that credited a Town Center
  with research a barracks was doing, masking real idle-TC warnings.

### Added

- **Notification font reader** (`loom/glyphs.py`): the game's event lines
  read as text via a harvested character set — one font, infinite
  vocabulary. "--Mill Built--", "--Knight Created--", "--Fletching
  Research Complete--" become structured events in the statistics without
  a template per phrase, an OCR engine, or an AI backend. Works across
  all eight player colours; one unreadable character drops the whole line
  (never guess), and dropped lines self-save for harvesting with
  `tools/build_notification_font.py`.

- **Build-complete report rows** for extra villagers and villagers lost
  (confirmed count drops, attributed to a raid within 20 seconds of an
  attack warning; wild-animal warnings excluded).
- **Frame-audit workflow**: `tools/replay_queue.py` replays a capture
  through the full pipeline, one belief line per frame plus the final
  report — the same run that surfaced everything under "Fixed" above.

## 0.9.0 — 2026-07-25

The first versioned release. Everything below landed in one long push on
top of the unversioned prototype.

### Added

- **The launcher** (`loom_app.py`): one window to pick a build order,
  start/stop/place the overlay, tune settings, and reach the developer
  tools. Everything it starts runs as a child process and dies with it.
- **Build preview**: the build order as a resizable window of step cards —
  previous, current (highlighted), and the next two. Browse it by click or
  wheel before a match; it follows the live game while the overlay runs.
  Resizing the window scales the cards.
- **Live state channel**: the overlay reports its state as sentinel lines
  on its own stdout (`loom/statefeed.py`); the launcher routes them to the
  preview. One producer, one consumer, no sockets.
- **Overlay size settings**: two independent knobs — overall size (grows
  everything) and text size (grows the writing and the panel's height,
  never its width).
- **Alert settings**: idle-TC taper thresholds, the HOUSE NOW headroom
  threshold, and on/off switches per alert family.
- **Stacked alert bands**: housing trouble and an idle TC can both be true;
  both now show.
- **Build-completion report** on the overlay: pace verdict, TC idle time,
  villagers lost (with raid attribution), milestone timings.
- **Post-game statistics**: one JSON file per game in `stats/` — the build
  report, the full-game summary, and a per-second timeline
  (`loom/gamestats.py`).
- **Statistics window** in the launcher: per-game tabs for the build
  report, the post-game summary, and graphs drawn with plain QPainter.
- **APM tracking** (`tools/apm_counter.py`): a background counter for
  keystrokes and clicks per minute — counts only, by construction it never
  knows which key. Launcher toggle, aligned to game time in the stats file.
- **Game-event reading** (`loom/notifications.py`): the game's own
  notification lines (attacked, Town Center built) harvested by template
  matching.
- **Developer mode** in the launcher: demo mode, the coach simulator,
  capture tools, a passthrough check, and the test suite streaming into
  the window.
- Eleven community build orders in `builds/`, downloaded unchanged in the
  RTS Overlay format.

### Fixed

- **Clock reading on civilizations with light decorative borders.** The
  clock band sits on the civ's architecture-set border artwork, which
  differs per civ — light sets (Portuguese, Vietnamese, other stone
  styles) glint brighter than a plain brightness threshold, so the digit
  segmentation broke and the new-game reset silently never fired on those
  games. The clock and population reads now use a white mask (bright AND
  colorless, two-pass strict/soft) with shape cleanup, and clock parsing
  stops at six digits so trailing "(Normal - 1.7)" text cannot poison a
  read. Player color was a coincidence of which civ had which border.
- **The overlay no longer breaks the game's mouse confinement.** Qt-level
  click-through never told the X server anything; the pointer genuinely
  entered the panel and the game dropped its cursor clip. The panel's X11
  input region is now empty (`WindowTransparentForInput`), verified at
  startup by `loom/passthrough.py`.
- The overlay exits cleanly on SIGTERM, so stopping it from the launcher
  saves stats and placement offsets.

## Pre-0.9.0 (unversioned prototype)

Reconstructed from git history; no dates were recorded per feature.

- **OCR pipeline**: reading the villager count and game clock off the HUD
  by template matching — anchor detection, digit recognition, debounce
  filters that must never stick, session tracking.
- **Build-order engine**: RTS Overlay JSON loading, count-plus-time step
  lookup (villager count alone cannot identify a step), pace tracking.
- **The overlay**: frameless, always-on-top ToolTip-type window over the
  fullscreen game, run through XWayland — both found by experiment.
- **Queue reading**: the global production queue's slots, tints and unit
  identities; production state (idle TCs, housed, pop-capped) and the
  alert policy on top of it.
- **Terminal coach** (`loom_coach.py`) and raw readout (`loom_read.py`).
