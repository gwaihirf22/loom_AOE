"""
Loom — loom/xconnect.py: resolving the X cookie when the hostname has drifted.

The bug this pins broke X11 capture completely on the author's own desktop
while every other X client on it kept working, which is the reason it is
worth a file of its own.

The display manager writes the session cookie into the xauthority file keyed
to the hostname at login. This machine's static hostname is unset, so the
transient one ("bazzite") replaced the name the cookie was written under
("RyzenDesktop"). python-xlib looks a cookie up by comparing that address for
EQUALITY - Xlib/xauth.py's get_best_auth - and does not even define
FamilyWild, so it found nothing, sent no auth, and the server refused the
connection. C's Xlib treats the FamilyWild entry sitting beside it as
matching any host, which is why xdpyinfo, xrandr and Qt's own xcb plugin all
connected on a machine where loom_read.py could not.

That shape is the dangerous one: the OVERLAY is Qt, whose xcb plugin uses
C's Xlib, so it comes up looking perfectly healthy while the four subsystems
that use python-xlib all fail at once - capture reads nothing, hotkeys are
refused, APM counts zero, and passthrough cannot answer. The reported
symptom was "hotkeys aren't working", which is the loudest of the four and
not the whole of it. And the causes are ordinary on the
immutable distributions Loom targets - a transient name from DHCP, an image
update resetting an unset static hostname, or a distrobox container.

These run on any platform. python-xlib is a pure-Python wheel and
requirements-dev.txt installs it without a marker on purpose, so the Linux
cookie logic stays answerable from the Windows boot without a reboot - the
same argument test_capture_selector makes for importing the backend.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import socket
import stat
import struct

import pytest

from Xlib import xauth

from loom import xconnect


def write_authority(folder, entries):
    """An xauthority file holding ENTRIES, in the format xauth(1) writes:
    family as a big-endian u16, then four length-prefixed byte fields."""
    raw = b""
    for family, address, number, name, data in entries:
        raw += struct.pack(">H", family)
        for field in (address, number, name, data):
            raw += struct.pack(">H", len(field)) + field
    path = folder / "xauthority"
    path.write_bytes(raw)
    return path


COOKIE = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"
SCHEME = b"MIT-MAGIC-COOKIE-1"


@pytest.fixture
def uncached(monkeypatch):
    """The corrected file is cached for the process, so each test starts
    without one or the second test would be handed the first one's."""
    monkeypatch.setattr(xconnect, "_authority_file", None)


@pytest.mark.parametrize("value, expected", [
    (":0", 0),
    (":1", 1),
    (":0.1", 0),
    ("host:12.0", 12),
    ("", 0),
    ("nonsense", 0),
])
def test_the_display_number_is_read_off_display(value, expected):
    """The screen suffix after the dot is not part of what a cookie is keyed
    to, and an unparseable DISPLAY guesses 0 as the rest of X does."""
    assert xconnect.display_number(value) == expected


def test_a_stale_hostname_is_rescued_by_the_wildcard_entry(
        tmp_path, monkeypatch, uncached):
    """The whole bug, end to end, with the two entries this machine had.

    The assertion that matters is the last one: python-xlib's OWN lookup -
    the one that returned nothing and left the connection unauthenticated -
    now finds the cookie. Asserting the bytes alone would only prove the
    file was written the way this test writes files.
    """
    original = write_authority(tmp_path, [
        (xauth.FamilyLocal, b"SomeOtherHost", b"0", SCHEME, COOKIE),
        (xconnect.FAMILY_WILD, b"", b"0", SCHEME, COOKIE),
    ])
    monkeypatch.setenv("XAUTHORITY", str(original))
    monkeypatch.setenv("DISPLAY", ":0")

    # The failure being fixed, stated as a fact rather than assumed: keyed to
    # this host, python-xlib finds nothing in the file it was given.
    with pytest.raises(Exception):
        xauth.Xauthority(str(original)).get_best_auth(
            xauth.FamilyLocal, socket.gethostname().encode(), 0)

    path = xconnect.authority_keyed_to_this_host()
    assert path is not None

    name, data = xauth.Xauthority(path).get_best_auth(
        xauth.FamilyLocal, socket.gethostname().encode(), 0)
    assert (name, data) == (SCHEME, COOKIE)


def test_no_wildcard_entry_means_no_rescue(tmp_path, monkeypatch, uncached):
    """Where C's Xlib would fail, this fails too.

    The fallback exists to do what every other X client already does, not to
    do more. Reaching for a cookie belonging to some other host would connect
    Loom to a display the rest of the desktop could not reach, and a capture
    backend that is MORE credulous than the platform's own library is a
    worse thing to ship than one that admits it cannot connect.
    """
    original = write_authority(tmp_path, [
        (xauth.FamilyLocal, b"SomeOtherHost", b"0", SCHEME, COOKIE),
    ])
    monkeypatch.setenv("XAUTHORITY", str(original))
    monkeypatch.setenv("DISPLAY", ":0")

    assert xconnect.authority_keyed_to_this_host() is None


def test_a_missing_authority_file_is_not_a_crash(
        tmp_path, monkeypatch, uncached):
    """No cookie at all is a normal state - a bare TTY, a server with auth
    disabled - and open_display re-raises the original refusal for it."""
    monkeypatch.setenv("XAUTHORITY", str(tmp_path / "does-not-exist"))
    assert xconnect.authority_keyed_to_this_host() is None


def test_the_cookie_file_is_written_once_and_kept_private(
        tmp_path, monkeypatch, uncached):
    """Two things one file can answer.

    Once, because open_display runs again on every reconnect - the reader
    re-opens the display when the game restarts - and a file per attempt
    would drip cookies into the runtime directory all session.

    Private, because the file holds a live session cookie: anyone who can
    read it can read the screen, which is the whole thing X authorisation
    exists to gate.
    """
    original = write_authority(tmp_path, [
        (xconnect.FAMILY_WILD, b"", b"0", SCHEME, COOKIE),
    ])
    monkeypatch.setenv("XAUTHORITY", str(original))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    first = xconnect.authority_keyed_to_this_host()
    second = xconnect.authority_keyed_to_this_host()
    assert first == second
    assert len(list(tmp_path.glob("loom-xauth-*"))) == 1

    # The mode is asked about only where a mode MEANS something. Windows
    # honours just the read-only bit in os.chmod and reports 0o666 back for
    # an ordinary writable file, so asserting 0o600 there fails on a
    # platform that never runs this code - the whole file is the X11 path.
    # Skipping says "I could not check" where a red test would have said
    # "this is not true", and the difference is the point: these tests run
    # on all three platforms deliberately, so the Linux logic stays
    # answerable from the Windows boot without a reboot.
    if os.name != "posix":
        pytest.skip("no POSIX mode bits here - os.chmod honours only the "
                    "read-only flag on this platform")

    mode = stat.S_IMODE(os.stat(first).st_mode)
    assert mode == 0o600, oct(mode)
