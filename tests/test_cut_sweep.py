"""
Loom — tests for how queue templates are cut and filed.

Neither the builder nor the sweep can be run here: both want the game's own
texture folder, and CI has no game installed. What CAN be tested is the
arithmetic and the filing rule, which is where both bugs actually lived.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import numpy as np

from loom import queue
from tools import build_queue_templates as build


def art(side=64):
    """A picture with no symmetry, so a shifted window cannot look centred."""
    rows = np.arange(side).reshape(-1, 1)
    columns = np.arange(side).reshape(1, -1)
    plane = ((rows * 3 + columns * 5) % 256).astype(np.uint8)
    return np.dstack([plane, plane // 2, plane // 3]).astype(np.uint8)


def test_todays_unit_cut_is_a_point_in_the_search():
    """The swept cut must reproduce what is committed at its defaults.

    Otherwise the sweep is measured against a moving baseline and "better
    than today" means nothing. This is also what let the whole template set
    be rebuilt on a different OS and diffed to zero.

    The offset is (0, 0) despite tools/cut_sweep.py recommending (-2, 0) at
    zoom 1.30 - 100% top-1 at all three HUD scales, junk held lower than
    here. `queue_report --check` refused it: a Magyars scouts game fell from
    100.00% to 97.16%, six cells reading `knight` in a game that never
    ordered one. The sweep's labels come from cells today's templates
    already read confidently, so the cells a new cut DAMAGES are the ones
    excluded from its sample. It can compare cuts; it cannot approve one.
    """
    image = art()
    assert np.array_equal(build.cut_unit_dds(image), build.cut_template(image))
    assert build.UNIT_DDS_OFFSET == (0, 0)
    assert build.UNIT_DDS_ZOOM == build.ZOOM


def test_the_window_moves_and_stops_at_the_edge():
    image = art()
    centred = build.cut_unit_dds(image, 1.25, (0, 0))
    moved = build.cut_unit_dds(image, 1.25, (2, 0))
    assert centred.shape == moved.shape == (build.TEMPLATE_SIZE,
                                            build.TEMPLATE_SIZE, 3)
    assert not np.array_equal(centred, moved)
    # Clamped, never padded. A window running off the edge would be part art
    # and part black, and the black scores as agreement on any dark cell.
    far = build.cut_unit_dds(image, 1.25, (99, 99))
    edge = build.cut_unit_dds(image, 1.25, (5, 5))
    assert np.array_equal(far, edge)
    # A zoom of 1.0 leaves no room to move, and must not crash asking.
    assert build.cut_unit_dds(image, 1.0, (3, 3)).shape[0] \
        == build.TEMPLATE_SIZE


def test_a_tech_icon_merges_only_into_a_technology():
    """The rule that stopped a research being filed under its unit.

    The game names an upgrade technology exactly like the unit it produces,
    so the collision cannot be resolved by spelling. It is resolved by the
    recorded KIND, and anything not recorded as a technology splits -
    because an unrecorded kind is not evidence that merging is safe.
    """
    kinds = {"loom": queue.TECHNOLOGY, "crossbowman": queue.UNIT,
             "town_center": queue.BUILDING, "mystery": queue.UNKNOWN}
    existing = set(kinds)
    # Two sources for one technology stay one identity, as they always did.
    assert build.tech_identity("loom", existing, kinds) == "loom"
    # A unit's name is not available to a technology.
    assert build.tech_identity("crossbowman", existing, kinds) \
        == "crossbowman" + build.UPGRADE_SUFFIX
    assert build.tech_identity("town_center", existing, kinds) \
        == "town_center" + build.UPGRADE_SUFFIX
    assert build.tech_identity("mystery", existing, kinds) \
        == "mystery" + build.UPGRADE_SUFFIX
    # Nothing to collide with: a new technology keeps its own plain name.
    assert build.tech_identity("bracer", existing, kinds) == "bracer"
    # KINDS.tsv also holds rows for feed subjects with no template. Those
    # are not in `existing`, so they collide with nothing.
    assert build.tech_identity("house", set(), {"house": queue.BUILDING}) \
        == "house"


def test_the_suffix_is_one_name_in_two_modules():
    """build_queue_templates writes it and queue.py reads it back.

    Two spellings would be the failure this project keeps meeting: one
    question answered in two places, the stricter answer winning in silence.
    """
    assert build.UPGRADE_SUFFIX == queue.UPGRADE_SUFFIX
