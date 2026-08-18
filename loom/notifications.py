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
lines. So a phrase normally fires only when it is the panel's bottom-most
text line. One measured exception earns a guarded second look: two events
finishing in the same redraw share one arrival, and the elder of the pair
(a Town Centre, in the game that proved it) lands one line up without ever
having been the bottom. watch() fires that line only when the feed was
already showing text and the phrase was not sighted a poll earlier - a
redisplay-after-fade fails both tests. The remaining accepted cost is an
undercount: an event whose line arrives one-up right after a fade is never
counted. A missed TC costs a missed idle alert; an imaginary TC nags for
the rest of the game.
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

# Extra lines a phrase may ALSO fire from, with the fresh-arrival guard.
# A Town Centre finishing in the same feed redraw as another event (measured
# live: TC #3 completed alongside "--Heavy Plow Research Complete--", which
# took the bottom line) is never the bottom-most text line, so the bottom
# rule alone loses it. Searching one line up is only safe with the guard in
# watch(): redisplayed history also sits one above the bottom, and an
# unguarded second line would mint imaginary TCs from echoes again.
EXTRA_LINES = {"town_center_built": (2,)}

# How long since the phrase was last seen in a FIREABLE band before an
# extra-band sighting may count as a fresh arrival. The feed redisplays
# history above any new message - without a fade first, measured live: a
# TC line expired individually out of a busy feed and resurfaced one-up
# 17 game-seconds later under a newer line, indistinguishable per-look
# from a sibling arrival. The cadence separates them: an echo resurfaces
# within seconds of the sightings it echoes (17s measured), while a real
# completion arriving one-up follows a long fireable-band silence (81s
# measured in the game that proved the sibling case). Sightings further
# up the stack do not reset this clock - they carry their own vetoes
# (the visible-last-look check, and the fade guard for blank feeds).
EXTRA_QUIET_SECONDS = 60

# The scales tried around the anchor's measurement when sizing a template.
# The anchor measures the ICON's scale to about half a percent, but the HUD
# font does not track the icon exactly - at a measured 0.98 the TC phrase
# matched best with the template sized to 0.99. A long template is what
# makes this matter: across 233px, a 1% size error shifts the far glyphs
# two pixels off and TM_CCOEFF_NORMED bleeds score for every misaligned
# outline - measured on a live line, the score fell 0.89 -> 0.77 between
# 0.99 and 0.98, so the steps here must be no coarser than 0.01. Five
# tries cost single-digit milliseconds a poll, worst case.
SCALE_BRACKET = (-0.02, -0.01, 0.0, 0.01, 0.02)


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

# How many consecutive looks a phrase must be missing from its allowed
# bands before its cooldown clears (see _tick_absence in the watcher).
# The game never reprints a line that is already on screen, so a reprint
# after real absence IS a new event however recent the last one - while a
# lingering line is sighted every look and can never rearm itself, which
# is what makes this safe where shortening the cooldown was not
# (measured: a 0.3s cooldown refired one lingering line thirteen times).
# Three looks is about a second of real time, so one misread glance
# cannot rearm a line that is still on screen.
REARM_LOOKS = 3


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

    A phrase normally fires only from its PRIMARY band - the bottom line,
    or one above for the wrapped attack warning. Phrases in EXTRA_LINES get
    a second chance one line further up, guarded so that redisplayed
    history cannot fire: an extra-band sighting counts only as either a
    roll-up of a line already being watched (which never re-fires) or a
    fresh arrival into a feed that was already showing text. An
    echo-suspect sighting neither fires nor touches the cooldown, so it
    cannot block a real later event from firing.
    """

    def __init__(self):
        self.templates = load_phrase_templates()
        # Phrase -> game time it was last sighted in a band I trust.
        # A line that lingers is re-sighted every look, which keeps
        # refreshing this clock - so it fires exactly once, however long it
        # stays. Only a sighting a full cooldown after the previous one is
        # a new event.
        self._last_fired = {}
        # Templates resized once per (phrase, scale), not once per poll.
        self._sized = {}
        # What the previous look saw, for the fresh-arrival guard: which
        # phrases were sighted in trusted bands, which were seen only as
        # echo suspects, and whether the feed was showing any text at all.
        # "Previous look", not "previous poll" - under load shedding looks
        # are sparser than polls, and a lingering line is still there on
        # the next look either way.
        self._sighted = set()
        self._suspect = set()
        self._feed_visible = False
        # Every phrase seen ANYWHERE in the stack on the previous look -
        # fireable bands or rolled further up. The game never reprints a
        # line that is still visible, so presence here vetoes the
        # fresh-arrival path outright.
        self._visible = set()
        # Phrase -> game time it was last seen in a band it may fire
        # from. An extra-band "fresh arrival" must follow
        # EXTRA_QUIET_SECONDS of such silence, or it is an echo
        # resurfacing (see that constant).
        self._last_fireable = {}
        # Consecutive looks each phrase has been missing from its allowed
        # bands. The game never reprints a line that is already on screen,
        # so a reprint after real absence IS a new event - once a phrase
        # has been gone this many looks, its cooldown clears and the next
        # arrival fires immediately. A lingering line is sighted every
        # look and so can never rearm itself, which is what makes this
        # safe where shortening the cooldown was not (measured: a 0.3s
        # cooldown refired one lingering line thirteen times).
        self._absent_looks = {}

    def watch(self, panel_bgr, scale, game_time):
        """Look at the feed once. Returns phrase names newly sighted.

        panel_bgr is the panel_region crop of the frame. scale is the HUD
        scale from the anchor, so the templates (harvested at scale 1.0)
        stay matched if the player changes UI scale. game_time drives the
        cooldown - game seconds, so pauses do not eat the window.

        A name appears TWICE in the result when two distinct lines carry
        the same phrase at once - two Town Centres finishing together are
        two events, and callers count occurrences, not membership.
        """
        if panel_bgr is None or panel_bgr.size == 0 or game_time is None:
            return []

        panel_gray = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2GRAY)
        bands = text_line_bands(panel_gray, scale)
        feed_was_visible = self._feed_visible
        self._feed_visible = bool(bands)
        visible_last_look = self._visible
        if not bands:
            self._sighted = set()
            self._suspect = set()
            self._visible = set()
            self._tick_absence(set())       # a blank feed is absence too
            return []

        events = []
        sighted_now = set()
        suspect_now = set()
        names_on_screen = set()
        names_fireable = set()
        for name in self.templates:
            primary = LINES_FROM_BOTTOM.get(name, 1)
            hits = set()
            for lines_up in (primary, *EXTRA_LINES.get(name, ())):
                if len(bands) >= lines_up and self._phrase_in_band(
                        panel_gray, bands[-lines_up], name, scale):
                    hits.add(lines_up)
            if not hits:
                # No hit in a band this phrase may FIRE from - but a line
                # rolled further up the stack is still ON SCREEN, and only
                # true absence may open the cooldown. Without this check, a
                # TC line pushed to the third band of a busy feed counted
                # as absent, rearmed after three looks, and re-fired when
                # the churn brought it back into range - a sandbox game
                # minted four phantom TCs from that loop alone, one every
                # ~10 seconds inside a single lingering line's lifetime.
                searched = {len(bands) - lines_up
                            for lines_up in (primary,
                                             *EXTRA_LINES.get(name, ()))
                            if len(bands) >= lines_up}
                if any(self._phrase_in_band(panel_gray, band, name, scale)
                       for position, band in enumerate(bands)
                       if position not in searched):
                    names_on_screen.add(name)
                continue
            names_on_screen.add(name)
            names_fireable.add(name)

            # An extra-band hit comes in three shapes, and only one may
            # fire. A FRESH ARRIVAL: the phrase was nowhere on screen last
            # look - not in a fireable band, not rolled further up - and
            # the feed never went blank: a genuine event whose line was
            # born one-up because a sibling event took the bottom. A
            # ROLL-UP: the line was already being watched and newer lines
            # pushed it one up - the same event; it neither fires nor
            # touches the cooldown, so it cannot block a real successor at
            # the bottom. An ECHO SUSPECT: anything else, i.e. the phrase
            # surfaced one-up right after a fade - redisplayed history. A
            # suspect is remembered so that seeing it again next look
            # cannot launder it into a fresh arrival. The nowhere-on-screen
            # test uses ALL bands: a line that drifted above the fireable
            # bands for longer than the cooldown and then slid back looked
            # "fresh" and re-fired - the game would never reprint a phrase
            # whose line is still visible, so visible-anywhere means
            # not-new, full stop.
            primary_hit = primary in hits
            extra_hit = len(hits) > (1 if primary_hit else 0)
            was_sighted = name in self._sighted
            was_suspect = name in self._suspect
            seen_at = self._last_fireable.get(name)
            long_quiet = (seen_at is None
                          or game_time - seen_at >= EXTRA_QUIET_SECONDS)
            extra_is_fresh = (extra_hit and not was_sighted
                              and not was_suspect and feed_was_visible
                              and name not in visible_last_look
                              and long_quiet)

            if primary_hit or extra_is_fresh or was_sighted:
                sighted_now.add(name)
            elif extra_hit:
                suspect_now.add(name)
                continue

            if not (primary_hit or extra_is_fresh):
                continue                    # a roll-up: watched, silent

            fired = self._last_fired.get(name)
            is_new = fired is None or game_time - fired >= COOLDOWN_SECONDS
            self._last_fired[name] = game_time
            if is_new:
                events.append(name)
                # Bottom AND one-up at once, both freshly arrived: two
                # distinct lines stating the same fact - two events. A
                # rolled-up elder alongside a fresh bottom line does NOT
                # take this branch: it was sighted before, so it is
                # already accounted for.
                if primary_hit and extra_is_fresh:
                    events.append(name)
        self._sighted = sighted_now
        self._suspect = suspect_now
        self._visible = names_on_screen
        # Updated AFTER the loop: the freshness test above must see the
        # PREVIOUS sighting time, not this look's own.
        for name in names_fireable:
            self._last_fireable[name] = game_time
        self._tick_absence(names_on_screen)
        return events

    def _tick_absence(self, names_on_screen):
        """Advance each phrase's gone-from-the-feed streak; rearm at the
        threshold.

        ANY sighting resets the streak - trusted, roll-up, or echo
        suspect - because all of them mean the line's pixels are still on
        screen, and only true absence proves the game is free to print
        the phrase again.
        """
        for name in self.templates:
            if name in names_on_screen:
                self._absent_looks[name] = 0
                continue
            gone = self._absent_looks.get(name, 0) + 1
            self._absent_looks[name] = gone
            if gone == REARM_LOOKS:
                self._last_fired.pop(name, None)

    def _phrase_in_band(self, panel_gray, band, name, scale):
        """Is this phrase's line drawn in this band?

        Tries the template at a small bracket of scales around the anchor's
        measurement (see SCALE_BRACKET) and accepts the first size that
        clears both the correlation gate and the ink gate.
        """
        top, bottom = band
        for delta in SCALE_BRACKET:
            sized = self._template_at(name, scale + delta)
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
            if ink_agreement(sized, region) >= MIN_INK_AGREEMENT:
                return True
        return False

    def _template_at(self, name, scale):
        """The phrase template resized to one scale, cached."""
        key = (name, round(scale, 3))
        sized = self._sized.get(key)
        if sized is None:
            sized = cv2.resize(self.templates[name], None,
                               fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
            self._sized[key] = sized
        return sized

    def reset(self):
        """Forget sightings. Call when a new game starts."""
        self._last_fired.clear()
        self._sighted = set()
        self._suspect = set()
        self._visible = set()
        self._feed_visible = False
        self._absent_looks.clear()
        self._last_fireable.clear()
