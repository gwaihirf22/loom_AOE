"""
Loom — reading "current/cap" off a small HUD.

The population band drives the housing alerts, and at 1920x1080 it had all
but stopped answering: Anne_HK read 152 of 263 legible bands and stock read
42 of 267, while both skins read 100% at 2560x1440. Three separate faults,
each measured over the capture runs in captures/.

  * The mod's max_glyph_width was floored at 13 - its own full-size value -
    so it never scaled DOWN. At 1440p its population runs measure 5-10px
    with a merged slash-and-digit at 16, which 13 splits; at 1080p the runs
    measure 2-8 and the merged pair lands near 12, under the floor, never
    split, classified as nothing, taking the band with it.

  * Stock's "5" did not match the templates cut at larger sizes, scoring a
    median 0.52 against a 0.55 gate. Two variants cut from the rendering
    itself fixed it - the same move as the existing 5_3, which was added
    for a 4K macOS "5" that scored 0.54.

  * Both of those made the band readable, and readable exposed a third
    fault: the last-resort housed pass splits a "4" into a 2px and a 3px
    piece, and the old code SKIPPED the narrow piece and read the rest as
    a "1". 4/5 became 1/5, frame after frame, which no downstream filter
    catches because it does not flicker. A narrow run in this band is
    always a broken glyph, so it now refuses the band instead.

The fixtures are live bands, and every value in them was read off the
pixels by eye before it was written down.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import pytest

from loom import digits, reader

DATA = pathlib.Path(__file__).parent / "data" / "population"

# What reader.min_glyph_width and max_glyph_width answer for these bands:
# the mod at anchor scale 0.735, stock at 0.740.
ANNEHK_GATES = (4, 9)
STOCK_GATES = (3, 7)


def band(name):
    image = cv2.imread(str(DATA / name))
    assert image is not None, f"missing fixture {name}"
    return image


@pytest.fixture(scope="module")
def templates():
    return digits.load_digit_templates()


def test_the_mod_reads_a_merged_slash_at_1080p(templates):
    """13/20 on Anne_HK at 1080p, where the slash touches the digit beside
    it. Unread before the width floor learned to scale."""
    assert digits.read_population(band("annehk_1080p_13_20.png"), templates,
                                  *ANNEHK_GATES) == (13, 20)


def test_the_mod_needs_a_split_bound_that_scales(templates):
    """The floor itself: at the old 13 this same band reads nothing, because
    the merged run is under the bound and is never taken apart."""
    assert digits.read_population(band("annehk_1080p_13_20.png"), templates,
                                  ANNEHK_GATES[0], 13) == (None, None)


def test_stock_reads_its_fives_at_1080p(templates):
    """6/15 on the stock bar at 1080p - unread until the "5" had a template
    cut from this rendering."""
    assert digits.read_population(band("stock_1080p_6_15.png"), templates,
                                  *STOCK_GATES) == (6, 15)


def test_a_broken_glyph_refuses_the_whole_band(templates):
    """The band in this fixture says 4/5 on screen. The housed pass breaks
    its "4" into two pieces, and reading the larger piece alone gives a
    perfectly plausible 1/5 that the housing alerts would act on. Refusing
    is the only safe answer: a poll costs nothing, a wrong population costs
    the alerts their meaning.
    """
    current, cap = digits.read_population(
        band("stock_1080p_broken_glyph_refused.png"), templates, *STOCK_GATES)

    assert (current, cap) != (1, 5), "the broken half is being read as a 1"
    assert current is None, f"read {current}/{cap} from a band it cannot see"


def test_the_gates_these_fixtures_were_cut_at_are_what_the_reader_answers():
    """The fixtures are meaningless if the reader would hand different
    bounds to a real frame of the same HUD, so the numbers are pinned to
    the functions rather than only living in this file."""
    from loom import hud

    stock = [p for p in hud.PROFILES if p.name == "stock"][0]
    annehk = [p for p in hud.PROFILES if p.name == "annehk"][0]

    assert (reader.min_glyph_width(0.735, annehk),
            reader.max_glyph_width(0.735, annehk)) == ANNEHK_GATES
    assert (reader.min_glyph_width(0.740, stock),
            reader.max_glyph_width(0.740, stock)) == STOCK_GATES


def test_the_population_band_asks_for_more_confidence_than_the_rest():
    """Stated because it looks like an inconsistency otherwise. A doubtful
    glyph elsewhere costs one reading; here it becomes a plausible number
    the alerts believe, and it repeats rather than flickering."""
    assert digits.POPULATION_MATCH_SCORE > digits.MIN_MATCH_SCORE
