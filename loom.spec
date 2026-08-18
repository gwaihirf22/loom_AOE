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

a = Analysis(
    ["loom_app.py"],
    pathex=[],
    binaries=[],
    datas=[
        # The shipped assets, addressed from PROJECT_ROOT at runtime -
        # paths._install_root resolves to this bundle's _internal directory.
        ("templates", "templates"),
        ("builds", "builds"),
        ("icons", "icons"),
        ("images", "images"),
    ],
    hiddenimports=[
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
