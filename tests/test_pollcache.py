"""
Loom — tests for the cached per-poll queue readings.

The corpus is gitignored and absent on CI, so what is tested here is the
contract: that the stamp covers everything that can change a reading, and
that a reading survives the round trip unchanged.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ast
import pathlib

from loom import paths, queue
from tools import pollcache


def test_the_stamp_covers_every_module_that_can_change_a_reading():
    """READING_MODULES is a hand-written list, which this project has
    learned to distrust: a name missing from one fails silently and
    invisibly.

    So it is walked rather than believed. Whatever framebands imports out
    of loom/ is, by definition, part of how a slot reading is produced -
    and must therefore be able to invalidate the cache.
    """
    source = (paths.PROJECT_ROOT / "tools" / "framebands.py").read_text(
        encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "loom":
            for alias in node.names:
                imported.add(alias.name)
    assert imported, "framebands imports nothing from loom - has it moved?"
    missing = imported - set(pollcache.READING_MODULES)
    assert not missing, (
        f"{sorted(missing)} can change a slot reading and would not "
        f"invalidate the cache - add them to pollcache.READING_MODULES")


def test_the_stamp_does_not_cover_the_rules_built_on_top():
    """The whole reason the cache exists. Tuning episodes.py must not throw
    away thirty thousand PNG decodes - that is forty minutes of the
    author's machine per question, and it is what made a day of sweeps
    cost more than it should have."""
    for name in ("episodes", "production", "events", "gamestats",
                 "statsview"):
        assert name not in pollcache.READING_MODULES, name


def test_the_stamp_is_stable_and_moves_with_a_template(tmp_path):
    before = pollcache.reading_fingerprint()
    assert before == pollcache.reading_fingerprint()

    probe = paths.TEMPLATES_DIR / "_pollcache_probe.png"
    probe.write_bytes(b"not really a png")
    try:
        pollcache._fingerprint = None
        assert pollcache.reading_fingerprint() != before
    finally:
        probe.unlink()
        pollcache._fingerprint = None
    assert pollcache.reading_fingerprint() == before


def test_a_reading_survives_the_round_trip():
    """Rebuilt as real SlotReadings, so a consumer runs against the same
    shape the live reader produces rather than a tuple that works until
    someone adds a field."""
    row = {"run": "run_x", "reader": pollcache.reading_fingerprint(),
           "polls": [[12, [[0, "green", 0.4, 3, "villager_male", 0.81]]],
                     [14, None],
                     [16, []]]}
    polls = list(pollcache.slot_readings(row))
    assert len(polls) == 3

    when, slots = polls[0]
    assert when == 12 and len(slots) == 1
    slot = slots[0]
    assert isinstance(slot, queue.SlotReading)
    assert (slot.index, slot.tint, slot.progress, slot.count,
            slot.identity, slot.identity_score) == (
        0, "green", 0.4, 3, "villager_male", 0.81)

    # A poll where the HUD was not found is None; a poll where the queue
    # was genuinely empty is an empty list. Collapsing those two would have
    # a consumer conclude production stopped when the game was in a menu -
    # the absent-versus-empty distinction that has cost this project two
    # phantom Town Centres by two different routes.
    assert polls[1][1] is None
    assert polls[2][1] == []


def test_a_row_from_another_reader_is_not_served(tmp_path, monkeypatch):
    monkeypatch.setattr(pollcache, "CACHE", pathlib.Path(tmp_path))
    (tmp_path / "run_x.json").write_text(
        '{"run": "run_x", "reader": "not-the-current-one", "polls": []}',
        encoding="utf-8")
    assert pollcache.load("run_x") is None
