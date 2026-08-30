# Installing Loom on macOS

**Read this first: which version of the game you run decides what you get.**
The WINDOWS build of AoE2:DE running through CrossOver is the better route —
Loom reads it and overlays it, fullscreen included. Feral Interactive's
native macOS port is paused and known-degraded: it works windowed only, one
to two seconds behind. See [platform support](platform-support.md) for the
detail and the measurements.

## What you need

- **macOS 13 or newer** (ScreenCaptureKit)
- **Python 3.10 or newer**
- **Age of Empires II: Definitive Edition**, either way it arrives:
  - the Windows build, via Steam inside **CrossOver** — the better route
  - or Feral Interactive's native port — windowed play only

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

- **Fullscreen is fine under CrossOver, and only there.** Wine draws "full
  screen" as a borderless window on the ordinary desktop Space, so the
  overlay floats above it. The native Feral port gets a real macOS
  fullscreen Space, which no window can float above — measured against every
  window level and collection behaviour macOS offers — so on the Feral route
  play **windowed**.
- **Prefer the display's native resolution.** Rendering below it (1080p on a
  4K screen) upscales the HUD to about 2.9×. The anchor search reaches 4.0×
  so this should now work, but it has not been retested on a Mac; if the
  overlay never finds the HUD, drop the in-game HUD scale a notch.
- **HUD scale at 100%**, and the stock or Anne_HK Better UI HUD, as on every
  platform.
- **Notification duration at its shortest** (Options → Interface), also as on
  every platform. Loom counts Town Centres out of the game's own message feed,
  and every timing it uses for that was measured at the shortest setting.
- **Keep the game frontmost.** macOS only composites the front window, so
  backgrounding the game stops capture. Loom blanks rather than serving a
  frozen clock. This applies on both routes — it is how macOS composites,
  not how either port draws.

## Run it

```bash
python loom_app.py          # the launcher
python loom_read.py         # a diagnostic: just the two HUD numbers
python loom_coach.py        # the build order, in a terminal
```

## If Loom keeps "waiting for the game"

Loom finds the native port by its bundle id, and the CrossOver route by its
title **corroborated by the window's owner being a Wine host** — a CrossOver
bundle id or a bare `.exe`-named process. A title match with no such owner
is refused on purpose: a browser tab about Age of Empires must never be
mistaken for the game. If a future CrossOver names its windows differently,
`python -m tools.macos_probe --list` shows what the game's window actually
reports, and `--fragment` aims Loom's probe at it directly.

## Where Loom keeps your things

- Settings and match statistics: `~/Library/Application Support/Loom`
- Build orders: the `builds` directory in the clone, plus your own in
  the data directory above. **Import build** in the launcher puts them
  there for you, after checking the file; **Open builds folder** shows
  you where.

## Recorded games under CrossOver

The game inside a bottle writes its recorded games to the bottle's own
Windows filesystem, where Loom's search does not look:

```
~/Library/Application Support/CrossOver/Bottles/<bottle>/drive_c/users/crossover/Games/Age of Empires 2 DE/<steam-id>/savegame
```

Point Loom there with the same variable the Linux guide uses (quote it —
the bottle name has spaces):

```bash
export LOOM_RECORDS_DIR="$HOME/Library/Application Support/CrossOver/Bottles/<bottle>/drive_c/users/crossover/Games/Age of Empires 2 DE/<steam-id>/savegame"
```

Without it, everything except the recorded-game features works; with it,
**Attach recorded game** and the post-game Data columns find the match.

## Known limitations

- **No APM tracking** on either route — there is no macOS counter yet, and
  the launcher's toggle says so rather than counting nothing silently.
- **No global hotkeys** on either route — the build order can only be
  followed automatically.
- **The game must be frontmost**, both routes.
- **Latency under CrossOver looked fine in a real session** — at 4K with
  the stock HUD (Transparent UI mod on), the clock kept pace with the game
  by eye. Not yet measured by instrument, so the number is still owed. On
  the native port the measured ~1–2s poll cost is platform overhead — this
  backend converts the whole 33MB frame per poll where the Linux one
  fetches only the small regions it reads; per-region conversion is the
  first thing to try.
- **The game itself runs worse under CrossOver than natively** — that is
  the translation layer's cost, not Loom's, but it is the trade this route
  asks for.
- **The native (Feral) route: no overlay over fullscreen.** Windowed play
  only there.
