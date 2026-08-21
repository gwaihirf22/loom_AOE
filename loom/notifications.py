"""
Loom — reading the game's own notification feed.

The game announces events as text on the left side of the screen:
"--Town Center Built--", "--Villager Created--", research completions, and
(in the player's colour) attack warnings. This is the one place the HUD
states facts as WORDS, and for some facts it is the only exact source there
is: the global queue can only ever suggest how many Town Centres exist, but
"--Town Center Built--" states it outright.

Recognition is phrase-level template matching: the font, size and wording
are fixed, and the vocabulary Loom cares about is tiny, so grayscale
templates harvested from capture frames (in templates/notifications/) beat
anything cleverer. The white outlined text scores ~0.9 against its own
phrase and nothing else comes close.

One template per phrase is not enough, though, and believing it was cost two
phantom Town Centres in a live game. The game does not draw this feed by
scaling a single master: at 1920x1080 it lays the text out at a smaller
point size, and those glyph shapes are its own rather than a shrunken copy
of the 1440p ones. Matching the 1440p harvest resized down compares against
a shape the screen never drew - the digit-template lesson in another band -
and the ink gate quietly stopped passing. So a phrase carries one template
PER RENDERING, each named with the anchor scale it was cut at; see
load_phrase_templates, MIN_INK_AGREEMENT, and tools/cut_phrase_template.py
for how to add the next one.

Why that mattered so much is worth keeping in view: every echo guard below
is built on "was this phrase sighted on the previous look". They are sound
reasoning from reliable sightings and worthless without them, so anything
that halves detection does not halve the damage - it removes the guards
altogether. Detection reliability IS the anti-echo mechanism.

EVERY TIMING BELOW IS CONDITIONAL ON A GAME SETTING, and that was not
written down until it had already cost something. The game lets the player
choose how long notifications stay on screen (Options -> Interface), and
every measurement here - COOLDOWN_SECONDS, REARM_SECONDS,
EXTRA_QUIET_SECONDS, and the "about ten seconds" below - was taken with it
at its SHORTEST. A longer setting does not just stretch those numbers, it
changes the behaviour they describe: lines linger, so more real events go
unannounced (the game will not reprint a line that is still up), and the
feed fades less often, so history is redisplayed less often too. The docs
ask players to use the shortest setting for that reason. Nothing measures
or enforces it yet; the roadmap has the shape that would.

A notification lingers for around ten seconds and scrolls as newer lines
arrive, so one event is sighted on many consecutive polls. The watcher
reports each phrase once per appearance, and the rule for "another
appearance" is the game's own: it does not reprint a phrase whose line is
still on screen, so a sighting is a new event exactly when the line already
counted has PROVABLY left. Nothing else is needed - a lingering line never
proves absence, so it cannot re-fire however long it stays or however often
it is looked at.

That replaced a cooldown, and the cooldown's failure is worth keeping
because it was invisible: it was measured from the last SIGHTING and
refreshed on every one, so a second Town Centre's own line kept pushing the
window forward and the event was not delayed but LOST. Two Town Centres
whose lines were sixteen game-seconds apart - far outside any cooldown -
counted as one. What makes absence the better rule is that it is a fact
about the game rather than a timer over the reader.

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
# pixels between the template and the matched region). Exists as defence in
# depth: a mistaken TC count poisons the idle logic for a whole game, so
# this channel gets two independent checks.
#
# It is a NARROWER gate than it looks, and the "0.94+" this comment used to
# claim was never true of the corpus. Re-measured across the live fixtures
# and two capture runs: a real sighting reads 0.55-0.76 against a template
# harvested at its own rendering, and other text reads <= 0.38. Real, but
# not the open water the old number described.
#
# It is also asymmetric by construction - the template is thresholded at a
# fixed 200, the region relative to its own brightest pixel - so it decays
# as a template is resized away from its harvest size: shrinking averages
# the white core into the black outline and the template's own ink set
# thins out. Measured on town_center_built, the DIMMEST of the three
# harvests: compared against ITSELF it scores 0.72 at scale 1.0 and 0.598
# at 0.735, i.e. under this gate. At 1920x1080 a perfect, noise-free match
# could not pass, live detection was 23 looks in 46, and since every guard
# in watch() is built on "was it sighted last look", one lingering line
# fired three times and minted two phantom Town Centres.
#
# The fix is a template per rendering (see load_phrase_templates), not a
# looser gate. Thresholding both sides relatively was tried and measured
# WORSE on real pixels - 0.50-0.59 against 0.55-0.76 - because self-
# agreement is not the quantity that matters here; template against screen
# is. Whatever else changes, a template resized to the HUD scale in front
# of it must still clear this gate against itself, and there is a test.
MIN_INK_AGREEMENT = 0.6
INK_THRESHOLD = 200

# Finding the stack's text lines. Game text is bright with a black outline,
# so a text pixel is a bright pixel NEXT TO a very dark one - plain
# brightness alone fails on the translucent HUD, where sunlit snow behind
# the panel is as bright as the font. The bright gate sits at 140 because
# attack warnings render in the attacker's player colour and red text
# peaks near 176 in grayscale. Both are grey LEVELS, so neither scales.
TEXT_BRIGHT = 140          # a glyph pixel is at least this bright...
TEXT_OUTLINE_DARK = 70     # ...and touches a pixel at most this dark
STRIP_PAD = 12             # slack around a band when cropping its strip

# The feed's line pitch in reference pixels - the distance from one message
# to the next. Measured off the panel at both sizes: 21 rows at anchor scale
# 0.735 and 28 at 0.98, i.e. 28 * scale. Every row measurement below is a
# fraction of it, because the one thing that reliably sets the size of a
# text line is the size of the text.
LINE_PITCH = 28.0

# How much of a row must be inked before it can belong to a line, as a
# fraction of the PANEL'S WIDTH. That dimension is the correction: this is
# a count of inked COLUMNS, so it has to scale with how many columns there
# are - which follows the frame, not the HUD slider. It used to be eight
# pixels times the HUD scale, which at 1920x1080 lowered the noise floor to
# six exactly where the panel is smaller and terrain speckle is relatively
# bigger. That is how bands appeared BELOW the feed (rows 123 and 141 of a
# 162-row panel) and above it (row 8), and every positional guard in
# watch() reads bands[-1] as "the newest line".
MIN_LINE_INK_FRACTION = 0.02

# Row spacing within one line, minimum height of a line, and the margin the
# darkness gate samples - all fractions of the pitch.
LINE_ROW_GAP_FRACTION = 0.20
MIN_LINE_HEIGHT_FRACTION = 0.30
BOX_MARGIN_FRACTION = 0.15

# A band taller than this many pitches is more than one line fused together
# and is split at the valleys of its row-ink profile. Fusion is not rare at
# 1920x1080 and it is not harmless: three visible lines were read as two
# (one 35-row band across two slots) and five as four (a 74-row band across
# three). A fused band shifts every line's index, so a redisplayed echo
# sitting at the TOP of the stack was reported as one line up from the
# bottom - a band the phrase is allowed to fire from - and counted as a
# Town Centre that was never built.
#
# Where 1.1 comes from: band heights are sharply bimodal. Measured over
# three capture runs at both resolutions, 177 of 220 accepted bands are
# 0.5 pitches tall and the single-line tail reaches 0.8; a fused PAIR runs
# from the top of one line to the bottom of the next, which is one pitch
# plus one line, about 1.5. The gap between 0.8 and 1.5 is where this sits.
# It was 1.6 first, chosen by eye, and that let a 32-row pair through
# against a 32.9-row threshold - the kind of margin that is really a coin
# toss.
FUSED_BAND_PITCHES = 1.1

# The test that a band is text on the notification box rather than scenery
# behind it: the fraction of pixels around it that are near-black. The box
# is drawn UNDER the text, so a real line sits on a lot of dark; terrain
# showing through the translucent HUD has bright pixels next to dark ones
# all day but no box. Measured per band across four capture runs, both
# skins and both resolutions:
#
#   real text lines             0.37 - 0.91
#   terrain, speckle, panel edges  0.01 - 0.23
#
# and of the bands tall enough to survive the height filter at all, the
# terrain ones read 0.14 or less. 0.30 sits in open water both ways.
# Checked against the case most likely to break it: Anne_HK with the
# TRANSPARENT UI mod reads a minimum of 0.37 and passes on every band, so
# that mod clears the border artwork and not the feed's own box. Stock at
# 1080p reads 0.53 at worst.
#
# glyphs.find_lines has the same idea at 0.10, which is too low - it passes
# the 0.15-0.23 that the panel's own top and bottom edges read.
MIN_BOX_DARKNESS = 0.30

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


def _ink_rows(panel_gray):
    """How many outlined-bright pixels each row of the panel holds.

    Bright NEXT TO near-black, not merely bright: sunlit terrain behind a
    translucent panel is as bright as the font, but it has no outline.
    """
    bright = (panel_gray > TEXT_BRIGHT).astype("uint8")
    dark = (panel_gray < TEXT_OUTLINE_DARK).astype("uint8")
    near_dark = cv2.dilate(dark, np.ones((3, 3), "uint8"))
    return (bright & near_dark).sum(axis=1)


def _split_fused_band(rows, top, bottom, pitch, min_height):
    """One band back into the lines it fused, cut at its quietest rows.

    A message boundary is where the row-ink profile dips, and the dip is
    shallow rather than empty - descenders and the box's own texture keep
    it inked - so the cut goes to the LOWEST row near where the pitch says
    a boundary should be, instead of waiting for the profile to reach zero.
    """
    height = bottom - top
    if height <= pitch * FUSED_BAND_PITCHES:
        return [(top, bottom)]
    # n lines fused span (n - 1) pitches plus one line of text, and a line
    # is about half a pitch - so n is height/pitch + 0.5, not height/pitch.
    # Rounding the ratio alone reads a three-line fusion (2.5 pitches) as
    # two, and Python rounds 2.5 DOWN, so it did.
    lines = max(2, int(round(height / pitch + 0.5)))
    step = height / lines
    reach = max(1, int(round(pitch * 0.25)))
    cuts = [top]
    for index in range(1, lines):
        target = top + int(index * step)
        low = max(top + min_height, target - reach)
        high = min(bottom - min_height, target + reach)
        if low >= high:
            continue
        cuts.append(min(range(low, high), key=lambda row: rows[row]))
    cuts.append(bottom)
    return list(zip(cuts, cuts[1:]))


def text_line_bands(panel_gray, scale=1.0):
    """The y-bands of the panel's text lines, top to bottom.

    A band is a contiguous run of rows holding outlined-bright pixels, then
    two corrections that the 1920x1080 panel forced - both of them the same
    mistake, which was measuring rows of a small panel with numbers taken
    off a big one.

      * Lines FUSE. Two messages 21 rows apart, each 12 rows of text, leave
        a gutter that the box's own texture inks right through, so they
        arrive as one band. Anything too tall to be a single line is cut
        back apart at the pitch (see FUSED_BAND_PITCHES) - it has to be,
        because a fused band silently renumbers every line beneath it and
        the whole watcher is written in terms of "one line up from the
        bottom".
      * TERRAIN fakes a line. It is bright and it is next to dark, and
        below the feed there is nothing else to disagree with it. What
        separates them is the notification box: real text is drawn ON it,
        so it sits on near-black, and scenery does not (MIN_BOX_DARKNESS).

    Returns inclusive (top, bottom) row pairs, densely, top to bottom.
    Callers index this list from the END - bands[-1] is the newest line -
    and an EMPTY list is read by watch() as proof the feed is blank, which
    is stronger than "nothing recognised". Returning [] with text on screen
    would mint phantom events, so it is the one answer never to guess at.
    """
    pitch = LINE_PITCH * scale
    rows = _ink_rows(panel_gray)
    floor = max(2, int(round(panel_gray.shape[1] * MIN_LINE_INK_FRACTION)))
    inky = np.where(rows >= floor)[0]
    if len(inky) == 0:
        return []

    gap = max(1, int(round(pitch * LINE_ROW_GAP_FRACTION)))
    min_height = max(2, int(round(pitch * MIN_LINE_HEIGHT_FRACTION)))
    margin = max(1, int(round(pitch * BOX_MARGIN_FRACTION)))

    runs = []
    start = prev = int(inky[0])
    for row in inky[1:]:
        row = int(row)
        if row - prev <= gap:
            prev = row
        else:
            runs.append((start, prev))
            start = prev = row
    runs.append((start, prev))

    bands = []
    for top, bottom in runs:
        for piece_top, piece_bottom in _split_fused_band(
                rows, top, bottom, pitch, min_height):
            if piece_bottom - piece_top < min_height:
                continue
            # The box proves itself AROUND the ink as much as under it -
            # a row of glyphs can be wall-to-wall bright on its own.
            box = panel_gray[max(0, piece_top - margin):
                             piece_bottom + margin + 1]
            if box.size and (box < TEXT_OUTLINE_DARK).mean() \
                    >= MIN_BOX_DARKNESS:
                bands.append((piece_top, piece_bottom))
    return bands


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

# How long a phrase must be missing from its allowed bands before its
# cooldown clears (see _tick_absence in the watcher). The game never
# reprints a line that is already on screen, so a reprint after real
# absence IS a new event however recent the last one - while a lingering
# line is sighted every look and should never be able to rearm itself,
# which is what makes this safe where shortening the cooldown was not
# (measured: a 0.3s cooldown refired one lingering line thirteen times).
#
# BOTH conditions, and the second one was learned the hard way. "Three
# looks is about a second of real time" was the original reasoning, and it
# is not true: looks are as dense as the poll loop makes them, and a
# handful of consecutive misreads is one bad moment rather than a line
# leaving the screen. Traced frame by frame on a 1920x1080 game - the TC
# line arrived and fired correctly, was then missed on four consecutive
# looks while plainly still on screen, and the third of those cleared a
# five-second-old cooldown so the next sighting fired a second, imaginary
# Town Centre. A count of looks cannot tell "gone" from "not seen".
#
# What separates the two is how much the feed itself is saying. A BLANK
# feed is proof: no text at all means this line is certainly gone, and
# nothing about template matching can argue with an empty panel. Text on
# screen WITHOUT this phrase found in it is only evidence, because "not
# found" and "not there" are the same answer from a matcher having a bad
# moment - and in the traced failure the feed was showing two to three
# lines throughout.
#
# So a blank feed rearms on the look count alone, as it always did, and
# an unrecognised phrase in a populated feed must ALSO have been missing
# for REARM_SECONDS of game time.
#
# That number is set conservatively, and the reason is worth stating
# plainly rather than dressing up: the two things it has to tell apart
# OVERLAP on the data I have. Measured on the 1080p run that produced the
# phantom, reading the game's own clock rather than assuming a frame
# rate - the first attempt at this assumed one and was wrong by a factor
# of three - detection flicker hid a line that was plainly on screen for
# 7 game seconds. A genuinely new Town Centre line arriving 16 seconds
# after the previous one fired leaves a gap of 8. Seven and eight; no
# threshold splits those.
#
# So this is deliberately above BOTH, which means the time route almost
# never fires and the real work is done by the cooldown (any line more
# than COOLDOWN_SECONDS after the last fire) and by the blank-feed proof.
# The cost is a known undercount: two Town Centres whose lines fall
# inside the cooldown, with the first pushed off early, count as one.
# That is the direction this module has always chosen to fail in - an
# imaginary Town Centre nags for the rest of the game, a missed one costs
# one alert - but it is a real cost and it is NOT settled. Deciding it
# needs captures of several Town Centres built close together, which is
# what tools/replay_notifications.py --sweep exists to measure.
REARM_LOOKS = 3
REARM_SECONDS = 12


def load_phrase_templates():
    """Load {phrase_name: [(template, harvest_scale), ...]} from
    templates/notifications/.

    Phrases are harvested from capture frames (the exact pixels the game
    drew), not rendered from a font - so they match the game's own
    anti-aliasing and outline exactly.

    A phrase may have SEVERAL templates, one per rendering, because the game
    does not draw this feed by scaling a single master: at 1920x1080 it lays
    the text out at a smaller point size and the glyph shapes are its own.
    Resizing the 1440p harvest down compares against a shape the screen never
    drew, which is the digit-template lesson in another band, and it cost
    exactly what that costs - see the module docstring. So the file name
    carries the anchor scale the pixels were cut at ("name@0.745.png"), a
    bare "name.png" means 1.0, and _template_at resizes from there.

    A file whose suffix after "@" is not a number is ignored rather than
    guessed at: a wrong harvest scale would silently mis-size every match.
    """
    templates = {}
    for path in sorted(glob.glob(str(paths.TEMPLATES_DIR / "notifications"
                                     / "*.png"))):
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        name, _, harvested_at = stem.partition("@")
        try:
            harvest_scale = float(harvested_at) if harvested_at else 1.0
        except ValueError:
            continue
        templates.setdefault(name, []).append((image, harvest_scale))
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
        # Phrase -> game time its current absence began, so the rearm can
        # ask how long it has been gone rather than only how many looks
        # failed to recognise it.
        self._absent_since = {}
        # Phrases whose line has PROVABLY left the screen since they last
        # fired, and which may therefore fire again. This is the whole
        # re-fire rule; see the fire decision in watch().
        self._absent_proved = set()

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
            self._tick_absence(set(), game_time, True)   # blank: proof
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

            # A sighting is a NEW event only if the line I already counted
            # has since provably left the screen (_tick_absence decides
            # what counts as proof). The game does not reprint a phrase
            # whose line is still up, so this is the game's own rule stated
            # directly, and it needs no cooldown behind it: a lingering
            # line never proves absence, so it can never re-fire however
            # long it lingers or however often it is looked at.
            #
            # The cooldown this replaces was measured from the last
            # SIGHTING and refreshed on every one, which quietly made a
            # second Town Centre unreportable: its own line kept pushing
            # the window forward, so the fifteen seconds never elapsed and
            # the event was lost rather than delayed. Measured on two
            # Town Centres whose lines were 16 game-seconds apart - well
            # outside any cooldown - the second one was never counted.
            # Three independent reasons a sighting can be a NEW event, and
            # each covers a case the others miss.
            #
            #   never fired          - the first one, obviously.
            #   cooldown elapsed     - the original rule. A line sighted in
            #                          a band it may fire from refreshes
            #                          this clock below, so a lingering
            #                          line can never reach it; an echo
            #                          does NOT reach the refresh, so a
            #                          real event after an echo still
            #                          counts.
            #   provably gone        - the line I counted has left the
            #                          screen (see _tick_absence). The game
            #                          does not reprint a phrase whose line
            #                          is still up, so a sighting after
            #                          real absence is a different line
            #                          however recently the last one fired.
            #
            # That last one is not a nicety. Without it a second Town
            # Centre inside the cooldown is not delayed but LOST, because
            # its own line refreshes the clock on every look and the
            # fifteen seconds never elapse. Measured on two Town Centres
            # whose lines were 16 game-seconds apart - well outside the
            # cooldown - the second was never counted at all.
            fired = self._last_fired.get(name)
            is_new = (fired is None
                      or game_time - fired >= COOLDOWN_SECONDS
                      or name in self._absent_proved)
            self._last_fired[name] = game_time
            self._absent_proved.discard(name)
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
        self._tick_absence(names_on_screen, game_time, False)
        return events

    def _tick_absence(self, names_on_screen, game_time, feed_blank):
        """Advance each phrase's gone-from-the-feed streak, and rearm it
        once the feed has PROVED the line is gone.

        ANY sighting resets the streak - trusted, roll-up, or echo
        suspect - because all of them mean the line's pixels are still on
        screen, and only true absence proves the game is free to print
        the phrase again.

        What counts as proof depends on what else the feed is showing; see
        REARM_SECONDS. A blank panel settles it outright. A phrase merely
        not recognised among other lines does not, and treating those two
        as the same thing is what minted a phantom Town Centre out of four
        consecutive misreads.
        """
        for name in self.templates:
            if name in names_on_screen:
                self._absent_looks[name] = 0
                self._absent_since.pop(name, None)
                continue
            gone = self._absent_looks.get(name, 0) + 1
            self._absent_looks[name] = gone
            since = self._absent_since.setdefault(name, game_time)
            really_gone = (feed_blank
                           or game_time - since >= REARM_SECONDS)
            if gone >= REARM_LOOKS and really_gone:
                self._absent_proved.add(name)

    def _phrase_in_band(self, panel_gray, band, name, scale):
        """Is this phrase's line drawn in this band?

        Tries the template at a small bracket of scales around the anchor's
        measurement (see SCALE_BRACKET) and accepts the first size that
        clears both the correlation gate and the ink gate.
        """
        top, bottom = band
        for index in self._variants_near(name, scale):
            for delta in SCALE_BRACKET:
                sized = self._template_at(name, index, scale + delta)
                # The band is the line's glyph CORE; the template also
                # carries outline and padding rows around it, so the strip
                # needs at least the template's overhang on top of the fixed
                # slack.
                pad = max(int(round(STRIP_PAD * scale)),
                          sized.shape[0] - (bottom - top))
                strip = panel_gray[max(0, top - pad):bottom + pad]
                if (sized.shape[0] > strip.shape[0]
                        or sized.shape[1] > strip.shape[1]):
                    continue
                scores = cv2.matchTemplate(strip, sized,
                                           cv2.TM_CCOEFF_NORMED)
                _, score, _, where = cv2.minMaxLoc(scores)
                if score < MIN_PHRASE_SCORE:
                    continue
                region = strip[where[1]:where[1] + sized.shape[0],
                               where[0]:where[0] + sized.shape[1]]
                if ink_agreement(sized, region) >= MIN_INK_AGREEMENT:
                    return True
        return False

    def _variants_near(self, name, scale):
        """This phrase's template indices, closest harvest scale first.

        Closest first because _phrase_in_band stops at the first variant
        that clears both gates, and the nearest rendering is both the most
        likely to clear them and the cheapest to resize.
        """
        variants = self.templates[name]
        return sorted(range(len(variants)),
                      key=lambda index: abs(variants[index][1] - scale))

    def _template_at(self, name, index, scale):
        """One of a phrase's templates, resized to one scale, cached.

        Resizing is from the template's OWN harvest scale, not from 1.0: a
        variant cut at 0.745 is already the right size for a 0.745 HUD and
        must not be shrunk again. When it is already right, it is used
        untouched - resampling a template to the size it already is only
        costs it sharpness, and sharpness is what ink_agreement measures.
        """
        key = (name, index, round(scale, 3))
        sized = self._sized.get(key)
        if sized is None:
            image, harvest_scale = self.templates[name][index]
            factor = scale / harvest_scale
            if abs(factor - 1.0) < 0.005:
                sized = image
            else:
                # INTER_AREA is the right filter for shrinking and a poor
                # one for growing, where it degenerates towards nearest
                # neighbour and hands the matcher stair-stepped outlines.
                sized = cv2.resize(
                    image, None, fx=factor, fy=factor,
                    interpolation=(cv2.INTER_AREA if factor < 1.0
                                   else cv2.INTER_CUBIC))
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
        self._absent_since.clear()
        self._absent_proved.clear()
        self._last_fireable.clear()
