"""
Loom — the header's pace chip, and what it says once the build is over.

`describe_pace` had no tests at all, which is how this got out: the chip
kept counting villagers into a build that had ended. Live, twenty minutes
after a build finished at +4, the header read

    +55 VILL · —

directly above a report row correctly saying "+4 beyond the build". The two
numbers were on screen together. `77 - 22 = 55` was not a fact about the
build; it was "the player kept playing".

Two separate faults produced that one line, and both are pinned here:

  * the SHAPE. "+N VILL · <verdict>" is a live warning about a slip that is
    happening. After completion there is no build left to be over, so the
    same shape reads as a count still running. It is a total now, and says
    so - and the "· —" tail goes, because a dash where a verdict used to be
    reads as a missing reading rather than as a meter that has retired.
  * the SOURCE. The number is taken from report.max_extra, which is already
    a running max over a window complete() closes. Header and report row
    are then the same number by construction rather than by agreement.

Pure, so no QApplication - the same reasoning as the window-flag tests.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom.overlay import (AHEAD_COLOR, BEHIND_COLOR, FAINT_TEXT,
                          ON_PACE_COLOR, SLIGHTLY_BEHIND_COLOR,
                          describe_pace)


# ---- during the build, which must not have changed ----------------------


def test_on_pace_says_so():
    assert describe_pace(0)[0] == "ON PACE"
    assert describe_pace(0)[1] is ON_PACE_COLOR


def test_ahead_and_behind_read_as_seconds():
    assert describe_pace(-60)[0] == "AHEAD 60s"
    assert describe_pace(-60)[1] is AHEAD_COLOR
    assert describe_pace(120)[0] == "BEHIND 120s"
    assert describe_pace(120)[1] is BEHIND_COLOR


def test_no_reading_is_a_dash_rather_than_a_number():
    """The never-guess rule at the chip: an unknown pace is admitted, never
    drawn as zero."""
    assert describe_pace(None)[0] == "—"
    assert describe_pace(None)[1] is FAINT_TEXT


def test_a_surplus_prefixes_the_verdict_and_turns_it_amber():
    text, color = describe_pace(0, 2)

    assert text == "+2 VILL · ON PACE"
    assert color is SLIGHTLY_BEHIND_COLOR


def test_already_behind_stays_the_louder_fact():
    """Red is not downgraded to amber by a surplus - being behind outranks
    knowing why."""
    assert describe_pace(120, 2)[1] is BEHIND_COLOR


# ---- once the build is over ---------------------------------------------


def test_a_finished_build_reports_a_total_not_a_live_count():
    """The bug, in one assertion. The old shape said "+55 VILL · —" which
    reads as a count that is still running; this says what it is."""
    text, color = describe_pace(None, 4, complete=True)

    assert text == "+4 VILLS > BUILD"
    assert color is SLIGHTLY_BEHIND_COLOR


def test_the_retired_pace_meter_is_not_shown_as_a_missing_reading():
    """"—" means Loom could not read the pace. After completion there is no
    pace to read, which is a different thing, and pairing a frozen total
    with a dash stated both at once."""
    assert "—" not in describe_pace(None, 4, complete=True)[0]
    assert "·" not in describe_pace(None, 4, complete=True)[0]


def test_a_build_finished_without_going_over_says_nothing():
    """The centre slot already carries BUILD DONE. "+0" would be a number
    where there is no story."""
    assert describe_pace(None, 0, complete=True)[0] == ""


def test_completion_outranks_whatever_the_pace_was():
    """The verdict is frozen in the report; the chip must not resurrect a
    stale delta beside the total."""
    for delta in (None, 0, -60, 120):
        assert describe_pace(delta, 4, complete=True)[0] == "+4 VILLS > BUILD"


def test_the_default_is_the_live_shape():
    """browser.py calls describe_pace(delta) with no extra and no complete,
    so the preview must keep the behaviour it had."""
    assert describe_pace(120)[0] == "BEHIND 120s"
    assert describe_pace(120, 0)[0] == "BEHIND 120s"
