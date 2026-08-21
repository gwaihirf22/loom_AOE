"""
Loom — tests for reading the game's notification feed.

The panel fixture is built from the real harvested phrase template pasted
onto a flat dark panel, so these tests exercise the same matching path as a
live frame without committing megabytes of screenshots.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import cv2
import numpy as np
import pytest

from loom import notifications, paths


@pytest.fixture(scope="module")
def phrase():
    image = cv2.imread(str(paths.TEMPLATES_DIR / "notifications"
                           / "town_center_built.png"), cv2.IMREAD_GRAYSCALE)
    assert image is not None, "harvested phrase template missing"
    return image


def panel_with(*lines):
    """A fake notification panel holding the given lines, top to bottom.

    Lines land on the game's ~40-row pitch so the band finder sees them as
    separate text lines, the way the real stack draws them.
    """
    panel = np.full((200, 500, 3), (40, 44, 42), np.uint8)   # dark feed box
    y = 40
    for line in lines:
        if line is not None:
            h, w = line.shape
            # Paste the white text over the panel the way the game
            # composites it: bright glyphs and their dark outlines both.
            panel[y:y + h, 30:30 + w] = cv2.cvtColor(line,
                                                     cv2.COLOR_GRAY2BGR)
        y += 40
    return panel


def test_phrase_fires_once_per_appearance(phrase):
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    assert watcher.watch(seen, 1.0, 100) == ["town_center_built"]
    # The line lingers on screen for many polls: same event, no re-fire.
    assert watcher.watch(seen, 1.0, 103) == []
    assert watcher.watch(seen, 1.0, 110) == []


def test_lingering_past_cooldown_does_not_refire(phrase):
    # A line that stays visible refreshes its own cooldown - only absence
    # followed by reappearance is a new event.
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    watcher.watch(seen, 1.0, 100)
    for t in range(103, 160, 3):
        assert watcher.watch(seen, 1.0, t) == []


def test_reappearance_after_gap_is_a_new_event(phrase):
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    empty = panel_with(None)
    watcher.watch(seen, 1.0, 100)
    for t in (110, 120, 130):
        watcher.watch(empty, 1.0, t)
    # A second TC finishing later must count again.
    assert watcher.watch(seen, 1.0, 140) == ["town_center_built"]


def test_empty_panel_is_silent(phrase):
    watcher = notifications.NotificationWatcher()
    assert watcher.watch(panel_with(None), 1.0, 100) == []


def newer_line():
    """A stand-in for a fresher message below the phrase - any outlined
    bright text the band finder counts as a line of the stack."""
    line = np.zeros((30, 300), np.uint8)
    cv2.putText(line, "--Villager Created--", (4, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, 255, 2)
    return line


def test_history_echo_above_a_newer_line_does_not_fire(phrase):
    # The panel REDISPLAYS recent history whenever any new message
    # arrives, so an old line resurfaces above the newer one - measured
    # live: one Town Centre fired three times over 92 game seconds this
    # way. Only the stack's bottom line is a fresh event.
    watcher = notifications.NotificationWatcher()
    assert watcher.watch(panel_with(phrase), 1.0, 100) \
        == ["town_center_built"]
    watcher.watch(panel_with(None), 1.0, 115)            # stack faded
    echo = panel_with(phrase, newer_line())              # history redrawn
    assert watcher.watch(echo, 1.0, 130) == []
    assert watcher.watch(echo, 1.0, 150) == []           # however long


def test_an_echo_does_not_block_a_real_later_event(phrase):
    # Echo sightings must not touch the cooldown clock: a real TC
    # finishing shortly after an echo still needs to count.
    watcher = notifications.NotificationWatcher()
    watcher.watch(panel_with(phrase), 1.0, 100)
    watcher.watch(panel_with(phrase, newer_line()), 1.0, 120)   # echo
    assert watcher.watch(panel_with(phrase), 1.0, 130) \
        == ["town_center_built"]


def test_wrapped_attack_warning_fires_from_one_line_up():
    # The attack warning wraps across two lines and its template is the
    # FIRST of the pair, so a fresh warning shows that text one line above
    # the stack's bottom - it must still fire, and the wild-animals second
    # line (bottom-most) fires with it.
    # A phrase now carries one template per rendering; these two have only
    # the scale-1.0 harvest, and this panel is composed at scale 1.0.
    templates = notifications.load_phrase_templates()
    warning = panel_with(templates["attacked"][0][0],
                         templates["wild_animals"][0][0])
    watcher = notifications.NotificationWatcher()
    events = watcher.watch(warning, 1.0, 100)
    assert set(events) == {"attacked", "wild_animals"}


def test_ink_agreement_separates_words_from_other_words():
    """Correlation finds a candidate spot; ink agreement confirms the
    actual words. Re-measured across the live fixtures and two capture
    runs: a real sighting against a template harvested at its own
    rendering reads 0.55-0.76, and a panel holding only OTHER text reads
    0.38 or less. (This file used to claim 0.94+, which was never true of
    the corpus and made the gate look far roomier than it is.)"""
    tcb = cv2.imread(str(paths.TEMPLATES_DIR / "notifications"
                         / "town_center_built.png"), cv2.IMREAD_GRAYSCALE)
    attacked = cv2.imread(str(paths.TEMPLATES_DIR / "notifications"
                              / "attacked.png"), cv2.IMREAD_GRAYSCALE)
    # Not 1.0: the region's adaptive threshold admits anti-aliased glyph
    # edges the template's fixed gate excludes.
    assert notifications.ink_agreement(tcb, tcb) \
        >= notifications.MIN_INK_AGREEMENT
    other = attacked[:tcb.shape[0], :tcb.shape[1]]
    assert notifications.ink_agreement(tcb, other) \
        < notifications.MIN_INK_AGREEMENT
    blank = np.zeros_like(tcb)
    assert notifications.ink_agreement(tcb, blank) == 0.0


def test_no_clock_no_events(phrase):
    # The cooldown is driven by game time; without a clock the watcher
    # stays quiet rather than risking double counts.
    watcher = notifications.NotificationWatcher()
    assert watcher.watch(panel_with(phrase), 1.0, None) == []


def test_reprint_after_absence_fires_inside_the_cooldown(phrase):
    # The game never reprints a line that is already on screen, so a
    # reprint after real absence IS a new event - TCs built ~18 game
    # seconds apart each reprint, and the cooldown alone swallowed them
    # (measured live: 3 built, 1 counted). Three absent looks rearm.
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    empty = panel_with(None)
    assert watcher.watch(seen, 1.0, 100) == ["town_center_built"]
    watcher.watch(seen, 1.0, 102)               # lingers, refreshes
    for t in (104, 105, 106):                   # gone three looks: rearm
        watcher.watch(empty, 1.0, t)
    # Well inside the 15s cooldown of the t=102 sighting - fires anyway.
    assert watcher.watch(seen, 1.0, 107) == ["town_center_built"]


def test_lingering_line_still_fires_exactly_once(phrase):
    # Rearm must never help a line that stays on screen: it is sighted
    # every look, its absence streak stays zero, and however long it
    # lingers it is one event (the 0.3s-cooldown experiment refired one
    # line thirteen times; absence-rearm must not recreate that).
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    assert watcher.watch(seen, 1.0, 100) == ["town_center_built"]
    for t in range(101, 140, 2):
        assert watcher.watch(seen, 1.0, t) == []


def test_echo_sightings_hold_the_rearm_off(phrase):
    # A history echo means the phrase's pixels are still on screen, so
    # the game would not reprint it - an echo sighting must reset the
    # absence streak (no rearm), while still never firing itself.
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    empty = panel_with(None)
    echo = panel_with(phrase, newer_line())
    assert watcher.watch(seen, 1.0, 100) == ["town_center_built"]
    watcher.watch(empty, 1.0, 104)              # one absent look...
    for t in (105, 106, 107):                   # ...echo interrupts it
        assert watcher.watch(echo, 1.0, t) == []
    for t in (108, 109, 110):                   # true absence at last
        watcher.watch(empty, 1.0, t)
    assert watcher.watch(seen, 1.0, 111) == ["town_center_built"]


def test_rolled_up_line_cannot_rearm_or_refire(phrase):
    # A busy feed pushes a line above the bands it may fire from. It is
    # still ON SCREEN: it must neither tick the absence streak (which
    # would rearm the cooldown) nor look like a fresh arrival when churn
    # brings it back into range - a sandbox game minted four phantom TCs
    # exactly this way, one every ~10 seconds of one line's lifetime.
    watcher = notifications.NotificationWatcher()
    assert watcher.watch(panel_with(phrase), 1.0, 100) \
        == ["town_center_built"]
    watcher.watch(panel_with(phrase, newer_line()), 1.0, 103)
    # Two newer lines: the phrase sits third from the bottom, unsearchable
    # for firing but plainly visible. Long enough to outlive the cooldown.
    deep = panel_with(phrase, newer_line(), newer_line())
    for t in (106, 109, 112, 115, 118, 121):
        assert watcher.watch(deep, 1.0, t) == []
    # The churn brings it back to one-up: NOT fresh - it never left.
    assert watcher.watch(panel_with(phrase, newer_line()), 1.0, 124) == []
    assert watcher.watch(panel_with(phrase, newer_line()), 1.0, 127) == []


def two_town_centres(looks_per_second=1):
    """A script for two Town Centres: the first line arrives, is pushed off
    early by a busy feed, and the second line arrives while the cooldown
    from the first is still running.

    The gap is derived from REARM_SECONDS rather than written down, because
    that constant is the thing under test and a duplicated number would
    just drift away from it.
    """
    gone_at = 4
    arrives_at = gone_at + notifications.REARM_SECONDS + 3
    end = arrives_at + 12
    script = []
    step = 1.0 / looks_per_second
    moment = 0.0
    while moment < end:
        if moment < gone_at or moment >= arrives_at:
            script.append((moment, "phrase"))
        else:
            script.append((moment, "busy"))
        moment += step
    return script


def test_a_second_town_centre_inside_the_cooldown_is_still_counted(phrase):
    """Two Town Centres finishing close together, which is the case the
    cooldown alone got wrong and got wrong SILENTLY.

    The cooldown is refreshed by every sighting in a band the phrase may
    fire from. So when a second Town Centre's line arrives inside the
    window, its own sightings keep pushing the window forward and the
    fifteen seconds never elapse - the event is not delayed, it is lost.
    Measured on two lines 16 game-seconds apart, the second was never
    counted at all. Absence is what rescues it: the first line provably
    left the screen, and the game does not reprint a phrase whose line is
    still up, so what came back is a different line.

    KNOWN LIMIT, and it is why this test derives its gap instead of
    choosing one: the two Town Centres have to be separated by more than
    REARM_SECONDS of absence. Closer than that and the second is still
    lost, because detection flicker on a lingering line produces absences
    of the same length - measured, 7 seconds of flicker against 8 seconds
    for a real gap - and nothing in the pixels separates them. See
    REARM_SECONDS.
    """
    watcher = notifications.NotificationWatcher()
    seen = panel_with(phrase)
    busy = panel_with(newer_line(), newer_line())

    fired = []
    for moment, which in two_town_centres():
        panel = seen if which == "phrase" else busy
        fired += [event for event in watcher.watch(panel, 1.0, moment)
                  if event == "town_center_built"]

    assert len(fired) == 2, "the second Town Centre was not counted"


def test_the_count_does_not_depend_on_how_often_the_feed_is_looked_at(phrase):
    """A reading that changes with the poll rate is not a reading.

    Looks are as dense as the poll loop makes them, and load shedding
    makes them sparser mid-game. Both failures this module has had were
    rate-dependent: a lingering line counted twice at two looks per frame
    and once at one, and a real second line counted at one and lost at
    three. Whatever the cadence, this sequence holds two Town Centres.
    """
    seen = panel_with(phrase)
    busy = panel_with(newer_line(), newer_line())

    counts = []
    for rate in (1, 2, 3, 4):
        watcher = notifications.NotificationWatcher()
        fired = []
        for moment, which in two_town_centres(rate):
            panel = seen if which == "phrase" else busy
            fired += [event for event in watcher.watch(panel, 1.0, moment)
                      if event == "town_center_built"]
        counts.append(len(fired))

    assert len(set(counts)) == 1, (
        f"counted {counts} at 1/2/3/4 looks per second - the answer "
        "depends on the poll rate")
    assert counts[0] == 2
