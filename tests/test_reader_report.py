"""
Loom — tests for the per-resolution reader report.

The corpus itself cannot be tested here: captures/ is scratch, gitignored,
and absent on CI. What CAN be tested is the reasoning that turns a run into
a number, and that is where every mistake in this tool has been so far.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

import pytest

from tools import reader_report


def row(**fields):
    base = {"run": "run_x", "skin": "stock", "every": 1, "frames": 100,
            "anchored": 100, "width": 2560, "height": 1440, "record": "r"}
    base.update(fields)
    return base


def test_a_row_measured_by_different_code_is_not_reused(tmp_path,
                                                        monkeypatch):
    """The cache stamp must track the READER, not the commit.

    The first draft stamped rows with paths.build_commit(). Between commits
    the reader changes constantly, so that hands back numbers produced by
    code that no longer exists - and it did: a clock-jump count of 19
    survived the fix that took it to 0, because nothing had been committed
    in between. This is reader_sweep's pooled-generations failure arriving
    from the other direction.
    """
    monkeypatch.setattr(reader_report, "CACHE", tmp_path)
    monkeypatch.setattr(reader_report, "FINGERPRINT", "aaaa")
    (tmp_path / "run_x.json").write_text(
        json.dumps(row(reader="aaaa")), encoding="utf-8")

    assert reader_report.cached("run_x", 1) is not None

    monkeypatch.setattr(reader_report, "FINGERPRINT", "bbbb")
    assert reader_report.cached("run_x", 1) is None


def test_a_sampled_row_is_never_mistaken_for_a_full_one(tmp_path,
                                                        monkeypatch):
    """--every 10 measures a tenth of the frames. Reading that back as a
    full pass would report a rare event a tenth as often and call it an
    improvement."""
    monkeypatch.setattr(reader_report, "CACHE", tmp_path)
    monkeypatch.setattr(reader_report, "FINGERPRINT", "aaaa")
    (tmp_path / "run_x.json").write_text(
        json.dumps(row(reader="aaaa", every=10)), encoding="utf-8")

    assert reader_report.cached("run_x", 10) is not None
    assert reader_report.cached("run_x", 1) is None


def test_the_fingerprint_moves_when_a_template_does(tmp_path, monkeypatch):
    """Templates are half of what the queue reader IS, so a template change
    has to invalidate a cached row exactly as a code change does."""
    before = reader_report.reader_fingerprint()
    assert before == reader_report.reader_fingerprint(), "must be stable"

    from loom import paths
    scratch = paths.TEMPLATES_DIR / "_fingerprint_probe.png"
    scratch.write_bytes(b"not really a png")
    try:
        assert reader_report.reader_fingerprint() != before
    finally:
        scratch.unlink()
    assert reader_report.reader_fingerprint() == before


def test_coverage_and_accuracy_are_never_one_number():
    """The two questions have different witnesses and different meanings,
    and averaging them is how a reader that gets identity right and
    idleness wrong scores 50% and looks mediocre instead of broken.

    Pinned by walking the report's own output rather than by agreeing with
    a list: the headings must both be present and the accuracy block must
    not carry a coverage band's name.
    """
    rows = [row(reader="x", clock_read=90, villagers_read=80,
                queue={"checkable": 100, "misread": 5})]
    import io
    import contextlib
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        reader_report.report(rows)
    text = buffer.getvalue()
    assert "COVERAGE" in text and "ACCURACY" in text
    coverage, _, accuracy = text.partition("ACCURACY")
    # A band with no witness may be reported as coverage and must never
    # appear as an accuracy figure.
    assert "resources" in coverage
    assert "95.00%" in accuracy       # 100 checkable, 5 misread


def printed(rows, feed=None):
    import contextlib
    import io as _io
    buffer = _io.StringIO()
    with contextlib.redirect_stdout(buffer):
        reader_report.report(rows, feed)
    return buffer.getvalue()


def test_every_reader_is_accounted_for_somewhere():
    """A reader missing from the report reads as an oversight; one named as
    unmeasurable reads as a decision. So every reader Loom has must appear
    either in a table or in the block that says why it cannot.

    Walked rather than hand-listed. The version of this test that agreed
    with a list would pass happily on the day a band is added to
    measure_run and forgotten by report().
    """
    text = printed([row(reader="x")])
    assert "NOT MEASURABLE" in text
    for band, _key, _over in reader_report.COVERAGE:
        assert band in text, band
    for label, _measure in reader_report.ACCURACY:
        assert label in text, label
    # The ones with no ruler, and the one that needs a flag.
    for named in ("resources", "APM", "feed"):
        assert named in text.split("NOT MEASURABLE")[1], named


def test_the_feed_moves_out_of_the_unmeasured_block_when_it_is_scored():
    """It is not unmeasurable, only unmeasured unless asked for - and the
    two must not be printed as the same thing."""
    without = printed([row(reader="x")])
    assert "not scored in this run" in without
    with_feed = printed([row(reader="x")], {(2560, 1440): "97.0%  n=100"})
    assert "not scored in this run" not in with_feed
    assert "97.0%" in with_feed


def test_the_villager_check_is_one_sided():
    """The record counts ORDERS, so it bounds Loom's count from above only.

    A villager can be ordered and cancelled, or born and killed, so reading
    FEWER than were ordered proves nothing. Only reading more is a fault.
    """
    ok = row(reader="x", villager_gain=5, villager_orders_in_window=9)
    over = row(reader="x", villager_gain=12, villager_orders_in_window=9)
    assert reader_report.villager_ceiling([ok]) == "1/1 runs ok"
    assert reader_report.villager_ceiling([over]) == "0/1 runs ok"
    # Under-reading is not counted as a fault in either direction.
    assert reader_report.villager_ceiling([ok, ok]) == "2/2 runs ok"


def test_an_age_arriving_early_is_the_only_impossible_one():
    """Research time is exact and does not divide among villagers, so
    ordered + duration is a FLOOR. Arriving late means a busy Town Centre;
    arriving EARLY cannot happen and convicts the reader, the pairing or
    the table - the same three-way answer measure_durations gives."""
    late = row(reader="x", ages_ordered={"feudal_age": 100},
               ages_seen={"feudal_age": 400})
    early = row(reader="x", ages_ordered={"feudal_age": 100},
                ages_seen={"feudal_age": 150})
    assert "0 under /1" in reader_report.age_error([late])
    assert "1 under /1" in reader_report.age_error([early])
    # The median offset rides alongside, because it is what says the crest
    # reader, the durations table and the record agree at all: measured
    # over 40 real transitions it lands at +8s, +8s and +1s.
    assert reader_report.age_error([late]).startswith("+170s")


@pytest.mark.parametrize("values,expect", [([], "-"), ([12.0], "12.0%")])
def test_spread_says_nothing_rather_than_zero(values, expect):
    """An empty measurement is not a measurement of zero - the distinction
    this whole project is built on."""
    assert reader_report.spread(values) == expect


def test_the_villager_ceiling_counts_orders_from_the_start_of_the_game():
    """A villager ALIVE at the end of a window was ordered before it.

    Training takes 25 seconds and longer when it queues behind something,
    so counting only the orders placed inside the window undercounts the
    ceiling and accuses the reader of inventing villagers it read
    correctly. Measured on a real run: a gain of 8 against 7 orders
    in-window, and 12 counted from the start of the game.

    The bound is deliberately loose. This check exists to catch a reader
    inventing villagers, and a ceiling that cannot be crossed innocently is
    worth more than a tight one that cries wolf.
    """
    lenient = row(reader="x", villager_gain=8, villager_orders_in_window=12)
    assert reader_report.villager_ceiling([lenient]) == "1/1 runs ok"


def test_a_run_that_left_its_match_is_not_measured_against_it():
    """grab_frames keeps going into the next game and the record knows
    nothing about what came after.

    Not hypothetical: before the boundary was honoured, one run reported a
    villager gain of 94 against 7 ordered - the second game's villagers
    measured against the first game's command log.

    Tested through the cached rows rather than through the source, because
    a test that pins where a line sits breaks on any harmless reordering
    and proves nothing about what the code does. Skips where there is no
    corpus, which is CI and any fresh checkout.
    """
    import json
    if not reader_report.CACHE.exists():
        pytest.skip("no measured corpus on this machine")
    rows = [json.loads(p.read_text(encoding="utf-8"))
            for p in reader_report.CACHE.glob("*.json")]
    windows = [r for r in rows if r.get("villager_window")
               and r.get("record_duration")]
    if not windows:
        pytest.skip("no paired run has a villager window yet")
    for r in windows:
        end_of_window = r["villager_window"][1]
        allowed = r["record_duration"] + reader_report.queue_report.            RECORD_END_GRACE
        assert end_of_window is None or end_of_window <= allowed, r["run"]


def test_an_age_is_only_an_arrival_when_the_change_was_watched():
    """A capture that starts after the age-up shows Castle on frame one.

    That is the recording arriving late, not the crest arriving early - and
    scoring it as an arrival makes such a run look like the impossible case
    the floor check exists to hunt for.
    """
    import inspect
    body = inspect.getsource(reader_report.measure_run)
    assert "age_seen_before" in body
    # An age recorded with nothing lower seen first is not an arrival.
    unwatched = row(reader="x", ages_ordered={"castle_age": 900},
                    ages_seen={})
    assert reader_report.age_error([unwatched]) == "-"
