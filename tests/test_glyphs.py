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


def test_a_line_that_flickers_unreadable_does_not_refire(monkeypatch):
    """A lingering line Loom loses and regains must not count twice.

    The line's identity in the stack is the TEXT it read, so a line
    sitting still on screen that reads, fails to read, then reads again
    LEAVES and RE-ENTERS the stack - and its count goes 0 -> 1 each time
    it comes back, which used to trip the burst bypass and fire past the
    cooldown.

    Measured in a real 1920x1080 game: one lingering "--Barracks Built--"
    fired ELEVEN times across thirteen game seconds, and a session counted
    fifteen barracks and twelve monasteries. Eleven barracks in thirteen
    seconds is not impossible to BUILD - but the feed holds a handful of
    lines and each lingers about ten seconds, so eleven separate arrivals
    of one line inside its own linger window is not something the game can
    show. That is what makes it a reader fault rather than a fast player.

    It matters more than a missed read: a gap self-corrects on the next
    poll, while an invented event ticks a checklist item and feeds the
    Town Centre count.

    The bands are supplied rather than drawn, because the fault lives in
    how arrivals are counted and not in how pixels are cut. A neighbour
    line that stays put is part of the reproduction though: with nothing
    carried over between looks there is no alignment to trust and the
    bypass never applies at all, so this only shows on a panel where
    something else is holding still - which is every real panel.
    """
    watcher = TextWatcher(save_unread=False)
    bands = [(0, 30), (30, 60)]
    monkeypatch.setattr(glyphs, "find_lines",
                        lambda panel, min_height=10, scale=1.0: bands)
    # A fresh digest per band per look, so the read cache - which is keyed
    # on the band's pixels - never answers for a band it has not been
    # offered. In a real game the terrain behind the feed moves, which is
    # both why the digests differ and why legibility wobbles at all.
    ticks = {"n": 0}

    def digest(line_bgr):
        ticks["n"] += 1
        return f"band{ticks['n']}"

    monkeypatch.setattr(glyphs, "_band_digest", digest)

    legible = iter([True, False, True, False, True])
    reads = {"n": 0}

    def per_band(line, font, scale=None, skin=None):
        index = reads["n"]
        reads["n"] += 1
        if index % 2 == 0:                      # the top line never moves
            return "--Monastery Built--", 1.0
        return (("--Barracks Built--", 1.0) if next(legible)
                else (None, 0.0))

    monkeypatch.setattr(glyphs, "read_line", per_band)

    panel = np.zeros((60, 400, 3), np.uint8)
    assert "built:barracks" in watcher.watch(panel, 100)
    for moment in (102, 104, 106, 108):
        assert "built:barracks" not in watcher.watch(panel, moment),             "a flicker is not an arrival"


def test_a_real_burst_still_fires_after_the_flicker_fix(monkeypatch):
    """The bypass must keep doing the job it was added for.

    Requiring the old count to be at least one is what separates a burst
    from a flicker, and it must not cost the burst: a SECOND identical line
    arriving beside the first is real, and that is the undercount this
    bypass was written to fix.
    """
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(
        glyphs, "read_line",
        lambda line, font, scale=None, skin=None: ("--Villager Created--", 1.0))
    assert watcher.watch(panel_for(["a"]), 100) == ["created:villager"]
    assert watcher.watch(panel_for(["a", "b"]), 102) == ["created:villager"]
    assert watcher.watch(panel_for(["a", "b", "c"]), 104) == \
        ["created:villager"]


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


# --- the font, read back to itself -----------------------------------------

# ---- the structural repair (a small rendering breaks the SEGMENTATION) -----
#
# Every fixture here is a real 1920x1080 line, and the class of failure is
# not the one the two letter-level repairs were built for. At that size the
# stroke JOINING a letter's verticals falls under the ink threshold, so the
# letter arrives as two runs and each half classifies confidently as
# something else. No score separates that from a genuine pair of letters -
# the rejoined H scores 0.906 against halves of 0.969 and 0.932, while a
# real "ll" reads 1.00 and 1.00 - so the vocabulary has to be the arbiter,
# and these tests exist to keep it an honest one. The last three are the
# poisoning cases: this pass CAN walk a badly-read line into a
# real-sounding sentence, and it must not.


def test_a_letter_broken_in_half_is_put_back_together(font):
    """The case the pass was built for. At 1080p the H of House loses its
    crossbar entirely, so it segments as two runs that read "ll" - and the
    same line's u reads as an n. "llouse" is not a game word; House is."""
    line = cv2.imread(str(DATA / "house_built_1080p_broken_h.png"))
    assert line is not None
    text, _score = glyphs.read_line(line, font, skin="annehk")
    assert text == "--House Built--"
    assert parse_event(text) == "built:house"


def test_an_orphaned_fragment_no_longer_kills_the_line(font):
    """A letter can break so its LEFT half still classifies confidently -
    the C of Created reads C at 0.810 while the two halves together read C
    at 0.910. The confident half was accepted, the other half was orphaned
    below the floor, and the whole line died. It read as nothing at all."""
    line = cv2.imread(str(DATA / "villager_created_1080p_split_c.png"))
    assert line is not None
    text, _score = glyphs.read_line(line, font, skin="annehk")
    assert text == "--Villager Created--"


def test_a_real_double_letter_is_never_restructured(font):
    """The other half of the same ruling, and why the vocabulary is the
    right arbiter. "Villager" holds a real "ll" that scores exactly like a
    broken H, and it must survive untouched - which it does for a reason
    that needs no threshold at all: the word is already a real one, so
    nothing ever asks the search about it."""
    line = cv2.imread(str(DATA / "villager_created_1080p_split_c.png"))
    text, _score = glyphs.read_line(line, font, skin="annehk")
    assert "Villager" in text


def test_a_clean_line_never_reaches_the_structural_pass(font):
    """The safety property the whole design rests on: a framed line whose
    words are all real game words is returned before the search is even
    considered, so a right answer can never be restructured into a wrong
    one."""
    line = cv2.imread(str(DATA / "stable_built.png"))
    assert line is not None
    text, score = glyphs.read_line(line, font)
    assert text == "--Stable Built--"
    # A structural read reports STRUCTURAL_GLYPH_FLOOR as its confidence;
    # a clean read reports what the glyphs actually scored.
    assert score > glyphs.STRUCTURAL_GLYPH_FLOOR


def test_a_badly_read_line_is_refused_rather_than_rebuilt(font):
    """"--Spearman Created--" reads "--Siega Rane Coreated--" at 1080p:
    not a broken letter but a wholesale misread, which happens to sit one
    edit from three real words at once. Rebuilt, it says "--Siege Ram
    Created--" - a unit that was never made, and the Slege Ram bug reborn.
    Two rebuilt words is the most a line may need; three means the
    rendering is gone."""
    line = cv2.imread(str(DATA / "spearman_created_1080p_misread.png"))
    assert line is not None
    text, _score = glyphs.read_line(line, font, skin="annehk")
    assert text != "--Siege Ram Created--"
    assert glyphs.parse_event(text or "") != "created:siege_ram"


def test_a_rebuilt_word_must_keep_the_games_capital(font):
    """"--Carvel Hull Research Complete--" reads "Hoill", whose first two
    runs join into a lowercase "m" - giving "mill", a real word one edit
    away with no rival to keep it honest. The game capitalises every word
    of every notification, so a lowercase rebuild is a coincidence spelled
    like a word, not a repair."""
    line = cv2.imread(str(DATA / "carvel_hull_1080p_lowercase_trap.png"))
    assert line is not None
    text, _score = glyphs.read_line(line, font, skin="annehk")
    assert "mill" not in (text or "")
    assert glyphs.parse_event(text or "") != "researched:carvel_mill"


def test_the_capitals_rule_reads_both_halves_of_a_hyphenated_word():
    """The game capitalises both halves of "Two-Handed" and "Double-Bit",
    so the rule checks every alphabetic run and not merely the first."""
    assert glyphs._keeps_the_games_capitals("Two-Handed")
    assert glyphs._keeps_the_games_capitals("--House")
    assert not glyphs._keeps_the_games_capitals("Two-handed")
    assert not glyphs._keeps_the_games_capitals("mill")


def test_no_two_labels_hold_the_same_picture(font):
    """The check test_digits.py has always made of the ten digit templates,
    finally made of the font as well - and it found the same class of fault
    the moment it was written.

    The notification font is forty times larger than the digit set and is
    cut by a tool from transcribed lines, so a slipped alignment files a
    glyph under its neighbour's letter and nothing downstream can tell.
    Thirty-three variants were filed wrongly that way, eighteen of them
    pixel-identical to a variant of a DIFFERENT letter - one picture, two
    labels, one of which has to be a lie. They were the commonest letters
    in the vocabulary (e, l, n, t, r, a), so nearly every line paid: on the
    1080p corpus run, removing them took understood lines from 36 to 64 and
    events from 74 to 104.

    A variant that is a near-perfect match for another letter cannot be
    defended whichever of the two is right, so this refuses the pair
    outright. Genuine near-twins - i against l, e against c at some
    renderings - sit well below this bar and are left alone; they are the
    reason the vocabulary gate exists downstream.
    """
    matrix, labels, aspects, scales, skins = glyphs._pack(font)
    assert matrix.shape[0] > 0
    labels = np.asarray(labels, dtype=object)

    worst = []
    for row in range(matrix.shape[0]):
        allowed = glyphs._allowed_mask(
            aspects[row], aspects, scales,
            None if np.isnan(scales[row]) else scales[row],
            skins, skins[row] or None)
        # Variants of the same letter are meant to look alike; only a
        # DIFFERENT letter matching this closely is evidence of a slip.
        allowed &= labels != labels[row]
        if not allowed.any():
            continue
        scores = matrix @ matrix[row] / matrix.shape[1]
        scores[~allowed] = -9.0
        best = int(np.argmax(scores))
        if scores[best] >= 0.98:
            worst.append(f"{labels[row]} and {labels[best]} "
                         f"({scores[best]:.3f})")

    assert not worst, (
        "these labels hold the same picture, so one of each pair is "
        "mislabelled: " + "; ".join(sorted(set(worst))[:10]))


# ---- deciding what a line IS, before counting it as anything ------------

def test_two_spellings_of_one_line_are_one_event(monkeypatch):
    """The counter was never wrong; it was handed five names for one thing.

    Measured on a live game: one Hand Cart research produced

        --Hand Can--  --Hand Cart--  --Hand Car--  --Hand Caet--

    across successive looks. Keyed on the TEXT, each is a line never seen
    before, so each was an arrival - five events, all of which resolved to
    `researched:hand_cart` afterwards, by which point the damage was done.
    Twelve technologies were duplicated in one game that way, and a
    technology completes at most once.
    """
    watcher = TextWatcher(save_unread=False)
    spellings = ["--Hand Can Research Complete--",
                 "--Hand Cart Research Complete--",
                 "--Hand Car Research Complete--",
                 "--Hand Caet Research Complete--"]
    spelling = {"now": spellings[0]}
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None:
                        (spelling["now"], 1.0))
    fired = []
    for moment, text in enumerate(spellings):
        spelling["now"] = text
        fired += watcher.watch(panel_for(["x"]), game_time=100 + moment * 4)
    assert fired.count("researched:hand_cart") == 1, fired


def test_the_identity_is_decided_before_the_count_not_after():
    watcher = TextWatcher(save_unread=False)
    key, event, repaired = watcher._identify("--Hand Can Research Complete--")
    assert event == "researched:hand_cart"
    # The KEY is the meaning, not the text - that is the whole fix.
    assert key == "researched:hand_cart"
    assert repaired == "--Hand Cart Research Complete--"


def test_a_line_nothing_can_identify_keys_on_its_own_text():
    # It produces no event either way, so two spellings of an unknown line
    # cost nothing - and it still gets tracked for presence like any other.
    watcher = TextWatcher(save_unread=False)
    key, event, repaired = watcher._identify("--Zzzz Qqqq Wwww--")
    assert event is None and repaired is None
    assert key == "--Zzzz Qqqq Wwww--"


def test_identifying_the_same_text_twice_is_free():
    watcher = TextWatcher(save_unread=False)
    first = watcher._identify("--Hand Can Research Complete--")
    assert "--Hand Can Research Complete--" in watcher._identities
    assert watcher._identify("--Hand Can Research Complete--") is first


def test_a_new_game_keeps_what_lines_mean():
    # Identities map a rendering to its meaning. A new game does not change
    # what a line says, and re-deriving it would walk every sentence the
    # game can print all over again.
    watcher = TextWatcher(save_unread=False)
    watcher._identify("--Hand Can Research Complete--")
    watcher.reset()
    assert watcher._identities
    assert not watcher._places


def test_a_line_absent_from_an_INCOMPLETE_look_has_not_left(monkeypatch):
    """"Not among the texts I read" is not "not on the screen".

    Only the second is evidence, and a look that left any band unread
    cannot support it. Measured over a whole game, this branch produced
    103 of 234 firings, its biggest single contributor being one misread
    of Villager flickering in and out of legibility 33 times.
    """
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    reads = {"text": "--Mill Built--"}
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None:
                        (reads["text"], 1.0))
    assert watcher.watch(panel_for(["x"]), 100) == ["built:mill"]

    # The band is still there and the reader cannot make it out. That is a
    # statement about the READER, not about the screen.
    reads["text"] = None
    for moment in (110, 130, 150):
        assert watcher.watch(panel_for(["x"]), moment) == []

    # It comes back long after the cooldown. Nothing ever SAW it leave.
    reads["text"] = "--Mill Built--"
    assert watcher.watch(panel_for(["x"]), 200) == []


def test_a_line_absent_from_a_COMPLETE_look_really_has_left(monkeypatch):
    # An empty feed is proof: there is no text at all, and no amount of
    # doubt about the reader applies to a blank picture.
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font, scale=None, skin=None:
                        ("--Mill Built--", 1.0))
    assert watcher.watch(panel_for(["x"]), 100) == ["built:mill"]

    for moment in (110, 130, 150):
        assert watcher.watch(panel_for([]), moment) == []
    assert watcher.watch(panel_for(["x"]), 200) == ["built:mill"]


def test_a_line_coming_back_no_lower_than_it_sat_is_the_same_line(monkeypatch):
    """A new print enters at the BOTTOM. One that has not moved down is not new.

    This is the last hole the complete-look rule left open, and it is why
    that rule was not enough alone: a band never DETECTED is not an unread
    band, it is simply absent from the stack - so a line fading at the top
    edge of the panel produces a look that read everything it found and
    does not contain the line. Absence looked proven and was not.

    Every over-count left in a whole game was this: an archery range, a
    castle and two barracks coming back 15 to 70 seconds later at the SAME
    or a HIGHER position than they already held.

    Note the direction. Not "lower, therefore a new print" - that is the
    refuted rule, because a mid-scroll capture transposes adjacent lines.
    This is "not lower, therefore NOT a new print": a veto, and a
    transposition can only ever manufacture the evidence it refuses.
    """
    distinct_digests(monkeypatch)
    watcher = TextWatcher(save_unread=False)
    # Bands come top-first, so the LAST entry is the newest line.
    panel = {"lines": ["--Castle Built--"]}

    def fake_read(line_bgr, font_, scale=None, skin=None):
        text = panel["lines"][fake_read.at % len(panel["lines"])]
        fake_read.at += 1
        return text, 1.0
    fake_read.at = 0
    monkeypatch.setattr(glyphs, "read_line", fake_read)

    def look(moment):
        fake_read.at = 0
        return watcher.watch(panel_for(["x"] * len(panel["lines"])), moment)

    # It arrives at the bottom, which is where a new print goes.
    assert look(100) == ["built:castle"]

    # A house arrives beneath it, pushing the castle up to place 1.
    panel["lines"] = ["--Castle Built--", "--House Built--"]
    assert look(110) == ["built:house"]

    # Now the castle stops being DETECTED at all - it is fading off the top.
    # Every band that is found reads perfectly, so the look is "complete".
    panel["lines"] = ["--House Built--"]
    for moment in (120, 140, 160):
        assert look(moment) == []

    # Back where it was, long after the cooldown. It never moved down, so
    # it is the castle that was there the whole time.
    panel["lines"] = ["--Castle Built--", "--House Built--"]
    assert look(200) == []
