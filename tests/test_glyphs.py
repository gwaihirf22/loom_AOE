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

    def fake_read(line_bgr, font_):
        text = sequence[min(reads["count"], len(sequence) - 1)]
        reads["count"] += 1
        return text, 1.0

    monkeypatch.setattr(glyphs, "read_line", fake_read)
    events = watcher.watch(panel_for(["a", "b"]), game_time=100)
    assert events == ["built:house"]        # the old line did NOT fire


def test_watcher_lingering_stack_fires_once(monkeypatch):
    watcher = TextWatcher(save_unread=False)
    monkeypatch.setattr(glyphs, "read_line",
                        lambda line, font: ("--Mill Built--", 1.0))
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
                        lambda line, font: ("--Villager Created--", 1.0))
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

    def fake_read(line, font):
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
                        lambda line, font: ("--Mill Built--", 1.0))
    assert watcher.watch(panel_for(["x"]), 100) == ["built:mill"]
    assert watcher.watch(np.zeros((30, 400, 3), np.uint8), 104) == []
    assert watcher.watch(panel_for(["x"]), 106) == []     # flap, floored
    assert watcher.watch(np.zeros((30, 400, 3), np.uint8), 110) == []
    assert watcher.watch(panel_for(["x"]), 130) == ["built:mill"]
