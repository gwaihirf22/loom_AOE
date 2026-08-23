"""
Loom — reading the age off the HUD.

The fixtures are cut from renderings the TEMPLATES WERE NOT cut from: a
different HUD skin, a different resolution, and frames mid-advance. That is
the whole point of them. Matching a template against the capture it came
from proves nothing, and the two failures this feature could plausibly have
are exactly the ones such a test would miss - a crest that turns out to be
skin-specific after all, and one that does not survive being resized.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob

import cv2
import pytest

from loom import age, hud, paths

FIXTURES = paths.PROJECT_ROOT / "tests" / "data" / "age"


def fixture(name):
    """A fixture band and the HUD scale it was captured at."""
    found = sorted(glob.glob(str(FIXTURES / f"{name}@*.png")))
    if not found:
        pytest.skip(f"no fixture {name}")
    scale = float(found[0].rsplit("@", 1)[1][:-4])
    return cv2.imread(found[0]), scale


@pytest.fixture(scope="module")
def crests():
    loaded = age.load_crests()
    if not loaded:
        pytest.skip("no age crests harvested")
    return loaded


def test_every_age_has_a_crest(crests):
    """Four ages, four templates. A missing one reads as "no reading"
    forever on that age, which is silent."""
    assert set(crests) == {age.DARK, age.FEUDAL, age.CASTLE, age.IMPERIAL}


def test_the_crest_reads_on_the_other_skin(crests):
    """The templates are cut from Anne_HK. If the crest were skin-specific
    art this would fail, and the feature would need eight templates and a
    per-skin harvest forever."""
    band, scale = fixture("stock_1440p_castle_crest")
    found, score = age.read_age(band, crests, scale)
    assert found == age.CASTLE, f"read {age.NAMES.get(found)} at {score:.3f}"
    assert score >= age.MIN_CREST_SCORE


def test_the_crest_reads_at_the_other_resolution(crests):
    """Cut at 0.98, read at 0.73. Artwork resizes where text does not -
    which is the reason this reads the crest and not the age's name."""
    band, scale = fixture("annehk_1080p_feudal_crest")
    found, score = age.read_age(band, crests, scale)
    assert found == age.FEUDAL, f"read {age.NAMES.get(found)} at {score:.3f}"


def test_the_crest_reads_on_the_other_skin_and_resolution_at_once(crests):
    band, scale = fixture("stock_1080p_dark_crest")
    found, _score = age.read_age(band, crests, scale)
    assert found == age.DARK


def test_a_band_with_no_crest_in_it_is_no_reading(crests):
    """Never guess. An empty or unrecognisable band answers None, not the
    least-bad age - the age gates which step is shown."""
    band, scale = fixture("stock_1440p_castle_crest")
    blank = band.copy()
    blank[:] = 0
    found, _score = age.read_age(blank, crests, scale)
    assert found is None
    assert age.read_age(None, crests, scale)[0] is None


def test_the_progress_bar_says_when_an_age_up_is_running(crests):
    """The click, read directly. The bar is a colour rather than a shape,
    so it needs no template and no per-rendering cut."""
    for name in ("stock_1440p_advancing_bar", "annehk_1080p_advancing_bar"):
        band, _scale = fixture(name)
        assert age.advancing(band) is True, name
        share = age.progress(band)
        assert share is not None and 0.0 < share <= 1.0, name


def test_no_bar_when_nothing_is_being_researched():
    for name in ("stock_1440p_castle_bar", "annehk_1080p_feudal_bar",
                 "stock_1080p_dark_bar"):
        band, _scale = fixture(name)
        assert age.advancing(band) is False, name
        assert age.progress(band) is None, name


def test_an_unreadable_bar_is_unknown_rather_than_absent():
    """"I did not see the bar" is not "the bar is not there"."""
    assert age.advancing(None) is None
    assert age.progress(None) is None


def test_the_age_being_advanced_to_is_the_next_one():
    """Nothing has to be read for this, which is what keeps the age's NAME
    - the one part that would need a harvest per rendering - off the
    critical path entirely."""
    assert age.AgeReading(age.FEUDAL, 0.9, advancing=True).target == age.CASTLE
    assert age.AgeReading(age.FEUDAL, 0.9, advancing=False).target is None
    assert age.AgeReading(age.IMPERIAL, 0.9, advancing=True).target is None
    assert age.AgeReading(None, 0.0, advancing=True).target is None


def test_both_skins_look_in_the_same_place():
    """One pair of offsets serves both profiles because the crest is
    SEARCHED for inside a roomy band. If a skin ever needs its own, this is
    the test that has to be changed deliberately rather than by accident."""
    bands = {(p.age_band, p.age_bar_band) for p in hud.PROFILES}
    assert len(bands) == 1


# --- turning readings into events -----------------------------------------


def reading(current, advancing):
    return age.AgeReading(current, 0.9, advancing=advancing)


def feed(tracker, script):
    """Run (age, advancing, time) triples through and collect the events."""
    seen = []
    for current, advancing, moment in script:
        for event in tracker.update(reading(current, advancing), moment):
            seen.append((moment,) + event)
    return seen


def test_the_bar_appearing_is_the_click_and_the_crest_changing_is_arrival():
    tracker = age.AgeTracker()
    events = feed(tracker, [
        (age.DARK, False, 0), (age.DARK, False, 10),
        (age.DARK, True, 20), (age.DARK, True, 30), (age.DARK, True, 40),
        (age.FEUDAL, False, 50), (age.FEUDAL, False, 60),
    ])
    assert events == [(30, age.CLICKED, age.FEUDAL),
                      (60, age.REACHED, age.FEUDAL)]
    assert tracker.clicked_at[age.FEUDAL] == 30
    assert tracker.reached_at[age.FEUDAL] == 60


def test_one_bad_look_changes_nothing():
    """A reading is one glance and a glance can be wrong - a fade, a menu, a
    mid-transition redraw. Nothing can get stuck either: the belief is
    always two agreeing looks from the truth."""
    tracker = age.AgeTracker()
    events = feed(tracker, [
        (age.DARK, False, 0), (age.DARK, False, 10),
        (age.CASTLE, False, 20),          # one absurd glance
        (age.DARK, False, 30), (age.DARK, False, 40),
    ])
    assert events == []
    assert tracker.age == age.DARK


def test_an_unread_crest_is_not_a_change():
    """None means "could not tell", which is not news and must never be
    mistaken for the age having gone away."""
    tracker = age.AgeTracker()
    feed(tracker, [(age.FEUDAL, False, 0), (age.FEUDAL, False, 10)])
    assert tracker.age == age.FEUDAL
    assert feed(tracker, [(None, None, 20), (None, None, 30)]) == []
    assert tracker.age == age.FEUDAL


def test_the_bar_going_away_claims_nothing():
    """A cancelled research and a completed one look identical from the bar
    alone. The crest reports the completion; nothing is said about the
    other case rather than guessing at it."""
    tracker = age.AgeTracker()
    events = feed(tracker, [
        (age.DARK, False, 0), (age.DARK, False, 10),
        (age.DARK, True, 20), (age.DARK, True, 30),
        (age.DARK, False, 40), (age.DARK, False, 50),   # cancelled
    ])
    assert [e[1] for e in events] == [age.CLICKED]


def test_a_crest_going_backwards_is_a_new_match_not_an_age_up():
    tracker = age.AgeTracker()
    feed(tracker, [(age.CASTLE, False, 0), (age.CASTLE, False, 10)])
    events = feed(tracker, [(age.DARK, False, 20), (age.DARK, False, 30)])
    assert events == []
    assert tracker.age == age.DARK


def test_imperial_has_nothing_to_advance_to():
    tracker = age.AgeTracker()
    events = feed(tracker, [
        (age.IMPERIAL, False, 0), (age.IMPERIAL, False, 10),
        (age.IMPERIAL, True, 20), (age.IMPERIAL, True, 30),
    ])
    assert events == []



def test_clicked_through_is_proven_three_ways():
    from loom.age import AgeTracker, AgeReading, FEUDAL, CASTLE
    tracker = AgeTracker(looks_to_believe=1)
    assert tracker.clicked_through is None
    # The crest alone: being IN an age proves nothing below it waits.
    tracker.update(AgeReading(age=1), 10)
    assert tracker.clicked_through == 1
    # The bar: a click seen is a click proven.
    tracker.update(AgeReading(age=1, advancing=True), 300)
    assert tracker.clicked_through == FEUDAL
    # The crest reaching an age proves its click even if the bar read was
    # missed - the gate must never stick on a missed reading.
    tracker.update(AgeReading(age=2, advancing=False), 700)
    tracker.update(AgeReading(age=3, advancing=False), 990)
    assert tracker.clicked_through == CASTLE


def test_the_queue_witness_outranks_the_blind_bar():
    """The two monitors of "advancing" meet in the tracker. The banner's
    red bar is honestly blind early in a research (the fill has not
    reached its gate), so the queue SHOWING the age-up wins over the
    bar's False - measured live, the CLICK UP band kept flashing after
    the click for exactly that stretch. The queue showing nothing
    delegates to the bar unchanged: silence is not absence."""
    from loom.age import AgeTracker, AgeReading, FEUDAL, CLICKED
    tracker = AgeTracker(looks_to_believe=2)
    tracker.update(AgeReading(age=1, advancing=False), 10)
    tracker.update(AgeReading(age=1, advancing=False), 11)
    # The click: the queue sees the research while the bar still reads
    # False. Two agreeing looks and the click is believed.
    assert tracker.update(AgeReading(age=1, advancing=False), 20,
                          queued_target=FEUDAL) == []
    assert tracker.update(AgeReading(age=1, advancing=False), 21,
                          queued_target=FEUDAL) == [(CLICKED, FEUDAL)]
    assert tracker.advancing is True
    # Queue silent again, bar unread: no witness, no news - the belief
    # stands rather than flapping back.
    tracker.update(AgeReading(age=1, advancing=None), 30)
    assert tracker.advancing is True
