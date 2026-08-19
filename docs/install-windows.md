# Installing Loom on Windows

Most people want the first section and can stop reading after it. Running
from source, further down, is for development and the terminal tools.

Everything works on Windows, including APM tracking. See
[platform support](platform-support.md) for how that compares to Linux and
macOS.

## The app — no Python, no install

1. Download the latest
   [`Loom-x.y.z-windows.zip`](https://github.com/gwaihirf22/loom_AOE/releases/latest).
2. Unzip it anywhere you like.
3. Run `Loom.exe`. That is the launcher: pick a build order, start the
   overlay, and every other window opens from there.

All it needs is **Windows 10 version 1903 or newer** (Windows Graphics
Capture arrived in it) and the game.

### The first run: Windows will probably object

Loom is a small unsigned open-source program, and Windows treats every new
release of one with suspicion until enough people have run it. Two flavours:

- **SmartScreen** ("Windows protected your PC"): click **More info → Run
  anyway**.
- **Microsoft Defender sometimes quarantines `Loom.exe` outright** — it
  vanishes right after you extract the zip, usually blamed on a
  machine-learning detection ending in `!ml`. That is a false positive on
  the freshly built file: a brand-new release has a file hash Defender has
  never seen, and "unknown + unsigned" is enough for its heuristics. Loom
  is a free community project and is not code-signed — signing is a paid
  subscription, and this app earns nothing — so a new release may always
  start out under suspicion. If it happens to you: **Windows Security →
  Virus & threat protection → Protection history**, find the entry naming
  `Loom.exe`, and choose **Restore** / **Allow on device**. If Defender
  keeps taking it, add the folder you unzipped Loom into to its
  exclusions: **Virus & threat protection settings → Exclusions → Add an
  exclusion → Folder** (this needs administrator rights).

Loom never injects into the game, reads its memory, or touches the network;
it reads pixels from the screen and draws a panel on top. The full source
for every release is published alongside the zip, so none of this has to be
taken on faith.

## Game settings

Two settings matter, both under **Options → Interface**:

- **HUD scale at 100%.** Loom detects the HUD at whatever size it is drawn, but
  digit recognition degrades away from 100%, and Loom prints a warning when it
  measures otherwise. (The slider tends to report 99% however it is set. That
  1% is well inside the tolerance.)
  Keep it at **90% or above**: measured at 2560x1440 on the stock HUD, 85%
  makes the HUD unfindable while 90–125% works.
- **The stock HUD, or the Anne_HK Better UI mod.** Loom knows both and works out
  which is on screen by itself. Another UI mod that replaces the resource-bar
  artwork needs its own profile; Loom says so rather than waiting silently.

Two mods are worth installing alongside Loom — recommended, not required:

- [Anne_HK — Better UI](https://www.ageofempires.com/mods/details/3762) is the
  layout Loom was originally built against: more room, standardised item
  locations, every read a little easier. Fully supported and auto-detected.
- [The transparent-UI mod](https://www.ageofempires.com/mods/details/2532)
  clears the per-civilization border artwork from around the HUD — the main
  source of reading trouble. Caveat: it does not cover every civ, and the
  newest civs (the most common offenders) are the least likely to be covered.

**Display mode:** any of them. The overlay was measured sitting above the game
at 2560x1440 fullscreen, so unlike macOS there is no windowed-only restriction
here. If you ever do find the panel hidden behind the game, switch to
**Windowed Fullscreen** (borderless) and it will come back.

## Where Loom keeps your things

- Settings and match statistics: `%APPDATA%\Loom`
- Your own build orders: `%APPDATA%\Loom\builds`. Use **Import build**
  in the launcher and you never need that path — it checks the file, copies
  it there and selects it; **Open builds folder** goes straight to it.
  Builds kept there survive updating Loom. The `builds` folder inside the
  app works too, but a new version's zip replaces it. Running from a clone,
  the `builds` folder beside the code is the equivalent.

If you previously ran Loom from a clone that kept `config.json` and `stats/`
beside the code, they are copied to `%APPDATA%\Loom` the first time you start
it. The originals are left where they are.

## Hotkeys and APM

Loom registers three key combinations while it runs (**Ctrl+Shift+W** forward a
step, **Ctrl+Shift+Q** back, **Ctrl+Shift+R** stop or resume following the
game). All three are rebindable and can be switched off in the launcher. The
step keys only pause automatic following for ten seconds and then resume by
themselves; the panel says **MANUAL** whenever it is not tracking the game.

**Track APM** counts keystrokes and clicks for the post-game statistics. It
uses the Windows Raw Input API, which reports that a key went down without
saying which one — Loom reads the device type and the press/release flag and
nothing else. It deliberately does not use a keyboard hook, which is the
mechanism keyloggers use and what antivirus software watches for. Switch it off
in the launcher if you would rather it counted nothing at all.

## If something is wrong

**Loom waits forever and never finds the HUD.** Check the HUD scale is 100% and
that you are on a supported HUD. From a source clone there is also a probe:

```powershell
python -m tools.windows_probe
```

It finds the game window, tries every capture path, and says which return real
pixels and whether Loom's own anchor search finds a HUD in them. `--list` shows
every window it can see, which answers "is it even finding the game?"

**The overlay is behind the game.** It should not be - it is measured above a
fullscreen game. Switch to Windowed Fullscreen as a workaround and please
report it.

**The mouse stops at the overlay.** It should not — the overlay checks this at
startup and prints a line if click-through failed. Please report it with that
line.

**Anything is misread.** Confirm the HUD scale first; it is the usual cause.

**A hotkey does nothing.** The launcher's output pane says why when the overlay
starts. The usual cause is that another program already owns that combination -
Windows will not share one, so whichever program registered it first keeps it.
Rebind it under **Build-order hotkeys**.

**A key stopped working in the game.** That is the same mechanism from the other
side: while Loom holds a combination, Age of Empires never sees it. Change the
binding, empty the field to switch that action off, or untick **Use hotkeys**.

**"Loom has no screen capture backend"** (source clone only) — the
`windows-capture` package did not install. Re-run
`pip install -r requirements.txt` and read the output.

## Running from source

For development, or the terminal tools (the coach, the live readout, the
probes). The app above is the better answer for playing.

### What you need

- **Python 3.10 or newer** — [python.org](https://www.python.org/downloads/),
  or `winget install Python.Python.3.12`. Tick **Add python.exe to PATH** in the
  installer if you use the graphical one.
- **Age of Empires II: Definitive Edition**

### Install

```powershell
git clone https://github.com/gwaihirf22/loom_AOE.git
cd loom_AOE
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If PowerShell refuses to run the activation script, either use
`.\.venv\Scripts\activate.bat` from cmd, or allow local scripts once with
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

### If Python "is not found" even though you just installed it

Two separate causes, and you can hit both at once.

**Your terminal is older than the install.** A shell copies `PATH` when it
starts and never re-reads it, so a window opened before installing Python has
no idea it exists. Close it and open a new one.

**Windows ships decoy `python.exe` files.** The `WindowsApps` folder in your
local app data contains zero-byte stubs for `python.exe` and `python3.exe`
whose only job is to advertise the Microsoft Store, and that folder is often
on the *system* `PATH` — which is searched before your user `PATH`. So the
stub beats a real Python installed for your user, and you get "Python was not
found; run without arguments to install from the Microsoft Store" in every new
terminal, forever.

You do not need to repair `PATH` to use Loom. Two ways round it:

- **Use `py` instead of `python`** outside the venv. The Python launcher is not
  shadowed, so `py -m venv .venv` works where `python -m venv .venv` does not.
- **Activate the venv**, after which plain `python` is the venv's own copy and
  everything behaves normally. This is the usual workflow anyway.

If you would rather remove the decoys: Settings → Apps → Advanced app settings
→ App execution aliases, and switch off **python.exe** and **python3.exe**.

Note also that `source .venv/bin/activate` is the Linux spelling; on Windows
the venv lives in `.venv\Scripts\` and is activated with `Activate.ps1`.

Check it before involving the game — this needs nothing but the install, and
the venv must be active (your prompt shows `(.venv)`):

```powershell
python loom_overlay.py --demo
```

That replays a whole match on your desktop in about a minute.

### Run it

```powershell
python loom_app.py          # the launcher: pick a build, start the overlay
python loom_overlay.py      # the overlay directly, over a running game
python loom_coach.py        # the same thing in a terminal
python loom_read.py         # just the two HUD numbers, for checking
```

Start with `loom_read.py` with a game on screen. If it prints a villager count
and a clock that both move, everything downstream will work.
