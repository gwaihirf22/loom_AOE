"""
Loom — classify every queue template as a unit or a technology.

The queue reader knows an icon's NAME and not its KIND, and two places
already need the kind: reconcile_identity_and_count uses the game's rule
that techs never show a count digit, and the statistics window wants to
split what it sighted into a Technology tab and a Military one.

Until now the kind was a 21-name frozenset in queue.py and everything
else defaulted to "unit", silently. The drift guard that was supposed to
catch that could not: it computed `techs = SET & built` and
`units = built - SET` and then asserted their union was `built`, which is
true by construction. A test that cannot fail.

WHY THIS IS A DATA FILE AND NOT A DERIVATION. Three routes were tried:

* the icon library's folders group by PRODUCING BUILDING, not by kind -
  barracks/ holds Champion (unit) and Arson (tech) side by side;
* the filenames do not agree with themselves (ChampionUpgDE,
  Cavalier-research, ArrowSlitsDE, Champion_aoe2DE);
* git provenance looked promising - one commit swept the game's own
  tech texture folder and added 436 files with no units among them - but
  measured against the known techs it found only 9 of 20, and leaked
  battering_ram and scorpion in, because the ram and scorpion UPGRADE
  techs produce the same slug as the units.

That last one is the important finding: an identity slug does not always
determine a kind, so no derivation can be complete. The kind has to be
recorded, and a template whose kind nobody has recorded must say
"unknown" rather than quietly become a unit - which is the rule the
notification vocabulary audit taught: a hand-curated allowlist fails
SILENTLY, so make the gap visible and audit the category.

So this writes every template stem with its kind, seeded from the two
sources that ARE reliable, and PRESERVES whatever a previous run or a
pair of eyes already recorded. Verify a batch, rerun, and the file grows
without losing what was checked.

HOW THE 332 UNKNOWNS WERE SETTLED (2026-08-24). Every one of them was
FIRST created by that tech-folder sweep - none had a unit source at all -
so "technology" is what the game's own filing says, and that became the
default. The audit was then the other direction: read the whole list and
pull out the slugs that name a UNIT (the game files a unit-enabling or
unit-upgrading tech under the unit's own name, which is why
battle_elephant and bombard_cannon were in there) and the ones that name
a BUILDING. That is the category swept rather than the word noticed,
which is what CLAUDE.md asks for, and the residue is bounded: a unit
missed by the audit sits under Technology, on a tab, where it is visible
and one row of this file away from being right.

    python -m tools.classify_queue_icons
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import re

from loom import paths, queue
from tools import build_queue_templates

KINDS_PATH = paths.TEMPLATES_DIR / "queue" / "KINDS.tsv"

UNIT, TECHNOLOGY = "unit", "technology"
BUILDING, ANIMAL, UNKNOWN = "building", "animal", "unknown"


# Subjects the NOTIFICATION feed names that have no queue template - the
# feed announces buildings, and buildings never enter the production
# queue, so they have no icon to match. The feed's vocabulary is
# open-ended (any framed line's subject becomes a slug), so this cannot
# be complete and does not pretend to be.
#
# It is a hand list, which CLAUDE.md is right to distrust - but its
# failure mode is the opposite of the one that rule was written for. A
# subject missing here shows up as "unclassified" on the statistics tabs
# and in Post-game Data. It is visible, not silent, which is the whole
# difference.
FEED_ONLY = {
    "archery_range": BUILDING, "blacksmith": BUILDING, "castle": BUILDING,
    "dock": BUILDING, "farm": BUILDING, "house": BUILDING,
    "market": BUILDING, "mill": BUILDING, "monastery": BUILDING,
    "outpost": BUILDING, "palisade_wall": BUILDING, "stone_wall": BUILDING,
    "town_center": BUILDING, "university": BUILDING,
    "watch_tower": BUILDING, "wonder": BUILDING,
    "gate": BUILDING,
    "villager": UNIT, "elite_longbowman": UNIT,
    "trebuchet_packed": UNIT, "cavalry_archer": UNIT,
    "heavy_camel_rider": UNIT, "mangudai": UNIT,
    "elite_mangudai": UNIT, "elite_conquistador": UNIT,
    "missionary": UNIT,
    "sheep": ANIMAL, "goat": ANIMAL, "cow": ANIMAL,
    "pig": ANIMAL, "goose": ANIMAL,
}


def template_identities():
    """Every identity that has a template, variants folded together."""
    return {path.name.split(".")[0]
            for path in (paths.TEMPLATES_DIR / "queue").glob("*.png")}


def curated_units():
    """The units named in build_queue_templates.SOURCES.

    Read out of the source text rather than imported, because the answer is
    not in the data: SOURCES maps a name to its art and says nothing about
    kind. Only the "# units." comment marks where the units start, and
    everything under it is one - so the comment IS the record, and parsing
    it is reading the record rather than guessing.

    (It used to be read this way for a different reason - that importing the
    builder reached for the game's texture folder at import time. That is no
    longer true; the lookup is lazy now. Left as a note because a stale
    reason in a docstring is how a working parser gets "simplified" away.)
    """
    source = (paths.PROJECT_ROOT / "tools"
              / "build_queue_templates.py").read_text(encoding="utf-8")
    block = source[source.index("SOURCES = {"):]
    block = block[:block.index("\n}\n")]
    found, inside = set(), False
    for line in block.splitlines():
        stripped = line.strip()
        if re.match(r"#\s*units\b", stripped, re.I):
            inside = True
            continue
        name = re.match(r'"([a-z0-9_]+)"\s*:', stripped)
        if inside and name:
            found.add(name.group(1))
    return found


def read_kinds():
    """What has already been recorded: {identity: kind}."""
    if not KINDS_PATH.exists():
        return {}
    known = {}
    for line in KINDS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        identity, _, kind = line.partition("\t")
        if kind.strip() in (UNIT, TECHNOLOGY, BUILDING, ANIMAL, UNKNOWN):
            known[identity.strip()] = kind.strip()
    return known


def split_off_the_tech_folder(identities):
    """Identities build_queue_templates split out of a unit's name.

    NOT inferred from the spelling. build_queue_templates writes that suffix
    only when a picture came out of the game's own tech/ folder and collided
    with an identity that was already something else - so the suffix is a
    RECORD of where the art came from, and reading it back is reading the
    builder's answer rather than guessing at a word. The game's tech folder
    holds technologies and nothing else, which is what makes the answer
    sound.

    That distinction matters because this module's whole argument is that a
    slug does not determine a kind. It still does not. What determines this
    one is provenance.
    """
    return {identity for identity in identities
            if identity.endswith(build_queue_templates.UPGRADE_SUFFIX)
            and identity[:-len(build_queue_templates.UPGRADE_SUFFIX)]
            in identities}


def classify():
    """{identity: kind} for every template, best knowledge first."""
    known = read_kinds()
    units = curated_units()
    identities = template_identities()
    upgrades = split_off_the_tech_folder(identities)
    kinds = dict(FEED_ONLY)
    for identity in identities:
        # Anything a person already recorded wins: this file is meant to
        # be corrected by eye, and a rerun must never undo that.
        recorded = known.get(identity)
        if recorded and recorded != UNKNOWN:
            kinds[identity] = recorded
        elif identity in queue.TECH_IDENTITIES or identity in upgrades:
            kinds[identity] = TECHNOLOGY
        elif identity in units:
            kinds[identity] = UNIT
        else:
            kinds[identity] = UNKNOWN
    return kinds


def main():
    kinds = classify()
    lines = ["# identity\tkind - unit | technology | building | animal | unknown",
             "# Regenerate with: python -m tools.classify_queue_icons",
             "# Hand-verified rows are preserved; unknown is honest, not lazy."]
    for identity in sorted(kinds):
        lines.append(f"{identity}\t{kinds[identity]}")
    KINDS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = {}
    for kind in kinds.values():
        counts[kind] = counts.get(kind, 0) + 1
    print(f"{paths.for_display(KINDS_PATH)}: {len(kinds)} identities")
    for kind in (TECHNOLOGY, UNIT, BUILDING, ANIMAL, UNKNOWN):
        print(f"  {kind:<11} {counts.get(kind, 0)}")


if __name__ == "__main__":
    main()
