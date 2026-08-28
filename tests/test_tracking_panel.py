"""
Loom — the two panels that have no build order behind them.

Loom reads far more than a build: the clock, the villager count, the
villagers on each resource, the population, the production queue and the age
crest are all independent of any build, and so are the alerts that matter
most - idle Town Centre, HOUSED, HOUSE SOON. These two panels are that half
of Loom on its own.

  BANDS_PANEL      the alert bands, and nothing whatsoever else
  DASHBOARD_PANEL  a live readout, with the bands under it

The bands-only panel is the one worth testing hardest, because "no card is
drawn" is a claim about pixels and nothing else can check it. A panel that
drew a faint empty card would pass every geometry assertion here and still
be wrong on screen.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from PyQt6.QtWidgets import QApplication

from loom import alerts
from loom.overlay import (BANDS_PANEL, BUILD_PANEL, DASHBOARD_PANEL,
                          MAX_ALERT_BANDS, Overlay, OverlayLayout,
                          WAITING_FOR_GAME, WAITING_FOR_MATCH, describe_tcs,
                          resource_cell, waiting_band)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def layout():
    return OverlayLayout()


SAMPLE = [("HOUSE SOON — 2 pop space left", alerts.FULL),
          ("2 TCs IDLE — 12s", alerts.FULL)]


# ---- bands only ---------------------------------------------------------


def test_the_bands_panel_has_no_card_to_measure_from(app, layout):
    """panel_height() == 0 is the whole of how this mode is drawn.

    _draw_alert_bands already measures its first band from panel_height(),
    and _resize_to_content already sizes the window from it, so a card of no
    height puts the bands at the top of a window with room for nothing else.
    Nothing else had to learn about the mode - which is why this one number
    is worth pinning.
    """
    panel = Overlay(layout=layout, panel_mode=BANDS_PANEL)

    assert panel.panel_height() == 0
    assert panel.height() == panel.chrome_height()


def test_the_bands_panel_still_reserves_room_for_both(app, layout):
    """Both bands, whether or not two are up. The first must not move when
    the second appears - that is what chrome_height exists for, and it is
    just as true with no card above them."""
    panel = Overlay(layout=layout, panel_mode=BANDS_PANEL)

    assert panel.chrome_height() == MAX_ALERT_BANDS * (layout.band_gap
                                                       + layout.band_height)


def test_the_bands_panel_is_shorter_but_not_narrower(app, layout):
    plain = Overlay(layout=layout, panel_mode=BUILD_PANEL)
    bands = Overlay(layout=layout, panel_mode=BANDS_PANEL)

    assert bands.height() < plain.height()
    assert bands.width() == plain.width(), (
        "the saved position is one offset for every mode, and default_offset "
        "measures from the panel's width - a narrower window would move "
        "where the player put it")


def test_no_card_is_drawn_and_the_bands_are(app, layout):
    """The pixel test, because every other assertion here would pass over a
    faint empty card.

    One alert, not two: the window is exactly two bands tall, so with both
    up there is no pixel left that is not a band. The second band's reserved
    space is the only place a stray card background could show, which makes
    it the one worth looking at.
    """
    panel = Overlay(layout=layout, panel_mode=BANDS_PANEL)
    panel.show_alerts(SAMPLE[:1])
    image = panel.grab().toImage()
    middle = panel.width() // 2

    # Compared against a pixel KNOWN to be untouched rather than against a
    # colour or an alpha value. QWidget.grab renders onto an opaque backing,
    # so every pixel comes back alpha=255 whatever the window's real
    # transparency - measured, not assumed. What survives that is "is this
    # the same as somewhere nothing was drawn", and the gap above the first
    # band is somewhere nothing was drawn.
    untouched = image.pixel(2, 0)

    first = layout.band_gap + layout.band_height // 2
    assert image.pixel(middle, first) != untouched, (
        "the first alert band was not painted")

    second = (layout.band_gap + layout.band_height + layout.band_gap
              + layout.band_height // 2)
    assert image.pixel(middle, second) == untouched, (
        "something was painted where only the second band belongs - in this "
        "mode there is no card, so unpainted band space stays clear")


def test_the_bands_panel_shows_alerts_with_no_reading_yet(app, layout):
    """Before a match there is no reading, and the build panel's paint path
    returns early in that case - which used to swallow the bands. A bands
    panel that waited for a reading would be blank for the whole pre-game."""
    panel = Overlay(layout=layout, panel_mode=BANDS_PANEL)
    panel.show_alerts(waiting_band(WAITING_FOR_GAME))
    image = panel.grab().toImage()

    assert not panel.have_reading
    band_middle = layout.band_gap + layout.band_height // 2
    assert (image.pixel(panel.width() // 2, band_middle)
            != image.pixel(2, 0)), "the waiting band was not painted"


def test_the_build_panel_still_draws_nothing_while_waiting(app, layout):
    """The regression guard on that change: show_waiting clears the alert
    list, so the build panel's waiting card must stay bandless."""
    panel = Overlay(layout=layout, panel_mode=BUILD_PANEL)
    panel.show_alerts(SAMPLE)
    panel.show_waiting("waiting for the game...")

    assert panel.alerts == []


# ---- the waiting band ---------------------------------------------------


def test_waiting_says_something_rather_than_nothing():
    """A transparent window with no bands is indistinguishable from a Loom
    that failed to start."""
    band = waiting_band(WAITING_FOR_GAME)

    assert len(band) == 1
    text, severity = band[0]
    assert "LOOM" in text
    assert severity == alerts.SOFT, (
        "waiting is information, not an alarm - SOFT does not flash, and an "
        "alarm that fires because nothing is wrong teaches the player to "
        "ignore the ones that mean something")


def test_both_waiting_stages_say_their_own_thing():
    assert waiting_band(WAITING_FOR_GAME) != waiting_band(WAITING_FOR_MATCH)


def test_no_stage_is_no_band():
    assert waiting_band(None) == []


# ---- the Town Centre line ----------------------------------------------


def test_the_tc_line_reads_both_counts():
    assert describe_tcs(2, 1) == "2 producing, 1 idle"


def test_one_of_each_is_named_rather_than_numbered():
    assert describe_tcs(1, 0) == "TC producing"
    assert describe_tcs(0, 1) == "TC idle"


def test_nothing_believed_yet_says_nothing():
    """Drawn as "not read" by the dashboard. An empty line would read as
    "no Town Centres", which is a claim Loom never made."""
    assert describe_tcs(0, 0) == ""


def test_the_two_counts_are_never_derived_from_each_other():
    """production.py gates "does another TC exist" and "is this one working"
    differently on purpose - erring busy is safe, erring towards a Town
    Centre that does not exist is permanent. Subtracting one from the other
    would manufacture a third answer neither gate stands behind, so a pair
    that does not add up must still report both honestly."""
    assert describe_tcs(3, 3) == "3 producing, 3 idle"


# ---- the dashboard ------------------------------------------------------


def test_the_dashboard_sits_between_the_other_two(app, layout):
    build = Overlay(layout=layout, panel_mode=BUILD_PANEL)
    dash = Overlay(layout=layout, panel_mode=DASHBOARD_PANEL)
    bands = Overlay(layout=layout, panel_mode=BANDS_PANEL)

    assert bands.panel_height() < dash.panel_height() < build.panel_height()


def test_the_dashboard_shows_what_it_read(app, layout):
    panel = Overlay(layout=layout, panel_mode=DASHBOARD_PANEL)
    panel.show_tracking(20, 580, {"food": 11, "wood": 8}, (76, 100),
                        age_text="Feudal Age", tc_text="2 producing, 1 idle")

    assert panel.have_reading
    assert panel.status_line == "9:40   20 villagers"
    assert panel.population_text == "76/100"
    assert panel.pace_text == "Feudal Age"


def test_an_unread_crest_admits_it_rather_than_guessing(app, layout):
    """The never-guess rule reaching the panel. A blank right-hand slot
    would read as "no age", which is not something Loom ever concluded."""
    panel = Overlay(layout=layout, panel_mode=DASHBOARD_PANEL)
    panel.show_tracking(20, 580, {}, None, age_text="", tc_text="")

    assert panel.pace_text == "age not read"
    assert panel.population_text == ""


def test_the_dashboard_has_no_targets_to_be_off(app, layout):
    """None, not {}. An empty dict says the build wants nobody on wood,
    which makes eight villagers there an off-target error worth a red
    underline - a verdict against a plan that does not exist."""
    panel = Overlay(layout=layout, panel_mode=DASHBOARD_PANEL)
    panel.show_tracking(20, 580, {"wood": 8}, None)

    assert panel.targets is None


def test_the_dashboard_paints_a_whole_frame(app, layout):
    """It is drawn by hand rather than by the build panel's row machinery,
    so the one thing worth checking is that a full frame paints at all."""
    panel = Overlay(layout=layout, panel_mode=DASHBOARD_PANEL)
    panel.show_tracking(20, 580, {"food": 11, "wood": 8, "gold": 4},
                        (76, 100), age_text="Feudal Age",
                        tc_text="2 producing, 1 idle")
    panel.show_alerts(SAMPLE)

    image = panel.grab().toImage()
    assert image.width() == panel.width()
    assert image.height() == panel.height()


# ---- the resource row with no plan behind it ---------------------------
#
# Found by rendering the panel and LOOKING at it, which is the only way it
# could have been found: every geometry assertion passed while the row read
# "8/None" on screen. The off-target and dim rules had learned that targets
# could be None; the text itself had not. The decision is a pure function
# now, so these call the real one rather than restating it.


def test_a_count_with_no_plan_stands_alone():
    assert resource_cell(None, 8) == ("8", False, False)


def test_nothing_read_and_no_plan_is_an_admission_not_a_zero():
    """"No villagers on stone" and "I could not see the stone count" are
    different facts, and a 0 would state the one Loom did not establish."""
    text, off, dim = resource_cell(None, None)

    assert text == "–"
    assert dim


def test_no_plan_means_nothing_can_be_off_target():
    """A red underline against a target that does not exist would be a
    verdict Loom never reached."""
    for have in (0, 1, 40, None):
        _text, off, _dim = resource_cell(None, have)
        assert off is False


def test_a_plan_still_reads_have_over_want():
    assert resource_cell(6, 8)[0] == "8/6"
    assert resource_cell(6, None)[0] == "6"


def test_a_plan_still_flags_a_count_that_is_off():
    assert resource_cell(6, 20)[1] is True
    assert resource_cell(6, 6)[1] is False
