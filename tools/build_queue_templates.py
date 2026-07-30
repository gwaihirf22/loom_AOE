"""
Loom — build the queue-icon template set (development tool).

The global queue renders each queued item as the standard unit/tech portrait,
but zoomed in about 1.25x compared to the reference art (the game crops the
edges off). I measured that zoom by sweeping match scores against live capture
frames: at 1.0 a man-at-arms cell scores 0.23 against its own icon, at 1.25 it
scores ~0.33 and clearly beats every wrong icon.

This tool cuts ready-to-match templates from my local icon library
(master_aoe2_images/, not committed) and writes them into templates/queue/
(committed), pre-zoomed so the matcher never has to think about it. Run it
again whenever a new unit or tech needs to be recognisable:

    python -m tools.build_queue_templates

Names follow the RTS Overlay token vocabulary (villager, man_at_arms, ...) so
queue identities line up with build-order steps without a translation table.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import os
import pathlib

import cv2

from loom import paths

# How much the in-game queue portrait is zoomed compared to the library art.
ZOOM = 1.25

# The side of the finished template in pixels. The live cell is 48 reference
# pixels; 40 leaves room for the template to slide a little inside the cell,
# which absorbs the grid's sub-pixel drift.
TEMPLATE_SIZE = 40

# What to recognise, and where its library art lives. Deliberately only the
# things that can appear in a Dark-through-Castle queue plus the common siege -
# every template added is one more comparison per identification, so the set
# should grow with need, not ambition.
SOURCES = {
    # units. Wiki art first: it is blue-tinted like an actual player-one
    # queue and out-scores the game's neutral-grey DDS icons on real capture
    # cells (measured 0.59-0.63 vs 0.49-0.57). The game files ride along as
    # variants - they win occasionally (the amber-washed ram) and they are
    # the ONLY source for units the wiki set lacks. The NNN numbers are the
    # units' icon_id from the game's .dat, mapped and visually verified
    # against the shipped units/NNN_50730.DDS sheet.
    "villager_male": ["resource/MaleVillDE.webp", "DDS:units/015_50730.DDS"],
    "villager_female": ["resource/FEMALEVILLDE.webp", "DDS:units/016_50730.DDS"],
    "militia": ["barracks/MilitiaDE.webp", "DDS:units/008_50730.DDS"],
    "man_at_arms": ["barracks/Manatarms_aoe2DE.webp", "DDS:units/010_50730.DDS"],
    "spearman": ["barracks/Spearman_aoe2DE.webp", "DDS:units/031_50730.DDS"],
    "pikeman": "DDS:units/011_50730.DDS",
    "archer": ["archery_range/Archer_aoe2DE.webp", "DDS:units/017_50730.DDS"],
    "skirmisher": ["archery_range/Skirmisher_aoe2DE.webp", "DDS:units/020_50730.DDS"],
    "crossbowman": ["archery_range/Crossbowman_aoe2DE.webp", "DDS:units/018_50730.DDS"],
    "scout_cavalry": ["stable/Scoutcavalry_aoe2DE.webp", "DDS:units/064_50730.DDS"],
    "light_cavalry": ["stable/Lightcavalry_aoe2DE.webp", "DDS:units/091_50730.DDS"],
    "knight": ["stable/Knight_aoe2DE.webp", "DDS:units/001_50730.DDS"],
    "camel_rider": "DDS:units/078_50730.DDS",
    "eagle_scout": "DDS:units/109_50730.DDS",
    # Monk and trade cart icons are REGIONAL: the .dat reassigns them per
    # civ. Base art plus the Middle-Eastern and East-Asian variants cover
    # the common ladder civs; more can be added as captures demand.
    "monk": ["monastery/Monk_aoe2DE.webp", "DDS:units/169_50730.DDS",
             "DDS:units/218_50730.DDS"],
    "battering_ram": ["siege_workshop/Battering_ram_aoe2DE.webp",
                      "DDS:units/074_50730.DDS"],
    "mangonel": ["siege_workshop/Mangonel_aoe2DE.webp", "DDS:units/027_50730.DDS"],
    "fishing_ship": ["dock/FishingShipDE.webp", "DDS:units/024_50730.DDS"],
    "galley": ["dock/Galley_aoe2DE.webp", "DDS:units/087_50730.DDS"],
    "transport_ship": "dock/Transportship_aoe2DE.webp",
    "trade_cart": ["market/Tradecart_aoe2DE.webp", "DDS:units/034_50730.DDS",
                   "DDS:units/155_50730.DDS"],
    # ages - MULTIPLE variants each, because the age-up shield art changes
    # with the civilization's architecture region. The wiki art matched one
    # civ's queue at 0.93 while another civ's age-up went unrecognised and
    # read as an idle TC. The game's own icon files (converted from DDS)
    # cover the default style; more variants get added as captures reveal
    # civs that match neither.
    "feudal_age": ["age/FeudalAgeIconDE.webp", "DDS:tech/030_feudal_age.DDS"],
    "castle_age": ["age/CastleAgeIconDE.webp", "DDS:tech/031_castle_age.DDS"],
    "imperial_age": ["age/ImperialAgeIconDE.webp", "DDS:tech/032_imperial_age.DDS"],
    # town centre techs - the COMPLETE set, verified against the in-game tech
    # tree: an identity here with no template reads as an idle TC, so every
    # one of the eight must be present (tests/test_queue.py guards this).
    # Primary source is the game's own icon file (DDS:), which is exactly
    # what the queue renders on this machine at this patch; the wiki art
    # rides along as a second variant. Unit icons cannot come from the game
    # files the same way yet: they are ID-numbered (units/NNN_50730.DDS),
    # and mapping IDs to names needs the SiegeEngineers/aoe2techtree
    # data.json - a future curated-updater tool, per the roadmap.
    "loom": ["DDS:tech/006_loom.DDS", "town_center/LoomDE.webp"],
    "town_watch": ["DDS:tech/069_town_watch.DDS", "town_center/TownWatchDE.webp"],
    "town_patrol": ["DDS:tech/089_town_patrol.DDS", "town_center/TownPatrolDE.webp"],
    "wheelbarrow": ["DDS:tech/079_wheelbarrow.DDS", "town_center/WheelbarrowDE.webp"],
    "hand_cart": ["DDS:tech/042_hand_cart.DDS", "town_center/HandcartDE.webp"],
    # The one non-villager unit a TC can train: Burgundians after Flemish
    # Revolution. Unique TECHS never show in a TC queue (they research at the
    # Castle), but this unique UNIT does.
    "flemish_militia": "unique_unit/Aoe2-icon-flemish-militia.webp",
    # economy techs
    "double_bit_axe": "lumber_camp/DoubleBitAxe_aoe2DE.webp",
    "bow_saw": "lumber_camp/BowSawDE.webp",
    "two_man_saw": "lumber_camp/TwoManSawDE.webp",
    "horse_collar": "mill/HorseCollarDE.webp",
    "heavy_plow": "mill/HeavyPlowDE.webp",
    "gold_mining": "mining_camp/GoldMiningDE.webp",
    "stone_mining": "mining_camp/StoneMiningDE.webp",
    "coinage": "market/CoinageDE.webp",
    "masonry": "university/Masonry_aoe2de.webp",
    "ballistics": "university/BallisticsDE.webp",
}

LIBRARY_DIR = paths.PROJECT_ROOT / "master_aoe2_images"
OUTPUT_DIR = paths.TEMPLATES_DIR / "queue"

# Where the game's own icon textures live. "DDS:" sources resolve from here -
# they are the authoritative art for what the queue actually renders on this
# machine, at this patch.
GAME_TEXTURES = (pathlib.Path.home() / ".local/share/Steam/steamapps/common"
                 / "AoE2DE/widgetui/textures/ingame")


def load_source(spec):
    """Load one source image, BGR. Understands library paths and "DDS:..."."""
    if spec.startswith("DDS:"):
        # The game ships these as DDS, which OpenCV cannot read - Pillow can.
        # Imported lazily so the tool still builds the library-only templates
        # on a machine without Pillow or without the game installed.
        from PIL import Image
        import numpy as np
        path = GAME_TEXTURES / spec[len("DDS:"):]
        if not path.exists():
            # The unit sheet flips extension case partway through (frames
            # 000-342 are .DDS, 343+ are .dds).
            swapped = path.with_suffix(".dds" if path.suffix == ".DDS"
                                       else ".DDS")
            if not swapped.exists():
                return None
            path = swapped
        rgb = np.array(Image.open(path).convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return cv2.imread(str(LIBRARY_DIR / spec))


def cut_dds_template(image):
    """Cut a template from a game texture, which pads its art with margins.

    The queue draws the icon's content nearly full-bleed, so I crop to the
    non-black content, pad back to a square (kite shields are narrow - the
    queue centres them rather than stretching), and size down.
    """
    import numpy as np
    bright = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) > 12
    ys, xs = np.nonzero(bright)
    cropped = image[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    height, width = cropped.shape[:2]
    side = max(height, width)
    square = np.zeros((side, side, 3), np.uint8)
    y0 = (side - height) // 2
    x0 = (side - width) // 2
    square[y0:y0 + height, x0:x0 + width] = cropped
    return cv2.resize(square, (TEMPLATE_SIZE, TEMPLATE_SIZE),
                      interpolation=cv2.INTER_AREA)


def cut_template(library_image):
    """Zoom into the library art the way the game does, then shrink to size."""
    # Resizing to (48 * ZOOM) and keeping the central 48-equivalent region is
    # the same as cropping the outer fifth of the art; going straight to the
    # final template size in one resize avoids a second interpolation pass.
    zoomed_side = int(TEMPLATE_SIZE * ZOOM)
    zoomed = cv2.resize(library_image, (zoomed_side, zoomed_side),
                        interpolation=cv2.INTER_AREA)
    margin = (zoomed_side - TEMPLATE_SIZE) // 2
    return zoomed[margin:margin + TEMPLATE_SIZE, margin:margin + TEMPLATE_SIZE]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    written, missing = 0, []
    for name, specs in SOURCES.items():
        if isinstance(specs, str):
            specs = [specs]
        for index, spec in enumerate(specs):
            image = load_source(spec)
            if image is None:
                missing.append(spec)
                continue
            # Tech/age DDS art carries big black margins the queue crops
            # away (bbox-cut); unit DDS portraits render edge-cropped like
            # the wiki art, so they take the same 1.25x zoom cut - measured
            # against labelled capture cells, zoom beats bbox for units.
            if spec.startswith("DDS:units/"):
                cut = cut_template(image)
            elif spec.startswith("DDS:"):
                cut = cut_dds_template(image)
            else:
                cut = cut_template(image)
            # First variant keeps the plain name; the rest carry a suffix
            # after a dot - load_icon_templates groups on the first dot.
            filename = f"{name}.png" if index == 0 else f"{name}.{index + 1}.png"
            cv2.imwrite(str(OUTPUT_DIR / filename), cut)
            written += 1

    print(f"Wrote {written} templates to {OUTPUT_DIR}/")
    for spec in missing:
        print(f"  MISSING source: {spec}")


if __name__ == "__main__":
    main()
