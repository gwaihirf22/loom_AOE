"""
Loom — is the overlay really invisible to the pointer?

The overlay asks Qt for WindowTransparentForInput, and Qt is supposed to turn
that into an empty X11 input region using the SHAPE extension. That is a
promise from a library, not a fact. If it ever stops being true the symptom is
not a crash - it is the game's cursor confinement breaking the moment I brush
the panel, the mouse walking onto the other monitor, and a lost match. That is
far too quiet a failure for something that costs a game, so I ask the X server
directly and say something at startup when the answer is wrong.

Everything here answers "I cannot tell" rather than failing: not X11, no SHAPE
extension, no python-xlib, window not realised yet. A machine that cannot be
asked is not evidence of a problem, and an overlay that refuses to start
because it could not run a self-check would be worse than the bug.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

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


def check(window_id):
    """Did click-through take effect? Returns (verdict, message).

    verdict is True (it did), False (it did not), or None (cannot tell). The
    message is one line, fit to print as it stands.
    """
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
