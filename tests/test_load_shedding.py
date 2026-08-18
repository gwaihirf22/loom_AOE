"""
Loom — under load, shed the advisory readers, never the sync signals.

The lag probe measured polls stretching to 0.5-5.5 SECONDS under a 4K game's
CPU load, against a 300ms budget - so the overlay was showing values read
seconds ago, which the player experiences as Loom running behind the game.
The poll's cost is dominated by the advisory readers (queue icons,
notifications, per-resource counts), all of which tolerate gaps by
construction. The villager count and clock are cheap, and they are the point.

Why the gate counts POLLS and not seconds: the first version used wall-time
intervals and shed nothing at all. With polls taking two seconds, a
one-second interval has always expired by the next poll, so every reader ran
every poll anyway - the probe measured it. Counting polls throttles the work
share whatever the cadence is.

The other contract pinned here: on a healthy machine nothing changes at all -
every reader runs every poll, exactly as Loom has always behaved.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import reader


def overloaded_reader():
    hud_reader = reader.HudReader()
    hud_reader._last_poll_cost = 2.0          # the probe measured worse
    return hud_reader


def test_a_healthy_machine_sheds_nothing():
    """Below budget the gate always says run - Linux behaviour unchanged."""
    hud_reader = reader.HudReader()
    hud_reader._last_poll_cost = 0.1
    for _ in range(10):
        hud_reader._poll_number += 1
        assert hud_reader._advisory_due("queue")
        assert hud_reader._advisory_due("notifications")
        assert hud_reader._advisory_due("resources")


def test_an_overloaded_machine_runs_each_reader_every_nth_poll():
    hud_reader = overloaded_reader()

    ran = []
    for poll in range(1, 13):
        hud_reader._poll_number = poll
        if hud_reader._advisory_due("queue"):
            ran.append(poll)

    # Every second poll, from the first ask - slower, never stopped.
    assert ran == [1, 3, 5, 7, 9, 11]


def test_slower_polls_do_not_defeat_the_gate():
    """The failure the first version had: the gate must not care how much
    wall time passes between polls, only how many polls pass."""
    hud_reader = overloaded_reader()
    hud_reader._poll_number = 1
    assert hud_reader._advisory_due("resources") is True
    # However long the next poll takes, it is still only one poll later.
    hud_reader._poll_number = 2
    assert hud_reader._advisory_due("resources") is False


def test_recovery_is_immediate():
    """The moment polls fit the budget again, everything runs every poll."""
    hud_reader = overloaded_reader()
    hud_reader._poll_number = 1
    assert hud_reader._advisory_due("resources") is True
    hud_reader._poll_number = 2
    assert hud_reader._advisory_due("resources") is False

    hud_reader._last_poll_cost = 0.1
    assert hud_reader._advisory_due("resources") is True


def test_each_reader_has_its_own_cadence():
    """One reader running must not reset another's count."""
    hud_reader = overloaded_reader()
    ran = {"queue": [], "notifications": [], "resources": []}
    for poll in range(1, 16):
        hud_reader._poll_number = poll
        for name in ran:
            if hud_reader._advisory_due(name):
                ran[name].append(poll)

    assert ran["queue"] == [1, 3, 5, 7, 9, 11, 13, 15]
    assert ran["notifications"] == [1, 4, 7, 10, 13]
    assert ran["resources"] == [1, 6, 11]


def test_the_budget_sits_between_health_and_pathology():
    """~100ms healthy polls must never trip it; the measured 1-5s ones must."""
    assert 0.15 < reader.POLL_BUDGET < 1.0
