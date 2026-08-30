"""
Loom — tests for following one produced item across polls.

Pure logic over hand-built SlotReading streams, the way production.py and
session.py are tested: no game, no frames, and every case is a shape the
corpus actually showed rather than one invented to pass.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import episodes
from loom.queue import SlotReading


def cell(index, progress, identity="villager_male", score=0.8,
         tint="green", count=None):
    return SlotReading(index, tint, progress, count, identity, score)


# How far apart polls are, in GAME seconds. The capture corpus polls every
# 4.0s; the live overlay every 300ms. Tests are written at the corpus
# cadence and derive it from the constant rather than typing a number, so a
# change to the rule cannot leave them passing for the wrong reason.
GAP = episodes.MIN_SECONDS_TO_VOTE


def run(polls):
    """Feed a tracker a list of (when, [slots]) and return every episode.

    `when` is an ORDINAL poll number, scaled to game seconds here. Writing
    the tests in ordinals keeps them about the shape of the evidence, and
    scaling in one place keeps them honest about the cadence.
    """
    tracker = episodes.EpisodeTracker()
    for when, slots in polls:
        tracker.update(None if when is None else when * GAP, slots)
    tracker.flush()
    return tracker.closed


def test_a_sawtooth_is_two_episodes_not_one():
    """The wash climbing and starting over is one item finishing and the
    next beginning - measured on a real run as
    0.62 0.79 0.87 -> 0.23 0.41 0.62."""
    found = run([
        (1, [cell(0, 0.62)]), (2, [cell(0, 0.79)]), (3, [cell(0, 0.87)]),
        (4, [cell(0, 0.23)]), (5, [cell(0, 0.41)]), (6, [cell(0, 0.62)]),
    ])
    assert len(found) == 2
    assert [e.polls for e in found] == [3, 3]
    assert found[0].started == 1 * GAP and found[0].ended == 3 * GAP
    assert found[1].started == 4 * GAP


def test_the_vote_beats_the_first_glance_and_the_last():
    """The whole point. Fifteen polls said mangudai, five said crossbowman,
    one said caravel - and the first glance was the caravel."""
    polls = [(1, [cell(0, 0.05, "caravel", 0.61)])]
    polls += [(t, [cell(0, 0.05 + t * 0.01, "crossbowman", 0.7)])
              for t in range(2, 7)]
    polls += [(t, [cell(0, 0.05 + t * 0.01, "mangudai", 0.75)])
              for t in range(7, 22)]
    found = run(polls)
    assert len(found) == 1
    episode = found[0]
    assert episode.identity == "mangudai"
    assert episode.runner_up == "crossbowman"
    assert set(episode.tally) == {"caravel", "crossbowman", "mangudai"}


def test_an_episode_nobody_watched_for_long_reports_nothing():
    """The never-guess rule. A phantom that flickers for one poll must not
    become a fact - that is what put nine things that never happened on a
    real Post-game page."""
    found = run([(1, [cell(0, 0.4, "trade_cog", 0.9)])])
    assert len(found) == 1
    assert found[0].tally                 # it was seen
    assert found[0].identity is None      # and it is still not an answer


def test_two_buildings_producing_at_once_stay_apart():
    """One item produces in 7,494 polls of the corpus, but two to four in
    about 4,800 and up to twenty-four. Their washes sit at similar
    fractions constantly, which is exactly why position leads."""
    found = run([
        (1, [cell(0, 0.20, "villager_male"), cell(3, 0.22, "knight")]),
        (2, [cell(0, 0.35, "villager_male"), cell(3, 0.38, "knight")]),
        (3, [cell(0, 0.50, "villager_male"), cell(3, 0.55, "knight")]),
    ])
    assert len(found) == 2
    assert {e.identity for e in found} == {"villager_male", "knight"}
    assert all(e.polls == 3 for e in found)


def test_progress_may_not_lead_because_it_is_ambiguous():
    """The first draft matched on progress and let position fall out, and
    it guessed in 23.5% of continuations. Here the two items CROSS in
    progress; a progress-first matcher swaps them and reports two episodes
    that each changed identity halfway."""
    found = run([
        (1, [cell(0, 0.30, "villager_male"), cell(2, 0.32, "knight")]),
        (2, [cell(0, 0.45, "villager_male"), cell(2, 0.44, "knight")]),
        (3, [cell(0, 0.60, "villager_male"), cell(2, 0.58, "knight")]),
    ])
    assert len(found) == 2
    by_name = {e.identity: e for e in found}
    assert set(by_name) == {"villager_male", "knight"}
    # Neither episode ever saw the other's identity.
    assert set(by_name["knight"].tally) == {"knight"}
    assert set(by_name["villager_male"].tally) == {"villager_male"}


def test_the_strip_closing_up_does_not_split_an_episode():
    """A shift of one is 4.3% of transitions, mostly -1: something ahead
    finished and the strip closed up. An episode split by that would vote
    twice and report one item as two."""
    found = run([
        (1, [cell(2, 0.30, "knight")]),
        (2, [cell(1, 0.45, "knight")]),
        (3, [cell(1, 0.60, "knight")]),
    ])
    assert len(found) == 1
    assert found[0].polls == 3
    assert found[0].identity == "knight"


def test_a_jump_further_than_one_slot_is_a_different_item():
    """Beyond one slot the evidence runs out, and pretending otherwise is
    how a tracker stitches two unrelated items into one long episode."""
    found = run([
        (1, [cell(0, 0.30, "knight")]),
        (2, [cell(6, 0.45, "knight")]),
    ])
    assert len(found) == 2
    assert all(e.polls == 1 for e in found)


def test_amber_and_red_are_not_production():
    """A waiting item has produced nothing. Ignoring them is most of why
    this approach is cheap, and all of why it is honest."""
    found = run([
        (1, [cell(0, None, "spearman", tint="amber"),
             cell(1, None, "monk", tint="red")]),
        (2, [cell(0, None, "spearman", tint="amber")]),
    ])
    assert found == []


def test_an_untinted_front_cell_is_an_item_just_placed():
    """production.py is right and classify_tint's docstring is not: at the
    FRONT, untinted means the wash has not started yet. Away from the front
    it is the waiting portrait, and must be ignored."""
    front = run([(1, [cell(0, None, "villager_male", tint=None)]),
                 (2, [cell(0, 0.12, "villager_male")]),
                 (3, [cell(0, 0.28, "villager_male")])])
    assert len(front) == 1 and front[0].polls == 3

    behind = run([(1, [cell(4, None, "villager_male", tint=None)])])
    assert behind == []


def test_flush_keeps_what_was_still_producing():
    """A game ending mid-item is not the item vanishing. It was seen, and
    what became of it afterwards is a different question from whether it
    was there."""
    tracker = episodes.EpisodeTracker()
    for when, progress in ((1, 0.2), (2, 0.4), (3, 0.6)):
        assert tracker.update(when * GAP,
                              [cell(0, progress, "knight")]) == []
    still = tracker.flush()
    assert len(still) == 1
    assert still[0].identity == "knight"
    assert tracker.closed == still


def test_update_returns_only_what_ended_on_that_poll():
    """The caller acts on closures, so a closure reported twice is an item
    recorded twice."""
    tracker = episodes.EpisodeTracker()
    assert tracker.update(1, [cell(0, 0.5, "archer")]) == []
    assert tracker.update(2, [cell(0, 0.7, "archer")]) == []
    ended = tracker.update(3, [])
    assert len(ended) == 1
    assert tracker.update(4, []) == []      # and never again
    assert tracker.closed == ended


def test_the_vote_needs_the_threshold_and_not_a_number_i_typed_here():
    """Pinned as the RULE, against the constant, rather than as whatever
    the constant happens to be today.

    The first version of this test asserted that two polls could not be
    voted on, which was true at a threshold of three and false the moment
    the corpus said two was the knee. That is a test documenting today
    wearing a test's clothes.
    """
    enough = [(t, [cell(0, 0.1 * t, "archer")])
              for t in range(episodes.MIN_POLLS_TO_VOTE + 1)]
    assert run(enough)[0].identity == "archer"

    assert run(enough[:1])[0].identity is None      # one poll, no span


def test_a_poll_with_no_clock_still_counts_as_evidence():
    """The clock band failing does not mean the queue cell was not seen.

    Evidence and timing are different things, and conflating them would
    make an episode vanish because a different reader had a bad frame.
    """
    found = run([
        (None, [cell(0, 0.20, "knight")]),
        (3, [cell(0, 0.35, "knight")]),
        (None, [cell(0, 0.50, "knight")]),
        (5, [cell(0, 0.65, "knight")]),
    ])
    assert len(found) == 1
    episode = found[0]
    assert episode.polls == 4                  # every poll was evidence
    assert episode.started == 3 * GAP          # only those that could say when
    assert episode.ended == 5 * GAP
    assert episode.identity == "knight"


def test_an_episode_seen_only_during_a_clock_gap_has_no_times():
    """Honest about what it does not know, rather than inventing a zero."""
    found = run([(None, [cell(0, 0.2, "archer")]),
                 (None, [cell(0, 0.4, "archer")]),
                 (None, [cell(0, 0.6, "archer")])])
    assert len(found) == 1
    assert found[0].identity == "archer"
    assert found[0].started is None and found[0].ended is None


def test_the_seconds_rule_is_what_survives_a_faster_poll_rate():
    """The half that nearly shipped missing.

    Everything else here was calibrated on capture runs polling every 4.0
    game seconds. The live overlay polls every 300ms - about ten times more
    often - so a poll count alone means four seconds of persistence where it
    was measured and six tenths of a second in a real game, and a phantom
    that flickers twice would pass a threshold measured to stop it.

    So the same evidence at a live cadence must still be refused.
    """
    live = [(t, [cell(0, 0.02 * t, "trade_cog")])
            for t in range(episodes.MIN_POLLS_TO_VOTE + 2)]
    tracker = episodes.EpisodeTracker()
    for ordinal, slots in live:
        tracker.update(ordinal * 0.3, slots)      # 300ms, the live rate
    closed = tracker.flush()
    assert closed[0].polls >= episodes.MIN_POLLS_TO_VOTE
    assert closed[0].identity is None, "a sub-second flicker is not a fact"

    # The same item, watched for long enough, is named.
    slow = run([(t, [cell(0, 0.02 * t, "trade_cog")])
                for t in range(episodes.MIN_POLLS_TO_VOTE + 1)])
    assert slow[0].identity == "trade_cog"
