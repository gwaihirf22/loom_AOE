"""Generate the recorded game's id -> subject table, and audit the vocabulary.

The recorded game names things by NUMBER: `building_id 12`, `technology_id
249`. Loom names them by WORD, because it reads them off the screen. Nothing
in either half connects the two, so `replay_truth` carried a table of sixteen
ids I had corroborated by hand - and `run_report` silently skipped every id
that was not in it, which was most of them.

Two sources, and which is which matters:

* **The numbers come from `aoc-reference-data`** (SiegeEngineers, the dataset
  `aocref` packages and `mgz.reference` looks for). Nothing else here has
  them. Corroborated before it was trusted: all twenty ids I had derived
  independently from pixel evidence agree with it exactly, names included.

* **Which of those ids are KEPT is decided by Loom**, not by the dataset. For
  every name, this asks `glyphs.parse_event` whether Loom would produce an
  event if the game printed that line. If it would not, the id is left out
  and REPORTED. A ruler must only carry subjects the thing it measures can
  actually say; an id whose name Loom can never emit would score zero forever
  and look like a reader fault.

That filter had to be `parse_event` rather than the icon library, and the
difference is not cosmetic. `lines.subjects()` is derived from icon
FILENAMES, so it holds `infantry pikeman` and `pikeman up` - from
`Aoe2-infantry-2-pikeman.webp` and `PikemanUpDE.webp` - and no plain
`pikeman`. Loom emits `researched:pikeman` all the same, because the event
parser has its own vocabulary. Filtering on the icons dropped a subject Loom
demonstrably reads.

The audit is the useful by-product. A name the dataset has and Loom cannot
parse is a hole in `KNOWN_WORDS`, and that list fails SILENTLY - it refuses
perfectly-read lines and nothing anywhere reports it. This reports it.

    python -m tools.build_id_table            # what it would write, and the gaps
    python -m tools.build_id_table --write    # write loom/replay_ids.py
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import glyphs, paths, queue  # noqa: E402

DATASET_URL = ("https://raw.githubusercontent.com/SiegeEngineers/"
               "aoc-reference-data/master/data/datasets/100.json")

OUT_PATH = paths.PROJECT_ROOT / "loom" / "replay_ids.py"

# What the game prints when each kind of thing happens. Asking Loom to parse
# a whole line rather than a bare word is deliberate: the word alone is not
# what the reader ever sees, and a subject that only parses out of context
# is not one this table should carry.
BUILT = "--{} Built--"
RESEARCHED = "--{} Research Complete--"

# The SECOND vocabulary, and it is not the same one.
#
# BUILDINGS and TECHNOLOGIES above are filtered by what the NOTIFICATION
# reader can say, because that is what they are compared against. The queue
# reader has its own vocabulary - one icon template per identity, named by
# filename - and the two only partly overlap. A table filtered for one and
# used for the other measures the wrong thing, which is the mistake
# `lines.subjects()` invited when it was tried as a filter and dropped
# `pikeman`, a subject Loom demonstrably reads.
#
# So these tables answer a different question: given a numeric id in a
# recorded game, what would the QUEUE reader call that thing if it were in
# a slot? An id with no icon template is left out, because the reader can
# never name it and an absence there is a fact about the template set
# rather than about a reading.


def queue_identities():
    """Every identity the queue reader can name, plus its family.

    FAMILY exists because the game draws one villager two ways. The record
    only ever says "Villager", so a comparison against the reader has to
    happen at the family level or every villager in every game reads as an
    identity nobody ordered.
    """
    found = {}
    for name in queue.load_icon_templates():
        found[name] = queue.FAMILY.get(name, name)
    return found


def normalised(name):
    """Every spelling of a dataset name worth trying against a template.

    Two forms, because the template set uses both: `cavalry_archer` and
    `cavalryarcher` are the same unit, and the difference is a filename
    convention that drifted rather than anything meaningful. Generated
    rather than listed, so a third naming accident costs nothing.
    """
    lowered = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (lowered, lowered.replace("_", ""))


def upgraded_forms(objects, technologies):
    """Unit identities that are an UPGRADED rung of a line, not a base unit.

    The record says what was ORDERED; the queue draws what will EMERGE, and
    those differ the moment an upgrade lands. Measured on a Turkish game:
    the player ordered Scout Cavalry 42 times and the reader never once said
    scout_cavalry - it said light_cavalry from 32:51 to 42:00 and hussar
    from 42:04 to 47:09, and Imperial Age completed at about 41:40. That is
    not a misread, it is the reader tracking a free civilisation upgrade to
    the second, and it was being scored as 455 faults.

    Free is the word that makes this unfixable by looking for the research:
    Turks get Light Cavalry and Hussar with no command at all, so the record
    is silent and its silence means nothing here. The dataset carries no
    civilisation bonuses to consult - `civilizations` is id and name only.

    What CAN be derived is which units are upgraded forms, because the game
    names an upgrade technology exactly like the unit it produces. `Hussar`
    is both; `Knight` is not, its upgrade being `Cavalier`. So a unit whose
    name is also a technology name may have arrived by research or by
    civilisation bonus, and the record cannot rule it out either way - which
    makes it UNVERIFIABLE rather than wrong. Structural, from the dataset,
    rather than a hand-written list of civ bonuses that would fail silently
    the first time one was missed.
    """
    tech_spellings = set()
    for name in technologies.values():
        if name and name.strip():
            tech_spellings.update(normalised(name.strip()))
    upgraded = set()
    for ident, name in objects.items():
        if not name or not name.strip():
            continue
        for spelling in normalised(name.strip()):
            if spelling in tech_spellings:
                upgraded.add(spelling)
    return upgraded


def queue_table_for(named):
    """(id -> queue identity family) for what the queue reader can name."""
    identities = queue_identities()
    families = {}
    for identity, family in identities.items():
        families.setdefault(identity, family)
    keep, refused = {}, {}
    for ident, name in named.items():
        if not name or not name.strip():
            continue
        hit = None
        for spelling in normalised(name.strip()):
            if spelling in families:
                hit = families[spelling]
                break
            # A family name the record uses but no template is called: the
            # record says "Villager", the templates say villager_male and
            # villager_female, and the family is the only place they meet.
            shared = [f for f in families.values() if f == spelling]
            if shared:
                hit = spelling
                break
        if hit:
            keep[int(ident)] = hit
        else:
            refused.setdefault(name.strip(), []).append(int(ident))
    return keep, refused


def fetch_dataset(url=DATASET_URL):
    with urllib.request.urlopen(url, timeout=60) as handle:
        return json.loads(handle.read().decode("utf-8"))


# What parse_event returns for a line, minus the action. Two shapes come
# back and only one has a colon: `--Barracks Built--` gives `built:barracks`,
# while `--Town Center Built--` gives `town_center_built` - the PHRASE
# watcher's naming, because town centres are read by notifications.py and
# reader.py discards the glyph reader's version of them.
#
# Rejecting the second shape dropped the most consequential subject in the
# project from the table, and it was the cross-check against twenty
# hand-derived ids that caught it, not the generator.
ACTIONS = ("_built", "_created", "_research_complete", "_destroyed", "_found")


def subject_for(name, template):
    """The subject Loom would emit for this line, or None if it would not."""
    event = glyphs.parse_event(template.format(name))
    if not event:
        return None
    if ":" in event:
        return event.split(":", 1)[1]
    for action in ACTIONS:
        if event.endswith(action):
            return event[:-len(action)]
    return event


def table_for(named, template):
    """(id -> subject) for what Loom can say, and the names it cannot."""
    keep, refused = {}, {}
    for ident, name in named.items():
        if not name or not name.strip():
            continue
        subject = subject_for(name.strip(), template)
        if subject:
            keep[int(ident)] = subject
        else:
            refused.setdefault(name.strip(), []).append(int(ident))
    return keep, refused


def render(objects, technologies, queue_objects, queue_techs, upgraded,
           version):
    lines = [
        '"""Recorded game ids, and what Loom calls the thing each one is.',
        "",
        "GENERATED by tools/build_id_table.py - do not edit by hand.",
        "",
        f"Numbers from aoc-reference-data dataset 100 (game data {version}).",
        "Which ids appear was decided by asking glyphs.parse_event whether",
        "Loom would emit an event for that name, so every subject here is one",
        "the reader can actually produce. Names Loom cannot parse are left",
        "out and listed by the generator, because an id whose subject Loom",
        "can never say would score zero forever and read as a reader fault.",
        '"""',
        "",
        "BUILDINGS = {",
    ]
    for ident in sorted(objects):
        lines.append(f"    {ident}: {objects[ident]!r},")
    lines += ["}", "", "TECHNOLOGIES = {"]
    for ident in sorted(technologies):
        lines.append(f"    {ident}: {technologies[ident]!r},")
    lines += [
        "}",
        "",
        "# The same ids against the QUEUE reader's vocabulary instead of the",
        "# notification reader's. The two overlap but are not the same set, and",
        "# a table filtered for one and used for the other measures the wrong",
        "# thing. Values are icon FAMILIES: the record only ever says",
        '# "Villager" while the templates say villager_male and',
        "# villager_female, so the family is the only level the two meet on.",
        "QUEUE_UNITS = {",
    ]
    for ident in sorted(queue_objects):
        lines.append(f"    {ident}: {queue_objects[ident]!r},")
    lines += ["}", "", "QUEUE_TECHS = {"]
    for ident in sorted(queue_techs):
        lines.append(f"    {ident}: {queue_techs[ident]!r},")
    lines += [
        "}",
        "",
        "# Unit identities that are an UPGRADED rung rather than a base unit,",
        "# spotted by the game naming an upgrade technology exactly like the",
        "# unit it produces - Hussar is both, Knight is not. The record says",
        "# what was ORDERED and the queue draws what will EMERGE, so one of",
        "# these on screen may have arrived by research or free from a",
        "# civilisation bonus, and the record can rule out neither. Turks get",
        "# Light Cavalry and Hussar for nothing, with no command to find.",
        "QUEUE_UPGRADED = frozenset({",
    ]
    for name in sorted(upgraded):
        lines.append(f"    {name!r},")
    lines += ["})", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true",
                        help=f"write {OUT_PATH.name} rather than only report")
    arguments = parser.parse_args()

    data = fetch_dataset()
    version = (data.get("dataset") or {}).get("version", "?")
    objects, no_object = table_for(data["objects"], BUILT)
    technologies, no_tech = table_for(data["technologies"], RESEARCHED)
    queue_objects, _ = queue_table_for(data["objects"])
    queue_techs, _ = queue_table_for(data["technologies"])
    all_upgraded = upgraded_forms(data["objects"], data["technologies"])
    upgraded = {name for name in all_upgraded
                if name in set(queue_objects.values())}

    print(f"dataset 100, game data {version}")
    print(f"  objects      {len(data['objects']):>5} -> {len(objects):>4} "
          f"Loom can name")
    print(f"  technologies {len(data['technologies']):>5} -> "
          f"{len(technologies):>4} Loom can name")
    print(f"  and against the QUEUE reader's icons instead:")
    print(f"    objects      {len(data['objects']):>5} -> "
          f"{len(queue_objects):>4} the queue reader can name")
    print(f"    technologies {len(data['technologies']):>5} -> "
          f"{len(queue_techs):>4} the queue reader can name")
    print(f"    of those units, {len(upgraded)} are an UPGRADED rung whose "
          f"arrival\n      the record cannot confirm or deny - research or a "
          f"free civ bonus")

    # The audit. A name here is a line the game can print and Loom would
    # refuse, which is a hole in KNOWN_WORDS - and that list fails silently.
    refused = sorted(set(no_tech) & set(no_object))
    print(f"\n  {len(refused)} names Loom cannot parse in EITHER form. Each is"
          f" a line the game\n  can print and the reader would refuse:")
    for name in refused[:40]:
        print(f"      {name}")
    if len(refused) > 40:
        print(f"      ... and {len(refused) - 40} more")

    if arguments.write:
        OUT_PATH.write_text(
            render(objects, technologies, queue_objects, queue_techs,
                   upgraded, version),
            encoding="utf-8")
        print(f"\nwrote {OUT_PATH}")
    else:
        print("\n(nothing written - pass --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
