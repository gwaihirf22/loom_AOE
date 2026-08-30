# Loom. Developed with AI assistance (Claude); design and code by Paul Blake.

# The one version number, read by the launcher's title bar and the stats
# files. 0.9.x is the "Linux feature set settling down" series; 1.0.0 was
# reserved for the first release that also runs on Windows, and kept.
#
# The -dev suffix means this tree is BETWEEN releases: on its way to 1.0.8,
# not 1.0.8 itself. tools/release.py reads that suffix and refuses to publish
# such a tree as a release, so a build carrying it is a nightly or a working
# copy and says so in the title bar and in every stats file it writes.
# Cutting 1.0.8 means dropping the suffix here first; the release script
# checks this number against the tag and the Flatpak metainfo, and this is
# the one that wins.
__version__ = "1.0.8"
