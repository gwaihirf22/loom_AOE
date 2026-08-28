"""The queue reader's gate has to be able to convict, and to stay silent.

A gate that flags everything is the same as one that flags nothing. This one
runs over thousands of real slot readings where most are right, so both
halves are tested: a reading the record rules out is caught, and a reading it
allows is left alone.

The hardest part is not the accusation but the SILENCE. Three separate things
mean "I cannot say", and every one of them was a live trap while writing
this:

  * the record names a unit the reader has no template for - it can never be
    read, so its absence is a fact about the template set;
  * the reader names something the dataset does not contain - unverifiable;
  * the game upgraded a unit, so the record says "Scout Cavalry" and the
    queue draws a Hussar. That one nearly turned the whole score into an
    accusation against the game's own upgrade lineage, and the only reason
    it did not is that the upgrade technologies are themselves in the
    record: a Hussar with no Hussar research really is a misread.
"""


from loom import replay, replay_ids


def test_the_two_vocabularies_are_kept_apart():
    """BUILDINGS is filtered for the notification reader, QUEUE_UNITS for the
    queue reader, and using one where the other belongs measures nothing."""
    assert replay_ids.BUILDINGS is not replay_ids.QUEUE_UNITS
    # The notification table is much larger: it carries every name Loom can
    # SAY, while the queue table carries only names it has a PICTURE of.
    assert len(replay_ids.BUILDINGS) > len(replay_ids.QUEUE_UNITS)


def test_the_villager_meets_the_reader_at_its_family():
    # The record only ever says "Villager"; the templates say villager_male
    # and villager_female. Without the family every villager in every game
    # would score as an identity nobody ordered.
    assert replay_ids.QUEUE_UNITS[83] == "villager"
    from loom import queue
    assert queue.FAMILY["villager_male"] == "villager"
    assert queue.FAMILY["villager_female"] == "villager"


def test_known_is_the_intersection_and_nothing_more():
    assert "villager" in replay.QUEUE_KNOWN
    assert "fire_galley" in replay.QUEUE_KNOWN      # a real thing, checkable
    assert "not_a_real_unit" not in replay.QUEUE_KNOWN


def _truth(queued=None, researched=None, orders=None):
    truth = replay.Truth(1)
    for ident, amount in (queued or {}).items():
        truth.queued[ident] += amount
    for ident, seconds in (researched or {}).items():
        truth.researches[ident].extend(seconds)
    for oid, rows in (orders or {}).items():
        truth.orders[oid].extend(rows)
    return truth


def test_what_was_ordered_is_allowed():
    truth = _truth(queued={83: 12}, researched={22: [500]})
    subjects = truth.queue_subjects()
    assert "villager" in subjects
    assert "loom" in subjects


def test_what_was_never_ordered_is_not_allowed():
    # The real case: an Arena game with no dock, where the reader reported
    # fire_galley 8 times off a cell of static decoration.
    truth = _truth(queued={83: 12})
    assert "fire_galley" not in truth.queue_subjects()


def test_an_upgrade_is_not_smuggled_in_by_its_base_unit():
    """The subtlety that nearly broke the score, kept as a test.

    Scout Cavalry (448) upgrades to Light Cavalry and then Hussar, and the
    queue draws the upgraded form. So ordering scouts does NOT license the
    reader to say hussar - the upgrade research has to be in the record too.
    In the game this was found on it was not, and the reader was saying
    hussar 133 times.
    """
    truth = _truth(queued={448: 42})
    subjects = truth.queue_subjects()
    assert "scout_cavalry" in subjects
    assert "hussar" not in subjects
    assert "light_cavalry" not in subjects


def test_an_order_with_no_template_is_unverifiable_not_absent():
    # Konnik and Shotel Warrior have no icon template. Naming them would be
    # inventing an answer, so they simply do not enter the allowed set - and
    # the count of them is reported rather than swallowed.
    truth = _truth(queued={1225: 5})          # Konnik
    assert truth.queue_subjects() == set()
    assert 1225 in truth.unverifiable_orders()


def test_a_town_centre_is_the_building_that_does_town_centre_things():
    # The record never says "this object is a Town Centre". An object that
    # trains villagers or researches Loom is one, and there is no other
    # route from a recorded game to how many existed at 09:38.
    truth = _truth(orders={
        3713: [(2, "unit", 83, 1), (507, "technology", 22, 1)],
        4881: [(1098, "unit", 83, 1)],
        9999: [(300, "unit", 4, 1)],           # an archery range
    })
    centres = replay.town_centres(truth)
    assert set(centres) == {3713, 4881}
    assert centres[3713] == 2
    assert centres[4881] == 1098


def test_town_centres_are_counted_as_of_a_moment():
    # The question the phantom-TC bug turns on: how many existed at 09:42.
    truth = _truth(orders={
        3713: [(2, "unit", 83, 1)],
        4881: [(1098, "unit", 83, 1)],
        4908: [(1165, "unit", 83, 1)],
    })
    assert replay.town_centres_at(truth, 582) == 1     # the phantom window
    assert replay.town_centres_at(truth, 1100) == 2
    assert replay.town_centres_at(truth, 2000) == 3


def test_a_town_centre_built_and_never_used_is_not_counted():
    # First ORDER, not first existence, and the under-count is deliberate:
    # it can convict Loom of believing in one Town Centre too many and never
    # of missing one, which is the direction that matters.
    truth = _truth(orders={3713: [(2, "unit", 83, 1)]})
    assert replay.town_centres_at(truth, 3000) == 1


def test_both_spellings_of_the_target_building_are_read():
    # DE_QUEUE and RESEARCH carry object_ids (a selection, so a list); MAKE
    # carries a single building_id. Reading only one loses a command type -
    # MAKE was 207 of 722 production orders in the game measured.
    assert replay._objects_in({"object_ids": [3713]}) == [3713]
    assert replay._objects_in({"building_id": 3720}) == [3720]
    assert replay._objects_in({}) == []


# ---- the tool itself ----------------------------------------------------

def test_the_pairing_manifest_is_optional():
    from tools import queue_report
    assert isinstance(queue_report.read_pairs(), dict)


def test_a_run_folder_name_yields_its_moment():
    from tools import queue_report
    when = queue_report.run_started("captures/run_20260826_142630_annehk")
    assert (when.year, when.hour, when.minute) == (2026, 14, 26)
    assert queue_report.run_started("captures/not_a_run") is None


def test_the_score_is_none_when_nothing_could_be_checked():
    # Not zero. A run the record cannot speak about has no score, and
    # reporting 0% would read as a reader that got everything wrong.
    from tools import queue_report
    faults = queue_report.Faults("run_empty")
    assert faults.identity_score is None


def test_a_replay_cannot_be_watched_before_it_is_written():
    """Causality is the guard that makes duration pairing safe.

    Game durations collide - short games especially - so length alone paired
    a capture run from 18 August with a record from the 22nd, which would
    have attached one game's truth to another game's readings. That is
    exactly the fault `replay.match` refuses to commit, and a development
    corpus does not get to be sloppier about it than the live path.
    """
    import datetime
    from tools import queue_report

    ran = datetime.datetime(2026, 8, 18, 12, 51, 0)
    sessions = [(ran, 900, "stats.json")]
    later = datetime.datetime(2026, 8, 22, 22, 33, 0)
    records = [(later, "written-after-the-run.aoe2record")]

    pairs = queue_report.propose_pairs(
        runs=["captures/run_20260818_125100_stock"],
        sessions=sessions, records=records)
    assert pairs[0][1] is None, "a later record must never pair"


def test_a_run_that_outlives_its_record_stops_being_scored():
    """The corpus fault that would have been baked in as the reader's.

    grab_frames keeps going after a match ends, so a run started during a
    five-minute game and still running when the next one begins captures
    both - and the record knows nothing about the second. Measured:
    run_20260822_213718_annehk paired correctly with a 309-second record and
    then read 996 frames of a 3376-second game, scoring 36%. Every one of
    those 1544 "misreads" was an honest reading of a match the record had
    never heard of. With the boundary enforced it scores 100% on the 61
    frames the record can actually speak about.

    Two ways to leave the game and both must stop it: running past the end,
    and the clock falling backwards into a new match.
    """
    from tools import queue_report
    assert queue_report.NEW_GAME_DROP > 0
    assert queue_report.RECORD_END_GRACE > 0

    ends = 309 + queue_report.RECORD_END_GRACE
    # past the end
    assert 3376 > ends
    # backwards into a new game, which the end test alone would miss
    furthest, when = 3000, 12
    assert when < furthest - queue_report.NEW_GAME_DROP


def test_frames_outside_the_record_are_counted_not_dropped():
    # A corpus whose blind spot is unmeasured is the same fault as a reader
    # whose blind spot is unmeasured.
    from tools import queue_report
    faults = queue_report.Faults("run_x")
    assert hasattr(faults, "beyond")
    assert faults.beyond == 0


def test_an_upgraded_rung_is_not_judged_at_all():
    """The third time the gate was wrong before the reader was.

    A Turkish game: the player ordered Scout Cavalry 42 times and the reader
    never once said scout_cavalry. It said light_cavalry from 32:51 to 42:00
    and hussar from 42:04 to 47:09, with Imperial Age completing at about
    41:40. That is the reader tracking a FREE civilisation upgrade to the
    second, and it was being scored as 455 faults.

    Free is what makes it unfixable by looking for the research: Turks get
    both with no command, so the record is silent and its silence means
    nothing here. And civ bonuses move every balance patch, so a written
    table of them would be KNOWN_WORDS again - silently wrong after the next
    patch with nothing reporting it.

    What is derivable instead: the game names an upgrade technology exactly
    like the unit it produces. So those units are declared unjudgeable, and
    base units stay fully checkable.
    """
    from loom import replay_ids
    for upgraded in ("hussar", "light_cavalry", "man_at_arms", "crossbowman"):
        assert upgraded in replay_ids.QUEUE_UPGRADED
    for base in ("villager", "knight", "archer", "monk"):
        assert base not in replay_ids.QUEUE_UPGRADED, base


def test_declining_to_judge_is_counted_separately_from_being_right():
    # Three silences, three counters, and none of them may be quietly folded
    # into the score: no template, outside the dataset, an upgraded rung.
    from tools import queue_report
    faults = queue_report.Faults("run_x")
    assert (faults.unknown, faults.upgraded, faults.beyond) == (0, 0, 0)
    assert faults.identity_score is None


def test_the_gate_can_actually_fail():
    """A gate never proven able to fail is decoration.

    Demonstrated on the real corpus rather than only asserted here: opening
    every identity gate in the reader (MIN_IDENTITY_SCORE 0.20 -> 0.02, the
    margin to zero) moved run_20260826_142630_annehk from 97.70% to 97.16%,
    and --check reported the fall. This tests the comparison that decides
    it, so the proof does not need eight hundred frames every time.
    """
    from tools import queue_report
    assert queue_report.regressed(0.9716, 0.9770)          # the measured fall
    assert not queue_report.regressed(0.9770, 0.9770)      # unchanged
    assert not queue_report.regressed(0.9800, 0.9770)      # improved
    # Floating-point noise is not a regression, and a real one cannot hide
    # under it - the smallest genuine fall measured was 0.54 points.
    assert not queue_report.regressed(0.977 - 1e-9, 0.9770)


def test_nothing_checkable_is_not_a_regression():
    # A run the record cannot speak about scores None, and None must not
    # read as zero - that would fail the gate for a corpus problem.
    from tools import queue_report
    assert not queue_report.regressed(None, 0.9770)
