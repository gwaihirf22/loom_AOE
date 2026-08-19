"""
Loom — finding one build order in a library that keeps growing.

Importing a build takes seconds now, so the drop-down stops answering the
question a player actually has: "what can I play as Mongols?".

Two rules here are worth more than the matching itself.

**A specific civilization includes the Generic builds.** Seven of the
thirteen builds shipped the day this was written are Generic, and a Generic
build is by definition playable as Mongols. Filtering them out would hide
most of the library and hide builds that work perfectly.

**The current choice survives every filter.** A drop-down that could drop
the selected build would silently move the selection onto a build the
player never picked, and Start would then run that one. It is the same
class of failure as a panel that quietly stops following the game: nothing
looks wrong, and the wrong thing happens.

Pure functions only - the launcher owns the widgets, this owns the rules,
so none of it needs a display.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom.build_order import (GENERIC_CIVILIZATION, BuildOrder,
                              available_builds, civilization_label,
                              civilization_names, civilizations,
                              filtered_builds, matches_civilization)


def build(name="A build", civilization="Generic", author="somebody"):
    return BuildOrder({
        "name": name,
        "civilization": civilization,
        "author": author,
        "build_order": [{"villager_count": 6, "time": "1:15",
                         "notes": ["do the thing"]}],
    })


LIBRARY = [
    ("mon15popscouts", build("MON 15 Pop Scouts", "Mongols", "Morley Games")),
    ("arenafastcastle", build("Arena Fast Castle Boom", "Generic", "Hera")),
    ("19popdrush", build("19 Pop Feudal Drush", "Generic", "Hera")),
    ("benphosphorus", build("BEN Phosphorus rush", "Bengalis", "Phosphorus")),
]


# ---- what a typed word matches --------------------------------------------

def test_no_query_shows_the_whole_library_in_order():
    assert filtered_builds(LIBRARY) == LIBRARY


def test_a_word_matches_the_build_s_name():
    found = filtered_builds(LIBRARY, "drush")

    assert [stem for stem, _build in found] == ["19popdrush"]


def test_a_word_matches_the_author():
    found = filtered_builds(LIBRARY, "hera")

    assert {stem for stem, _b in found} == {"arenafastcastle", "19popdrush"}


def test_a_word_matches_the_civilization():
    found = filtered_builds(LIBRARY, "mongols")

    assert [stem for stem, _b in found] == ["mon15popscouts"]


def test_a_word_matches_the_file_name():
    """The stem is what the player saved and may be the only name they
    remember - the build can call itself something else inside."""
    found = filtered_builds(LIBRARY, "benphos")

    assert [stem for stem, _b in found] == ["benphosphorus"]


def test_matching_ignores_case():
    assert filtered_builds(LIBRARY, "HERA") == filtered_builds(LIBRARY, "hera")


def test_every_word_has_to_match_so_more_words_narrow():
    """The property that makes typing more feel like searching harder."""
    assert len(filtered_builds(LIBRARY, "hera")) == 2
    assert [stem for stem, _b in filtered_builds(LIBRARY, "hera arena")] \
        == ["arenafastcastle"]


def test_a_query_matching_nothing_shows_nothing():
    assert filtered_builds(LIBRARY, "trebuchet") == []


# ---- civilizations ---------------------------------------------------------

def test_a_civilization_brings_its_own_builds_and_the_generic_ones():
    """The rule that makes the filter answer the player's real question."""
    found = filtered_builds(LIBRARY, civilization="Mongols")

    assert {stem for stem, _b in found} == {
        "mon15popscouts", "arenafastcastle", "19popdrush"}


def test_asking_for_generic_asks_narrowly():
    """Generic is not a civilization, it is the builds that suit all of
    them - so it must not drag in the Mongol and Bengali ones."""
    found = filtered_builds(LIBRARY, civilization=GENERIC_CIVILIZATION)

    assert {stem for stem, _b in found} == {"arenafastcastle", "19popdrush"}


def test_no_civilization_means_all_of_them():
    assert filtered_builds(LIBRARY, civilization=None) == LIBRARY


def test_search_and_civilization_narrow_together():
    found = filtered_builds(LIBRARY, "hera", "Mongols")

    assert {stem for stem, _b in found} == {"arenafastcastle", "19popdrush"}


def test_the_civilization_list_comes_from_the_library():
    """Never a list of the game's civilizations: that would offer forty
    entries with nothing behind most of them, and age every time the game
    adds one."""
    assert civilizations(LIBRARY) == ["Generic", "Bengalis", "Mongols"]


def test_generic_leads_the_list_and_the_rest_are_sorted():
    assert civilizations(LIBRARY)[0] == GENERIC_CIVILIZATION


def test_a_library_with_no_generic_builds_does_not_invent_one():
    only = [("a", build(civilization="Mongols"))]

    assert civilizations(only) == ["Mongols"]


# ---- the field itself ------------------------------------------------------

def test_a_build_naming_several_civilizations_matches_any_of_them():
    """The format allows a list where every build I have seen uses a
    string. Left alone it would render as "['Mayans', 'Aztecs']" in the
    drop-down and match nothing anybody typed."""
    pair = [("twociv", build(civilization=["Mayans", "Aztecs"]))]

    assert matches_civilization(pair[0][1], "Aztecs")
    assert matches_civilization(pair[0][1], "Mayans")
    assert not matches_civilization(pair[0][1], "Mongols")
    assert filtered_builds(pair, "aztecs") == pair


def test_several_civilizations_read_as_one_label():
    assert civilization_label(build(civilization=["Mayans", "Aztecs"])) \
        == "Mayans/Aztecs"


def test_a_build_that_names_no_civilization_counts_as_generic():
    """Both spellings of "did not say": absent, and present but empty."""
    assert civilization_names(build(civilization="")) \
        == (GENERIC_CIVILIZATION,)
    assert civilization_names(build(civilization=[])) \
        == (GENERIC_CIVILIZATION,)


# ---- the choice a filter must never take away ------------------------------

def test_the_kept_build_survives_a_filter_that_excludes_it():
    """The safety rule. Without it the drop-down moves the selection to
    whatever is left, and Start runs a build the player never picked."""
    found = filtered_builds(LIBRARY, "mongols", keep="benphosphorus")

    assert "benphosphorus" in {stem for stem, _b in found}


def test_the_kept_build_survives_a_query_matching_nothing_at_all():
    found = filtered_builds(LIBRARY, "trebuchet", keep="19popdrush")

    assert [stem for stem, _b in found] == ["19popdrush"]


def test_the_kept_build_is_not_listed_twice_when_it_also_matches():
    found = filtered_builds(LIBRARY, "hera", keep="19popdrush")

    stems = [stem for stem, _b in found]
    assert stems.count("19popdrush") == 1


def test_keeping_a_build_that_is_not_in_the_library_changes_nothing():
    """Whatever was selected has just been deleted from disk. Not an error:
    the picker falls back to the first entry, as it always has."""
    found = filtered_builds(LIBRARY, "hera", keep="deleted_yesterday")

    assert len(found) == 2


def test_the_kept_build_holds_its_place_in_the_order():
    found = filtered_builds(LIBRARY, "hera", keep="mon15popscouts")

    assert [stem for stem, _b in found][0] == "mon15popscouts"


# ---- against the real library ---------------------------------------------

def test_every_civilization_offered_finds_at_least_its_own_build():
    """End to end over the builds actually installed: whatever the
    drop-down offers must lead somewhere, or it is an entry that looks
    broken when picked."""
    pairs, _problems = available_builds()

    for name in civilizations(pairs):
        found = filtered_builds(pairs, civilization=name)
        assert found, f"the {name} entry would show an empty list"


def test_filtering_the_real_library_never_invents_a_build():
    pairs, _problems = available_builds()

    for name in civilizations(pairs):
        found = filtered_builds(pairs, civilization=name)
        assert set(found) <= set(pairs)
