"""
Loom — build the Windows release zip, and prove it is well formed.

This exists because PowerShell's Compress-Archive produced a zip that is
correct on Windows and broken everywhere else, in a way nothing on Windows
can see.

A zip stores every name TWICE: once in the local header before each file's
bytes, and once in the central directory at the end. Compress-Archive wrote
forward slashes in the central directory and backslashes in every local
header. Windows Explorer and Python's zipfile both read the central
directory, so both called it fine. unzip and Ark read local headers - and
the spec says only "/" separates directories, so they treated the
backslashes as ordinary characters in a filename. A Linux user unpacking
the 1.0.1 zip got 1600 files called "Loom\\_internal\\..." in one flat
heap, with no folders at all.

The lesson is in check(), not in the writing: Python's zipfile writes both
copies from one normalised name, so simply using it fixes the bug. What
keeps it fixed is refusing to hand back an archive whose LOCAL HEADERS were
never looked at, because that is the half of the file the tool that found
this reads.

    python -m tools.package_windows              # dist/Loom -> the zip
    python -m tools.package_windows --check FILE # just audit an existing one
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import struct
import sys
import zipfile
from pathlib import Path

from loom import __version__, paths

# The one character that must never appear in a stored name.
BACKSLASH = chr(92)

# Local file header: "PK\x03\x04", then a fixed 30-byte head whose last two
# shorts are the name and extra-field lengths, then the name itself.
LOCAL_SIGNATURE = b"PK" + bytes([3, 4])
NAME_LENGTH_OFFSET = 26
NAME_OFFSET = 30


def local_header_names(path):
    """Every name as the LOCAL headers spell it - what unzip reads.

    Scanned from the raw bytes because Python's zipfile only ever reports
    the central directory, which is precisely the half that looked correct
    while the archive was unusable.
    """
    data = Path(path).read_bytes()
    names = []
    offset = data.find(LOCAL_SIGNATURE)
    while offset >= 0:
        name_length, extra_length = struct.unpack_from(
            "<HH", data, offset + NAME_LENGTH_OFFSET)
        start = offset + NAME_OFFSET
        names.append(data[start:start + name_length].decode("utf-8", "replace"))
        offset = data.find(LOCAL_SIGNATURE, start + name_length + extra_length)
    return names


def check(path):
    """Problems with an archive, as readable lines. Empty means good."""
    problems = []

    central = zipfile.ZipFile(path).namelist()
    local = local_header_names(path)

    for where, names in (("central directory", central),
                         ("local headers", local)):
        bad = [name for name in names if BACKSLASH in name]
        if bad:
            problems.append(
                f"{len(bad)} of {len(names)} names in the {where} use "
                f"backslashes, e.g. {bad[0]!r}. Only '/' separates "
                "directories in a zip; anything else unpacks as one flat "
                "heap of oddly named files on Linux and macOS.")

    # The two halves disagreeing is the exact shape of the original bug, and
    # it hides from every tool that reads only one of them.
    if len(central) != len(local):
        problems.append(
            f"the central directory lists {len(central)} entries but there "
            f"are {len(local)} local headers")
    elif sorted(central) != sorted(local):
        differing = next(c for c, l in zip(sorted(central), sorted(local))
                         if c != l)
        problems.append(
            "the central directory and the local headers spell names "
            f"differently, starting at {differing!r}")

    return problems


def build(source, destination):
    """Zip a folder, storing every name with forward slashes.

    zipfile normalises each arcname once and writes that same string into
    both the local header and the central directory, so the two halves
    cannot disagree the way Compress-Archive let them.
    """
    source = Path(source)
    root = source.name
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            # Joined from parts rather than str(): a Windows path stringifies
            # with backslashes, which is how this went wrong in the first
            # place.
            name = "/".join((root, *relative.parts))
            if path.is_dir():
                archive.writestr(name + "/", b"")
            else:
                archive.write(path, name)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split(".")[0])
    parser.add_argument("--source", default=None,
                        help="the folder to zip (default: dist/Loom)")
    parser.add_argument("--out", default=None,
                        help="where to write it (default: dist/Loom-<version>"
                             "-windows.zip)")
    parser.add_argument("--check", default=None, metavar="FILE",
                        help="audit an existing zip instead of building one")
    arguments = parser.parse_args(argv)

    if arguments.check:
        target = Path(arguments.check)
    else:
        source = Path(arguments.source or paths.PROJECT_ROOT / "dist" / "Loom")
        if not source.is_dir():
            print(f"nothing to package: {source} does not exist "
                  "(run pyinstaller loom.spec first)")
            return 1
        target = Path(arguments.out or source.parent /
                      f"Loom-{__version__}-windows.zip")
        build(source, target)
        size = target.stat().st_size / (1024 * 1024)
        print(f"built {target} ({size:.1f} MB)")

    problems = check(target)
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if problems:
        return 1

    entries = len(zipfile.ZipFile(target).namelist())
    print(f"checked {target.name}: {entries} entries, forward slashes in "
          "both the central directory and every local header")
    return 0


if __name__ == "__main__":
    sys.exit(main())
