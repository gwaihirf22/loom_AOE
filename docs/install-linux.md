# Installing Loom on Linux

The original platform, and the one everything is developed against first.
See [platform support](platform-support.md) for the comparison.

## What you need

- **X11, or Wayland with XWayland.** Developed on Bazzite / KDE Plasma /
  Wayland. Loom reads the game's XWayland window directly, which is why
  Wayland is fine even though screenshotting the desktop there is not.
- **Age of Empires II: Definitive Edition**, through Proton
- **Python 3.10 or newer** — only for the source install below. The Flatpak
  brings its own, which is the point of it.

## Install as a Flatpak (recommended)

One command, no Python install, and it works the same on Bazzite, Fedora,
Ubuntu, Arch, SteamOS and Nobara.

```bash
flatpak install ./Loom-x.y.z-x86_64.flatpak
flatpak run io.github.gwaihirf22.loom_AOE
```

Download the bundle from the
[releases page](https://github.com/gwaihirf22/loom_AOE/releases).

### The one thing you may have to grant

Loom finds your recorded games in the default Steam library on its own. If
your **Steam library is on another drive**, tell the sandbox where:

```bash
flatpak override --user     --filesystem=/run/media/you/games/SteamLibrary:ro     io.github.gwaihirf22.loom_AOE
```

and then point Loom at the folder inside it:

```bash
flatpak override --user     --env=LOOM_RECORDS_DIR=/run/media/you/games/SteamLibrary/steamapps/compatdata/813780/pfx/drive_c/users/steamuser/Games/"Age of Empires 2 DE"     io.github.gwaihirf22.loom_AOE
```

Two libraries: separate them with `:` like `PATH`. [Flatseal] does the same
thing with checkboxes if you would rather click.

Recorded games are only used **after** a match ends, to check Loom's own
readings against what the game was actually told to do. Loom never writes to
them, and the permission is read-only. You can also turn the whole feature
off in the launcher's settings, in which case you need none of this.

[Flatseal]: https://flathub.org/apps/com.github.tchx84.Flatseal

### Why it says "potentially unsafe"

Because it asks for **X11 access**, and a software centre flags that. It is
accurate, and it is not avoidable: Loom reads the game's window pixels,
grabs global hotkeys and counts APM, and Wayland has no API for any of the
three by design — which is exactly why they are privileged. The game runs
under XWayland and Loom has to be on the same X server it is.

What Loom does **not** ask for is `--device=input`. It never reads
`/dev/input`, and APM is counted by asking the X server for event *counts*
rather than by watching a key stream. See `packaging/linux/README.md` for
the full list and what each permission is for.

## Install from source

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
- **Notification duration at its shortest** (Options → Interface). Loom counts
  Town Centres by reading the game's own `--Town Center Built--` line, and how
  long a message lingers changes what the feed shows: the game will not
  reprint a line that is still up, and it redisplays recent history after the
  feed fades. Every timing Loom uses for this was measured at the shortest
  setting; the others are untested rather than known bad. The symptom if one
  misbehaves is an idle-TC warning for a Town Centre you do not have.

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

From the Flatpak these are the same paths with the sandbox's home in front:
`~/.var/app/io.github.gwaihirf22.loom_AOE/config/loom/` and
`.../data/loom/stats`. Nothing special happens to make that work — Flatpak
rewrites `XDG_CONFIG_HOME` and `XDG_DATA_HOME`, and Loom was already
following them.

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

**Statistics says you have no recorded games, and you have hundreds.** This
is the Flatpak, not Loom: the sandbox cannot see your Steam library unless
it is told to. Grant it read-only and restart Loom —

```bash
flatpak override --user --filesystem=/path/to/SteamLibrary:ro io.github.gwaihirf22.loom_AOE
```

— or set `LOOM_RECORDS_DIR` to the folder holding the records, colon-separated
for more than one, like `PATH`. [Flatseal](https://flathub.org/apps/com.github.tchx84.Flatseal)
does the same thing with a GUI. Note Loom refuses to read a game that is
still being played, so a record only appears once the match has ended.
