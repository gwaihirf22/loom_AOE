"""
Loom — what the panel says before a game is running.

The overlay used to stay invisible until a match was on screen: a player who
started Loom first saw nothing at all, which looks exactly like a program
that failed to launch. It now comes up straight away with the chosen build
on it and its numbers at zero.

Zeros are the delicate part. A panel showing numbers it did not read is the
one thing Loom must never do, and what makes these honest is the banner
saying what it is waiting for. So the banner's wording is pinned here, and
so is the fact that a real reading takes it away again.

Pure functions and panel state only, no display and no QApplication - the
same way the window-flag and layout tests are written.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import overlay


def test_each_stage_says_which_one_it_is():
    """Two different facts, and the player can act on the difference: one
    means "your game is not running", the other means "it is, and I am
    watching for a match"."""
    game, _colour = overlay.describe_waiting(overlay.WAITING_FOR_GAME)
    match, _colour = overlay.describe_waiting(overlay.WAITING_FOR_MATCH)

    assert game and match
    assert game != match
    assert "GAME" in game
    assert "MATCH" in match


def test_the_banner_is_impossible_to_read_as_a_reading():
    """It sits beside a clock of 0:00 and a villager count of 0, so it has
    to be unmistakable. Upper case, like MANUAL, and in the same amber the
    not-following note uses - a state the player should notice, not an
    alarm."""
    for stage in (overlay.WAITING_FOR_GAME, overlay.WAITING_FOR_MATCH):
        text, colour = overlay.describe_waiting(stage)
        assert text == text.upper()
        assert colour == overlay.NOT_FOLLOWING_COLOR


def test_an_unknown_stage_draws_nothing():
    """"" is the header slot's own "nothing to say", the same answer
    describe_follow gives while the game is driving. A stage nobody
    recognises must not paint a stray word over the panel."""
    text, _colour = overlay.describe_waiting(None)
    assert text == ""
    assert overlay.describe_waiting("something else")[0] == ""


def test_the_waiting_banner_shares_the_slot_the_manual_note_uses():
    """Both live in the gap between the status line and the pace chip, and
    they can never be true at once - one is before a game, the other during
    one. Sharing costs no panel height, which a 186px panel cannot spare."""
    following, _colour = overlay.describe_follow(None)
    assert following == "", "a following panel must leave the slot free"
