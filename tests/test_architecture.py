"""The map in docs/architecture.md has to stay true to the code.

A diagram that has drifted is worse than no diagram: it still renders, still
looks authoritative, and describes a system that no longer exists. This is
the same guard tests/test_about.py puts on the How-to-use page, which reads
the supported skins from hud.PROFILES so the page "cannot drift out of date
the way it did the day stock support landed".

The rules are deliberately asymmetric. A map may SIMPLIFY - leaving out
`paths`, which nearly everything imports and which says nothing about how the
system works - but it may not LIE, and it may not silently omit a whole
module. Omission of an edge is an editorial choice; invention of one is a
false fact, and a module on no diagram is a part of the system the map denies
exists.
"""

import pytest

from tools import architecture

# Two of the checks below read CLAUDE.md, which is the project's working
# agreement and is deliberately NOT published: tools/release.py strips it,
# so the released snapshot runs this suite without it. unlisted_modules
# answers "nothing unlisted" when it has no Layout to check against, and
# these are the tests that would otherwise ASSERT on that empty answer and
# pass for having checked nothing. A skip is honest and a passing count is
# not - the same reasoning the offscreen font tests are written under.
#
# Everything else in this file supplies its own document and needs no file
# on disk, which is a property worth keeping: it means the map's own rules
# stay tested wherever the code goes.
needs_layout = pytest.mark.skipif(
    not architecture.LAYOUT_DOC.exists(),
    reason="CLAUDE.md carries the Layout list and is not published in the "
           "release snapshot, so there is nothing here to check against")


@needs_layout
def test_the_drawing_agrees_with_the_code():
    problems = architecture.complaints()
    assert problems == [], "\n".join(problems)


def test_every_module_is_on_the_map():
    # The half that catches drift. A module added next month is invisible
    # until someone draws it, and this is what makes that a failing test
    # rather than a quiet gap.
    imports = architecture.module_imports()
    drawn = {name for edge in architecture.diagram_edges(_doc()) for name in edge}
    missing = sorted(set(imports) - drawn - architecture.MAY_BE_OMITTED)
    assert missing == [], f"modules on no diagram: {missing}"


def test_the_entry_points_are_part_of_the_map():
    # Without them, six modules that the applications begin at - reader,
    # debuglog, apmwin, passthrough, pace, launcher - are imported by nothing
    # inside the package and look like orphans.
    imports = architecture.module_imports()
    for entry in ("loom_app", "loom_overlay", "loom_coach", "loom_read"):
        assert entry in imports, f"{entry} is not in the graph"
    assert "reader" in imports["loom_overlay"]


# ---- the gate has to be able to FAIL, or it is decoration ---------------

def test_an_arrow_that_is_not_a_real_import_is_caught():
    lying = "```mermaid\nflowchart LR\n    overlay --> anchor\n```"
    problems = architecture.complaints(markdown=lying)
    assert any("does not import" in problem for problem in problems), problems


def test_a_box_that_names_no_module_is_caught():
    # A typo becomes a phantom component nobody can find, so it must not
    # quietly pass as scenery.
    typo = "```mermaid\nflowchart LR\n    anchor --> raeder\n```"
    problems = architecture.complaints(markdown=typo)
    assert any("raeder" in problem for problem in problems), problems


def test_a_module_drawn_nowhere_is_caught():
    almost_empty = "```mermaid\nflowchart LR\n    hud --> anchor\n```"
    problems = architecture.complaints(markdown=almost_empty)
    assert any("appears in no diagram" in problem for problem in problems)


def test_a_declared_external_is_allowed_to_have_no_module():
    # The world the code sits in is drawn too, and must not be an error.
    world = "```mermaid\nflowchart LR\n    game([the game]) --> capture\n```"
    problems = architecture.complaints(markdown=world)
    # Only complaints ABOUT THE BOX called game count here - "gamestats
    # appears in no diagram" is a different and correct complaint, and an
    # `in` test loose enough to match it would pass whatever happened.
    assert not any(problem.startswith("box 'game'") for problem in problems)
    assert not any("game --> capture" in problem for problem in problems)


def test_text_outside_a_mermaid_block_is_not_read_as_a_diagram():
    # Prose that happens to contain an arrow is prose.
    prose = "The reader --> nonsense here is just a sentence.\n"
    assert architecture.diagram_edges(prose) == []


def _doc():
    with open(architecture.DIAGRAM_DOC, encoding="utf-8") as handle:
        return handle.read()


# ---- the focused map, for working inside one subsystem ------------------

def test_a_neighbourhood_shows_both_directions():
    # What feeds queue AND what queue feeds. One direction alone answers
    # half the question you actually have while working in a module.
    block = architecture.neighbourhood("queue")
    assert "anchor --> queue" in block      # queue imports anchor
    assert "queue --> reader" in block      # reader imports queue
    assert block.startswith("```mermaid")


def test_a_neighbourhood_leaves_the_rest_of_the_system_out():
    # The point is that it is SMALL. Forty-seven modules on one page is a
    # thing you look at; eight lines is a thing you use.
    block = architecture.neighbourhood("queue")
    assert "launcher" not in block
    assert len(block.splitlines()) < 15


def test_a_neighbourhood_is_true_by_construction():
    # Generated, not drawn - the opposite trade from docs/architecture.md,
    # which is why this one needs no gate of its own. Every edge it emits
    # must still survive the same check the drawing does.
    imports = architecture.module_imports()
    block = architecture.neighbourhood("overlay", imports)
    for feeds, fed in architecture.diagram_edges(block):
        assert feeds in imports[fed], f"{feeds} --> {fed} is not a real import"


def test_asking_about_a_module_that_does_not_exist_is_an_error():
    with pytest.raises(KeyError):
        architecture.neighbourhood("no_such_module")


# ---- CLAUDE.md's Layout list is gated too -------------------------------

@needs_layout
def test_the_layout_list_names_every_module():
    """The map and the Layout do different jobs and both must be complete.

    The map says how the parts CONNECT; the Layout says what each part IS.
    Half the boxes on the map were explained nowhere near it, and four
    modules - lines, events, replay, durations - were not named in the
    Layout at all. Neither document should copy the other, so both are
    checked instead.
    """
    assert architecture.unlisted_modules() == []


def test_a_package_counts_as_named_by_its_directory():
    # capture/ and hotkeys/ are packages and the Layout names them with a
    # trailing slash. Demanding "capture.py" reported six missing modules
    # when only four were, which is a gate crying wolf about its own
    # spelling.
    block = "```\ncapture/   pixels\nanchor.py  the icon\n```"
    imports = {"capture": set(), "anchor": set()}
    assert architecture.unlisted_modules(block=block, imports=imports) == []


def test_a_module_missing_from_the_layout_is_caught():
    block = "```\nanchor.py  the icon\n```"
    imports = {"anchor": set(), "newthing": set()}
    assert architecture.unlisted_modules(block=block,
                                         imports=imports) == ["newthing"]


def test_the_layout_block_is_the_one_under_the_heading():
    markdown = ("# Loom\n\n```\nnot the layout\n```\n\n"
                "## Layout\n\n```\nanchor.py  the icon\n```\n\n"
                "## After\n\n```\nnor this\n```\n")
    block = architecture.layout_block(markdown)
    assert "anchor.py" in block
    assert "not the layout" not in block and "nor this" not in block


# ---- some stories are not module-shaped ---------------------------------
#
# `production` never imports `queue` - it is handed slot readings as plain
# data - so the most useful picture of how a queue cell becomes a belief
# cannot be drawn with module boxes at all. A block may declare itself
# drawn at function granularity, and is then checked against that file's
# own definitions. Different oracle, same promise: a map may simplify, it
# may not invent.

FUNCTION_BLOCK = ("```mermaid\n%% functions: loom/queue.py\n"
                  "flowchart LR\n    classify_tint --> wash_against_icon\n```")


def test_a_function_block_is_not_measured_against_the_import_graph():
    # Without this the boxes read as a dozen phantom modules, and the
    # escape hatch would be unusable rather than merely unused.
    assert architecture.diagram_edges(FUNCTION_BLOCK) == []


def test_a_function_block_is_still_checked():
    # Opting down a level is not opting out. These two really are defined
    # in loom/queue.py, so this block is honest and must pass.
    assert architecture.function_complaints(FUNCTION_BLOCK) == []


def test_a_function_that_does_not_exist_is_caught():
    lying = ("```mermaid\n%% functions: loom/queue.py\n"
             "flowchart LR\n    classify_tint --> classify_taint\n```")
    problems = architecture.function_complaints(lying)
    assert any("classify_taint" in problem for problem in problems), problems


def test_a_declaration_pointing_nowhere_is_caught():
    # The declaration is what makes the block checkable, so a wrong path
    # would otherwise turn the check off and report success.
    lying = ("```mermaid\n%% functions: loom/no_such_file.py\n"
             "flowchart LR\n    a --> b\n```")
    assert architecture.function_complaints(lying) != []


def test_constants_count_as_drawable():
    # The tint bars ARE the subject of that part of the reader, and a
    # picture of it that cannot name them says very little.
    block = ("```mermaid\n%% functions: loom/queue.py\n"
             "flowchart LR\n    ICON_WASH_RATIO --> wash_against_icon\n```")
    assert architecture.function_complaints(block) == []


def test_drawing_a_function_is_not_drawing_its_module():
    # Rule 3 still wants every MODULE on the map somewhere. A function-level
    # block must not be able to satisfy it by naming something inside one.
    problems = architecture.complaints(markdown=FUNCTION_BLOCK)
    assert any("queue" in p and "appears in no diagram" in p for p in problems)


def test_the_real_document_passes_the_function_check_too():
    assert architecture.function_complaints() == []
