"""
Loom — reading the notification font, one character at a time.

The game states events as text lines ("--Mill Built--", "--Knight
Created--") in one fixed font. Ten digit templates already read every
number the HUD can show; this module makes the same bet on the alphabet:
harvest each character's glyph once (tools/build_notification_font.py),
and every line the game can ever print becomes readable - no template per
phrase, no OCR engine, no AI.

Text isolation must survive all eight player colours (attack warnings
render in the attacker's colour). Grayscale is the wrong axis for that:
yellow text reads ~226 in luminance but pure blue reads ~29, nearly as
dark as the panel behind it. The colour-agnostic axis is the BRIGHTEST
CHANNEL - every saturated player colour drives at least one channel near
full - thresholded relative to the line's own peak, which also catches
grey, the one unsaturated colour.

The never-guess rule holds at the character level: one unclassifiable
glyph kills the whole line. A dropped line costs a stats entry; a misread
word would poison them.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import glob
import os

import cv2
import numpy as np

from . import digits, paths

FONT_DIR = paths.TEMPLATES_DIR / "notification_font"

# Below this correlation a glyph is not believed. Measured on real lines:
# a true match scores 0.99+, the best WRONG letter scores about 0.6 - so
# 0.8 sits in open water, and a line of near-misses dies rather than
# reading as plausible garbage (one did, at 0.57, before this was raised).
MIN_GLYPH_SCORE = 0.8

# One glyph per line may fall short of MIN_GLYPH_SCORE, down to this floor,
# without killing the line. A HUD rendered a hair off the harvest scale
# softens every glyph a little and occasionally drops exactly one below the
# gate (measured at a 2% resample: eighteen glyphs at 0.83-0.97, one at
# 0.76) - all-or-nothing turned that into a whole game of unread lines.
# The tolerated glyph still enters as its best-scoring label, and
# parse_event's KNOWN_WORDS vocabulary is the backstop: a wrong letter
# makes a non-word, and the line refuses at the event stage instead.
WEAK_GLYPH_FLOOR = 0.65

# A candidate and a template must have roughly similar shapes to compare at
# all: extract_glyph squashes everything to one box, which would make a
# stretched "i" impersonate an "l" - so the natural width/height ratio is
# checked first, and only templates within this factor compete.
ASPECT_TOLERANCE = 1.8

# A gap wider than this fraction of the line's height reads as a space.
# Measured: letter gaps run 1-3px, word gaps 6-8px, at line heights of
# 26-34px - so 0.15 of the height splits them cleanly.
SPACE_FRACTION = 0.15

# Runs narrower than this fraction of line height are dropped as noise
# specks (a real "i" or "l" is thin but taller than this is wide).
MIN_RUN_FRACTION = 0.08

# Every real glyph carries the font's near-black outline; bright terrain
# showing past the notification box's edge does not. A run whose darkest
# tenth is brighter than this is scenery, not text.
OUTLINE_DARKNESS = 60

# How label names map to characters, for the filename scheme. Filenames
# must survive case-insensitive filesystems (the Windows goal), so "A" and
# "a" become upper_A / lower_a rather than colliding files.
PUNCT_NAMES = {
    "-": "hyphen", ".": "period", ",": "comma", "'": "apostrophe",
    "(": "lparen", ")": "rparen", "!": "bang", "/": "slash", ":": "colon",
}
PUNCT_CHARS = {name: char for char, name in PUNCT_NAMES.items()}


def label_for(char):
    """The filesystem-safe label for one character."""
    if char.isalpha():
        return ("upper_" if char.isupper() else "lower_") + char
    if char.isdigit():
        return "digit_" + char
    if char in PUNCT_NAMES:
        return "punct_" + PUNCT_NAMES[char]
    raise ValueError(f"no label scheme for {char!r}")


def char_for(label):
    """The character (or characters) a label stands for.

    punct_dashes is the one multi-character label: the "--" framing around
    every notification renders as a single joined stroke, so it segments
    as one glyph and reads back as two characters.
    """
    if label == "punct_dashes":
        return "--"
    kind, _, name = label.partition("_")
    if kind in ("upper", "lower", "digit"):
        return name
    if kind == "punct":
        return PUNCT_CHARS[name]
    raise ValueError(f"unrecognized label {label!r}")


def load_font(directory=None):
    """The glyph set: {label: [(normalized image, aspect), ...]}.

    Variants per label, like the digit templates - the game renders the
    same character slightly differently by sub-pixel position. Returns {}
    when no font has been harvested yet; the caller treats that as
    "cannot read", never as an error.
    """
    directory = directory or FONT_DIR
    font = {}
    for path in sorted(glob.glob(str(directory / "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0]
        label = name.rsplit("_", 1)[0]     # strip the variant number
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            continue
        aspect = image.shape[1] / image.shape[0]
        # Each glyph also enters at a few slightly resampled sizes. The
        # harvested pixels are one exact rendering; the game at any other
        # HUD scale antialiases the same character differently, and that
        # alone drops match scores from 1.00 to below the 0.8 gate -
        # measured on the very line the font was harvested from, resampled
        # by 2%. Blurring the variants into the set keeps the gate strict
        # while letting a slightly softer rendering through.
        for factor in (0.96, 0.98, 1.0, 1.02, 1.04):
            source = image
            if factor != 1.0:
                source = cv2.resize(image, None, fx=factor, fy=factor,
                                    interpolation=cv2.INTER_AREA)
                if source.size == 0:
                    continue
            boxed = cv2.resize(source,
                               (digits.GLYPH_WIDTH, digits.GLYPH_HEIGHT),
                               interpolation=cv2.INTER_AREA)
            _admit(font.setdefault(label, []),
                   digits._normalize(boxed), aspect)
    return font


def _admit(variants, normalized, aspect):
    """Add a variant unless the label already holds a near-twin.

    Many resamples of the same source glyph collapse to almost the same
    normalized box, and every kept variant is paid for on every classify
    of every run of every line - the whole variant set went 5x when the
    resampled sizes were added, and reading one busy panel crossed 150ms.
    Templates are normalized, so a true twin correlates near 1.0; keeping
    only sufficiently different variants preserves the tolerance the
    resamples exist for at a fraction of their cost.
    """
    for kept, _ in variants:
        if float((kept * normalized).mean()) >= 0.985:
            return
    variants.append((normalized, aspect))


# ---- isolating the text ----------------------------------------------------


def text_mask(line_bgr):
    """White-on-black image of the line's text, any player colour.

    Brightest-channel, thresholded relative to the line's own peak: white
    text, all eight player colours, and grey all clear it, while the dark
    notification box and the terrain showing through it do not. The floor
    keeps an all-dark crop from amplifying its own noise.
    """
    peak = line_bgr.max(axis=2)
    # The absolute floor guards an all-dark crop against amplifying its own
    # noise; 100 rather than higher because the GREY player's text sits
    # near 128 and must clear it - the relative term does the real work.
    floor = max(100, 0.72 * float(peak.max()))
    return ((peak >= floor) * 255).astype(np.uint8)


# One rendered notification line is about this tall. Bands much taller
# than it are stacked or wrapped lines that must be split apart before
# reading - a fused band can never read. Measured 28 on live panels (the
# notification feed's line pitch); the 26 it used to be made the splitter
# over-count lines in tall fused bands and cut real lines in half.
NOMINAL_LINE_HEIGHT = 28

# The fraction of a band's pixels that must be near-black for it to count
# as text-on-the-notification-box. Bright terrain has highlights that pass
# the ink mask, but it has no dark box and no outline behind them.
DARK_FRACTION = 0.10
DARK_LEVEL = 70


def find_lines(panel_bgr, min_height=10):
    """Text-line bands in the notification panel: [(y1, y2), ...].

    Rows with enough inked columns, grouped - then two corrections the
    real feed forced:

    * Stacked and WRAPPED lines sit so close that their bands fuse (a
      two-line attack warning, or four messages arriving together), so a
      band much taller than one line is split again at the valleys of its
      row-ink profile.
    * Bright terrain fakes ink without a notification box behind it, so a
      band must also contain a decent share of near-black pixels (the box
      and the font's outline) or it is scenery.
    """
    mask = text_mask(panel_bgr)
    gray = cv2.cvtColor(panel_bgr, cv2.COLOR_BGR2GRAY)
    # Only ink NEXT TO the font's near-black outline counts toward a line.
    # Brightness alone is not text: sunlit terrain showing past the panel
    # clears the relative threshold easily, and a whole game of "lines"
    # found this way read as nothing but saved junk crops. A dark-adjacency
    # test is how the phrase watcher's band finder stays clean, and it is
    # the same trick here - terrain highlights have no outline behind them.
    dark = (gray < DARK_LEVEL).astype(np.uint8)
    near_dark = cv2.dilate(dark, np.ones((3, 3), np.uint8))
    outlined = mask & (near_dark * 255)
    rows = outlined.sum(axis=1) // 255
    coarse = []
    start = None
    for y, count in enumerate(rows):
        if count >= 6 and start is None:
            start = y
        elif count < 6 and start is not None:
            if y - start >= min_height:
                coarse.append((start, y))
            start = None
    if start is not None and len(rows) - start >= min_height:
        coarse.append((start, len(rows)))

    bands = []
    for y1, y2 in coarse:
        for a, b in _split_tall_band(rows, y1, y2):
            # The darkness that proves a notification box is AROUND the
            # ink as much as inside it, so the gate samples a few rows of
            # margin too - ink rows alone can be wall-to-wall bright.
            gate = gray[max(0, a - 4):min(len(rows), b + 4)]
            if gate.size == 0:
                continue
            if (gate < DARK_LEVEL).mean() < DARK_FRACTION:
                continue                     # scenery, not a boxed line
            bands.append((max(0, a - 2), min(len(rows), b + 2)))
    return bands


def _split_tall_band(rows, y1, y2, min_height=10):
    """Split one fused band at the valleys of its row-ink profile."""
    height = y2 - y1
    if height <= NOMINAL_LINE_HEIGHT * 1.6:
        return [(y1, y2)]
    lines = max(2, round(height / NOMINAL_LINE_HEIGHT))
    approx = height / lines
    cuts = [y1]
    for index in range(1, lines):
        # The valley nearest the expected boundary: line gaps have the
        # least ink even when they never reach zero.
        target = y1 + int(index * approx)
        lo = max(y1 + min_height, target - 6)
        hi = min(y2 - min_height, target + 6)
        if lo >= hi:
            continue
        valley = min(range(lo, hi), key=lambda y: rows[y])
        cuts.append(valley)
    cuts.append(y2)
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b - a >= min_height]


def segment_line(line_bgr):
    """One line into per-character crops: (mask, [(start, end), ...]).

    Shared by the reader and the harvest tool, so the glyphs the font was
    built from segment exactly like the glyphs read at runtime.

    Two filters beyond the raw column runs: width (specks are not
    characters) and OUTLINE (real glyphs carry the font's near-black
    outline; bright terrain past the notification box's edge does not, and
    it otherwise segments into convincing phantom runs).
    """
    mask = text_mask(line_bgr)
    gray = cv2.cvtColor(line_bgr, cv2.COLOR_BGR2GRAY)
    height = mask.shape[0]
    min_run = max(2, int(height * MIN_RUN_FRACTION))

    runs = []
    for start, end in digits.find_column_runs(mask):
        if end - start < min_run:
            continue
        margin = 2
        region = gray[:, max(0, start - margin):end + margin]
        if float(np.percentile(region, 10)) > OUTLINE_DARKNESS:
            continue
        # No pre-emptive splitting of wide runs here: read_line splits a
        # run only when reading it whole has FAILED and every piece then
        # classifies - splitting first once carved an "m" into "ln".
        runs.append((start, end))
    return mask, runs


# A run wider than this fraction of line height is suspected of being two
# touching letters. Wide single glyphs stay under it: "m" and the joined
# "--" both measure ~0.65 of the height; merged pairs measure 0.85+.
MERGED_RUN_FRACTION = 0.85


def _pinch_split(mask, start, end, height):
    """Split a suspiciously wide run at its thinnest column, recursively.

    Touching letters ("ey", "rs") arrive as one run; the seam between them
    is a pinch - a column with far less ink than the run's average. A wide
    glyph with no real pinch (a "w") is left whole. Geometry only: the
    caller decides whether the split's READING is acceptable.
    """
    width = end - start
    if width < height * MERGED_RUN_FRACTION:
        return [(start, end)]
    columns = mask[:, start:end].sum(axis=0) / 255
    centre = columns[width // 4: width - width // 4]
    if len(centre) == 0:
        return [(start, end)]
    pinch = int(np.argmin(centre)) + width // 4
    if columns[pinch] > 0.4 * columns.mean():
        return [(start, end)]
    return (_pinch_split(mask, start, start + pinch, height)
            + _pinch_split(mask, start + pinch, end, height))


def _read_split(mask, start, end, font):
    """Read one failed run as touching letters, or refuse.

    The bar is deliberately high: the split only counts if it actually
    produced MORE than one piece and EVERY piece classifies confidently.
    A real "m" survives because reading it whole succeeds long before
    this is reached; a real merged "ey" arrives here having failed whole,
    splits at its pinch, and both halves read.
    """
    pieces = _pinch_split(mask, start, end, mask.shape[0])
    if len(pieces) < 2:
        return None
    characters = []
    weakest = 1.0
    for piece_start, piece_end in pieces:
        glyph, aspect = extract(mask, piece_start, piece_end)
        if glyph is None:
            return None
        char, score = classify(glyph, aspect, font)
        if char is None or score < MIN_GLYPH_SCORE:
            return None
        characters.append(char)
        weakest = min(weakest, score)
    return characters, weakest


# ---- reading ---------------------------------------------------------------


def extract(mask, start, end):
    """One character crop: (boxed glyph, natural aspect) or (None, 0).

    Unlike digits.extract_glyph this does NOT trim to the character's own
    ink rows - the full line height is the canvas. Two reasons, both
    learned the hard way: a hyphen trimmed to its ink is a featureless
    solid block (zero variance, so normalized correlation degenerates to
    zero and the whole line dies), and vertical position is real signal -
    a hyphen lives mid-line, a period on the baseline, a descender hangs
    below. The aspect is width over LINE height, measured before the
    squash so a stretched "i" cannot impersonate an "l"; the harvest tool
    cuts templates the same way.
    """
    column_slice = mask[:, start:end]
    if column_slice.max() == 0:
        return None, 0.0
    aspect = column_slice.shape[1] / column_slice.shape[0]
    boxed = cv2.resize(column_slice, (digits.GLYPH_WIDTH, digits.GLYPH_HEIGHT),
                       interpolation=cv2.INTER_AREA)
    return boxed, aspect


def classify(glyph, aspect, font):
    """(character, score) for the best glyph match, aspect-gated."""
    normalized = digits._normalize(glyph)
    best_label, best_score = None, -1.0
    for label, variants in font.items():
        for template, template_aspect in variants:
            ratio = aspect / template_aspect if template_aspect else 99
            if ratio > ASPECT_TOLERANCE or ratio < 1 / ASPECT_TOLERANCE:
                continue
            score = float((normalized * template).mean())
            if score > best_score:
                best_label, best_score = label, score
    if best_label is None:
        return None, 0.0
    return char_for(best_label), best_score


def read_line(line_bgr, font):
    """The line as text, or (None, 0.0) if any character is not believed.

    Spaces come from gaps: the font's word gaps are far wider than its
    letter gaps, so a gap over SPACE_FRACTION of the line height reads as
    one space.
    """
    if not font:
        return None, 0.0
    mask, runs = segment_line(line_bgr)
    if not runs:
        return None, 0.0

    height = mask.shape[0]
    space_gap = height * SPACE_FRACTION
    characters = []
    weakest = 1.0
    weak_used = False
    previous_end = None
    for start, end in runs:
        if previous_end is not None and start - previous_end >= space_gap:
            characters.append(" ")
        previous_end = end

        glyph, aspect = extract(mask, start, end)
        if glyph is None:
            continue
        char, score = classify(glyph, aspect, font)
        if char is None or score < MIN_GLYPH_SCORE:
            # Before giving up: this may be two touching letters. The
            # split must EARN acceptance - the whole run failed AND every
            # piece classifies - or an "m" would read as "ln" (it did,
            # once, in a real game: "colnplete").
            pieces = _read_split(mask, start, end, font)
            if pieces is not None:
                chars, piece_weakest = pieces
                characters.extend(chars)
                weakest = min(weakest, piece_weakest)
                continue
            # Not touching letters either. One slightly-soft glyph per
            # line is forgiven (see WEAK_GLYPH_FLOOR); a second means the
            # rendering is genuinely off, and the line dies rather than
            # guess. Never guess: one BAD glyph still kills the line.
            if (char is not None and score >= WEAK_GLYPH_FLOOR
                    and not weak_used):
                weak_used = True
                characters.append(char)
                weakest = min(weakest, score)
                continue
            return None, 0.0
        characters.append(char)
        weakest = min(weakest, score)

    text = "".join(characters).strip()
    return (text, weakest) if text else (None, 0.0)


# ---- from text to events ---------------------------------------------------

# Every word an event subject may contain. The subjects are GAME ENTITIES
# - a finite vocabulary - and this list is the last line of defence
# against confident misreads: "Slege Ram" and "Rracer" both cleared the
# per-glyph score gate in a real game (i/l and A/R are near-twins when a
# line is fading), but "slege" is not a word, and a subject containing an
# unknown word refuses rather than minting a unit that does not exist.
# Curated by hand from the game's unit/building/technology names; a
# genuinely new word costs one addition here when it shows up.
KNOWN_WORDS = set("""
age arambai arbalester archer archery arms arrow arson at atonement axe
axeman ballista ballistics banking barding barracks battering battle
berserk bit blacksmith blast block bloodlines bodkin bombard bow boyar
bracer camel cannon cannoneer capped caravan caravanserai cart castle
cataphract cavalier cavalry center centurion champion chemistry chu
coinage cog collar complete conquistador coustillier crane crop
crossbowman demolition dock donjon double eagle elephant elite engineers
faith farm feitoria fervor feudal fire fishing fletching folwark forging
furnace galleon galley gambesons gate gendarme genoese ghulam gold guard
guilds halberdier hand handed harbor heavy heresy herbal holes horse
house hussar hussite huskarl illumination imperial iron jaguar janissary
kamayuk karambit keshik kipchak knight ko krepost lancer leather legionary
leitis light lumber long longbowman loom magyar mail mameluke man mangonel
mangudai market masonry medicine militia mill mining missionary monastery
monk murder nu obuch onager outpost padded paladin palisade parthian
petard pikeman plate plow plumed printing raider ram range ratha rattan
redemption relic ring rider rocket rotation samurai sanctity sapper
sappers saw scale scorpion scout serjeant shaft ship shrivamsha siege
skirmisher spearman squires stable steppe stone supplies swordsman
tactics tarkan teutonic theocracy thirisadai throwing thumb tower town
trade transport treadmill trebuchet two university urumi wagon wall war
villager warrior watch wheelbarrow woad wonder workshop
boar capybara chicken cow deer goat goose ibex llama ostrich pig sheep
turkey yak zebra
""".split())

# The event phrasings the game uses, learned from real lines. Longest
# suffix first, so "research complete" wins before any shorter match.
EVENT_SUFFIXES = (
    (" research complete", "researched"),
    (" created", "created"),
    (" built", "built"),
    (" found", "found"),
    (" destroyed", "destroyed"),
    (" lost", "lost"),
)


def slugify(words):
    """'Town Center' -> 'town_center': the stats-file spelling."""
    cleaned = "".join(c if c.isalnum() or c == " " else " "
                      for c in words.lower())
    return "_".join(cleaned.split())


def parse_event(text):
    """One read line as an event name, or None for a line worth ignoring.

    "--Mill Built--" -> "built:mill"; "--Town Center Built--" additionally
    keeps its legacy name (production counts TCs by it). Attack warnings
    WRAP across two lines - the fixed first line is "--Warning: You are
    being attacked by" and the attacker's name follows on its own line in
    their player colour - so "attacked" parses from the first line alone
    and the name line is free to drop (arbitrary gamer tags are outside
    any font's coverage, and the event is already counted).

    Event lines must carry their "--" framing. A fragment without it is a
    partially-read line, and treating fragments as facts is how a real
    game once recorded "colnplete" as an event. A framed line matching no
    known shape becomes "line:<slug>" - observed facts are kept, even
    unclassified ones - but frame-less text is refused outright.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    if lowered.startswith("--warning:") or "attacked by" in lowered:
        return "attacked"

    # Everything else must be a whole framed line.
    if not (stripped.startswith("--") and stripped.endswith("--")):
        return None
    words = stripped.strip("- ").strip()
    if not words or len(words) < 3:
        return None
    lowered = words.lower()

    for suffix, kind in EVENT_SUFFIXES:
        if lowered.endswith(suffix):
            subject = slugify(words[: -len(suffix)])
            if not subject:
                return None
            # The vocabulary gate: every subject word must be a real game
            # word, or this "event" is a confident misread.
            if any(word not in KNOWN_WORDS
                   for word in subject.split("_")):
                return None
            if kind == "built" and subject == "town_center":
                return "town_center_built"
            return f"{kind}:{subject}"

    slug = slugify(words)
    if any(word not in KNOWN_WORDS for word in slug.split("_")):
        return None
    return f"line:{slug}"


# How long after sighting a line before the same text can count as a new
# event, in game seconds - and the trap that makes the BOTTOM rule
# necessary: the feed redisplays HISTORY. After idling it fades, and the
# next message brings recent lines back above itself, so an old line
# resurfaces long past any cooldown. A fresh message always arrives as
# the bottom-most line of the stack; redisplayed history sits above newer
# lines. So only the bottom line may fire, and every visible line
# refreshes its cooldown so history cannot re-fire by scrolling back down.
TEXT_COOLDOWN_SECONDS = 15

# At most one unreadable-line crop is saved per this many game seconds -
# enough to harvest from, not enough to flood the disk.
UNREAD_SAVE_GAP = 30


class TextWatcher:
    """Reads the notification feed as text, one event per appearance.

    The glyph-path sibling of notifications.NotificationWatcher, sharing
    its cooldown semantics and the bottom-line rule. Lines the font cannot
    read are saved to captures/notif_unread/ - each saved crop is one
    harvest command away from becoming coverage.
    """

    def __init__(self, save_unread=True):
        self.font = load_font()
        self._last_fired = {}
        self._last_signature = None
        self._last_unread_save = None
        self.save_unread = save_unread
        # Digest -> read result. A line lingers ~10 seconds and gets
        # re-read on every look; with the resampled font variants a busy
        # panel costs ~100ms to read, so each distinct rendering pays
        # that once and lingering is free. The digest is the same coarse
        # fingerprint the stack signature uses, so anything stable enough
        # to track is stable enough to cache.
        self._read_cache = {}

    def watch(self, panel_bgr, game_time):
        """Read the feed once. Returns event names newly sighted.

        Counting works on the STACK SIGNATURE - the tuple of every visible
        line, unreadable ones included as pixel digests - rather than a
        per-text cooldown, because a per-text cooldown counted "Villager
        Created" eight times in a 145-villager game. The rules:

        * A second identical line arriving under the first CHANGES the
          signature (two entries vs one) and fires - repeats count.
        * A static lingering stack never refires (signature unchanged).
        * History redisplaying above a new message fires only the new
          message - redisplays are never the bottom line.
        * A line expiring off the TOP shrinks the stack but leaves the
          bottom line the same line - no fire (depth did not grow and the
          bottom did not change).
        * A lone line flapping through fade-and-return is rate-floored by
          the cooldown; genuine bursts (depth > 1) are exempt from it.
        """
        if not self.font or panel_bgr is None or panel_bgr.size == 0 \
                or game_time is None:
            return []

        bands = find_lines(panel_bgr)
        stack = []
        for (y1, y2) in bands:
            line = panel_bgr[y1:y2]
            digest = _band_digest(line)
            if digest in self._read_cache:
                text = self._read_cache[digest]
            else:
                text, _score = read_line(line, self.font)
                # The cache maps renderings, not game state, so dropping
                # it wholesale now and then costs one re-read per visible
                # line and caps the footprint for a whole session.
                if len(self._read_cache) >= 256:
                    self._read_cache.clear()
                self._read_cache[digest] = text
            if text is None:
                stack.append(("pixels", digest))
            else:
                stack.append(("text", text))
        signature = tuple(stack)

        events = []
        if stack and signature != self._last_signature:
            previous = self._last_signature or ()
            kind, bottom = stack[-1]
            depth_grew = len(stack) > len(previous)
            bottom_changed = not previous or previous[-1] != stack[-1]
            if kind == "text" and (depth_grew or bottom_changed):
                fired = self._last_fired.get(bottom)
                in_burst = len(stack) > 1
                if (in_burst or fired is None
                        or game_time - fired >= TEXT_COOLDOWN_SECONDS):
                    event = parse_event(bottom)
                    if event is not None:
                        events.append(event)
                self._last_fired[bottom] = game_time
            elif kind == "pixels" and (depth_grew or bottom_changed):
                self._save_unread(panel_bgr[bands[-1][0]:bands[-1][1]],
                                  game_time)
        self._last_signature = signature
        return events

    def reset(self):
        """Forget sightings. Call when a new game starts."""
        self._last_fired.clear()
        self._last_signature = None

    def _save_unread(self, line_bgr, game_time):
        if not self.save_unread:
            return
        if (self._last_unread_save is not None
                and game_time - self._last_unread_save < UNREAD_SAVE_GAP):
            return
        self._last_unread_save = game_time
        out_dir = paths.CAPTURES_DIR / "notif_unread"
        os.makedirs(out_dir, exist_ok=True)
        cv2.imwrite(str(out_dir / f"line_t{int(game_time)}.png"), line_bgr)


def _band_digest(line_bgr):
    """A small stable fingerprint for an unreadable band, so the stack
    signature can still track it across polls."""
    mask = text_mask(line_bgr)
    small = cv2.resize(mask, (32, 4), interpolation=cv2.INTER_AREA)
    return bytes((small > 96).flatten().tolist()).hex()
