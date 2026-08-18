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


def naive_find_icon(frame_bgr, template_gray):
    """The pre-optimisation search: full resolution, whole strip, both passes."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    area = gray[0:int(gray.shape[0] * anchor.SEARCH_HEIGHT_FRACTION), :]
    coarse = anchor._best_over_scales(area, template_gray, anchor.COARSE_SCALES)
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
