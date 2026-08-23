"""
Loom — derive the notification vocabulary from the shipped assets.

    python -m tools.build_vocabulary            # report what is missing
    python -m tools.build_vocabulary --write    # add it to glyphs.KNOWN_WORDS

glyphs.KNOWN_WORDS is the last gate on a read notification line: every word
of an event's subject must be a real game word, or the line is refused as a
misread. The gate is sound and its failure mode is silent - a MISSING word
refuses perfectly-read lines and nothing reports it. "camp" was missing
once, and 23 build-order items never ticked; "armor" was missing, and all
nine armour upgrades were refused. Both were found by hand, one game at a
time, which is exactly the process this tool retires.

The words do not need discovering - they are already on disk, twice:

  * templates/queue/*.png are NAMED as identity slugs (battle_elephant,
    arbalest, ...) - 500-odd files covering every unit and technology the
    queue reader knows;
  * master_aoe2_images/** names every entity the build files can draw,
    tidied through the same icon_to_words the panel uses.

The output is a reviewable list of ADDITIONS, never a silent rewrite. The
gate's strictness is its value: junk entering the list weakens it, so
variant markers, digits and single letters are filtered, and --write still
prints everything it adds.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import build_order, glyphs, paths  # noqa: E402

# Fragments that appear in asset file names but are not game words. Most
# noise never gets this far - icon_to_words already strips the de/aoe2/upg
# markers - but the queue templates carry a few naming quirks of their own.
JUNK = {
    "aoe", "aoe2", "aoe2de", "de", "doi", "upg", "icon", "img", "unique",
    "kite", "packed", "unpacked", "district", "research", "alpha",
    # Naming quirks seen in a first run of this tool: abbreviations,
    # UI-artwork labels and stat words that are not game entities. A
    # misread must not be able to accidentally spell one of these.
    "ao", "bei", "cao", "cav", "chu", "civ", "check", "cost", "bonus",
    "box", "damage", "bridge", "chuan", "set", "tech", "tree", "menu",
    "flag", "misc", "old", "new", "big", "small", "left", "right",
    # Generic words from UI-artwork names. Real English, no game entity -
    # and a two-error misread hiding behind "with" or "start" is exactly
    # what the gate exists to refuse.
    "with", "side", "start", "effect", "power", "high", "male", "female",
    "medium", "strong", "unknown", "unit", "ingame", "increase", "reduce",
    "unlock", "navel", "northern", "western", "eastern", "white", "wild",
    # Truncated asset names ("demolition_ship_upgra...").
    "upgra", "resear",
}

# Folders whose names can never appear in a notification line. Civilisation
# and hero names do not print as Built/Created/Research subjects, and the
# resource icons are villager-and-pile artwork the feed never announces.
# Animals STAY: "--Pig Found--" is a real line.
EXCLUDED_FOLDERS = {"civilization", "hero", "resource"}

# The shortest word worth adding automatically. KNOWN_WORDS already holds
# the legitimate short ones (nu, ko, war, yak) by hand; an automatic "ao"
# or "cav" would hand a two-letter misread a word to hide behind.
MIN_WORD = 4


def words_from_stem(stem):
    """One asset file stem into candidate vocabulary words."""
    # Queue templates carry variant suffixes: "arbalest.2" is arbalest.
    stem = re.sub(r"\.\d+$", "", stem)
    tidy = build_order.icon_to_words(stem)
    for word in glyphs.slugify(tidy).split("_"):
        if len(word) < MIN_WORD or word.isdigit() or word in JUNK:
            continue
        if not word.isalpha():
            continue
        # Asset names weld "Icon" onto the entity ("KeshikIcon" arrives in
        # lowercase stems as one word): strip it so the entity's own word
        # is what enters, and the weld never does.
        if word.endswith("icon") and len(word) > 6:
            word = word[:-4]
        yield word


def derived_vocabulary():
    """Every word the shipped assets say the game can print."""
    words = set()
    for path in glob.glob(str(paths.TEMPLATES_DIR / "queue" / "*.png")):
        words.update(words_from_stem(os.path.splitext(
            os.path.basename(path))[0]))
    for path in glob.glob(str(paths.PROJECT_ROOT / "master_aoe2_images"
                              / "**" / "*.*"), recursive=True):
        folder = os.path.basename(os.path.dirname(path))
        if folder in EXCLUDED_FOLDERS:
            continue
        words.update(words_from_stem(os.path.splitext(
            os.path.basename(path))[0]))
    return words


def rewrap(words, width=72):
    lines, current = [], ""
    for word in sorted(words):
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="add the missing words to glyphs.KNOWN_WORDS")
    arguments = parser.parse_args()

    derived = derived_vocabulary()
    missing = sorted(derived - glyphs.KNOWN_WORDS)
    print(f"derived {len(derived)} words from the shipped assets; "
          f"{len(missing)} are missing from KNOWN_WORDS:")
    for word in missing:
        print(f"   {word}")

    if not arguments.write or not missing:
        return

    source = paths.PROJECT_ROOT / "loom" / "glyphs.py"
    text = source.read_text(encoding="utf-8")
    start = text.index('KNOWN_WORDS = set("""')
    end = text.index('""".split())', start) + len('""".split())')
    merged = glyphs.KNOWN_WORDS | set(missing)
    block = 'KNOWN_WORDS = set("""\n' + rewrap(merged) + '\n""".split())'
    source.write_text(text[:start] + block + text[end:], encoding="utf-8")
    print(f"\nwrote {len(merged)} words to {source}")


if __name__ == "__main__":
    main()
