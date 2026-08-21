"""
Loom — Milestone 1, icon anchoring.

Finds the population icon in a captured frame using multi-scale template
matching, then works out where the two numbers I care about live:

  * total villager count  — the cyan number inside the population icon
  * game time             — the HH:MM:SS clock further along the top bar

Everything is expressed as offsets from the icon, in "reference pixels"
(the pixel sizes measured on a 2560x1440 capture). When the icon is found
at a different size, I scale those offsets by the same factor.

Debug usage:
    python anchor.py captures/frame_0121.png
writes an annotated copy so you can SEE what it found.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import sys

import cv2
import numpy as np

from . import hud, paths

# Where the template was cut from, in the reference frame.
TEMPLATE_ORIGIN_X = 541
TEMPLATE_ORIGIN_Y = 9

# The regions below are the Anne_HK skin's, and they are also what
# hud.ANNEHK carries. They stay here as module constants because that is
# where they have always been read from; locate_regions now takes the offsets
# from a HudProfile so a second skin can bring its own. See loom/hud.py.

# Read regions, in reference pixels, relative to the template's top-left corner.
# (x1, y1, x2, y2)
VILLAGER_REGION = (14, 31, 60, 56)

# Deliberately generous: a band I search inside, not an exact box.
# The clock's exact spot can shift a little with the in-game HUD scale slider.
CLOCK_BAND = (545, -8, 810, 30)

# The population display ("21/25") sits just right of the icon, white on the
# dark box. The band stops well short of the idle-villager counter further
# right, so a red bell full of white digits can never leak into this reading.
# The right edge also stays clear of the red strip the box grows when the
# player is housed - bright red passes a brightness threshold and a junk run
# at the band's edge aborts an otherwise clean read. "199/200" ends near
# x=140, so 162 loses nothing.
POPULATION_BAND = (58, 14, 162, 44)

# The sizes the icon is looked for at FIRST, and where almost every HUD is.
# I search coarsely first, then refine around the winner: the clock sits
# ~590 reference-pixels from the anchor, so a 3% scale error there becomes
# ~18px of drift. Offset error grows with distance from the anchor.
COARSE_SCALES = np.linspace(0.5, 2.0, 31)
REFINE_STEPS = 21
REFINE_RADIUS = 0.05

# Bigger than the common range, swept only when the common range found nothing.
#
# 2.0 was a hard ceiling until a 4K screen met it: at the game's own 100% HUD
# scale the HUD measures ~2.6x the reference, so Loom refused a HUD it could
# read perfectly well. The comment that used to sit on BEYOND_RANGE_SCALES
# claimed reading up here was unsupported because "the width constants stop
# holding". That was worth testing rather than believing, and it is not true:
# measured over real capture frames upscaled to 2.25x, 2.64x, 2.99x and 3.74x,
# the villager count, the clock and the population all read IDENTICALLY to the
# native-size read, on both skins. The two width constants it was worried about
# had already been fixed - min_glyph_width is capped and max_glyph_width scales.
#
# The step must stay 0.05. The fine sweep brackets the coarse winner by
# REFINE_RADIUS, which is 0.05, so a coarser step here would let the refine
# pass miss the truth entirely.
#
# Why a SECOND sweep instead of simply widening the first one: measured on a
# 3840x2160 frame, the coarse sweep costs 110ms over 0.5-2.0 and 265ms over
# 0.5-4.0. Widening would charge every player 2.4x on acquisition - on the path
# whose comments below record it once costing 15 seconds at 4K - to serve the
# few whose HUD is genuinely huge. Kept separate, an ordinary HUD pays nothing
# and only a HUD that was not found in the common range pays the extra 156ms.
#
# 4.0 rather than higher because it is the last useful value: _best_over_scales
# skips a scale whose template outgrows the search area, and the strip is 12% of
# the frame halved again, so on a 1080p frame nothing above ~3.4x is findable at
# any setting. 4.0 covers a 4K screen up to about 150% in-game HUD scale.
EXTENDED_SCALES = np.linspace(2.0, 4.0, 41)

# The coarse score below which the common range is judged not to have found the
# icon at all, so it is worth asking whether it is simply bigger. A real match
# on the half-size strip scores 0.93-0.96; a sweep that missed scores ~0.47.
# There is a lot of room between those, and this sits in it.
EXTENDED_SEARCH_BELOW = 0.8

# Only the top slice of the frame can contain the resource bar.
SEARCH_HEIGHT_FRACTION = 0.12

# The coarse sweep runs on a half-size copy of the search strip.
#
# It only has to get the scale roughly right - the fine sweep that follows
# runs at full resolution and does the precise work - so paying for four times
# the pixels to answer "roughly what size?" is waste. On a 4K frame the whole
# search went from 1154ms to 246ms, and picked the same scale (1.36 against
# 1.37) with a slightly better score.
#
# That mattered more than an optimisation usually does: at 4K the search cost
# 15 SECONDS under game load, and find_hud runs on the re-anchor path inside
# poll(), so every re-anchor froze the overlay for that long.
#
# Halving the image halves the apparent size of everything in it, so the
# coarse scales are halved to match; the winner is doubled back before the
# fine sweep. Half a coarse step is 0.025 here, which becomes 0.05 at full
# size - exactly REFINE_RADIUS, so the fine sweep still brackets the truth.
COARSE_DOWNSCALE = 0.5

# Re-anchoring already knows roughly what size the HUD is, because the HUD
# does not resize during a match - changing the in-game scale needs an overlay
# restart anyway. So it sweeps a narrow band instead of the whole range.
REANCHOR_RADIUS = 0.10
REANCHOR_STEPS = 9

# Scales past even the extended range, checked only to EXPLAIN a failure.
# Loom just hung waiting for a HUD that was never findable, and the difference
# between "no HUD yet" and "this HUD can never be found" is a player's whole
# evening. Reading is not attempted up here - nothing has been measured at
# these sizes, and on most frames the template no longer fits the search strip
# anyway - but naming the cause costs one sweep, once.
BEYOND_RANGE_SCALES = np.linspace(4.0, 6.0, 8)


def load_template(profile=None):
    """Load a HUD skin's anchor template as greyscale."""
    if profile is None:
        profile = hud.DEFAULT
    template = cv2.imread(str(profile.pop_icon), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"Missing template: {profile.pop_icon}")
    return template


def find_icon(frame_bgr, template_gray, near_scale=None):
    """Locate the population icon at whatever size it happens to be.

    Returns (score, x, y, scale) where x, y is the icon's top-left corner in
    frame coordinates, or None if nothing matched well enough.

    near_scale says "you already know roughly how big it is". Re-anchoring
    does, because the HUD does not resize mid-match, and it turns a full sweep
    into a nine-step one. Leave it None - as the first search must - to hunt
    the whole range.

    Both paths finish with the SAME full-resolution fine sweep, so whichever
    way the scale was guessed, the position handed back is measured at full
    precision. That matters because the clock band sits ~590 reference-pixels
    from the anchor, where a 3% scale error becomes ~18px of drift.
    """
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    # The resource bar is always at the top, so don't waste time lower down.
    search_height = int(frame_gray.shape[0] * SEARCH_HEIGHT_FRACTION)
    search_area = frame_gray[0:search_height, :]

    if near_scale is not None:
        coarse = _best_over_scales(
            search_area, template_gray,
            np.linspace(near_scale - REANCHOR_RADIUS,
                        near_scale + REANCHOR_RADIUS, REANCHOR_STEPS))
        if coarse is None:
            return None
        coarse_scale = coarse[3]
        coarse_x, coarse_y = coarse[1], coarse[2]
    else:
        # Pass 1: coarse sweep for roughly the right size, on a half-size
        # copy. Scales are shrunk to match the image and the winner grown
        # back, so this searches the same range of real sizes as before.
        small = cv2.resize(search_area, None,
                           fx=COARSE_DOWNSCALE, fy=COARSE_DOWNSCALE,
                           interpolation=cv2.INTER_AREA)
        coarse = _best_over_scales(small, template_gray,
                                   COARSE_SCALES * COARSE_DOWNSCALE)

        if coarse is None:
            return None
        coarse_scale = coarse[3] / COARSE_DOWNSCALE
        coarse_x = int(coarse[1] / COARSE_DOWNSCALE)
        coarse_y = int(coarse[2] / COARSE_DOWNSCALE)

    # Pass 2: fine sweep around the winner, at full resolution, which is what
    # makes the returned position trustworthy.
    #
    # Only around the winner. The coarse pass already said WHERE as well as
    # how big, so re-searching the whole strip at twenty-one scales is work
    # thrown away: on a 4K frame that strip is 3840x259 and the icon is about
    # 90x49. Searching a small window instead is what takes this from seconds
    # to milliseconds - and it has to, because find_icon runs on the re-anchor
    # path inside poll(), competing for CPU with a game rendering at 4K.
    fine_scales = np.linspace(coarse_scale - REFINE_RADIUS,
                              coarse_scale + REFINE_RADIUS,
                              REFINE_STEPS)
    refined = _refine_near(search_area, template_gray, fine_scales,
                           coarse_x, coarse_y, coarse_scale)
    if refined is not None:
        return refined

    # Nothing refined: the window may have been too small for the template at
    # these scales. A full-resolution sweep is slow, but a slow answer beats
    # no answer.
    return _best_over_scales(search_area, template_gray, COARSE_SCALES)


def _coarse_scale_over(frame_bgr, template_gray, scales, min_score=0.8):
    """Roughly how big is the icon, if it is here at these scales at all?

    Returns the matched scale, or None. Coarse-only and on a half-size copy:
    both callers want one number - "is it about this big?" - and neither
    places anything with it. Whoever needs a position asks find_icon after,
    with this as its near_scale, which turns a full sweep into a nine-step one.
    """
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    search_height = int(frame_gray.shape[0] * SEARCH_HEIGHT_FRACTION)
    small = cv2.resize(frame_gray[0:search_height, :], None,
                       fx=COARSE_DOWNSCALE, fy=COARSE_DOWNSCALE,
                       interpolation=cv2.INTER_AREA)
    found = _best_over_scales(small, template_gray, scales * COARSE_DOWNSCALE)
    if found is None or found[0] < min_score:
        return None
    return found[3] / COARSE_DOWNSCALE


def larger_icon_scale(frame_bgr, template_gray):
    """Is the icon here but bigger than the common range looks?

    Returns a scale inside EXTENDED_SCALES, or None. This is the READABLE
    oversize case - a 4K screen at the game's own 100% HUD scale puts the icon
    near 2.6x - and the answer feeds a real acquisition.

    Deliberately NOT folded into find_icon. Tried that first and measured what
    it cost: identify_hud tries every skin's template, so the skin that is not
    on screen fell through to the extended sweep every single time, and
    wait_for_hud runs that loop twice a second while the player sits in a menu.
    A blank 4K frame went from 234ms to 500ms an attempt - against a docstring
    on wait_for_hud promising the slow case stays under a third of one core.
    Kept out here, the common path is untouched and the caller decides how
    often the extra search is worth paying for. See reader.find_hud.
    """
    return _coarse_scale_over(frame_bgr, template_gray, EXTENDED_SCALES,
                              EXTENDED_SEARCH_BELOW)


def icon_beyond_range(frame_bgr, template_gray):
    """Is the icon on screen but larger than even the extended search?

    Returns the matched scale, or None. This one never feeds a reading: it
    exists to turn "Loom hangs forever" into a sentence naming the cause.
    """
    return _coarse_scale_over(frame_bgr, template_gray, BEYOND_RANGE_SCALES)


def _refine_near(search_area, template_gray, scales, x, y, scale):
    """Fine sweep in a window around a known position.

    The window is the template's own size at the coarse scale plus a margin,
    so the true match cannot fall outside it: the coarse pass located the
    icon to within half a coarse step, which the margin covers several times
    over. Coordinates come back in whole-strip terms, because every caller
    expects frame coordinates and a window-relative answer would be a silent
    trap.
    """
    height, width = search_area.shape[:2]
    template_h, template_w = template_gray.shape[:2]
    margin = int(max(template_w, template_h) * (scale + REFINE_RADIUS)) + 8

    x1 = max(0, x - margin)
    y1 = max(0, y - margin)
    x2 = min(width, x + int(template_w * (scale + REFINE_RADIUS)) + margin)
    y2 = min(height, y + int(template_h * (scale + REFINE_RADIUS)) + margin)
    if x2 - x1 < 2 or y2 - y1 < 2:
        return None

    best = _best_over_scales(search_area[y1:y2, x1:x2], template_gray, scales)
    if best is None:
        return None
    return (best[0], best[1] + x1, best[2] + y1, best[3])


def _best_over_scales(search_area, template_gray, scales):
    """Try the template at each scale; return the single best match found."""
    best = None
    for scale in scales:
        if scale <= 0:
            continue

        scaled = cv2.resize(template_gray, None, fx=scale, fy=scale,
                            interpolation=cv2.INTER_AREA)

        # A template bigger than the area I'm searching can't be matched.
        if scaled.shape[0] > search_area.shape[0] or scaled.shape[1] > search_area.shape[1]:
            continue

        # TM_CCOEFF_NORMED ignores overall brightness and matches on pattern.
        scores = cv2.matchTemplate(search_area, scaled, cv2.TM_CCOEFF_NORMED)
        _, best_score, _, best_location = cv2.minMaxLoc(scores)

        if best is None or best_score > best[0]:
            best = (best_score, best_location[0], best_location[1], scale)

    return best


def scale_region(region, icon_x, icon_y, scale):
    """Turn a reference-pixel offset box into real frame coordinates."""
    x1, y1, x2, y2 = region
    return (
        int(icon_x + x1 * scale),
        int(icon_y + y1 * scale),
        int(icon_x + x2 * scale),
        int(icon_y + y2 * scale),
    )


def locate_regions(frame_bgr, template_gray, near_scale=None, profile=None):
    """Find the icon and return the two regions to read numbers from.

    near_scale is passed straight through to find_icon; see there.

    profile says which HUD skin's offsets to hang off the icon, and must be
    the profile the template came from - the template and the offsets are one
    measurement, not two. It defaults to the Anne_HK skin, which is what every
    caller meant before there was a choice.
    """
    if profile is None:
        profile = hud.DEFAULT

    match = find_icon(frame_bgr, template_gray, near_scale)
    if match is None:
        return None

    score, icon_x, icon_y, scale = match
    return {
        "score": score,
        "scale": scale,
        "profile": profile,
        "icon": (icon_x, icon_y,
                 icon_x + int(template_gray.shape[1] * scale),
                 icon_y + int(template_gray.shape[0] * scale)),
        "villagers": scale_region(profile.villager_region,
                                  icon_x, icon_y, scale),
        "clock_band": scale_region(profile.clock_band, icon_x, icon_y, scale),
        "population": scale_region(profile.population_band,
                                   icon_x, icon_y, scale),
    }


def identify_hud(frame_bgr, templates, near_scale=None, wood_templates=None):
    """Which HUD skin is on screen, decided by TWO icons agreeing.

    templates maps a profile to its already-loaded anchor template, so the
    caller keeps them across polls; wood_templates does the same for each
    skin's wood icon. Returns the best locate_regions result with a "score"
    the caller gates on exactly as it always has.

    Why two icons. Every skin draws the SAME game art - the population icon is
    the same two villagers whichever bar surrounds it - so the anchor alone is
    a weak discriminator: measured, the stock anchor scores 0.91-0.95 on
    modded HUDs against the mod anchor's 0.93-0.97, a margin of about 0.02.
    Deciding a whole session's read geometry on 0.02 is not deciding it at
    all, and the loser's offsets would read confident nonsense off the wrong
    parts of the bar.

    The wood icon is the second opinion, matched at the scale the anchor
    proposes. A skin that is really on screen has both its icons where it
    expects them at one size; a skin that is not has to explain the pop icon
    at some wrong scale and then finds nothing where its wood icon should be.
    Scoring the pair by their WEAKER member turned that 0.02 into 0.27 or
    better on every frame measured - across three stock civs, twenty-seven
    modded capture runs, and both resolutions. The true skin never scored
    under 0.91; the wrong one never over 0.71.
    """
    best = None
    for profile, template in templates.items():
        found = locate_regions(frame_bgr, template, near_scale, profile)
        if found is None:
            continue

        wood_template = (wood_templates or {}).get(profile)
        if wood_template is not None:
            found["anchor_score"] = found["score"]
            found["wood_score"] = _wood_agreement(frame_bgr, wood_template,
                                                  found["scale"])
            # The pair is only as good as its weaker half. A mean would let a
            # near-perfect pop match carry a wood icon that is simply absent.
            found["score"] = min(found["anchor_score"], found["wood_score"])

        if best is None or found["score"] > best["score"]:
            best = found
    return best


def _wood_agreement(frame_bgr, wood_template, scale):
    """How well a skin's wood icon sits in the bar at the anchor's scale.

    Imported lazily: resources imports anchor for its own region maths, and
    asking for it at module level would be a circular import.
    """
    from . import resources

    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    bar_height = int(frame_gray.shape[0] * resources.BAR_HEIGHT_FRACTION)
    match = resources._match_at_scale(frame_gray[0:bar_height, :],
                                      wood_template, scale)
    return 0.0 if match is None else match[0]


def draw_debug(frame_bgr, found):
    """Draw the match and read regions so a human can check them."""
    annotated = frame_bgr.copy()
    colors = {
        "icon": (0, 255, 0),        # green  — what the template matched
        "villagers": (255, 0, 255),  # magenta — villager count region
        "clock_band": (0, 200, 255),  # orange — clock search band
        "population": (255, 200, 0),  # cyan — population N/M display
    }
    for key, color in colors.items():
        x1, y1, x2, y2 = found[key]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        cv2.putText(annotated, key, (x1, max(12, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    label = (f"{found['profile'].name}  score={found['score']:.3f}"
             f"  scale={found['scale']:.2f}")
    cv2.putText(annotated, label, (10, frame_bgr.shape[0] - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return annotated


def main():
    if len(sys.argv) < 2:
        print("usage: python anchor.py <frame.png> [more.png ...]")
        return

    templates = {profile: load_template(profile) for profile in hud.PROFILES}
    for path in sys.argv[1:]:
        frame = cv2.imread(path)
        if frame is None:
            print(f"{path}: could not read")
            continue

        found = identify_hud(frame, templates)
        if found is None:
            print(f"{path}: no match")
            continue

        out_path = path.replace(".png", "_debug.png")
        cv2.imwrite(out_path, draw_debug(frame, found))
        print(f"{path}: hud={found['profile'].name} "
              f"score={found['score']:.3f} scale={found['scale']:.2f} "
              f"icon={found['icon'][:2]} -> {out_path}")


if __name__ == "__main__":
    main()
