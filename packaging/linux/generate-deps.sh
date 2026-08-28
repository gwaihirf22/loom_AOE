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
# --only-binary=:all: because numpy and opencv publish manylinux wheels and
# building either from source inside flatpak-builder costs tens of minutes
# for a byte-identical result.
set -eu

# Match the runtime's interpreter, or pip picks wheels for the Python
# running this script and the build installs cp312 wheels into a cp313
# runtime, which fails at import rather than at build. Ask the SDK itself
# rather than trusting a number written down here:
#
#     flatpak run --command=python3 org.kde.Sdk//6.11 -V
#
PYTHON_VERSION="${PYTHON_VERSION:-3.13}"

pip install --user requirements-parser

curl -fsSLO https://raw.githubusercontent.com/flatpak/flatpak-builder-tools/master/pip/flatpak-pip-generator
chmod +x flatpak-pip-generator

./flatpak-pip-generator \
    --output python3-deps \
    --yaml \
    --only-binary=:all: \
    --python-version="$PYTHON_VERSION" \
    numpy==2.5.1 \
    opencv-python-headless==5.0.0.93 \
    python-xlib==0.33 \
    pytest==9.1.1 \
    construct

rm -f flatpak-pip-generator

echo "wrote python3-deps.yml — commit it alongside the requirements.txt change"
