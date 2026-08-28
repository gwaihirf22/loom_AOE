"""
Loom — reading the game's own civilisation rules out of the install.

tools/civ_reference.py writes reference/ from files Age of Empires II ships.
Nothing under loom/ imports any of it and Loom behaves identically without
it, so what is at stake here is not the program's behaviour but whether the
reference TELLS THE TRUTH - and a reference that is quietly wrong is worse
than none, because it will be believed.

CI has no game install, which is the constraint that shaped the tool: every
function that decides anything takes its input as an argument, so all of this
runs from literals on a machine that has never seen AoE2.

The parsing tests are not pedantry. Each one is a thing that cost time:
the files carry a BOM, <b> is a toggle rather than a tag, newlines are two
characters, and the civ id is not the civ's name.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

import pytest

from tools import civ_reference as civ


# ---- the language file --------------------------------------------------


def test_a_string_is_found_by_its_number():
    strings = civ.read_strings('10271 "Britons"\n10272 "Franks"\n')

    assert strings[10271] == "Britons"
    assert strings[10272] == "Franks"


def test_escaped_newlines_become_real_ones():
    """The game writes the two characters backslash and n, not a newline.
    Left alone, every civ's bonuses arrive as one unreadable line."""
    strings = civ.read_strings(r'1 "first\nsecond"' + "\n")

    assert strings[1] == "first\nsecond"


def test_the_bold_marker_is_a_toggle_not_a_tag():
    """The game writes "<b>Unique Unit:<b>" - there is no closing tag. A
    parser expecting </b> keeps the markup and nobody notices until it is in
    the committed reference."""
    strings = civ.read_strings('1 "<b>Unique Unit:<b> Longbowman"\n')

    assert strings[1] == "Unique Unit: Longbowman"


def test_a_byte_order_mark_does_not_become_part_of_the_first_id():
    """The files are utf-8 WITH a BOM. Decoded as plain utf-8 the first line
    carries an invisible character and its id never matches."""
    strings = civ.read_strings("﻿" + '10271 "Britons"\n')

    assert strings[10271] == "Britons"


def test_a_line_that_is_not_a_string_is_ignored():
    """The file opens with comment lines and a block of ASCII art."""
    strings = civ.read_strings('// a comment\n\n10271 "Britons"\n')

    assert strings == {10271: "Britons"}


# ---- the derivation, which is a pattern rather than a promise -----------


def test_the_help_string_sits_at_a_fixed_offset_from_the_name():
    """Measured across all 59 civs on the author's install. There is no help
    id in civilizations.json or anywhere else, so this arithmetic is the only
    route from a civ to its bonuses."""
    assert civ.help_string_id(civ.NAME_BASE) == civ.HELP_BASE
    assert civ.help_string_id(10276) == 120155      # Chinese, verified live


def test_a_derivation_that_slipped_is_caught():
    """The failure this guards is silent: an offset off by one still returns
    TEXT, so every civ would get the next civ's bonuses and the reference
    would read perfectly while being wrong about all of them."""
    civs = [{"tech_tree_name": "BRITONS", "name_string_id": 10271}]
    right = {120150: "Foot Archer civilization\n\nthings"}
    wrong = {120150: "Longbowman"}       # text, but not a civ description

    assert civ.check_derivation(civs, right) == []
    assert civ.check_derivation(civs, wrong) == ["BRITONS"]


# ---- what a civ has, and has not ---------------------------------------


def node(name, status):
    return {"Name": name, "Node Status": status}


def test_the_three_statuses_land_where_they_belong():
    has, cannot, unknown = civ.split_nodes([
        node("Lumber Camp", "ResearchedCompleted"),
        node("Bloodlines", "ResearchRequired"),
        node("Paladin", "NotAvailable"),
    ])

    assert has == ["Bloodlines", "Lumber Camp"]
    assert cannot == ["Paladin"]
    assert unknown == []


def test_a_status_nobody_recognises_is_reported_not_assumed():
    """The direction matters. Counting an unknown status as available would
    produce a reference claiming a civ has something it does not - which is
    the one failure mode that makes the whole file untrustworthy."""
    has, cannot, unknown = civ.split_nodes([node("Mule Cart", "Something New")])

    assert has == [] and cannot == []
    assert unknown == ["Mule Cart (Something New)"]


def test_a_node_with_no_name_is_skipped_rather_than_crashing():
    assert civ.split_nodes([{"Node Status": "NotAvailable"}]) == ([], [], [])


def test_gaia_is_not_a_civilisation_anyone_plays():
    """The map's own "civ" - trees and deer - ships a tech tree file like
    every other."""
    listed = [{"tech_tree_name": "GAIA", "name_string_id": 10102},
              {"tech_tree_name": "BRITONS", "name_string_id": 10271},
              {"internal_name": "no tech tree"}]

    assert [c["tech_tree_name"] for c in civ.playable(listed)] == ["BRITONS"]


# ---- the description ----------------------------------------------------


HELP = ("Foot Archer civilization\n\n"
        "• Shepherds work +25% faster\n"
        "• Foot Archers +1/+2 range\n\n"
        "Unique Unit: \nLongbowman\n\n"
        "Unique Techs: \n• Yeomen\n• Warwolf\n\n"
        "Team Bonus: \nArchery Ranges work +10% faster")


def test_the_description_splits_into_the_parts_worth_grepping():
    parsed = civ.parse_help(HELP)

    assert parsed["archetype"] == "Foot Archer civilization"
    assert parsed["bonuses"] == ["Shepherds work +25% faster",
                                 "Foot Archers +1/+2 range"]
    assert parsed["unique_units"] == ["Longbowman"]
    assert parsed["unique_techs"] == ["Yeomen", "Warwolf"]
    assert parsed["team_bonus"] == "Archery Ranges work +10% faster"


def test_a_description_that_breaks_the_convention_still_yields_what_it_has():
    """The text is prose the game writes for humans; its shape is a habit,
    not a format. A civ that does not follow it must not take the whole run
    down with it."""
    parsed = civ.parse_help("Cavalry civilization")

    assert parsed["archetype"] == "Cavalry civilization"
    assert parsed["bonuses"] == []
    assert parsed["team_bonus"] == ""


# ---- what gets written --------------------------------------------------


RECORD = {
    "id": "BRITONS", "name": "Britons", "era": "base",
    "archetype": "Foot Archer civilization",
    "bonuses": ["Shepherds work +25% faster"],
    "unique_units": ["Longbowman"], "unique_techs": ["Yeomen"],
    "team_bonus": "Archery Ranges work +10% faster",
    "buildings": {"has": ["Lumber Camp"], "cannot": ["Bombard Tower"]},
    "units": {"has": ["Archer"], "cannot": ["Paladin", "Bloodlines"]},
    "unknown_status": [],
}


def test_the_civ_file_can_be_grepped_for_what_it_has():
    """The use this exists for: `grep -L "Lumber Camp" reference/civs/*.md`
    names the civilisations for which --Lumber Camp Built-- can never fire.
    That only works if the name is on one line with its neighbours."""
    page = civ.render_civ(RECORD, "2026-06-23")

    assert "# Britons" in page
    assert "Lumber Camp" in page
    assert "Paladin" in page


def test_cannot_have_comes_before_has():
    """It is the section that settles an argument - an identity sighted for
    a civ listed there is a misread, provably - so it does not go last where
    a long Has list would bury it."""
    page = civ.render_civ(RECORD, "2026-06-23")

    assert page.index("## Cannot have") < page.index("## Has")


def test_a_civ_that_lacks_nothing_says_so_rather_than_leaving_a_blank():
    """An empty line reads as "not recorded". Loom's whole discipline is that
    an admission and an absence are different things."""
    full = dict(RECORD, buildings={"has": ["Lumber Camp"], "cannot": []},
                units={"has": ["Archer"], "cannot": []})

    assert "nothing" in civ.render_civ(full, "2026-06-23")


def test_an_unrecognised_status_is_shouted_about_in_the_file_itself():
    """Not only in the tool's output, which nobody reads a month later."""
    odd = dict(RECORD, unknown_status=["Mule Cart (Something New)"])
    page = civ.render_civ(odd, "2026-06-23")

    assert "Unrecognised node status" in page
    assert "Mule Cart (Something New)" in page


# ---- noticing a patch ---------------------------------------------------


BEFORE = {"patch": "2026-06-23",
          "source": {"civilizations.json": "aaa",
                     "CivTechTrees/BRITONS.json": "bbb"}}


def test_an_identical_install_reports_nothing():
    assert civ.differences(dict(BEFORE), BEFORE) == []


def test_the_gate_can_actually_fail():
    """A check that cannot fail is decoration. Each of the three ways the
    game can differ has to produce a line."""
    changed = {"patch": "2026-08-14",
               "source": {"civilizations.json": "aaa",
                          "CivTechTrees/BRITONS.json": "CHANGED",
                          "CivTechTrees/ROMANS.json": "new"}}
    lines = civ.differences(changed, BEFORE)

    assert any("2026-06-23 -> 2026-08-14" in line for line in lines)
    assert any("changed" in line and "BRITONS" in line for line in lines)
    assert any("new file" in line and "ROMANS" in line for line in lines)


def test_a_file_the_game_stopped_shipping_is_noticed():
    """A civ removed is as much a change as a civ added, and the silent
    direction: nothing in the distilled Markdown would mention it."""
    lines = civ.differences({"patch": "2026-06-23", "source": {}}, BEFORE)

    assert any("gone" in line for line in lines)


# ---- finding the game ---------------------------------------------------


def test_the_override_wins_and_is_the_only_candidate():
    """Set LOOM_AOE2_DIR and Loom must look there and nowhere else -
    otherwise an override that points at the wrong place silently falls back
    to a real install and the override appears not to work."""
    found = civ.install_candidates(env={civ.INSTALL_ENV: "/somewhere/else"})

    assert [str(path) for path in found] == [str(found[0])]
    assert "somewhere" in str(found[0])


def test_a_machine_with_no_steam_returns_candidates_rather_than_raising(tmp_path):
    """"No install found" and "I never looked at your other drive" are
    different messages, and the caller can only tell them apart if it is
    handed the list it tried. Same reasoning as loom.replay.search_paths."""
    found = civ.install_candidates(home=tmp_path, env={})

    assert isinstance(found, list)
    for path in found:
        assert "AoE2DE" in str(path)


def test_a_second_library_on_another_drive_is_found(tmp_path):
    """The case that makes this necessary: the author's game is on E:, which
    no amount of guessing at Program Files would ever find."""
    steam = tmp_path / ".steam" / "steam" / "steamapps"
    steam.mkdir(parents=True)
    elsewhere = str(tmp_path / "elsewhere").replace("\\", "\\\\")
    (steam / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path"  "' + elsewhere + '"\n }\n}\n',
        encoding="utf-8")

    libraries = [str(path) for path in
                 civ.steam_libraries(home=tmp_path, platform="linux")]

    assert any("elsewhere" in path for path in libraries)


def test_an_unreadable_libraryfolders_does_not_stop_the_search(tmp_path):
    """A Steam directory can exist with nothing readable in it."""
    (tmp_path / ".steam" / "steam" / "steamapps").mkdir(parents=True)

    assert civ.steam_libraries(home=tmp_path, platform="linux")


def test_the_windows_branch_is_reachable_from_any_machine(tmp_path):
    """paths.py's discipline: an OS-specific branch has to be askable from
    the other OS, or it is only ever exercised on one of them."""
    libraries = [str(path) for path in
                 civ.steam_libraries(home=tmp_path, platform="win32")]

    assert any("Program Files" in path for path in libraries)


def test_nothing_is_found_when_the_game_is_not_installed(tmp_path):
    """platform="linux" is what keeps this off the real machine.

    Without it the search read the author's actual libraryfolders.vdf and
    found the game on E: - a test that passed home=tmp_path and was not
    isolated by it at all, which is why steam_libraries takes a platform.
    """
    assert civ.find_install(home=tmp_path, env={}, platform="linux") is None


# ---- the reference is not part of the program ---------------------------


def test_no_module_under_loom_imports_the_reference_tool():
    """reference/README.md's rule, and the reason it matters: a build order
    that behaved differently because a reference file was edited would be a
    program whose behaviour lives outside its own source."""
    from loom import paths

    offenders = []
    for module in sorted((paths.PROJECT_ROOT / "loom").rglob("*.py")):
        text = module.read_text(encoding="utf-8")
        if "civ_reference" in text or "reference/" in text:
            offenders.append(module.name)

    assert offenders == [], f"loom/ reaches into the reference: {offenders}"
