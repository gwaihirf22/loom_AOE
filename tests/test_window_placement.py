"""
Loom — a remembered window position must survive a second monitor.

Every window Loom opens remembers where it was left, and the launcher was
quietly refusing to honour that. It clamped the saved position against ONE
screen's work area, and for a window that has not been shown yet that screen
is the primary one - so a position saved on a second monitor was squashed back
onto the primary display on every single launch. Measured on a two-monitor
desk: a saved (2811, -236) came back as (1359, 0), which looks from the outside
exactly like the position never being saved at all.

These are the cases that bug lived in. Pure arithmetic, because arranging and
unplugging real monitors to test it is not a thing anyone will do twice.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom.placement import MIN_VISIBLE, visible_on

# The desk this was found on: a 1080p primary, and a tall portrait monitor to
# the right whose top edge is well above the primary's.
PRIMARY = (0, 0, 1920, 1080)
SECONDARY = (1920, -1555, 1152, 2752)
BOTH = [PRIMARY, SECONDARY]

LAUNCHER = (560, 914)


def test_a_position_on_the_second_monitor_is_kept():
    """The bug, in one line. This is a real saved position from the config
    file of the machine that reported it."""
    assert visible_on(BOTH, 2811, -236, *LAUNCHER)


def test_the_same_position_is_refused_once_that_monitor_is_gone():
    """And the other half: unplug it and the window must not be restored
    somewhere nobody can reach."""
    assert not visible_on([PRIMARY], 2811, -236, *LAUNCHER)


def test_negative_coordinates_are_ordinary():
    """A monitor above or left of the primary one has negative coordinates.
    Treating those as invalid is how a window gets dragged across the desk."""
    assert visible_on([(-1920, 0, 1920, 1080)], -1800, 100, 560, 400)
    assert visible_on([(0, -1080, 1920, 1080)], 100, -900, 560, 400)


def test_a_window_straddling_two_screens_is_fine():
    assert visible_on(BOTH, 1800, 200, 560, 400)


def test_a_sliver_does_not_count():
    """A window showing ten pixels of its corner is lost for every practical
    purpose, so it is treated as lost."""
    sliver = MIN_VISIBLE - 1
    assert not visible_on([PRIMARY], 1920 - sliver, 100, 560, 400)
    assert visible_on([PRIMARY], 1920 - MIN_VISIBLE, 100, 560, 400)


def test_no_screens_at_all_is_not_visible():
    """Rather than an exception on a machine mid-way through waking up."""
    assert not visible_on([], 0, 0, 560, 400)
