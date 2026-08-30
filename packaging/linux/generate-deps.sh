#!/bin/sh
# Regenerate python3-deps.yml, the pinned wheels the Flatpak installs.
#
# Run this on Linux, from this directory, whenever requirements.txt changes.
# It writes a generated file: never hand-edit python3-deps.yml, edit
# requirements.txt and re-run this.
#
# WHY IT IS GENERATED. A Flathub build has NO NETWORK. Every source has to
# be declared up front with a sha256, so "pip install -r requirements.txt"
# cannot appear in the manifest - flatpak-pip-generator resolves the tree
# once, here, and writes each wheel out as a pinned source.
#
# WHAT IS DELIBERATELY ABSENT:
#
#   PyQt6, PyQt6-Qt6  the BaseApp provides them, built against the runtime's
#                     own Qt. Installing the PyPI wheels would put a second
#                     copy of Qt inside a runtime that already has one.
#   mgz               a git fork until happyleavesaoc/aoc-mgz#147 lands, and
#                     flatpak-pip-generator cannot express a VCS
#                     requirement. It is its own module in the manifest.
#   mss               moved to requirements-dev.txt: only
#                     tools/capture_smoketest.py imports it, and nothing
#                     under loom/ ever has.
#   pyobjc, pywin32,  markered to other platforms in requirements.txt, so
#   windows-capture   pip never selects them here.
#
# WHY construct IS PINNED HERE. It is not a Loom dependency at all - it is
# mgz's, and mgz pins it EXACTLY (`construct==2.8.16`). The mgz module
# installs with --no-deps, so nothing in the build would catch a
# disagreement: pip resolves whatever it likes, the build succeeds, and
# mgz.fast breaks at RUNTIME when a record is first read, because construct
# changed its API substantially after 2.8. Left unpinned the generator
# picked 2.10.70. If the fork's pin ever moves, this moves with it.
#
# --prefer-wheels for numpy and opencv because both publish manylinux
# wheels and building either from source inside flatpak-builder costs tens
# of minutes for a byte-identical result. The others are pure Python and
# have nothing to prefer.
set -eu

# Which runtime the wheels must fit. This used to be a Python VERSION
# written down here, kept in step with the SDK by hand and commented with
# the command to check it against. --runtime is better than a number: it
# runs pip INSIDE the SDK, so the interpreter resolving the tree IS the
# interpreter that will import it, and the two cannot drift. Keep it equal
# to the manifest's own sdk/runtime-version.
RUNTIME="${RUNTIME:-org.kde.Sdk//6.11}"

# The packages worth fetching as built wheels rather than sdists.
PREFER_WHEELS="${PREFER_WHEELS:-numpy,opencv-python-headless}"

# WHICH interpreter runs the generator, chosen here rather than left to the
# script's own `#!/usr/bin/env python3`. On this machine that shebang picks
# Homebrew's python, whose `packaging` is a namespace remnant with no
# __file__, so the generator died on `from packaging.version import Version`
# while pip cheerfully reported the dependency already satisfied - into a
# DIFFERENT interpreter. Naming the interpreter and installing into that
# same one is the whole fix.
#
# The project venv first, per the immutable-OS rule in CLAUDE.md: never
# install into a system Python. --user is the fallback for someone running
# this from a checkout with no venv built yet.
# Absolute, or the venv warns that its own sys.prefix is not the path it
# was reached by and prints two RuntimeWarnings over every run.
REPO=$(cd "$(dirname "$0")/../.." && pwd)

if [ -z "${PYTHON:-}" ]; then
    if [ -x "$REPO/.venv/bin/python" ]; then
        PYTHON="$REPO/.venv/bin/python"
        PIP_TARGET_ARGS=""
    else
        PYTHON=python3
        PIP_TARGET_ARGS="--user"
    fi
fi
"$PYTHON" -m pip install --quiet $PIP_TARGET_ARGS requirements-parser packaging

# The .py extension is load-bearing. Upstream made the extensionless
# `pip/flatpak-pip-generator` a SYMLINK to this file, and GitHub's raw
# endpoint serves a symlink's target PATH as its content - so the old URL
# came back as 24 bytes reading "flatpak-pip-generator.py", chmod +x
# happily marked it executable, and running it failed with
# "flatpak-pip-generator.py: command not found". curl reported success
# throughout, which is why this went unnoticed and python3-deps.yml was
# never generated at all.
curl -fsSLO https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator.py

# Downloaded, not verified, is how the above went wrong for months. A
# generator that is not a Python script cannot produce a dependency list,
# and saying so here beats a shell error naming a file nobody asked for.
if ! head -n 1 flatpak-pip-generator.py | grep -q '^#!'; then
    echo "flatpak-pip-generator.py is not a script - upstream may have" >&2
    echo "moved it again. Got: $(head -c 100 flatpak-pip-generator.py)" >&2
    rm -f flatpak-pip-generator.py
    exit 1
fi
# Run through the interpreter chosen above, not the shebang.

"$PYTHON" flatpak-pip-generator.py \
    --output python3-deps \
    --yaml \
    --runtime="$RUNTIME" \
    --prefer-wheels="$PREFER_WHEELS" \
    numpy==2.5.1 \
    opencv-python-headless==5.0.0.93 \
    python-xlib==0.33 \
    pytest==9.1.1 \
    construct==2.8.16

rm -f flatpak-pip-generator.py

# The generator names its own output .yaml; everything that refers to this
# file - the manifest, both READMEs, RELEASING.md - says .yml, as does the
# manifest's own extension. One name for one thing, so it is renamed here
# rather than spelled two ways across five files.
mv -f python3-deps.yaml python3-deps.yml

echo "wrote python3-deps.yml — commit it alongside the requirements.txt change"
