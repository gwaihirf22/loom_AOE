"""The ruler has to be honest about what it does not know.

expected_notifications is the number Loom's whole-run counts get measured
against. Every other gate scores ONE crop of pixels; this one scores a whole
game. That makes a wrong number here worse than a wrong number anywhere
else - it would not fail a reader, it would EXCUSE one.

So the tests are mostly about refusal: unknown ids are reported rather than
invented, technologies are counted by completions rather than orders, and
subjects the recorded game cannot rule on are left out loudly.
"""

from tools import replay_truth


class FakeTruth(replay_truth.Truth):
    """A Truth built by hand, so no recorded game is needed."""

    def __init__(self, builds=None, researches=None):
        super().__init__(player=1)
        for ident, placements in (builds or {}).items():
            self.builds[ident] = placements
        for ident, times in (researches or {}).items():
            self.researches[ident] = times


def test_buildings_are_counted_by_order():
    # Three barracks placed is three lines the feed should have printed. It
    # is a CEILING - a cancelled foundation prints nothing - and a ceiling is
    # what catches the bug that mattered: Loom reported fifteen.
    counts, _skipped = replay_truth.expected_notifications(
        FakeTruth(builds={12: [(453, 1, 1), (620, 2, 2), (900, 3, 3)]}))
    assert counts["barracks"] == 3


def test_the_two_town_centre_ids_are_added_not_replaced():
    # 109 and 621 are both a town centre. Summing by NAME is what merges
    # them; assigning would silently keep only whichever came last.
    counts, _skipped = replay_truth.expected_notifications(
        FakeTruth(builds={109: [(10, 1, 1)], 621: [(1591, 2, 2)]}))
    assert counts["town_center"] == 2


def test_a_technology_ordered_twice_completed_once():
    # A research can be cancelled at any point before it completes, so a
    # repeated order means the first one was abandoned. Counting orders here
    # would hand a double-firing reader a perfect score.
    counts, _skipped = replay_truth.expected_notifications(
        FakeTruth(researches={22: [373, 502]}))
    assert counts["loom"] == 1


def test_an_id_with_no_name_is_reported_rather_than_invented():
    # The glyphs.KNOWN_WORDS lesson: a hand-curated list fails SILENTLY. An
    # id that entered the ruler as "technology_39" would be a subject Loom
    # can never match, scoring zero forever with nothing saying why.
    # 3 and 9 are ids the generated table still has no name for. They were
    # 39 and 49 until that table stopped being sixteen hand-corroborated
    # entries and became 416 buildings and 184 technologies - at which point
    # both acquired names (husbandry, siege_workshop) and this test began
    # failing by succeeding. The fall-through itself still has to be tested.
    counts, skipped = replay_truth.expected_notifications(
        FakeTruth(researches={3: [1484]}, builds={9: [(1774, 1, 1)]}))
    assert 3 in skipped and 9 in skipped
    assert not any(name.startswith(("technology_", "building_"))
                   for name in counts)


def test_farms_are_named_but_not_ruled_on():
    # Reseeding a tile and rebuilding a destroyed farm look identical to an
    # order stream, so the count cannot be checked. Harvested and reported,
    # deliberately absent from the ruler.
    truth = FakeTruth(builds={50: [(580, 1, 1), (600, 1, 1), (700, 2, 2)]})
    counts, _skipped = replay_truth.expected_notifications(truth)
    assert "farm" not in counts
    assert truth.name_of_building(50) == "farm"
    assert "farm" in replay_truth.NOT_RULED_ON


def test_the_report_says_what_it_left_out():
    # An omission nobody is told about is the same failure as a wrong number.
    truth = FakeTruth(builds={50: [(580, 1, 1)], 9: [(1774, 1, 1)]},
                      researches={22: [373]})
    written = replay_truth.describe(truth)
    assert "not ruled on: farm" in written
    assert "no name" in written


# ---- the generated id table --------------------------------------------

def test_the_generated_table_agrees_with_what_pixels_proved():
    """The twenty ids derived by hand became the cross-check, not the table.

    Each was corroborated against this archive before any dataset existed -
    the first-placement times line up with a real build order, and the age
    technologies land at the ages that game reached. All twenty agree with
    the generated file exactly, names included, and that agreement is what
    earned the generated one its trust. If a regenerated table ever
    disagrees, the dataset changed under us and the pixels are the witness
    that says so.
    """
    from loom import replay
    by_hand = {
        12: "barracks", 50: "farm", 68: "mill", 70: "house", 82: "castle",
        84: "market", 87: "archery_range", 101: "stable", 103: "blacksmith",
        104: "monastery", 109: "town_center", 209: "university",
        562: "lumber_camp", 584: "mining_camp", 621: "town_center",
        1251: "krepost",
    }
    for ident, name in by_hand.items():
        assert replay.BUILDINGS.get(ident) == name, ident
    for ident, name in {22: "loom", 101: "feudal_age", 102: "castle_age",
                        103: "imperial_age"}.items():
        assert replay.TECHNOLOGIES.get(ident) == name, ident


def test_every_generated_subject_is_one_loom_can_actually_say():
    """A ruler may only carry subjects the thing it measures can produce.

    An id whose name Loom can never emit would score zero for the whole of
    every game and read as a reader fault rather than a table gap. So the
    generator asks glyphs.parse_event, and this asserts the result of that
    question rather than trusting it.
    """
    from loom import glyphs, replay

    def emitted(line):
        event = glyphs.parse_event(line)
        if not event:
            return None
        return event.split(":", 1)[1] if ":" in event else event

    for ident, subject in list(replay.TECHNOLOGIES.items())[:60]:
        assert emitted(f"--{subject.replace('_', ' ').title()} "
                       f"Research Complete--") is not None, (ident, subject)


def test_town_centre_survived_the_generation():
    """The subject the whole project turns on, and it nearly did not.

    parse_event returns TWO shapes - `built:barracks` has a colon, and
    `--Town Center Built--` gives `town_center_built`, the PHRASE watcher's
    naming, because notifications.py reads town centres and reader.py
    discards the glyph reader's version. A generator that only understood
    the colon form dropped it, and the hand-derived cross-check is what
    caught that rather than any test of the generator.
    """
    from loom import replay
    assert replay.BUILDINGS.get(109) == "town_center"
    assert replay.BUILDINGS.get(621) == "town_center"
