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
    templates = notifications.load_phrase_templates()
    warning = panel_with(templates["attacked"], templates["wild_animals"])
    watcher = notifications.NotificationWatcher()
    events = watcher.watch(warning, 1.0, 100)
    assert set(events) == {"attacked", "wild_animals"}


def test_ink_agreement_separates_words_from_other_words():
    """Correlation finds a candidate spot; ink agreement confirms the
    actual words. Measured live: a real sighting reads 0.94+, a panel
    holding only OTHER text reads 0.36 or less."""
    tcb = cv2.imread(str(paths.TEMPLATES_DIR / "notifications"
                         / "town_center_built.png"), cv2.IMREAD_GRAYSCALE)
    attacked = cv2.imread(str(paths.TEMPLATES_DIR / "notifications"
                              / "attacked.png"), cv2.IMREAD_GRAYSCALE)
    # Not 1.0: the region's adaptive threshold admits anti-aliased glyph
    # edges the template's fixed gate excludes. Real sightings read 0.91+.
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
