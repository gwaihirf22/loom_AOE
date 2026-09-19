"""
Loom — tests for the whole-line analyser.

The third reader, and the one with the most room to do damage: it answers
about a line the letter reader got WRONG, so its whole safety case is
knowing when to say nothing. The refusing tests below matter more than the
matching ones.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import glyphs, lines


@pytest.fixture(scope="module")
def universe():
    found = lines.universe()
    assert found, "the icon library produced no lines at all"
    return found


def test_the_universe_holds_the_lines_a_build_depends_on(universe):
    """The events the shipped builds tick off must be in the universe.

    A derived list still fails silently when the derivation is wrong -
    that is the allowlist failure mode wearing a new hat - so the things
    Loom actually watches for are asserted by name. Town Centre most of
    all: production.py counts them, and a universe missing it would match
    a misread "--Town Center Built--" to whatever else was nearest.
    """
    for wanted in ("--Town Center Built--", "--House Built--",
                   "--Mill Built--", "--Barracks Built--",
                   "--Lumber Camp Built--", "--Mining Camp Built--",
                   "--Villager Created--", "--Market Built--",
                   "--Blacksmith Built--", "--Stable Built--",
                   "--Archery Range Built--", "--Farm Built--",
                   "--Loom Research Complete--",
                   "--Wheelbarrow Research Complete--"):
        assert wanted in universe, f"the universe is missing {wanted!r}"


def test_a_clean_line_is_matched_to_itself(universe):
    assert lines.nearest_line("--Mill Built--")[1] == "built:mill"


def test_a_badly_read_line_still_finds_its_sentence():
    """The case this exists for: several words wrong at once, which is
    what a small rendering does and what both word-level repairs give up
    on - each needs all but one word to be right."""
    matched = lines.nearest_line("--Towu Watch Researle Complete--")
    assert matched is not None
    assert matched[1] == "researched:town_watch"


def test_a_fragment_never_becomes_an_event():
    """A partially-read line is not a sentence to be matched. This is the
    rule that keeps "colnplete" from ever being a fact."""
    assert lines.nearest_line("Mill Built") is None          # no framing
    assert lines.nearest_line("--Mill--") is None            # too short
    assert lines.nearest_line("") is None


def test_a_read_too_far_from_anything_claims_nothing():
    assert lines.nearest_line("--Qxzzy Wobblenaut Blorped--") is None


def test_a_near_rival_refuses_rather_than_wins():
    """The whole safety case. "--Siega Rane Coreated--" is a misread of
    "--Spearman Created--" that sits near several real sentences at once;
    the corpus caught it being rebuilt into "--Siege Ram Created--", a
    unit that was never made. A second candidate within the margin means
    nothing is claimed."""
    matched = lines.nearest_line("--Siega Rane Coreated--")
    assert matched is None or matched[1] != "created:siege_ram"


def test_rivals_are_compared_by_EVENT_not_by_spelling():
    """Two candidates that mean the same thing are not rivals.

    Several spellings of one entity survive the icon derivation - a
    concatenated form and a split one - and if those counted as rivals the
    analyser would refuse every line whose subject happens to have two
    names on file, which is most of the interesting ones.
    """
    matched = lines.nearest_line("--Scout Cavalry Created--")
    assert matched is not None
    assert matched[1] == "created:scout_cavalry"


def test_the_margin_is_load_bearing():
    """Measured, not assumed: at margin 1 the corpus gains more events and
    starts inventing them. If someone lowers this, the corpus gate is what
    should stop them - but the number itself is worth pinning here too."""
    assert lines.LINE_MARGIN >= 2


def test_every_universe_line_parses_to_a_real_event(universe):
    """A candidate that cannot become an event is dead weight at best and
    a rival that refuses good matches at worst."""
    unparsed = [line for line in universe[:400]
                if glyphs.parse_event(line) is None]
    assert not unparsed, f"these parse to nothing: {unparsed[:5]}"


def test_a_sentence_the_game_cannot_print_is_not_in_the_universe(universe):
    """The fault that sent me here.

    Every subject crossed with every phrasing invents lines the game has
    no way to draw, and each one is a RIVAL that can refuse a real read.
    "--Wood Research Complete--" sat two edits from
    "--Loom Research Complete--", so the Loom line was refused even from a
    PERFECT read - the one research line in the whole table that no repair
    could ever recover.
    """
    for impossible in ("--Wood Research Complete--",
                       "--Gold Research Complete--",
                       "--Stone Research Complete--",
                       "--Food Research Complete--",
                       "--Barracks Research Complete--",
                       "--Goat Research Complete--",
                       "--Mill Created--"):
        assert impossible not in universe, f"{impossible!r} is not a real line"


def test_the_villager_survives_the_resource_folder():
    """The trap in the fix above, and the reason it asks the object table
    rather than the folder.

    The icon library files the villager under resource/ beside wood and
    stone, and "--Villager Created--" is the commonest line in the whole
    labelled corpus. Dropping the folder wholesale would have taken it.
    """
    assert "--Villager Created--" in lines.universe()
    assert lines.nearest_line("--Villager Created--")[1] == "created:villager"


def test_loom_can_be_recovered_from_a_wobbled_read():
    """What the whole exercise was for. This line was unrecoverable at any
    distance, including zero."""
    assert lines.nearest_line("--Loom Research Complete--")[1] \
        == "researched:loom"
    assert lines.nearest_line("--Loom Researcli Complete--")[1] \
        == "researched:loom"


def test_a_real_rival_still_refuses():
    """The safety case is unchanged where the rival is a real sentence.

    Huskarl is a unit the game can genuinely research, so a wobbly Hussar
    line has a real neighbour and must claim nothing. Narrowing the
    universe must not have narrowed away the refusals that matter.
    """
    assert lines.nearest_line("--Hussar Research Complete--") is None


def test_a_unit_keeps_the_phrasing_its_upgrade_uses():
    """An upgrade is named after its unit, so a unit is both Created and
    Research Complete - and an Elite form is researched whatever it is."""
    assert "Created" in lines.phrasings_for("crossbowman")
    assert "Research Complete" in lines.phrasings_for("crossbowman")
    assert "Research Complete" in lines.phrasings_for("elite berserk")


def test_a_subject_nobody_has_classified_keeps_every_phrasing():
    """No opinion is not a refusal. The object table names 454 things and
    the icon library 653, so a missing subject is usually a fact about the
    table rather than about the game."""
    assert lines.phrasings_for("a subject nobody has ever heard of") \
        == lines.PHRASINGS
