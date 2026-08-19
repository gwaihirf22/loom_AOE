# Installing Loom on Linux

The original platform, and the one everything is developed against first.
See [platform support](platform-support.md) for the comparison.

## What you need

- **Python 3.10 or newer**
- **X11, or Wayland with XWayland.** Developed on Bazzite / KDE Plasma /
  Wayland. Loom reads the game's XWayland window directly, which is why
  Wayland is fine even though screenshotting the desktop there is not.
- **Age of Empires II: Definitive Edition**, through Proton

## Install

```bash
git clone https://github.com/gwaihirf22/loom_AOE.git
cd loom_AOE
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On an immutable distribution (Bazzite, Silverblue) use the venv and do not
install anything system-wide.

Check it before involving the game — this needs nothing but the install:

```bash
python loom_overlay.py --demo
```

That replays a whole match on your desktop in about a minute.

## Game settings

- **Full screen** mode. Not "Full desktop", which needs identical monitors
  forming a rectangle.
- **HUD scale at 100%** (Options → Interface). Loom detects the HUD at whatever
  size it is drawn, but digit recognition degrades away from 100% and Loom
  warns when it measures otherwise. (The slider tends to report 99% however it
  is set; that 1% is well inside the tolerance.)
  Keep it at **90% or above**: measured at 2560x1440 on the stock HUD, 85%
  makes the HUD unfindable while 90–125% works.
- **The stock HUD, or the Anne_HK Better UI mod.** Loom knows both and works
  out which is on screen by itself. Another UI mod that replaces the
  resource-bar artwork needs its own profile; Loom says so rather than waiting
  silently.

Two mods are worth installing alongside Loom — recommended, not required:

- [Anne_HK — Better UI](https://www.ageofempires.com/mods/details/3762) is the
  layout Loom was originally built against: more room, standardised item
  locations, every read a little easier. Fully supported and auto-detected.
- [The transparent-UI mod](https://www.ageofempires.com/mods/details/2532)
  clears the per-civilization border artwork from around the HUD — the main
  source of reading trouble. Caveat: it does not cover every civ, and the
  newest civs (the most common offenders) are the least likely to be covered.

## Run it

```bash
python loom_app.py          # the launcher: pick a build, start the overlay
python loom_overlay.py      # the overlay directly, over a running game
python loom_coach.py        # the same thing in a terminal
python loom_read.py         # just the two HUD numbers, for checking
```

Start with `loom_read.py` with a game on screen. If it prints a villager count
and a clock that both move, everything downstream will work.

## Where Loom keeps your things

Following the XDG base directory spec:

- Settings: `~/.config/loom/config.json` (or `$XDG_CONFIG_HOME/loom`)
- Match statistics: `~/.local/share/loom/stats` (or `$XDG_DATA_HOME/loom`)
- Build orders: the `builds` directory in the clone, plus your own in
  the data directory above. **Import build** in the launcher puts them
  there for you, after checking the file; **Open builds folder** shows
  you where.

If you previously ran Loom from a clone that kept `config.json` and `stats/`
beside the code, they are copied across the first time you start it. The
originals are left where they are.

## APM tracking

Optional, off by default, and switched on in the launcher's settings. It reads
raw X input events to count keys and clicks per five-second bucket. It records
**counts only** — never which key, never what was typed, and it never looks at
any other window.

## If something is wrong

**Loom waits forever and never finds the HUD.** Check the HUD scale is 100% and
that you are on a supported HUD. If the game is on a second monitor or Loom
cannot find the window at all, `python loom_read.py` prints what it is doing.

**The overlay is not click-through — the mouse escapes to another monitor.**
Loom checks this at startup by asking the X server for the window's input
region, and prints a line if it failed. Report it with that line; it should
never happen, and when it does it costs a match.

**The overlay does not appear over the game.** It needs to run under XWayland;
`loom_overlay.py` sets `QT_QPA_PLATFORM=xcb` itself for this reason.
