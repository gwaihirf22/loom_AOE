"""
Loom — the launcher's output pane must actually be fixed-pitch.

Its own file because test_launcher.py is deliberately Qt-free - the command
table is plain data, which is the whole reason it is a table - and a font
cannot be asked what it resolved to without a QGuiApplication.

The bug this pins was invisible for the same reason a lot of tonight's were:
the code said the obviously-correct thing and the platform did something
else. `QFont("Monospace")` with a Monospace style hint resolved to Tahoma on
Windows, fixedPitch False. There is no family called "Monospace" there, and a
style hint is a preference Qt stops consulting once the family lookup has
been satisfied - so the pane the code called fixed-width was proportional for
every Windows user, and pytest output in it was the soup the comment warned
about.

The side effect was the only visible symptom, and it looked unrelated:
Windows offers `8514oem`, a legacy raster face, as a Monospace substitute,
DirectWrite cannot load it, and Qt printed CreateFontFaceFromHDC errors to
the terminal. Intermittently, because a substitute is only sought when a
glyph is actually wanted - so it read as a random fault rather than as a font
that had been wrong since the first paint.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from PyQt6.QtGui import QFontDatabase, QFontInfo
from PyQt6.QtWidgets import QApplication

from loom.launcher import fixed_width_font


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def real_fonts(app):
    """Skip where the platform cannot answer the question at all.

    The offscreen QPA ships a stub font database: even the platform's OWN
    fixed font comes back with fixedPitch() False there, so a failure would
    say nothing about Loom. The guard asks whether the environment can
    answer rather than testing the platform NAME - "I could not check" is
    not "it is not true", and a skip says which of the two happened where a
    red test would not.
    """
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    if not QFontInfo(system).fixedPitch():
        pytest.skip("no real font database here - the platform's own fixed "
                    "font is not fixed-pitch either")


def test_the_output_pane_font_is_actually_fixed_pitch(real_fonts):
    """Asks QFontInfo what it RESOLVED to, never what was requested.

    A test asserting the family string would have passed throughout the
    bug - it would have been checking that the code still says what it
    always said. Whether the font is fixed-pitch is the only question the
    pane actually cares about, so it is the one asked.
    """
    assert QFontInfo(fixed_width_font()).fixedPitch(), (
        "the output pane's font is not fixed-pitch on this platform")


def test_the_font_names_real_families_rather_than_a_hint(app):
    """The families list must not be empty, because the style hint alone is
    what failed. Belt to the braces above: fixedPitch() is the guarantee,
    this catches the specific way it was lost."""
    families = fixed_width_font().families()

    assert families, "nothing but a style hint stands behind this font"
