"""
Loom — which of a step's items are done, and how confidently.

The failures this guards are all silent and all of the same family: a guess
presented as a reading. Nothing here builds a widget or checks that a bullet
is drawn - that is visible the instant the window opens.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import checklist
from loom.build_order import BuildOrder


def build(*step_notes):
    """A build order with one step per notes string, 3 villagers apart."""
    return BuildOrder({"name": "test", "build_order": [
        {"villager_count": 3 + 3 * index, "time": f"0:{10 + index:02d}",
         "notes": [notes]}
        for index, notes in enumerate(step_notes)]})


MILL = "@mill/Mill_aoe2de.webp@"
HOUSE = "@other/House_aoe2DE.webp@"
VILL = "@resource/MaleVillDE.webp@"
WOOD = "@resource/Aoe2de_wood.webp@"
BOAR = "@animal/Boar_aoe2DE.webp@"
TOWN_CENTRE = "@town_center/Towncenter_aoe2DE.webp@"
LUMBER = "@lumber_camp/Lumber_camp_aoe2de.webp@"
LOOM = "@town_center/LoomDE.webp@"
HEAVY_PLOW = "@mill/HeavyPlowDE.webp@"
DOUBLE_BIT = "@lumber_camp/DoubleBitAxe_aoe2DE.webp@"
HORSE_COLLAR = "@mill/HorseCollarDE.webp@"


def test_an_icon_token_names_what_would_prove_the_item_done():
    """The whole reason this needs no English parsing: the build file
    already says which entity each instruction is about, machine-readably,
    and it lines up with what the notification reader emits."""
    order = build(f"Build a {MILL}")
    assert checklist.item_evidence(order.steps[0].items_segments[0]) \
        == (("mill", 1),)
    assert checklist.event_subject("built:mill") == "mill"


def test_villagers_resources_and_animals_prove_nothing():
    """Assume-only by design. "--Villager Created--" fires all game and says
    nothing about whether a re-tasking instruction was carried out, and a
    boar is FOUND when it is seen, which is not when it is lured."""
    order = build(f"Next 3 {VILL} to {WOOD}", f"Lure the {BOAR}")
    for step in order.steps:
        assert checklist.item_evidence(step.items_segments[0]) == ()


def test_naming_a_building_is_not_asking_for_one_to_be_built():
    """The worst error this module can make, and it made it.

    "Eat it under the Town Center" names a Town Centre because that is
    WHERE to eat the boar - you cannot build one in the Dark Age at all -
    and it ticked green off a "--Town Center Built--" line. A confident
    claim that the player did something they were never asked to do.

    So the default is inverted: an item is assume-only unless the words ask
    for the thing to be MADE. Failing to spot a phrasing costs a faded tick
    where a green was possible; the opposite costs a lie.
    """
    for words in ("Eat it under the " + TOWN_CENTRE,
                  "Move 5 Villager from " + TOWN_CENTRE + " to Wood",
                  "Next 2 Villager to Wood (still only 1 " + LUMBER + ")"):
        order = build(words)
        assert checklist.item_evidence(order.steps[0].items_segments[0]) == (),             f"{words!r} would tick green off an unrelated line"


def test_the_words_players_actually_use_for_building_are_recognised():
    """Community builds are written by people, so this is what they wrote -
    every one of these is a real line from a shipped build."""
    for words in ("Build 1 " + HOUSE,
                  "Hammer 4 " + HOUSE,
                  "Add " + TOWN_CENTRE + " and keep making Villager",
                  "Research " + LOOM,
                  "Get " + HEAVY_PLOW + " later"):
        order = build(words)
        assert checklist.item_evidence(order.steps[0].items_segments[0]),             f"{words!r} should be watchable"


def test_a_bare_technology_name_is_an_instruction_to_research_it():
    """"Double Bit Axe & Horse Collar" has no verb to find, because the
    whole item is the list of things to research."""
    order = build(DOUBLE_BIT + " & " + HORSE_COLLAR)
    assert checklist.item_evidence(order.steps[0].items_segments[0]) == (
        ("double_bit_axe", 1), ("horse_collar", 1))


def test_an_item_naming_two_things_needs_both_before_it_counts():
    """"Build 2 House, then Mill at Berries" is not done when the House is.

    Fails in the safe direction on purpose: an unfinished item stays
    unticked, where crediting it on the first half would report a job half
    done as complete.
    """
    order = build(f"Build {HOUSE}, then {MILL}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) is checklist.NOT_DONE

    sheet.observe(-1, ["built:mill"])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_a_tick_can_arrive_before_its_step_and_still_counts():
    """Players adapt, misclick and build things early. A Mill built two
    steps ahead of the build asking for one is still a Mill."""
    order = build("Do nothing", "Do nothing either", f"Build a {MILL}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["built:mill"])
    assert sheet.state(2, 0) == checklist.OBSERVED


def test_two_of_the_same_thing_take_two_sightings():
    """One event credits ONE item, so a build asking for two Houses in
    different steps needs two lines, in order."""
    order = build(f"Build {HOUSE}", f"Build another {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) == checklist.OBSERVED
    assert sheet.state(1, 0) is checklist.NOT_DONE

    sheet.observe(-1, ["built:house"])
    assert sheet.state(1, 0) == checklist.OBSERVED


def test_passing_a_step_assumes_only_what_could_never_be_seen():
    """The four-state distinction, and the reason there are four.

    Once a step is behind you, an item Loom was WATCHING for and never saw
    is news; an item it could never have seen either way is not. Collapsing
    them into one "assumed" would either cry wolf about every villager
    instruction or say nothing about a Mill that never appeared.
    """
    # One step Loom can check, one it never could - plus a trailing
    # step, because the FINAL card is never graded as passed at all.
    order = build(f"Build a {MILL}", f"Next 3 {VILL} to {WOOD}",
                  "Keep producing")
    sheet = checklist.Checklist(order)

    sheet.observe(-1)
    assert sheet.states(0, 1) == [checklist.NOT_DONE]

    sheet.observe(0)                       # step 0 is now behind us
    assert sheet.state(0, 0) == checklist.UNCONFIRMED
    sheet.observe(1)                       # ...and so is step 1
    assert sheet.state(1, 0) == checklist.ASSUMED


def test_a_sighting_outranks_an_assumption_whichever_arrives_first():
    """A fact does not stop being a fact because the build moved on."""
    order = build(f"Build a {MILL}", "Keep producing")
    sheet = checklist.Checklist(order)

    sheet.observe(0)                                   # passed, unconfirmed
    assert sheet.state(0, 0) == checklist.UNCONFIRMED
    sheet.observe(0, ["built:mill"])                   # ...then seen
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_assumptions_follow_the_step_index_backwards():
    """Assumed is a VIEW, not a stored fact.

    That is what makes a new match un-assume itself with nothing to reset,
    and what stops a hotkey scrolled backwards leaving guesses behind it.
    """
    order = build(f"Build a {MILL}", f"Build {HOUSE}",
                  "Keep producing")
    sheet = checklist.Checklist(order)

    sheet.observe(1)
    assert sheet.state(1, 0) == checklist.UNCONFIRMED
    sheet.observe(-1)
    assert sheet.state(1, 0) is checklist.NOT_DONE


def test_only_sightings_travel_between_the_windows():
    """The statefeed contract. Assumptions are derived at both ends from the
    step index; a sighting is a fact only the reader can have, so it is the
    only part that has to be sent - and a reset upstream has to arrive."""
    order = build(f"Build a {MILL}", f"Build {HOUSE}")
    sending = checklist.Checklist(order)
    sending.observe(1, ["built:mill"])

    receiving = checklist.Checklist(order)
    receiving.observe(1)
    receiving.apply_observed(sending.observed_ticks())
    assert receiving.state(0, 0) == checklist.OBSERVED

    sending.reset()
    receiving.apply_observed(sending.observed_ticks())
    assert receiving.state(0, 0) == checklist.UNCONFIRMED


def test_events_about_nothing_tick_nothing():
    """"attacked" and the unclassified line:... catch-all are about no
    particular entity and must not be credited to whatever comes first."""
    order = build(f"Build a {MILL}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["attacked", "line:something_odd", "created:villager"])
    assert sheet.state(0, 0) is checklist.NOT_DONE


def test_an_item_asking_for_two_is_not_done_after_one():
    """The author watched "Build 2 House" go green on the FIRST
    "--House Built--" - a confident claim that two houses stood when one
    did. The count is the build file's own number, so nothing is inferred:
    the number the author wrote is the number of sightings the item costs.
    """
    order = build(f"Build 2 {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) is checklist.NOT_DONE

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_a_sighting_never_pays_for_two_instructions():
    """The author's second bug from the same game: one "--House Built--"
    also crossed off a build-house item further down the build. Each
    sighting credits exactly one instruction, in build order, and a later
    item only starts collecting once the earlier one is full."""
    order = build(f"Build 2 {HOUSE}", f"Build 1 {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) is checklist.NOT_DONE
    assert sheet.state(1, 0) is checklist.NOT_DONE

    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) == checklist.OBSERVED
    assert sheet.state(1, 0) is checklist.NOT_DONE

    sheet.observe(-1, ["built:house"])
    assert sheet.state(1, 0) == checklist.OBSERVED


def test_an_ordinal_is_which_not_how_many():
    """"Build 2nd Lumber Camp" is ONE camp - the 2nd names which camp it
    is. Reading it as two would make the item unfinishable: the second
    sighting belongs to a different instruction."""
    order = build(f"Next 3 Villager to Wood (build 2nd {LUMBER})")
    assert checklist.item_evidence(order.steps[0].items_segments[0]) == \
        (("lumber_camp", 1),)


# --- the population cap as a second witness for houses ---------------------
#
# The feed structurally misses houses built close together: the game never
# reprints a line that is still on screen, so two houses in one line's
# lifetime print one line. Before Castle Age the cap is an exact witness -
# +5 and nothing else can raise it - and the two channels reconcile by MAX,
# the same rule production.py uses for Town Centres, because one house
# produces both signals.


def test_the_cap_finds_the_house_the_feed_never_printed():
    """The author's drush game, as arithmetic: cap 5->10 with a feed line,
    then 10->15 in silence. Two houses, credited exactly twice."""
    h = checklist.HouseEvidence()
    assert h.update(5, 1, 0) == 0            # baseline
    assert h.update(10, 1, 1) == 1           # house 1: both witnesses
    assert h.update(15, 1, 0) == 1           # house 2: cap alone
    assert h.update(15, 1, 0) == 0


def test_one_house_seen_by_both_witnesses_is_one_house():
    """MAX, never added - the same house produces both signals."""
    h = checklist.HouseEvidence()
    h.update(5, 1, 0)
    assert h.update(10, 1, 0) == 1           # cap first
    assert h.update(10, 1, 1) == 0           # feed catches up: no new house


def test_the_cap_says_nothing_after_castle_age():
    """A +5 from Castle Age on could be a Town Centre. Only the feed
    counts from there."""
    h = checklist.HouseEvidence()
    h.update(100, 3, 0)
    assert h.update(105, 3, 0) == 0          # could be a TC: not a house
    assert h.update(105, 3, 1) == 1          # the feed still works


def test_an_unread_cap_is_no_news():
    h = checklist.HouseEvidence()
    h.update(5, 1, 0)
    assert h.update(None, 1, 0) == 0
    assert h.update(10, 1, 0) == 1           # the step still lands after


def test_a_razed_house_rebuilt_counts_again():
    """The cap falls when a house burns; the next one built is a new
    house, not a repeat."""
    h = checklist.HouseEvidence()
    h.update(5, 1, 0)
    assert h.update(15, 1, 0) == 2
    assert h.update(10, 1, 0) == 0           # razed: nothing credited
    assert h.update(15, 1, 0) == 1           # rebuilt: a new house


def test_an_unknown_age_keeps_the_cap_quiet():
    """Before the crest has been read, a cap step proves nothing - Loom
    might be joining a Castle Age game mid-match."""
    h = checklist.HouseEvidence()
    h.update(5, None, 0)
    assert h.update(10, None, 0) == 0


def test_one_house_seen_by_feed_and_cap_credits_the_checklist_once():
    """The author's 42-second test game, verbatim. He built ONE house; the
    feed printed its line at 19s and the cap stepped at 20s. Handing the
    checklist the feed event AND the reconciled credit made two, and
    "Build 2 House" ticked green on a single house. One witness pair, one
    credit, and the item stays honestly unticked."""
    order = build(f"Build 2 {HOUSE}")
    sheet = checklist.Checklist(order)
    houses = checklist.HouseEvidence()

    script = [
        (12, 5, []),                     # baseline
        (19, 5, ["built:house"]),        # the feed line lands first
        (20, 10, []),                    # the cap catches up
        (26, 10, []),
    ]
    for _moment, cap, feed in script:
        feed_houses = sum(1 for e in feed if e == "built:house")
        new = houses.update(cap, 1, feed_houses)
        sheet.observe(-1, checklist.merged_house_events(feed, new))

    assert sheet.state(0, 0) is checklist.NOT_DONE, \
        "one house ticked an item that asks for two"

    # ...and the second house, seen only by the cap, completes it.
    new = houses.update(15, 1, 0)
    sheet.observe(-1, checklist.merged_house_events([], new))
    assert sheet.state(0, 0) == checklist.OBSERVED


# --- commodity items credit only near the current step ---------------------
#
# Community builds are pacing guides, not scripts: Arena Fast Conqs asks
# for ONE house before Castle Age and a real game needs six, so a global
# ledger over houses lies almost at once - the game's routine supply house
# would tick a card the player has not reached. Houses credit only inside
# a window around the current step; unique things (techs, mills, camps,
# production buildings, of which one to three ever exist) stay global.


def test_a_routine_house_does_not_tick_a_distant_card():
    """The Arena problem, as arithmetic: a house built at step 0 for supply
    must not credit a "Build 1 House" card five steps away."""
    order = build("March", "March on", "Keep marching", "And more",
                  "Still going", f"Build 1 {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, ["built:house"])          # active step is 1
    assert sheet.state(5, 0) is checklist.NOT_DONE

    # Reached: the house built DURING that card's window ticks it.
    sheet.observe(4, ["built:house"])
    assert sheet.state(5, 0) == checklist.OBSERVED


def test_the_window_covers_working_one_card_ahead():
    """Players do the next card's business early; the window forgives one
    card of that, plus the just-completed card as grace."""
    order = build(f"Build 1 {HOUSE}", f"Build 1 {HOUSE}",
                  f"Build 1 {HOUSE}")
    sheet = checklist.Checklist(order)

    # current_index -1: window is steps -1..1 - the opening two cards.
    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) == checklist.OBSERVED
    sheet.observe(-1, ["built:house"])
    assert sheet.state(1, 0) == checklist.OBSERVED
    # Step 2 is outside the opening window; nothing has reached it.
    sheet.observe(-1, ["built:house"])
    assert sheet.state(2, 0) is checklist.NOT_DONE


def test_unique_things_still_credit_from_anywhere():
    """The window is for commodities only. A Mill built five steps early is
    still THE Mill - nobody builds spare mills - and early work ticking a
    later card is the behaviour the author asked to keep."""
    order = build("March", "March on", "Keep marching", "And more",
                  "Still going", f"Build a {MILL}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, ["built:mill"])
    assert sheet.state(5, 0) == checklist.OBSERVED


# --- age completions come from the crest, not the feed ----------------------


AGE_TOKEN = "@age/FeudalAgeIconDE.webp@"


def test_the_crest_ticks_the_age_item_the_feed_never_printed():
    """The author watched this live: "queued" at the click, the age
    completed, and the Feudal Age item sat amber all game. The feed's
    "...Research Complete" line wraps and often never reads; the crest read
    every transition in every capture. Its REACHED is the verdict."""
    order = build(f"19 pop {AGE_TOKEN}")
    sheet = checklist.Checklist(order)

    events = checklist.merged_age_events([], [2])
    sheet.observe(-1, events)
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_an_age_arriving_makes_every_mention_of_it_true():
    """Ages are once-per-game facts, so "Click Feudal Age", "Before
    Feudal Age" and "In Feudal Age" are three views of one moment, not
    three deliverables. First-item-only crediting blocked a live game
    twice: the click item consumed the single age event, and "In Feudal
    Age: build Market with 2 Villager, Blacksmith with 1" could never
    complete even with the market and blacksmith both read."""
    order = build(f"19 pop {AGE_TOKEN}", f"Before {AGE_TOKEN}")
    sheet = checklist.Checklist(order)

    events = checklist.merged_age_events(["researched:feudal_age"], [2])
    assert events.count("researched:feudal_age") == 1
    sheet.observe(-1, events)
    assert sheet.state(0, 0) == checklist.OBSERVED
    assert sheet.state(1, 0) == checklist.OBSERVED


def test_an_age_header_with_other_subjects_still_needs_them():
    """The age fills its slot in "In Feudal Age: build Market..." but the
    market is still the player's to build - the header item goes green
    only when the rest of it is done too."""
    order = build(f"In {AGE_TOKEN}: build a {MILL}")
    sheet = checklist.Checklist(order)

    sheet.observe(-1, checklist.merged_age_events([], [2]))
    assert sheet.state(0, 0) is checklist.NOT_DONE
    sheet.observe(-1, ["built:mill"])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_other_events_pass_through_the_age_merge_untouched():
    events = checklist.merged_age_events(
        ["built:mill", "researched:loom", "researched:feudal_age"], [])
    assert events == ["built:mill", "researched:loom"]



def test_the_final_card_is_never_graded_as_passed():
    """The build "completes" by count, clock and age, but nothing proves
    the last card's work - and the panel now RESTS on it while the player
    does it. Its watchable items stay open (not "missed"), its
    unobservable ones stay open (not struck-as-done), and an actual
    sighting still ticks green."""
    order = build(f"Build a {MILL}", f"Build {HOUSE}",
                  "Build a @castle/Castle_aoe2DE.webp@ | Train your "
                  "unique unit")
    sheet = checklist.Checklist(order)
    last = len(order.steps) - 1
    sheet.observe(last)              # cursor says the whole build is done
    assert sheet.state(last, 0) is checklist.NOT_DONE   # castle: not missed
    assert sheet.state(last, 1) is checklist.NOT_DONE   # not struck as done
    # The step before it grades as passed exactly as before...
    assert sheet.state(last - 1, 0) == checklist.UNCONFIRMED
    # ...and an actual sighting still ticks the resting card green.
    sheet.observe(last, ["built:castle"])
    assert sheet.state(last, 0) == checklist.OBSERVED


FEUDAL_ICON = "@age/FeudalAgeIconDE.webp@"
CASTLE_ICON = "@age/CastleAgeIconDE.webp@"


def test_only_verifiable_items_before_the_ask_gate_the_click():
    """The author's rule. Items before the card's age-up ask must be
    OBSERVED before CLICK UP may urge; the ask itself and anything after
    it never gate - and "no verdict" stays distinct from "not done", so
    a card without an ask silences nothing."""
    order = build(f"Build a {MILL} | Build {HOUSE} | Click {FEUDAL_ICON}"
                  f" | Build {LUMBER}",
                  "Keep producing")
    sheet = checklist.Checklist(order)
    assert sheet.click_prerequisites_settled(0) is False
    assert sheet.click_prerequisites_settled(1) is None    # no ask there
    sheet.observe(-1, ["built:mill"])
    assert sheet.click_prerequisites_settled(0) is False   # house missing
    sheet.observe(-1, ["built:house"])
    # Both pre-ask items seen: settled, though the post-ask lumber camp
    # was never observed - after the ask is not a prerequisite.
    assert sheet.click_prerequisites_settled(0) is True


def test_a_heading_naming_the_current_age_is_not_the_ask():
    """Card 8's shape: "In Feudal Age: build Market..." wants feudal_age
    too, but the heading is where the card IS, not where it is going.
    The ask is the item wanting an age ABOVE the card's own - here the
    castle click - and the market line before it is the gate."""
    order = BuildOrder({"name": "t", "build_order": [
        {"villager_count": 3, "age": 1, "time": "0:10",
         "notes": ["Open normally"]},
        {"villager_count": 6, "age": 2, "time": "5:00",
         "notes": [f"In {FEUDAL_ICON}: build {MILL}"
                   f" | Sell wood, click {CASTLE_ICON}"]},
    ]})
    sheet = checklist.Checklist(order)
    assert sheet.click_prerequisites_settled(1) is False   # mill unbuilt
    # Feudal arrives (the heading's own want fills), then the mill: the
    # pre-ask item completes and the gate lifts, castle still unclicked.
    sheet.observe(0, ["researched:feudal_age"])
    assert sheet.click_prerequisites_settled(1) is False   # mill still
    sheet.observe(0, ["built:mill"])
    assert sheet.click_prerequisites_settled(1) is True


def test_word_boundaries_do_not_split_a_subject_from_its_event():
    """Regression, found by auditing the shipped builds: the item's subject
    comes from an icon FILENAME and the event's from the game's own line,
    and "Scoutcavalry_aoe2DE.webp" vs "--Scout Cavalry Created--" made an
    item no event could ever credit. The comparison ignores boundaries -
    both sides spell the same words in the same order."""
    order = BuildOrder({"name": "t", "build_order": [
        {"villager_count": 3, "age": 2, "time": "0:10",
         "notes": ["Train @stable/Scoutcavalry_aoe2DE.webp@"]},
    ]})
    sheet = checklist.Checklist(order)
    sheet.observe(-1, ["created:scout_cavalry"])
    assert sheet._state.get((0, 0)) == checklist.OBSERVED


# --- things the feed can never report, and things it should not be asked to -

def test_a_farm_is_never_watched():
    """The game announces twenty-two kinds of building and a farm is not
    one of them: 900 labelled corpus lines from eight games hold houses,
    mills, markets and camps, and zero farms. It says "--Farm
    Exhausted--" when one runs out, which is a different event entirely.

    Watching for a line that is never printed makes the item unfinishable
    - and takes anything sharing that item down with it, which is the
    wall bug in a new place. Eight items across the shipped builds were
    waiting on it."""
    order = BuildOrder({"name": "t", "build_order": [
        {"villager_count": 3, "age": 1, "time": "0:10",
         "notes": [f"Next seeds @other/FarmDE.webp@ and builds {HOUSE}"]},
    ]})
    sheet = checklist.Checklist(order)
    wanted = dict(checklist.item_evidence(order.steps[0].items_segments[0]))
    assert "farm" not in wanted
    assert wanted == {"house": 1}, "the house must still be watchable"

    # And the item completes on the house alone, rather than sitting amber
    # for the rest of the game waiting on the farm.
    sheet.observe(-1, ["built:house"])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_a_tool_named_in_a_step_is_not_a_thing_to_deliver():
    """"Use Market to build 2nd Town Center" asks for a Town Centre. The
    Market is where the stone gets sold and was built eight minutes
    earlier - its one event already spent on the step that asked for it -
    so requiring a second market line left the item unfinishable, and the
    real second Town Centre could not tick it."""
    order = BuildOrder({"name": "t", "build_order": [
        {"villager_count": 3, "age": 3, "time": "9:00",
         "notes": ["Use @other/Market_aoe2DE.webp@ to build 2nd "
                   "@other/Towncenter_aoe2DE.webp@ when possible"]},
    ]})
    wanted = dict(checklist.item_evidence(order.steps[0].items_segments[0]))
    assert wanted == {"town_center": 1}

    sheet = checklist.Checklist(order)
    sheet.observe(-1, ["town_center_built"])
    assert sheet.state(0, 0) == checklist.OBSERVED


# ---- buildings that do another building's job ---------------------------
#
# Some civilisations do not build what a build order names. An Inca player
# has no Lumber Camp, Mining Camp or Mill at all - a Settlement is all
# three, and supports population besides - so every one of those items in
# every shipped build was unfinishable, falling back to ASSUMED and drawing
# a faded hollow bullet where the player had done the work and Loom had read
# the line saying so.

SETTLEMENT = "built:settlement"
MINING = "@mining_camp/Mining_camp_aoe2de.webp@"


def test_a_settlement_is_a_lumber_camp():
    order = build(f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_a_settlement_is_a_mill_and_a_mining_camp_too():
    for token in (MILL, MINING):
        order = build(f"Build a {token}")
        sheet = checklist.Checklist(order)
        sheet.observe(0, [SETTLEMENT])
        assert sheet.state(0, 0) == checklist.OBSERVED, token


def test_a_settlement_is_OBSERVED_and_not_assumed():
    """The game announced it and Loom read it. That is a sighting, and
    dressing a sighting as a guess would be the never-guess rule broken
    from the harmless side - but broken all the same."""
    order = build(f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(0, 0) is not checklist.ASSUMED
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_one_settlement_credits_a_dropsite_AND_a_house():
    """The author's ruling, and the reason this is not just a spelling
    table. A Settlement really did give the player both a dropsite and five
    population, so both instructions are genuinely served."""
    order = build(f"Build a {LUMBER}", f"Build a {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(0, 0) == checklist.OBSERVED
    assert sheet.state(1, 0) == checklist.OBSERVED


def test_one_settlement_never_credits_two_houses():
    """What keeps the ruling above from weakening the House protection.

    The existing rule forbids one sighting paying twice for the SAME
    instruction - "Build 2 House" costs two lines. A Settlement crediting a
    lumber camp and a house is not that, because the credits land on
    different subjects. Two houses off one Settlement would be.
    """
    order = build(f"Build a {HOUSE}", f"Build a {HOUSE}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(0, 0) == checklist.OBSERVED
    assert sheet.state(1, 0) is checklist.NOT_DONE

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(1, 0) == checklist.OBSERVED


def test_a_distant_settlement_credits_nothing():
    """Nearest item, not first waiting - the author's ruling, and the same
    care a house gets. A Settlement can satisfy four different subjects, so
    a distant match is the MOST dangerous kind there is."""
    order = build("Do nothing", "Do nothing either", "Nor this",
                  "Still nothing", f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)

    sheet.observe(0, [SETTLEMENT])
    assert sheet.state(4, 0) is checklist.NOT_DONE


def test_a_mule_cart_is_a_lumber_camp_but_never_a_mill():
    """Armenians and Georgians still build Mills and Farms - the Mule Cart
    replaces the Lumber Camp and Mining Camp only. This asymmetry against
    the Settlement is the whole reason these are three rows."""
    order = build(f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:mule_cart"])
    assert sheet.state(0, 0) == checklist.OBSERVED

    order = build(f"Build a {MILL}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:mule_cart"])
    assert sheet.state(0, 0) is checklist.NOT_DONE


def test_a_mule_cart_is_not_a_house():
    order = build(f"Build a {HOUSE}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:mule_cart"])
    assert sheet.state(0, 0) is checklist.NOT_DONE


def test_a_folwark_is_a_mill_and_nothing_else():
    order = build(f"Build a {MILL}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:folwark"])
    assert sheet.state(0, 0) == checklist.OBSERVED

    order = build(f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:folwark"])
    assert sheet.state(0, 0) is checklist.NOT_DONE


def test_the_ordinary_building_still_credits_itself():
    """The seam was added beside the old behaviour, not through it."""
    order = build(f"Build a {LUMBER}")
    sheet = checklist.Checklist(order)
    sheet.observe(0, ["built:lumber_camp"])
    assert sheet.state(0, 0) == checklist.OBSERVED


def test_every_stand_in_is_a_line_the_reader_can_actually_produce():
    """The audit a hand-written table has to have.

    CLAUDE.md's rule: a hand-curated allowlist fails SILENTLY. A key
    misspelled `settlment` would sit here forever matching nothing, and no
    test, gate or log anywhere would say so - the player would just see a
    hollow bullet and assume Loom had missed the line.

    So this checks the CATEGORY rather than the entries: every key must be
    a subject the notification reader really emits for a line the game
    really prints.
    """
    from loom import glyphs
    for stand_in in checklist.STANDS_IN_FOR:
        printed = "--%s Built--" % stand_in.replace("_", " ").title()
        assert glyphs.parse_event(printed) == "built:" + stand_in, printed


def test_every_word_of_every_stand_in_is_a_known_word():
    """KNOWN_WORDS is the gate the line passes before any of this runs.

    A missing word refuses the whole line as a misread, so the substitution
    below it would never be reached - the failure would look like a reader
    fault rather than a vocabulary one, which is exactly how "camp" cost 23
    items across the shipped builds.
    """
    from loom import glyphs
    for stand_in in checklist.STANDS_IN_FOR:
        for word in stand_in.split("_"):
            assert word in glyphs.KNOWN_WORDS, word


def test_every_substituted_subject_is_one_a_build_actually_asks_for():
    """A row nobody's build can reach is dead weight, and dead weight in a
    hand-written table is indistinguishable from a typo until someone
    plays that civilisation and finds nothing ticks."""
    import glob
    from loom import paths
    wanted = set()
    for path in glob.glob(str(paths.PROJECT_ROOT / "builds" / "*.json")):
        with open(path, encoding="utf-8") as handle:
            text = handle.read().lower()
        for subject in ("lumber_camp", "mining_camp", "mill", "house"):
            if subject in text or subject.replace("_", "") in text:
                wanted.add(subject)
    for stand_in, stands_for in checklist.STANDS_IN_FOR.items():
        unreachable = stands_for - wanted
        assert not unreachable, (stand_in, unreachable)


def test_an_upgrade_ticks_a_step_named_for_the_unit():
    """One reading, two questions, two answers.

    The queue reader had to hold "crossbowman the research" and "crossbowman
    the unit" apart - they are two different pictures, correlating at 0.045,
    and calling one by the other's name is a confident wrong reading. A build
    order asking for Crossbowman is not asking that question: it wants to
    know whether the step happened, and the research happening is what it
    meant.

    The direction is the whole content of the rule. A build step named for
    the UPGRADE must not be ticked by merely training the unit, because
    training it proves the research already happened only if you assume the
    thing being tested.
    """
    step = {"crossbowman": None, "archer": None}
    assert checklist._same_subject("crossbowman_upgrade", step) == "crossbowman"
    assert checklist._same_subject("crossbowman", step) == "crossbowman"
    upgrade_step = {"crossbowman_upgrade": None}
    assert checklist._same_subject("crossbowman", upgrade_step) is None
    # An unrelated upgrade still matches nothing.
    assert checklist._same_subject("paladin_upgrade", step) is None
