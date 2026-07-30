"""
Loom — tests for reading the global production queue.

The image-facing tests run against real 48x48 slot crops cut from capture
frames (tests/data/queue/), each named for what a human verified it shows.
The fixtures cover every state the classifier claims to handle: all three
tints, the untinted waiting portrait, a tech staying green through a pop
block, the white mouse-hover ring, and plain terrain where a slot is not.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import numpy as np
import pytest

from loom import queue
from loom.production import (ProductionTracker, PRODUCTION_IDLE,
                             PRODUCTION_RESUMED, HOUSED, POP_CAPPED, UNBLOCKED)

DATA = pathlib.Path(__file__).parent / "data" / "queue"


def fixture(name):
    image = cv2.imread(str(DATA / f"{name}.png"))
    assert image is not None, f"missing fixture {name}"
    return image


# --- tint classification -------------------------------------------------

def test_green_wash_is_green():
    tint, progress = queue.classify_tint(fixture("green_villager"))
    assert tint == "green"
    assert 0.0 < progress <= 1.0


def test_red_wash_is_red():
    tint, _ = queue.classify_tint(fixture("red_housed"))
    assert tint == "red"


def test_amber_wash_is_amber():
    tint, _ = queue.classify_tint(fixture("amber_villager_x3"))
    assert tint == "amber"


def test_tech_stays_green_under_pop_cap():
    # In a fully pop-capped queue every unit goes amber, but a tech keeps
    # producing and keeps its green wash. The classifier must not let the
    # amber neighbours bleed into this call.
    tint, _ = queue.classify_tint(fixture("green_tech_masonry"))
    assert tint == "green"


def test_waiting_portrait_has_no_tint():
    tint, progress = queue.classify_tint(fixture("dark_waiting_x1"))
    assert tint is None
    assert progress is None


def test_hover_ring_does_not_change_tint():
    tint, _ = queue.classify_tint(fixture("amber_hover_ring"))
    assert tint == "amber"


def test_bare_skin_is_not_a_tint():
    # The male villager portrait is a quarter warm-hued pixels before any
    # wash touches it. This cell false-alarmed "housed" for a whole live
    # session; skin must never read as red or amber.
    tint, _ = queue.classify_tint(fixture("untinted_villager_skin"))
    assert tint in (None, "green")


# --- group counts ---------------------------------------------------------

@pytest.fixture(scope="module")
def count_templates():
    return queue.load_count_templates()


def test_count_reads_or_abstains(count_templates):
    # The never-guess rule in numbers: a readable count must be right, and
    # an unreadable one must be None - never a wrong value.
    expectations = {
        "amber_villager_x3": 3,
        "amber_ram_x7": 7,
        "red_housed": 1,
        "dark_waiting_x1": 1,
        "green_villager": 2,
    }
    for name, expected in expectations.items():
        got = queue.read_count(fixture(name), count_templates)
        assert got in (expected, None), f"{name}: read {got}, not {expected}"


# --- identity -------------------------------------------------------------

@pytest.fixture(scope="module")
def icon_templates():
    return queue.load_icon_templates()


def test_identifies_villager(icon_templates):
    gray = cv2.cvtColor(fixture("green_villager"), cv2.COLOR_BGR2GRAY)
    name, score = queue.identify(gray, icon_templates)
    assert name in ("villager_male", "villager_female")
    assert score >= queue.MIN_IDENTITY_SCORE


def test_identifies_ram_through_amber(icon_templates):
    gray = cv2.cvtColor(fixture("amber_ram_x7"), cv2.COLOR_BGR2GRAY)
    name, _ = queue.identify(gray, icon_templates)
    assert name == "battering_ram"


def test_identifies_masonry_tech(icon_templates):
    gray = cv2.cvtColor(fixture("green_tech_masonry"), cv2.COLOR_BGR2GRAY)
    name, _ = queue.identify(gray, icon_templates)
    assert name == "masonry"


# --- occupancy ------------------------------------------------------------

def compose_frame(occupied_cells):
    """Paste occupied-cell fixtures onto flat terrain at the real grid spots.

    Occupancy only looks at edge structure at the grid positions, so a
    composed frame with the real geometry exercises the same code path as a
    capture, without needing megabytes of committed frames.
    """
    frame = np.full((300, 900, 3), (60, 105, 75), np.uint8)  # flat grass green
    boxes = queue.slot_boxes(6, 15, 1.0)
    for index, name in enumerate(occupied_cells):
        x1, y1, x2, y2 = boxes[index]
        frame[y1:y1 + 48, x1:x1 + 48] = fixture(name)
    return frame, boxes


def test_counts_occupied_prefix():
    names = ["green_villager", "amber_villager_x3", "red_housed",
             "dark_waiting_x1", "amber_ram_x7"]
    frame, boxes = compose_frame(names)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    assert queue.count_occupied(gray, boxes) == 5


def test_empty_grid_counts_zero():
    frame, boxes = compose_frame([])
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    assert queue.count_occupied(gray, boxes) == 0


def test_techs_never_carry_counts():
    """The game's rule, applied both ways by confidence.

    A confident tech keeps its identity and sheds the count (the age
    shield's III strokes read as digits); a weak "tech" with a real count
    is a misidentified unit batch and surrenders its identity - the exact
    mechanism that credited a TC with wheelbarrow research while a
    halberdier batch trained, masking a real idle TC.
    """
    confident = queue.CONTENT_IDENTITY_SCORE
    assert queue.reconcile_identity_and_count("castle_age", confident + 0.1,
                                              11) == ("castle_age", None)
    assert queue.reconcile_identity_and_count("wheelbarrow", confident - 0.1,
                                              4) == (None, 4)
    # Units keep both; techs with no count are untouched.
    assert queue.reconcile_identity_and_count("villager_male", 0.5, 3) \
        == ("villager_male", 3)
    assert queue.reconcile_identity_and_count("loom", 0.25, None) \
        == ("loom", None)


def test_every_template_is_classified_unit_or_tech():
    """Drift guard: a new template must land in exactly one camp, or the
    count/identity reconciliation cannot reason about it."""
    built = {path.name.split(".")[0]
             for path in (paths.TEMPLATES_DIR / "queue").glob("*.png")}
    techs = queue.TECH_IDENTITIES & built
    units = built - queue.TECH_IDENTITIES
    assert techs | units == built
    # Spot-check the two camps contain what they should.
    assert "castle_age" in techs and "villager_male" in units
    assert production.TC_TECH_IDENTITIES <= queue.TECH_IDENTITIES


def test_decor_shows_edges_but_no_content(icon_templates, count_templates):
    """The invariant behind the occupancy content gate.

    Several civs hang decorative UI art exactly where slot one sits (the
    Lithuanian drape, the Goth chains). Decor can pass the edge-box test -
    that is the live false positive - but it must never produce any content
    signal, because tint+count+identity all reading None is what ends the
    occupancy walk in QueueReader.read().
    """
    decor = np.full((48, 48, 3), (60, 70, 80), np.uint8)      # muted panel
    cv2.rectangle(decor, (1, 1), (46, 46), (190, 195, 200), 2)  # bright frame
    gray = cv2.cvtColor(decor, cv2.COLOR_BGR2GRAY)
    frame = np.zeros((120, 120), np.uint8)
    frame[30:78, 30:78] = gray
    assert queue._edge_second_weakest(frame, (30, 30, 78, 78)) \
        >= queue.MIN_EDGE_STEP

    tint, _ = queue.classify_tint(decor)
    assert tint is None
    assert queue.read_count(decor, count_templates) is None
    # A flat panel can luck past the matcher's floor against one of many
    # templates; what it must never do is look CONVINCING.
    _, score = queue.identify(gray, icon_templates)
    assert score < queue.CONTENT_IDENTITY_SCORE


def test_terrain_cell_is_not_occupied():
    for name in ("empty_terrain", "empty_terrain_2"):
        frame, boxes = compose_frame([name])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # A pasted terrain crop has edges at the paste seam, but a real empty
        # cell sits in continuous terrain; this asserts the pasted SLOT crops
        # count and pasted terrain does not overwhelm the threshold. The seam
        # makes this deliberately the hardest version of the test.
        assert queue.count_occupied(gray, boxes) <= 1


# --- the production tracker ----------------------------------------------

def slot(tint=None, identity=None):
    return queue.SlotReading(0, tint, None, None, identity, 0.0)


def vill(tint="green"):
    return slot(tint, "villager_male")


def test_idle_needs_two_polls():
    tracker = ProductionTracker()
    assert tracker.update(100, []) == []
    # The match starts with a TC (tcs_seen floors at 1), so an empty queue
    # confirms BOTH facts on the same poll: production idle, and the starting
    # TC idle - even though no villager was ever seen training.
    assert tracker.update(103, []) == [PRODUCTION_IDLE, TC_IDLE]
    assert tracker.idle
    # Idleness is dated from the first empty glance, not the confirmation.
    assert tracker.idle_since == 100
    assert tracker.idle_duration(110) == 10


def test_single_empty_glance_is_ignored():
    tracker = ProductionTracker()
    tracker.update(100, [slot("green")])
    tracker.update(103, [])
    assert tracker.update(106, [slot("green")]) == []
    assert not tracker.idle


def test_resume_needs_two_polls():
    tracker = ProductionTracker()
    tracker.update(100, [])
    tracker.update(103, [])
    assert tracker.idle
    tracker.update(106, [vill()])
    events = tracker.update(109, [vill()])
    assert PRODUCTION_RESUMED in events
    assert not tracker.idle
    assert tracker.idle_duration(112) == 0


def test_unreadable_changes_nothing():
    tracker = ProductionTracker()
    tracker.update(100, [])
    tracker.update(103, None)
    assert PRODUCTION_IDLE in tracker.update(106, [])


def test_housed_and_unblocked():
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill("red")])
    events = tracker.update(103, [vill(), vill("red")])
    assert events == [HOUSED]
    assert tracker.blocked == "housed"
    tracker.update(106, [vill()])
    assert tracker.update(109, [vill()]) == [UNBLOCKED]
    assert tracker.blocked is None


def test_amber_is_routine_not_an_event():
    # The frame audit showed amber on any merely-WAITING item (behind a
    # villager, behind an age research), so it must never fire an event.
    tracker = ProductionTracker()
    tracker.update(100, [vill("amber")])
    assert tracker.update(103, [vill("amber")]) == []
    assert tracker.blocked is None


def test_red_still_means_housed():
    # The red/amber distinction is preserved: red is the housed wash.
    tracker = ProductionTracker()
    tracker.update(100, [vill("red"), vill("amber")])
    assert tracker.update(103, [vill("red"), vill("amber")]) == [HOUSED]


# --- Town Centre tracking --------------------------------------------------

from loom import paths, production
from loom.production import TC_IDLE, TC_RECOVERED


def test_every_tc_identity_has_a_template():
    """The drift guard: an identity with no template fails silently.

    A TC researching Town Patrol once read as idle for exactly this reason -
    the name was in the identity set, but the matcher had no template to
    recognise it with, so the slot came back unidentified.
    """
    # Everything before the first dot is the identity; the rest names a
    # variant ("castle_age.2.png" is a second Castle Age art style).
    built = {path.name.split(".")[0]
             for path in (paths.TEMPLATES_DIR / "queue").glob("*.png")}
    assert production.TC_TECH_IDENTITIES <= built, \
        f"techs missing templates: {production.TC_TECH_IDENTITIES - built}"
    assert production.TC_UNIT_IDENTITIES <= built, \
        f"units missing templates: {production.TC_UNIT_IDENTITIES - built}"


def test_age_identities_have_regional_variants():
    """The age shields change art per civilization architecture - a single
    variant read one civ's age-up as an idle TC. At least two styles must
    stay built for each age."""
    for age in ("feudal_age", "castle_age", "imperial_age"):
        variants = list((paths.TEMPLATES_DIR / "queue").glob(f"{age}*.png"))
        assert len(variants) >= 2, f"{age}: only {len(variants)} variant(s)"


def test_second_tc_raises_the_high_water_mark():
    tracker = ProductionTracker()
    tracker.update(100, [vill()])
    assert tracker.tcs_seen == 1
    # Queue evidence needs three CONTINUOUS game-seconds above the mark -
    # persistent misreads fooled shorter streaks into minting phantom TCs.
    tracker.update(103, [vill(), vill()])
    assert tracker.tcs_seen == 1
    tracker.update(104, [vill(), vill()])
    assert tracker.tcs_seen == 1          # only 1s sustained so far
    tracker.update(106, [vill(), vill()])
    assert tracker.tcs_seen == 2          # 3s continuous: believed
    # The mark never falls during a game - one TC going quiet is exactly
    # what the idle-TC warning exists to catch.
    tracker.update(109, [vill()])
    assert tracker.tcs_seen == 2


def test_a_blip_of_double_greens_does_not_mint_a_tc():
    tracker = ProductionTracker()
    tracker.update(100, [vill()])
    tracker.update(101, [vill(), vill()])   # misread flickers in...
    tracker.update(102, [vill(), vill()])
    tracker.update(103, [vill()])           # ...and out before 3s
    tracker.update(106, [vill(), vill()])   # a fresh window must restart
    tracker.update(107, [vill(), vill()])
    assert tracker.tcs_seen == 1


def test_shrinking_evidence_proves_the_lower_count():
    # Three greens sustaining, dipping to two, still proves two: the
    # candidate tracks the lowest count held throughout the window.
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill(), vill()])
    tracker.update(102, [vill(), vill()])
    tracker.update(103, [vill(), vill()])
    assert tracker.tcs_seen == 2


def test_queue_evidence_closes_after_the_opening():
    # Queue evidence exists to catch a multi-TC START. Past two minutes
    # every new TC announces itself in the notification feed, and only
    # that channel may raise the count - a live Turks game minted three
    # phantom TCs from sustained queue misreads before this window.
    tracker = ProductionTracker()
    tracker.update(200, [vill(), vill()])
    tracker.update(202, [vill(), vill()])
    tracker.update(204, [vill(), vill()])
    tracker.update(210, [vill(), vill()])
    assert tracker.tcs_seen == 1


def test_notification_raises_the_count_at_any_time():
    tracker = ProductionTracker()
    tracker.update(1000, [vill()])
    tracker.register_tc_built()
    assert tracker.tcs_seen == 2


def test_idle_tc_detected_when_one_of_two_stops():
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill()])
    tracker.update(103, [vill(), vill()])
    tracker.update(106, [vill()])
    events = tracker.update(109, [vill()])
    assert TC_IDLE in events
    assert tracker.idle_tcs == 1
    tracker.update(112, [vill(), vill()])
    assert TC_RECOVERED in tracker.update(115, [vill(), vill()])
    assert tracker.idle_tcs == 0


def test_researching_tc_is_not_idle():
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill()])
    # One TC switches to researching wheelbarrow: still working.
    researching = [vill(), slot("green", "wheelbarrow")]
    tracker.update(103, researching)
    assert tracker.update(106, researching) == []
    assert tracker.idle_tcs == 0


def test_blocked_tc_is_not_idle():
    # A housed TC has work queued - that is the blockage's problem. Only the
    # HOUSED event should fire, not a TC_IDLE pile-on.
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill()])
    blocked = [vill("red"), vill("red")]
    tracker.update(103, blocked)
    events = tracker.update(106, blocked)
    assert events == [HOUSED]
    assert tracker.idle_tcs == 0


def test_waiting_villager_group_proves_nothing():
    # One TC with [tech, villagers] queued shows two groups, but the
    # untinted waiting group must not count as a second TC.
    tracker = ProductionTracker()
    seen = [slot("green", "loom"), vill(None)]
    tracker.update(100, seen)
    tracker.update(103, seen)
    assert tracker.tcs_seen == 1    # the researching TC, and nothing more
    assert tracker.idle_tcs == 0


def test_the_starting_tc_is_assumed():
    # Every standard match begins with a TC, so an untouched queue means an
    # idle TC from second zero - no villager needs to be seen first. (Nomad
    # is the known exception, deliberately not handled yet.)
    tracker = ProductionTracker()
    assert tracker.tcs_seen == 1


def test_researching_does_not_raise_the_high_water_mark():
    # The tech icons are dark silhouettes that mis-match military groups -
    # letting them prove TCs invented phantoms in a one-TC game. Only green
    # villager groups (and the notification feed) create TCs.
    tracker = ProductionTracker()
    seen = [vill(), slot("green", "town_watch")]
    tracker.update(100, seen)
    tracker.update(103, seen)
    assert tracker.tcs_seen == 1
    assert tracker.idle_tcs == 0    # the "tech" still counts as busy work


def test_fresh_age_up_counts_busy_instantly():
    # A just-clicked age-up's wash is too thin to classify, so the slot
    # reads tint None. It must count as busy from the first glance - the
    # old green-only rule flashed TC IDLE at "Researching 3%".
    tracker = ProductionTracker()
    seen = [slot(None, "feudal_age")]
    assert tracker.update(100, seen) == []
    assert tracker.update(103, seen) == []
    assert tracker.tc_busy == 1
    assert tracker.idle_tcs == 0


def test_notification_registers_a_real_tc():
    tracker = ProductionTracker()
    tracker.update(100, [vill()])
    tracker.register_tc_built()
    assert tracker.tcs_seen == 2
    # Only one villager group training now: the new TC is idle.
    tracker.update(103, [vill()])
    events = tracker.update(106, [vill()])
    assert TC_IDLE in events
    assert tracker.idle_tcs == 1


def test_reset_forgets_the_old_game():
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill(), vill()])
    tracker.reset()
    assert tracker.tcs_seen == 1    # back to the assumed starting TC
    assert tracker.idle_tcs == 0
    assert not tracker.idle
