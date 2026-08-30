"""
Loom — build the queue-icon template set (development tool).

The global queue renders each queued item as the standard unit/tech portrait,
but zoomed in about 1.25x compared to the reference art (the game crops the
edges off). I measured that zoom by sweeping match scores against live capture
frames: at 1.0 a man-at-arms cell scores 0.23 against its own icon, at 1.25 it
scores ~0.33 and clearly beats every wrong icon.

This tool cuts ready-to-match templates from my local icon library
(master_aoe2_images/, which ships) and writes them into templates/queue/
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
import sys

import cv2

from loom import paths, queue
from tools import civ_reference

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
    # The captured cell rides beside the wiki art: live pixels are the
    # art that reliably wins on live pixels. This fixture spent a week
    # mislabelled as a battering ram - a parade of techs "outscored the
    # ram" on it precisely because it is not one.
    "hussite_wagon": ["unique_unit/Aoe2-icon-hussite-wagon.webp",
                      "CELL:tests/data/queue/amber_hussite_wagon_x7.png"],
    "war_wagon": "unique_unit/WarWagonIcon-DE.webp",
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
    # Units whose art leans villager. Audited 2026-07-31: every master
    # library image was rendered as a fake queue cell and pushed through
    # identify() against the then-current template set; these were the
    # ones whose BEST match was a villager (bare skin and robes look
    # villager-shaped once zoomed to 40px). An identity wins by default
    # when the real unit's template is missing, and a green-washed
    # "villager" is TC evidence - so each offender gets its own template
    # to out-compete the villagers. Ordered by measured villager score.
    "slinger": "unique_unit/SlingerIcon-DE.webp",
    "temple_guard": "unique_unit/Temple_Guard.webp",
    "janissary": "unique_unit/JanissaryIcon-DE.webp",
    "guecha_warrior": "unique_unit/Guecha_Warrior.webp",
    "elite_champi_warrior": "barracks/Elite_Champi_Warrior.webp",
    "champi_runner": "barracks/Champi_Runner.webp",
    "gbeto": "unique_unit/GbetoIcon-DE.webp",
    "pikeman_upgrade": "barracks/PikemanUpDE.webp",
    "two_handed_swordsman": "barracks/Twohanded_aoe2DE.webp",
    "chu_ko_nu": "unique_unit/ChukoNuIcon-DE.webp",
    "jaguar_warrior": "unique_unit/JaguarWarriorIcon-DE.webp",
    "fire_archer": "unique_unit/Fire_Archer.webp",
    "throwing_axeman": "unique_unit/ThrowingAxemanIcon-DE.webp",
    "champion": "barracks/Champion_aoe2DE.webp",
    "blackwood_archer": "unique_unit/Blackwood_Archer.webp",
    "rattan_archer": "unique_unit/Rattanarchericon-DE.webp",
    "ratha": "unique_unit/Aoe2de_ratha_ranged.webp",
    "ibirapema_warrior": "unique_unit/Ibirapema_Warrior.webp",
    "karambit_warrior": "unique_unit/Karambitwarrioricon-DE.webp",
    "kona": "unique_unit/Kona.webp",
    "hand_cannoneer": "archery_range/Hand_cannoneer_aoe2DE.webp",
    "traction_trebuchet": "siege_workshop/Traction_Trebuchet.webp",
    "arbalester": "archery_range/Arbalester_aoe2DE.webp",
    "dromon": "unique_unit/Dromon-DE.webp",
    # Not flagged (it currently reads as "archer", which is harmless),
    # but the user reports a live longbow queue once minting TCs - washes
    # shift scores, so it earns its own name.
    "longbowman": "unique_unit/LongbowmanIcon-DE.webp",
    # A castle tech whose art leaned villager; research shows in the
    # queue, so it needs a template like any queueable item.
    "hoardings": "castle/HoardingsDE.webp",
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

# Where the game's own icon textures live, under whichever install this
# machine has. "DDS:" sources resolve from here - they are the authoritative
# art for what the queue actually renders on this machine, at this patch.
#
# This used to be a hard-coded Linux Steam path, and the failure was not a
# missing feature, it was DESTRUCTIVE and silent. main() deletes every
# template before it writes any, and the whole tech sweep sits behind an
# `if it exists` - so running this on Windows deleted 526 templates and wrote
# back about 315, losing 181 identities without a word. civ_reference already
# knows how to find the game: it parses Steam's libraryfolders.vdf (mine is on
# E:, which no amount of guessing at Program Files would reach) and honours
# LOOM_AOE2_DIR.
TEXTURES_UNDER_INSTALL = pathlib.Path("widgetui/textures/ingame")

_textures = None


def game_textures():
    """The install's ingame texture folder, or None if the game is not here.

    Looked up on demand rather than at import, so importing this module costs
    nothing and stays safe on a machine with no game - tests and the
    classifier both import it.
    """
    global _textures
    if _textures is None:
        install = civ_reference.find_install()
        _textures = (install / TEXTURES_UNDER_INSTALL) if install else False
    return _textures or None


def load_source(spec):
    """Load one source image, BGR. Understands library paths and "DDS:..."."""
    if spec.startswith("CELL:"):
        # A live captured queue cell (48px): already game-rendered at
        # queue zoom, so it only needs the center cut to template size.
        return cv2.imread(str(paths.PROJECT_ROOT / spec[len("CELL:"):]))
    if spec.startswith("DDS:"):
        # The game ships these as DDS, which OpenCV cannot read - Pillow can.
        # Imported lazily so the tool still builds the library-only templates
        # on a machine without Pillow or without the game installed.
        from PIL import Image
        import numpy as np
        path = game_textures() / spec[len("DDS:"):]
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


# The cut for the game's own UNIT portraits, kept apart from ZOOM above so a
# sweep of this one cannot move the library cut - which is the one that
# actually wins on live cells - underneath it.
#
# MEASURED AND REFUSED: (1.30, (-2, 0)). tools/cut_sweep.py exists now and it
# is worth keeping, but its recommendation did not survive the corpus gate
# and the reason is worth more than the constant would have been.
#
# What the sweep found, over 699 labelled cells: the window wants to be LEFT
# of centre. At every zoom from 1.20 to 1.40, top-1 falls monotonically as
# the window moves right (to 74-83% at +4) and is flat-to-better as it moves
# left; (1.30, -2) read 100% at all three HUD scales SEPARATELY and held junk
# terrain to 0.476 against this cut's 0.500. A gradient across five slices,
# not one lucky row.
#
# What it could not see, and neither could two rounds of fixing it:
#
#   1. Mean SCORE peaks at dx = 0 and at zoom 1.25 - it points straight back
#      at these constants while accuracy points elsewhere. The notification
#      font's lesson: a corpus score can rise while the thing gets worse.
#   2. (1.25, -2) took top-1 to 100% and pushed a JUNK terrain cell from
#      0.500 to 0.521, past the uncorroborated identity gate. Junk carries no
#      label, so it was never in the population being optimised; an invariant
#      test caught it. The sweep scores junk now.
#   3. And that is still not enough. `queue_report --check` refused
#      (1.30, -2): a Magyars SCOUTS game went 100.00% -> 97.16%, six cells
#      reading `knight` in a game that never ordered one. The labels come
#      from cells today's templates already read CONFIDENTLY, so the cells a
#      new cut damages are precisely the ones excluded from the measurement.
#      A scout cell that starts losing to a knight was never in the sample.
#
# So the sweep can compare cuts and it cannot approve one. Only the whole-run
# gate can, and it is the thing to run before believing any number above.
UNIT_DDS_ZOOM = ZOOM
UNIT_DDS_OFFSET = (0, 0)


def cut_unit_dds(image, zoom=None, offset=None):
    """Cut a unit portrait out of the game's own texture.

    Same shape as cut_template - zoom the art, keep a window of template size
    - but the window can be moved off centre. Only the zoom had ever been
    tried, and a centred window is an assumption rather than a measurement:
    the game crops these to fit a cell whose art is not necessarily centred
    in the source file.
    """
    zoom = UNIT_DDS_ZOOM if zoom is None else zoom
    dx, dy = UNIT_DDS_OFFSET if offset is None else offset
    side = max(TEMPLATE_SIZE, int(TEMPLATE_SIZE * zoom))
    zoomed = cv2.resize(image, (side, side), interpolation=cv2.INTER_AREA)
    margin = (side - TEMPLATE_SIZE) // 2
    # Clamped rather than wrapped or padded: a window that ran off the edge
    # would be part real art and part black, and the black would score as
    # agreement wherever the cell is dark.
    x0 = min(max(margin + dx, 0), side - TEMPLATE_SIZE)
    y0 = min(max(margin + dy, 0), side - TEMPLATE_SIZE)
    return zoomed[y0:y0 + TEMPLATE_SIZE, x0:x0 + TEMPLATE_SIZE]


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


# Directories whose ENTIRE remaining contents ride along automatically,
# named by a cleaned-up slug of the filename. The curated SOURCES above
# carry the names that matter (everything TC logic reasons about); these
# exist so the matcher has the RIGHT answer available for whatever else is
# researching or training. Two live phantoms forced this: the Husbandry
# horseshoe (no template) read as flemish_militia at 0.30 and the elite
# skirmisher upgrade shield read as villager_male at 0.53 - both green,
# both TC identities, both minted phantom TCs. The matcher can only be
# right about what it has templates for.
AUTO_DIRS = ("blacksmith", "university", "monastery", "market", "stable",
             "archery_range", "barracks", "dock", "mill", "lumber_camp",
             "mining_camp", "town_center", "castle", "siege_workshop")

# Library files that must NOT become templates. Buildings, walls and
# towers never appear in the global queue (only units and techs do), so
# their icons are pure confusion surface - the synthetic-decor invariant
# test caught a boxy building frame scoring 0.57 where junk must stay
# under 0.38. Anything else here is a measured thief: an auto template
# that outscored a correct identity on a real fixture cell.
AUTO_EXCLUDE = {
    "blacksmith/Blacksmith_aoe2de.webp",
    "university/University_AoE2_DE.webp",
    "university/BombardTower_aoe2DE.webp",
    "university/FortifiedWallDE.webp",
    "university/Tower_aoe2de.webp",
    "monastery/MonasteryAoe2DE.webp",
    "monastery/FortifiedChurch.webp",
    "market/Market_aoe2DE.webp",
    "archery_range/Archery_range_aoe2DE.webp",
    "dock/Dock_aoe2de.webp",
    "castle/Castle_aoe2DE.webp",
    "town_center/Towncenter_aoe2DE.webp",
    # Farm and pasture: flat field textures whose frames read like decor
    # (the synthetic-decor invariant measured farm at 0.57 where junk must
    # stay under 0.38); they are buildings, not queue items.
    "mill/FarmDE.webp",
    "mill/Mill_aoe2de.webp",
    "mill/Pasture.webp",
    # Supplies, and it is here for BOTH reasons this list exists.
    #
    # The filename is misspelled, so it slugs to "supllies" and the
    # collision-into-variant rule cannot see that the game's own
    # 124_supplies.DDS is the same technology - it became a second identity
    # for one thing, competing in every match.
    #
    # Curating it under the right name fixed that and cost more than it
    # saved: measured on run_20260826_142630_annehk, supplies then claimed
    # 3 of 3613 slot readings in a game that never researched it, and
    # queue_report --check refused the change. The game's own tech art is
    # what the queue actually renders, and it does not need a second
    # opinion from the wiki.
    "barracks/Suplliesicon.webp",
}

# Game tech files kept out of the automatic DDS sweep: filenames only,
# added when a template measurably steals a correct identity from a real
# fixture cell (same rule as AUTO_EXCLUDE).
DDS_TECH_EXCLUDE = {
    # Scenario and campaign-only techs that no standard queue can hold -
    # their flat art stole real fixture identities (sell_estate outscored
    # the battering ram) and made synthetic decor look convincing.
    "094_wood_trading_0x.dds", "095_wood_trading_4x.dds",
    "157_wood_trading_1x.dds", "158_wood_trading_2x.dds",
    "159_wood_trading_3x.dds", "204_ant_guard_tower.dds",
    "205_ant_keep.dds", "210_sell_estate.dds",
    "153_ant_imperial_age.dds", "176_amulet_protection.dds",
    "187_ant_elite_skirmisher.dds", "195_ant_war_galley.dds",
    "196_ant_elite_galley.dds", "222_add_health_regen.dds",
    "211_sell_athenas_gold.dds", "223_big_naval_upgrade.dds",
    "224_gold_to_naval_power.dds", "225_increase_attack_speed_1.dds",
    "226_increase_attack_speed_2.dds", "228_increase_speed_1.dds",
    "229_Increase_speed_2.dds",
    # Tower-line upgrades: real university research, but their boxy
    # tower art breaks the decor content-gate invariant. They return
    # with real fixture cells when a capture needs them.
    "016_keep.DDS", "076_guard_tower.DDS",
}


# The suffix a technology takes when a unit already owns its name.
UPGRADE_SUFFIX = "_upgrade"


def tech_identity(slug, existing, kinds):
    """What to file a tech icon under when something already holds its name.

    The game names an upgrade technology exactly like the unit it produces:
    029_crossbowman.DDS is the Crossbowman RESEARCH and 018_50730.DDS is the
    crossbowman. Folding the second into the first as a variant made one
    identity out of two different pictures - measured, the tech art scores
    1.000 against the variant it was filed as and 0.045 against the unit's own
    portrait, and across ten sampled pairs the range is 0.045-0.666. So Loom
    owned the research's picture, matched it at 0.784, and confidently
    reported a unit. classify_queue_icons already named the mechanism ("the
    ram and scorpion UPGRADE techs produce the same slug as the units") and
    stopped at the classifier without following it back to here.

    Merging is right when both files really are one thing - the wiki Loom
    icon beside the game's own - so the test is the recorded KIND, not the
    spelling: merge only into an identity KINDS.tsv calls a technology.
    Unit, building, animal or unknown all split, because an unrecorded kind
    is not evidence that merging is safe. That means a missing KINDS.tsv
    splits everything, which is loud rather than silent, and is the right
    way round.

    Only collisions with something written THIS run count. KINDS.tsv also
    carries rows for subjects the notification feed names and no template
    exists for; a tech icon sharing one of those names collides with nothing.
    """
    if slug not in existing:
        return slug
    if kinds.get(slug) == queue.TECHNOLOGY:
        return slug
    return slug + UPGRADE_SUFFIX


def auto_name(filename):
    """A template identity slug from a library filename."""
    import re
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"(?i)aoe2|aoe2de|icon|[-_ ]de$|de$", "", stem)
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem)
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return slug or "unnamed"


def main():
    # Checked BEFORE the directory is emptied. Without the game's textures
    # this tool does not build a smaller set, it builds a broken one - 181
    # identities and 30 curated variants short - and it used to do that in
    # silence on any machine whose install was not where the old hard-coded
    # path said. Saying where it looked is the actionable half: "no install
    # found" and "I never checked your other drive" are different messages.
    if game_textures() is None:
        print("Cannot build: the game's textures are not where I looked.",
              file=sys.stderr)
        for candidate in civ_reference.install_candidates():
            print(f"  tried {candidate}", file=sys.stderr)
        print(f"Set {civ_reference.INSTALL_ENV} to the install directory.",
              file=sys.stderr)
        return 1
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Start clean: every template is generated from a source, and the
    # collision-into-variant rule scans the directory - rebuilding on top
    # of a previous build would re-add the whole set as bogus variants
    # (it did: 523 templates became 952 on the second run).
    for stale in pathlib.Path(OUTPUT_DIR).glob("*.png"):
        stale.unlink()
    written, missing, split = 0, [], []
    used_specs = set()
    used_names = set()

    def write_template(name, spec, index):
        nonlocal written
        image = load_source(spec)
        if image is None:
            missing.append(spec)
            return
        # Tech/age DDS art carries big black margins the queue crops
        # away (bbox-cut); unit DDS portraits render edge-cropped like
        # the wiki art, so they take the same 1.25x zoom cut - measured
        # against labelled capture cells, zoom beats bbox for units.
        if spec.startswith("CELL:"):
            # Already a live cell at queue zoom: center-cut to size.
            h, w = image.shape[:2]
            y0, x0 = (h - TEMPLATE_SIZE) // 2, (w - TEMPLATE_SIZE) // 2
            cut = image[y0:y0 + TEMPLATE_SIZE, x0:x0 + TEMPLATE_SIZE]
        elif spec.startswith("DDS:units/"):
            cut = cut_unit_dds(image)
        elif spec.startswith("DDS:"):
            cut = cut_dds_template(image)
        else:
            cut = cut_template(image)
        # First variant keeps the plain name; the rest carry a suffix
        # after a dot - load_icon_templates groups on the first dot.
        filename = f"{name}.png" if index == 0 else f"{name}.{index + 1}.png"
        cv2.imwrite(str(OUTPUT_DIR / filename), cut)
        written += 1

    for name, specs in SOURCES.items():
        if isinstance(specs, str):
            specs = [specs]
        used_names.add(name)
        for index, spec in enumerate(specs):
            used_specs.add(spec)
            write_template(name, spec, index)

    def next_variant_index(name):
        """A slug that collides with an existing identity becomes an
        extra variant of it rather than a second identity."""
        index = 0
        while (OUTPUT_DIR / (f"{name}.png" if index == 0
                             else f"{name}.{index + 1}.png")).exists():
            index += 1
        return index

    for directory in AUTO_DIRS:
        # Sorted by NAME, not by Path. Path comparison is case-INSENSITIVE on
        # Windows and case-sensitive on Linux, so the same library folder
        # hands back a different order on each - and since a name collision
        # becomes "the next variant", that order decides which picture is
        # war_galley.png and which is war_galley.2.png. Rebuilding on the
        # other OS swapped ten committed templates in pairs. Harmless to the
        # matcher, which takes the max over variants, and pure noise in a
        # diff; a stable key means the set is a function of the sources
        # rather than of the machine.
        for path in sorted((LIBRARY_DIR / directory).glob("*.webp"),
                           key=lambda path: path.name):
            spec = f"{directory}/{path.name}"
            if spec in used_specs or spec in AUTO_EXCLUDE:
                continue
            name = auto_name(path.name)
            used_names.add(name)
            write_template(name, spec, next_variant_index(name))

    # The game's own tech icons - all of them, named by their own files
    # (NNN_snake_case.DDS). This is the authoritative art for whatever any
    # building researches, and it closes the missing-research-template
    # class for good: the Elite Skirmisher upgrade shield spent two games
    # wearing "villager_male" (a phantom TC) and then "?" for want of
    # exactly this sweep. Name collisions with curated or library
    # identities become variants of them, which is a feature - the
    # game-exact art rides beside the wiki art the way the curated
    # entries already pair them.
    import re as _re
    kinds = queue.identity_kinds()
    for path in sorted((game_textures() / "tech").iterdir(),
                       key=lambda path: path.name):
        if path.suffix.lower() != ".dds":
            continue
        spec = f"DDS:tech/{path.name}"
        if spec in used_specs or path.name in DDS_TECH_EXCLUDE:
            continue
        slug = _re.sub(r"^\d+_", "", path.stem).lower()
        name = tech_identity(slug, used_names, kinds)
        if name != slug:
            split.append(f"{slug} -> {name}")
        used_names.add(name)
        write_template(name, spec, next_variant_index(name))

    print(f"Wrote {written} templates to {OUTPUT_DIR}/")
    for spec in missing:
        print(f"  MISSING source: {spec}")
    if split:
        print(f"  {len(split)} tech icons kept out of a unit's identity:")
        for line in split:
            print(f"    {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
