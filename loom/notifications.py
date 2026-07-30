"""
Loom — reading the game's own notification feed.

The game announces events as text on the left side of the screen:
"--Town Center Built--", "--Villager Created--", research completions, and
(in the player's colour) attack warnings. This is the one place the HUD
states facts as WORDS, and for some facts it is the only exact source there
is: the global queue can only ever suggest how many Town Centres exist, but
"--Town Center Built--" states it outright.

Recognition is phrase-level template matching: the font, size and wording
are fixed, and the vocabulary Loom cares about is tiny, so one grayscale
template per phrase (harvested from a capture frame, in
templates/notifications/) beats anything cleverer. The white outlined text
scores ~0.9 against its own phrase and nothing else comes close.

A notification lingers for around ten seconds and scrolls as newer lines
arrive, so one event is sighted on many consecutive polls. The watcher
reports each phrase once per appearance: a rising edge starts a cooldown,
and re-sightings inside it are the same event.

The trap in that: the panel does not just fade lines out, it brings them
BACK. After ~8 idle seconds the whole feed fades, and the next message -
any message - redisplays the recent history above itself. One real "Town
Center Built" resurfaced under every subsequent "Villager Created" for
minutes, and each resurfacing after the cooldown counted as a new TC
(measured in a live game: one TC fired three times over 92 game seconds).
The tell that separates event from echo: a fresh message always arrives as
the BOTTOM line of the stack, while redisplayed history sits above newer
lines. So a phrase only fires when it is the panel's bottom-most text
line. The accepted cost is an undercount: an event whose line is pushed
off the bottom within a single poll (~0.3s) by an even newer message is
never counted. A missed TC costs a missed idle alert; an imaginary TC
nags for the rest of the game.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np

from . import paths

# Where the feed lives, as fractions of the frame: the left quarter of the
# screen, below the resource bar. Generous on purpose - the panel's exact
# spot shifts a little as lines scroll, and matching inside a roomy region
# costs microseconds.
PANEL_TOP = 0.11
PANEL_BOTTOM = 0.26
PANEL_RIGHT = 0.25

# Below this score a phrase is not on screen. Its own template scores ~0.9;
# unrelated text peaks well under 0.7.
MIN_PHRASE_SCORE = 0.8

# Second gate: the WORDS themselves. Correlation finds a candidate spot;
# this confirms the text shape by comparing binarized ink (IoU of white
# pixels between the template and the matched region). Measured: a real
# sighting reads 0.94+, panels with only other text read <= 0.36 - a gate
# at 0.6 sits in open water. Exists as defence in depth: a mistaken TC
# count poisons the idle logic for a whole game, so this channel gets two
# independent checks.
MIN_INK_AGREEMENT = 0.6
INK_THRESHOLD = 200

# Finding the stack's text lines. Game text is bright with a black outline,
# so a text pixel is a bright pixel NEXT TO a very dark one - plain
# brightness alone fails on the translucent HUD, where sunlit snow behind
# the panel is as bright as the font. The bright gate sits at 140 because
# attack warnings render in the attacker's player colour and red text
# peaks near 176 in grayscale. All in pixels at HUD scale 1.0.
TEXT_BRIGHT = 140          # a glyph pixel is at least this bright...
TEXT_OUTLINE_DARK = 70     # ...and touches a pixel at most this dark
MIN_LINE_INK = 8           # rows with fewer text pixels are noise
LINE_ROW_GAP = 6           # rows this close belong to the same line
MIN_LINE_HEIGHT = 10       # real lines band ~15 rows; sub-10 is noise
STRIP_PAD = 12             # slack around a band when cropping its strip

# Which line of its message a template shows, counted from the message's
# last line. The attack warning wraps: "--Warning: You are being attacked
# by" / "<attacker>!!!--", and the harvested template is the FIRST line -
# so when the warning is the newest message, that template sits one line
# above the stack's bottom. Everything else is single-line (wild_animals
# is the wrapped warning's own second line, so it IS bottom-most).
LINES_FROM_BOTTOM = {"attacked": 2}


def text_line_bands(panel_gray, scale=1.0):
    """The y-bands of the panel's text lines, top to bottom.

    A band is a contiguous run of rows containing outlined-bright text
    pixels. Measured on live panels (opaque and translucent HUD both): real
    lines band 14-20 rows tall at scale 1.0 on a ~28-row pitch, while
    terrain showing through a translucent panel produces either no band or
    sub-10-row flecks that the height filter drops.
    """
    bright = (panel_gray > TEXT_BRIGHT).astype("uint8")
    dark = (panel_gray < TEXT_OUTLINE_DARK).astype("uint8")
    near_dark = cv2.dilate(dark, np.ones((3, 3), "uint8"))
    rows = (bright & near_dark).sum(axis=1)
    inky = np.where(rows >= max(2, int(round(MIN_LINE_INK * scale))))[0]
    if len(inky) == 0:
        return []
    gap = max(1, int(round(LINE_ROW_GAP * scale)))
    min_height = max(2, int(round(MIN_LINE_HEIGHT * scale)))
    bands = []
    start = prev = int(inky[0])
    for row in inky[1:]:
        row = int(row)
        if row - prev <= gap:
            prev = row
        else:
            bands.append((start, prev))
            start = prev = row
    bands.append((start, prev))
    return [(top, bottom) for top, bottom in bands
            if bottom - top >= min_height]


def ink_agreement(template_gray, region_gray):
    """How much of the two crops' INK overlaps: IoU of their bright pixels.

    The template's ink is white by construction (harvested from white
    lines), so a fixed threshold suits it. The REGION's ink is whatever the
    game drew - attack warnings render in the attacker's player colour, and
    red text peaks near 150 in grayscale, invisible to a fixed 200 gate.
    Its threshold is relative to its own brightest pixels instead; measured
    across red and white warnings both read 0.92+, other words 0.27.
    """
    template_ink = template_gray > INK_THRESHOLD
    region_ink = region_gray > 0.7 * float(region_gray.max())
    union = (template_ink | region_ink).sum()
    if union == 0:
        return 0.0
    return float((template_ink & region_ink).sum()) / float(union)

# How long after sighting a phrase before the same phrase can count as a new
# event, in game seconds. Notifications linger ~10s; 15 adds margin.
COOLDOWN_SECONDS = 15


def load_phrase_templates():
    """Load {phrase_name: grayscale template} from templates/notifications/.

    Phrases are harvested from capture frames (the exact pixels the game
    drew), not rendered from a font - so they match the game's own
    anti-aliasing and outline exactly.
    """
    templates = {}
    for path in sorted(glob.glob(str(paths.TEMPLATES_DIR / "notifications"
                                     / "*.png"))):
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is not None:
            templates[os.path.splitext(os.path.basename(path))[0]] = image
    return templates


def panel_region(frame_width, frame_height):
    """The notification feed's box for a frame this size: (x1, y1, x2, y2)."""
    return (0, int(frame_height * PANEL_TOP),
            int(frame_width * PANEL_RIGHT), int(frame_height * PANEL_BOTTOM))


class NotificationWatcher:
    """Watches the feed and reports each phrase once per appearance.

    Only the stack's NEWEST line can fire - a phrase is searched for solely
    in the strip around its allowed band (the bottom line, or one above for
    the wrapped attack warning). Sightings anywhere else are redisplayed
    history and do not exist to this class: they neither fire nor touch the
    cooldown, so an echo cannot block a real later event from firing.
    """

    def __init__(self):
        self.templates = load_phrase_templates()
        # Phrase -> game time it was last sighted in its allowed band.
        # A line that sits at the bottom is re-sighted every poll, which
        # keeps refreshing this clock - so it fires exactly once, however
        # long it lingers. Only a bottom-line sighting a full cooldown
        # after the previous one is a new event.
        self._last_fired = {}

    def watch(self, panel_bgr, scale, game_time):
        """Look at the feed once. Returns phrase names newly sighted.

        panel_bgr is the panel_region crop of the frame. scale is the HUD
        scale from the anchor, so the templates (harvested at scale 1.0)
        stay matched if the player changes UI scale. game_time drives the
        cooldown - game seconds, so pauses do not eat the window.
        """
        if panel_bgr is None or panel_bgr.size == 0 or game_time is None:
            return []

        panel_gray = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2GRAY)
        bands = text_line_bands(panel_gray, scale)
        if not bands:
            return []

        events = []
        for name, template in self.templates.items():
            lines_up = LINES_FROM_BOTTOM.get(name, 1)
            if len(bands) < lines_up:
                continue
            top, bottom = bands[-lines_up]

            sized = template
            if abs(scale - 1.0) > 0.02:
                sized = cv2.resize(template, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_AREA)
            # The band is the line's glyph CORE; the template also carries
            # outline and padding rows around it, so the strip needs at
            # least the template's overhang on top of the fixed slack.
            pad = max(int(round(STRIP_PAD * scale)),
                      sized.shape[0] - (bottom - top))
            strip = panel_gray[max(0, top - pad):bottom + pad]
            if (sized.shape[0] > strip.shape[0]
                    or sized.shape[1] > strip.shape[1]):
                continue
            scores = cv2.matchTemplate(strip, sized, cv2.TM_CCOEFF_NORMED)
            _, score, _, where = cv2.minMaxLoc(scores)
            if score < MIN_PHRASE_SCORE:
                continue
            region = strip[where[1]:where[1] + sized.shape[0],
                           where[0]:where[0] + sized.shape[1]]
            if ink_agreement(sized, region) < MIN_INK_AGREEMENT:
                continue

            fired = self._last_fired.get(name)
            is_new = fired is None or game_time - fired >= COOLDOWN_SECONDS
            self._last_fired[name] = game_time
            if is_new:
                events.append(name)
        return events

    def reset(self):
        """Forget sightings. Call when a new game starts."""
        self._last_fired.clear()
