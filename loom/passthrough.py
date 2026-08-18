"""
Loom — is the overlay really invisible to the pointer?

The overlay asks Qt for WindowTransparentForInput, and Qt is supposed to turn
that into an empty X11 input region using the SHAPE extension. That is a
promise from a library, not a fact. If it ever stops being true the symptom is
not a crash - it is the game's cursor confinement breaking the moment I brush
the panel, the mouse walking onto the other monitor, and a lost match. That is
far too quiet a failure for something that costs a game, so I ask the X server
directly and say something at startup when the answer is wrong.

Each platform is asked in its own terms, and in every case the thing asked is
the thing that decides: the X server's input region, the NSWindow's
ignoresMouseEvents, or the Win32 window's WS_EX_TRANSPARENT bit. None of those
is a filter inside Qt that merely looks like click-through, which is the whole
point - a library's promise is what is in doubt here.

Everything here answers "I cannot tell" rather than failing: not X11, no SHAPE
extension, no python-xlib, window not realised yet. A machine that cannot be
asked is not evidence of a problem, and an overlay that refuses to start
because it could not run a self-check would be worse than the bug.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

# Xlib.ext.shape's SK.Input. Spelled out here so this module says what "2"
# means and the import can stay inside the function.
SHAPE_KIND_INPUT = 2


def input_rectangles(window_id):
    """What the X server thinks this window's input region is.

    Returns a list of (x, y, width, height), or None if the question could not
    be asked at all. An EMPTY list is the answer I want: the window claims no
    input area anywhere, so the pointer passes through to whatever is beneath
    and no crossing event is ever generated for it.

    python-xlib only attaches shape_get_rectangles to window objects when the
    server actually advertises SHAPE, which is why that is a hasattr check
    rather than a try/except wrapped round the call.
    """
    try:
        from Xlib import display as xdisplay
    except ImportError:
        return None

    try:
        dpy = xdisplay.Display()
    except Exception:
        # No X server reachable: Wayland-native, headless, or no DISPLAY.
        return None

    try:
        window = dpy.create_resource_object("window", int(window_id))
        if not hasattr(window, "shape_get_rectangles"):
            return None                 # the server has no SHAPE extension
        reply = window.shape_get_rectangles(SHAPE_KIND_INPUT)
        return [(r.x, r.y, r.width, r.height) for r in reply.rectangles]
    except Exception:
        return None
    finally:
        try:
            dpy.close()
        except Exception:
            pass


def ignores_mouse_events(view_pointer):
    """Does the Cocoa window behind this view refuse the pointer?

    Returns True, False, or None when the question cannot be asked.

    macOS is the easy case. Qt turns WindowTransparentForInput into
    NSWindow.ignoresMouseEvents, which the window server itself honours when
    routing the pointer - so unlike the X11 side there is no worry about a
    library-internal filter that only looks like click-through. Asking the
    window is asking the thing that decides.

    On macOS Qt's winId() is an NSView*, not a window, so the view is wrapped
    back into an object and its window asked for.
    """
    # A null handle means the widget has not been realised yet. Checked before
    # anything else because there is NO recovering from handing Objective-C a
    # bad pointer: it does not raise, it takes the process down, and no
    # try/except here can catch that.
    if not view_pointer:
        return None

    try:
        import ctypes

        import objc
    except ImportError:
        return None

    try:
        view = objc.objc_object(c_void_p=ctypes.c_void_p(int(view_pointer)))
        window = view.window()
        if window is None:
            return None                  # not realised on screen yet
        return bool(window.ignoresMouseEvents())
    except Exception:
        return None


def is_transparent_to_the_pointer(window_handle):
    """Does Windows route the pointer straight past this window?

    Returns True, False, or None when the question cannot be asked.

    Windows is the easy case for the same reason macOS is: the flag being
    checked is the one the system itself consults. WS_EX_TRANSPARENT makes a
    window invisible to hit-testing, so the pointer is delivered to whatever
    sits underneath - it is not a filter inside Qt that only looks like
    click-through, which is the exact worry that made this module exist for
    X11.

    -20 is GWL_EXSTYLE, and 0x20 is WS_EX_TRANSPARENT. Both are spelled out
    rather than imported so this reads without a Windows SDK to hand.

    GetWindowLongPtrW rather than GetWindowLongW because a 64-bit style word
    truncated to 32 bits would silently drop the high half; the restype has to
    be set for the same reason, since ctypes defaults to C int. On 32-bit
    Windows the Ptr spelling does not exist, hence the fallback.
    """
    if not window_handle:
        return None

    try:
        import ctypes
    except ImportError:
        return None

    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return None                      # not Windows

    try:
        try:
            get_style = user32.GetWindowLongPtrW
        except AttributeError:
            get_style = user32.GetWindowLongW
        get_style.restype = ctypes.c_ssize_t
        get_style.argtypes = (ctypes.c_void_p, ctypes.c_int)

        style = get_style(ctypes.c_void_p(int(window_handle)), -20)
        if not style:
            # 0 is also what a bad handle returns, so it is "cannot tell"
            # rather than "no flags set". An overlay that is not realised yet
            # must not be reported as broken.
            return None
        return bool(style & 0x20)
    except Exception:
        return None


def check(window_id):
    """Did click-through take effect? Returns (verdict, message).

    verdict is True (it did), False (it did not), or None (cannot tell). The
    message is one line, fit to print as it stands.
    """
    if sys.platform == "win32":
        transparent = is_transparent_to_the_pointer(window_id)
        if transparent is None:
            return None, ("cannot check click-through: Windows would not "
                          "report the window's extended style (the overlay "
                          "may not be on screen yet)")
        if transparent:
            return True, ("click-through confirmed: the window has "
                          "WS_EX_TRANSPARENT")
        return False, (
            "the overlay is NOT click-through - its window lacks "
            "WS_EX_TRANSPARENT, so Windows will hit-test the panel and the "
            "pointer will stop there instead of reaching the game. Check that "
            "WindowTransparentForInput is still in "
            "overlay.OVERLAY_WINDOW_FLAGS."
        )

    if sys.platform == "darwin":
        ignoring = ignores_mouse_events(window_id)
        if ignoring is None:
            return None, ("cannot check click-through: no answer from the "
                          "NSWindow (pyobjc missing, or the window is not on "
                          "screen yet)")
        if ignoring:
            return True, ("click-through confirmed: the NSWindow ignores "
                          "mouse events")
        return False, (
            "the overlay is NOT click-through - its NSWindow still accepts "
            "mouse events, so the pointer will enter the panel instead of "
            "reaching the game. Check that WindowTransparentForInput is still "
            "in overlay.OVERLAY_WINDOW_FLAGS."
        )

    rectangles = input_rectangles(window_id)

    if rectangles is None:
        return None, ("cannot check click-through: no answer from the X SHAPE "
                      "extension (not XWayland, or python-xlib missing)")
    if not rectangles:
        return True, "click-through confirmed: the X11 input region is empty"
    return False, (
        "the overlay is NOT click-through - the X server still reports an "
        f"input region of {rectangles}. The pointer will enter the panel, "
        "which releases the game's hold on the cursor and lets the mouse "
        "escape onto another monitor mid-match. Check that "
        "WindowTransparentForInput is still in overlay.OVERLAY_WINDOW_FLAGS "
        "and that QT_QPA_PLATFORM=xcb."
    )
