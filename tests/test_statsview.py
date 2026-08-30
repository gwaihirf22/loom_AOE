"""
Loom — tests for the statistics window's pure parts.

The window itself is hand-tested like the rest of the launcher; what earns
automated tests is the file reading (which faces user-visible disk and must
never crash on garbage), the TC-efficiency arithmetic, and the axis-tick
maths behind the graphs.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import os
import pathlib

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from loom import paths, statsview
from loom.gamestats import GameRecorder


@pytest.fixture
def stats_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "STATS_DIR", tmp_path)
    return tmp_path


def write_game(directory, name, duration=300):
    recorder = GameRecorder("test", "Test Build", "2026-07-25T12:00:00")
    for t in range(0, duration + 1, 5):
        recorder.observe(t, 3 + t // 25, -2)
    recorder.write(directory / name)


def test_list_stats_newest_first_with_labels(stats_dir):
    write_game(stats_dir, "2026-07-24_a.json")
    write_game(stats_dir, "2026-07-25_b.json")
    rows = statsview.list_stats()
    assert [p.name for p, _, _ in rows] == ["2026-07-25_b.json",
                                            "2026-07-24_a.json"]
    assert "Test Build" in rows[0][1]
    assert "5:00" in rows[0][1]


def test_corrupt_and_foreign_files_are_listed_as_unreadable(stats_dir):
    (stats_dir / "broken.json").write_text("{ not json")
    (stats_dir / "foreign.json").write_text(json.dumps({"schema": 99}))
    rows = statsview.list_stats()
    assert all(data is None for _, _, data in rows)
    assert all("unreadable" in label for _, label, _ in rows)


def test_empty_or_missing_folder_is_fine(stats_dir):
    assert statsview.list_stats() == []


# ---- managing the history ----------------------------------------------

def test_a_renamed_game_keeps_its_filename_and_the_rest_of_its_file(
        stats_dir):
    """The filename holds the timestamp the list sorts by, so renaming
    must never touch it - and the rewrite must not lose the game."""
    write_game(stats_dir, "2026-08-23_a.json")
    path = stats_dir / "2026-08-23_a.json"
    assert statsview.rename_game(path, "  the one where I got boomed  ")
    assert path.exists(), "renaming moved the file"

    data = statsview.load_stats(path)
    assert data["meta"]["label"] == "the one where I got boomed"
    assert data["meta"]["build_name"] == "Test Build", "the rewrite lost meta"
    assert data["timeline"]["t"], "the rewrite lost the timeline"
    assert "the one where I got boomed" in statsview.list_stats()[0][1]


def test_clearing_a_name_goes_back_to_the_builds_own(stats_dir):
    write_game(stats_dir, "2026-08-23_a.json")
    path = stats_dir / "2026-08-23_a.json"
    statsview.rename_game(path, "temporary")
    assert statsview.rename_game(path, "")
    label = statsview.list_stats()[0][1]
    assert "temporary" not in label
    assert "Test Build" in label


def test_a_deleted_game_leaves_the_history(stats_dir):
    write_game(stats_dir, "2026-08-23_a.json")
    write_game(stats_dir, "2026-08-23_b.json")
    assert statsview.delete_game(stats_dir / "2026-08-23_a.json")
    assert [p.name for p, _, _ in statsview.list_stats()] == [
        "2026-08-23_b.json"]


def test_deleting_what_is_already_gone_is_not_a_failure(stats_dir):
    """The folder is user-visible - they can delete a file by hand between
    the window listing it and them clicking delete."""
    assert statsview.delete_game(stats_dir / "never_existed.json")


def test_the_filter_finds_a_game_by_what_is_in_its_row():
    label = "2026-08-23 07:50 — Scouts rush - 18 pop — 14:19"
    assert statsview.matches_filter(label, "scouts")
    assert statsview.matches_filter(label, "18 pop")
    assert statsview.matches_filter(label, "2026-08-23")
    assert not statsview.matches_filter(label, "arena")


def test_the_rolling_median_smooths_without_inventing_anything():
    times = list(range(0, 60, 5))
    values = [12, 0, 24, 0, 12, 0, 24, 0, 12, 0, 24, 0]
    smooth = statsview.rolling_median(times, values, 4)
    assert len(smooth) == len(values)
    # It fills from the left rather than waiting for a full window, so
    # the line starts where the data starts.
    assert smooth[0] == 12
    assert smooth[1] == 6
    # And the sawtooth is gone by the time the window is full.
    settled = smooth[4:]
    assert max(abs(settled[i] - settled[i - 1])
               for i in range(1, len(settled))) <= 6


def test_the_rolling_median_keeps_gaps_as_gaps():
    """A None must survive, or the drawing code cannot break the line
    where Loom had no reading."""
    smooth = statsview.rolling_median([0, 5, 10, 15], [60, None, 120, 120], 4)
    assert smooth[1] is None
    # The gap did not reset the window: 60 is still in the average.
    assert smooth[2] == 90


def test_the_rolling_median_never_averages_across_a_time_seam():
    """A rewound replay recorded into one file: the end of the first run
    must not be carried into the start of the second."""
    smooth = statsview.rolling_median([100, 105, 5, 10], [600, 600, 12, 12], 4)
    assert smooth[2] == 12, "the seam leaked the old game's APM across"
    assert smooth[3] == 12


def test_an_unread_villager_count_shows_as_a_dash():
    """Absent and unread are different: a game that never recorded
    villagers says nothing, a second Loom could not read says so."""
    unread = statsview.hover_summary({"t": 60, "villagers": None})
    assert "villagers —" in unread
    never = statsview.hover_summary({"t": 60})
    assert "villagers" not in never
    read = statsview.hover_summary({"t": 60, "villagers": 14})
    assert "14 villagers" in read


def test_tc_efficiency():
    assert statsview.tc_efficiency(
        {"duration": 1000, "tc_count": 2, "tc_idle_seconds": 200}) == 0.9
    # No TCs or no duration: no honest answer.
    assert statsview.tc_efficiency({"duration": 0, "tc_count": 1}) is None
    # Overcounted idleness clamps rather than going negative.
    assert statsview.tc_efficiency(
        {"duration": 10, "tc_count": 1, "tc_idle_seconds": 100}) == 0.0


def test_game_rows_carry_the_honesty_notes():
    game = {"duration": 600, "max_villagers": 30, "tc_count": 2,
            "tc_idle_seconds": 12.0, "queued": {"knight": 500},
            "deaths": [[300, 2, True]], "attacks": [295]}
    rows = statsview.game_rows(game)
    values = dict((label, value) for label, value, _ in rows)
    assert values["villagers lost"] == "2 (2 to raids)"
    # A queue sighting is NOT here any more. It is a thing the recorded
    # game can be asked about, so it belongs in the inventory where the
    # answer sits beside it - listed here as well, it was the same
    # sighting twice on one tab with only one of the pair saying who saw
    # it.
    assert "knight" not in {label for label, _, _ in rows}
    units = dict(statsview.inventory_rows({"game": game}))["Units"]
    assert ("knight", "queue 8:20", None) in units, units


def test_the_build_window_idle_row_leads_the_whole_game_one():
    """My ruling: idleness during the build is villagers the build order
    asked for and never got, and it compounds for the rest of the match.
    The whole-game total is context, so it comes second."""
    rows = statsview.game_rows(
        {"duration": 2400, "tc_count": 3, "tc_idle_seconds": 900.0},
        {"tc_idle_seconds": 12.0})
    labels = [label for label, _, _ in rows]
    assert (labels.index("TC idle time (during the build)")
            < labels.index("TC idle time (whole game)"))
    values = dict((label, value) for label, value, _ in rows)
    # The unit travels with the number: 900 is TC-seconds, not wall time,
    # and printed bare it reads as impossible in a 40-minute game.
    assert values["TC idle time (whole game)"] == "900s of TC time"
    assert values["TC idle time (during the build)"] == "12s of TC time"


def test_an_unfinished_build_says_nothing_about_its_idle_time():
    rows = statsview.game_rows({"duration": 600, "tc_count": 1})
    assert all("during the build" not in label for label, _, _ in rows)


# ---- the charts, actually painted -------------------------------------

@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def painted(view):
    """Render a chart view offscreen, so a drawing fault is a failure.

    Every other test here works on the pure parts, which is why the graphs
    could grow a line slashed across the whole plot and a series drawn up
    into its neighbour's frame without any test noticing. A paint that
    raises now fails here rather than in the launcher.
    """
    from PyQt6.QtGui import QPixmap
    view.resize(600, 500)
    pixmap = QPixmap(view.size())
    view.render(pixmap)
    return pixmap


def game_with_ages():
    recorder = GameRecorder("test", "Test Build", "2026-08-23T12:00:00")
    for t in range(0, 901, 5):
        idle = 3 if 400 <= t < 460 else 0
        recorder.observe(t, 3 + t // 25, -2,
                         tracker=FakeTracker(idle_tcs=idle, tcs_seen=3))
    recorder.ages = [(600, "clicked", 1), (740, "reached", 2)]
    return recorder.to_dict()


class FakeTracker:
    def __init__(self, idle_tcs=0, tcs_seen=1):
        self.idle_tcs = idle_tcs
        self.tcs_seen = tcs_seen
        self.blocked = None


def test_every_registered_chart_paints_with_the_ages_marked(app):
    """The age rules cross every chart, so a fault in them breaks all of
    them at once - and CHARTS maps a name to a method by STRING, which
    Python cannot check until the moment it paints.

    This test used to hand-list the tabs, and drifted: "military" was
    never on the list, so when _draw_military was deleted in a refactor
    nothing noticed until the Military tab was opened in the launcher
    and the paint raised. The list has to come from the registry, or it
    is a hand-curated allowlist failing silently all over again.
    """
    data = game_with_ages()
    
    for name in statsview.ChartView.CHARTS:
        view = statsview.ChartView((name,))
        view.show_game(data)
        assert painted(view) is not None, name


def test_a_game_with_no_ages_still_paints(app):
    """Files written before the age reader existed have no ages key."""
    view = statsview.ChartView(("villagers", "idle_tcs"))
    data = game_with_ages()
    data["game"].pop("ages")
    view.show_game(data)
    assert painted(view) is not None


def test_only_the_arrival_is_drawn_and_it_wears_the_crest(app):
    """The author's ruling: the click does not matter enough to clutter
    every chart, and it is no longer recorded. Files written before that
    still carry clicks, so styling one must return None rather than
    raise - a paint that raises aborts the process."""
    assert statsview.age_rule_style("reached")[3] is True
    assert statsview.age_rule_style("clicked") is None
    assert statsview.age_rule_style("something_later") is None


def test_a_recorder_keeps_arrivals_and_drops_clicks():
    """The click was logged every poll the red bar was seen, not once per
    transition - four entries for one age-up, four rules on the graph."""
    recorder = GameRecorder("test", "Test Build", "2026-08-23T12:00:00")
    for t, events in ((100, [("clicked", 2)]), (105, [("clicked", 2)]),
                      (110, [("clicked", 2)]), (200, [("reached", 2)])):
        recorder.observe(t, 20, 0, age_events=events)
    assert recorder.ages == [(200, "reached", 2)]


def test_the_window_zooms_on_time_and_never_leaves_the_game(app):
    """Zoom is horizontal only, clamped inside the recorded clock, and
    floored so the span can never collapse to nothing and divide by it."""
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    full = view.full_span()
    assert view.window() == (0.0, full), "a new game opens fitted"

    view.zoom_by(4, anchor=full / 2)
    low, high = view.window()
    assert high - low == pytest.approx(full / 4)
    assert low >= 0 and high <= full, "the window left the game"

    # Zooming past the floor stops at it rather than collapsing.
    view.zoom_by(10_000)
    low, high = view.window()
    assert high - low == pytest.approx(statsview.MIN_WINDOW_SECONDS)

    # Scrolling past the end is pulled back rather than showing blank.
    view.set_window(left=full * 10)
    low, high = view.window()
    assert high == pytest.approx(full)

    view.fit()
    assert view.window() == (0.0, full), "fit is one action, always"


def test_a_new_game_is_never_opened_still_zoomed_in(app):
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    view.zoom_by(8)
    view.show_game(game_with_ages())
    assert view.window() == (0.0, view.full_span())


def test_the_readout_reads_the_sample_not_the_pixel(app):
    """Values are the nearest RECORDED second. A number interpolated
    between two readings was never read, and Loom does not invent one."""
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    values = view.values_at(302.5)
    assert values["t"] in (300, 305)
    assert values["villagers"] is not None


def test_every_age_has_crest_art_of_some_kind():
    """The icon library is optional and gitignored, so the reader's own
    templates have to be able to stand in - a fresh clone still draws
    crests, just uglier ones. This fails if BOTH sources go missing."""
    from loom.age import CASTLE, DARK, FEUDAL, IMPERIAL
    for which in (DARK, FEUDAL, CASTLE, IMPERIAL):
        assert statsview.crest_source(which) is not None, which


def test_every_coming_soon_tab_says_what_blocks_it(app, stats_dir):
    """And that the list is only tabs that ARE coming soon.

    This drifted once: Economy, Technology and Military stayed here long
    after they were built and filled, so the source told anyone reading it
    that Loom could not do things it had been doing for weeks. Nothing
    displayed them, so nothing contradicted them - the same shape as a
    chart registered by string whose method had been deleted."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    titles = {window.tabs.tabText(i) for i in range(window.tabs.count())}

    for name in statsview.COMING_SOON:
        html = statsview.coming_soon_html(name)
        assert "Not yet." in html
        assert len(html) > 200, name
        assert name in titles, f"{name} is promised but is not a tab"

    # A partial tab EXISTS - it is not promised, it is unfinished - so it
    # must not be in the coming-soon list at all.
    for name in statsview.PARTIAL:
        assert name in titles, f"{name} carries a note and is not a tab"
        assert name not in statsview.COMING_SOON,             f"{name} is both a real tab and promised as one"

    # ...and no tab still showing the promise has been left off the list.
    for index in range(window.tabs.count()):
        page = window.tabs.widget(index)
        title = window.tabs.tabText(index)
        text = " ".join(child.text() for child in page.findChildren(
            type(window.build_tab["label"])))
        if "Not yet." in text:
            assert title in statsview.COMING_SOON,                 f"{title} says 'Not yet' and is not in COMING_SOON"


def test_nice_ticks_are_round_and_cover_the_range():
    ticks = statsview.nice_ticks(0, 47)
    assert ticks[0] == 0
    assert ticks[-1] >= 40
    assert all(t == round(t, 10) for t in ticks)
    assert statsview.nice_ticks(5, 5) == [5]


def test_demo_files_are_not_history(stats_dir):
    """Demo replays write a stats file so the pipeline can be exercised with
    no game - but they are rehearsals, and a row per demo run would bury the
    real games. The author's folder once held one phantom game per minute of
    a forgotten demo."""
    write_game(stats_dir, "2026-07-25_real.json")
    write_game(stats_dir, "2026-07-25_120000_demo.json")

    rows = statsview.list_stats()

    assert [p.name for p, _, _ in rows] == ["2026-07-25_real.json"]


def test_age_spans_credit_the_stretch_before_the_first_arrival():
    """An entry says which age was REACHED, so everything before the
    first one belongs to the age below it - reaching Feudal at 7:06 means
    the game was in the Dark Age until 7:06."""
    from loom.age import CASTLE, DARK, FEUDAL
    spans = statsview.age_spans(
        [[426, "reached", FEUDAL], [1000, "reached", CASTLE]], 1800)
    assert spans == [(DARK, 0, 426), (FEUDAL, 426, 1000), (CASTLE, 1000, 1800)]


def test_age_spans_are_empty_when_no_age_was_ever_read():
    """Loom cannot label a stretch it never identified."""
    assert statsview.age_spans([], 1800) == []
    assert statsview.age_spans([[400, "clicked", 2]], 1800) == []


def test_a_span_average_is_a_mean_of_what_falls_inside_it():
    times = [0, 10, 20, 30, 40]
    values = [100, 200, 300, 400, 500]
    assert statsview.span_average(times, values, 10, 30) == 250
    # Half-open: the end moment belongs to the next span, not this one.
    assert statsview.span_average(times, values, 0, 10) == 100
    # Nothing inside is not zero - it is no answer.
    assert statsview.span_average(times, values, 100, 200) is None


def test_a_tab_only_reports_the_series_it_draws():
    """The APM tab has no business quoting a villager count nobody can
    see on it - it invites the reader to hunt for a line that is not
    there."""
    values = {"t": 300, "villagers": 20, "pace": 15, "idle_tcs": 2,
              "apm": 140}
    apm_only = statsview.hover_summary(values, ("apm",))
    assert "140 APM" in apm_only
    assert "villagers" not in apm_only and "behind" not in apm_only
    # No keys at all still means everything, which is what a test wants
    # and no tab does.
    assert "villagers" in statsview.hover_summary(values)


def test_shift_and_the_wheel_pans_instead_of_zooming(app):
    from PyQt6.QtCore import QPoint, QPointF, Qt as QtCore_Qt
    from PyQt6.QtGui import QWheelEvent

    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    view.resize(600, 400)
    view.zoom_by(4)
    before = view.window()

    def wheel(modifier):
        return QWheelEvent(
            QPointF(300, 200), QPointF(300, 200), QPoint(0, 0),
            QPoint(0, 120), QtCore_Qt.MouseButton.NoButton, modifier,
            QtCore_Qt.ScrollPhase.NoScrollPhase, False)

    view.wheelEvent(wheel(QtCore_Qt.KeyboardModifier.ShiftModifier))
    panned = view.window()
    assert panned[1] - panned[0] == pytest.approx(before[1] - before[0]), \
        "shift+wheel changed the zoom instead of panning"
    assert panned[0] < before[0], "shift+wheel did not move back in time"

    view.wheelEvent(wheel(QtCore_Qt.KeyboardModifier.NoModifier))
    zoomed = view.window()
    assert zoomed[1] - zoomed[0] < panned[1] - panned[0], \
        "a bare wheel should still zoom"


# ---- what the build said, against what happened -------------------------

class FakeStep:
    def __init__(self, time, segments, age=1):
        self.time = time
        self.age = age
        self.items_segments = segments


def plan_for(monkeypatch, steps, game, record=None):
    from loom import build_order
    fake = type("B", (), {"steps": steps})()
    monkeypatch.setattr(build_order.BuildOrder, "load_by_name",
                        classmethod(lambda cls, name: fake))
    return statsview.plan_versus_actual("whatever", game, record)


def item(token, words="Build "):
    return [("text", words), ("icon", token)]


def test_a_repeated_commodity_takes_the_next_sighting_not_the_first(
        monkeypatch):
    """A build names "house" on three steps. Crediting each with the
    first house ever seen reported the third 580 seconds early - a global
    ledger over a commodity lying, exactly as CLAUDE.md says it will."""
    steps = [FakeStep(50, [item("building_economy/house")]),
             FakeStep(175, [item("building_economy/house")]),
             FakeStep(700, [item("building_economy/house")])]
    game = {"events": [[60, "built:house"], [200, "built:house"],
                       [900, "built:house"]]}
    rows = plan_for(monkeypatch, steps, game)
    assert [row.observed for row in rows] == [60, 200, 900]


def test_an_item_is_done_when_its_LAST_part_is(monkeypatch):
    steps = [FakeStep(100, [[("text", "Build 2 "),
                            ("icon", "building_economy/house")]])]
    game = {"events": [[60, "built:house"], [300, "built:house"]]}
    rows = plan_for(monkeypatch, steps, game)
    assert rows[0].observed == 300, "an item of two was done on the first"
    assert "2" in rows[0].name, "the count is not on the label"


def test_a_technology_named_twice_gets_one_row(monkeypatch):
    """A later step naming an age again is a heading - "In Feudal Age:" -
    not a second research, and must not draw a row claiming it never
    arrived."""
    steps = [FakeStep(300, [item("age/feudal_age", "Research ")]),
             FakeStep(600, [item("age/feudal_age", "In ")])]
    game = {"ages": [[400, "reached", 2]]}
    rows = plan_for(monkeypatch, steps, game)
    assert len(rows) == 1 and rows[0].observed == 400


def test_an_age_is_answered_by_the_crest_and_nothing_else(monkeypatch):
    """The house rule: age completions come from the CREST only. The
    queue sees the age-up the moment it is CLICKED, which is why Feudal
    kept reporting minutes before it landed, and the feed's age line is
    long, wraps, and often never reads at all."""
    steps = [FakeStep(300, [item("age/feudal_age", "Research ")])]
    clicked_only = plan_for(monkeypatch, steps,
                            {"queued": {"feudal_age": 310},
                             "events": [[420, "researched:feudal_age"]]})
    assert clicked_only[0].observed is None, "a click answered for an arrival"

    with_crest = plan_for(monkeypatch, steps,
                          {"queued": {"feudal_age": 310},
                           "ages": [[500, "reached", 2]]})
    assert with_crest[0].observed == 500


def test_an_item_never_seen_says_so_rather_than_guessing(monkeypatch):
    steps = [FakeStep(100, [item("building_economy/mill")])]
    rows = plan_for(monkeypatch, steps, {"events": []})
    assert rows[0].observed is None


def test_a_missing_build_file_is_no_rows_not_a_crash(monkeypatch):
    assert statsview.plan_versus_actual("no_such_build", {}) == []


def test_the_plan_packs_into_lanes_rather_than_a_row_each(app):
    """A row per item made the chart unreadable whenever it was short.
    Items that do not overlap in time share a lane."""
    view = statsview.ChartView(("plan",))
    view.plan = [statsview.PlanRow("early", None, 10, 20, (0, 60)),
                 statsview.PlanRow("late", None, 500, 520, (490, 560)),
                 statsview.PlanRow("clash", None, 12, 30, (0, 60))]
    view.span, view.left, view._full = None, 0.0, 600
    plot = (50, 60, 800, 200)
    lanes = {round(y) for _, _, _, y in view._plan_layout(plot)}
    assert len(lanes) == 2, "the two that cannot clash should share a lane"


def test_the_pointer_picks_out_one_item_to_bring_forward(app):
    view = statsview.ChartView(("plan",))
    view.plan = [statsview.PlanRow("a thing", None, 100, 200, (85, 260))]
    view.span, view.left, view._full = None, 0.0, 600
    plot = (50, 60, 800, 200)
    marks = view._plan_layout(plot)
    _, said, done, y = marks[0]
    view.hover_x, view.hover_y = done, y
    assert view._plan_under_pointer(marks) is marks[0]
    view.hover_x, view.hover_y = done, y + 40
    assert view._plan_under_pointer(marks) is None
    view.hover_x = None
    assert view._plan_under_pointer(marks) is None


def test_the_picture_is_of_the_thing_being_counted(monkeypatch):
    """"2 villagers to the lumber camp" leads with a villager token,
    which item_evidence rightly ignores - and taking the first icon drew
    villagers where lumber camps and houses belonged."""
    steps = [FakeStep(100, [[("text", "Move 2 "),
                             ("icon", "resource/MaleVillDE.webp"),
                             ("text", " to build a "),
                             ("icon", "lumber_camp/Lumber_camp_aoe2de.webp")]])]
    rows = plan_for(monkeypatch, steps, {"events": [[150, "built:lumber_camp"]]})
    assert rows[0].token == "lumber_camp/Lumber_camp_aoe2de.webp"
    assert "villager" not in rows[0].name


def test_an_item_under_the_pointer_silences_the_general_readout(app):
    """One pointer, one answer: the item's own label already says the
    time, and a second box printed over it is what the author saw."""
    from PyQt6.QtGui import QPixmap
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("a thing", None, 100, 200, (85, 260))]
    view.resize(900, 300)
    canvas = QPixmap(view.size())

    view.render(canvas)                     # learn where the mark landed
    assert view._plan_marks, "the plan chart drew nothing to point at"
    assert view._pointer_text is None, "claimed the pointer with none on it"

    _, _, done, y = view._plan_marks[0]
    view.hover_x, view.hover_y = done, y
    view.render(canvas)
    assert view._pointer_text, "the plan chart did not claim the pointer"
    assert "a thing" in view._pointer_text


def test_a_card_is_when_the_instruction_appears_not_a_deadline():
    """The author's ruling. A card at 2:05 with the next at 2:55 does not
    demand a lumber camp AT 2:05 - it has to be walked to and built, the
    card can be superseded while the work is in flight, and the build's
    own pacing already says how long that was expected to take."""
    start, end = statsview.expected_window(125, 175)
    assert start == 125 - statsview.EARLY_GRACE
    assert end == 175 + statsview.LATE_TAIL
    # The last card has nothing after it to bound the window.
    start, end = statsview.expected_window(600, None)
    assert end == 600 + statsview.LAST_CARD_TAIL + statsview.LATE_TAIL
    # A build whose next card comes BEFORE this one never closes the
    # window early - the item still needs its time.
    _, end = statsview.expected_window(600, 300)
    assert end == 600 + statsview.REACTION + statsview.LATE_TAIL


def test_the_window_waits_for_the_thing_to_be_BUILT():
    """A card says start; what Loom sees is a finish. Both ends of the
    window move by the build or research time, so a slow thing is not
    late for being slow - and a prompt player is not early either."""
    plain = statsview.expected_window(100, 200)
    slow = statsview.expected_window(100, 200, duration=130)
    assert slow[0] == plain[0] + 130, "the window did not wait to start"
    assert slow[1] > plain[1], "the window did not wait to close"


def test_the_verdict_and_the_slip_agree_with_the_window():
    row = statsview.PlanRow("mill", None, 275, None, (260, 405))
    assert statsview.plan_verdict(row) is None
    assert statsview.plan_slip(row) is None

    inside = row._replace(observed=311)
    assert statsview.plan_verdict(inside) == "on time"
    assert statsview.plan_slip(inside) == 0, "on time must not report a slip"

    late = row._replace(observed=500)
    assert statsview.plan_verdict(late) == "late"
    assert statsview.plan_slip(late) == 95, "the slip is past the WINDOW"

    early = row._replace(observed=200)
    assert statsview.plan_verdict(early) == "early"
    assert statsview.plan_slip(early) == -60


def test_an_age_the_player_reached_late_moves_everything_in_it(monkeypatch):
    """Half a build cannot be attempted before its age exists. A Stable
    is a Feudal thing, and calling it late against a Feudal that arrived
    three minutes after the build wanted it blames the player for the
    same delay twice."""
    steps = [FakeStep(375, [item("age/feudal_age", "Research ")], age=1),
             FakeStep(605, [item("stable/Stable_aoe2DE.webp")], age=2)]
    game = {"ages": [[562, "reached", 2]],
            "events": [[649, "built:stable"]]}
    rows = plan_for(monkeypatch, steps, game)
    stable = rows[-1]
    assert statsview.plan_verdict(stable) == "on time", (
        "the stable was judged against a Feudal that had not happened yet")
    # Reaching an age EARLY must not drag the expectation earlier: the
    # shift only ever delays, exactly as the overlay cursor's does.
    early = plan_for(monkeypatch, steps,
                     {"ages": [[300, "reached", 2]],
                      "events": [[649, "built:stable"]]})
    from loom.durations import build_or_research_time
    assert early[-1].expected == statsview.expected_window(
        605, None, 0, build_or_research_time("stable"))


def test_the_combined_frame_draws_every_mix_of_its_layers(app):
    """A paint that raises does not fail politely - PyQt cannot carry a
    Python exception out through C++, so the process aborts with no
    traceback. That is exactly what a missing layer did here, and the
    suite stayed green because nothing rendered a combined view. Calling
    the draw directly is what surfaces it as a failure."""
    from PyQt6.QtGui import QPainter, QPixmap
    view = statsview.ChartView(("plan", "pace"), combined="build and pace")
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260))]
    view.resize(880, 300)
    canvas = QPixmap(view.size())
    for enabled in (["plan", "pace"], ["plan"], ["pace"], []):
        view.enabled = enabled
        painter = QPainter(canvas)
        try:
            view._draw_combined(painter)     # raises rather than aborting
        finally:
            painter.end()


def test_a_layer_sharing_a_frame_does_not_draw_its_own_key(app):
    """Three layers each drawing a key would stack three keys on top of
    each other. The frame's owner draws it once."""
    from PyQt6.QtGui import QPainter, QPixmap
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    view.resize(880, 300)
    # The pixmap must OUTLIVE the painter: painting into a temporary
    # takes the process down rather than raising.
    canvas = QPixmap(view.size())
    painter = QPainter(canvas)
    try:
        view._layer_villagers(painter, (50, 60, 700, 200))
    finally:
        painter.end()


def test_our_own_padding_never_makes_prompt_play_read_early():
    """The author's rule, and he was right where I said it was
    unnecessary. The window is pushed later by two paddings of OURS - the
    thing's build time, and however late the player was into its age - so
    without this guard an archery range finished eighty seconds after
    Feudal read "early by fifty seconds". Early must mean ahead of what
    the BUILD asked, never ahead of our estimate of it."""
    row = statsview.PlanRow("archery range", None, 605, 647, (698, 793))
    assert statsview.plan_verdict(row) == "on time"
    assert statsview.plan_slip(row) == 0

    # Genuinely ahead of the card is still early.
    genuine = row._replace(observed=300)
    assert statsview.plan_verdict(genuine) == "early"
    assert statsview.plan_slip(genuine) == 300 - 605


# ---- the recorded game, attached to a stats file -------------------------

class StoredTruth:
    duration = 2160
    builds = {"lumber_camp": [(90, 0, 0), (95, 0, 0)]}
    researches = {"loom": [200]}
    queued = {"villager": 40}

    def name_of_building(self, i):
        return i

    def name_of_tech(self, i):
        return i

    def name_of_unit(self, i):
        return i

    def ages(self):
        return {"feudal_age": 629}


def test_an_older_stats_file_still_opens_after_the_schema_moved(stats_dir):
    """The gate tested `schema != SCHEMA`, so bumping the version alone
    would have made every file already on disk unreadable in one commit."""
    write_game(stats_dir, "2026-08-23_old.json")
    path = stats_dir / "2026-08-23_old.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    assert statsview.load_stats(path) is not None
    assert all(d is not None for _, _, d in statsview.list_stats())


def test_the_record_lands_beside_what_loom_read_never_over_it(stats_dir,
                                                              monkeypatch):
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    before = json.loads(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(statsview.replay, "match",
                        lambda p, available=None: statsview.replay.Match(
                            type("R", (), {"path": pathlib.Path("r.aoe2record")}),
                            statsview.replay.CERTAIN, "one", None))
    monkeypatch.setattr(statsview.replay, "harvest", lambda p: StoredTruth())

    assert statsview.enrich_with_record(path).startswith("added")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["game"] == before["game"], "the read section was rewritten"
    assert after["timeline"] == before["timeline"]
    assert after["record"]["duration"] == 2160
    # Undoable by deleting one key, and never done twice.
    assert "already" in statsview.enrich_with_record(path)


def test_the_accuracy_report_works_from_the_file_alone(stats_dir, monkeypatch):
    """Once attached, the comparison must survive the .aoe2record being
    deleted, moved, or left on another machine."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    monkeypatch.setattr(statsview.replay, "match",
                        lambda p, available=None: statsview.replay.Match(
                            type("R", (), {"path": pathlib.Path("r.aoe2record")}),
                            statsview.replay.CERTAIN, "one", None))
    monkeypatch.setattr(statsview.replay, "harvest", lambda p: StoredTruth())
    statsview.enrich_with_record(path)

    data = statsview.load_stats(path)
    rows = statsview.accuracy_rows(data)
    assert [d.subject for d in rows["unread"]] == ["lumber_camp", "loom"] or         {d.subject for d in rows["unread"]} == {"lumber_camp", "loom"}
    assert rows["overfired"] == [], "nothing was read, so nothing over-fired"
    assert "ordered" in statsview.accuracy_html(data)


def test_a_game_with_no_record_says_so_rather_than_showing_nothing():
    html = statsview.accuracy_html({"schema": 2, "game": {}})
    assert "Not compared yet" in html
    assert "cannot happen" in html, "the two lists are not explained"


def test_every_tab_of_the_window_paints_with_a_game_selected(app, stats_dir):
    """The Military tab crashed on open because a chart was registered by
    STRING and its method had been deleted in a refactor - no import error,
    no failing test, until a human clicked it. The chart registry has its
    own guard now; this is the same guard one level up, over the tabs
    themselves, because a tab is added by hand and nothing else walks them.
    """
    from PyQt6.QtGui import QPixmap
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.resize(900, 600)
    window.refresh()
    window.games.setCurrentRow(0)
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        canvas = QPixmap(window.size())
        window.render(canvas)          # a paint that raises aborts the process
    assert window.tabs.count() >= 9


def test_a_misread_clock_is_marked_and_never_mended(stats_dir):
    """The ruling, and it is the same one that keeps the record from
    overwriting a reading: a repaired corpus destroys the read-versus-truth
    gap, which is the only thing that says a reader needs fixing. It is
    also how 34 broken files were traced to one 24-minute window."""
    write_game(stats_dir, "2026-08-19_202441_g.json")
    path = stats_dir / "2026-08-19_202441_g.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    before = list(data["timeline"]["t"])
    data["timeline"]["t"] = before[:3] + [36060]        # the real shape
    data["game"]["duration"] = 36060
    path.write_text(json.dumps(data), encoding="utf-8")

    loaded = statsview.load_stats(path)
    faults = statsview.clock_faults(loaded)
    assert faults, "a ten-hour game raised nothing"
    assert any("jumped" in f for f in faults)
    assert any("hours long" in f for f in faults)

    # Marked where it is chosen...
    assert any("clock" in label for _, label, _ in statsview.list_stats())
    # ...and explained where its numbers are read.
    assert "not repaired" in statsview.clock_warning_html(loaded)
    # ...and NOT changed.
    assert json.loads(path.read_text(encoding="utf-8"))["game"]["duration"]         == 36060, "the file was repaired behind the author's back"


def test_a_sound_clock_says_nothing_at_all(stats_dir):
    write_game(stats_dir, "2026-08-23_fine.json")
    data = statsview.load_stats(stats_dir / "2026-08-23_fine.json")
    assert statsview.clock_faults(data) == []
    assert statsview.clock_warning_html(data) == ""
    assert all("clock" not in label for _, label, _ in statsview.list_stats())


def test_choosing_the_game_you_are_playing_explains_why_not(stats_dir):
    """The file picker is the one place a person can point Loom at a match
    in progress, so it is the one place the boundary has to be explained
    rather than reported as an error code."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    live = stats_dir / "rec.aoe2record"
    live.write_bytes(b"a match in progress")

    said = statsview.enrich_with_record(
        stats_dir / "2026-08-25_010714_g.json", live)
    assert "right now" in said and "after it has ended" in said
    assert "Error" not in said, "the boundary was reported as a fault"
    # ...and nothing was written.
    assert "record" not in statsview.load_stats(
        stats_dir / "2026-08-25_010714_g.json")


# ---- two witnesses on one chart ------------------------------------------

def test_a_chart_the_record_cannot_answer_offers_no_control_for_it(app):
    """The rule that matters more than the colours. The recorded game is a
    command log - it has no villager count, no pace and no idle Town
    Centres and never will. A checkbox that draws nothing is worse than
    none: the reader ticks it, sees no line, and concludes the record says
    zero rather than that it was never asked."""
    assert statsview.ChartTab(("idle_tcs",)).witness_boxes == {}
    assert statsview.ChartTab(("pace",)).witness_boxes == {}
    for chart in ("apm", "plan"):
        offered = set(statsview.ChartTab((chart,)).witness_boxes)
        assert offered == {statsview.SCREEN, statsview.FROM_RECORD}, chart
    # villagers serves both too, but its screen witness is a group label
    # over three part boxes rather than a checkbox of its own.
    society = statsview.ChartTab(("villagers",))
    assert set(society.witness_boxes) == {statsview.FROM_RECORD}
    assert set(society.part_boxes) == {"villagers", "population", "cap"}


def test_the_witnesses_a_chart_can_serve_are_declared_not_guessed():
    """Anything not declared is screen-only, which is the truthful
    default rather than a convenient one."""
    assert statsview.ChartView.witnesses_for("apm") == (
        statsview.SCREEN, statsview.FROM_RECORD)
    # An idle Town Centre is a thing Loom INFERS from the queue. The
    # record has no idea a Town Centre was idle and never will.
    assert statsview.ChartView.witnesses_for("idle_tcs") == (
        statsview.SCREEN,)
    assert statsview.ChartView.witnesses_for("nothing_like_this") == (
        statsview.SCREEN,)


def test_every_mix_of_witnesses_paints(app, stats_dir):
    """Including none at all, which is a reachable state - both boxes
    unticked - and must be an empty frame rather than a crash."""
    from PyQt6.QtGui import QPixmap
    data = game_with_ages()
    data["apm"] = {"t": [0, 5, 10], "apm": [60, 120, 90],
                   "keys_total": 10, "clicks_total": 5, "bucket_seconds": 5}
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "apm": {"t": [0, 5, 10], "apm": [24, 36, 24],
                              "commands_total": 7, "bucket_seconds": 5}}
    view = statsview.ChartView(("apm",))
    view.show_game(data)
    view.resize(700, 400)
    for mix in ([statsview.SCREEN, statsview.FROM_RECORD], [statsview.SCREEN],
                [statsview.FROM_RECORD], []):
        view.witnesses = mix
        canvas = QPixmap(view.size())
        view.render(canvas)


def test_no_record_means_no_record_line_rather_than_a_line_at_zero(app):
    """Absent and zero must not look alike: a game nobody commanded and a
    game nobody asked about are different answers."""
    data = game_with_ages()
    data["apm"] = {"t": [0, 5], "apm": [60, 60], "keys_total": 5,
                   "clicks_total": 0, "bucket_seconds": 5}
    view = statsview.ChartView(("apm",))
    view.show_game(data)
    assert view._record_apm() is None
    # ...and an attached record whose body stopped early is also None.
    data["record"] = {"path": "r.aoe2record", "duration": 900, "apm": None}
    view.show_game(data)
    assert view._record_apm() is None


# ---- what the record says about the match itself -------------------------

def a_header(**over):
    header = {"map": "Arabia", "diplomacy": "1v1", "difficulty": "Hardest",
              "completed": True,
              "players": [{"name": "TheFlyinGoaT", "named": True,
                           "civilisation": "Ethiopians", "winner": True,
                           "eapm": 37, "rating": 1018},
                          {"name": "an unnamed opponent", "named": False,
                           "civilisation": "Mongols", "winner": False,
                           "eapm": 798, "rating": None}]}
    header.update(over)
    return {"path": "r.aoe2record", "header": header}


def test_the_record_can_say_who_won_and_nothing_else_can():
    """Winning is not drawn anywhere Loom reads, so this is not a better
    reading of something it saw - it is the only reading there is."""
    rows = statsview.record_rows(a_header())
    flat = {label: (value, good) for label, value, good in rows}
    assert flat["map"][0] == "Arabia"
    assert any("won" in label for label in flat)
    assert any(good is True for _, good in flat.values())
    assert "Ethiopians" in flat["TheFlyinGoaT — won"][0]
    assert "1018 rating" in flat["TheFlyinGoaT — won"][0]


def test_an_abandoned_game_makes_nobody_the_loser():
    """A match nobody won - quit at 2:54, measured - must not paint every
    player red. "Not known" and "lost" are different answers."""
    header = a_header()
    for player in header["header"]["players"]:
        player["winner"] = False
    rows = statsview.record_rows(header)
    assert all(good is None for _, _, good in rows), "an abandoned game had losers"
    assert statsview.game_outcome({"record": header}) is None


def test_the_list_says_won_only_when_the_record_says_so():
    assert statsview.game_outcome({"record": a_header()}) == "won"
    # Losing needs someone to have WON. Flipping the first player alone
    # is the abandoned case, not a defeat - which is the distinction the
    # test above pins and this one nearly lost.
    lost = a_header()
    lost["header"]["players"][0]["winner"] = False
    lost["header"]["players"][1]["winner"] = True
    assert statsview.game_outcome({"record": lost}) == "lost"
    # No record attached is not a loss.
    assert statsview.game_outcome({"game": {}}) is None
    # A header this mgz could not read is not a loss either.
    assert statsview.game_outcome({"record": {"path": "r", "header": None}}) is None


def test_a_nameless_opponent_is_not_a_blank_row():
    """Skirmish opponents arrive with an empty string. "" is the absence
    of a name, not a name."""
    rows = statsview.record_rows(a_header())
    assert all(label.strip() for label, _, _ in rows)
    assert any("unnamed" in label for label, _, _ in rows)


def test_a_record_attached_by_an_older_build_is_topped_up(stats_dir,
                                                          monkeypatch):
    """The header and the command series both arrived after the first
    enrichments did. Filling those in is finishing an answer, not
    re-reading a settled one."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "builds": {}, "researches": {}, "queued": {},
                      "ages": {}}          # no header, no apm: an old one
    path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(statsview.replay, "harvest", lambda p: StoredTruth())
    monkeypatch.setattr(statsview.replay, "command_rate",
                        lambda p, **k: {"t": [0], "apm": [12],
                                        "commands_total": 1})
    monkeypatch.setattr(statsview.replay, "summary", lambda p: a_header()["header"])
    monkeypatch.setattr(statsview.replay, "records", lambda **k: [
        type("R", (), {"path": pathlib.Path("r.aoe2record")})])

    assert statsview.enrich_with_record(path).startswith("added")
    after = statsview.load_stats(path)
    assert after["record"]["header"]["map"] == "Arabia"
    # ...and a COMPLETE record is still left alone.
    assert "already" in statsview.enrich_with_record(path)


def test_the_records_villager_line_is_orders_not_a_head_count(app):
    """It counts every villager ASKED FOR, so it only ever rises. Against
    the HUD count - who is alive - the gap is losses plus whatever is
    still in a Town Centre, and that gap is the reason both are drawn."""
    data = game_with_ages()
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "villagers_ordered": [5, 5, 5, 34, 93]}
    view = statsview.ChartView(("villagers",))
    view.show_game(data)
    assert view._villagers_ordered() == [(5, 1), (5, 2), (5, 3),
                                         (34, 4), (93, 5)]
    # A batch of three queued in one second is a STEP of three, which is
    # what the player did - smoothing it would draw a rise nobody made.
    assert view._villagers_ordered()[2][0] == view._villagers_ordered()[0][0]


def test_a_game_with_no_record_draws_no_ordered_line(app):
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    assert view._villagers_ordered() == []


def test_a_record_from_an_older_build_can_be_FINISHED_from_the_window(
        stats_dir, monkeypatch):
    """The author hit this: games enriched before eAPM existed showed no
    eAPM and there was no way to ask for it.

    enrich_with_record knew how to top such a record up. The button was
    disabled whenever a record existed AT ALL, so that code could never be
    reached from the window - two separate judgements about the same
    question, and the stricter one won silently."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "builds": {}, "researches": {}, "queued": {},
                      "ages": {}}
    path.write_text(json.dumps(data), encoding="utf-8")

    assert not statsview.record_is_complete(statsview.load_stats(path))

    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    assert window.add_record_button.isEnabled(),         "an unfinished record could not be finished"
    assert "Finish" in window.add_record_button.text(),         "the button did not say what it would do"


def test_a_complete_record_leaves_the_button_alone(stats_dir):
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "header": None, "apm": None,
                      "villagers_ordered": None}
    path.write_text(json.dumps(data), encoding="utf-8")

    # Every value None and still COMPLETE: they were asked for and the
    # answer was nothing. Absent and null are different answers.
    assert statsview.record_is_complete(statsview.load_stats(path))
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    assert not window.add_record_button.isEnabled()


def test_what_a_complete_record_holds_has_one_definition():
    """The bug was two places deciding it separately. RECORD_KEYS is the
    one list, and record_section must write every key in it - including
    when the answer is None - or a file would be topped up forever."""
    import inspect
    source = inspect.getsource(statsview.record_section)
    for key in statsview.RECORD_KEYS:
        assert f'"{key}"' in source, f"record_section never writes {key}"


# ---- the two witnesses, side by side -------------------------------------

def a_compared_game():
    return {
        "game": {"duration": 2829, "max_villagers": 138, "tc_count": 3,
                 "tc_idle_seconds": 1907.0, "deaths": [[300, 6, False]],
                 "ages": [[700, "reached", 2]]},
        "apm": {"t": [0], "apm": [60], "keys_total": 956, "clicks_total": 2948},
        "record": {"path": "r.aoe2record", "duration": 2829,
                   "villagers_ordered": [1] * 144,
                   "ages": {"feudal_age": 508},
                   "builds": {"town_center": [100, 200]},
                   "apm": {"t": [0], "apm": [12], "commands_total": 1480},
                   "header": {"players": [{"name": "me", "eapm": 31}]}},
    }


def test_the_starting_town_centre_is_not_a_disagreement():
    """A player BEGINS with one and a starting Town Centre is never
    placed, so the record's placements are always one short of the number
    standing. Left raw the row read "3 seen / 2 placed", which looks
    exactly like the reader over-counting and is not - and a table whose
    whole job is surfacing disagreements must not manufacture one."""
    rows = dict((label, (read, rec))
                for label, read, rec in statsview.comparison_rows(
                    a_compared_game()))
    read, recorded = rows["town centers"]
    assert read == "3 seen"
    assert recorded.startswith("3"), "the starting Town Centre went missing"
    assert "one to start" in recorded


def test_an_age_says_which_moment_each_witness_is_naming():
    """The crest says when an age ARRIVED, the record when it was
    STARTED. Different moments, so neither is the other's correction."""
    rows = dict((label, (read, rec))
                for label, read, rec in statsview.comparison_rows(
                    a_compared_game()))
    read, recorded = rows["feudal age"]
    assert "reached" in read and "started" in recorded


def test_a_row_only_one_witness_can_answer_leaves_the_other_blank():
    """Nothing on the HUD says what a player's eAPM was. Writing a number
    there would be a claim Loom never made."""
    rows = dict((label, (read, rec))
                for label, read, rec in statsview.comparison_rows(
                    a_compared_game()))
    assert rows["eAPM"][0] is None, "Loom cannot compute eAPM and said it did"
    # And a number only LOOM can give is not in this table at all - it
    # belongs under "Only Loom could see this", which says so in a
    # heading rather than by leaving a cell empty and hoping.
    assert "TC idle time" not in rows
    html = statsview.two_column_html(statsview.comparison_rows(
        a_compared_game()))
    assert "None" not in html, "a blank cell rendered the word None"


def test_no_record_means_no_comparison_at_all():
    """Half a comparison is not a comparison."""
    assert statsview.comparison_rows({"game": {"duration": 600}}) == []


def test_a_tab_with_one_chart_does_not_offer_to_switch_it_off(app):
    """"APM" on the APM tab switches off the only thing there is to look
    at. Not a choice worth offering."""
    apm = statsview.ChartTab(("apm",))
    assert "apm" in apm.boxes, "the chart is still there to be drawn"
    assert not apm.boxes["apm"].isVisible() or apm.boxes["apm"].parent() is None
    labels = [b.text() for b in apm.witness_boxes.values()]
    assert labels == ["APM (Loom read)", "eAPM (recorded game)"]


def test_each_chart_names_its_own_witnesses():
    """A generic "Loom read / recorded game" pair on every chart invites
    the gap between two lines to be read as a fault. They are measuring
    different quantities and the label has to say which."""
    assert statsview.ChartView.witness_label("apm", statsview.FROM_RECORD)[0]         == "eAPM (recorded game)"
    assert statsview.ChartView.witness_label(
        "villagers", statsview.FROM_RECORD)[0] ==         "villagers queued (recorded game)"
    # ...and every one explains how the number arrived.
    for chart, witnesses in statsview.ChartView.WITNESSES.items():
        for witness in witnesses:
            label, tip = statsview.ChartView.witness_label(chart, witness)
            assert tip and len(tip) > 40, f"{chart}/{witness} has no tooltip"


def test_the_record_villager_line_is_called_QUEUED_not_a_count():
    """The author's own diagnosis and he is right: the record counts the
    click, Loom counts the villager. Naming both "villagers" made the gap
    look like a reading fault."""
    label, tip = statsview.ChartView.witness_label(
        "villagers", statsview.FROM_RECORD)
    assert "queued" in label
    assert "ORDERED" in tip and "alive" not in label


def test_villagers_and_population_switch_separately(app):
    """They share one value axis, so they cannot be separate charts - two
    charts on one frame would each draw an axis and put the lines on
    different scales while looking comparable."""
    tab = statsview.ChartTab(("villagers",))
    assert set(tab.part_boxes) == {"villagers", "population", "cap"}
    tab.part_boxes["population"].setChecked(False)
    assert tab.view.parts == ["villagers", "cap"]


def test_a_witness_with_parts_becomes_their_LABEL_not_a_third_box(app):
    """Unticking every part already draws nothing, which is exactly what
    a witness checkbox would have done. Two controls for one outcome is a
    control that lies about what it does, so the witness becomes the
    heading its parts sit inside:

        Loom read { [ ] villagers  [ ] population }   [ ] villagers queued
    """
    from PyQt6.QtWidgets import QCheckBox, QLabel, QWidget
    tab = statsview.ChartTab(("villagers",))
    assert statsview.SCREEN not in tab.witness_boxes,         "the screen witness kept a checkbox its parts had replaced"
    assert set(tab.part_boxes) == {"villagers", "population", "cap"}

    # Found by SEARCHING rather than by walking layout positions: the
    # control row became a flow row so it could wrap, and a test that
    # navigates the layout tree breaks on a change that is invisible to
    # anyone using the window.
    group = next(w for w in tab.findChildren(QWidget)
                 if w.layout() is not None
                 and any(isinstance(w.layout().itemAt(i).widget(), QLabel)
                         for i in range(w.layout().count())))
    inside = group.layout()
    heading = inside.itemAt(0).widget()
    assert isinstance(heading, QLabel) and heading.text() == "Loom read"
    assert [inside.itemAt(i).widget().text() for i in range(1, inside.count())
            if isinstance(inside.itemAt(i).widget(), QCheckBox)] == [
        "villagers", "population", "house room"]


def test_the_screen_witness_stays_on_when_its_parts_speak_for_it(app):
    """With no box of its own it must never be dropped from the witness
    list - the parts decide what is drawn, and all of them off already
    draws nothing."""
    tab = statsview.ChartTab(("villagers",))
    assert statsview.SCREEN in tab.view.witnesses
    tab.part_boxes["population"].setChecked(False)
    assert tab.view.parts == ["villagers", "cap"]
    assert statsview.SCREEN in tab.view.witnesses,         "turning off one line turned off the whole witness"
    tab.part_boxes["villagers"].setChecked(False)
    tab.part_boxes["cap"].setChecked(False)
    assert tab.view.parts == []
    assert statsview.SCREEN in tab.view.witnesses


def test_a_witness_with_no_parts_keeps_its_own_box(app):
    """APM has one series per witness, so there is nothing to delegate
    to and the checkbox is the only control there could be."""
    tab = statsview.ChartTab(("apm",))
    assert set(tab.witness_boxes) == {statsview.SCREEN, statsview.FROM_RECORD}
    assert tab.part_boxes == {}


def test_the_record_witness_has_no_parts_to_nest(app):
    """It is one series. Only the screen witness subdivides."""
    tab = statsview.ChartTab(("apm",))
    assert tab.part_boxes == {}


def test_the_axis_follows_only_what_is_drawn(app):
    """Hiding the population must rescale to the villager line, not leave
    it squashed against the floor by a cap it can no longer be compared
    to."""
    from PyQt6.QtGui import QPixmap
    data = game_with_ages()
    data["timeline"]["pop"] = [200] * len(data["timeline"]["t"])
    data["timeline"]["pop_cap"] = [200] * len(data["timeline"]["t"])
    view = statsview.ChartView(("villagers",))
    view.show_game(data)
    view.resize(700, 400)
    view.parts = ["villagers"]
    view.render(QPixmap(view.size()))       # a paint that raises aborts
    view.parts = []
    view.render(QPixmap(view.size()))


def test_the_readout_says_every_line_that_is_switched_on(app):
    """It reported the villager count and nothing else, whatever else was
    drawn - so three of the four lines on Society had a number nobody
    could read off the chart."""
    data = game_with_ages()
    data["timeline"]["pop"] = [122] * len(data["timeline"]["t"])
    data["timeline"]["pop_cap"] = [130] * len(data["timeline"]["t"])
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "villagers_ordered": list(range(0, 300, 3))}
    view = statsview.ChartView(("villagers",))
    view.show_game(data)

    def said():
        keys = tuple(k for name in view.charts if name in view.enabled
                     for k in view._readout_keys(name))
        return statsview.hover_summary(view.values_at(300), keys)

    everything = said()
    for expected in ("villagers", "population", "house room", "queued"):
        assert expected in everything, expected


def test_a_line_switched_off_is_not_quoted_at_it(app):
    """A number for a line that is not on screen invites the reader to
    hunt for it - the same fault as a tab quoting a series from another
    tab's chart."""
    data = game_with_ages()
    data["timeline"]["pop"] = [122] * len(data["timeline"]["t"])
    data["timeline"]["pop_cap"] = [130] * len(data["timeline"]["t"])
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "villagers_ordered": [1, 2, 3]}
    view = statsview.ChartView(("villagers",))
    view.show_game(data)

    def said():
        keys = tuple(k for name in view.charts if name in view.enabled
                     for k in view._readout_keys(name))
        return statsview.hover_summary(view.values_at(300), keys)

    view.parts = ["villagers"]
    view.witnesses = [statsview.SCREEN]
    only = said()
    assert "villagers" in only
    for gone in ("population", "house room", "queued"):
        assert gone not in only, gone

    # Nothing on at all leaves the time and no claims.
    view.parts = []
    assert said() == statsview.format_time(300)


def test_the_records_number_is_called_queued_in_the_readout_too(app):
    """"112 villagers - 118 villagers" would undo on hover all the
    labelling the key and the checkbox got right."""
    values = {"t": 300, "villagers": 112, "queued": 118}
    said = statsview.hover_summary(values, ("villagers", "queued"))
    assert "112 villagers" in said and "118 queued" in said
    assert said.count("villagers") == 1


def test_the_two_witnesses_never_wear_the_same_colour():
    """Green is what Loom read, violet is the recorded game, on every
    chart. APM broke this quietly: APM_COLOR was (200,160,235) against
    RECORD_COLOR's (190,140,235) - fine for as long as APM had one line,
    and indistinguishable the moment eAPM landed beside it.

    Distance rather than inequality, because two colours can differ by a
    digit and still be one colour to a person looking at a chart."""
    def apart(one, other):
        return (abs(one.red() - other.red()) + abs(one.green() - other.green())
                + abs(one.blue() - other.blue()))

    screen_side = (statsview.ON_PACE_COLOR, statsview.APM_COLOR,
                   statsview.APM_RAW_COLOR)
    for colour in screen_side:
        assert apart(colour, statsview.RECORD_COLOR) > 120,             "a Loom line is the same colour as the recorded game's"
    # ...and the APM pair is the screen colour rather than its own hue,
    # so the convention cannot drift one constant at a time.
    assert apart(statsview.APM_COLOR, statsview.ON_PACE_COLOR) == 0


def test_the_plan_chart_shows_when_you_ORDERED_each_item(monkeypatch):
    """The checkbox for this existed for several commits and drew
    nothing - the witness was declared and never wired, which is the
    exact failure the declaration was written to prevent."""
    steps = [FakeStep(100, [item("building_economy/mill")])]
    game = {"events": [[300, "built:mill"]]}
    record = {"builds": {"mill": [180]}, "researches": {}, "ages": {}}
    from loom import build_order
    fake = type("B", (), {"steps": steps})()
    monkeypatch.setattr(build_order.BuildOrder, "load_by_name",
                        classmethod(lambda cls, name: fake))
    rows = statsview.plan_versus_actual("whatever", game, record)
    assert rows[0].ordered == 180
    assert rows[0].observed == 300, "the order overwrote the completion"


def test_an_age_order_time_survives_the_name_it_is_stored_under(monkeypatch):
    """AGE_NAMES says "Feudal Age" and the record says "feudal_age".
    Joining them without splitting produced "feudal age_age", which
    matched nothing and left every age silently without an order time."""
    steps = [FakeStep(300, [item("age/feudal_age", "Research ")])]
    game = {"ages": [[700, "reached", 2]]}
    record = {"builds": {}, "researches": {}, "ages": {"feudal_age": 508}}
    from loom import build_order
    fake = type("B", (), {"steps": steps})()
    monkeypatch.setattr(build_order.BuildOrder, "load_by_name",
                        classmethod(lambda cls, name: fake))
    rows = statsview.plan_versus_actual("whatever", game, record)
    assert rows[0].ordered == 508
    assert rows[0].observed == 700


def test_an_item_the_record_never_mentions_gets_no_mark(monkeypatch):
    """A mark at a time nobody knows would be an invention, on the one
    chart whose whole job is keeping seen apart from assumed."""
    steps = [FakeStep(100, [item("building_economy/mill")])]
    from loom import build_order
    fake = type("B", (), {"steps": steps})()
    monkeypatch.setattr(build_order.BuildOrder, "load_by_name",
                        classmethod(lambda cls, name: fake))
    rows = statsview.plan_versus_actual(
        "whatever", {"events": [[300, "built:mill"]]}, None)
    assert rows[0].ordered is None


def test_the_order_marks_appear_only_while_that_witness_is_on(app):
    """Ticking a box must change the picture, which is the whole
    complaint that started this."""
    from PyQt6.QtGui import QPixmap
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260), 150)]
    view.resize(800, 300)

    def violet():
        canvas = QPixmap(view.size())
        view.render(canvas)
        image = canvas.toImage()
        want = statsview.RECORD_COLOR
        return sum(1 for y in range(0, image.height(), 2)
                   for x in range(0, image.width(), 2)
                   if abs(image.pixelColor(x, y).red() - want.red()) < 30
                   and abs(image.pixelColor(x, y).green() - want.green()) < 30
                   and abs(image.pixelColor(x, y).blue() - want.blue()) < 30)

    view.witnesses = [statsview.SCREEN, statsview.FROM_RECORD]
    with_it = violet()
    view.witnesses = [statsview.SCREEN]
    assert with_it > violet(), "the record witness drew nothing"


def test_the_build_icons_answer_to_the_screen_checkbox(app):
    """The icons ARE the screen witness - each sits where the feed said
    the thing arrived, wearing the verdict that reading earned. They drew
    regardless until the author ticked the box and nothing happened,
    which is the failure the witness declaration exists to prevent."""
    from PyQt6.QtGui import QPixmap
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260), 150)]
    view.resize(800, 300)

    def verdict_pixels():
        canvas = QPixmap(view.size())
        view.render(canvas)
        image = canvas.toImage()
        want = statsview.ON_PACE_COLOR
        return sum(1 for y in range(0, image.height(), 2)
                   for x in range(0, image.width(), 2)
                   if abs(image.pixelColor(x, y).red() - want.red()) < 30
                   and abs(image.pixelColor(x, y).green() - want.green()) < 30
                   and abs(image.pixelColor(x, y).blue() - want.blue()) < 30)

    view.witnesses = [statsview.SCREEN, statsview.FROM_RECORD]
    assert verdict_pixels() > 0, "the on-time border was never drawn"
    view.witnesses = [statsview.FROM_RECORD]
    assert verdict_pixels() == 0, "the icons ignored the screen checkbox"


def test_the_hit_geometry_survives_the_icons_being_hidden(app):
    """`marks` is the hit-test geometry, not just the icons - the record's
    rings are still there to be hovered when the icons are off."""
    from PyQt6.QtGui import QPixmap
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260), 150)]
    view.resize(800, 300)
    view.witnesses = [statsview.FROM_RECORD]
    view.render(QPixmap(view.size()))
    assert view._plan_marks, "hiding the icons threw away the hit-testing"


def test_a_legend_never_names_a_line_nobody_is_drawing(app):
    """The verdicts are the screen witness's judgements, so their key
    goes when that witness does - and the record's entry only appears
    when some item actually has an order time to mark."""
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260), None)]
    view.witnesses = [statsview.SCREEN, statsview.FROM_RECORD]
    assert not view._plan_has_orders(), "claimed a ring with nothing to mark"
    view.plan = [statsview.PlanRow("mill", None, 100, 200, (85, 260), 150)]
    assert view._plan_has_orders()
    view.witnesses = [statsview.SCREEN]
    assert not view._plan_has_orders(), "the ring key ignored its own witness"


def test_the_pace_line_is_dotted_and_nothing_else_is():
    """Two greys on one chart: "never seen" at (120,120,128) against the
    pace line's (162,160,180), 134 apart and still one grey in thin
    strokes. Told apart by KIND rather than hue, which survives being
    next to any future colour and works for a colourblind reader."""
    import inspect
    from PyQt6.QtCore import Qt as QtCore_Qt
    pace = inspect.getsource(statsview.ChartView._layer_pace)
    assert "DotLine" in pace, "the pace line went back to solid"
    # ...and the default is still solid, so no other caller moved.
    signature = inspect.signature(statsview.ChartView._draw_series)
    assert signature.parameters["style"].default == QtCore_Qt.PenStyle.SolidLine


def test_a_key_swatch_matches_the_line_it_stands_for():
    """A dotted line with a solid swatch beside it is a legend
    disagreeing with the chart it explains - and the reader trusts the
    small picture precisely because it is next to the words."""
    import inspect
    source = inspect.getsource(statsview.ChartView._paint_key)
    assert "entry[3]" in source, "a key entry can no longer carry a style"
    assert "SolidLine" in source, "entries without a style lost their default"


# ---- panes the player can size --------------------------------------------

def test_the_games_list_is_no_longer_capped(app, stats_dir):
    """It was setMaximumWidth(300), so a build name longer than that was
    elided however large the window got - widening only ever fed the
    tabs, and there was no way to see the rest of the name."""
    window = statsview.StatsWindow()
    assert window.games.maximumWidth() > 1000, "the list is still capped"
    assert window.games.minimumWidth() > 0, "the list can now vanish entirely"


def test_a_game_name_too_long_to_show_is_still_readable(stats_dir):
    """The divider helps and cannot always be enough."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    statsview.rename_game(stats_dir / "2026-08-25_010714_g.json",
                          "the one where I got boomed off three town centres")
    window = statsview.StatsWindow()
    window.refresh()
    item = window.games.item(0)
    assert "boomed" in item.toolTip(), "an elided name has nowhere to be read"


def test_every_divider_is_restored_from_one_place(app, stats_dir):
    """A splitter built and then left out of a hand-kept restore list
    would silently ignore its saved position - the kind of gap nobody
    notices for months, and the third one of its shape this week."""
    window = statsview.StatsWindow()
    assert {name for _, name, _ in window._dividers} == {
        "history", "build", "military"}


def test_a_saved_divider_position_comes_back(app, stats_dir, monkeypatch):
    """And a position saved when the window had a different number of
    panes is ignored rather than padded: it answers a question nobody is
    asking any more."""
    from loom import config
    from PyQt6.QtWidgets import QApplication
    monkeypatch.setattr(config, "stats_splitters",
                        lambda: {"history": [321, 654]})
    window = statsview.StatsWindow()
    # Shown and sized first: before that the window has no geometry and
    # Qt clamps setSizes to a layout that has not happened yet, which is
    # the whole reason the restore lives in showEvent.
    window.resize(1000, 600)
    window.show()
    QApplication.processEvents()
    window._restore_splitter(window.split, "history", [260, 720])
    # Within a few pixels, not exactly. Qt scales the whole list to fill
    # the window, so both the saved sizes and the defaults come back
    # slightly adjusted - asserting the exact number would be testing
    # arithmetic nobody chose, and it would fail on any window width.
    assert abs(window.split.sizes()[0] - 321) < 10

    monkeypatch.setattr(config, "stats_splitters",
                        lambda: {"history": [1, 2, 3]})
    window._restore_splitter(window.split, "history", [260, 720])
    assert abs(window.split.sizes()[0] - 260) < 10,         "a saved shape from a different set of panes was forced on"


def test_a_hand_edited_config_cannot_break_the_window(stats_dir, monkeypatch):
    """The settings file is user-visible, so anything that is not a list
    of numbers is simply not there rather than a crash on open."""
    from loom import config
    for rubbish in ({"history": "wide"}, {"history": [None, 2]},
                    "not a dict", None):
        monkeypatch.setattr(config, "load", lambda r=rubbish: {
            config.STATS_SPLITTERS: r})
        assert config.stats_splitters() == {} or all(
            isinstance(v, list) for v in config.stats_splitters().values())


def test_the_control_row_does_not_pin_the_whole_tab(app, stats_dir):
    """Qt reports a row's minimum width as the SUM of its children, so
    the long witness labels pinned the Build report page at 1438px and
    the divider could never give the list more than its 140px minimum
    however wide the window got. A flow row's minimum is its widest
    single child."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.refresh()
    for index in range(window.tabs.count()):
        page = window.tabs.widget(index)
        assert page.minimumSizeHint().width() < 900, \
            f"{window.tabs.tabText(index)} pins the window open"


def test_a_dotted_line_stays_dotted_where_it_runs_flat(app):
    """It came out dotted on its steep climbs and solid everywhere it was
    flat. Qt restarts a pen's dash pattern at every drawLine call, and a
    flat run is about a pixel wide per sample - shorter than one dash
    period, so every segment drew entirely "on"."""
    from PyQt6.QtGui import QPixmap
    flat = list(range(0, 600, 5))
    data = {"meta": {}, "game": {"ages": []},
            "timeline": {"t": flat, "pace": [40] * len(flat),
                         "villagers": [10] * len(flat),
                         "idle_tcs": [0] * len(flat),
                         "pop": [10] * len(flat),
                         "pop_cap": [20] * len(flat)}}
    view = statsview.ChartView(("pace",))
    view.show_game(data)
    view.resize(800, 300)
    canvas = QPixmap(view.size())
    view.render(canvas)
    image = canvas.toImage()

    want = statsview.PACE_COLOR

    def lit(x, y):
        pixel = image.pixelColor(x, y)
        return (abs(pixel.red() - want.red()) < 40
                and abs(pixel.blue() - want.blue()) < 40)

    row = max(range(image.height()),
              key=lambda y: sum(lit(x, y) for x in range(image.width())))
    along = [lit(x, row) for x in range(image.width())]
    breaks = sum(1 for i in range(1, len(along)) if along[i] != along[i - 1])
    assert breaks > 20, "the flat run drew solid - the dash did not carry"


def test_a_series_is_one_polyline_per_unbroken_run(app):
    """A gap and a recording seam must still break the line, or the two
    halves join across a hole in the readings."""
    import inspect
    source = inspect.getsource(statsview.ChartView._draw_series)
    assert "drawPolyline" in source, "back to a drawLine per pair"
    # Three places have to flush: a None reading, a backwards clock, and
    # the end of the points.
    assert source.count("flush()") >= 3, "a break stopped breaking the line"


def test_every_checkbox_on_a_control_row_explains_itself(app):
    """The witness boxes carried an explanation from the day they were
    added and the series boxes beside them never did, so on the one tab
    that has both, half the row explained itself and half did not."""
    tab = statsview.ChartTab(("plan", "pace"), combined="build and pace")
    for name, box in tab.boxes.items():
        assert box.toolTip(), f"the {name} checkbox says nothing about itself"
    for witness, box in tab.witness_boxes.items():
        assert box.toolTip(), f"the {witness} checkbox says nothing"


def test_every_registered_chart_has_something_to_say_about_itself():
    """A hand-kept list beside a registry drifts - this is the third one
    this week - so it is walked from the registry rather than eyeballed."""
    for name in statsview.ChartView.CHARTS:
        about = statsview.ChartView.ABOUT.get(name)
        assert about and len(about) > 20, name


# ---- the header band, measured rather than guessed ------------------------

def test_the_frame_reserves_the_crest_room_it_actually_needs(app):
    """Three functions used to decide this independently and none of them
    measured: _frame hard-coded 26px, _draw_key re-derived the title's own
    row from scratch, and _draw_age_rules hung half a crest above the plot
    without telling either. The crests landed six pixels into the title.

    The clearance is asserted for every REGISTERED chart, and then again
    with CREST_SIZE monkeypatched - that second half is the point. It is
    the only assertion here that fails the moment someone writes a magic
    number back into _frame.
    """
    from PyQt6.QtGui import QPainter, QPixmap
    for name, (title, _method) in statsview.ChartView.CHARTS.items():
        view = statsview.ChartView((name,))
        view.show_game(game_with_ages())
        canvas = QPixmap(700, 300)
        painter = QPainter(canvas)
        try:
            plot = view._frame(painter, 10, 10, 600, 260, title,
                               statsview.ChartView.PLAN_KEY)
        finally:
            painter.end()
        crest_top = plot[1] - statsview.CREST_OVERHANG
        header = 10 + statsview.header_height(
            title, statsview.ChartView.PLAN_KEY, 600)
        assert crest_top >= header, f"{name}: the crest sits in the words"


def test_a_bigger_crest_moves_the_room_reserved_for_it(app, monkeypatch):
    """The reservation and the draw read ONE constant, so they cannot
    drift apart again. This is the requirement the old code failed:
    CREST_SIZE was not referenced by _frame at all, so changing it
    silently changed the overlap."""
    from PyQt6.QtGui import QPainter, QPixmap

    def plot_top(overhang):
        monkeypatch.setattr(statsview, "CREST_OVERHANG", overhang)
        view = statsview.ChartView(("villagers",))
        view.show_game(game_with_ages())
        canvas = QPixmap(700, 300)
        painter = QPainter(canvas)
        try:
            return view._frame(painter, 10, 10, 600, 260, "T", ())[1]
        finally:
            painter.end()

    assert plot_top(40) - plot_top(10) == 30, \
        "the plot did not move with the crest it is making room for"


def test_the_key_wraps_and_never_drops_an_entry():
    """It ran off the right edge of a narrow chart. Wrapping rather than
    clipping, because a key entry silently missing is a legend lying
    about which colour is which."""
    entries = [(statsview.ON_PACE_COLOR, 2, f"series number {n}")
               for n in range(10)]
    for width in (1200, 700, 480):
        placed, rows = statsview.key_layout("A LONG CHART TITLE",
                                            entries, width)
        assert len(placed) == len(entries), f"{width}: an entry was dropped"
        assert all(dx < width for _entry, dx, _row in placed), \
            f"{width}: an entry starts past the frame"
    narrow = statsview.key_layout("A LONG CHART TITLE", entries, 480)[1]
    wide = statsview.key_layout("A LONG CHART TITLE", entries, 1200)[1]
    assert narrow > wide, "a narrow chart did not wrap its key"


def test_a_wrapped_key_pushes_the_plot_down(app):
    """The header is measured, so more rows of key means less plot -
    which is what "measured" has to mean if it means anything."""
    entries = [(statsview.ON_PACE_COLOR, 2, f"series number {n}")
               for n in range(10)]
    one = statsview.header_height("T", entries[:1], 1200)
    many = statsview.header_height("T", entries, 480)
    assert many > one, "wrapping the key did not make room for it"


def test_a_chart_with_no_key_pays_nothing_for_one():
    """The author wants things tight. A header that always reserved a key
    row would cost every chart that has no key."""
    assert statsview.header_height("T", (), 800) < statsview.header_height(
        "T", [(statsview.ON_PACE_COLOR, 2, "one")], 800)


def test_the_readout_never_covers_a_chart_title(app):
    """It clamped only to the widget, so it landed on titles, keys and
    crests. Pushed DOWN past the header rather than up: the gap above a
    chart is MARGIN and the box is taller than that, so upward is not a
    direction that exists here."""
    view = statsview.ChartView(("villagers", "apm"))
    view.show_game(game_with_ages())
    view.resize(900, 600)
    boxes = view._chart_boxes()
    assert boxes, "no charts to point at"
    x, y, width, _height, title, _draw = boxes[0]

    view.hover_x, view.hover_y = x + 200, y + 4      # right on the title
    _left, top = view.hover_box(160)
    assert top >= y + statsview.header_height(title, view.PLAN_KEY, width), \
        "the readout sat on the chart's own title"


def test_the_readout_never_leaves_the_left_edge(app):
    """A wide readout flipped near the left edge went off the widget
    entirely - there was a right clamp and no left one."""
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    view.resize(600, 400)
    view.hover_x, view.hover_y = 20, 200
    left, _top = view.hover_box(560)
    assert left >= statsview.MARGIN, "the readout went off the left edge"


def test_a_long_readout_stays_inside_the_widget(app):
    """The gate this chart never had, and the reason the fault survived.

    The test above asserts left >= MARGIN and passes precisely because it
    checks ONE edge. Nothing anywhere asserted left + width <= width(), so
    a box wider than the widget was pinned at the left margin and ran off
    the right with nothing to stop it - which is exactly what an unpaired
    build item did, at 730-790px on a pane whose minimum is 480.
    """
    view = statsview.ChartView(("villagers",))
    view.show_game(game_with_ages())
    view.resize(600, 400)
    view.hover_x, view.hover_y = 300, 200

    told = ("stable - card 14:20 - ordered 13:52 - from the recorded game"
            " - Loom read fewer of these than you built, so it cannot say"
            " which one this was")
    lines = statsview.wrap(told)
    assert len(lines) > 1, "the label under test should need more than a line"

    from PyQt6.QtGui import QFontMetrics
    metrics = QFontMetrics(statsview.readout_font())
    width = max(metrics.horizontalAdvance(line) for line in lines) + 10
    width = min(width, max(60, view.width() - 2 * statsview.MARGIN))
    left, top = view.hover_box(width)

    assert left >= statsview.MARGIN
    assert left + width <= view.width(), "the readout ran off the right edge"
    height = statsview.READOUT_HEIGHT * len(lines)
    top = max(statsview.READOUT_HEIGHT + 2,
              min(top, view.height() - height - 2))
    assert top + height <= view.height(), "the readout ran off the bottom"


def test_the_hovered_label_is_drawn_on_more_than_one_line(app):
    """What the player sees. The unpaired-item label carries 103
    characters of unconditional tail, so it can never fit on one line at
    any window size worth using."""
    told = ("stable - card 14:20 - ordered 13:52 - from the recorded game"
            " - Loom read fewer of these than you built, so it cannot say"
            " which one this was")

    lines = statsview.wrap(told)

    assert len(lines) >= 3
    for line in lines:
        assert len(line) <= 60, line


def test_the_chart_geometry_has_one_definition(app):
    """paintEvent held the arithmetic and an unreachable branch of it
    held a second copy - which is how the pointer ended up with its own
    third idea of where the charts were."""
    import inspect
    source = inspect.getsource(statsview.ChartView.paintEvent)
    assert source.count("if self.combined:") == 1, \
        "the unreachable combined branch is back"
    assert "_chart_boxes" in source


def test_the_age_numbers_sit_on_the_floor_not_the_ceiling(app):
    """They shared the top row with the crests AND with the build
    chart's first lane of icons. The floor is not free either - the idle
    band grows up from it - so each label clears its own ground, which
    the top could never have offered: `hi` is the maximum of the values,
    so every series touches the ceiling by construction."""
    import inspect
    source = inspect.getsource(statsview.ChartView._draw_span_labels)
    assert "plot_y + plot_h" in source, "the labels went back to the top"
    assert "_label_chip" in source, "a label draws straight onto the series"


def test_a_label_is_never_drawn_under_the_thing_that_covers_it(app):
    """Both the idle chart and the military chart drew their labels
    BEFORE the series that paints over them. True before this change and
    guaranteed after it, since the labels now sit where the idle band
    grows."""
    import inspect
    for method in (statsview.ChartView._draw_idle_tcs,
                   statsview.ChartView._draw_military):
        source = inspect.getsource(method)
        last_label = source.rfind("_draw_span_labels")
        last_paint = max(source.rfind("fillRect"), source.rfind("_draw_series"))
        assert last_label > last_paint, \
            f"{method.__name__} still paints over its own labels"


# ---- a chart in a window of its own ---------------------------------------

def test_popping_out_leaves_the_tab_its_own_chart(app, stats_dir):
    """A SECOND copy, the author's ruling - so a big window can be
    studied BESIDE the tab rather than instead of it. The cost is that
    they are two views: they do not follow each other, and the button
    says so."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    window._pop_out(("villagers",), "Society", None)
    popped = window._popouts[("villagers",)]

    popped.tab.part_boxes["population"].setChecked(False)
    popped.tab.view.zoom_by(4)
    assert popped.tab.view.parts != window.society.view.parts
    assert popped.tab.view.window() != window.society.view.window(), \
        "the two views share a zoom - they are one chart, not two"


def test_a_popped_out_chart_follows_the_selected_game(app, stats_dir):
    """A window still showing the game before last, while the list has
    moved on, is the worst failure available to a window whose whole
    purpose is careful reading - and it would look exactly like data."""
    write_game(stats_dir, "2026-08-25_010714_a.json")
    write_game(stats_dir, "2026-08-24_010714_b.json")
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    window._pop_out(("villagers",), "Society", None)
    popped = window._popouts[("villagers",)]

    window.games.setCurrentRow(1)
    assert popped.tab.view.data is window._selected, \
        "the pop-out kept showing the game the list had moved off"


def test_popping_out_twice_raises_the_one_that_exists(app, stats_dir):
    """Rather than stacking an identical second window behind the
    first."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    window._pop_out(("villagers",), "Society", None)
    window._pop_out(("villagers",), "Society", None)
    assert len(window._popouts) == 1


def test_closing_a_pop_out_is_remembered(app, stats_dir):
    """Forgotten rather than hidden, so the next one is a fresh window at
    the remembered SIZE rather than wherever the last was dragged."""
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    window._pop_out(("villagers",), "Society", None)
    window._popouts[("villagers",)].close()
    assert window._popouts == {}


def test_a_pop_out_does_not_offer_to_pop_itself_out(app):
    """A pop-out of a pop-out is two windows arguing about one chart."""
    from PyQt6.QtWidgets import QPushButton
    popped = statsview.ChartWindow(("apm",), "APM")
    assert "pop out" not in [b.text() for b in popped.findChildren(QPushButton)]
    assert "pop out" in [
        b.text() for b in statsview.ChartTab(("apm",)).findChildren(QPushButton)]


def test_every_tab_that_draws_a_chart_can_be_popped_out(app, stats_dir):
    """The author's ruling: consistent, so there is nothing to remember
    about which ones do it."""
    from PyQt6.QtWidgets import QPushButton
    write_game(stats_dir, "2026-08-25_010714_g.json")
    window = statsview.StatsWindow()
    for tab in (window.society, window.economy, window.apm_charts,
                window.military_charts, window.pace_charts):
        assert "pop out" in [b.text() for b in tab.findChildren(QPushButton)]


def test_shift_and_the_wheel_walks_along_a_long_game_name(app, stats_dir):
    """Qt elides an item that overflows and offers no way to see the
    rest. The tooltip covers reading ONE; this covers comparing twenty."""
    from PyQt6.QtCore import QPoint, QPointF, Qt as QtCore_Qt
    from PyQt6.QtGui import QWheelEvent
    write_game(stats_dir, "2026-08-25_010714_g.json")
    statsview.rename_game(stats_dir / "2026-08-25_010714_g.json",
                          "the one where I got boomed off three town centres"
                          " while staring at the wrong side of the map")
    window = statsview.StatsWindow()
    window.resize(1000, 600)
    window.show()
    window.refresh()
    window.split.setSizes([200, 800])
    app.processEvents()

    bar = window.games.horizontalScrollBar()
    assert bar.maximum() > 0, "the name was elided instead of overflowing"

    def wheel(modifier, dy=-120):
        return QWheelEvent(
            QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, dy),
            QtCore_Qt.MouseButton.NoButton, modifier,
            QtCore_Qt.ScrollPhase.NoScrollPhase, False)

    window.games.wheelEvent(wheel(QtCore_Qt.KeyboardModifier.ShiftModifier))
    moved = bar.value()
    assert 0 < moved < bar.maximum(), \
        "one notch went nowhere, or went the whole way at once"
    window.games.wheelEvent(
        wheel(QtCore_Qt.KeyboardModifier.ShiftModifier, dy=120))
    assert bar.value() < moved, "it would not come back"


def test_a_plain_wheel_still_scrolls_the_list_the_way_it_did(app, stats_dir):
    """Shift is the addition. Taking the ordinary gesture away would be
    a worse trade than the problem it solves."""
    from PyQt6.QtCore import QPoint, QPointF, Qt as QtCore_Qt
    from PyQt6.QtGui import QWheelEvent
    for n in range(40):
        write_game(stats_dir, f"2026-08-{(n % 28) + 1:02d}_0107{n:02d}_g.json")
    window = statsview.StatsWindow()
    window.resize(600, 300)
    window.show()
    window.refresh()
    app.processEvents()
    down = window.games.verticalScrollBar()
    assert down.maximum() > 0, "not enough games to scroll"
    window.games.wheelEvent(QWheelEvent(
        QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, -120),
        QtCore_Qt.MouseButton.NoButton, QtCore_Qt.KeyboardModifier.NoModifier,
        QtCore_Qt.ScrollPhase.NoScrollPhase, False))
    assert down.value() > 0, "a plain wheel stopped scrolling the list"


def test_the_witness_group_border_is_aimed_at_the_box_alone(app):
    """A bare `QWidget { border }` in a stylesheet is inherited by every
    widget inside it, so the checkboxes came out boxed as well - and
    undoing that with `QCheckBox { border: none }` is fighting the
    cascade rather than not starting it."""
    from PyQt6.QtWidgets import QWidget
    tab = statsview.ChartTab(("villagers",))
    group = next(w for w in tab.findChildren(QWidget)
                 if w.objectName() == "witnessGroup")
    assert "#witnessGroup" in group.styleSheet()
    assert "border: none" not in group.styleSheet()
    margins = group.layout().contentsMargins()
    assert margins.top() >= 3 and margins.bottom() >= 3, \
        "the border runs through the checkbox indicators"


# ---- an item Loom could not place -----------------------------------------

def test_a_drifted_item_is_placed_by_the_record_not_by_a_guess(monkeypatch):
    """Items are matched to sightings in planned order, which is only
    sound when Loom read exactly as many of a thing as were ordered. Read
    one house against three built and the later cards are credited with
    completions belonging to different houses - measured on a real game,
    "house x2, ordered 0:04, Loom saw 6:33", six minutes on a
    twenty-five-second building."""
    steps = [FakeStep(50, [item("building_economy/house")]),
             FakeStep(175, [item("building_economy/house")])]
    game = {"events": [[600, "built:house"]]}
    record = {"builds": {"house": [10, 120]}, "researches": {}, "ages": {}}
    rows = plan_for(monkeypatch, steps, game, record)
    assert [row.paired for row in rows] == [False, False]
    assert [row.ordered for row in rows] == [10, 120]


def test_a_subject_loom_counted_right_is_left_exactly_as_it_was(monkeypatch):
    """The author's rule: if Loom was already correct, leave it."""
    steps = [FakeStep(50, [item("building_economy/mill")])]
    game = {"events": [[300, "built:mill"]]}
    record = {"builds": {"mill": [180]}, "researches": {}, "ages": {}}
    rows = plan_for(monkeypatch, steps, game, record)
    assert rows[0].paired and rows[0].observed == 300


def test_a_subject_the_record_says_nothing_about_is_not_drifted(monkeypatch):
    """Silence is not disagreement. Judging a subject the record never
    mentions would be the mistake this whole seam exists to avoid."""
    steps = [FakeStep(50, [item("building_economy/mill")])]
    rows = plan_for(monkeypatch, steps,
                    {"events": [[300, "built:mill"]]}, None)
    assert rows[0].paired


def test_a_drifted_item_answers_to_the_RECORD_checkbox(app):
    """It is the recorded game speaking, not Loom - so it goes on and off
    with that box, and unticking "what Loom saw" leaves exactly the items
    Loom could not place."""
    from PyQt6.QtGui import QPixmap
    view = statsview.ChartView(("plan",))
    view.show_game(game_with_ages())
    view.plan = [statsview.PlanRow("house", None, 100, 600, (85, 260),
                                   150, False)]
    view.resize(800, 300)

    def violet():
        canvas = QPixmap(view.size())
        view.render(canvas)
        image = canvas.toImage()
        want = statsview.RECORD_COLOR
        return sum(1 for y in range(0, image.height(), 2)
                   for x in range(0, image.width(), 2)
                   if abs(image.pixelColor(x, y).red() - want.red()) < 30
                   and abs(image.pixelColor(x, y).green() - want.green()) < 30
                   and abs(image.pixelColor(x, y).blue() - want.blue()) < 30)

    view.witnesses = [statsview.FROM_RECORD]
    assert violet() > 0, "the drifted item vanished with the screen witness"
    view.witnesses = [statsview.SCREEN]
    assert violet() == 0, "a drifted item drew under the wrong checkbox"


def test_a_drifted_item_gets_no_verdict_and_says_why(app):
    """A violet icon with no explanation is a colour the reader has to
    guess the meaning of, on the one chart where guessing is the thing
    being designed out."""
    view = statsview.ChartView(("plan",))
    row = statsview.PlanRow("house", None, 100, 600, (85, 260), 150, False)
    told = view._plan_label((row, 0, 0, 0))
    assert "recorded game" in told
    assert "cannot say which one" in told
    for verdict in ("on time", "late", "early"):
        assert verdict not in told, "a drifted item was judged anyway"


# ---- how long the player's own buildings take -----------------------------

def test_a_players_own_building_times_replace_the_book_number(monkeypatch,
                                                              tmp_path):
    """The book is one-villager time and nobody builds with one. Measured
    across 61 games, a Castle listed at 200s came in at 75/123/175 - two
    or three villagers on every one, every time."""
    import json
    from loom import durations, paths
    (tmp_path / "durations.json").write_text(
        json.dumps({"games": 61, "buildings": {"castle": 123}}),
        encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    durations.measured(reload=True)
    try:
        assert durations.build_or_research_time("castle") == 123
        # ...and the game's own number is still askable, which is what a
        # tool comparing the two needs.
        assert durations.build_or_research_time("castle", personal=False) == 200
    finally:
        durations.measured(reload=True)


def test_a_technology_is_never_overridden_by_a_measurement(monkeypatch,
                                                           tmp_path):
    """Research does not divide among helpers, so the book value IS the
    truth - and a measured figure can only be that plus queue time,
    because a Blacksmith already busy makes the next technology wait.

    The evidence: across 61 games the MINIMUM matches the table exactly
    for horse_collar, gold_mining, bodkin_arrow, fletching, ballistics,
    husbandry, iron_casting, bracer, wheelbarrow and every armour line -
    and not one measured minimum came in UNDER the table, which would be
    impossible."""
    import json
    from loom import durations, paths
    (tmp_path / "durations.json").write_text(
        json.dumps({"games": 61, "buildings": {"loom": 999}}),
        encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    durations.measured(reload=True)
    try:
        assert durations.build_or_research_time("loom") == 25
    finally:
        durations.measured(reload=True)


def test_a_missing_or_broken_measurement_file_changes_nothing(monkeypatch,
                                                              tmp_path):
    """It lives in the player's own data folder, which is user-visible."""
    from loom import durations, paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    durations.measured(reload=True)
    assert durations.build_or_research_time("castle") == 200
    (tmp_path / "durations.json").write_text("{ not json", encoding="utf-8")
    durations.measured(reload=True)
    assert durations.build_or_research_time("castle") == 200
    try:
        (tmp_path / "durations.json").write_text(
            '{"buildings": {"castle": "soon"}}', encoding="utf-8")
        durations.measured(reload=True)
        assert durations.build_or_research_time("castle") == 200
    finally:
        durations.measured(reload=True)


def test_one_game_with_an_opinion_is_not_a_measurement():
    """A median over two samples is two games, not a habit."""
    from tools.measure_durations import personal_times
    from loom import durations
    plenty = [40] * durations.ENOUGH_SAMPLES
    kept, thin = personal_times({
        ("mill", "building"): plenty,
        ("castle", "building"): [80, 120],
        ("loom", "technology"): plenty,
    })
    assert kept == {"mill": 40}
    assert "castle" in thin
    assert "loom" not in kept, "a technology was written out as a building"


def _chart_tabs(window):
    """Every ChartTab the window actually holds, found by walking it.

    Walked rather than listed, for the reason the chart registry test
    already gives: a hand-written list agrees with the window on the day
    it is written and silently stops covering the tab somebody adds
    afterwards. This is the test for a bug that was exactly that shape.
    """
    return [tab for tab in window.findChildren(statsview.ChartTab)]


def test_attaching_a_record_redraws_everything_that_shows_one(stats_dir,
                                                              monkeypatch):
    """The fault: the record landed on disk and the screen kept the old
    answer until the selection was re-run by hand.

    `_add_record` refreshed the accuracy label and the button. The record
    feeds nine things, five of them ChartTabs, so every violet series
    stayed missing and a user reported the charts as broken. Asserted by
    walking the window's own tabs, so a tab added later is covered
    without anyone remembering to add it here.
    """
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    monkeypatch.setattr(statsview.replay, "match",
                        lambda p, available=None: statsview.replay.Match(
                            type("R", (), {"path": pathlib.Path("r.aoe2record")}),
                            statsview.replay.CERTAIN, "one", None))
    monkeypatch.setattr(statsview.replay, "harvest", lambda p: StoredTruth())
    monkeypatch.setattr(statsview.QMessageBox, "information",
                        staticmethod(lambda *a, **k: None))

    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    tabs = _chart_tabs(window)
    assert tabs, "the window has no charts to check"
    assert all((tab.view.data or {}).get("record") is None for tab in tabs)

    window._add_record()

    for tab in tabs:
        assert (tab.view.data or {}).get("record"), \
            "a chart is still showing the game as it was before the record"


def test_the_banner_and_the_button_never_disagree(stats_dir):
    """Two controls for one action, so they are driven by one function.

    The banner exists because a missing record is missing from every
    chart at once while the button for it lives on the tenth tab. That is
    only safe while nothing can make them say different things.
    """
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    assert window.banner.isVisibleTo(window), "nothing offered a record"
    assert window.add_record_button.isEnabled()

    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {"path": "r.aoe2record", "duration": 900,
                      "header": None, "apm": None, "villagers_ordered": None}
    path.write_text(json.dumps(data), encoding="utf-8")
    window.reload(path)
    assert not window.banner.isVisibleTo(window), \
        "the banner outlived the thing it was asking for"
    assert not window.add_record_button.isEnabled()


def test_the_scan_report_leads_with_what_somebody_can_act_on(stats_dir):
    """A bulk operation nobody reads the result of has finished silently.

    The two actionable outcomes lead even when they are the smallest
    numbers: a report opening with 206 games that never had a record
    buries the three that need a person to choose.
    """
    from collections import Counter
    outcomes = Counter({statsview.ATTACHED: 2,
                        statsview.NO_RECORD: 206,
                        statsview.NEEDS_A_PERSON: 3,
                        statsview.UNREADABLE: 1})
    said = statsview.scan_report(outcomes, 212, 212, stopped=False)
    assert said.index("could not be decided") < said.index("no recorded game")
    paragraphs = [line for line in said.splitlines() if line.strip()]
    assert "Attached 2" in paragraphs[1], "the count did not lead"
    # A bucket at zero is left out entirely rather than reported as none.
    quiet = statsview.scan_report(Counter({statsview.ATTACHED: 1}), 1, 1,
                                 stopped=False)
    assert "unreadable" not in quiet and "could not be read" not in quiet
    # Stopping is not failing, and it says how far it got.
    stopped = statsview.scan_report(Counter(), 4, 58, stopped=True)
    assert "4 of 58" in stopped


def test_a_race_and_an_absence_are_different_outcomes():
    """The classifier must not fold "too fresh to read" into "no record",
    for the same reason `replay.match` must not: one is worth trying
    again in a few seconds and the other never will be."""
    assert statsview.scan_outcome("added") == statsview.ATTACHED
    assert statsview.scan_outcome("already has its recorded game") \
        == statsview.ATTACHED
    assert statsview.scan_outcome(
        "the game is still writing that file - try again in a few"
        " seconds, once it has finished") == statsview.WAITING
    assert statsview.scan_outcome("no recorded game was running then") \
        == statsview.NO_RECORD
    assert statsview.scan_outcome("2 recorded games were running then") \
        == statsview.NEEDS_A_PERSON
    assert statsview.scan_outcome(
        "could not read that recorded game: ValueError") \
        == statsview.UNREADABLE


def test_the_scan_button_wears_the_records_own_colour(stats_dir):
    """It is a record control, so it is violet - the same violet every
    series the recorded game contributes is drawn in. Pinned against
    RECORD_COLOR rather than against a literal, so the button follows if
    that colour is ever retuned."""
    window = statsview.StatsWindow()
    style = window.scan_button.styleSheet()
    assert statsview.css_rgb(statsview.RECORD_COLOR) in style
    # Scoped by object name: a bare `border:` is inherited by a widget's
    # children, which is how the witness checkboxes once wore their
    # group's border.
    assert "#scanButton" in style
    assert window.scan_button.objectName() == "scanButton"


def test_a_popped_out_chart_lands_on_the_screen_its_parent_is_on(stats_dir,
                                                                 monkeypatch):
    """The fault: a pop-out was given a size and no position at all.

    A window with no position asked for is not placed neutrally, it is
    placed by somebody else's default - and with the statistics window on
    a second monitor that default put the chart back on the primary
    desktop, partly off the edge of it.

    A second monitor cannot be conjured under the offscreen platform, so
    the screen this window is on is stated instead. That is the right
    thing to fake: the bug was never in the arithmetic, it was in which
    screen's work area the arithmetic was handed.
    """
    write_game(stats_dir, "2026-08-25_010714_g.json")
    # A monitor to the RIGHT of the primary one, the case that broke.
    second = (2560, 0, 5119, 1439)
    monkeypatch.setattr(statsview.placement, "work_area",
                        lambda widget: second)

    window = statsview.StatsWindow()
    window.refresh()
    window.games.setCurrentRow(0)
    window.move(2600, 100)
    window.resize(1200, 800)

    window._pop_out(("villagers",), "Society", None)
    popped = next(iter(window._popouts.values()))

    left, top, right, bottom = second
    assert popped.x() >= left, "the chart went back to the primary screen"
    assert popped.x() + popped.width() <= right + 1, "it hangs off the edge"
    assert top <= popped.y() <= bottom, "it is off the top or bottom"
    # And near the window it came from, not merely somewhere legal.
    assert abs(popped.y() - window.y()) <= window.height()


def test_beside_flips_rather_than_hanging_off_a_second_monitor():
    """The arithmetic, at coordinates a second monitor actually uses.

    Right by preference, left when the right would hang off - and the
    work area passed MUST be the anchor's own screen, which is the whole
    reason this moved out of launcher.py where only one window used it.
    """
    from loom.placement import beside
    second = (2560, 0, 5119, 1439)
    # Room to the right: it goes there.
    x, _y = beside((2600, 100, 800), (900, 560), second)
    assert x == 2600 + 800 + 12
    # No room: it flips to the left of the anchor rather than off-screen.
    x, _y = beside((4200, 100, 800), (900, 560), second)
    assert x == 4200 - 900 - 12
    # Wider than the space left: clamped on, never half off.
    x, _y = beside((2560, 100, 100), (4000, 560), second)
    assert x >= 2560


# ---- the post-game inventory, and the line it must not cross -------------

def test_the_inventory_does_not_move_reader_accuracy(stats_dir):
    """The guarantee the whole design rests on.

    The unit join happens in the VIEW - `record_orders` reads the record
    section directly and never touches `loom.events`. That is what makes
    "Reader accuracy is untouched" a fact rather than a hope, and if the
    join ever leaks into the sightings this is what says so.
    """
    write_game(stats_dir, "2026-08-25_010714_g.json")
    path = stats_dir / "2026-08-25_010714_g.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {"path": "r.aoe2record", "duration": 600,
                      "header": None, "apm": None, "villagers_ordered": None,
                      "builds": {"barracks": [120]}, "researches": {},
                      "queued": {"unit_4": 31}, "ages": {}}
    before = statsview.accuracy_rows(data)
    statsview.postgame_html(data)          # the whole view, rendered
    after = statsview.accuracy_rows(data)
    assert [d.subject for d in before["overfired"]] == \
        [d.subject for d in after["overfired"]]
    assert [d.subject for d in before["unread"]] == \
        [d.subject for d in after["unread"]]


def test_a_unit_the_record_stored_as_an_id_still_gets_its_count():
    """`replay.UNITS` names only the villager, so the record carries
    `unit_4` rather than `archer` - which read as an archer nobody
    ordered until the view learned to translate. Nine subjects on one
    real game, every one of them a unit the player obviously trained."""
    orders = statsview.record_orders(
        {"queued": {"unit_4": 31, "unit_39": 57, "villager": 87}})
    assert orders["archer"] == "ordered ×31"
    assert orders["cavalryarcher"] == "ordered ×57"
    # A COUNT, never a time. The record tracks trained units as a running
    # total with no per-unit clock, so a time here would be invented.
    assert ":" not in orders["archer"]


def test_three_spellings_of_one_unit_are_one_row():
    """The feed says `cavalry_archer`, the queue's template is
    `cavalryarcher`, and the record's id table agrees with the queue.
    Joined on the raw string, a unit the player really trained sits in
    the pile with nothing beside it - the exact false accusation this
    view exists to prevent."""
    game = {"queued": {"cavalryarcher": 500},
            "events": [[520, "created:cavalry_archer"]]}
    units = dict(statsview.inventory_rows(
        {"game": game,
         "record": {"queued": {"unit_39": 57}}}))["Units"]
    labels = [row[0] for row in units]
    assert labels.count("cavalry archer") == 1, labels
    assert all(row[2] == "ordered ×57" for row in units if row[2])


def test_what_the_record_cannot_name_is_not_filed_as_unbacked(stats_dir):
    """A relic pickup is not an order and the record has no vocabulary
    for one. Putting it under "nothing in the record matches these"
    would be absence dressed as refutation - the record was never asked,
    so its silence is not evidence."""
    game = {"events": [[100, "picked_up:relic_picked_up"]],
            "queued": {"careening": 300}}
    data = {"game": game, "record": {"builds": {}, "researches": {},
                                     "queued": {}}}
    inventory = dict(statsview.inventory_rows(data))
    listed = [row[0] for rows in inventory.values() for row in rows]
    assert "relic picked up" not in listed
    assert "relic_picked_up" in [s.subject
                                 for s in statsview.unmatched_kinds(data)]


def test_the_unbacked_heading_states_evidence_not_a_verdict():
    """The record holds ORDERS, so a cancelled foundation and an
    abandoned research land under this heading legitimately. Calling
    them misreads is the verdict Reader accuracy delivers under a much
    narrower rule, and it is not earned here."""
    said = statsview.NOTHING_MATCHES.lower()
    for verdict in ("misread", "wrong", "phantom", "error", "invented",
                    "false", "bug"):
        assert verdict not in said, f"{verdict!r} is a verdict, not evidence"
    assert "record" in said


def test_the_loom_column_always_says_what_it_is():
    """Loom is shipping. Somebody reading `careening` in a game with no
    ships has to be told what the left column is, or the honest answer -
    these are raw reader output - reads as a program making things up."""
    for has_record in (True, False):
        said = statsview.loom_disclaimer(has_record)
        assert "may or may not" in said
    # With no record every row is unverified and no empty cell says so,
    # which is why that case says more rather than less.
    assert len(statsview.loom_disclaimer(False)) > 120
    assert "attach" in statsview.loom_disclaimer(False).lower()


# ---- attaching the last few records when the window opens ---------------
#
# The launcher attaches a record when the overlay exits, believing the
# match is then over. Measured on a real game it is not: the stats file
# landed at 12:19:20 and the game was still writing its record at 12:34:37,
# fifteen minutes later. replay.py rightly refuses a file mid-write, the
# launcher's one retry fires 35 seconds after the overlay exits, and
# nothing asks again - so two of the author's six most recent games had no
# record while matching CERTAINLY when asked later.

def _drain(window, app, limit=40):
    """Run the single-shot chain to completion, as the event loop would."""
    for _ in range(limit):
        if window._scanning is None:
            return
        app.processEvents()
    raise AssertionError("the attach chain never finished")


def test_opening_the_window_attaches_the_last_few_records(
        stats_dir, app, monkeypatch):
    for name in ("2026-08-01_a.json", "2026-08-02_b.json",
                 "2026-08-03_c.json"):
        write_game(stats_dir, name)
    tried = []
    monkeypatch.setattr(statsview, "enrich_with_record",
                        lambda path, **kw: tried.append(path) or "added it")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])

    window = statsview.StatsWindow()
    window._attach_recent()
    _drain(window, app)

    # Filtered to this test's own directory: any StatsWindow left shown by
    # an earlier test still has a chain pending, and processEvents drains
    # everyone's. That is the intended product behaviour and a test that
    # asserted on the raw list would be asserting about its neighbours.
    mine = [p.name for p in tried if p.parent == stats_dir]
    assert mine == ["2026-08-03_c.json", "2026-08-02_b.json",
                    "2026-08-01_a.json"]


def test_it_stops_at_the_bound_rather_than_reading_the_history(
        stats_dir, app, monkeypatch):
    """The whole history behind the button is a deliberate decision, not an
    oversight: 58 of 274 past games have a record waiting, at about two
    seconds each. Two minutes is fine to ask for and wrong to impose."""
    for day in range(1, statsview.ATTACH_ON_OPEN + 4):
        write_game(stats_dir, f"2026-08-{day:02d}_g.json")
    tried = []
    monkeypatch.setattr(statsview, "enrich_with_record",
                        lambda path, **kw: tried.append(path) or "added it")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])

    window = statsview.StatsWindow()
    window._attach_recent()
    _drain(window, app)

    assert len([p for p in tried if p.parent == stats_dir])         == statsview.ATTACH_ON_OPEN


def test_a_game_with_no_record_is_not_re_read_every_time(
        stats_dir, app, monkeypatch):
    """Reading a record costs about two seconds. A game that has none would
    otherwise pay that on every open, forever.

    Remembered in memory and not written into the stats file: a "nothing
    found" on disk would freeze out a record that settles later, and "I
    looked once and found nothing" is not "there is nothing"."""
    write_game(stats_dir, "2026-08-01_a.json")
    calls = []
    monkeypatch.setattr(
        statsview, "enrich_with_record",
        lambda path, **kw: calls.append(path) or "no recorded game was running")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])

    window = statsview.StatsWindow()
    window._attach_recent()
    _drain(window, app)
    window._attach_recent()
    _drain(window, app)

    assert len([p for p in calls if p.parent == stats_dir]) == 1

    # A new window is a new question - nothing was written to disk.
    again = statsview.StatsWindow()
    again._attach_recent()
    _drain(again, app)
    assert len([p for p in calls if p.parent == stats_dir]) == 2


def test_it_says_nothing_and_does_not_borrow_the_button(
        stats_dir, app, monkeypatch):
    """Nobody asked for this one, so nobody is owed a dialog - and the scan
    button belongs to the scan a person started."""
    write_game(stats_dir, "2026-08-01_a.json")
    monkeypatch.setattr(statsview, "enrich_with_record",
                        lambda path, **kw: "added it")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])
    shown = []
    monkeypatch.setattr(statsview.QMessageBox, "information",
                        lambda *a, **k: shown.append(a))

    window = statsview.StatsWindow()
    before = window.scan_button.text()
    window._attach_recent()
    _drain(window, app)

    assert shown == [], "an unasked-for attach must not raise a dialog"
    assert window.scan_button.text() == before


def test_a_game_that_already_has_its_record_is_left_alone(
        stats_dir, app, monkeypatch):
    write_game(stats_dir, "2026-08-01_a.json")
    path = stats_dir / "2026-08-01_a.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["record"] = {key: None for key in statsview.RECORD_KEYS}
    path.write_text(json.dumps(data), encoding="utf-8")

    tried = []
    monkeypatch.setattr(statsview, "enrich_with_record",
                        lambda p, **kw: tried.append(p) or "added it")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])

    window = statsview.StatsWindow()
    window._attach_recent()
    _drain(window, app)
    assert [p for p in tried if p.parent == stats_dir] == []


def test_it_stands_aside_for_a_scan_a_person_started(
        stats_dir, app, monkeypatch):
    """One chain, one owner. Two of them interleaving would judge two games
    against different views of the disk."""
    write_game(stats_dir, "2026-08-01_a.json")
    monkeypatch.setattr(statsview.replay, "records", lambda **kw: [])
    window = statsview.StatsWindow()
    import collections
    window._scanning = {"todo": [], "available": [], "done": 0,
                        "outcomes": collections.Counter(), "attached": []}
    window._attach_recent()
    assert window._scanning["todo"] == [], "it must not take over the scan"


def test_an_age_gets_one_card_however_often_the_build_names_it(stats_dir):
    """The reported fault: two Feudal cards on one match, disagreeing.

    An age arrives ONCE. The build names it twice - "Click Feudal at 22
    pop" is the instruction, "In Feudal Age: build a market" is a
    heading - and both drew a card against the same arrival. Because
    their planned times differ the windows differ, so one read "on time"
    and the other "2s late" about the same moment.

    There was already a guard for this whose own comment names the
    heading form. It could not reach it: it asks whether EVERY subject
    is a technology, and "In Feudal Age: build a market" fails that the
    moment it also names the market.
    """
    import collections
    build = {
        "name": "Two Mentions",
        "build_order": [
            {"villager_count": 20, "time": "8:20",
             "resources": {"food": 10, "wood": 10, "gold": 0, "stone": 0},
             "notes": ["Click @age/FeudalAgeIconDE.webp@ at 22 pop"]},
            {"villager_count": 24, "time": "10:30",
             "resources": {"food": 10, "wood": 10, "gold": 4, "stone": 0},
             "notes": ["In @age/FeudalAgeIconDE.webp@: build"
                       " @building/Market.webp@"]},
        ],
    }
    (stats_dir / "builds").mkdir(exist_ok=True)
    (stats_dir / "builds" / "twomentions.json").write_text(
        json.dumps(build), encoding="utf-8")

    from loom import build_order, paths as loom_paths
    order = build_order.BuildOrder(build)
    import unittest.mock as mock
    with mock.patch.object(build_order.BuildOrder, "load_by_name",
                           staticmethod(lambda stem: order)):
        rows = statsview.plan_versus_actual(
            "twomentions", {"ages": [[700, "reached", 2]], "events": []}, None)

    ages = collections.Counter(r.name for r in rows if "age" in (r.name or ""))
    assert ages == collections.Counter({"feudal age": 1}), ages
    # And the heading is not thrown away with the duplicate - it is a real
    # instruction about a market and should say so.
    assert any("market" in (r.name or "") for r in rows), \
        f"the heading's own subject vanished: {[r.name for r in rows]}"


def test_an_age_row_wears_its_own_icon_not_the_cards_first(stats_dir):
    """A row's picture has to be of the thing the row is about.

    "Sell 100 [stone] and 100 [wood], click [Castle Age]" put a STONE on
    a row labelled "castle age". The age icon was in the card all along,
    third, and the picker took whichever icon came first - so the caption
    and the image disagreed about what the row meant.
    """
    build = {
        "name": "Late Icon",
        "build_order": [
            {"villager_count": 24, "time": "10:30",
             "resources": {"food": 10, "wood": 10, "gold": 4, "stone": 0},
             "notes": ["Sell 100 @resource/Aoe2de_stone.webp@, click"
                       " @age/CastleAgeIconDE.webp@"]},
        ],
    }
    from loom import build_order
    from loom.checklist import token_subject
    import unittest.mock as mock

    order = build_order.BuildOrder(build)
    with mock.patch.object(build_order.BuildOrder, "load_by_name",
                           staticmethod(lambda stem: order)):
        rows = statsview.plan_versus_actual(
            "lateicon", {"ages": [[900, "reached", 3]], "events": []}, None)

    ages = [r for r in rows if "age" in (r.name or "")]
    assert len(ages) == 1, [r.name for r in rows]
    assert token_subject(ages[0].token) == "castle_age", (
        f"the row wears {ages[0].token}, which is not what it is about")


# ---- the order gets its own verdict, not the completion's ---------------

def _row(planned, ordered, observed=None, shift=0):
    ready = planned + shift
    return statsview.PlanRow(
        "mill", None, planned, observed,
        statsview.expected_window(planned, None, shift, 35),
        ordered, True,
        (ready - statsview.EARLY_GRACE,
         ready + statsview.REACTION + statsview.LATE_TAIL))


def test_the_order_is_judged_against_the_card_not_the_finish():
    """The whole reason it has its own verdict.

    plan_verdict asks when a thing FINISHED, against a window padded by
    however long it takes to build. A Mill clicked four seconds after its
    card and finished ninety seconds later is late as a Mill and prompt
    as a decision, and painting the completion's verdict on the moment
    the player clicked would say they were late when they were not.
    """
    row = _row(planned=390, ordered=394, observed=700)
    assert statsview.order_verdict(row) == "on time", "the click was prompt"
    assert statsview.plan_verdict(row) == "late", "the Mill was not"
    # The two disagreeing is the POINT, not a fault to be reconciled.
    assert statsview.order_verdict(row) != statsview.plan_verdict(row)


def test_the_next_card_does_not_excuse_a_late_order():
    """Measured, and it is why this window is not expected_window.

    That function closes at the NEXT card, because the game retires a
    card while its work is still in flight - an argument about
    completions. An order is due at its own card, and a Castle Age
    clicked 182 seconds after it read "on time" purely because the
    following card sat 180 seconds away.
    """
    assert statsview.order_verdict(_row(planned=630, ordered=812)) == "late"
    assert statsview.order_verdict(_row(planned=630, ordered=660)) == "on time"
    assert statsview.order_verdict(_row(planned=630, ordered=500)) == "early"


def test_an_age_slip_moves_the_order_window_too():
    """Half a build cannot be attempted before its age arrives, so
    blaming a Castle for a late Castle Age is blaming the player for the
    same delay twice - the rule expected_window already follows."""
    assert statsview.order_verdict(
        _row(planned=810, ordered=985, shift=169)) == "on time"
    assert statsview.order_verdict(
        _row(planned=810, ordered=985)) == "late"


def test_no_order_means_no_verdict_rather_than_a_guess():
    """A mark for a time nobody knows would be an invention, and a
    verdict about one doubly so."""
    assert statsview.order_verdict(_row(planned=390, ordered=None)) is None
    bare = statsview.PlanRow("mill", None, 390, None, (0, 0), 400, True)
    assert bare.ordered_expected is None
    assert statsview.order_verdict(bare) is None


def test_the_key_says_what_the_ring_means():
    """A colour on the chart that the legend does not name is a colour
    the reader has to guess at."""
    assert "ring" in statsview.ORDER_KEY_LABEL
    for verdict in ("early", "on time", "late"):
        assert verdict in statsview.ORDER_KEY_LABEL
