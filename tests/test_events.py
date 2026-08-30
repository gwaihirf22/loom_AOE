"""
Loom — the pipe between the readers and everything downstream.

These pin the one rule that already exists three times by hand elsewhere:
two witnesses to the same event are RECONCILED, never added. Adding the
Town Centre feed line to the queue's high-water count invented a third
Town Centre in a live game, and this module exists so that mistake has
one place to be prevented instead of four.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import events


def game(queued=None, feed=()):
    return {"queued": dict(queued or {}),
            "events": [list(entry) for entry in feed]}


def test_both_witnesses_arrive_separately():
    raw = events.from_recording(
        game(queued={"loom": 340}, feed=[(350, "researched:loom")]))
    assert [(e.subject, e.witness) for e in raw] == [
        ("loom", events.QUEUE), ("loom", events.FEED)]


def test_a_feed_line_with_no_subject_is_not_a_sighting():
    """"attacked" and "wild_animals" are game-state events, not things
    happening to a subject - they belong to whoever wants them raw."""
    raw = events.from_recording(
        game(feed=[(100, "attacked"), (100, "built:house")]))
    assert [e.subject for e in raw] == ["house"]


def test_a_subject_both_readers_saw_is_one_sighting_not_two():
    """The whole reason this module exists."""
    seen = events.for_statistics(
        game(queued={"archer": 680}, feed=[(700, "created:archer")]))
    assert len(seen) == 1
    assert seen[0].witnesses == {events.QUEUE, events.FEED}


def test_the_count_comes_from_the_feed_alone():
    """Reconcile by max, never by addition: the queue saw the same archer
    the feed announced, so its sighting must not add to the tally."""
    seen = events.for_statistics(
        game(queued={"archer": 680},
             feed=[(700, "created:archer"), (720, "created:archer")]))
    assert seen[0].count == 2, "the queue's sighting was added to the feed's"


def test_a_subject_only_the_queue_saw_admits_it_cannot_count():
    """The queue hides duplicate groups and never reports a completion, so
    it can say a thing happened and never how many times."""
    seen = events.for_statistics(game(queued={"galley": 900}))
    assert seen[0].count is None
    assert seen[0].witnesses == {events.QUEUE}


def test_times_are_taken_at_their_extremes():
    seen = events.for_statistics(
        game(queued={"archer": 680},
             feed=[(900, "created:archer"), (700, "created:archer")]))
    assert (seen[0].first, seen[0].last) == (680, 900)


def test_sightings_are_split_by_kind_and_the_unknown_stay_unknown():
    seen = events.for_statistics(
        game(queued={"loom": 300, "villager_male": 10, "no_such_thing": 20}))
    assert [s.subject for s in events.of_kind(seen, "technology")] == ["loom"]
    assert [s.subject for s in events.of_kind(seen, "unit")] == \
        ["villager_male"]
    assert [s.subject for s in events.of_kind(seen, "unknown")] == \
        ["no_such_thing"]


def test_an_empty_game_yields_nothing_rather_than_failing():
    assert events.for_statistics({}) == []


# ---- a clock that runs the game twice ----------------------------------

def test_the_longest_pass_wins_when_the_clock_runs_twice():
    """26 of 252 recorded games hold two passes of the clock, which drew
    as two lines crossing each other. Neither line is wrong; the file is,
    so pick the fuller pass rather than inventing a merged truth."""
    times = [99, 200, 300, 400, 529, 7, 100, 200, 300, 400, 500, 600]
    assert events.usable_run(times) == (5, 12)


def test_small_backward_steps_are_wobble_and_do_not_split():
    """The clock is read off the screen. A second or two backwards is the
    reader, not a new game - splitting on it shreds every series."""
    assert events.usable_run([219, 216, 240, 279, 277, 276, 300]) == (0, 7)


def test_a_clean_clock_is_left_entirely_alone():
    times = list(range(0, 100, 5))
    assert events.usable_run(times) == (0, len(times))


def test_an_empty_series_does_not_explode():
    assert events.usable_run([]) == (0, 0)


def test_a_tie_goes_to_the_later_pass():
    """Two equally complete passes: keep the one still being played when
    the file was written."""
    start, stop = events.usable_run([0, 10, 20, 0, 10, 20])
    assert (start, stop) == (3, 6)


# ---- the recorded game, joined as a third witness -----------------------

class FakeTruth:
    """A loom.replay.Truth, as far as events.from_record cares."""

    def __init__(self, builds=None, researches=None):
        self.builds = builds or {}
        self.researches = researches or {}

    def name_of_building(self, ident):
        return ident

    def name_of_tech(self, ident):
        return ident


def test_the_record_never_moves_a_time_loom_read_off_the_screen():
    """The record says a building was PLACED; the feed says it was BUILT.
    Different moments, so a placement may not drag a completion earlier -
    that would put a placement on screen wearing a completion's clothes."""
    seen = events.with_record(
        {"events": [[300, "built:mill"]]},
        FakeTruth(builds={"mill": [(120, 0, 0)]}))
    mill = seen[0]
    assert mill.first == 300, "the placement overwrote the completion"
    assert mill.ordered == (120,), "the placement was lost instead"
    assert mill.witnesses == {"feed", "record"}


def test_a_subject_only_the_record_holds_admits_it_was_never_read():
    """None here is the finding, not a gap: the player ordered it and Loom
    never saw it happen."""
    seen = events.with_record({}, FakeTruth(builds={"blacksmith": [(400, 0, 0)]}))
    smith = seen[0]
    assert smith.first is None and smith.count is None
    assert smith.ceiling == 1 and smith.ordered == (400,)


def test_reading_more_than_was_ever_ordered_convicts_the_reader():
    """Measured on a real game: the feed reader reported EIGHT lumber camps
    where the player ordered two. That is the counting bug CLAUDE.md says
    no per-frame gate can see."""
    seen = events.with_record(
        {"events": [[t, "built:lumber_camp"] for t in (100, 110, 120, 130)]},
        FakeTruth(builds={"lumber_camp": [(90, 0, 0), (95, 0, 0)]}))
    bad = events.disagreements(seen)
    assert [d.verdict for d in bad] == [events.OVERFIRED]
    assert bad[0].read == 4 and bad[0].ceiling == 2


def test_reading_fewer_is_reported_but_never_called_proof():
    """An order is not an event. A foundation can be cancelled and a
    research abandoned, so falling UNDER the ceiling is allowed - and
    collapsing "provably wrong" into "possibly missed" would be an
    assumption dressed as a reading."""
    seen = events.with_record(
        {}, FakeTruth(researches={"loom": [200]}))
    bad = events.disagreements(seen)
    assert [d.verdict for d in bad] == [events.UNREAD]


def test_a_subject_the_record_says_nothing_about_is_no_disagreement():
    """Units are trained without the record timing each one, and the feed
    names things the record never mentions. Silence is not conflict."""
    seen = events.for_statistics({"events": [[100, "built:house"]]})
    assert events.disagreements(seen) == []


def test_without_a_record_nothing_changes_at_all():
    """Every stats file written before today has no record beside it."""
    game = {"queued": {"archer": 300}, "events": [[400, "built:house"]]}
    assert events.with_record(game, None) == events.for_statistics(game)


# ---- how long things actually took ---------------------------------------

def test_a_duration_is_the_order_subtracted_from_the_announcement():
    """The one honest use of placement times. Alone they are no evidence
    about the notification reader; subtracted FROM a completion they
    measure something Loom cannot otherwise know at all."""
    seen = events.with_record(
        {"events": [[187, "built:barracks"]]},
        FakeTruth(builds={"barracks": [(130, 0, 0)]}))
    took = events.build_durations(seen)
    assert [d.seconds for d in took] == [57]


def test_a_subject_loom_miscounted_yields_no_duration_at_all():
    """One missed completion shifts every later order onto the next one's
    finish, and the errors GROW rather than cancelling. Measured: houses
    came back at 268, 480, 436 and 542 seconds on a game whose reader was
    over-firing - numbers that look like data and are pure drift.

    A duration is a difference between two readings, so it is no better
    than the worse of the two, and a drifted pair cannot be told from a
    slow build by looking at it."""
    seen = events.with_record(
        {"events": [[300, "built:house"]]},          # one read...
        FakeTruth(builds={"house": [(10, 0, 0), (20, 0, 0)]}))   # ...two built
    assert events.build_durations(seen) == []


def test_a_research_faster_than_physically_possible_is_a_bad_pairing():
    """Building time divides among builders; RESEARCH does not. A Town
    Centre researches Loom at one speed whatever else is happening, so the
    table time is a genuine floor and anything under it proves the pairing
    wrong rather than the player fast. Measured: "loom took 0s" and
    "feudal_age took 1s"."""
    impossible = events.with_record(
        {"events": [[201, "researched:loom"]]},
        FakeTruth(researches={"loom": [200]}))
    assert events.build_durations(impossible) == [], "1s of Loom was believed"

    real = events.build_durations(events.with_record(
        {"events": [[226, "researched:loom"]]},
        FakeTruth(researches={"loom": [200]})))
    assert [d.seconds for d in real] == [26]


def test_an_order_that_never_finished_is_not_given_a_duration():
    """A foundation can be cancelled and a research abandoned."""
    seen = events.with_record({}, FakeTruth(builds={"castle": [(400, 0, 0)]}))
    assert events.build_durations(seen) == []



# ---- which record of the queue the page is built from -------------------

def test_an_old_file_still_gets_its_queued_list():
    """A game recorded before episodes existed has only `queued`, and is
    not improved by being shown less than it actually recorded."""
    game = {"queued": {"knight": 300, "archer": 120}}
    assert events.queue_sightings(game) == [(120, "archer"), (300, "knight")]


def test_episodes_win_where_they_exist():
    """The whole point of the vote. `queued` believed one glance, which put
    45 subjects on a real Post-game page that the vote reduced to 20 - a
    fleet of dragon ships and fireships in a Fast Castle."""
    game = {
        "queued": {"knight": 300, "dragon_ship": 118, "fireship": 402},
        "episodes": [
            {"subject": "knight", "started": 300, "polls": 9},
            {"subject": None, "started": 118, "polls": 1},
            {"subject": None, "started": 402, "polls": 1},
        ],
    }
    assert events.queue_sightings(game) == [(300, "knight")]


def test_a_subject_is_reported_once_at_its_earliest_episode():
    """Deliberately the same shape `queued` had. Episodes can now say how
    MANY times a thing produced, which `queued` never could, but turning
    that on changes what every consumer downstream counts."""
    game = {"episodes": [
        {"subject": "archer", "started": 500, "polls": 6},
        {"subject": "archer", "started": 240, "polls": 6},
        {"subject": "archer", "started": 900, "polls": 6},
    ]}
    assert events.queue_sightings(game) == [(240, "archer")]


def test_an_empty_episode_list_is_not_a_missing_one():
    """A game where the queue was watched and nothing survived the vote is
    a game with nothing to report - not a game to fall back to the old
    believed-on-sight list for. Absent and empty are different answers."""
    game = {"queued": {"dragon_ship": 118}, "episodes": []}
    assert events.queue_sightings(game) == []


def test_villagers_stay_out_of_the_list():
    """The timeline tells that story second by second; a row here adds
    nothing. gamestats has always left them out of `queued` and the two
    sources must not disagree about it."""
    game = {"episodes": [
        {"subject": "villager_male", "started": 20, "polls": 9},
        {"subject": "villager_female", "started": 40, "polls": 9},
        {"subject": "knight", "started": 300, "polls": 9},
    ]}
    assert events.queue_sightings(game) == [(300, "knight")]
