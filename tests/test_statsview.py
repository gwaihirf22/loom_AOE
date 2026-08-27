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
    rows = statsview.game_rows(
        {"duration": 600, "max_villagers": 30, "tc_count": 2,
         "tc_idle_seconds": 12.0, "queued": {"knight": 500},
         "deaths": [[300, 2, True]], "attacks": [295]})
    labels = {label for label, _, _ in rows}
    assert "knight" in labels
    values = dict((label, value) for label, value, _ in rows)
    # Queue sightings are labelled as sightings, never as produced counts.
    assert values["knight"].startswith("first queued")
    assert values["villagers lost"] == "2 (2 to raids)"


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


def plan_for(monkeypatch, steps, game):
    from loom import build_order
    fake = type("B", (), {"steps": steps})()
    monkeypatch.setattr(build_order.BuildOrder, "load_by_name",
                        classmethod(lambda cls, name: fake))
    return statsview.plan_versus_actual("whatever", game)


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
                        lambda p: statsview.replay.Match(
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
                        lambda p: statsview.replay.Match(
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
    """The record has no idea a Town Centre was idle. Writing 0 there
    would be a claim it never made."""
    rows = dict((label, (read, rec))
                for label, read, rec in statsview.comparison_rows(
                    a_compared_game()))
    assert rows["TC idle time"][1] is None
    assert rows["eAPM"][0] is None, "Loom cannot compute eAPM and said it did"
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
    from PyQt6.QtWidgets import QCheckBox, QLabel
    tab = statsview.ChartTab(("villagers",))
    assert statsview.SCREEN not in tab.witness_boxes,         "the screen witness kept a checkbox its parts had replaced"
    assert set(tab.part_boxes) == {"villagers", "population", "cap"}

    row = tab.layout().itemAt(0).layout()
    group = next(row.itemAt(i).widget() for i in range(row.count())
                 if row.itemAt(i).widget() is not None
                 and row.itemAt(i).widget().layout() is not None)
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
