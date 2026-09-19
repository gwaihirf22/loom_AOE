"""
Loom — tests for the technology timeline's logic.

Pure logic over hand-built Sightings, no window and no game, the way
production.py and episodes.py are tested. The chart that draws these is a
separate question; this is the part that decides what is true.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import events, techtimeline
from loom.queue import TECHNOLOGY, UNIT


def sighting(subject, kind=TECHNOLOGY, first=None, witnesses=(), ordered=()):
    return events.Sighting(subject, kind, first, first, None,
                           set(witnesses), (), tuple(ordered), None)


def test_both_witnesses_naming_it_is_its_own_state():
    """The whole reason for a third colour. Two independent witnesses
    agreeing is a stronger fact than either alone, and nothing anywhere a
    person looks said so before."""
    seen = sighting("wheelbarrow", first=400,
                    witnesses=(events.QUEUE, events.RECORD), ordered=(390,))
    assert techtimeline.state_of(seen) == techtimeline.BOTH


def test_the_feed_counts_as_loom_seeing_it():
    """The queue and the feed are two readers of one SCREEN, so either one
    is Loom having seen it. The record is not a third pair of eyes on the
    same thing - it is the command log, read afterwards."""
    assert techtimeline.state_of(
        sighting("loom", first=120, witnesses=(events.FEED,))) \
        == techtimeline.LOOM_ONLY
    assert techtimeline.state_of(
        sighting("loom", first=120,
                 witnesses=(events.FEED, events.RECORD), ordered=(100,))) \
        == techtimeline.BOTH


def test_ordered_but_never_seen_is_the_state_about_a_reader():
    """The one state that actually convicts something: the game was told
    to research it and Loom never saw it happen."""
    seen = sighting("bow_saw", witnesses=(events.RECORD,), ordered=(800,))
    assert techtimeline.state_of(seen) == techtimeline.RECORD_ONLY


def test_a_word_the_record_does_not_have_is_not_a_disagreement():
    """The distinction this module exists for.

    The record's id tables can only speak about the subjects in
    QUEUE_KNOWN. A technology outside that set is not one the record
    disputes - it is one the record has no word for. Drawing it as a
    conflict would invent one out of a gap in a lookup table, which is the
    same fault as reading a failed match as proof of absence.
    """
    speakable = {"wheelbarrow"}
    rows = [sighting("wheelbarrow", first=400, witnesses=(events.QUEUE,)),
            sighting("sappers", first=900, witnesses=(events.QUEUE,))]
    found = {mark.subject: mark for mark in techtimeline.marks(rows, speakable)}

    # Both are LOOM_ONLY - the state does not change with vocabulary...
    assert found["wheelbarrow"].state == techtimeline.LOOM_ONLY
    assert found["sappers"].state == techtimeline.LOOM_ONLY
    # ...but the chart can still say WHY the record is silent about one.
    assert found["wheelbarrow"].speakable is True
    assert found["sappers"].speakable is False


def test_no_opinion_about_vocabulary_is_not_a_guess():
    """A caller that cannot say which subjects the record can name must
    not have this module guessing for it."""
    rows = [sighting("sappers", first=900, witnesses=(events.QUEUE,))]
    assert techtimeline.marks(rows)[0].speakable is True


def test_loom_time_wins_and_the_record_stands_in():
    """The observed moment is a fact about the game; the ordered moment is
    intent that may never have completed. Same rule the build-order
    timeline already applies."""
    both = sighting("hand_cart", first=650,
                    witnesses=(events.QUEUE, events.RECORD), ordered=(600,))
    assert techtimeline.when_of(both) == 650

    only_ordered = sighting("hand_cart", witnesses=(events.RECORD,),
                            ordered=(900, 600))
    assert techtimeline.when_of(only_ordered) == 600, "earliest order"


def test_a_subject_nobody_can_time_is_dropped_not_drawn_at_zero():
    """A mark at 0:00 is a claim that something happened at the start of
    the game, and nothing here knows that."""
    rows = [sighting("loom", witnesses=(events.QUEUE,))]
    assert techtimeline.when_of(rows[0]) is None
    assert techtimeline.marks(rows) == []


def test_only_technologies_are_drawn():
    rows = [sighting("wheelbarrow", first=400, witnesses=(events.QUEUE,)),
            sighting("knight", kind=UNIT, first=500,
                     witnesses=(events.QUEUE,))]
    assert [mark.subject for mark in techtimeline.marks(rows)] \
        == ["wheelbarrow"]


def test_marks_come_back_in_time_order():
    rows = [sighting("c", first=900, witnesses=(events.QUEUE,)),
            sighting("a", first=100, witnesses=(events.QUEUE,)),
            sighting("b", first=500, witnesses=(events.QUEUE,))]
    assert [mark.subject for mark in techtimeline.marks(rows)] \
        == ["a", "b", "c"]


def test_the_key_shows_every_state_even_at_zero():
    """A row that vanishes when its count is zero makes an absence look
    like a category that does not exist."""
    counted = techtimeline.tally(
        techtimeline.marks([sighting("loom", first=120,
                                     witnesses=(events.QUEUE,))]))
    assert counted == {techtimeline.LOOM_ONLY: 1,
                       techtimeline.BOTH: 0,
                       techtimeline.RECORD_ONLY: 0}
