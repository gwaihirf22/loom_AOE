"""
Loom — the notification watcher against real captured panels.

These fixtures are the exact frames that exposed the missed-TC bugs on
2026-07-31 (captures/run_20260731_123008_annehk_3tc-deep-dive, HUD scale measured 0.98): three
Town Centres, zero town_center_built events recorded. The synthetic tests
in test_notifications.py prove the logic; these prove it against the
game's own pixels, including the 1% font-vs-anchor scale mismatch that a
plain resize-to-measured-scale still failed on.

The sequence the panels tell:
    0331  t1209  chat lines only - primes "the feed is visible"
    0332  t1212  --Town Center Built-- fresh at the BOTTOM      (TC #2)
    0336  t1227  the same line, still at the bottom
    0348  t1271  the line redisplayed as history, 3rd from bottom
    0358  t1305  other lines only
    0359  t1311  --Town Center Built-- one line UP, born beside
                 --Heavy Plow Research Complete--               (TC #3)
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import cv2
import numpy as np
import pytest

from loom import notifications, paths

SCALE = 0.98
FIXTURES = paths.PROJECT_ROOT / "tests" / "data" / "notifications_live"


def panel(number):
    image = cv2.imread(str(FIXTURES / f"panel_{number:04d}.png"))
    assert image is not None, f"missing fixture panel_{number:04d}.png"
    return image


def blank():
    """A faded feed: no text at all."""
    return np.zeros((216, 460, 3), np.uint8)


def test_the_whole_sequence_yields_exactly_two_tcs():
    # Times follow the live look cadence: watch() runs at most a second
    # of game time apart, so a lingering line keeps refreshing its own
    # cooldown between these sampled moments. Panel 0336 is therefore
    # dated within the cooldown of 0332's sighting, as it would be live.
    watcher = notifications.NotificationWatcher()
    moments = [(331, 1209), (332, 1212), (336, 1218),
               (348, 1271), (358, 1305), (359, 1311)]
    fired = []
    for number, t in moments:
        for event in watcher.watch(panel(number), SCALE, t):
            fired.append((number, event))
    assert fired == [(332, "town_center_built"),
                     (359, "town_center_built")]


def test_bottom_line_fires_despite_the_font_scale_mismatch():
    # The anchor measured 0.98 but the phrase matches best at 0.99 - the
    # scale bracket has to bridge that, or the score sits at ~0.77
    # against the 0.8 gate (how a whole game of TCs went missing).
    watcher = notifications.NotificationWatcher()
    assert watcher.watch(panel(332), SCALE, 1212) == ["town_center_built"]


def test_redisplayed_history_does_not_fire():
    watcher = notifications.NotificationWatcher()
    watcher.watch(panel(331), SCALE, 1209)
    assert watcher.watch(panel(348), SCALE, 1271) == []


def test_sibling_arrival_fires_from_one_line_up():
    # TC #3 completed in the same redraw as Heavy Plow, which took the
    # bottom line - the TC line was never bottom-most and the bottom-only
    # rule lost it. Feed visible, phrase newly arrived: it must fire.
    watcher = notifications.NotificationWatcher()
    watcher.watch(panel(358), SCALE, 1305)
    assert watcher.watch(panel(359), SCALE, 1311) == ["town_center_built"]


def test_one_line_up_after_a_fade_is_an_echo_and_stays_one():
    # The same pixels as the sibling arrival, but the feed was blank the
    # look before: that is the redisplay-after-fade shape, and it must
    # not fire - not on first sight, and not laundered in on the second.
    watcher = notifications.NotificationWatcher()
    watcher.watch(blank(), SCALE, 1300)
    assert watcher.watch(panel(359), SCALE, 1311) == []
    assert watcher.watch(panel(359), SCALE, 1340) == []


# What the finder must report for each of these panels, checked against the
# sequence in the module docstring. These are 2560x1440 pixels, where the
# band finder was never in doubt - they are here so that a change aimed at
# 1920x1080 cannot quietly renumber the lines at the size that already
# worked.
BANDS_AND_WHERE_THE_TC_IS = {
    331: (0, None),     # chat only: nothing this finder counts as a line
    332: (2, 1),        # TC fresh at the bottom
    336: (2, 1),        # the same line, still bottom-most
    348: (3, 3),        # redisplayed as history, third from the bottom
    358: (2, None),     # other lines only
    359: (4, 2),        # TC one line up, born beside Heavy Plow
}


@pytest.mark.parametrize("number", sorted(BANDS_AND_WHERE_THE_TC_IS))
def test_the_line_positions_these_fixtures_depend_on(number):
    """The watcher is written in POSITIONS - bands[-1] is the newest line,
    bands[-2] the one above it - so every echo test in this file is really
    an assertion about where the finder puts things.

    That was left implicit, and it meant a change to the finder could move
    a line between "fires" and "cannot fire" while every test still passed
    for a different reason. Panel 0359 is the one that matters most: the
    sibling-arrival case only tests anything while its TC sits at exactly
    bands[-2].
    """
    count, lines_up = BANDS_AND_WHERE_THE_TC_IS[number]
    grey = cv2.cvtColor(panel(number), cv2.COLOR_BGR2GRAY)
    found = notifications.text_line_bands(grey, SCALE)
    assert len(found) == count, f"panel_{number:04d} banded {found}"

    watcher = notifications.NotificationWatcher()
    where = [len(found) - index for index, band in enumerate(found)
             if watcher._phrase_in_band(grey, band, "town_center_built",
                                        SCALE)]
    assert where == ([] if lines_up is None else [lines_up])
