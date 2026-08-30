"""
Loom — the game's own civilisation rules, read out of the install.

Writes reference/ from the files Age of Empires II ships. Not part of Loom:
nothing under loom/ imports any of this, and the program behaves identically
without it. It exists so a person or an agent mid-debug can settle "is that a
reader bug, or is that the game?" in two greps.

    python -m tools.civ_reference            # what would change, writing nothing
    python -m tools.civ_reference --write    # update reference/
    python -m tools.civ_reference --check    # as above, exit 1 if anything changed

WHY THE INSTALL AND NOT A COMMUNITY DATASET. The game ships CivTechTrees/ as
one already-parsed JSON per civilisation, so there is no .dat to decode and no
dependency to add. It is current the instant Steam patches, where
SiegeEngineers/aoe2techtree trails by weeks; it carries every civ including
the Chronicles ones, where that dataset carried 53 of 59 when this was
written; and it needs no network, so this works on a train.

THE FILE MTIME IS THE PATCH DATE. Steam's patcher rewrites these files, so
there is no version field to look for and none is needed - the timestamp on
disk is the identity, and it goes into the manifest.

WHAT COST TIME TO FIND OUT, so the next reader does not pay it again:

  * The files are utf-8 WITH A BOM. Plain "utf-8" raises on them.
  * <b> is a TOGGLE, not a tag. The game writes "<b>Unique Unit:<b>", never
    "</b>". A parser expecting a closing tag keeps the markup silently.
  * Newlines in the strings file are the two characters \\ and n, not real
    newlines, so they need unescaping.
  * civ_id is not the civ's name. The tech tree file is INCAS.json and the
    name string is "Inca"; MAYANS is "Maya". Matching by name fails on
    both, which is why civilizations.json below is the mapping and no
    hand-written table is.
  * There is no help-string id anywhere in the data. It is derived - see
    help_string_id - and that derivation is measured on every run rather
    than trusted, because it is a pattern in the numbering rather than a
    documented contract.

WHAT IS NOT HERE. Measurements of the author's own games are not game rules;
those come from tools/measure_durations.py. And a recorded game is a
different thing entirely - see the design rule in CLAUDE.md. These files are
public reference data, the same information the in-game tech tree shows any
player mid-match.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from loom.paths import PROJECT_ROOT  # noqa: E402

OUT_DIR = PROJECT_ROOT / "reference"

# Where the game keeps what this reads, relative to the install root.
CIV_TREES = pathlib.PurePosixPath("resources/_common/dat/CivTechTrees")
CIV_LIST = pathlib.PurePosixPath("resources/_common/dat/civilizations.json")
STRINGS = pathlib.PurePosixPath(
    "resources/en/strings/key-value/key-value-strings-utf8.txt")

# The override, named like LOOM_DATA_DIR and LOOM_CAPTURE_BACKEND so the
# escape hatches all read alike.
INSTALL_ENV = "LOOM_AOE2_DIR"

# The three things the game says about a node in a civ's tech tree. The
# first two mean the civ has it; the third is the whole point of this file -
# a queue identity sighted for a civ that cannot have it is a reader fault
# with no other explanation.
AVAILABLE = ("ResearchedCompleted", "ResearchRequired")
UNAVAILABLE = "NotAvailable"

# Every civ's bonus text sits at this offset from its name string. Derived,
# not documented, so check_derivation() re-measures it on every run.
NAME_BASE = 10271
HELP_BASE = 120150

# A civ known to lack things, used to prove the status vocabulary above still
# means what it meant. If Britons ever "gain" Paladin this is wrong about the
# game, not about the civ - and either way it must stop rather than write a
# reference nobody can trust.
CANARY_CIV = "BRITONS"
CANARY_LACKS = ("Paladin", "Bloodlines", "Thumb Ring")


# ---- finding the game -----------------------------------------------------

def steam_libraries(home=None, platform=None):
    """Every Steam library root this machine might have, existing or not.

    Steam keeps its extra drives in libraryfolders.vdf rather than anywhere
    predictable, so a machine with the game on E: cannot be found by guessing
    Program Files. Parsed with a regex rather than a VDF library: one quoted
    "path" per library is the whole of what is wanted here, and a dependency
    for that would be a poor trade.

    platform is injectable for the same reason paths.data_home's is: a test
    on one OS has to be able to ask what the other OS would do. Without it
    this function reaches past its arguments to the real machine, and a test
    passing home=tmp_path still finds the author's actual E: drive - which it
    did, before this argument existed.
    """
    home = pathlib.Path(home or pathlib.Path.home())
    platform = platform or sys.platform
    roots = []
    if platform == "win32":
        roots += [pathlib.Path(r"C:\Program Files (x86)\Steam"),
                  pathlib.Path(r"C:\Program Files\Steam")]
    else:
        roots += [home / ".steam" / "steam",
                  home / ".local" / "share" / "Steam"]

    found = list(roots)
    for root in roots:
        manifest = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for path in re.findall(r'"path"\s+"([^"]+)"', text):
            library = pathlib.Path(path.replace("\\\\", "\\"))
            if library not in found:
                found.append(library)
    return found


def install_candidates(home=None, env=None, platform=None):
    """Where the game might be installed, in the order worth trying.

    Returned whether or not they exist, so a caller that finds nothing can
    say WHERE it looked - "no install found" and "I never checked your other
    drive" are different messages, and only one of them is actionable. Same
    reasoning as loom.replay.search_paths.

    NOT the same tree as the recorded games: those live under the player's
    home directory, this under steamapps/common. Easy to confuse and the
    error when you do is a puzzling empty directory.
    """
    env = os.environ if env is None else env
    override = env.get(INSTALL_ENV)
    if override:
        return [pathlib.Path(override)]
    return [library / "steamapps" / "common" / "AoE2DE"
            for library in steam_libraries(home, platform)]


def find_install(home=None, env=None, platform=None):
    """The first candidate that actually holds the game. None if none does."""
    for candidate in install_candidates(home, env, platform):
        if (candidate / CIV_TREES).is_dir():
            return candidate
    return None


# ---- reading the game's files ---------------------------------------------

def read_game_json(path):
    """A game JSON file. utf-8-sig because these carry a BOM."""
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8-sig"))


STRING_LINE = re.compile(r'^(\d+) "(.*)"$', re.M)


def read_strings(text):
    """The language file as {id: text}, unescaped.

    The game writes newlines as the two characters \\ and n, and uses <b> as
    a TOGGLE rather than an opening tag - "<b>Unique Unit:<b>". Both are
    undone here so every caller gets plain text rather than each inventing
    its own half-right unescaping.
    """
    out = {}
    # A caller that decoded the file itself may not have used utf-8-sig, and
    # a leading BOM stops the very first line matching - silently, since
    # every other line still does.
    text = text.lstrip("﻿")
    for number, body in STRING_LINE.findall(text):
        body = body.replace("\\n", "\n").replace("<b>", "")
        out[int(number)] = body.strip()
    return out


def help_string_id(name_string_id):
    """Where a civ's bonus text sits, given where its name sits.

    A pattern in the numbering, not a documented contract - there is no help
    id in civilizations.json or anywhere else. check_derivation proves it
    still holds before anything is written.
    """
    return HELP_BASE + (name_string_id - NAME_BASE)


def check_derivation(civs, strings):
    """Prove the derived bonus text belongs to the civ it is claimed for.

    Every civ description opens by naming its own archetype - "Foot Archer
    civilization". A derivation that had slipped by one would still return
    text, and the reference would be quietly wrong about every civ. Cheap to
    check, and the failure is silent without it.
    """
    wrong = []
    for civ in civs:
        body = strings.get(help_string_id(civ["name_string_id"]), "")
        if "civilization" not in body[:60].lower():
            wrong.append(civ["tech_tree_name"])
    return wrong


# ---- what a civ IS --------------------------------------------------------

def playable(civ_list):
    """The civs worth writing down, in the game's own order.

    GAIA is the map's own "civilisation" - trees and deer - and has a tech
    tree file like everything else. It is not a civ anyone plays.
    """
    return [civ for civ in civ_list
            if civ.get("tech_tree_name")
            and civ["tech_tree_name"] != "GAIA"
            and civ.get("name_string_id") is not None]


def split_nodes(nodes):
    """(has, cannot_have) names out of one tech tree list, sorted.

    A node is either something the civ starts with or can research, or
    something greyed out. Anything the game invents a fourth status for
    lands in neither and is reported, rather than being silently counted as
    available - which is the direction that produces a reference claiming a
    civ has something it does not.
    """
    has, cannot, unknown = set(), set(), set()
    for node in nodes:
        status, name = node.get("Node Status"), node.get("Name")
        if not name:
            continue
        if status in AVAILABLE:
            has.add(name)
        elif status == UNAVAILABLE:
            cannot.add(name)
        else:
            unknown.add(f"{name} ({status})")
    return sorted(has), sorted(cannot), sorted(unknown)


BONUS = re.compile(r"^[•\-\*]\s*(.+)$")


def parse_help(body):
    """A civ's description, split into the parts worth grepping separately.

    Returns {archetype, bonuses, unique_units, unique_techs, team_bonus}.
    The text is the game's own and its shape is a convention rather than a
    format, so every part is optional and a civ that does not follow it
    still yields whatever it does have.
    """
    lines = [line.strip() for line in body.split("\n")]
    out = {"archetype": lines[0] if lines else "", "bonuses": [],
           "unique_units": [], "unique_techs": [], "team_bonus": ""}
    section = "bonuses"
    for line in lines[1:]:
        if not line:
            continue
        lowered = line.lower().rstrip(":")
        if lowered.startswith("unique unit"):
            section = "unique_units"
            continue
        if lowered.startswith("unique tech"):
            section = "unique_techs"
            continue
        if lowered.startswith("team bonus"):
            section = "team_bonus"
            continue
        bullet = BONUS.match(line)
        value = bullet.group(1).strip() if bullet else line
        if section == "team_bonus":
            out["team_bonus"] = value
        elif section == "bonuses":
            if bullet:
                out["bonuses"].append(value)
        else:
            out[section].extend(part.strip() for part in value.split(",")
                                if part.strip())
    return out


def civ_record(civ, strings, trees_dir):
    """Everything the reference knows about one civilisation."""
    tree_name = civ["tech_tree_name"]
    tree = read_game_json(pathlib.Path(trees_dir) / f"{tree_name}.json")
    buildings = split_nodes(tree.get("civ_techs_buildings", []))
    units = split_nodes(tree.get("civ_techs_units", []))
    described = parse_help(strings.get(help_string_id(civ["name_string_id"]),
                                       ""))
    return {
        "id": tree_name,
        "name": strings.get(civ["name_string_id"], tree_name.title()),
        "era": civ.get("era", "base"),
        **described,
        "buildings": {"has": buildings[0], "cannot": buildings[1]},
        "units": {"has": units[0], "cannot": units[1]},
        "unknown_status": buildings[2] + units[2],
    }


# ---- writing it down ------------------------------------------------------

def render_civ(record, patch):
    """One civilisation as Markdown, written to be grepped.

    Long lines on purpose: `grep -l "Lumber Camp"` has to find the civ, and
    a name wrapped onto its own row would hide from it. reference/README.md
    states the rule - a table nobody can grep is worse than a list somebody
    can.
    """
    lines = [f"# {record['name']}", "",
             f"`{record['id']}` · {record['era']} · {record['archetype']}"
             f" · patch {patch}", ""]
    if record["bonuses"]:
        lines += ["## Bonuses", ""]
        lines += [f"- {bonus}" for bonus in record["bonuses"]] + [""]
    if record["team_bonus"]:
        lines += ["## Team bonus", "", record["team_bonus"], ""]
    if record["unique_units"] or record["unique_techs"]:
        lines += ["## Unique", ""]
        if record["unique_units"]:
            lines.append(f"Units: {', '.join(record['unique_units'])}")
        if record["unique_techs"]:
            lines.append(f"Techs: {', '.join(record['unique_techs'])}")
        lines.append("")

    # The section this file exists for. Cannot-have comes FIRST because it is
    # the one that settles an argument: an identity sighted for a civ listed
    # here is a misread, provably.
    lines += ["## Cannot have", ""]
    for label, key in (("buildings", "buildings"), ("units and techs", "units")):
        missing = record[key]["cannot"]
        lines.append(f"{label}: {', '.join(missing) if missing else 'nothing'}")
    lines += ["", "## Has", ""]
    for label, key in (("buildings", "buildings"), ("units and techs", "units")):
        lines.append(f"{label}: {', '.join(record[key]['has'])}")
    if record["unknown_status"]:
        lines += ["", "## Unrecognised node status", "",
                  "The game answered something this tool does not know. Do"
                  " not read the lists above as complete until this is"
                  " explained:", ""]
        lines += [f"- {item}" for item in record["unknown_status"]]
    return "\n".join(lines).rstrip() + "\n"


def render_index(records, patch):
    """One file naming every civ and what it cannot have, for a fast grep."""
    lines = ["# Every civilisation, and what it cannot have", "",
             f"Generated by tools/civ_reference.py from the game install."
             f" Patch {patch}.", "",
             "One line per civilisation. For the full detail including"
             " bonuses see `civs/<ID>.md`.", ""]
    for record in records:
        missing = record["buildings"]["cannot"] + record["units"]["cannot"]
        lines.append(f"## {record['name']} (`{record['id']}`)")
        lines.append("")
        lines.append(f"{record['archetype']}")
        lines.append("")
        lines.append(f"cannot have: {', '.join(missing) if missing else 'nothing'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def digest(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def patch_date(install):
    """When Steam last rewrote any of this, as YYYY-MM-DD.

    The game ships no version field, so the timestamp is the identity. The
    NEWEST of the files actually read, not any one of them: measured, this
    machine had civilizations.json at 2026-02-18 and the tech trees at
    2026-06-23, because a patch touches only what it changed. Taking one file
    would have stamped the reference four months earlier than the data in it.
    """
    install = pathlib.Path(install)
    sources = [install / CIV_LIST, install / STRINGS]
    sources += sorted((install / CIV_TREES).glob("*.json"))
    newest = max(path.stat().st_mtime for path in sources)
    return datetime.datetime.fromtimestamp(newest).strftime("%Y-%m-%d")


def build(install):
    """Read the install and return (records, patch, manifest)."""
    install = pathlib.Path(install)
    strings = read_strings(
        (install / STRINGS).read_text(encoding="utf-8-sig"))
    civ_list = read_game_json(install / CIV_LIST)["civilization_list"]
    civs = playable(civ_list)

    wrong = check_derivation(civs, strings)
    if wrong:
        raise SystemExit(
            "the bonus-text derivation no longer holds for: "
            + ", ".join(wrong)
            + "\nThe numbering pattern this tool relies on has changed."
              " Nothing written - a reference with the wrong civ's bonuses"
              " on it is worse than no reference.")

    trees = install / CIV_TREES
    records = [civ_record(civ, strings, trees) for civ in civs]

    canary = next((r for r in records if r["id"] == CANARY_CIV), None)
    if canary is not None:
        missing = set(canary["units"]["cannot"])
        absent = [want for want in CANARY_LACKS if want not in missing]
        if absent:
            raise SystemExit(
                f"{CANARY_CIV} now appears to have {', '.join(absent)}."
                "\nEither the game changed profoundly or the node-status"
                " vocabulary did. Nothing written until someone looks.")

    manifest = {
        "patch": patch_date(install),
        "civilisations": len(records),
        "source": {
            CIV_LIST.name: digest(install / CIV_LIST),
            STRINGS.name: digest(install / STRINGS),
            **{f"CivTechTrees/{path.name}": digest(path)
               for path in sorted(trees.glob("*.json"))},
        },
    }
    return records, manifest["patch"], manifest


def write(records, patch, manifest, install, out_dir=OUT_DIR):
    """Write reference/, replacing what is generated and nothing else."""
    out_dir = pathlib.Path(out_dir)
    civs_dir = out_dir / "civs"
    raw_dir = out_dir / "raw"
    for stale in (civs_dir, raw_dir):
        if stale.is_dir():
            shutil.rmtree(stale)
    civs_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "CivTechTrees").mkdir(parents=True, exist_ok=True)

    for record in records:
        (civs_dir / f"{record['id']}.md").write_text(
            render_civ(record, patch), encoding="utf-8")
    (out_dir / "civilisations.md").write_text(
        render_index(records, patch), encoding="utf-8")

    install = pathlib.Path(install)
    for source in sorted((install / CIV_TREES).glob("*.json")):
        shutil.copyfile(source, raw_dir / "CivTechTrees" / source.name)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")


# ---- what changed ---------------------------------------------------------

def differences(now, before):
    """English lines describing how two manifests differ. [] means identical.

    Compares checksums rather than the distilled Markdown, so a change in
    something the distillation dropped is still caught. It reports as a file
    rather than as a fact, which is honest about what is known.
    """
    lines = []
    if before.get("patch") != now.get("patch"):
        lines.append(f"patch {before.get('patch', '?')} -> {now.get('patch')}")
    was, is_now = before.get("source", {}), now.get("source", {})
    for name in sorted(set(was) | set(is_now)):
        if name not in was:
            lines.append(f"new file      {name}")
        elif name not in is_now:
            lines.append(f"gone          {name}")
        elif was[name] != is_now[name]:
            lines.append(f"changed       {name}")
    return lines


def civ_differences(records, out_dir=OUT_DIR):
    """Per-civ English, by re-reading what was written last time.

    The Markdown IS the record, so this diffs the lists a person would read
    rather than a parallel structure that could disagree with them.
    """
    lines = []
    civs_dir = pathlib.Path(out_dir) / "civs"
    for record in records:
        path = civs_dir / f"{record['id']}.md"
        fresh = render_civ(record, "?")
        if not path.exists():
            lines.append(f"{record['id']:14} is new")
            continue
        old = path.read_text(encoding="utf-8")
        # The patch stamp differs on every patch and says nothing about the
        # civ, so it is normalised away before comparing.
        strip = re.compile(r" · patch \S+")
        if strip.sub("", old) != strip.sub("", fresh):
            lines.append(f"{record['id']:14} changed")
    return lines


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--write", action="store_true",
                        help="update reference/ rather than only report")
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the install disagrees with"
                             " the committed reference")
    arguments = parser.parse_args()

    install = find_install()
    if install is None:
        print("no Age of Empires II install found. Looked in:")
        for candidate in install_candidates():
            print(f"    {candidate}")
        print(f"\nSet {INSTALL_ENV} if it lives somewhere else.")
        return 1

    print(f"reading {install}")
    records, patch, manifest = build(install)
    print(f"{len(records)} civilisations, patch {patch}")

    committed = OUT_DIR / "manifest.json"
    before = (json.loads(committed.read_text(encoding="utf-8"))
              if committed.exists() else {})
    changes = differences(manifest, before) if before else ["no reference yet"]
    per_civ = civ_differences(records) if before else []

    if changes:
        print("\nwhat changed:")
        for line in changes[:40]:
            print(f"    {line}")
        if len(changes) > 40:
            print(f"    ... and {len(changes) - 40} more")
    if per_civ:
        print("\ncivilisations whose reference would change:")
        for line in per_civ:
            print(f"    {line}")
    if not changes and not per_civ:
        print("\nno change since the last snapshot")

    if arguments.write:
        write(records, patch, manifest, install)
        print(f"\nwritten to {OUT_DIR}")
        return 0
    if arguments.check:
        return 1 if (changes or per_civ) else 0
    print("\n(nothing written - pass --write)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
