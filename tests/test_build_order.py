"""
Tests for loading build orders and working out which step the player is on.

Most of these use a small build defined here rather than a real one, so the
tests keep meaning what they say even if the shipped builds are edited. The
one exception checks that the build actually shipped is valid.
"""

# Developed with AI assistance (Claude), used as a pair programmer, tutor
# and debugger. Design, architecture, testing and integration by Paul Blake.

import pytest

from loom import paths
from loom.build_order import (BuildOrder, format_time, icon_to_words,
                              parse_time, split_notes)

# A deliberately small build with the awkward case built in: villager count 12
# appears on three consecutive steps, because a Town Center cannot train
# villagers while researching an age.
SAMPLE = {
    "name": "Test Build",
    "civilization": "Generic",
    "build_order": [
        {"villager_count": 6, "age": 1, "time": "1:15",
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0},
         "notes": ["Six Villagers to Sheep | Build 2 Houses"]},
        {"villager_count": 10, "age": 1, "time": "2:55",
         "resources": {"food": 6, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Four Villagers to Wood"]},
        {"villager_count": 12, "age": 1, "time": "3:45",
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Click Feudal Age"]},
        {"villager_count": 12, "age": 2, "time": "5:00",
         "resources": {"food": 8, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["In Feudal Age"]},
        {"villager_count": 15, "age": 2, "time": "6:00",
         "resources": {"food": 11, "wood": 4, "gold": 0, "stone": 0},
         "notes": ["Three Villagers to Farms"]},
    ],
}


@pytest.fixture
def build():
    return BuildOrder(SAMPLE)


# ---- reading the file --------------------------------------------------

def test_time_parsing():
    assert parse_time("7:30") == 450
    assert parse_time("1:02:03") == 3723
    assert parse_time("") is None
    assert parse_time("half past four") is None


def test_time_formatting_handles_negatives():
    """Pace deltas can be negative, so this must not produce '7:-30'."""
    assert format_time(450) == "7:30"
    assert format_time(-90) == "-1:30"


def test_icon_tokens_become_readable_words():
    """Real builds spell the game's tag three different ways, so the cleanup
    works by discarding meaningless words rather than stripping suffixes."""
    assert icon_to_words("animal/Boar_aoe2DE.webp") == "Boar"
    assert icon_to_words("resource/Aoe2de_wood.webp") == "Wood"
    assert icon_to_words("age/FeudalAgeIconDE.webp") == "Feudal Age"
    assert icon_to_words("lumber_camp/Lumber_camp_aoe2de.webp") == "Lumber Camp"
    # These two do not tidy into anything a player would recognize.
    assert icon_to_words("resource/MaleVillDE.webp") == "Villager"
    assert icon_to_words("resource/BerryBushDE.webp") == "Berries"


def test_notes_split_into_a_headline_and_extras():
    details, footnotes = split_notes(["Build 2 Houses | Then to Sheep | Lure Boar"])
    assert details == "Build 2 Houses"
    assert footnotes == ["Then to Sheep", "Lure Boar"]


def test_steps_load_with_their_villager_targets(build):
    first = build.steps[0]
    assert first.villager_count == 6
    assert first.time == 75
    assert first.villagers == {"food": 6, "wood": 0, "gold": 0, "stone": 0}
    assert first.details == "Six Villagers to Sheep"


# ---- which step am I on ------------------------------------------------

def test_the_active_step_is_the_first_one_not_finished(build):
    """Regression: the overlay used to show the last COMPLETED step.

    That is what the player has already done, so the instruction was always
    one beat behind their hands, and at the start of a game it said "waiting
    for the build to start" while they sat there wanting to be told something.
    """
    # Three villagers, ten seconds in: nothing is finished yet.
    assert build.active_step(3, 10).details == "Six Villagers to Sheep"
    assert build.completed_step(3, 10) is None


def test_the_same_villager_count_maps_to_different_steps_over_time(build):
    """The awkward case. Twelve villagers means three different things
    depending on the clock, so villager count alone cannot identify a step."""
    assert build.active_step(12, 200).details == "Click Feudal Age"
    assert build.active_step(12, 240).details == "In Feudal Age"
    assert build.active_step(12, 320).details == "Three Villagers to Farms"


def test_following_step_reads_one_further_ahead(build):
    assert build.following_step(3, 10).details == "Four Villagers to Wood"


def test_running_out_of_build_gives_nothing_rather_than_an_error(build):
    assert build.active_step(40, 9999) is None
    assert build.following_step(40, 9999) is None


# ---- expectations ------------------------------------------------------

def test_target_time_interpolates_between_steps(build):
    """Community builds group villagers, so there may be a step at 10 and the
    next at 12 with nothing for 11. Refusing to answer would be unhelpful."""
    assert build.target_time(10) == 175
    assert build.target_time(11) == pytest.approx(200)


def test_no_target_before_the_first_checkpoint(build):
    """Below the first step every count would map to the same time, which is
    a meaningless answer. Saying so is better than inventing one."""
    assert build.target_time(3) is None


def test_expected_villagers_is_the_inverse_of_target_time(build):
    assert build.expected_villagers(75) == 6
    assert build.expected_villagers(175) == 10
    assert build.expected_villagers(125) == pytest.approx(8)


# ---- validation --------------------------------------------------------

def test_validate_accepts_a_sound_build(build):
    assert build.validate() == []


def test_validate_reports_time_going_backwards():
    broken = {"name": "Broken", "build_order": [
        {"villager_count": 6, "time": "2:00", "notes": ["a"],
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0}},
        {"villager_count": 8, "time": "1:00", "notes": ["b"],
         "resources": {"food": 8, "wood": 0, "gold": 0, "stone": 0}},
    ]}
    problems = BuildOrder(broken).validate()
    assert any("backwards" in problem for problem in problems)


def test_validate_notices_villagers_that_do_not_add_up():
    """Not an error: villagers away constructing something legitimately break
    the sum. Worth mentioning, not worth refusing to load."""
    odd = {"name": "Odd", "build_order": [
        {"villager_count": 10, "time": "1:00", "notes": ["a"],
         "resources": {"food": 3, "wood": 3, "gold": 0, "stone": 0}},
    ]}
    problems = BuildOrder(odd).validate()
    assert any("add up to 6" in problem for problem in problems)


def test_validate_reports_missing_instructions():
    empty = {"name": "Empty", "build_order": [
        {"villager_count": 6, "time": "1:00", "notes": [],
         "resources": {"food": 6, "wood": 0, "gold": 0, "stone": 0}},
    ]}
    assert any("no instructions" in p for p in BuildOrder(empty).validate())


# ---- the build that actually ships -------------------------------------

def test_shipped_builds_are_valid():
    """Whatever is in builds/ should load and make sense."""
    for path in sorted(paths.BUILDS_DIR.glob("*.json")):
        loaded = BuildOrder.load(path)
        assert loaded.steps, f"{path.name} has no steps"
        assert loaded.validate() == [], f"{path.name}: {loaded.validate()}"
