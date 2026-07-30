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

from . import paths

# Where the template was cut from, in the reference frame.
TEMPLATE_ORIGIN_X = 541
TEMPLATE_ORIGIN_Y = 9

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

# The icon can be anywhere from half to double its reference size.
# I search coarsely first, then refine around the winner: the clock sits
# ~590 reference-pixels from the anchor, so a 3% scale error there becomes
# ~18px of drift. Offset error grows with distance from the anchor.
COARSE_SCALES = np.linspace(0.5, 2.0, 31)
REFINE_STEPS = 21
REFINE_RADIUS = 0.05

# Only the top slice of the frame can contain the resource bar.
SEARCH_HEIGHT_FRACTION = 0.12


def load_template():
    """Load the anchor template as greyscale."""
    template = cv2.imread(str(paths.POP_ICON_TEMPLATE), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"Missing template: {paths.POP_ICON_TEMPLATE}")
    return template


def find_icon(frame_bgr, template_gray):
    """Locate the population icon at whatever size it happens to be.

    Returns (score, x, y, scale) where x, y is the icon's top-left corner in
    frame coordinates, or None if nothing matched well enough.
    """
    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    # The resource bar is always at the top, so don't waste time lower down.
    search_height = int(frame_gray.shape[0] * SEARCH_HEIGHT_FRACTION)
    search_area = frame_gray[0:search_height, :]

    # Pass 1: coarse sweep to find roughly the right size.
    best = _best_over_scales(search_area, template_gray, COARSE_SCALES)
    if best is None:
        return None

    # Pass 2: fine sweep around the winner, so distant offsets stay accurate.
    coarse_scale = best[3]
    fine_scales = np.linspace(coarse_scale - REFINE_RADIUS,
                              coarse_scale + REFINE_RADIUS,
                              REFINE_STEPS)
    refined = _best_over_scales(search_area, template_gray, fine_scales)

    return refined if refined and refined[0] >= best[0] else best


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


def locate_regions(frame_bgr, template_gray):
    """Find the icon and return the two regions to read numbers from."""
    match = find_icon(frame_bgr, template_gray)
    if match is None:
        return None

    score, icon_x, icon_y, scale = match
    return {
        "score": score,
        "scale": scale,
        "icon": (icon_x, icon_y,
                 icon_x + int(template_gray.shape[1] * scale),
                 icon_y + int(template_gray.shape[0] * scale)),
        "villagers": scale_region(VILLAGER_REGION, icon_x, icon_y, scale),
        "clock_band": scale_region(CLOCK_BAND, icon_x, icon_y, scale),
        "population": scale_region(POPULATION_BAND, icon_x, icon_y, scale),
    }


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

    label = f"score={found['score']:.3f}  scale={found['scale']:.2f}"
    cv2.putText(annotated, label, (10, frame_bgr.shape[0] - 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    return annotated


def main():
    if len(sys.argv) < 2:
        print("usage: python anchor.py <frame.png> [more.png ...]")
        return

    template = load_template()
    for path in sys.argv[1:]:
        frame = cv2.imread(path)
        if frame is None:
            print(f"{path}: could not read")
            continue

        found = locate_regions(frame, template)
        if found is None:
            print(f"{path}: no match")
            continue

        out_path = path.replace(".png", "_debug.png")
        cv2.imwrite(out_path, draw_debug(frame, found))
        print(f"{path}: score={found['score']:.3f} scale={found['scale']:.2f} "
              f"icon={found['icon'][:2]} -> {out_path}")


if __name__ == "__main__":
    main()
