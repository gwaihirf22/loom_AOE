"""
Loom — reading the notification feed at 1920x1080.

The whole suite's live pixels were 2560x1440 until now, and one phrase
quietly stopped being readable below that. The cost was not a missed line:
it was two imaginary Town Centres, 674 game-seconds charged as idle TC time
in a match where all three Town Centres were producing almost constantly,
and a "2 TCs IDLE" band on screen for 149 unbroken seconds.

Two faults, and the second only bites because of the first.

  * templates/notifications/town_center_built.png was cut at 2560x1440. The
    game does not draw this feed by scaling one master - at 1080p it lays
    the text out at a smaller point size - so resizing that harvest down
    compares against a shape the screen never drew. ink_agreement also
    thresholds the template at a fixed 200 while thresholding the region
    relative to its own maximum, so shrinking a template thins its own ink
    and the score decays with scale. Compared against ITSELF at 0.735 this
    phrase scores 0.598, under the 0.6 gate: at 1080p a perfect, noise-free
    match could not pass. Live it was recognised on 23 looks out of 46.

  * Every echo guard in watch() is built on "was this phrase sighted last
    look", and _tick_absence read three unrecognised looks as "the line has
    gone, the game may print it again". At 50% detection that is reached
    constantly. Traced here frame by frame: the line arrives and fires, is
    missed four looks running while plainly still on screen, and the next
    sighting fires a second, imaginary Town Centre five seconds later.

The fixtures are consecutive panels from captures/run_20260820_153531_annehk
at anchor scale 0.735, covering exactly that sequence. Which panels the
reader recognises and which it does not was checked frame by frame before
these tests were written:

    panel_0185  the line has not arrived yet
    panel_0186  it arrives in the bottom band          -> recognised
    panel_0187  still on screen                        -> MISSED
    panel_0188  still on screen                        -> MISSED
    panel_0189  still on screen                        -> recognised
    panel_0190  rolled one line up                     -> recognised
    panel_0191  rolled one line up                     -> recognised

Note 0187 and 0188: those misses are why this file exists, and they are
still misses. The fix is not that every look now succeeds - it is that a
miss can no longer be mistaken for the line leaving the screen.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import numpy as np
import pytest

from loom import notifications

DATA = pathlib.Path(__file__).parent / "data" / "notifications_1080p"

# The anchor's measurement for this run. 1920x1080 against templates cut at
# 2560x1440 lands here, and every number in this file was measured at it.
SCALE = 0.735

# The GAME's own clock for each fixture panel, read off the frames rather
# than assumed. Assuming was the first mistake here: a guess of "about two
# seconds a frame" was out by nearly a factor of two, and it made the
# absence between panels look shorter than it is - which is exactly the
# quantity REARM_SECONDS is compared against, so the test passed for the
# wrong reason. panel_0186's own clock does not read; it sits between its
# neighbours and is interpolated.
CLOCK = {185: 1337, 186: 1341, 187: 1344,
         188: 1348, 189: 1351, 190: 1355, 191: 1358}

# The live overlay polls every 300ms and reads this feed every third poll,
# so it looked at each of these panels about twice. That matters because
# the fault these fixtures pin is a count of LOOKS being mistaken for a
# span of time.
LOOKS_PER_PANEL = 2


def panel(number):
    """One fixture panel by its frame number."""
    image = cv2.imread(str(DATA / f"panel_{number:04d}.png"))
    assert image is not None, f"missing fixture panel_{number:04d}.png"
    return image


def panels():
    """The lingering-line SEQUENCE, in order.

    Named by CLOCK rather than globbed: this directory also holds panels
    cut for the band-structure tests, which are single frames from
    elsewhere in the same game and are not part of this timeline.
    """
    return [panel(number) for number in sorted(CLOCK)]


def moments():
    """Each panel with the game time it was actually captured at."""
    numbers = sorted(CLOCK)
    return [(CLOCK[n], n) for n in numbers]


def replay(watcher, looks_per_panel=LOOKS_PER_PANEL):
    """Feed the sequence through a watcher and collect the TC events.

    Extra looks are spread across the real interval to the next frame, so
    a denser poll rate does not also become a faster clock.
    """
    fired = []
    every = panels()
    times = [t for t, _n in moments()]
    for index, panel in enumerate(every):
        following = times[index + 1] if index + 1 < len(times) else times[-1]
        step = (following - times[index]) / max(1, looks_per_panel)
        for look in range(looks_per_panel):
            fired += [event for event
                      in watcher.watch(panel, SCALE,
                                       times[index] + look * step)
                      if event == "town_center_built"]
    return fired


def test_one_town_centre_fires_once_however_often_it_is_looked_at():
    """The regression. One Town Centre was built in this sequence, and the
    watcher must say so once - at any polling rate.

    Rate is the point. The old rearm counted looks, so how many times a
    lingering line could re-fire depended on how fast the overlay happened
    to be polling: at one look per panel this sequence fired once, and at
    two or more it fired twice. A reading that changes with the poll loop
    is not a reading.
    """
    for looks in (1, 2, 3, 4):
        watcher = notifications.NotificationWatcher()
        assert replay(watcher, looks) == ["town_center_built"], (
            f"fired more than once at {looks} looks per panel")


def test_an_unrecognised_look_is_not_a_line_leaving_the_screen():
    """The mechanism, stated on its own.

    panel_0187 and panel_0188 are misses with the line still on screen. At
    the live cadence they are FOUR unrecognised looks in a row, which is
    past REARM_LOOKS - so if looks alone could rearm, the cooldown clears
    and panel_0189 fires a second Town Centre five seconds after the first.
    The feed is showing two and three text lines throughout, so it never
    proves the line is gone.

    Both looks at each panel matter here: at one look per panel the streak
    only reaches two and the fault hides. That is the same accident of
    timing that decided whether this bug appeared in any given game.
    """
    watcher = notifications.NotificationWatcher()
    everything = panels()

    assert watcher.watch(everything[1], SCALE, CLOCK[186]) == [
        "town_center_built"]
    assert watcher.watch(everything[1], SCALE, CLOCK[186] + 1) == []
    for moment, panel in ((CLOCK[187], everything[2]),
                          (CLOCK[187] + 2, everything[2]),
                          (CLOCK[188], everything[3]),
                          (CLOCK[188] + 2, everything[3])):
        assert watcher.watch(panel, SCALE, moment) == []
    # Seven game seconds of not being recognised, and the line never left.
    assert watcher.watch(everything[4], SCALE, CLOCK[189]) == []


def test_a_blank_feed_still_rearms_at_once():
    """The exemption that keeps the rearm useful. An empty panel is proof
    rather than evidence - there is no text at all, so the line has gone,
    and no amount of matcher doubt applies to a blank picture. A phrase
    reprinted after that is a new event even inside the cooldown."""
    watcher = notifications.NotificationWatcher()
    everything = panels()
    blank = everything[0] * 0            # no text: nothing to find

    assert watcher.watch(everything[1], SCALE, CLOCK[186]) == [
        "town_center_built"]
    for moment in (CLOCK[187], CLOCK[188], CLOCK[189]):
        watcher.watch(blank, SCALE, moment)
    assert watcher.watch(everything[1], SCALE, CLOCK[190]) == [
        "town_center_built"]


def test_the_phrase_is_recognised_at_this_size_at_all():
    """Detection itself, which is what the native template bought. Four of
    these six panels carry the line in a band the reader searches; with the
    1440p harvest resized down, this used to be about half."""
    watcher = notifications.NotificationWatcher()
    recognised = 0
    for panel in panels():
        grey = cv2.cvtColor(panel, cv2.COLOR_BGR2GRAY)
        bands = notifications.text_line_bands(grey, SCALE)
        if any(watcher._phrase_in_band(grey, band, "town_center_built",
                                       SCALE) for band in bands):
            recognised += 1
    assert recognised >= 4, f"only {recognised} of 7 panels recognised"


def test_a_template_clears_the_ink_gate_against_itself_at_any_hud_scale():
    """The cheap, general form of the first fault, and the one test that
    would have caught it before a game did.

    ink_agreement is asymmetric, so a template resized away from its
    harvest size loses agreement with ITSELF. Once that falls under the
    gate, no rendering of that phrase can ever be recognised at that HUD
    scale - the ceiling is below the floor. Every phrase must hold at every
    scale Loom claims to read.
    """
    watcher = notifications.NotificationWatcher()
    for name in watcher.templates:
        for scale in (1.0, 0.98, 0.9, 0.8, SCALE, 0.7):
            index = watcher._variants_near(name, scale)[0]
            sized = watcher._template_at(name, index, scale)
            agreement = notifications.ink_agreement(sized, sized)
            assert agreement >= notifications.MIN_INK_AGREEMENT, (
                f"{name} at HUD scale {scale} scores {agreement:.3f} "
                f"against itself - it can never be recognised there")


def test_the_town_centre_phrase_ships_a_template_for_this_rendering():
    """Stated explicitly because the fix IS the file. Without a variant cut
    near this scale the phrase falls back to resizing the 1440p harvest,
    which is what could not clear the ink gate."""
    variants = notifications.NotificationWatcher().templates[
        "town_center_built"]
    nearest = min(abs(harvest - SCALE) for _image, harvest in variants)
    assert nearest < 0.05, ("no town_center_built template harvested near "
                            f"{SCALE}; have {[s for _i, s in variants]}")


@pytest.mark.parametrize("scale", [1.0, 0.98, SCALE])
def test_a_variant_is_used_untouched_at_its_own_harvest_scale(scale):
    """A template already the right size must not be resampled to it.
    Resizing a picture to the size it already is only costs sharpness, and
    sharpness is exactly what the ink gate measures."""
    watcher = notifications.NotificationWatcher()
    for name, variants in watcher.templates.items():
        for index, (image, harvest) in enumerate(variants):
            if abs(harvest - scale) < 0.005:
                sized = watcher._template_at(name, index, scale)
                assert sized.shape == image.shape


# What is ACTUALLY on screen in each of these panels, read by eye off the
# frames before any of these numbers were written down. Two of them are the
# ones that produced a phantom Town Centre; the other two are the same fault
# in the opposite direction.
#
#   0250  --Heavy Plow Research Complete-- / --Villager Created--
#   0251  --Town Center Built-- / --Heavy Plow ...-- / --Villager Created--
#   0252  the same three, one look later
#   0229  --House Built-- / --Chain Barding Armor Research / Complete--
#         / --Villager Created-- / --Town Center Built--   (the middle
#         message WRAPS, so five rendered lines from four messages)
VISIBLE_LINES = {229: 5, 250: 2, 251: 3, 252: 3}


@pytest.mark.parametrize("number", sorted(VISIBLE_LINES))
def test_the_band_count_is_the_number_of_lines_on_screen(number):
    """The finder's whole contract, and it used to be wrong in both
    directions on these four panels: 250 read four lines where there are
    two, 252 read five where there are three, 251 read two where there are
    three, and 229 read four where there are five.

    It matters because the watcher is written in terms of position -
    bands[-1] is the newest line, bands[-2] the one above it - so a
    miscount does not degrade the reading, it renumbers every line and
    aims every guard at the wrong one.
    """
    grey = cv2.cvtColor(panel(number), cv2.COLOR_BGR2GRAY)
    found = notifications.text_line_bands(grey, SCALE)
    assert len(found) == VISIBLE_LINES[number], (
        f"panel_{number:04d} has {VISIBLE_LINES[number]} lines on screen, "
        f"found {len(found)}: {found}")


def test_a_redisplayed_echo_lands_outside_every_fireable_band():
    """panel_0251 is the frame that minted the last phantom Town Centre.

    The feed had faded and redisplayed its history, so "--Town Center
    Built--" is at the TOP of a three-line stack, above two lines that were
    already on screen unchanged the look before. Redisplayed history is
    exactly what the bottom-line rule exists to ignore - but the finder
    fused the two lines below it into one band, which made the echo the
    second band from the bottom, and the second band from the bottom is a
    band this phrase may fire from.

    Three lines, and the echo three up: unsearchable, which is the answer.
    """
    grey = cv2.cvtColor(panel(251), cv2.COLOR_BGR2GRAY)
    watcher = notifications.NotificationWatcher()
    found = notifications.text_line_bands(grey, SCALE)
    where = [len(found) - index for index, band in enumerate(found)
             if watcher._phrase_in_band(grey, band, "town_center_built",
                                        SCALE)]

    assert where == [3], f"the echo is at lines_up {where}, not 3"
    searchable = {notifications.LINES_FROM_BOTTOM.get("town_center_built", 1)}
    searchable.update(notifications.EXTRA_LINES.get("town_center_built", ()))
    assert not searchable.intersection(where), (
        "the echo is sitting in a band the phrase can fire from")


def test_terrain_alone_is_never_a_text_line():
    """The other half of the miscount: bright terrain showing through the
    translucent panel is bright AND next to dark, so it passes the ink
    test, and below the feed there is nothing to disagree with it. It
    produced 39- and 45-row bands - not the "sub-10-row flecks" this
    module used to claim - at y=123 and y=141 of a 162-row panel, and at
    y=8 above the feed.

    The fixture is the one glyphs.find_lines already uses for this; it had
    never been pointed at this finder.
    """
    terrain = cv2.imread(str(pathlib.Path(__file__).parent / "data"
                             / "glyphs_live" / "terrain_panel.png"))
    assert terrain is not None, "missing terrain_panel fixture"
    grey = cv2.cvtColor(terrain, cv2.COLOR_BGR2GRAY)
    assert notifications.text_line_bands(grey, 0.98) == []
    assert notifications.text_line_bands(grey, SCALE) == []


def test_the_dark_box_is_what_rejects_terrain():
    """Stated on its own because it is the load-bearing idea, and because
    it is not obvious that a TEXT finder should care how dark the
    background is.

    Terrain seen through the translucent panel is bright pixels with dark
    ones scattered among them, which is precisely what the ink test looks
    for - "bright NEXT TO near-black" - so every row of it reads as inked
    and the old finder banded whatever height the speckle happened to
    span. What terrain does not have is the notification box: a real line
    is drawn ON one, so it sits on a lot of near-black, and scenery sits
    on none.

    The speckle below inks all ninety rows and yields no band at all.
    """
    rng = np.random.default_rng(7)
    terrain = np.full((90, 300), 200, np.uint8)
    terrain[rng.random(terrain.shape) < 0.10] = 20

    inked = notifications._ink_rows(terrain)
    assert (inked > 6).sum() > 60, (
        "this fixture is meant to LOOK like text to the ink test")
    assert notifications.text_line_bands(terrain, 1.0) == [],         "scattered dark speckle is scenery, not a line of text"

    # The same phrase, drawn on a box: found.
    phrase = notifications.NotificationWatcher().templates[
        "town_center_built"][0][0]
    height, width = phrase.shape
    on_the_box = np.zeros((height + 40, width + 40), np.uint8)
    on_the_box[20:20 + height, 20:20 + width] = phrase
    assert notifications.text_line_bands(on_the_box, 1.0),         "text on a dark box must be found"
