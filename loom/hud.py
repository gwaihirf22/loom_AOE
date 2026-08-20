"""
Loom — which HUD skin am I looking at.

Every template Loom shipped until now was cut from the Anne_HK Better UI mod,
and with the stock HUD the anchor scored 0.743 against a 0.8 gate: the overlay
sat on "Waiting for a match to start..." forever, because `find_icon` was
finding the right icon and the score was refusing it.

A HudProfile is everything that differs between two skins of the same HUD. The
game draws the same numbers in the same order either way; what changes is the
ART (so each profile needs its own anchor templates) and the SPACING between
the anchor and the numbers (so each profile needs its own offsets). The digit
glyphs themselves are the game's own font and are shared - given the right
region, `templates/digits/` reads the stock bar unchanged.

One thing surprised me and is worth stating plainly: the glyph *metrics* have
to live here too. Stock population digits are 12px tall where the mod's are
15px, and the stock "/" is about 3px wide. The shared `min_glyph_width` of 6
discarded it, `_parse_population` then found no slash, and population read as
"no reading" on a HUD that was otherwise perfectly legible. A pixel constant
has to scale with the thing it bounds - and the thing it bounds turned out to
be the skin, not just the HUD scale slider.

Adding a third skin means one more entry here and two more template files.
Nothing else in Loom should need to learn about it.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Tuple

from . import paths


# eq=False so profiles hash and compare by IDENTITY. They are named
# singletons - there is one ANNEHK and one STOCK - and callers key template
# dictionaries on them, which a field-by-field hash cannot support once a
# field is a dict or a function. Identity is also the honest comparison: two
# profiles with the same numbers would still be two different skins.
@dataclass(frozen=True, eq=False)
class HudProfile:
    """One HUD skin: where its anchors are and what hangs off them.

    Every region is (x1, y1, x2, y2) in REFERENCE PIXELS relative to the
    anchor template's top-left corner, at the scale the template was cut at.
    `anchor.scale_region` multiplies them by the scale the icon was found at.
    """

    name: str
    pop_icon: Path            # the anchor: the population icon
    wood_icon: Path           # the queue's own anchor, in the same bar
    villager_region: Tuple[int, int, int, int]
    clock_band: Tuple[int, int, int, int]
    population_band: Tuple[int, int, int, int]
    slot_one: Tuple[float, float, float, float]  # queue cell 1, from wood
    resource_dir: Path        # where this skin's four resource icons live
    number_strip: dict        # the villagers-on-this-resource number
    min_glyph_width: Callable[[float], int]
    max_glyph_width: Callable[[float], int]


# --- glyph metrics -------------------------------------------------------
#
# Both are "reference pixels, scaled, then clamped". The clamps matter more
# than they look: a gate that grows with the HUD once ate the "1" out of
# 19/25 and reported 9/25, and a gate that stayed fixed while the font grew
# split single digits in half. See reader.min_glyph_width for that history -
# these functions are the per-skin version of the same idea.


def _annehk_min_glyph_width(scale):
    """Narrowest run the mod's bar can call a glyph."""
    return max(4, min(int(6 * scale), 6))


def _annehk_max_glyph_width(scale):
    """Widest run before the mod's bar calls it two glyphs touching."""
    return max(13, int(13 * scale))


def _stock_min_glyph_width(scale):
    """Narrowest run the stock bar can call a glyph.

    Measured, not guessed: stock population reads 4/10 correctly at 4 and
    fails at 5, because the stock slash is ~3px wide where the mod's is ~5.
    The floor of 3 keeps the gate above single-pixel speckle at the smallest
    HUD sizes; below about 0.75 scale the slash is thinner than any gate can
    separate from noise, and "no reading" is the right answer there.
    """
    return max(3, min(int(4 * scale), 4))


def _stock_max_glyph_width(scale):
    """Widest run before the stock bar calls it two glyphs touching.

    Scaled down from the mod's 13 in the same proportion as the font (stock
    glyphs are 12px tall against 15), so it stays clear of a single wide
    digit while still splitting a slash that brushes its neighbour.
    """
    return max(10, int(10 * scale))


# --- the skins -----------------------------------------------------------

# Cut from a 2560x1440 capture at HUD slider 100%, template origin (541, 9).
# These are the values anchor.py has carried since milestone 1; they live
# there still as module constants so nothing that imports them breaks.
ANNEHK = HudProfile(
    name="annehk",
    pop_icon=paths.POP_ICON_TEMPLATE,
    wood_icon=paths.TEMPLATES_DIR / "wood_icon.png",
    villager_region=(14, 31, 60, 56),
    clock_band=(545, -8, 810, 30),
    population_band=(58, 14, 162, 44),
    slot_one=(-4.5, 60.5, 43.5, 108.5),
    resource_dir=paths.TEMPLATES_DIR,
    # The mod prints the villagers-on-this-resource number BELOW its icon, in
    # yellow. Scanned as a generous strip rather than a tight box per
    # resource, because the number is not in quite the same spot for each.
    number_strip={"left": -4, "right": 62, "top": 22, "bottom": 52},
    min_glyph_width=_annehk_min_glyph_width,
    max_glyph_width=_annehk_max_glyph_width,
)

# Cut from tests/data/frames/hud_2560x1440_stock_portuguese.png - a live stock
# game, HUD slider set as high as it goes (the game reports 99% however it is
# set). That frame DEFINES stock scale 1.0, the same way the modded reference
# frame defines Anne_HK's; a 1% offset is well inside the +/-5% the reader
# tolerates before it warns.
#
# Template origin (537, 10), and the cut is EXACTLY the icon's own black box,
# stopping above the villager badge at y=51.
#
# That boundary is the whole lesson of this profile. My first cut reached out
# into the wooden bar texture around the icon, because the chrome separated
# stock from the mod cleanly where the portrait alone did not. It also broke
# instantly on Portuguese: the resource bar's border art is drawn PER CIV, and
# the light stone-and-vines set shares nothing with the dark wood I measured -
# the anchor fell to 0.59 and matched the wrong place entirely. Measured
# across two civs, the icon's black box is pixel-identical (max channel
# difference 2) while the chrome around it differs on 83% of its pixels.
#
# Nothing that changes may sit inside an anchor: not the border art, not the
# villager badge, not a resource sub-count. What replaced the chrome as the
# discriminator is corroboration between two icons - see identify_hud.
STOCK = HudProfile(
    name="stock",
    pop_icon=paths.STOCK_TEMPLATES_DIR / "pop_icon.png",
    wood_icon=paths.STOCK_TEMPLATES_DIR / "wood_icon.png",
    # The villager count is a small badge in the icon's bottom-right corner,
    # stamped on the portrait rather than sitting in a dark box. It is just
    # below where the anchor template stops, which is not a coincidence: the
    # template stops there BECAUSE this changes.
    #
    # The band is far wider than the badge I measured it on, and that is the
    # point. The number is RIGHT-aligned, so it grows leftward as villagers
    # are trained: a box cut snug around the "3" of an opening position read
    # "2" out of "12" - it clipped the leading digit and reported the
    # remainder with total confidence. Three digits need room to x1=26, so
    # this leaves a little beyond that. The left edge cannot go much further
    # without taking in the portrait; digits.read_count's colourless pass is
    # what keeps the portrait out of the reading.
    #
    # The TOP edge matters as much as the left, and for a different reason.
    # At y=35 the band reaches up into the banner art above the number.
    # Where a digit's columns also carry a speck of that art, the glyph's
    # row-trim spans from the speck down to the digit and squashes the digit
    # into the bottom half of a 14x20 box - so it is compared against the
    # templates in a shape it never had on screen. Measured across five
    # stock runs: at y=35 the glyphs score a median 0.32 at 1080p and 0.28
    # at 1440p, with most of them under the 0.55 match gate; at y=40 the
    # same frames score 0.75 and 0.78 with almost none under it. It is not
    # a resolution bug - the banner scales with everything else - which is
    # why it went unnoticed while only the badge fallback pass, reading the
    # leading digit alone, kept answering.
    #
    # 40 rather than 41 (identical on every frame measured) for the extra
    # row of headroom under the digits; 39 lets the banner back in and 42
    # starts clipping the digit tops at 1080p.
    villager_region=(24, 40, 58, 56),
    # The clock text sits at x 1123-1202, y 12-27, so the LEFT edge is the
    # one that has to be right - the mod's offset put it 111px too far along,
    # reading the speed text instead of the clock.
    #
    # The right edge is deliberately far past the clock. Stock prints
    # "00:00:34 (Normal - 1.7) (Standard)" and _parse_clock isolates the
    # HH:MM:SS group regardless: I measured the same 34 with the edge
    # anywhere from 1210 to 1580, so width costs nothing here. It is not free
    # in the other direction - a snug 700 bracketed the clock at the scale I
    # measured it at and clipped the last digit at 0.67, because the font does
    # not shrink at quite the rate the icon art does. A band must stay on the
    # right side of what it bounds at EVERY scale, not just its own.
    clock_band=(573, -8, 773, 28),
    # "4/10" starts at x=602. The right edge stops at x=682, short of the
    # idle-villager bell at ~685, so the bell's own digits can never leak in -
    # the same reasoning as the mod's band, at stock's spacing.
    population_band=(58, 18, 145, 40),
    # Fitted by maximising edge energy under a 48x48 outline: stock's first
    # cell sits at (1, 75) with the wood icon template's origin at (10, 11).
    # The mod's grid was fitted the same way. Only the ORIGIN differs -
    # SLOT_PITCH and ROW_PITCH are the game's own grid and stay shared.
    slot_one=(-9, 64, 39, 112),
    resource_dir=paths.STOCK_TEMPLATES_DIR,
    # Stock does not print that number below the icon: it stamps it INSIDE
    # the icon's box, bottom-right, in white - the same badge the population
    # icon carries its villager count in. So this strip is the badge box, and
    # like the villager band it is cut wide enough for the number to grow
    # leftward. The four icon boxes are civ-identical (max channel difference
    # 6 across two civs) and sit at x 10, 142, 274, 406 on a 2560-wide bar.
    number_strip={"left": 22, "right": 57, "top": 35, "bottom": 56},
    min_glyph_width=_stock_min_glyph_width,
    max_glyph_width=_stock_max_glyph_width,
)

# Detection order. Nothing depends on it - the winner is whichever scores
# highest above the reader's gate, and the two separate by ~0.15 in both
# directions - but the mod goes first because it is what I play with.
PROFILES = (ANNEHK, STOCK)

DEFAULT = ANNEHK


def by_name(name):
    """The profile with this name, or None. For --hud and debug tools."""
    for profile in PROFILES:
        if profile.name == name:
            return profile
    return None
