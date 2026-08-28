"""
Loom — is a remembered window position still somewhere the player can reach?

Every window Loom opens remembers where it was left, and every one of them can
be left somewhere that no longer exists: a second monitor unplugged, a laptop
undocked, a resolution changed. Restoring a position blindly puts the window
off the desktop, where the only symptom is that Loom appears not to have
started at all.

The rule is deliberately NOT "clamp it onto a screen". Clamping needs one
screen to clamp against, and picking the wrong one is its own bug - the
launcher clamped a position saved on the second monitor against the PRIMARY
screen's work area and dragged the window back across the desk on every single
launch, because a window that has not been shown yet reports the primary
screen as its own. Measured: a saved (2811, -236) came back as (1359, 0).

So the question is the weaker and more useful one: is a meaningful amount of
this rectangle on ANY screen? If it is, the position is believed exactly as
saved. If it is not, the caller falls back to its own default and says so.

Pure arithmetic on purpose. The interesting inputs - a monitor at negative
coordinates, a rectangle straddling two screens, one off every screen - are
exactly the ones that are miserable to reproduce by plugging displays in, and
they are the ones that go wrong.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# How much of a window must be on some screen for a saved position to be
# believed. A sliver does not count: a window showing 10 pixels of its corner
# is lost for every practical purpose.
MIN_VISIBLE = 60


def visible_on(screens, x, y, width, height):
    """Is a meaningful amount of this rectangle on any of these screens?

    screens is [(x, y, width, height)].
    """
    for screen_x, screen_y, screen_w, screen_h in screens:
        overlap_w = min(x + width, screen_x + screen_w) - max(x, screen_x)
        overlap_h = min(y + height, screen_y + screen_h) - max(y, screen_y)
        if overlap_w >= MIN_VISIBLE and overlap_h >= MIN_VISIBLE:
            return True
    return False


def screen_rects(app):
    """Every screen's geometry as plain tuples, ready for visible_on."""
    return [(g.x(), g.y(), g.width(), g.height())
            for g in (screen.geometry() for screen in app.screens())]


# ---- putting a window somewhere a person can reach it --------------------
#
# These live here rather than in the launcher because they answer the same
# question for every window Loom opens: the build preview beside the
# launcher, and a chart popped out of the statistics window. They were in
# launcher.py while the launcher was the only caller, and moving them was
# the alternative to statsview growing a second copy of the arithmetic -
# which is how two windows end up disagreeing about what "beside" means.

# The gap left between a window and one placed beside it.
WINDOW_GAP = 12


def clamped_position(position, size, area):
    """Move a window fully onto the screen. Returns (x, y).

    position is where it wants to be, size is (width, height), area is the
    work area as (left, top, right, bottom).

    The case this is for is a geometry remembered on one machine and restored
    on another: a launcher left at the bottom of a 2560x1440 desktop reopens
    entirely below a 1920x1080 one, and the only symptom is that Loom appears
    not to start. Top-left wins over bottom-right when the window is larger
    than the screen, because the title bar is the part you need to reach.
    """
    x, y = position
    width, height = size
    left, top, right, bottom = area

    x = min(x, right - width)
    y = min(y, bottom - height)
    return max(left, x), max(top, y)


def beside(anchor, size, area):
    """Where to put a window so it sits next to another one, on screen.

    anchor is (x, y, width) of the window to sit beside, size is (width,
    height) of the window being placed, and area is the screen's work area as
    (left, top, right, bottom). Returns (x, y).

    Pure arithmetic on purpose: the interesting cases are a preview too wide
    for the space to the right, and a monitor left of the primary one whose
    coordinates are negative. Neither is convenient to reproduce by opening
    real windows, and both would put the preview somewhere the player cannot
    reach - so they are worth testing with fake inputs instead.

    To the right by preference, flipping left when the right would hang off
    the edge, and clamped into the work area either way.

    The work area MUST be the one belonging to the anchor's own screen. Pass
    the primary screen's while the anchor sits on a second monitor and the
    clamp drags the new window across the desk - which is this module's
    opening paragraph happening to a window that is being placed rather than
    one being restored.
    """
    anchor_x, anchor_y, anchor_width = anchor
    width, height = size
    left, top, right, bottom = area

    x = anchor_x + anchor_width + WINDOW_GAP
    if x + width > right:
        x = anchor_x - width - WINDOW_GAP
    # Clamped last, so a window wider than the space still lands on screen
    # rather than half off it.
    x = max(left, min(x, right - width))
    y = max(top, min(anchor_y, bottom - height))
    return x, y


def work_area(widget):
    """The work area of the screen THIS widget is actually on.

    (left, top, right, bottom), the shape `beside` and `clamped_position`
    take.

    `widget.screen()` rather than the primary screen, and that is the whole
    point of the function. A widget that has not been shown yet reports the
    primary screen as its own, so this is only worth asking of a window the
    player can see - which is exactly the case it exists for: placing a new
    window next to one already on screen.
    """
    area = widget.screen().availableGeometry()
    return (area.left(), area.top(), area.right(), area.bottom())
