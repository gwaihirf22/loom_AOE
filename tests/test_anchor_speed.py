"""
Loom — the fast anchor search must find exactly what the slow one found.

find_icon got two optimisations because at 4K it cost SECONDS, and it runs on
the re-anchor path inside poll() - so every re-anchor froze the overlay for
that long, which is the shape of "the overlay lags behind" that started this.
Measured on a live 4K game: 13.5s to find the HUD, 0.27s after.

  * the coarse sweep runs on a half-size copy, since it only decides roughly
    what size the icon is;
  * the fine sweep looks only NEAR the coarse winner, because the coarse pass
    already said where it was - re-searching a 3840x259 strip to place a 90x49
    icon was the bulk of the cost.

Neither may change the answer. Everything downstream hangs off this position:
the clock band sits ~590 reference-pixels away, where a 3% scale error becomes
~18px of drift and the clock reads the game speed instead of the time. So
these tests compare the shipping search against a deliberately naive
full-resolution one over every full frame in the corpus, and require they
agree.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pathlib

import cv2
import numpy as np
import pytest

from loom import anchor

FRAMES = sorted((pathlib.Path(__file__).parent / "data" / "frames").glob("*.png"))

# The anchor's own tolerance for "the same match": a scale within one refine
# step, and a position within a couple of pixels.
SCALE_TOLERANCE = anchor.REFINE_RADIUS
POSITION_TOLERANCE = 2


def naive_find_icon(frame_bgr, template_gray, scales=None):
    """The pre-optimisation search: full resolution, whole strip, both passes.

    scales says which range to sweep, so the same reference can check the
    extended one without charging every in-range fixture for 41 scales it
    will never match.
    """
    if scales is None:
        scales = anchor.COARSE_SCALES
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    area = gray[0:int(gray.shape[0] * anchor.SEARCH_HEIGHT_FRACTION), :]
    coarse = anchor._best_over_scales(area, template_gray, scales)
    if coarse is None:
        return None
    fine = np.linspace(coarse[3] - anchor.REFINE_RADIUS,
                       coarse[3] + anchor.REFINE_RADIUS, anchor.REFINE_STEPS)
    refined = anchor._best_over_scales(area, template_gray, fine)
    return refined if refined and refined[0] >= coarse[0] else coarse


@pytest.fixture(scope="module")
def template():
    return anchor.load_template()


@pytest.mark.skipif(not FRAMES, reason="no full-frame fixtures")
@pytest.mark.parametrize("path", FRAMES, ids=lambda p: p.name)
def test_the_fast_search_agrees_with_the_naive_one(template, path):
    frame = cv2.imread(str(path))
    assert frame is not None, f"unreadable fixture {path}"

    slow = naive_find_icon(frame, template)
    fast = anchor.find_icon(frame, template)
    assert (slow is None) == (fast is None)
    if slow is None:
        return

    assert abs(fast[3] - slow[3]) <= SCALE_TOLERANCE, "scale drifted"
    assert abs(fast[1] - slow[1]) <= POSITION_TOLERANCE, "x drifted"
    assert abs(fast[2] - slow[2]) <= POSITION_TOLERANCE, "y drifted"


@pytest.mark.skipif(not FRAMES, reason="no full-frame fixtures")
@pytest.mark.parametrize("path", FRAMES, ids=lambda p: p.name)
def test_a_re_anchor_that_knows_the_scale_finds_the_same_icon(template, path):
    """The nine-step sweep is what runs mid-match; it must not be sloppier."""
    frame = cv2.imread(str(path))
    full = anchor.find_icon(frame, template)
    if full is None:
        pytest.skip("no anchor in this fixture")

    narrow = anchor.find_icon(frame, template, near_scale=full[3])
    assert narrow is not None
    assert abs(narrow[3] - full[3]) <= SCALE_TOLERANCE
    assert abs(narrow[1] - full[1]) <= POSITION_TOLERANCE
    assert abs(narrow[2] - full[2]) <= POSITION_TOLERANCE


@pytest.mark.skipif(not FRAMES, reason="no full-frame fixtures")
def test_a_badly_wrong_hint_still_finds_the_icon(template):
    """A hint is a shortcut, not a promise.

    reader.find_hud falls back to the full hunt when a narrow sweep scores
    too low, so a stale hint costs one slow poll rather than the HUD.
    """
    frame = cv2.imread(str(FRAMES[0]))
    truth = anchor.find_icon(frame, template)
    if truth is None:
        pytest.skip("no anchor in this fixture")

    misled = anchor.find_icon(frame, template, near_scale=truth[3] + 0.8)
    # It may find nothing convincing, which is the caller's cue to hunt
    # properly - but it must not confidently report the wrong place.
    if misled is not None and misled[0] >= 0.8:
        assert abs(misled[1] - truth[1]) <= POSITION_TOLERANCE


# ---- the extended range ----------------------------------------------------
#
# 2.0 was a hard ceiling until a 4K screen met it: at the game's own 100% HUD
# scale the HUD measures ~2.6x the reference, and Loom refused a HUD it reads
# perfectly well. Measured over real frames upscaled to 2.25x / 2.64x / 2.99x /
# 3.74x, villagers, clock and population all read identically to the native
# read on both skins - so the ceiling was a search limit, not a reading one.
#
# The fixture is made here by upscaling rather than committed: a 4K PNG of a
# HUD nobody can re-measure is weight without evidence.


def test_a_hud_past_the_common_range_can_be_found_and_placed(template):
    """The whole point of the change, over the path reader.find_hud uses:
    one coarse discovery sweep, then find_icon with that as a near_scale.

    Position matters as much as finding it - the clock band sits ~590
    reference-pixels from the anchor, so a scale that is merely close puts the
    clock read somewhere else entirely.
    """
    frame = cv2.imread(str(FRAMES[0]))
    big = cv2.resize(frame, None, fx=3.5, fy=3.5, interpolation=cv2.INTER_CUBIC)

    discovered = anchor.larger_icon_scale(big, template)
    assert discovered is not None, "an oversize HUD was not discovered"
    assert discovered > anchor.COARSE_SCALES[-1], "this fixture should be oversize"

    placed = anchor.find_icon(big, template, near_scale=discovered)
    assert placed is not None

    slow = naive_find_icon(big, template, anchor.EXTENDED_SCALES)
    assert abs(placed[3] - slow[3]) <= SCALE_TOLERANCE, "scale drifted"
    assert abs(placed[1] - slow[1]) <= POSITION_TOLERANCE, "x drifted"
    assert abs(placed[2] - slow[2]) <= POSITION_TOLERANCE, "y drifted"


@pytest.mark.parametrize("path", FRAMES, ids=lambda p: p.name)
def test_find_icon_never_sweeps_the_extended_range(template, path, monkeypatch):
    """The performance promise, and the reason the extended sweep lives in
    reader.find_hud rather than in here.

    Folding it into find_icon was tried first and measured: identify_hud tries
    every skin's template, so the skin NOT on screen fell through to the
    extended sweep every single time, and wait_for_hud runs that twice a second
    for as long as a player sits in a menu. A blank 4K frame went from 234ms to
    500ms an attempt, against a docstring promising the slow case stays under a
    third of one core. Nothing about a slower acquisition looks like a bug, so
    only a test holds this.
    """
    swept = []
    real = anchor._best_over_scales
    monkeypatch.setattr(anchor, "_best_over_scales",
                        lambda area, tmpl, scales: (
                            swept.append(float(max(scales))) or
                            real(area, tmpl, scales)))

    anchor.find_icon(cv2.imread(str(path)), template)

    ceiling = anchor.EXTENDED_SCALES[-1] * anchor.COARSE_DOWNSCALE
    assert not any(abs(top - ceiling) < 1e-9 for top in swept), (
        "find_icon swept the extended range; that cost belongs to the caller")


def test_an_empty_frame_is_not_handed_a_confident_giant_match(template):
    """Looking in a bigger range must not turn "nothing here" into a large
    match. This is the never-guess-a-reading rule: no HUD has to stay no HUD,
    or Loom hangs its whole read geometry off noise."""
    blank = np.zeros((1080, 1920, 3), np.uint8)

    assert anchor.larger_icon_scale(blank, template) is None
