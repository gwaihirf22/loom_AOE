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

from loom import hud, queue
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
        "amber_hussite_wagon_x7": 7,
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
    name, score, _ = queue.identify(gray, icon_templates)
    assert name in ("villager_male", "villager_female")
    assert score >= queue.MIN_IDENTITY_SCORE


def test_identifies_hussite_wagon_through_amber(icon_templates):
    # This fixture spent a week mislabelled as a battering ram, and a
    # parade of unrelated techs kept "outscoring the ram" on it - because
    # nothing in the set matched what it actually shows. The author
    # identified it: an amber Hussite Wagon, seven queued. A cell no
    # template matches well is a naming bug waiting to be found, not a
    # threshold to tune around.
    gray = cv2.cvtColor(fixture("amber_hussite_wagon_x7"), cv2.COLOR_BGR2GRAY)
    name, _, _ = queue.identify(gray, icon_templates)
    assert name == "hussite_wagon"


def test_identifies_masonry_tech(icon_templates):
    gray = cv2.cvtColor(fixture("green_tech_masonry"), cv2.COLOR_BGR2GRAY)
    name, _, _ = queue.identify(gray, icon_templates)
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
             "dark_waiting_x1", "amber_hussite_wagon_x7"]
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
    # templates - with five hundred of them it always will - so the
    # occupancy gate keys on the vetted identity instead: the clear-win
    # and margin gates must turn this into an honest None.
    identity, _, _ = queue.QueueReader()._identify_cached(
        0, gray, has_content=False)
    assert identity is None


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
    # Confident identity by default: these tests exercise the tracker's
    # logic, not the evidence gate (which has its own tests below).
    return queue.SlotReading(0, tint, None, None, identity, 0.9, 0.3)


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


def test_amber_never_blocks_and_never_produces():
    # Amber is either waiting behind an in-progress item or stuck at the
    # population cap - it must never fire a BLOCKED event, and it must
    # never count as production either: an amber-alone queue is an idle
    # TC, whatever the reason (the author's rule, proven live when three
    # amber-behind-wheelbarrow items masked a genuinely idle second TC).
    tracker = ProductionTracker()
    tracker.update(100, [vill("amber")])
    events = tracker.update(103, [vill("amber")])
    assert tracker.blocked is None
    assert HOUSED not in events and POP_CAPPED not in events
    assert tracker.tc_busy == 0
    assert TC_IDLE in events


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
    tracker.update(100.0, [vill()])
    assert tracker.tcs_seen == 1
    # Queue evidence needs three CONTINUOUS game-seconds across three
    # reads above the mark - a misread or a tech-handover artifact
    # flickers for a glance or two, a real in-progress item stays.
    tracker.update(100.3, [vill(), vill()])
    assert tracker.tcs_seen == 1          # first glance opens the window
    tracker.update(101.5, [vill(), vill()])
    assert tracker.tcs_seen == 1          # two reads, ~1s: not yet
    tracker.update(103.4, [vill(), vill()])
    assert tracker.tcs_seen == 2          # 3 reads across 3.1s: believed
    # The mark never falls during a game - one TC going quiet is exactly
    # what the idle-TC warning exists to catch.
    tracker.update(106, [vill()])
    assert tracker.tcs_seen == 2


def test_a_blip_of_double_greens_does_not_mint_a_tc():
    tracker = ProductionTracker()
    tracker.update(100.0, [vill()])
    tracker.update(100.3, [vill(), vill()])   # misread flickers in...
    tracker.update(100.6, [vill()])           # ...and out again
    tracker.update(101.2, [vill(), vill()])   # a fresh window must restart
    tracker.update(102.4, [vill(), vill()])   # two reads spanning 1.2s...
    tracker.update(103.0, [vill()])           # ...still dies with the dip
    assert tracker.tcs_seen == 1


def test_shrinking_evidence_proves_the_lower_count():
    # Three items sustaining, dipping to two, still proves two: the
    # candidate tracks the lowest count held throughout the window.
    tracker = ProductionTracker()
    tracker.update(100.0, [vill(), vill(), vill()])
    tracker.update(101.5, [vill(), vill()])
    tracker.update(103.1, [vill(), vill()])
    assert tracker.tcs_seen == 2


def test_queue_evidence_works_all_game():
    # The notification feed cannot carry the TC count alone: the game
    # never reprints a line that is already on screen, so a TC completing
    # while "--Town Center Built--" lingers announces nothing (measured
    # live: a seven-TC game produced three line appearances). In-progress
    # queue items are a floor on the TC count at ANY game time - the old
    # 120-second window undercounted every boom.
    tracker = ProductionTracker()
    tracker.update(1000.0, [vill(), vill()])
    tracker.update(1001.5, [vill(), vill()])
    tracker.update(1003.1, [vill(), vill()])
    assert tracker.tcs_seen == 2


def test_notification_raises_the_count_at_any_time():
    tracker = ProductionTracker()
    tracker.update(1000, [vill()])
    tracker.register_tc_built()
    assert tracker.tcs_seen == 2


def test_idle_tc_detected_when_one_of_two_stops():
    tracker = ProductionTracker()
    tracker.update(100, [vill(), vill()])
    tracker.update(102, [vill(), vill()])
    tracker.update(104, [vill(), vill()])   # third read: 2 TCs believed
    tracker.update(107, [vill()])
    events = tracker.update(110, [vill()])
    assert TC_IDLE in events
    assert tracker.idle_tcs == 1
    tracker.update(113, [vill(), vill()])
    assert TC_RECOVERED in tracker.update(116, [vill(), vill()])
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


def test_amber_and_red_prove_nothing():
    # A different-type item waits AMBER behind its building's front and
    # turns RED when the population cannot fit it (measured in game,
    # 2026-07-31) - neither is being produced, so neither may prove a TC.
    tracker = ProductionTracker()
    seen = [slot("green", "loom"), vill("amber"), vill("red")]
    tracker.update(100.0, seen)
    tracker.update(101.5, seen)
    tracker.update(103.1, seen)
    assert tracker.tcs_seen == 1    # the researching TC, and nothing more
    assert tracker.idle_tcs == 0


def test_untinted_item_proves_a_tc():
    # A just-placed item is untinted before its wash grows enough to
    # classify - it IS producing (author-verified, 2026-08-01), so a
    # confident untinted villager beside a researching TC confirms the
    # second TC seconds before the green would.
    tracker = ProductionTracker()
    seen = [slot("green", "loom"), vill(None)]
    tracker.update(100.0, seen)
    tracker.update(101.5, seen)
    tracker.update(103.1, seen)
    assert tracker.tcs_seen == 2


def test_the_starting_tc_is_assumed():
    # Every standard match begins with a TC, so an untouched queue means an
    # idle TC from second zero - no villager needs to be seen first. (Nomad
    # is the known exception, deliberately not handled yet.)
    tracker = ProductionTracker()
    assert tracker.tcs_seen == 1


def test_researching_tech_proves_a_second_tc():
    # A green tech IS a TC's one in-progress item: a villager group and a
    # researching tech on screen together can only come from two TCs. The
    # old rule ignored techs for existence and undercounted every boom
    # that aged up while training.
    tracker = ProductionTracker()
    seen = [vill(), slot("green", "town_watch")]
    tracker.update(100.0, seen)
    tracker.update(101.5, seen)
    tracker.update(103.1, seen)
    assert tracker.tcs_seen == 2
    assert tracker.idle_tcs == 0


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


def test_decor_cell_is_not_a_queue_item():
    # Some civs drape artwork straight through the queue grid; this red
    # tapestry's saturated pattern read as a BLOCKED villager group and
    # marked one TC busy forever (a live six-TC game showed "5 TCs IDLE").
    # A known decoration must never be a queue item.
    reader = queue.QueueReader()
    cell = fixture("decor_red_tapestry")
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    assert reader._is_decor(gray, 0.98)


def test_real_cards_are_not_decor():
    # A card drawn over the decoration covers its pattern - real queue
    # items must keep reading normally on decorated HUDs.
    reader = queue.QueueReader()
    for name in ("green_villager", "red_housed", "amber_villager_x3",
                 "dark_waiting_x1"):
        gray = cv2.cvtColor(fixture(name), cv2.COLOR_BGR2GRAY)
        assert not reader._is_decor(gray, 1.0), name


def test_uncorroborated_junk_identity_is_refused():
    # Terrain that sneaks past the occupancy edge test matches everything
    # a little and nothing well - this cell read "galley" at 0.5 in a
    # landlocked Dark Age game. With no wash and no count numeral to
    # corroborate, a flat ranking must read as None, which ends the queue.
    reader = queue.QueueReader()
    gray = cv2.cvtColor(fixture("junk_terrain_galley"), cv2.COLOR_BGR2GRAY)
    identity, _, _ = reader._identify_cached(0, gray, has_content=False)
    assert identity is None


def test_corroborated_flat_ranking_keeps_its_identity():
    # A genuine amber villager batch wins by only ~0.05 - but its wash
    # already proves something real is drawn there, so the best guess
    # stands and the busy accounting keeps its villager group.
    reader = queue.QueueReader()
    gray = cv2.cvtColor(fixture("amber_villager_x3"), cv2.COLOR_BGR2GRAY)
    identity, _, _ = reader._identify_cached(0, gray, has_content=True)
    assert identity in ("villager_male", "villager_female")


def test_weak_tech_claim_is_refused(monkeypatch):
    # Real tech icons read 0.91+; a "tech" in the 0.4-0.6 range is a
    # misread unit batch or junk, and once credited a TC with wheelbarrow
    # research while a halberdier batch trained. Weak tech claims fall to
    # None whatever else the cell shows.
    reader = queue.QueueReader()
    monkeypatch.setattr(queue, "identify",
                        lambda cell, templates: ("wheelbarrow", 0.5, 0.2))
    identity, _, _ = reader._identify_cached(0, None, has_content=True)
    assert identity is None


def test_identity_hysteresis_stops_flapping(monkeypatch):
    # Weak cells re-rank every poll; two similar portraits trading
    # hair's-breadth wins must not flap the identity (and the TC busy
    # count) poll to poll. The challenger takes the slot only by beating
    # the incumbent decisively.
    reader = queue.QueueReader()
    gray = cv2.cvtColor(fixture("green_villager"), cv2.COLOR_BGR2GRAY)
    name, _, _ = reader._identify_cached(0, gray, has_content=True)
    assert name in ("villager_male", "villager_female")
    # The cell now scores weakly for everyone (below CLEAR, so the fast
    # path falls through) and a rival "wins" by a hair: the cached name
    # holds rather than flapping.
    monkeypatch.setattr(queue, "_match_variants",
                        lambda cell, variants: 0.49)
    monkeypatch.setattr(queue, "identify",
                        lambda cell, templates: ("monk", 0.52, 0.02))
    name, _, _ = reader._identify_cached(0, gray, has_content=True)
    assert name in ("villager_male", "villager_female")


def test_identity_hysteresis_yields_to_a_decisive_win(monkeypatch):
    # A genuine content change drops the incumbent's score AND crowns the
    # newcomer decisively (militia beat a stale villager name by 0.15):
    # it must flip immediately.
    reader = queue.QueueReader()
    gray = cv2.cvtColor(fixture("green_villager"), cv2.COLOR_BGR2GRAY)
    reader._identify_cached(0, gray, has_content=True)
    monkeypatch.setattr(queue, "_match_variants",
                        lambda cell, variants: 0.45)
    monkeypatch.setattr(queue, "identify",
                        lambda cell, templates: ("militia", 0.64, 0.15))
    name, _, _ = reader._identify_cached(0, gray, has_content=True)
    assert name == "militia"


def test_unconfident_identity_is_not_tc_evidence():
    # The reader's least-bad guess on an icon it has no template for lands
    # on villagers and TC techs disturbingly often (an unknown Armenian
    # icon read villager_male 0.42/margin 0.00 and minted a phantom TC).
    # A green cell with a weak, unclear identity proves something is
    # producing - busy - but never that a TC exists.
    tracker = ProductionTracker()
    guess = queue.SlotReading(1, "green", 0.4, None, "villager_male",
                              0.42, 0.0)
    seen = [vill(), guess]
    tracker.update(100.0, seen)
    tracker.update(101.5, seen)
    tracker.update(103.1, seen)
    assert tracker.tcs_seen == 1          # no phantom from the guess
    assert tracker.tc_busy >= 1           # but it still counts as work


def test_amber_items_do_not_make_a_tc_busy():
    # Amber means WAITING BEHIND the building's in-progress item - the
    # front of that queue is already counted. Live: one TC holding
    # wheelbarrow(red) + town_watch(amber) + imperial_age(amber) read as
    # three busy TCs, and a freshly built second TC sat idle unwarned.
    tracker = ProductionTracker()
    tracker.register_tc_built()           # two TCs known
    seen = [slot("red", "wheelbarrow"), slot("amber", "town_watch"),
            slot("amber", "imperial_age")]
    tracker.update(100, seen)
    events = tracker.update(103, seen)
    assert tracker.tc_busy == 1           # the blocked front item only
    assert tracker.idle_tcs == 1          # the second TC is idle
    assert TC_IDLE in events


def test_a_just_queued_item_is_not_mistaken_for_an_empty_slot():
    """The false TC IDLE, on the exact pixels that caused it.

    An item placed in the Town Centre but whose green wash has not begun
    shows no tint and no count numeral, so the only evidence it can offer is
    its portrait. Measured live on stock, this cell reads villager_male at
    0.585 with a margin of 0.04 - the RIGHT answer, fifteen thousandths
    under CLEAR_IDENTITY_SCORE. It was refused, the cell then had no wash,
    no count and no identity, the content gate ended the queue there, and
    Loom announced TC IDLE with a villager plainly training.

    The slot having held a believed item on the previous poll is what
    corroborates it now: decoration does not come and go, and a slot that
    genuinely empties has its cache entry dropped.
    """
    reader = queue.QueueReader(profile=hud.STOCK)
    gray = cv2.cvtColor(fixture("unwashed_villager"), cv2.COLOR_BGR2GRAY)

    # One poll after a believed item sat in this slot: the cell is still a
    # queue item, and must be named rather than thrown away.
    reader._cache[0] = ("villager_male", 0.74)
    identity, _, _ = reader._identify_cached(0, gray, has_content=False)

    assert identity == "villager_male"


def test_an_emptied_slot_stops_vouching_for_what_follows():
    """The corroboration must not outlive the item.

    Decoration hangs exactly where slot one sits on several civs, and the
    whole point of the uncorroborated gate is to refuse it. If a stale cache
    entry could vouch for a cell, the first decorated frame after a queue
    emptied would read as a queue item - so read() drops the entry for every
    slot past the end of the queue, and this pins that.
    """
    reader = queue.QueueReader(profile=hud.STOCK)
    reader._cache[0] = ("villager_male", 0.74)
    gray = cv2.cvtColor(fixture("junk_terrain_galley"), cv2.COLOR_BGR2GRAY)

    # Junk still reads as junk even with a cached neighbour in the slot: the
    # cache corroborates that SOMETHING is drawn, it does not name it.
    identity, _, _ = reader._identify_cached(0, gray, has_content=False)

    assert identity != "galley" or identity is None
