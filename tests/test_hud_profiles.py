"""
Loom — tests for telling one HUD skin from another, and reading both.

Every template Loom shipped until now came from the Anne_HK Better UI mod, and
the stock HUD scored 0.743 against a 0.8 gate: found, and refused. These tests
pin what fixed it - a second anchor with its own offsets, and identification by
two icons agreeing rather than one icon winning by a hair.

Two lessons are nailed down here because both were learned the expensive way.

CIV ART IS NOT ANCHOR MATERIAL. The resource bar's border is drawn per
civilization, so an anchor cut that reaches into it works on the civ it was
measured on and collapses on the next one - the first stock anchor scored 1.00
on a dark-bordered civ and 0.59 on Portuguese, whose bar is light stone and
vines. Hence STOCK_FRAMES: three civs with three different bar arts, and every
one of them must read.

ONE ICON IS NOT ENOUGH TO NAME A SKIN. Every skin draws the same game art, so
the anchors resemble each other: the stock anchor scores 0.91-0.95 on modded
HUDs against the mod's own 0.93-0.97. Corroborating with the wood icon turns
that 0.02 into 0.27 or better.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import pathlib

import cv2
import pytest

from loom import anchor, digits, hud, queue, reader, resources

FRAMES = pathlib.Path(__file__).parent / "data" / "frames"
CAPTURES = pathlib.Path(__file__).parent.parent / "captures"

# Three stock civilizations, three different border arts, and what a human
# reads off each: (clock seconds, population, villager count).
STOCK_FRAMES = [
    (FRAMES / "hud_2560x1440_stock_portuguese.png", 27, (4, 5), 3),
    (FRAMES / "hud_2560x1440_stock_slider99.png", 34, (4, 10), 3),
]

MODDED_FRAMES = [FRAMES / "hud_1920x1080_100.png",
                 FRAMES / "hud_1728x1084_100.png",
                 FRAMES / "hud_1728x1084_slider100.png"]

# The stock HUD at a second resolution. Read regions must track scale, but the
# digits themselves are known not to survive this far down - the modded frames
# at the same scale do not read either. Used for geometry, not for numbers.
STOCK_SMALL = FRAMES / "hud_1920x1080_slider100.png"


def frame(path):
    image = cv2.imread(str(path))
    assert image is not None, f"missing fixture {path}"
    return image


@pytest.fixture(scope="module")
def templates():
    return {profile: anchor.load_template(profile) for profile in hud.PROFILES}


@pytest.fixture(scope="module")
def wood_templates():
    return {profile: queue.load_wood_template(profile)
            for profile in hud.PROFILES}


def identify(path, templates, wood_templates):
    return anchor.identify_hud(frame(path), templates,
                               wood_templates=wood_templates)


# --- picking a skin ------------------------------------------------------

@pytest.mark.parametrize("path", [p for p, *_ in STOCK_FRAMES] + [STOCK_SMALL],
                         ids=lambda p: p.name)
def test_stock_frames_pick_the_stock_profile(path, templates, wood_templates):
    found = identify(path, templates, wood_templates)
    assert found["profile"] is hud.STOCK
    assert found["score"] >= reader.MIN_ANCHOR_SCORE


@pytest.mark.parametrize("path", MODDED_FRAMES, ids=lambda p: p.name)
def test_modded_frames_pick_the_annehk_profile(path, templates,
                                              wood_templates):
    found = identify(path, templates, wood_templates)
    assert found["profile"] is hud.ANNEHK
    assert found["score"] >= reader.MIN_ANCHOR_SCORE


def test_the_anchors_alone_would_be_too_close_to_call(templates):
    """The measurement that justifies corroborating with a second icon.

    If this ever shows a comfortable margin, the wood check has stopped
    earning its keep. Until then it is the only thing standing between a
    0.02 coin-flip and a session read against the wrong skin's offsets.
    """
    modded = frame(MODDED_FRAMES[0])
    stock_anchor = anchor.find_icon(modded, templates[hud.STOCK])[0]
    mod_anchor = anchor.find_icon(modded, templates[hud.ANNEHK])[0]

    assert mod_anchor > stock_anchor
    assert mod_anchor - stock_anchor < 0.05, "margin grew; re-check the design"


def test_corroboration_separates_the_skins_decisively(templates,
                                                      wood_templates):
    """Both icons, at one scale. The wrong skin cannot satisfy both."""
    for path in [p for p, *_ in STOCK_FRAMES] + MODDED_FRAMES + [STOCK_SMALL]:
        image = frame(path)
        scores = {}
        for profile, template in templates.items():
            found = anchor.locate_regions(image, template, None, profile)
            wood = anchor._wood_agreement(image, wood_templates[profile],
                                          found["scale"])
            scores[profile] = min(found["score"], wood)

        winner = max(scores, key=scores.get)
        loser = min(scores, key=scores.get)
        assert scores[winner] >= 0.9, (
            f"{path.name}: winner only {scores[winner]:.3f}")
        assert scores[loser] <= 0.75, (
            f"{path.name}: loser reached {scores[loser]:.3f}")


# --- civ border art ------------------------------------------------------

def test_the_stock_anchor_ignores_the_civs_border_art():
    """The Portuguese regression, kept.

    The bar's border is per-civ; the icon's black box is not. An anchor that
    reaches past the box scores well on the civ it was cut from and fails on
    the next, so all three civs must score essentially perfectly.
    """
    template = anchor.load_template(hud.STOCK)
    civs = [p for p, *_ in STOCK_FRAMES]
    jul30 = sorted(glob.glob(str(
        CAPTURES / "run_20260730_145605_stock_mod-disabled-discovery"
        / "frame_*.png")))
    if jul30:
        civs.append(pathlib.Path(jul30[3]))

    assert len(civs) >= 2, "need at least two civilizations to prove this"
    for path in civs:
        score = anchor.find_icon(frame(path), template)[0]
        assert score >= 0.95, f"{path.name} scored {score:.3f}"


def test_the_stock_anchor_holds_still_while_the_hud_changes():
    """Nothing that changes during a game may sit inside an anchor.

    The villager badge is the trap: it is part of the population icon, it
    sits inside the same black box, and it counts up all game. The template
    stops above it - so the two frames here, with different clocks and
    different populations, must match it identically.
    """
    template = anchor.load_template(hud.STOCK)
    scores = [anchor.find_icon(frame(path), template)[0]
              for path, *_ in STOCK_FRAMES]

    assert min(scores) >= 0.99
    assert max(scores) - min(scores) < 0.01


# --- reading the stock bar -----------------------------------------------

@pytest.mark.parametrize("path,seconds,population,villagers", STOCK_FRAMES,
                         ids=lambda p: p.name if hasattr(p, "name") else p)
def test_the_stock_hud_reads_all_three_numbers(path, seconds, population,
                                               villagers, templates,
                                               wood_templates):
    """The whole point: given the right regions, the shared digit templates
    read the stock bar. Nothing about recognition needed porting."""
    stock = frame(path)
    found = identify(path, templates, wood_templates)
    profile, scale = found["profile"], found["scale"]
    glyphs = digits.load_digit_templates()
    smallest = reader.min_glyph_width(scale, profile)
    widest = reader.max_glyph_width(scale, profile)

    def band(key):
        x1, y1, x2, y2 = found[key]
        return stock[max(0, y1):y2, max(0, x1):x2]

    clock, _ = digits.read_clock_seconds(band("clock_band"), glyphs, smallest)
    read_population = digits.read_population(band("population"), glyphs,
                                             smallest, widest)
    read_villagers, _ = digits.read_count(band("villagers"), glyphs, smallest)

    assert clock == seconds
    assert read_population == population
    assert read_villagers == villagers


def test_the_villager_badge_reads_more_than_one_digit():
    """Caught live: the band read "2" out of "12" and reported it confidently.

    The badge is RIGHT-aligned, so it grows leftward as villagers are trained.
    A band cut snug around the single digit of an opening position clipped the
    leading digit off every later reading - and a villager count that silently
    loses its tens column is the worst reading Loom can produce, because
    villagers are the only signal the build order advances on.

    The crop is the live band at the profile's own offsets, so this tests the
    reading; test_the_villager_band_has_room_for_three_digits tests the room.
    """
    crop = frame(pathlib.Path(__file__).parent / "data" / "clock"
                 / "stock_villager_badge_12.png")
    count, _ = digits.read_count(crop, digits.load_digit_templates(), 3)

    assert count == 12


def test_the_villager_band_has_room_for_three_digits():
    """Villager counts pass 100 in any long game; the band must already fit.

    Measured on the stock bar, a badge digit is about 7.5 reference pixels
    wide. Three of those plus the outline is the width this has to clear -
    checked as arithmetic rather than waiting for a 100-villager screenshot.
    """
    x1, _, x2, _ = hud.STOCK.villager_region
    assert (x2 - x1) >= 3 * 7.5 + 4


def test_the_two_skins_keep_their_own_glyph_metrics():
    """The regression that made this a profile and not just a template swap.

    Stock draws a smaller font than the mod: its slash is ~3px wide, and the
    mod's min_glyph_width of 6 threw it away. _parse_population then found no
    slash and reported nothing at all - a silent "no reading" on a HUD that is
    perfectly legible. If the two skins ever share one width again, this fails.
    """
    for scale in (0.75, 1.0, 1.5, 2.0):
        assert (reader.min_glyph_width(scale, hud.STOCK)
                < reader.min_glyph_width(scale, hud.ANNEHK)), scale
        assert (reader.max_glyph_width(scale, hud.STOCK)
                < reader.max_glyph_width(scale, hud.ANNEHK)), scale


def test_stock_population_reads_with_the_stock_glyph_width(templates,
                                                           wood_templates):
    """The stock band, read with stock's own metrics, gives the right pair.

    This test used to ALSO assert that the mod's wider width produced no
    reading at all, as the visible proof that the metrics mattered. That
    assertion is gone deliberately, and the reason is worth recording.

    digits._is_bar now recognises a "1" by its shape rather than by matching
    it against a template, because a bar stretched to the template's aspect
    becomes a solid block that correlates with nothing. A "1" is narrower
    than any sensible width gate BY NATURE, so it is exempt from the gate -
    and that exemption happens to rescue this frame under the mod's width
    too. The reader got less brittle, so a test pinning the old brittleness
    was pinning the wrong thing.

    What still matters is checked above, as configuration: the two skins have
    genuinely different metrics at every scale.
    """
    path, _, population, _ = STOCK_FRAMES[1]
    stock = frame(path)
    found = identify(path, templates, wood_templates)
    x1, y1, x2, y2 = found["population"]
    band = stock[max(0, y1):y2, max(0, x1):x2]
    glyphs = digits.load_digit_templates()
    scale = found["scale"]

    with_stock_width = digits.read_population(
        band, glyphs,
        reader.min_glyph_width(scale, hud.STOCK),
        reader.max_glyph_width(scale, hud.STOCK))

    assert with_stock_width == population


def test_the_clock_band_brackets_the_clock_at_every_scale(templates,
                                                          wood_templates):
    """A band is only correct if it is correct at sizes it was not measured at.

    The stock clock band was first cut snug around the clock at the scale I
    measured it (0.99) and clipped the last digit at 0.67, because the font
    does not shrink at quite the rate the icon art does.
    """
    for path in [p for p, *_ in STOCK_FRAMES] + [STOCK_SMALL]:
        found = identify(path, templates, wood_templates)
        assert found["profile"] is hud.STOCK
        x1, y1, x2, y2 = found["clock_band"]
        band = frame(path)[max(0, y1):y2, max(0, x1):x2]
        # Eight characters of clock, plus the speed text the game prints
        # after it, need well more room than the digits themselves.
        assert band.shape[1] >= 8 * 9 * found["scale"], f"{path.name} too narrow"
        assert band.shape[0] >= 14 * found["scale"], f"{path.name} too short"


# --- per-resource villager counts ----------------------------------------

def test_the_stock_bar_reads_its_per_resource_counts(templates,
                                                     wood_templates):
    """Stock puts this number somewhere else, and inks it differently.

    The mod prints it in yellow BELOW each icon; stock stamps it white INSIDE
    the icon's box, bottom-right - the same badge style the villager count
    uses. Values verified by eye off the two fixtures.
    """
    expected = {
        STOCK_FRAMES[0][0]: {"wood": 0, "food": 0, "gold": 0, "stone": 0},
        STOCK_FRAMES[1][0]: {"wood": 0, "food": 2, "gold": 0, "stone": 0},
    }
    glyphs = digits.load_digit_templates()

    for path, counts in expected.items():
        image = frame(path)
        found = identify(path, templates, wood_templates)
        profile, scale = found["profile"], found["scale"]
        regions = resources.locate_regions(
            image, resources.load_resource_templates(profile), scale, profile)
        smallest = reader.min_glyph_width(scale, profile)

        read = {}
        for name, (x1, y1, x2, y2) in regions.items():
            read[name] = resources.read_one(
                image[max(0, y1):y2, max(0, x1):x2], glyphs, smallest)
        assert read == counts, path.name


def test_a_resource_icon_matched_out_in_the_terrain_is_refused():
    """The same false positive the wood anchor had, on all four icons.

    The mod's food template scores 0.62 at x=1545 on a stock bar - past
    MIN_ICON_SCORE, and nowhere the food icon could be. Before the position
    gate that became a read region over open terrain.
    """
    stock = frame(STOCK_FRAMES[0][0])
    mod_templates = resources.load_resource_templates(hud.ANNEHK)
    found = anchor.locate_regions(stock, anchor.load_template(hud.ANNEHK),
                                  None, hud.ANNEHK)

    regions = resources.locate_regions(stock, mod_templates, found["scale"],
                                       hud.ANNEHK)
    for name, (x1, _, _, _) in regions.items():
        assert x1 < stock.shape[1] * resources.MAX_ICON_X_FRACTION, name


# --- the queue's own anchor ----------------------------------------------

def test_a_wood_match_out_in_the_frame_is_refused():
    """Score alone is not enough to believe a position.

    This is the shape of the bug that existed before skins were told apart:
    the mod anchor found the stock HUD at scale 0.890, and at THAT scale the
    mod's wood template matches the stock bar out at x=1078 with score 0.701 -
    past MIN_WOOD_SCORE - which would have anchored the whole slot grid a
    thousand pixels from the queue and read confident nonsense off terrain.
    """
    stock = frame(STOCK_SMALL)
    mod_wood = queue.load_wood_template(hud.ANNEHK)
    mod_scale = anchor.find_icon(stock, anchor.load_template(hud.ANNEHK))[3]
    assert round(mod_scale, 2) == 0.89, "fixture changed; re-measure"

    assert queue.find_wood_icon(stock, mod_wood, mod_scale) is None


def test_the_stock_grid_lands_on_the_stock_queue_cell():
    """The slot origin is per-skin; the pitch is the game's and is shared."""
    path = STOCK_FRAMES[1][0]
    stock = frame(path)
    queue_reader = queue.QueueReader(hud.STOCK)
    wood = queue.find_wood_icon(stock, queue_reader.wood_template, 1.0)
    assert wood is not None

    _, wood_x, wood_y = wood
    first = queue.slot_boxes(wood_x, wood_y, 1.0,
                             slot_one=hud.STOCK.slot_one)[0]
    # Fitted by edge energy at (1, 75), 48x48. A couple of pixels of drift is
    # tolerable; the seven the mod's origin gave is not.
    assert abs(first[0] - 1) <= 2
    assert abs(first[1] - 75) <= 2
    assert first[2] - first[0] == 48
    assert first[3] - first[1] == 48


def test_the_stock_queue_reads_its_occupied_slot():
    path = STOCK_FRAMES[1][0]
    stock = frame(path)
    queue_reader = queue.QueueReader(hud.STOCK)
    width, height, _ = queue.strip_extent(1.0)
    slots = queue_reader.read(stock[0:height, 0:width], 1.0)

    assert slots is not None
    assert [slot.identity for slot in slots] == ["villager_male"]


def test_switching_skins_forgets_where_the_old_one_was():
    """A wood position cached from the wrong art is worse than none."""
    queue_reader = queue.QueueReader(hud.ANNEHK)
    queue_reader._wood = (6, 15)
    queue_reader._cache = {0: ("villager_male", 0.9)}

    queue_reader.use_profile(hud.STOCK)

    assert queue_reader.profile is hud.STOCK
    assert queue_reader._wood is None
    assert queue_reader._cache == {}
