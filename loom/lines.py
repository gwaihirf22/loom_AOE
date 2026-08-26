"""
Loom — every line the game can print, and matching a bad read against them.

The third reader, after the letter reader (glyphs) and the word gate
(KNOWN_WORDS). Those two work bottom-up: pixels become letters, letters
become words, and one unknown word refuses the line. This one works
top-down. The game is not producing arbitrary text - it prints one of a
few thousand sentences with a rigid shape, "--<Subject> <Phrasing>--" -
so a corrupted read can be compared against the whole set of things it
could have been.

Why that is worth so much more than fixing the word: measured on the
corpus, 80% of misread WORDS have exactly one real game word at their
distance, but 96% of misread LINES do. A notification is 20-40 characters
of rigid structure, so a four-edit corruption still leaves the better part
of thirty characters agreeing with exactly one sentence. Enlarging the
candidate set twelvefold - from the 159 lines those games happened to
print to 1864 built from the game's own entity list - did not degrade that
at all, which is the tell that it is the STRUCTURE doing the work rather
than a small pool.

This never runs on a line that already parsed. It is the last thing tried,
on a read that would otherwise be thrown away, and it refuses far more
often than it answers - see LINE_MARGIN.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os
import re

from . import glyphs, paths

# How far a read may sit from a real line and still be matched to it, and
# how far clear of its nearest RIVAL that match must be. Both measured
# end to end on the labelled corpus, against a universe built from the
# shipped icons alone so nothing was scored right by having been told:
#
#     budget 8, margin 1   68 of 87 recovered, 3 WRONG
#     budget 5, margin 1   56 of 87 recovered, 2 WRONG
#     budget 5, margin 2   33 of 87 recovered, 0 wrong
#     budget 4, margin 2   28 of 87 recovered, 0 wrong
#
# The margin is what makes this honest rather than a guess. Dropping it to
# 1 nearly doubles what is recovered and starts inventing events, which is
# the trade this project does not make: a missed line costs a stats entry,
# an invented one poisons the checklist and the Town Centre count.
LINE_BUDGET = 5
LINE_MARGIN = 2

# A read shorter than this is not a corrupted sentence, it is a fragment,
# and a fragment must never become an event. Measured: the shortest real
# line, "--Mill Built--", is fourteen characters.
MIN_LINE_LENGTH = 12

# The phrasings the game wraps a subject in. glyphs.EVENT_SUFFIXES holds
# the same set for parsing; these are the display forms, capitalised the
# way the game draws them.
PHRASINGS = ("Built", "Created", "Research Complete", "Found",
             "Destroyed", "Lost")

# Noise in the icon filenames that is not part of any entity's name.
_FILENAME_NOISE = re.compile(
    r"(_?aoe2_?de|_?aoe2|_?de\b|icon|_alpha|upg|research|resear|_)",
    re.IGNORECASE)


def _split_concatenation(word):
    """"towncenter" -> ["town", "center"], or [word] if it is one word.

    The icon files are named Towncenter and Scoutcavalry while the game
    prints "Town Center" and "Scout Cavalry", so the library holds the
    entity under a name the feed never uses. The vocabulary is what splits
    them - a cut is only taken where BOTH halves are real game words.

    The split is attempted even when the whole token is itself in
    KNOWN_WORDS, and that is deliberate: the vocabulary carries the
    concatenated forms too ("scoutcavalry", "cavalryarcher", "handcart"),
    so stopping at "it is already a word" would refuse the very split it
    is there to power. Both spellings are kept.
    """
    if len(word) < 6:
        return [word]
    for cut in range(3, len(word) - 2):
        if word[:cut] in glyphs.KNOWN_WORDS and word[cut:] in glyphs.KNOWN_WORDS:
            return [word[:cut], word[cut:]]
    return [word]


def subjects():
    """Every entity the shipped icon library names, lowercased.

    DERIVED, not curated, which matters because a hand-written list of
    this kind fails silently - it refuses perfectly-read lines and nothing
    anywhere reports it. The icons ship with the app and are the same
    thing the build orders reference, so they stay in step with the game
    by construction.

    A name is kept only if every word of it is in KNOWN_WORDS, because
    parse_event would refuse anything else anyway.
    """
    found = set()
    for path in glob.glob(str(paths.ICON_LIBRARY_DIR / "*" / "*.webp")):
        stem = os.path.splitext(os.path.basename(path))[0]
        cleaned = _FILENAME_NOISE.sub(" ", stem)
        cleaned = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", cleaned)
        words = [w.lower() for w in cleaned.replace("-", " ").split()
                 if w.isalpha() and len(w) > 1]
        if not words:
            continue
        found.add(" ".join(words))
        pieces = []
        for word in words:
            pieces.extend(_split_concatenation(word))
        found.add(" ".join(pieces))
    found = {name for name in found
             if all(word in glyphs.KNOWN_WORDS for word in name.split())}
    # The game prints an Elite form of most unique units and the icon
    # library does not always carry one. One rule beats fifty entries, and
    # a subject that never occurs only ever costs a rival that loses.
    return found | {f"elite {name}" for name in found
                    if not name.startswith("elite")}


_universe = None


def universe():
    """Every line the game can print: ["--Mill Built--", ...], cached."""
    global _universe
    if _universe is None:
        _universe = sorted(
            "--" + " ".join(word.capitalize() for word in name.split())
            + " " + phrasing + "--"
            for name in subjects() for phrasing in PHRASINGS)
    return _universe


def distance(a, b, cap):
    """Levenshtein distance, giving up past `cap` (returns cap + 1).

    The cap is not only a speed trick: nearly every candidate is far away,
    and abandoning a row once its best cell is already past the budget is
    what keeps a sweep of several thousand lines affordable per read.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for index, char_a in enumerate(a, 1):
        current = [index]
        for position, char_b in enumerate(b, 1):
            current.append(min(previous[position] + 1,
                               current[position - 1] + 1,
                               previous[position - 1] + (char_a != char_b)))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def nearest_line(text, budget=LINE_BUDGET, margin=LINE_MARGIN):
    """(line, event) for the one line this read can only have been, or None.

    The rules, every one refusing toward silence:

      * the read is a whole framed line of a plausible length - a fragment
        must never become an event;
      * some real line sits within `budget` edits;
      * every OTHER candidate within `margin` further out means the same
        event, or nothing is claimed.

    That last rule is the whole safety case, and it compares EVENTS rather
    than strings on purpose: "--Elite Skirmisher Research Complete--" has
    several near neighbours that are the same fact differently spelled,
    and those are not rivals. A neighbour that would mint a DIFFERENT
    event is, and one of those is enough to refuse.
    """
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) < MIN_LINE_LENGTH:
        return None
    if not (stripped.startswith("--") and stripped.endswith("--")):
        return None

    reach = budget + margin
    scored = []
    for candidate in universe():
        found = distance(stripped, candidate, reach)
        if found <= reach:
            scored.append((found, candidate))
    if not scored:
        return None
    scored.sort()
    best_distance, best_line = scored[0]
    if best_distance > budget:
        return None
    event = glyphs.parse_event(best_line)
    if event is None or event.startswith("line:"):
        return None
    for found, candidate in scored[1:]:
        if found > best_distance + margin:
            break
        if _same_fact(glyphs.parse_event(candidate), event):
            continue
        return None                 # a different fact is nearly as close
    return best_line, event


def _same_fact(one, other):
    """Do these two events say the same thing about the same entity?

    Word boundaries are ignored, and that is not a nicety - the icon
    derivation deliberately keeps BOTH spellings of an entity whose file
    is named Scoutcavalry while the game prints "Scout Cavalry", so a
    clean read has its own concatenated twin one edit away. Slugified
    those become created:scout_cavalry and created:scoutcavalry, and a
    rival test comparing slugs called them different facts and refused
    every line whose subject has two names on file - which is most of the
    interesting ones, Town Centre and Scout Cavalry included. Measured by
    a test that fed it a PERFECT line and got nothing back.
    """
    if one is None or other is None:
        return False
    return one.replace("_", "") == other.replace("_", "")
