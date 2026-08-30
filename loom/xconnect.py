"""
Loom — opening an X connection the way the rest of the desktop does.

Four subsystems talk to X through python-xlib: capture reads the game's
window, hotkeys grab keys, passthrough asks whether the overlay really is
click-through, and the APM counter watches raw input. Each opened its own
connection with a bare Display(), and each therefore inherited the same
defect, which is why this is one module rather than a fix repeated in four
places.

THE DEFECT. The session cookie lives in an xauthority file keyed to the
hostname at login. python-xlib looks it up by comparing that address for
EQUALITY - get_best_auth in Xlib/xauth.py - and does not so much as define
FamilyWild, the "matches any host" family that C's Xlib falls back on in
XauGetBestAuthByAddr. So when the hostname drifts from the name the cookie
was written under, every C client keeps working and python-xlib alone sends
no authorisation at all and is refused.

The drift is ordinary on the immutable distributions Loom targets: a
transient name from DHCP, an image update resetting an unset static
hostname, or a distrobox container. It cost a whole evening here, and the
reason it was hard to see is worth writing down. The OVERLAY is Qt, whose
xcb plugin uses C's Xlib and connects perfectly - so the panel comes up
looking healthy while capture reads nothing, hotkeys are refused, and APM
counts zero. Three subsystems dead, no error dialog, and the one thing on
screen looking fine.

Nothing here is more credulous than the platform's own library: only the
wildcard entry is accepted, so where C's Xlib would fail, this fails too.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import socket
import struct
import tempfile

# The "matches any host" family, 0xffff in the protocol. Spelled out because
# python-xlib does not define it - Xlib/xauth.py names every other family and
# omits this one, which is the same gap that makes any of this necessary.
FAMILY_WILD = 0xffff

# The corrected cookie file, once written. Cached because connecting happens
# again on every reconnect - the reader re-opens the display when the game
# restarts - and a file per attempt would leave a slow drip of live cookies
# in the runtime directory for the length of a session.
_authority_file = None


def display_number(name=None):
    """The display number out of a DISPLAY string: ":0" and "host:0.1" -> 0.

    The screen suffix after the dot is not part of what a cookie is keyed to,
    so it is dropped. Returns 0 when DISPLAY is unset or unparseable, which
    is the same guess the rest of X makes.
    """
    name = name if name is not None else os.environ.get("DISPLAY", "")
    _, _, number = name.partition(":")
    number = number.split(".")[0]
    try:
        return int(number)
    except ValueError:
        return 0


def authority_keyed_to_this_host():
    """A one-entry xauthority file addressed to this machine's hostname.

    Returns its path, or None if no cookie could be found to put in it -
    which is the honest answer for a machine with no wildcard entry, a bare
    TTY, or a server running with authorisation disabled.
    """
    global _authority_file
    if _authority_file is not None and os.path.exists(_authority_file):
        return _authority_file

    from Xlib import error, xauth

    try:
        authority = xauth.Xauthority()
    except error.XauthError:
        return None

    number = display_number()
    try:
        name, data = authority.get_best_auth(FAMILY_WILD, b"", number)
    except error.XNoAuthError:
        return None

    # The .Xauthority record layout: family as a big-endian u16, then four
    # length-prefixed byte fields in this order. Same thing xauth(1) writes.
    record = struct.pack(">H", xauth.FamilyLocal)
    for field in (socket.gethostname().encode(), str(number).encode(),
                  name, data):
        record += struct.pack(">H", len(field)) + field

    # In the runtime directory when there is one: it is already user-private
    # and emptied at logout, which suits a file holding a live session cookie
    # better than anything that outlives the session.
    folder = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    handle, path = tempfile.mkstemp(prefix="loom-xauth-", dir=folder)
    with os.fdopen(handle, "wb") as fh:
        fh.write(record)
    os.chmod(path, 0o600)
    _authority_file = path
    return path


def connect(name=None):
    """Open an X display, rescuing a cookie python-xlib cannot find itself.

    Raises whatever python-xlib raises, so each caller keeps translating
    failure into its own subsystem's error - CaptureError, HotkeyError - and
    nothing here has to know which one is asking.

    The plain connection is tried first and is what almost every machine
    takes; the retry re-presents the same cookie under the name python-xlib
    will actually look for. XAUTHORITY is set for the process because it is
    the only channel python-xlib reads a cookie through: Display() takes no
    auth argument.
    """
    from Xlib import display

    try:
        return display.Display(name)
    except Exception:
        path = authority_keyed_to_this_host()
        if path is None:
            raise
        os.environ["XAUTHORITY"] = path
        return display.Display(name)
