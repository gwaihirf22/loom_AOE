"""
Loom — reading villagers-per-resource off the HUD.

Under each resource icon the game shows a small yellow number: how many
villagers are gathering that resource. Reading them lets Loom compare the
player's actual distribution against what the build order wants.

This is deliberately kept apart from the main reader. Per-resource counts are
**advisory only** - they are shown to the player, but they never decide which
build-order step is current. Total villager count does that. Per-resource
numbers swing wildly the instant villagers are re-tasked, so they make a poor
signal for anything the player cannot immediately see for themselves.

Finding the numbers works exactly like the population icon: match each resource
icon, then read the digits at a known offset from it. The population icon has
already established the HUD scale by the time this runs, so each resource icon
is matched at that one scale rather than searched across many - which makes it
cheap enough to do once at startup.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import cv2
import numpy as np

from . import digits, paths

# The four resource icons, in the order they appear on the bar.
RESOURCE_NAMES = ("food", "wood", "gold", "stone")

# The yellow number sits just below the icon, in a box this many reference
# pixels across and starting this far below the icon's top. The number is not
# in exactly the same spot for every resource, so rather than a tight box per
# resource I scan a generous strip and pick the digits out of it. (all in
# reference pixels, scaled to the HUD)
NUMBER_STRIP = {"left": -4, "right": 62, "top": 22, "bottom": 52}

# Only the top slice of the frame holds the resource bar.
BAR_HEIGHT_FRACTION = 0.06

# Below this match score I assume the icon is not there (a mod replaced it, or
# the bar is not visible) and skip that resource rather than reading garbage.
MIN_ICON_SCORE = 0.6

# ...and however good the score, the four icons are the leftmost things in the
# bar. Measured: on a stock bar the mod's food icon scores 0.62 out at x=1545,
# which is not the food icon, it is terrain.
MAX_ICON_X_FRACTION = 0.35


def load_resource_templates(profile=None):
    """Load a HUD skin's four resource icon templates as grayscale images."""
    from . import hud

    if profile is None:
        profile = hud.DEFAULT
    templates = {}
    for name in RESOURCE_NAMES:
        path = profile.resource_dir / f"{name}_icon.png"
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise FileNotFoundError(f"Missing resource template: {path}")
        templates[name] = image
    return templates


def _match_at_scale(search_area, template, scale):
    """Find the best position for a template at one fixed size.

    Returns (score, x, y) in search-area coordinates, or None if the scaled
    template does not fit.

    The FILTER follows the direction, which it did not used to. INTER_AREA
    shrinks well and grows badly, degenerating towards nearest neighbour
    and handing the matcher a stair-stepped outline to compare against a
    smoothly drawn icon - and above HUD scale 1.0 every template here is
    being GROWN. This is on the identify_hud path (the wood icon is the
    second opinion that names the skin) and on the queue's own
    self-location, so a score lost here is not one band misreading, it is
    the HUD not being found or the queue reporting nothing at all.
    age.py:169 has made this call correctly since it was written; this is
    the same call in the three places that had not caught up.
    """
    scaled = cv2.resize(template, None, fx=scale, fy=scale,
                        interpolation=(cv2.INTER_AREA if scale < 1.0
                                       else cv2.INTER_CUBIC))
    if scaled.shape[0] > search_area.shape[0] or scaled.shape[1] > search_area.shape[1]:
        return None

    scores = cv2.matchTemplate(search_area, scaled, cv2.TM_CCOEFF_NORMED)
    _, best_score, _, best_location = cv2.minMaxLoc(scores)
    return best_score, best_location[0], best_location[1]


def locate_regions(frame_bgr, templates, scale, profile=None):
    """Find each resource icon and the box to read its number from.

    `scale` is the HUD scale already worked out from the population icon.
    Returns {name: (x1, y1, x2, y2)} for the resources that were found.

    profile says where that number sits relative to its icon, which is not the
    same on every skin - the mod prints it below, stock stamps it inside the
    icon's own box.
    """
    from . import hud

    if profile is None:
        profile = hud.DEFAULT
    strip = profile.number_strip

    frame_gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    bar_height = int(frame_gray.shape[0] * BAR_HEIGHT_FRACTION)
    search_area = frame_gray[0:bar_height, :]

    regions = {}
    for name, template in templates.items():
        match = _match_at_scale(search_area, template, scale)
        if match is None or match[0] < MIN_ICON_SCORE:
            continue

        _, icon_x, icon_y = match
        # Same lesson the queue's wood anchor learned: a score says how well
        # the picture matched, never whether it matched in a place the icon
        # could be. The four icons live in the left of the bar, in order, so
        # a match out past the middle of the frame is a false positive - the
        # mod's food icon lands at x=1545 on a stock bar at 0.62.
        if icon_x > frame_bgr.shape[1] * MAX_ICON_X_FRACTION:
            continue

        regions[name] = (
            int(icon_x + strip["left"] * scale),
            int(icon_y + strip["top"] * scale),
            int(icon_x + strip["right"] * scale),
            int(icon_y + strip["bottom"] * scale),
        )
    return regions


# How much blue a pixel may carry and still count as one of the yellow
# digits. Measured rather than chosen: the mod draws these numbers at blue
# EXACTLY 0 - min, median and max all zero across every digit in a live
# frame - while the resource bar's own brown chrome starts at blue 61. The
# gate used to be 110, which is above the chrome, so the chrome came through.
#
# That mattered for one band only, and for a reason worth writing down. The
# number strip starts a few reference pixels LEFT of its icon, to allow for a
# number that is not centred the same on every resource. For food, gold and
# stone those pixels land on the previous icon's black box. Wood is the
# LEFTMOST resource, so they land on the bar's end cap instead - brown, tall
# enough to survive _keep_digit_shapes, and therefore a second "glyph" beside
# the real one. The reader then saw two digits where there was one and
# refused, which is the never-guess rule working correctly on bad input.
#
# Swept over the capture corpus, 1686 anchored frames: wood 97.0% -> 99.8%,
# and food, gold and stone unchanged at 99.8%. The misses clustered whole
# runs at a time rather than scattering, which is what a per-game difference
# in the bar art looks like and not what a marginal threshold looks like.
#
# 30 because it is the middle of the gap: 30 clear of the digits at 0 and 31
# clear of the chrome at 61, rather than hugging either population. Swept at
# 20, 30 and 40 the corpus gives the same 99.8% on all four bands, so the
# choice inside that range is free and the symmetric one is the one to take -
# the digits are the fixed population (the mod draws them pure) and the
# chrome is the variable one, so margin below matters most.
#
# `red - blue` would separate them too, 122 against 72. One discriminator
# with a 61-level gap is enough, and a second constant is a second thing
# that has to stay true.
MAX_DIGIT_BLUE = 30


def yellow_mask(crop_bgr):
    """White-on-black image of just the yellow pixels in a crop.

    The per-resource numbers are yellow, and a plain brightness threshold does
    not work for them: the wooden bar's highlights are bright too, and get
    caught alongside the digits, which breaks them into pieces. Yellow is
    specifically high red and green with low blue, so testing for that isolates
    the digits and drops the brown-and-white noise around them.

    How little blue is the load-bearing part - see MAX_DIGIT_BLUE.
    """
    blue = crop_bgr[:, :, 0].astype(int)
    green = crop_bgr[:, :, 1].astype(int)
    red = crop_bgr[:, :, 2].astype(int)

    is_yellow = ((red > 120) & (green > 100) & (blue < MAX_DIGIT_BLUE)
                 & (red - blue > 60))
    mask = (is_yellow * 255).astype(np.uint8)
    return _keep_digit_shapes(mask)


def _keep_digit_shapes(mask):
    """Drop everything in the mask that is not shaped like a digit.

    The wooden bar's highlight leaks through as thin horizontal lines, and its
    texture as small specks. Both wreck the digit segmentation: a line puts a
    white pixel in every column, so the digits merge into one wide blob.

    Connected-component analysis groups touching pixels, so I can keep only the
    groups that are tall enough to be a digit and drop the rest. A horizontal
    line is wide but short; a speck is small; a digit is neither.
    """
    if mask.max() == 0:
        return mask

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    height = mask.shape[0]

    cleaned = np.zeros_like(mask)
    for label in range(1, count):          # 0 is the black background
        component_height = stats[label, cv2.CC_STAT_HEIGHT]
        component_area = stats[label, cv2.CC_STAT_AREA]
        if component_height >= 0.45 * height and component_area >= 8:
            cleaned[labels == label] = 255
    return cleaned


def read_one(crop_bgr, digit_templates, min_glyph_width):
    """Read the number from a single resource crop. None if unreadable.

    Two passes, because the skins ink this number differently: the mod prints
    it yellow below the icon, stock stamps it white inside the icon's box. The
    yellow pass runs first and unchanged, so a HUD it already reads is read
    exactly as before; the white-and-colourless pass is the same one the
    villager badge needs, at the same threshold and for the same reason - it
    sits on the icon art, where brightness alone cannot tell a digit from a
    highlight.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None

    found, _ = digits.read_binary(yellow_mask(crop_bgr), digit_templates,
                                  min_glyph_width)
    if found is None:
        found, _ = digits.read_binary(
            _keep_digit_shapes(digits.white_mask(crop_bgr,
                                                 digits.BADGE_WHITE)),
            digit_templates, min_glyph_width)
    return None if found is None else digits.digits_to_int(found)
