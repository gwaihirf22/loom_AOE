"""
Loom — tooltip text that wraps instead of running off the screen.

Qt draws a plain-text tooltip on one line whatever its length: a routine
one-sentence explanation measures 1080 pixels. Loom has about 77 distinct
tooltip strings, 69% of them over 70 characters, so the long ones ran past
the window and the statistics chart's hovered label was chopped off at the
pane edge.

The tempting fix is rich text, because Qt word-wraps rich text. Measured,
it does nothing: <qt>...</qt> around the same sentence is still 1080 wide.
Only explicit breaks wrap, and a plain newline does it as well as <br>
(612x26 against 612x28) while needing no HTML escaping - which matters,
because Loom's tooltips carry & and strings like --Lumber Camp Built--.

These are string tests. No QApplication, no display - the same reasoning as
the window-flag tests. The last one is the one that keeps working after
everybody here has forgotten this file exists: it reads the tooltips out of
the shipped source rather than from a list typed in here.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ast
import pathlib

from loom import paths
from loom.tooltips import TOOLTIP_WIDTH, wrap, wrapped

LONG = ("Alert when a Town Center is sitting idle - the most expensive "
        "routine mistake in the game.")


def test_a_short_tooltip_is_left_alone():
    """Most call sites are a no-op, so the diff says what it touched."""
    assert wrapped("Close the preview.") == "Close the preview."
    assert wrapped("Bigger cards.") == "Bigger cards."


def test_a_long_tooltip_is_broken_into_readable_lines():
    lines = wrap(LONG)

    assert len(lines) > 1
    for line in lines:
        assert len(line) <= TOOLTIP_WIDTH, line


def test_the_paragraphs_the_author_wrote_survive():
    """The one a naive textwrap.wrap over the whole string fails.

    The longest tooltip in the project - the preview's "No overlay" box -
    already carries a deliberate blank line. Wrapping the string as one
    block would flatten it and lose the structure its author chose.
    """
    text = ("Keep the overlay panel off the game permanently, and it stays"
            " away until this is unticked."
            "\n\n"
            "This is the REMEMBERED setting. The Hide overlay button is the"
            " temporary version and is forgotten when Loom closes.")

    lines = wrap(text)

    assert "" in lines, "the blank line between the paragraphs was lost"
    assert lines[0].startswith("Keep the overlay")
    assert lines[lines.index("") + 1].startswith("This is the REMEMBERED")


def test_a_word_longer_than_the_line_is_not_broken():
    """A tooltip can carry a file path - "Open C:/Users/.../builds" is a
    real one - and a path split across two lines cannot be read back or
    copied. Letting one over-long word overflow is much the lesser fault.
    """
    path = "C:/Users/paulb/AppData/Roaming/Loom/builds/somewhere/deep/enough"

    lines = wrap(f"Open {path}")

    assert path in lines, f"the path was broken up: {lines}"


def test_wrapping_loses_no_words():
    """The text is the whole point of a tooltip; the shape is a detail."""
    assert " ".join(wrap(LONG)).split() == LONG.split()


def test_no_markup_is_introduced():
    """Kept plain deliberately. Loom's tooltips carry & and lines like
    --Lumber Camp Built--, and as HTML those would need escaping - an
    escape missed at one call site shows as visible markup in a tooltip
    nobody happens to be looking at."""
    text = ("Reads the feed for lines like --Lumber Camp Built-- & counts"
            " them, so <no> markup survives the trip")

    out = wrapped(text)

    assert "&amp;" not in out and "&lt;" not in out
    assert "<br" not in out and "<qt" not in out
    assert "--Lumber Camp Built--" in out
    assert "<no>" in out


def test_nothing_is_no_lines():
    assert wrap("") == []
    assert wrapped("") == ""


def test_the_width_is_adjustable_for_a_caller_that_needs_narrower():
    lines = wrap(LONG, 30)

    assert lines, "a narrow width still has to produce something"
    for line in lines:
        assert len(line) <= 30, line


# ---- the shipped tooltips themselves ------------------------------------


def tooltip_strings():
    """Every literal handed to setToolTip in the shipped code.

    Read out of the source rather than listed here, so a tooltip written
    next month is covered without anyone remembering to add it - the same
    reason test_release_exclusions globs the working documents instead of
    naming them.
    """
    found = []
    for path in sorted(pathlib.Path(paths.PROJECT_ROOT / "loom").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "setToolTip" and node.args):
                continue
            arg = node.args[0]
            # Every call site goes through wrapped() now, so the literal is
            # one level in. Looking only for a bare Constant found nothing
            # at all the moment that landed - which the test below caught,
            # and is why it is there.
            if (isinstance(arg, ast.Call)
                    and getattr(arg.func, "id",
                                getattr(arg.func, "attr", "")) == "wrapped"
                    and arg.args):
                arg = arg.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.append((path.name, node.lineno, arg.value))
    return found


def test_the_source_really_does_contain_tooltips_to_check():
    """A finder that silently matched nothing would make the test below
    pass by doing no work at all.

    Not hypothetical: it fired the moment every call site was routed
    through wrapped(), because the literal moved one level down the tree
    and the finder was still looking for a bare string. Without this the
    property test would have gone green over an empty list.
    """
    assert len(tooltip_strings()) > 20


def test_every_shipped_tooltip_wraps_to_a_readable_width():
    """The property, over the real strings. A literal already wrapped at
    the call site passes unchanged; one added later is covered the day it
    is written."""
    too_wide = []
    for name, line, text in tooltip_strings():
        for wrapped_line in wrap(text):
            # A single unbreakable word may exceed the width by design.
            if len(wrapped_line) > TOOLTIP_WIDTH and " " in wrapped_line:
                too_wide.append(f"{name}:{line} {wrapped_line!r}")

    assert not too_wide, "\n".join(too_wide)
