"""
Loom — what it means for a capture to fail.

One tiny module rather than a definition in the package's __init__, because
the backends need to raise this and __init__ imports the backends. Putting it
here is what stops that being a circular import.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.


class CaptureError(RuntimeError):
    """A frame could not be captured, with a reason worth printing.

    Every backend translates its own platform's failures into this - Xlib
    errors on Linux, ScreenCaptureKit refusals on macOS - so callers can catch
    one type without knowing which backend is loaded.

    A backend must raise this rather than return a black array. A black frame
    is an environment fault (a permission not granted, a window not being
    composited), and if it reaches the reader as pixels it looks exactly like
    "the HUD is not on screen right now". That is the difference between Loom
    saying it cannot see and Loom quietly believing something wrong.
    """
