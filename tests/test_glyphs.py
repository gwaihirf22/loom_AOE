"""
Loom — tests for the notification font reader.

The bet under test: one harvested glyph set reads any line the game can
print. The fixtures are real lines cut from real captures, read against
the committed font - so a font regression (a mislabelled glyph, a broken
mask) fails loudly here. The never-guess rule matters more than coverage:
a dropped line costs a stats entry, a misread would poison them, so the
poisoning cases get their own tests.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import numpy as np
import pytest

from loom import glyphs
from loom.glyphs import TextWatcher, parse_event

DATA = pathlib.Path(__file__).parent / "data" / "notifications"

FIXTURES = [
    ("mangonel_created.png", "--Mangonel Created--"),
    ("villager_created.png", "--Villager Created--"),
    ("turkey_found.png", "--Turkey Found--"),
    ("stable_built.png", "--Stable Built--"),
    ("forging_research_complete.png", "--Forging Research Complete--"),
    ("centurion_created.png", "--Centurion Created--"),
]


@pytest.fixture(scope="module")
def font():
    loaded = glyphs.load_font()
    assert loaded, "the committed notification font is missing"
    return loaded


@pytest.mark.parametrize("name,expected", FIXTURES)
def test_real_lines_read_exactly(font, name, expected):
    line = cv2.imread(str(DATA / name))
    assert line is not None
    text, score = glyphs.read_line(line, font)
    assert text == expected
    assert score >= glyphs.MIN_GLYPH_SCORE


def test_a_corrupted_glyph_kills_the_line(font):
    # Never guess: paint over one character and the whole line must drop,
    # not read as its ten remaining neighbours.
    line = cv2.imread(str(DATA / "turkey_found.png"))
    height, width = line.shape[:2]
    line[:, width // 2 - 4: width // 2 + 4] = (255, 255, 255)
    text, _ = glyphs.read_line(line, font)
    assert text is None


def test_empty_font_reads_nothing():
    line = cv2.imread(str(DATA / "turkey_found.png"))
    assert glyphs.read_line(line, {}) == (None, 0.0)


def test_labels_survive_case_insensitive_filesystems():
    # "A" and "a" must never be the same filename - the Windows goal.
    assert glyphs.label_for("A") != glyphs.label_for("a")
    for char in "AzQ9-(":
        assert glyphs.char_for(glyphs.label_for(char)) == char
    assert glyphs.char_for("punct_dashes") == "--"


def test_text_mask_sees_all_eight_player_colors():
    # Grayscale would pass yellow and lose blue; the max-channel mask must
    # pass every player colour. Reference: images/AOE possible player
    # colors.png - grey, red, orange, pink, cyan, yellow, green, blue.
    colors_bgr = [(128, 128, 128), (0, 0, 230), (0, 140, 255),
                  (180, 105, 255), (230, 230, 0), (0, 230, 230),
                  (0, 200, 0), (230, 40, 40)]
    for bgr in colors_bgr:
        band = np.zeros((20, 40, 3), np.uint8)
        band[:] = (20, 20, 25)              # the dark notification box
        band[6:14, 10:30] = bgr             # a colored "stroke"
        mask = glyphs.text_mask(band)
        assert mask[10, 20] == 255, f"player colour {bgr} was lost"
        assert mask[2, 2] == 0


# ---- words to events -------------------------------------------------------

@pytest.mark.parametrize("text,event", [
    ("--Mill Built--", "built:mill"),
    ("--Town Center Built--", "town_center_built"),   # the legacy name
    ("--Knight Created--", "created:knight"),
    ("--Fletching Research Complete--", "researched:fletching"),
    ("--Turkey Found--", "found:turkey"),
    ("--Warning: You are being attacked by", "attacked"),  # wrapped line 1
    ("--Wonder--", "line:wonder"),               # readable, unclassified
    ("--Slege Ram Created--", None),             # misread: not a real word
    ("Complete--", None),                        # fragment: no framing
    ("7 Chaghri Beg: Resetting townsize.", None),  # AI chat, not an event
    ("-", None),                                 # framing scraps
])
def test_parse_event(text, event):
    assert parse_event(text) == event


# ---- the watcher (fake reads, no pixels) -----------------------------------

def panel_for(lines):
    """A synthetic panel: each string painted as one text-like band.

    Rendered as actual glyph strokes, not solid bars: the band finder only
    counts bright ink NEXT TO near-black (the font's outline), which is
    what separates text from sunlit terrain - a solid bar has edges but no
    interior gaps, and correctly finds no band.
    """
    height = 30 * max(1, len(lines))
    panel = np.zeros((height, 400, 3), np.uint8)
    for index in range(len(lines)):
        cv2.putText(panel, "--Sample Text Line--", (10, index * 30 + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return panel


def distinct_digests(monkeypatch):
    """Make every band digest unique, so the read cache never hits.

    Tests that script read_line by CALL SEQUENCE need this: the cache is
    content-addressed, and the synthetic panels paint every band with the
    same pixels - without it, one read would stand in for all of them.
    """
    import itertools
    counter = itertools.count()
    monkeypatch.setattr(glyphs, "_band_digest",
                        lambda line: f"band{next(counter)}")


def test_watcher_only_fires_the_bottom_line(monkeypatch):
    # The feed redisplays HISTORY above new lines (found live: one TC fired
    # three times). Only the bottom-most line may fire.
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    sequence = ["--Mill Built--", "--House Built--"]
    reads = {"count": 0}

    def fake_read(line_bgr, font_, scale=None, skin=None):
        text = sequence[min(reads["count"], len(sequence) - 1)]
        reads["count"] += 1
        return text, 1.0

    monkeypatch.setattr(glyphs, "read_line", fake_read)
    events = watcher.watch(panel_for(["a", "b"]), game_time=100)
    assert events == ["built:house"]        # the old line did NOT fire


def test_watcher_lingering_stack_fires_once(monkeypatch):
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None: ("--Mill Built--", 1.0))
    assert watcher.watch(panel_for(["x"]), 100) == ["built:mill"]
    # The same line lingering: signature unchanged, no matter how long.
    for t in (101, 110, 130, 200):
        assert watcher.watch(panel_for(["x"]), t) == []
    watcher.reset()
    assert watcher.watch(panel_for(["x"]), 300) == ["built:mill"]


def test_watcher_counts_identical_repeats_in_a_burst(monkeypatch):
    # THE fix for the 8-villagers-in-a-145-villager-game undercount: a
    # second identical line arriving under the first changes the stack
    # signature and fires, cooldown or no cooldown.
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None: ("--Villager Created--", 1.0))
    assert watcher.watch(panel_for(["a"]), 100) == ["created:villager"]
    assert watcher.watch(panel_for(["a", "b"]), 103) == ["created:villager"]
    assert watcher.watch(panel_for(["a", "b", "c"]), 106) == \
        ["created:villager"]
    # Static three-line stack: nothing new.
    assert watcher.watch(panel_for(["a", "b", "c"]), 109) == []


def test_watcher_scroll_off_does_not_refire(monkeypatch):
    # A line expiring off the TOP shrinks the stack but the bottom line is
    # the same line - that is not a new event.
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    texts = {1: "--Mill Built--", 2: "--Villager Created--"}
    calls = {"n": 0}

    def fake_read(line, font, scale=None, skin=None):
        calls["n"] += 1
        # Two-band panels read mill-then-villager; one-band reads villager.
        if calls["n"] in (1, 2):
            return texts[calls["n"]], 1.0
        return "--Villager Created--", 1.0

    monkeypatch.setattr(glyphs, "read_line", fake_read)
    first = watcher.watch(panel_for(["a", "b"]), 100)
    assert first == ["created:villager"]        # bottom of the pair
    # The mill line expires; the villager line is now alone at the bottom.
    assert watcher.watch(panel_for(["b"]), 110) == []


def test_watcher_lone_line_flapping_is_rate_floored(monkeypatch):
    # Fade-and-redisplay of the same single line inside the cooldown must
    # not double count; after the cooldown it is genuinely new again.
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None: ("--Mill Built--", 1.0))
    assert watcher.watch(panel_for(["x"]), 100) == ["built:mill"]
    assert watcher.watch(np.zeros((30, 400, 3), np.uint8), 104) == []
    assert watcher.watch(panel_for(["x"]), 106) == []     # flap, floored
    assert watcher.watch(np.zeros((30, 400, 3), np.uint8), 110) == []
    assert watcher.watch(panel_for(["x"]), 130) == ["built:mill"]


# --- the one-glyph repair --------------------------------------------------
#
# Some glyph pairs are true near-twins at some renderings - measured, the
# "b" of "Stable" scored lower_h 0.929 against lower_b 0.915, a coin toss
# no harvest can fix - so a framed line with exactly one unknown word may
# have one glyph replaced by its own RUNNER-UP reading, when that yields
# exactly one known word. The tests below are mostly refusals, because the
# repair's failure mode is a minted event and every ambiguity must decline.


def alt(char, margin=0.02):
    return (char, 0.9, margin)


def test_the_repair_fixes_the_case_it_was_built_for():
    # "Stahle": the h's runner-up is b, and Stable is the only known word
    # one substitution away.
    text = "--Stahle Built--"
    alternatives = [None] * len(text)
    alternatives[text.index("h", 4)] = alt("b")
    from loom.glyphs import repair_unknown_word
    assert repair_unknown_word(text, alternatives) == "--Stable Built--"


def test_no_repair_without_the_frame():
    """A fragment must never become an event, repaired or not."""
    from loom.glyphs import repair_unknown_word
    text = "Stahle Built"
    alternatives = [None] * len(text)
    alternatives[text.index("h", 1)] = alt("b")
    assert repair_unknown_word(text, alternatives) is None


def test_no_repair_when_two_words_are_unknown():
    """Two unknown words means the line is badly read, not one glyph off."""
    from loom.glyphs import repair_unknown_word
    text = "--Stahle Bnilt--"
    alternatives = [None] * len(text)
    alternatives[text.index("h", 4)] = alt("b")
    assert repair_unknown_word(text, alternatives) is None


def test_no_repair_from_a_distant_runner_up():
    """The substituted letter must be what the ink nearly said. A runner-up
    far behind the winner is a different letter, not a near-twin."""
    from loom.glyphs import repair_unknown_word
    text = "--Stahle Built--"
    alternatives = [None] * len(text)
    alternatives[text.index("h", 4)] = alt("b", margin=0.2)
    assert repair_unknown_word(text, alternatives) is None


def test_ambiguity_refuses_rather_than_guesses():
    """Two viable repairs claim nothing. "mall" is one substitution from
    both "wall" (m->w) and "mill" (a->i); with the ink supporting either,
    picking one would be the Slege Ram bug reborn."""
    from loom.glyphs import repair_unknown_word
    text = "--mall Built--"
    alternatives = [None] * len(text)
    alternatives[2] = alt("w")     # wall
    alternatives[3] = alt("i")     # mill
    assert repair_unknown_word(text, alternatives) is None


def test_a_clean_line_is_never_touched():
    """No unknown word, no repair - a real word misread as another real
    word is out of scope on purpose, and untouched."""
    from loom.glyphs import repair_unknown_word
    text = "--Mill Built--"
    alternatives = [alt("x")] * len(text)
    assert repair_unknown_word(text, alternatives) is None


def test_the_vocabulary_covers_every_shipped_asset():
    """KNOWN_WORDS must be a superset of what the shipped assets name.

    The gate's failure mode is silent - a missing word refuses perfectly
    read lines and reports nothing; "camp" cost 23 build items, "armor"
    cost nine armour upgrades. The words are on disk twice over (queue
    templates are NAMED as identity slugs, and the icon library names
    every drawable entity), so a new template landing without its
    vocabulary fails here, naming the words, instead of failing silently
    in some future game.
    """
    from tools.build_vocabulary import derived_vocabulary
    from loom.glyphs import KNOWN_WORDS

    missing = sorted(derived_vocabulary() - KNOWN_WORDS)
    assert not missing, (
        f"assets name words the vocabulary gate would refuse: {missing} - "
        f"run python -m tools.build_vocabulary --write")



# --- the band cutter and the reader, together ------------------------------

BANDS = pathlib.Path(__file__).parent / "data" / "notif_bands"


@pytest.mark.parametrize("name", [
    "mining_camp_built_28px.png",
    "mining_camp_built_28px_second.png",
])
def test_two_descenders_do_not_stretch_the_band(font, name):
    """The line that failed deterministically, three sessions running.

    "Mining Camp" holds two descender letters, whose tails together
    cleared find_lines' row floor and stretched the band to the full
    28px pitch - so every glyph was normalised into a canvas a third
    taller than the templates' and the two commonest camps in any build
    order never existed. These crops are the watcher's own saved bands
    from that game; the cutter must re-cut them to the text core and the
    reader must then read them exactly.
    """
    panel = cv2.imread(str(BANDS / name))
    assert panel is not None
    bands = glyphs.find_lines(panel, scale=1.0)
    assert len(bands) == 1
    top, bottom = bands[0]
    text, score = glyphs.read_line(panel[top:bottom], font, 1.0)
    assert text == "--Mining Camp Built--"
    assert score >= glyphs.MIN_GLYPH_SCORE


# --- the vocabulary-nearest repair -----------------------------------------

def test_a_word_the_vocabulary_nearly_said_becomes_its_event():
    """The stock HUD's confident misreads, each one letter or two off,
    each repairable because exactly one substitution makes the line an
    event the game can print."""
    assert glyphs.nearest_event_repair("--Blacksmith Buih--") == (
        "built:blacksmith", "--Blacksmith Built--")
    assert glyphs.nearest_event_repair("--Goose Eound--") == (
        "found:goose", "--Goose Found--")
    assert glyphs.nearest_event_repair("--Mining Camp Buih--") == (
        "built:mining_camp", "--Mining Camp Built--")


def test_the_repair_refuses_ties_and_narrow_wins():
    """Ambiguity refuses, in both shapes. "Markn" has five vocabulary
    words within two edits that all make grammatical events, so nothing
    is claimed. "GaBeon" (a merged-ll misread of Galley) repairs cleanly
    to Galleon - a real entity ONE edit nearer than the truth - which is
    why a winner must beat every rival by two clear edits, not on
    points. The one wrong repair in thirty-nine, on the labelled
    corpus, was this."""
    assert glyphs.nearest_event_repair("--Markn Built--") is None
    assert glyphs.nearest_event_repair("--GaBeon Created--") is None


def test_the_repair_never_invents_from_fragments_or_novelty():
    # A frameless fragment is a partial read, not a notification.
    assert glyphs.nearest_event_repair("Complete--") is None
    # Genuinely unknown vocabulary (a civ's unique unit the font read
    # perfectly well) is not "close" to anything - refused, not forced.
    assert glyphs.nearest_event_repair("--Guecha Warrior Created--") is None
    # Short words never repair: two edits on three letters is a
    # different word ("Ram"/"Farm" are two apart in the real vocabulary).
    assert glyphs.nearest_event_repair("--Rm Created--") is None


def test_watcher_fires_a_chunk_of_arrivals_at_once(monkeypatch):
    """The alignment rule's reason to exist: messages arriving TOGETHER
    between looks. Bottom-only firing let only the newest speak - a
    five-line chunk on a fast-forwarded replay arrived with a Mill and
    a Lumber Camp in its middle, and neither ever fired."""
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    sequence = iter(["--Goose Found--",
                     "--Goose Found--", "--Lumber Camp Built--",
                     "--Mill Built--", "--House Built--"])
    monkeypatch.setattr(
        glyphs, "read_line",
        lambda line, font, scale=None, skin=None: (next(sequence), 1.0))
    assert watcher.watch(panel_for(["a"]), 100) == ["found:goose"]
    # Three lines arrive in one gap; the goose line is carried over.
    events = watcher.watch(panel_for(["a", "b", "c", "d"]), 110)
    assert events == ["built:lumber_camp", "built:mill", "built:house"]


def test_watcher_fires_a_mid_stack_insertion(monkeypatch):
    """The feed orders lines by event time, so a new line can slot ABOVE
    a newer one that keeps the bottom - measured live, a Mill inserted
    above a younger Villager line. An insertion between carried-over
    lines is an arrival, not history."""
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    sequence = iter(["--Goose Found--", "--Villager Created--",
                     "--Goose Found--", "--Mill Built--",
                     "--Villager Created--"])
    monkeypatch.setattr(
        glyphs, "read_line",
        lambda line, font, scale=None, skin=None: (next(sequence), 1.0))
    assert watcher.watch(panel_for(["a", "b"]), 100) == ["created:villager"]
    events = watcher.watch(panel_for(["a", "c", "b"]), 110)
    assert events == ["built:mill"]


def test_watcher_reorder_flicker_does_not_double_count(monkeypatch):
    """Measured on real frames: mid-scroll captures swap two lines back
    and forth. A line ALREADY COUNTED must not refire on the flip - the
    swap is count-neutral, so the cooldown floors it."""
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    sequence = iter(["--House Built--",
                     "--House Built--", "--Villager Created--",
                     "--Villager Created--", "--House Built--",
                     "--House Built--", "--Villager Created--"])
    monkeypatch.setattr(
        glyphs, "read_line",
        lambda line, font, scale=None, skin=None: (next(sequence), 1.0))
    assert watcher.watch(panel_for(["a"]), 100) == ["built:house"]
    assert watcher.watch(panel_for(["a", "b"]), 101) == ["created:villager"]
    assert watcher.watch(panel_for(["b", "a"]), 103) == []   # the flip
    assert watcher.watch(panel_for(["a", "b"]), 104) == []   # ...and back


# --- the world showing past the end of the message box ---------------------

def test_a_health_bar_beside_a_line_does_not_refuse_it(font):
    """The live failure this exists for, on the frame it happened on.

    The band is as wide as the feed's CROP and the message box ends about
    250px short of that, so a unit's white health bar sat level with
    "--Barracks Built--" and was segmented as if it were text. It cannot
    classify, one unclassifiable run refuses the whole line, and the
    barracks never ticked off the build - silently, three looks running.
    Trimmed to the message the same line reads at 0.92.
    """
    panel = cv2.imread(str(BANDS / "barracks_built_health_bar_beside_it.png"))
    assert panel is not None
    bands = glyphs.find_lines(panel, scale=0.98)
    assert len(bands) == 1
    top, bottom = bands[0]
    text, score = glyphs.read_line(panel[top:bottom], font, 0.98)
    assert text == "--Barracks Built--"
    assert score >= glyphs.MIN_GLYPH_SCORE


def test_the_trim_keeps_a_whole_message_and_drops_the_world():
    """The threshold is the line's own height, and the two things it
    separates are nowhere near it: the widest gap inside real text is a
    word space of a few pixels, and the void before the world beyond the
    box is a couple of hundred. Runs from the frame above."""
    text = [(34, 40), (41, 47), (49, 63), (64, 74), (76, 86)]   # word gaps 1-2
    world = [(575, 578), (613, 615), (637, 640)]                # bars, far right
    assert glyphs.trim_to_message(text + world, 21) == text


def test_the_trim_leaves_an_ordinary_line_alone():
    """Nothing to cut, nothing cut - a line whose gaps are all word gaps
    comes back whole, whatever its height."""
    runs = [(34, 40), (41, 47), (55, 63), (64, 74)]     # widest gap 8px
    assert glyphs.trim_to_message(runs, 21) == runs
    assert glyphs.trim_to_message(runs, 15) == runs
    assert glyphs.trim_to_message([], 21) == []
