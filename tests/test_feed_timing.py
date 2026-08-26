"""Measuring how long the game leaves a notification line up.

The measurement is only as good as what it refuses to count, and two
refusals do all the work: a text printed a SECOND time while the first is
still up welds into one enormous fake linger, and a line pushed off a full
panel never lived its life at all.
"""

from tools import feed_timing


def timeline(*rows):
    """(game second, texts, lines on the panel) for each look."""
    return [(second, set(texts), bands) for second, texts, bands in rows]


def test_an_unbroken_stretch_is_one_appearance():
    found = feed_timing.appearances(timeline(
        (10, ["--Loom Research Complete--"], 1),
        (14, ["--Loom Research Complete--"], 1),
        (18, ["--Loom Research Complete--"], 1),
        (22, [], 0),
        (26, [], 0)))
    assert len(found) == 1
    text, first, last, _bands = found[0]
    assert (first, last) == (10, 18)


def test_one_missing_look_is_the_reader_blinking():
    # A readable line is read on about 83% of looks, so a single hole is
    # the common case; treating it as the line leaving would halve every
    # measurement.
    found = feed_timing.appearances(timeline(
        (10, ["--Loom Research Complete--"], 1),
        (14, [], 0),
        (18, ["--Loom Research Complete--"], 1)))
    assert len(found) == 1
    assert found[0][1:3] == (10, 18)


def test_two_missing_looks_end_it():
    found = feed_timing.appearances(timeline(
        (10, ["--Loom Research Complete--"], 1),
        (14, [], 0),
        (18, [], 0),
        (22, ["--Loom Research Complete--"], 1)))
    assert len(found) == 2


def test_research_lines_are_the_clean_sample():
    """A technology completes at most once per game, by the rules.

    A unit line can be reprinted while the last copy is still up, and then
    an unbroken stretch is not a duration at all - it measured
    `--Knight Created--` at 231 game seconds, which is thirty knights.
    """
    found = [("--Knight Created--", 10, 240, 5),
             ("--Loom Research Complete--", 10, 21, 1)]
    picked = feed_timing.research_lines(found)
    assert [row[0] for row in picked] == ["--Loom Research Complete--"]
