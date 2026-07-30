# Changelog

All notable changes to Loom are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/); versions follow
[semantic versioning](https://semver.org/) with one project-specific
promise: **1.0.0 is reserved for the first release that also runs on
Windows.** Until then, 0.9.x is the Linux feature set settling down.

## Unreleased

### Added

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

### Fixed

- **A silently halved villager count at large HUD scales.**
  `min_glyph_width` skips runs too narrow to be characters, but it was
  `int(6 * scale)` and "1" is far thinner than its siblings — 7px against
  12–13px in the same band. At HUD scale 1.37 the threshold reached 8 and
  deleted the "1": a population of 19/25 read as **9/25**, and 18 villagers
  as **8**, both reported confidently. A wrong villager count desynchronises
  the whole build order, so this was the exact failure the never-guess rule
  exists to prevent.

  The fixtures already disagreed with the runtime and nothing noticed: all
  four clock bands in `tests/data/clock` read correctly at widths 2–5, while
  the runtime's value of 6 at scale 1.0 broke two of them —
  `test_clock_themes.py` passes hand-picked widths, so the live formula was
  free to drift. It is now `reader.min_glyph_width()`, a named function with
  its own tests that drive the real fixtures through the *runtime* value and
  assert the threshold can never overtake a "1".

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
