from pathlib import Path

# Loom — the PyInstaller build, one folder with one executable.
#
#     pyinstaller loom.spec
#
# One executable, four programs: Loom.exe with no arguments is the launcher,
# and the launcher starts its children as `Loom.exe --mode overlay` - see
# loom/entry.py, which is the module that makes that work identically frozen
# and unfrozen. The three mode modules are hiddenimports because entry.run
# reaches them through importlib, which static analysis cannot see - miss one
# and the launcher's Start button dies only in the packaged copy.
#
# onedir rather than onefile, deliberately: onefile re-extracts ~100MB to a
# temp directory on every launch and its bootloader is the shape antivirus
# heuristics distrust most. A folder that just runs is the better artifact.
#
# console=False: Loom is a GUI. The coach and readout remain source-tree
# tools - run from a clone they keep their terminals; from the bundle the
# launcher's output pane is where child output lands anyway.

# WHICH COMMIT this bundle was built from, stamped HERE rather than by a
# step someone has to remember. A frozen build has no git and may have no
# source tree, so paths.build_commit() reads this file back instead.
#
# It answers what meta.loom cannot: the version moves on releases, readers
# change between them, and a stats corpus grouped by version silently mixes
# reader generations. The alternative was bumping the version by hand on
# "material" reader changes, which makes a machine-checkable fact depend on
# discipline and fails SILENTLY when someone forgets - the hand-curated-list
# pattern this codebase has been bitten by three times.
#
# If git is not there the file is written EMPTY rather than guessed at, and
# build_commit() then reports None. Absence is a real answer: "this build
# did not record its commit" beats a wrong one.
import subprocess as _subprocess

try:
    _commit = _subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, timeout=5)
    _commit = _commit.stdout.strip() if _commit.returncode == 0 else ""
except Exception:
    _commit = ""
Path("BUILD_COMMIT").write_text(_commit + "\n", encoding="utf-8")
print(f"loom.spec: stamping build commit {_commit or '(unknown)'}")

a = Analysis(
    ["loom_app.py"],
    pathex=[],
    binaries=[],
    datas=[
        # The shipped assets, addressed from PROJECT_ROOT at runtime -
        # paths._install_root resolves to this bundle's _internal directory.
        ("BUILD_COMMIT", "."),
        ("templates", "templates"),
        ("builds", "builds"),
        ("icons", "icons"),
        ("images", "images"),
        # The build-step icon library. Without it every @icon@ token in a
        # build order falls back to words - the overlay works, but reads
        # like a telegram. Game art, shipped under Microsoft's Game Content
        # Usage Rules; the notice is in the README.
        ("master_aoe2_images", "master_aoe2_images"),
    ],
    hiddenimports=[
        # Reading a recorded game. loom/replay.py imports mgz INSIDE the
        # function that parses, so that most runs never pay for it.
        #
        # Belt and braces, NOT a fix for a bug. I assumed a lazy import
        # would be invisible to PyInstaller and measured it instead: two
        # builds of this spec, one with these three names and one without,
        # both put 40 mgz entries and 11 construct entries in the PYZ.
        # PyInstaller's modulegraph walks every import in a module,
        # including the ones inside function bodies, so nothing was ever
        # missing. Named here anyway because it costs nothing and makes a
        # runtime dependency that appears nowhere at module scope visible
        # to anyone reading the build - and because an import moved behind
        # a try/except or an importlib call later WOULD vanish.
        "mgz",
        "mgz.fast",
        "construct",
        # entry.run imports these by name at runtime; nothing static sees it.
        "loom_overlay",
        "loom_coach",
        "loom_read",
        # The per-OS backends load through importlib with an f-string -
        # loom/capture/__init__.py and loom/hotkeys/__init__.py both - so
        # static analysis cannot see them either. Found the packaged way:
        # the frozen overlay reported "no hotkey backend" and the live
        # reader would have followed with "no capture backend". Windows
        # only; the x11 siblings would drag Xlib into a bundle that can
        # never use it.
        "loom.capture.windows",
        "loom.hotkeys.windows",
        # Imported inside functions throughout the capture backend.
        "windows_capture",
        "win32api",
        "win32con",
        "win32gui",
        "win32process",
        "win32ui",
    ],
    excludes=[
        # Nothing here uses tkinter; PyInstaller sometimes drags it in
        # through pillow-adjacent probing.
        "tkinter",
    ],
    noarchive=False,
)

# OpenCV's bundled ffmpeg is 30MB of video decoding Loom never does -
# imread and matchTemplate do not touch it, and nothing here constructs a
# VideoCapture. Dropped by name rather than pattern so a future OpenCV
# renaming fails the build loudly instead of silently shipping the weight.
a.binaries = [entry for entry in a.binaries
              if "opencv_videoio_ffmpeg" not in entry[0]]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Loom",
    icon="images/loom.ico",
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Loom",
)
