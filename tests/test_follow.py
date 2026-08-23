"""
Loom — the manual cursor, with a fake clock.

loom/follow.py is the one piece of build-order state in the whole program.
Everything else is recomputed from the villager count and the game clock every
poll, which is what lets build_order.py claim to be pure logic; a player
pressing "next step" is a fact about the player, not about the game, so it has
to be remembered - and remembered somewhere small enough to reason about.

The clock is injected, so nothing here sleeps and the hold can be tested at
the exact instant it expires.

The behaviour that matters most is that the hold always ends. An overlay that
stopped following the game and never resumed, while looking exactly as it
always does, is the silent desynchronisation the rest of Loom is built to
prevent - it is the same failure the read filters have their own design rule
about ("read filters must never get stuck").
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import follow


def state(hold_seconds=10, step_count=20):
    return follow.FollowState(hold_seconds=hold_seconds, step_count=step_count)


# ---- the normal case -------------------------------------------------------

def test_it_follows_the_game_by_default():
    """The whole pitch. Nothing pressed, nothing overridden."""
    following = state()

    assert following.effective_index(7, now=100) == 7
    assert following.mode(now=100) == follow.FOLLOWING


def test_the_game_keeps_moving_it_while_following():
    following = state()

    assert following.effective_index(7, now=100) == 7
    assert following.effective_index(8, now=101) == 8


# ---- stepping --------------------------------------------------------------

def test_next_step_moves_forward_from_where_the_game_is():
    following = state()

    following.next_step(auto_index=7, now=100)

    assert following.effective_index(7, now=100) == 8
    assert following.mode(now=100) == follow.HOLDING


def test_previous_step_moves_back():
    following = state()

    following.previous_step(auto_index=7, now=100)

    assert following.effective_index(7, now=100) == 6


def test_two_presses_move_two_steps():
    """The bug this guards against: stepping from auto_index every time would
    make the second press undo the first whenever the reading had not changed
    yet - which is exactly when a player is pressing it."""
    following = state()

    following.next_step(auto_index=7, now=100)
    following.next_step(auto_index=7, now=100.5)

    assert following.effective_index(7, now=101) == 9


def test_the_game_cannot_move_the_cursor_during_a_hold():
    """The point of the hold. A villager arriving mid-hold must not yank the
    panel back while the player is still reading."""
    following = state()
    following.next_step(auto_index=7, now=100)

    assert following.effective_index(9, now=105) == 8


# ---- the hold always ends --------------------------------------------------

def test_the_hold_expires_and_the_game_takes_over():
    following = state(hold_seconds=10)
    following.next_step(auto_index=7, now=100)

    assert following.effective_index(9, now=110) == 9
    assert following.mode(now=110) == follow.FOLLOWING


def test_the_hold_expires_exactly_on_time():
    following = state(hold_seconds=10)
    following.next_step(auto_index=7, now=100)

    assert following.effective_index(9, now=109.99) == 8
    assert following.effective_index(9, now=110.0) == 9


def test_a_press_during_a_hold_restarts_the_clock():
    following = state(hold_seconds=10)
    following.next_step(auto_index=7, now=100)
    following.next_step(auto_index=7, now=108)

    assert following.effective_index(20, now=115) == 9      # still held
    assert following.effective_index(20, now=118) == 20     # now expired


def test_the_countdown_reports_whole_seconds_remaining():
    following = state(hold_seconds=10)
    following.next_step(auto_index=7, now=100)

    assert following.seconds_left(now=100) == 10
    assert following.seconds_left(now=105.5) == 5
    assert following.seconds_left(now=110) is None


def test_nothing_is_counting_down_while_following():
    assert state().seconds_left(now=100) is None


# ---- the toggle ------------------------------------------------------------

def test_toggling_stops_the_game_moving_the_panel():
    following = state()

    following.toggle()

    assert following.mode(now=100) == follow.MANUAL
    assert following.effective_index(7, now=100) == 7      # no cursor yet
    following.next_step(auto_index=7, now=100)
    assert following.effective_index(99, now=100) == 8


def test_a_manual_hold_never_expires():
    """Toggled off means off until said otherwise. A countdown here would
    imply following is about to come back when it is not."""
    following = state(hold_seconds=10)
    following.toggle()
    following.next_step(auto_index=7, now=100)

    assert following.effective_index(99, now=1_000_000) == 8
    assert following.mode(now=1_000_000) == follow.MANUAL


def test_toggling_back_on_snaps_to_the_game_at_once():
    following = state()
    following.toggle()
    following.next_step(auto_index=7, now=100)

    following.toggle()

    assert following.effective_index(15, now=101) == 15
    assert following.mode(now=101) == follow.FOLLOWING


def test_the_toggle_reports_the_new_state():
    following = state()

    assert following.toggle() is False
    assert following.toggle() is True


# ---- a new match -----------------------------------------------------------

def test_a_new_game_clears_the_cursor():
    """A cursor left pointing at step 20 of the last game is exactly the
    silent desync everything else in Loom exists to avoid."""
    following = state()
    following.next_step(auto_index=18, now=100)

    following.reset()

    assert following.effective_index(0, now=101) == 0
    assert following.mode(now=101) == follow.FOLLOWING


def test_a_new_game_switches_following_back_on():
    """Otherwise one toggle earlier in the session quietly costs the player
    help for every game after it."""
    following = state()
    following.toggle()

    following.reset()

    assert following.auto is True


# ---- staying inside the build ----------------------------------------------

def test_it_cannot_run_off_the_end():
    following = state(step_count=5)
    for moment in range(20):
        following.next_step(auto_index=0, now=100 + moment)

    assert following.effective_index(0, now=100) == 4


def test_it_cannot_go_below_before_the_first_step():
    """-1 is a real position - "before step one" - and the floor, because
    that is what current_index() returns at the start of a match."""
    following = state()
    for moment in range(10):
        following.previous_step(auto_index=0, now=100 + moment)

    assert following.effective_index(0, now=100) == -1


def test_an_unknown_step_count_still_clamps_the_bottom():
    following = follow.FollowState(hold_seconds=10, step_count=None)
    following.previous_step(auto_index=-1, now=100)

    assert following.effective_index(0, now=100) == -1


# ---- the hold length is configurable ---------------------------------------

def test_the_hold_length_is_honoured():
    following = state(hold_seconds=30)
    following.next_step(auto_index=7, now=100)

    assert following.effective_index(9, now=125) == 8
    assert following.effective_index(9, now=131) == 9


# ---- the tail: reviewing a finished build ----------------------------------
#
# When the build order completes the overlay parks on its report card, and
# before this that card was a dead end - the step keys moved the cursor but
# nothing could be shown, so a player could not look back at what the build
# had actually asked for.
#
# The card is a slot of its own now, one past the last step, and every
# behaviour asked for falls out of the cursor arithmetic that was already
# here rather than out of a special case.


def test_without_a_tail_the_last_step_is_still_the_ceiling():
    """The whole of the pre-existing behaviour, pinned: a build that has not
    finished has nothing past its last step to step onto."""
    following = state(step_count=20)
    for moment in range(5):
        following.next_step(auto_index=19, now=100 + moment)

    assert following.effective_index(19, now=100) == 19


def test_a_tail_adds_exactly_one_reachable_slot():
    following = state(step_count=20)
    following.tail = 1
    for moment in range(5):
        following.next_step(auto_index=19, now=100 + moment)

    assert following.effective_index(19, now=100) == 20


def test_stepping_back_from_the_report_lands_on_the_last_step():
    """The thing the player asked for, in one assertion. The report sits at
    20, so the first press back must show step 19 - the LAST step of the
    build - and not skip it by counting from where the reading was."""
    following = state(step_count=20)
    following.tail = 1
    following.previous_step(auto_index=20, now=100)

    assert following.effective_index(20, now=100) == 19


def test_and_keeps_going_back_from_there():
    following = state(step_count=20)
    following.tail = 1
    following.previous_step(auto_index=20, now=100)
    following.previous_step(auto_index=20, now=101)
    following.previous_step(auto_index=20, now=102)

    assert following.effective_index(20, now=103) == 17


def test_stepping_forward_off_the_last_step_returns_to_the_report():
    """One of the two ways back, and the one that needs no new key."""
    following = state(step_count=20)
    following.tail = 1
    following.previous_step(auto_index=20, now=100)
    following.next_step(auto_index=20, now=101)

    assert following.effective_index(20, now=102) == 20


def test_forward_from_the_report_stays_on_the_report():
    following = state(step_count=20)
    following.tail = 1
    following.next_step(auto_index=20, now=100)
    following.next_step(auto_index=20, now=101)

    assert following.effective_index(20, now=102) == 20


def test_resuming_following_returns_to_the_report():
    """The other way back: the report is what following automatically MEANS
    once the build is done, so the toggle that drops the cursor lands there."""
    following = state(step_count=20)
    following.tail = 1
    following.toggle()                      # off, the way a review starts
    following.previous_step(auto_index=20, now=100)
    following.toggle()                      # back on

    assert following.effective_index(20, now=101) == 20


def test_a_new_match_takes_the_tail_away():
    """Load-bearing rather than tidiness. A slot left over from the last
    game's finished build is a position the player could step onto in the
    middle of this one, to be shown a report for a build still running."""
    following = state(step_count=20)
    following.tail = 1
    following.reset()
    for moment in range(5):
        following.next_step(auto_index=19, now=100 + moment)

    assert following.tail == 0
    assert following.effective_index(19, now=100) == 19


def test_on_tail_knows_where_the_steps_end():
    following = state(step_count=20)

    assert not following.on_tail(19)
    assert following.on_tail(20)
    assert following.on_tail(21)


def test_on_tail_is_false_when_the_build_length_is_unknown():
    """Mid-construction the count can be None, and a panel that decided to
    draw a report from that would be showing one for no build at all."""
    following = follow.FollowState(hold_seconds=10, step_count=None)

    assert not following.on_tail(0)
    assert not following.on_tail(999)


# ---- a review cursor is sticky ---------------------------------------------

def test_a_move_with_following_switched_off_never_expires():
    """What makes reviewing a finished build possible at all.

    The hold exists so the panel cannot drift out of sync with a LIVE build.
    Once the build is done there is nothing to drift from, so the overlay
    switches following off before moving and the cursor stays put - however
    long the player spends reading.
    """
    following = state(step_count=20)
    following.tail = 1
    following.toggle()
    following.previous_step(auto_index=20, now=100)

    assert following.hold_until is None
    assert following.effective_index(20, now=100_000) == 19
    assert following.mode(now=100_000) == follow.MANUAL
