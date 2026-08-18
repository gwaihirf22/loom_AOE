# Installing Loom on macOS

**Read this first: macOS support is paused and known-degraded.** Reading works,
but Loom trails the game by one to two seconds, and the overlay cannot float
above the game's fullscreen Space. See
[platform support](platform-support.md) for the detail and the measurements.

Linux and Windows are the supported platforms. This page is here so the macOS
work is not lost, not because the experience is good yet.

## What you need

- **macOS 13 or newer** (ScreenCaptureKit)
- **Python 3.10 or newer**
- **Age of Empires II: Definitive Edition**, Feral Interactive's native port

## Install

```bash
git clone https://github.com/gwaihirf22/loom_AOE.git
cd loom_AOE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Screen Recording permission

macOS grants Screen Recording to **whichever application launched Python** — a
terminal, or an IDE — never to Python itself. So grant it to your terminal in
System Settings → Privacy & Security → Screen Recording, and quit and reopen
that terminal afterwards.

Without it, capture returns black rather than failing, which looks exactly like
"the HUD is not on screen".

## Game settings

- **Windowed mode.** Not fullscreen: the overlay cannot appear above a
  fullscreen Space. This was measured against every window level and collection
  behaviour macOS offers.
- **The game's resolution must be the display's native resolution.** Rendering
  below it (1080p on a 4K screen) upscales the HUD past the anchor search's
  ceiling. Loom prints a note naming this when it happens.
- **HUD scale at 100%**, and the stock or Anne_HK Better UI HUD, as on every
  platform.
- **Keep the game frontmost.** macOS only composites the front window, so
  backgrounding the game stops capture. Loom blanks rather than serving a
  frozen clock.

## Run it

```bash
python loom_read.py         # start here: just the two HUD numbers
python loom_coach.py        # the build order, in a terminal
python loom_app.py          # the launcher
```

The terminal coach is the more usable front end here, given the overlay's
fullscreen limitation.

## Where Loom keeps your things

- Settings and match statistics: `~/Library/Application Support/Loom`
- Build orders: the `builds` directory in the clone

## Known limitations

All measured, none worked around yet:

- **~1–2 second delay.** A poll costs about a second under game load. Neither
  of Apple's scheduling levers moved it. The prime suspect is that this backend
  converts the whole 33MB frame per poll where the Linux one fetches only the
  small regions it reads; per-region conversion is the first thing to try.
- **No overlay over fullscreen.** Windowed play only.
- **The game must be frontmost.**
- **No APM tracking.**
