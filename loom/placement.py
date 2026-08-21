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
