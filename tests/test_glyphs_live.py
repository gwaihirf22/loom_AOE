"""
Loom — the glyph reader against real captured pixels.

Two fixtures from live frames, guarding the two failures that silenced the
TextWatcher for a whole game on 2026-07-31: bright terrain read as text
lines (so the bottom band was junk every poll), and a font so overfit to
its harvest pixels that a 2% resample refused every line.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import cv2
import pytest

from loom import glyphs, paths

FIXTURES = paths.PROJECT_ROOT / "tests" / "data" / "glyphs_live"


@pytest.fixture(scope="module")
def font():
    loaded = glyphs.load_font()
    if not loaded:
        pytest.skip("no notification font harvested")
    return loaded


def test_terrain_panel_has_no_lines():
    # Sunlit terrain clears any brightness threshold; only ink beside the
    # font's near-black outline counts as text. This panel produced eight
    # phantom bands before the outline gate existed.
    panel = cv2.imread(str(FIXTURES / "terrain_panel.png"))
    assert panel is not None
    assert glyphs.find_lines(panel) == []


def test_harvest_line_reads_at_native_size(font):
    line = cv2.imread(str(FIXTURES / "villager_created_line.png"))
    assert line is not None
    text, score = glyphs.read_line(line, font)
    assert text == "-- Villager Created--"
    assert score >= glyphs.MIN_GLYPH_SCORE


def test_harvest_line_survives_a_small_resample(font):
    # The game at a slightly different HUD scale renders the same text
    # with different antialiasing. The resampled font variants keep the
    # per-glyph gate strict while absorbing that softness. A misread
    # letter is acceptable here - parse_event's KNOWN_WORDS vocabulary is
    # the event-level backstop - but a refused line is the bug.
    line = cv2.imread(str(FIXTURES / "villager_created_line.png"))
    grown = cv2.resize(line, None, fx=1.02, fy=1.02,
                       interpolation=cv2.INTER_AREA)
    text, _ = glyphs.read_line(grown, font)
    assert text is not None
