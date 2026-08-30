"""
Loom — how long the game takes to build and research things.

The statistics window needs this for one job: the build order's card
time is when the INSTRUCTION APPEARS, and what Loom observes is the
COMPLETION - the feed announces "--Mill Built--" and "--Loom Research
Complete--", never a start. So the moment worth expecting is

    card + duration + a little for a human

and without the duration every slow thing reads late for being slow.

A LITERAL, not a data file, so it is greppable and each line can carry
its own note - the same reasoning that keeps glyphs.KNOWN_WORDS in the
source. A subject that is not here has NO duration rather than a guessed
one: unknown degrades to the old behaviour, which was merely coarse,
where a wrong number would be confidently wrong.

Two honest caveats, both of which make the window GENEROUS - the safe
direction for a tolerance:

* BUILDING TIME DIVIDES AMONG BUILDERS. These are one-villager times, so
  a house that two villagers put up takes about half of what is written
  here. Loom cannot see how many were on it, so it allows the slowest
  case and forgives a fast one.
* Research time does NOT divide, and those numbers are exact.

Sourcing: Wheelbarrow's 75s is confirmed against the community wikis;
the rest are from knowledge of the game, and both wiki hosts refuse
automated fetching (403 and 402), so they are written here to be
CORRECTED BY EYE rather than trusted blindly. An error of a few seconds
only widens or narrows a tolerance by a few seconds; it cannot make a
prompt player read late or a slow one read on time.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# Buildings, in seconds for ONE villager (see the caveat above).
# VERIFIED AGAINST THE GAME ITSELF, 2026-08-25. These were written from
# knowledge because both wikis refuse automated fetching (403 and 402), and
# only Wheelbarrow's 75s had a source. Attaching recorded games to the stats
# corpus made a better check possible than either wiki: a RESEARCH does not
# divide among helpers, so the time from the player's order to the game's
# own announcement is the research time plus at most one poll.
#
#     feudal_age    measured 130, 130, 136, 152, 162   table 130   exact
#     castle_age    measured 160, 160, 161              table 160   exact
#     loom          measured  26,  28,  33,  45,  91    table  25   +1
#
# The minimum is the true value and the spread above it is the Town Centre
# being busy. Buildings cannot be checked this way and are not claimed to
# be: their time divides among builders and the measurement includes
# walking, so measured runs both under and over the listed number - castle
# came back at 91 against 200 with several villagers on it.
#
# Re-check with: python -m tools.reader_sweep, or the statistics window's
# Reader accuracy tab.

BUILDINGS = {
    "house": 25, "farm": 15, "outpost": 15,
    "mill": 35, "lumber_camp": 35, "mining_camp": 35, "dock": 35,
    "blacksmith": 40, "monastery": 40, "siege_workshop": 40,
    "barracks": 50, "archery_range": 50, "stable": 50,
    "market": 60, "university": 60,
    "watch_tower": 80,
    "town_center": 150, "castle": 200,
}

# Technologies. These are exact and do not divide among anybody.
TECHNOLOGIES = {
    # Town Centre - the ones Loom can see queued, and so the ones it is
    # most confident about.
    "loom": 25, "town_watch": 25, "town_patrol": 40,
    "wheelbarrow": 75, "hand_cart": 55,
    "feudal_age": 130, "castle_age": 160, "imperial_age": 190,
    # Lumber camp, mill, mining camp.
    "double_bit_axe": 50, "bow_saw": 100, "two_man_saw": 100,
    "horse_collar": 20, "heavy_plow": 70, "crop_rotation": 70,
    "gold_mining": 30, "stone_mining": 30,
    "gold_shaft_mining": 75, "stone_shaft_mining": 75,
    # Blacksmith.
    "fletching": 30, "bodkin_arrow": 35, "bracer": 40,
    "forging": 50, "iron_casting": 75, "blast_furnace": 100,
    "scale_mail_armor": 40, "chain_mail_armor": 55, "plate_mail_armor": 70,
    "scale_barding_armor": 45, "chain_barding_armor": 60,
    "plate_barding_armor": 75,
    "padded_archer_armor": 40, "leather_archer_armor": 55,
    "ring_archer_armor": 70,
    # University, market, castle, stable, barracks, monastery.
    "ballistics": 60, "murder_holes": 60, "masonry": 50, "architecture": 70,
    "treadmill_crane": 50, "heated_shot": 30, "arrow_slits": 25,
    "banking": 50, "coinage": 70, "guilds": 50, "caravan": 40,
    "husbandry": 40, "bloodlines": 50, "squires": 40, "conscription": 60,
    "sanctity": 60, "redemption": 50, "atonement": 40, "fervor": 50,
    "herbal_medicine": 35, "heresy": 60, "illumination": 65,
    "block_printing": 55, "faith": 60,
}


# ---- what the player's own games actually took ---------------------------
#
# The tables above are the GAME's numbers. This is the player's: swept out
# of their own recorded games by tools/measure_durations.py and kept with
# their statistics, never in the repository, because it describes how one
# person plays rather than how the game works.
#
# The split between what it may override and what it may not is the same
# asymmetry the whole module rests on.
#
# A BUILDING's time divides among however many villagers help, so the
# one-villager number above is a ceiling nobody plays at. If somebody puts
# two villagers on every Castle, their Castles really do take 123 seconds
# rather than 200, and their own median is a better prediction of their
# next one than the book is. Measured: castle 75/123/175 against a listed
# 200, mill 22/40/63 against 35.
#
# RESEARCH does not divide, so the book value is the truth and a measured
# figure can only ever be that plus queue time - a Blacksmith already busy
# makes the next technology wait, and the wait is indistinguishable from
# the research in this measurement. Measured across 61 games, the minimum
# matches the table exactly for horse_collar, gold_mining, bodkin_arrow,
# fletching, ballistics, husbandry, iron_casting, bracer, wheelbarrow and
# every armour line, which is what confirms the tables rather than
# replacing them. So technologies are NEVER overridden here.
MEASURED_PATH_NAME = "durations.json"

# Below this many samples a median is one game with an opinion.
ENOUGH_SAMPLES = 4

_measured = None


def measured(reload=False):
    """{subject: seconds} from the player's own games, or empty."""
    global _measured
    if _measured is not None and not reload:
        return _measured
    import json
    from . import paths
    path = paths.DATA_DIR / MEASURED_PATH_NAME
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        found = {}
    _measured = {str(k): float(v) for k, v in (found.get("buildings") or {}).items()
                 if isinstance(v, (int, float)) and v > 0}
    return _measured


def build_or_research_time(subject, personal=True):
    """Seconds this takes, or 0 when nobody has said.

    Zero rather than a guess: an unknown subject falls back to judging on
    the card's own pacing, which is coarse but never confidently wrong.

    `personal=False` asks for the GAME's number regardless of what this
    player's own games say - which is what a tool comparing the two
    wants, and what anything reasoning about the game itself should use.
    """
    if personal and subject not in TECHNOLOGIES:
        mine = measured().get(subject)
        if mine:
            return mine
    return BUILDINGS.get(subject) or TECHNOLOGIES.get(subject) or 0
