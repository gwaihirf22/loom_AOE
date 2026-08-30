"""
Loom — tooltip text broken into lines short enough to read.

Qt lays a plain-text tooltip out on ONE line however long it is. Measured:
a routine one-sentence explanation comes out 1080 pixels wide. Loom has 48
tooltip call sites and about 77 distinct strings once the ones built from
data tables are expanded; 69% are over 70 characters and 40% over 120, the
longest being 411. So the long ones run past the window, and on the
statistics chart the hovered label runs past the pane and is chopped off.

WHY REAL NEWLINES AND NOT HTML, because the obvious answer is wrong and
somebody will try it again otherwise. Qt word-wraps rich text, so wrapping
a tooltip in <qt>...</qt> looks like the fix. It is not - measured, the
same string comes back 1080 pixels wide either way. Only explicit breaks
wrap it, and a plain newline does that exactly as well as <br>: 612x26
against 612x28 for the same sentence.

Plain text is also the safer of the two. Loom's tooltips carry & and
strings like --Lumber Camp Built--, which as HTML would need escaping, and
an escape missed at one call site shows up as visible markup in a tooltip
nobody happens to be looking at.

It is the house convention already. Three tooltips carry a hand-written \\n
today - the preview's "No overlay" box, the build list and the civ filter.
This only saves everyone else from doing it by hand.

WHAT THIS IS NOT. Not elision: loom/overlay.py's elide() cuts a line to fit
a width and is the right answer when there is one line's worth of room and
no more. This is for text that is allowed to be tall.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import textwrap

# Characters, not pixels. A tooltip is read at a glance and a narrow column
# is easier to take in than a wide one; it also keeps the box well clear of
# a screen edge, which is where the clipping happened. Roughly 600 pixels at
# the sizes Qt draws tooltips.
TOOLTIP_WIDTH = 60


def wrap(text, width=TOOLTIP_WIDTH):
    """The lines a tooltip should be drawn as. Never breaks a word.

    Paragraphs the author wrote survive. The longest tooltip in the project
    already contains a deliberate blank line, and wrapping the whole string
    as one block would flatten it - so each paragraph is wrapped on its own
    and the blank lines are kept.

    break_long_words is off on purpose. A tooltip can carry a file path
    (`Open C:\\Users\\...\\builds`) or a hotkey spelling, and a path split
    across two lines cannot be read back or copied. A single over-long word
    is left to overflow, which is the lesser fault by a wide margin.
    """
    if not text:
        return []
    lines = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")            # a blank line the author meant
            continue
        lines.extend(textwrap.wrap(paragraph, width,
                                   break_long_words=False,
                                   break_on_hyphens=False))
    return lines


def wrapped(text, width=TOOLTIP_WIDTH):
    """A tooltip ready for setToolTip: the same text, with line breaks.

    Short text comes back unchanged, so most call sites are a no-op and the
    diff says what it actually touched.
    """
    return "\n".join(wrap(text, width))
