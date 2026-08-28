"""
Loom — the build preview: a stack of step cards in its own window.

A column of step cards: the step just done, the CURRENT step (highlighted),
and as many of the ones after it as the window has room for. Each card shows
what the overlay shows for a step - instruction with icons, "by M:SS · N
vills", the villagers-per-resource targets - so the window reads as a stack of
overlays, which is the point: study the whole build before a match on the
second monitor, then let it follow along during one.

The preview is a separate top-level window rather than a launcher column,
because the window manager is the best size control there is. How many cards
are on screen follows the window's height; how big they are is the player's
own choice through the zoom buttons, falling back to fitting the width. An
ordinary native Wayland window on purpose: nothing here needs the overlay's
XWayland/ToolTip machinery.

With alerts switched on it stops being only a reference and becomes somewhere
to play from: the same TC IDLE and HOUSE SOON bands the overlay draws, so a
second monitor can carry Loom and the game can carry none of it.

Two modes, and live wins. With no usable game state the player browses: click
a card or scroll the wheel to move through the build. The moment a usable
state line arrives from the overlay (see statefeed.py), the view snaps to the
step the player is actually on and clicks go dead - a preview that fights
the game over where to look would be worse than none. Menus and the pre-match
wait announce themselves as not-usable, so browsing comes back exactly when
following has nothing to follow.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import time

from PyQt6.QtCore import QEvent, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QFontMetrics, QColor, QCursor, QFont, QPainter, QPainterPath,
                         QPalette, QPen)
from PyQt6.QtWidgets import (QApplication, QCheckBox, QFrame,
                             QGraphicsOpacityEffect, QLabel, QPushButton,
                             QScrollArea, QScrollBar, QVBoxLayout,
                             QHBoxLayout, QWidget)

from . import alerts as alerts_module
from . import checklist as checklist_module
from . import config, placement, steplayout
from .flowlayout import flow_row
from .tooltips import wrapped
from .build_order import format_time
from .overlay import (draw_items, item_indent, measure_segments,
                      AHEAD_COLOR, ALERT_BAND_HEIGHT, ALERT_FULL_BRIGHT,
                      ALERT_FULL_DIM, ALERT_FULL_TEXT, ALERT_GAP,
                      ALERT_SOFT_FILL, ALERT_SOFT_TEXT, FLASH_SECONDS,
                      MAX_ALERT_BANDS,
                      BACKGROUND, BORDER, DIM_TEXT, FAINT_TEXT,
                      HOLDING_COLOR,
                      ICON_HEIGHT, NOT_FOLLOWING_COLOR, ON_PACE_COLOR, TEXT,
                      describe_pace,
                      draw_resource_row, draw_segments, elide,
                      load_resource_icons)

# The slots in the stack, top to bottom: previous, current, next, next-after.
CARD_SLOTS = 4

# The designed card, at scale 1.0. Same content width as the overlay panel,
# so instructions elide identically in both windows; shorter, because a card
# has no THEN row or alert bands.
CARD_WIDTH = 560
CARD_HEIGHT = 132

# How far the cards may be pushed. The lower bound keeps a carelessly small
# window readable-ish; the upper stops a full-screen window from producing
# poster-sized villagers.
#
# The ceiling was 3.0 and that was too generous: automatic fitting spends the
# whole width, so a maximised window on a 1600px monitor drew ONE card 1530px
# wide and 360 tall, and only three of them fit the screen. Reported as
# "the content can become too large if the window is maximised". At 2.0 the
# same window shows more of the build at a size that is still comfortably
# large, and the zoom buttons are there for anyone who wants bigger or
# smaller than the window's own guess.
MIN_CARD_SCALE = 0.5
MAX_CARD_SCALE = 2.0

# Room the window chrome takes around the cards: layout margins plus a
# vertical scrollbar's worth, so the scale settles instead of oscillating
# when the scrollbar appears.
CARD_MARGINS = 40

# The margin the card stack keeps inside the scroll area, set explicitly
# rather than inherited from the platform style.
#
# It was inherited, and that was the bug behind a horizontal scrollbar that
# never went away at any window size - along with the right-hand edge of every
# card being clipped, which is the same fault seen from the other side. The
# scale is computed from the viewport width and the card is then made exactly
# that wide, so ANY margin around it pushes the column wider than the viewport
# it has to fit in. The platform default measured 11px a side, so the column
# always wanted 22px more than it could have. Subtracted below, and pinned
# here so a different style cannot reintroduce it.
STACK_MARGIN = 6

# The window's size before the player has ever resized it.
DEFAULT_WINDOW = (CARD_WIDTH + CARD_MARGINS, 640)

# Room the header row takes, on top of the cards themselves, when working out
# how short the window may be.
MIN_HEIGHT_CHROME = 44

# A few pixels of slack in the window's minimum size.
#
# The minimum has to guarantee that a card at MIN_CARD_SCALE still FITS,
# because below that the floor wins, the card overflows, and with the
# horizontal bar off there is nothing to scroll to it with. Every term in that
# sum is asked of the widgets rather than guessed, but frame widths and layout
# spacing still vary a little by style, and being a few pixels too generous
# costs nothing while being one pixel short costs a strip of every card.
MINIMUM_SLACK = 6

# The current card is brighter than BACKGROUND, the way the eye should land.
CURRENT_BACKGROUND = QColor(26, 26, 32, 235)

# Where a card sits in the build, as a tint you feel rather than read: the step
# already behind you carries a breath of red, the ones still coming a breath of
# green. Deliberately close to BACKGROUND - this has to survive being seen out
# of the corner of an eye on a second monitor without turning the stack into a
# traffic light, and the CURRENT card must stay the thing the eye lands on.
PAST_BACKGROUND = QColor(32, 18, 20, 215)
UPCOMING_BACKGROUND = QColor(18, 30, 22, 215)

# How many cards the window shows. The count follows the height available -
# more room, more of the build - between a floor that still shows you the step
# after this one and a ceiling that stops a tall monitor building forty
# widgets nobody reads.
MIN_VISIBLE_CARDS = 3
MAX_VISIBLE_CARDS = 12

# Space between cards in the stack, in designed pixels, scaled like everything
# else so the gap does not swallow the cards at small sizes.
CARD_GAP = 8

# What one press of + or - moves the card scale by.
ZOOM_STEP = 0.15

# The zoom buttons are square-ish and out of the way; they are a
# convenience beside the build, not part of it.
ZOOM_BUTTON_WIDTH = 28

# How much the neighbours fade: one knob dims text, icons and resource colors
# uniformly, instead of hand-picking a faint variant of every color.
PREVIOUS_OPACITY = 0.55
EMPTY_OPACITY = 0.35

# How long a resize has to hold still before the window size is saved.
# Saving per resize event would write the settings file once per pixel of
# the drag.
SAVE_SIZE_AFTER_MS = 1000

# --- the chrome, and how it comes and goes --------------------------------
#
# Everything in this window that is not a card fades away while the pointer
# is elsewhere, leaving the build and nothing else.
#
# The window earned that when the alert bands arrived: it stopped being a
# reference you read before a match and became somewhere to play FROM, which
# means it spends the match on a second monitor being glanced at while both
# hands are in the game. Controls that are useful for the few seconds they
# are actually clicked do not earn a permanent strip across the top of it.
#
# Fading in is quicker than fading out. Reaching for a control wants the
# control now; leaving wants to be undramatic, and the slower exit also stops
# the chrome strobing when the pointer clips a corner of the window.
CHROME_FADE_IN_SECONDS = 0.12
CHROME_FADE_OUT_SECONDS = 0.22

# How long the chrome stays up after the pointer leaves, so that brushing
# past the window on the way somewhere else does not flash it.
CHROME_LINGER_SECONDS = 0.4

# How long the chrome stays up when the window opens.
#
# A window that opened with no visible controls at all would read as broken
# rather than as designed, and there would be nothing to teach the player the
# rule. Showing the chrome once and then taking it away demonstrates it.
CHROME_OPENING_SECONDS = 2.0

# How often the pointer is looked for, and how often a fade steps.
#
# A poll rather than enterEvent/leaveEvent, which is not laziness: the cards
# cover the whole viewport, and Qt sends a widget a Leave the moment the
# pointer crosses into any of its children. Every one of those looks exactly
# like leaving the window. Testing the cursor's global position against the
# frame has none of that problem, and it works while the window is unfocused
# - the normal case for a window on the monitor you are not playing on.
HOVER_POLL_MS = 80
FADE_TICK_MS = 16

# Below this the chrome is gone rather than faint, and has to stop taking
# clicks with it: an invisible button that still works is a trap.
CHROME_DEAD_OPACITY = 0.02

# How close to an edge counts as reaching for it to resize the window.
RESIZE_MARGIN = 6

# The gutter the window keeps around its contents, and the smallest it can
# be: the two are deliberately the same number.
#
# The ground around the cards should be as thin as it can be - the cards are
# the window and everything else is packaging. But the gutter has a second
# job now that the frame is Loom's own. It is the strip of window belonging
# to no child widget, and so the only place the resize hit-test can see the
# pointer at all: a child covering those pixels takes the mouse events with
# it. Below RESIZE_MARGIN the window stops being resizable by its own edge
# before it looks any better, so that is where this stops.
WINDOW_MARGIN = RESIZE_MARGIN

# The ground the cards sit on, and the chrome that floats over them.
#
# Solid and dark on purpose. With the native frame gone this ground IS the
# window, and the platform's window grey behind dark cards reads as an
# unfinished dialog rather than a panel.
WINDOW_BACKGROUND = QColor(12, 12, 15)
CHROME_BACKGROUND = QColor(20, 20, 26, 247)

# How round the window's own corners are.
#
# The same radius the cards use for theirs, so the window reads as one more
# card holding the rest rather than as a box they happen to be in.
#
# It costs the window a translucent background: the corners have to be
# painted with antialiasing to be smooth, and the pixels outside the curve
# have to be genuinely absent rather than merely dark. setMask is the
# alternative and would give a jagged, unantialiased edge - the one thing a
# rounded corner cannot afford.
#
# It fits inside WINDOW_MARGIN by construction. The content starts at
# (WINDOW_MARGIN, WINDOW_MARGIN), which is well inside the arc centred at
# (WINDOW_RADIUS, WINDOW_RADIUS) - so no child widget ever has a square
# corner poking out through a round one.
WINDOW_RADIUS = 10

# Which cursor says which edge, and which Qt.Edge to hand the window manager.
EDGE_CURSORS = {
    frozenset({"left"}): Qt.CursorShape.SizeHorCursor,
    frozenset({"right"}): Qt.CursorShape.SizeHorCursor,
    frozenset({"top"}): Qt.CursorShape.SizeVerCursor,
    frozenset({"bottom"}): Qt.CursorShape.SizeVerCursor,
    frozenset({"left", "top"}): Qt.CursorShape.SizeFDiagCursor,
    frozenset({"right", "bottom"}): Qt.CursorShape.SizeFDiagCursor,
    frozenset({"right", "top"}): Qt.CursorShape.SizeBDiagCursor,
    frozenset({"left", "bottom"}): Qt.CursorShape.SizeBDiagCursor,
}
QT_EDGES = {"left": Qt.Edge.LeftEdge, "right": Qt.Edge.RightEdge,
            "top": Qt.Edge.TopEdge, "bottom": Qt.Edge.BottomEdge}


def visible_indices(focus, step_count, count=CARD_SLOTS):
    """Which step index each slot shows: [prev, focus, +1, +2, ...].

    The focused step is always SECOND from the top - one step of history above
    it and the rest of the window ahead of it. That is the shape the author
    designed and it does not change with the card count; a taller window buys
    more lookahead, not more history, because the build is a thing you are
    about to do rather than a thing you have done.

    None for a slot that falls outside the build, so the first step has an
    empty slot above it and the last steps have empty slots below - the stack
    keeps its shape at the ends instead of jumping. A wild focus is clamped
    rather than refused; the caller's arithmetic stays simple.
    """
    count = max(1, count)
    if step_count <= 0:
        return [None] * count
    focus = max(0, min(focus, step_count - 1))
    first = focus - 1
    return [index if 0 <= index < step_count else None
            for index in range(first, first + count)]


def step_plan(step, scale, text=1.0):
    """The item layout for one step on a card at this scale and text size.

    Measured with a standalone QFontMetrics rather than a painter's, because
    the answer decides how TALL the card is and a widget cannot resize
    itself inside its own paintEvent.
    """
    rows = step.items_segments if step is not None else []
    if not rows:
        return steplayout.plan_items(0)
    points = max(1, round(steplayout.ITEM_POINTS * scale * text))
    metrics = QFontMetrics(QFont("sans", points))
    _radius, indent = item_indent(metrics, scale)
    # Width follows the card scale alone - bigger text makes a card taller,
    # never wider - so the room for columns does not grow with the text
    # while the items in them do.
    content = round(CARD_WIDTH * scale) - round(32 * scale)
    widest = max(measure_segments(metrics, row, points, scale)
                 for row in rows)
    columns = steplayout.columns_for(content, widest + indent, len(rows),
                                     round(steplayout.COLUMN_GAP * scale))
    return steplayout.plan_items(len(rows), columns)


def card_height(step, scale, text=1.0):
    """How tall the card for one step has to be, in real pixels."""
    grown = CARD_HEIGHT + step_plan(step, scale, text).extra
    return round(grown * scale * text)


def cards_for_steps(heights, available_height, gap):
    """How many cards fit a stack this tall, given each one's own height.

    The count is what makes the window fit its own contents: pick it from
    the heights and the cards never need scrolling inside their own scroll
    area, which is what let the wheel and the scrollbar disagree about what
    scrolling meant.

    Cards used to be one height, so this was a division. They are not any
    more - a step with seven instructions is taller than a step with one -
    so it accumulates instead. `heights` must be in the order the stack
    shows them, because visible_indices only ever APPENDS as the count
    grows: that is what makes the total monotonic in the count, and so what
    makes stopping at the first overflow right rather than a guess.

    MIN_VISIBLE_CARDS always fit whatever their heights say. Below that this
    has stopped being a preview of anything, and the caller shrinks the
    scale instead - the same escape the width has.
    """
    used = 0
    count = 0
    for height in heights[:MAX_VISIBLE_CARDS]:
        need = used + height + (max(0, gap) if count else 0)
        if count >= MIN_VISIBLE_CARDS and need > available_height:
            break
        used = need
        count += 1
    return max(MIN_VISIBLE_CARDS, count)


def zoomed(chosen, fit, floor=MIN_CARD_SCALE, ceiling=MAX_CARD_SCALE):
    """The scale to actually draw at.

    chosen is what the player asked for with + and -, or None for automatic.

    A chosen size is a CEILING, not a size: the window never grows the cards
    past what was asked for, but a window too small to hold them still shrinks
    them to fit, down to the floor. That is the rule the author asked for -
    the buttons resize the cards and nothing else, resizing the window leaves
    them alone, until the window is too small for them.
    """
    wanted = fit if chosen is None else min(chosen, fit)
    return max(floor, min(ceiling, wanted))


def live_focus(current_index, step_count):
    """The step to highlight for a live reading.

    current_index is build_order semantics: the last step already reached,
    -1 before the first. The step the player should be DOING is the one
    after it - clamped to the last step once the build is complete, so a
    finished build rests on its final card rather than an empty stack.
    """
    if step_count <= 0:
        return 0
    return max(0, min(current_index + 1, step_count - 1))


def usable_card_width(viewport_width, scrollbar_width):
    """How much width a card may take inside a viewport of this size.

    The card is made exactly this wide, so everything the column spends
    around it has to come off first: the stack's own margins, and a vertical
    scrollbar's worth. The scrollbar is subtracted whether or not one is
    showing, because a card sized to the bar-less width makes the bar appear,
    which narrows the viewport, which makes the card too wide - the
    oscillation CARD_MARGINS was invented to damp.
    """
    return max(1, viewport_width - 2 * STACK_MARGIN - scrollbar_width)


def card_scale(available_width):
    """The card scale a window of this content width earns.

    One uniform factor: the width the window offers, over the designed card
    width, clamped to sane bounds. Resizing the window IS the size control.
    """
    return max(MIN_CARD_SCALE,
               min(MAX_CARD_SCALE, available_width / CARD_WIDTH))


def chrome_target(pointer_inside, warning_showing):
    """How visible the chrome should be: all the way, or not at all.

    The pointer arriving is the ordinary reason. The other one is not
    negotiable. While the chip is warning that the overlay has stopped
    following the game, the chrome stays up whether anyone is reaching for it
    or not - CLAUDE.md forbids the panel ceasing to follow the game quietly,
    and chrome that faded would leave "manual" and "following" looking
    identical from across the desk. That is the same class of failure as a
    wrong villager count: silent, and trusted.

    "following game" is not a warning and pins nothing. The absence of an
    alarm is not an alarm.
    """
    return 1.0 if (pointer_inside or warning_showing) else 0.0


def stepped_opacity(current, target, seconds,
                    fade_in=CHROME_FADE_IN_SECONDS,
                    fade_out=CHROME_FADE_OUT_SECONDS):
    """Where the fade has reached `seconds` after being at `current`.

    Driven by elapsed time rather than by a fixed step per tick, so a window
    that misses a few ticks still finishes in the time it promised instead of
    fading slower - the same reason the notification watcher counts game
    seconds rather than looks.

    Monotone and clamped: it walks toward the target and stops there, and
    cannot overshoot however long the gap between two ticks turns out to be.
    """
    if current == target:
        return target
    duration = fade_in if target > current else fade_out
    if duration <= 0:
        return target
    step = max(0.0, seconds) / duration
    if target > current:
        return min(target, current + step)
    return max(target, current - step)


def resize_edges(x, y, width, height, margin=RESIZE_MARGIN):
    """Which window edges a point this close to the frame is reaching for.

    Plain arithmetic returning names rather than Qt flags, so it can be
    tested the way the rest of this file's arithmetic is - with no
    QApplication and no window on screen. The caller maps the names to a
    cursor and to Qt.Edge.

    The margin is clamped to less than half the window in each direction, so
    a window dragged all the way to its minimum still has a middle that is
    not an edge. Without that the two sides meet and every point in the
    window becomes a resize handle.
    """
    margin = max(0, min(margin, (width - 1) // 2, (height - 1) // 2))
    edges = set()
    if x < margin:
        edges.add("left")
    elif x >= width - margin:
        edges.add("right")
    if y < margin:
        edges.add("top")
    elif y >= height - margin:
        edges.add("bottom")
    return frozenset(edges)


def ground_opacity(chrome, rest, hover):
    """How solid the window's ground is at this point of the chrome fade.

    The fade is the interpolant between two player-set endpoints: the ground
    with the pointer away and the ground with the pointer on the window. The
    designed look is exactly (rest=0, hover=1) - cards floating on the
    desktop, a solid ground on arrival - and both knobs exist because that
    is a choice about the player's wallpaper, which Loom cannot see.
    """
    # The two-product form rather than rest + (hover - rest) * chrome,
    # because only this one is EXACT at both ends: the subtraction form
    # returns 0.19999... for (1.0, 0.8, 0.2), and an endpoint that misses
    # the player's own number is the golden case failing by a float crumb.
    value = rest * (1.0 - chrome) + hover * chrome
    return max(0.0, min(1.0, value))


def card_alpha(designed_alpha, opacity):
    """A card fill's alpha at the player's chosen card opacity.

    opacity is TRUE opacity of the CURRENT card, whose designed fill is
    alpha 235 - the same semantics as the overlay's background knob. The
    other roles keep their designed ratio to it, so the stack's tints stay
    in proportion however solid the player makes it. At the default
    (config.DEFAULT_PREVIEW_CARD_OPACITY, 235/255) every designed alpha
    comes back byte-identical, which is the golden case the tests pin.
    """
    return min(255, max(0, round(designed_alpha * opacity * 255 / 235)))


class StepCard(QWidget):
    """One step of the build, drawn like a small overlay panel.

    All the drawing literals are the designed (scale 1.0) numbers, mapped
    through _s()/_pt() at paint time - the same trick as the overlay's
    OverlayLayout, but one-axis: a card scales uniformly with its window.
    """

    clicked = pyqtSignal(int)   # the step index shown, on mouse press

    def __init__(self, icons, parent=None):
        super().__init__(parent)
        self._icons = icons     # resource icons, baked at the current scale
        self._scale = 1.0
        # The player's appearance knobs, pushed by the browser: how solid
        # the fill is (true opacity of the current card's designed 235) and
        # how much bigger than designed the text is drawn.
        self._fill_opacity = 235 / 255
        self._text = 1.0
        self._plan = steplayout.plan_items(0)
        # Checklist states for this step's items, one each. Empty means
        # nothing is ticked, which is what browsing wants.
        self._states = []
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        self._index = None      # step index in the build, None = empty slot
        self._step = None
        self._total = 0
        self._role = "next"     # "previous" | "current" | "next"
        # Live values, only ever set on the current card during a game.
        self._live = None       # (villagers, game_time, pace_delta, per_resource)

    # ---- scaling -------------------------------------------------------

    def set_scale(self, scale, icons):
        """Resize the card to the window's chosen scale.

        icons come along because resource icons are baked to a height at
        load time - the browser reloads them once per scale change and every
        card shares the batch.
        """
        self._icons = icons
        if self._scale != scale:
            self._scale = scale
            self._fix_size()
            self.update()

    def set_appearance(self, fill_opacity, text_scale):
        """The player's card opacity and text size, from the settings tab."""
        if (self._fill_opacity, self._text) == (fill_opacity, text_scale):
            return
        self._fill_opacity = fill_opacity
        self._text = text_scale
        self._fix_size()
        self.update()

    def _fix_size(self):
        """Width from the card scale alone; height from the text too.

        Bigger text makes a card TALLER, never wider - the same rule as the
        overlay's text knob, and for the same reason: lines that collide are
        worse than lines that moved.

        A step with a lot of instructions makes it taller again, on the same
        axis: steplayout grows the box before it will shrink the writing.
        """
        self._plan = step_plan(self._step, self._scale, self._text)
        grown = CARD_HEIGHT + self._plan.extra
        self.setFixedSize(round(CARD_WIDTH * self._scale),
                          round(grown * self._scale * self._text))

    def _s(self, base):
        """A designed pixel value at the current scale."""
        return round(base * self._scale)

    def _sy(self, base):
        """A designed VERTICAL position, which the text size stretches too.

        Every row's y rides the text multiplier along with the fonts, so the
        rows spread apart exactly as fast as the text grows into them.
        Horizontal positions stay _s: the card does not get wider, and the
        headline already knows how to elide.
        """
        return round(base * self._scale * self._text)

    def _pt(self, base):
        """A designed font size at the current scale and text size, never 0."""
        return max(1, round(base * self._scale * self._text))

    # ---- what to show --------------------------------------------------

    def show_step(self, index, step, total, role, states=()):
        state = (index, step, total, role, tuple(states))
        if (self._index, self._step, self._total, self._role,
                tuple(self._states)) != state:
            self._index, self._step, self._total, self._role = state[:4]
            self._states = list(states)
            self._fix_size()
            self.update()

    def show_empty(self):
        if self._index is not None or self._live is not None:
            self._index = self._step = self._live = None
            self._states = []
            self._fix_size()
            self.update()

    def set_live(self, villagers, game_time, pace_delta, per_resource):
        live = (villagers, game_time, pace_delta, per_resource)
        if self._live != live:
            self._live = live
            self.update()

    def clear_live(self):
        if self._live is not None:
            self._live = None
            self.update()

    # ---- input ---------------------------------------------------------

    def mousePressEvent(self, event):
        if self._index is not None:
            self.clicked.emit(self._index)

    # ---- painting ------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._step is None:
            self._draw_empty(painter)
            return

        if self._role == "previous":
            painter.setOpacity(PREVIOUS_OPACITY)

        self._draw_frame(painter)
        self._draw_header(painter)
        self._draw_items(painter)
        self._draw_resources(painter)

    def _draw_empty(self, painter):
        painter.setOpacity(EMPTY_OPACITY)
        painter.setBrush(BACKGROUND)
        painter.setPen(BORDER)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1),
                                self._s(10), self._s(10))
        painter.setFont(QFont("sans", self._pt(12)))
        painter.setPen(FAINT_TEXT)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "—")

    def _draw_frame(self, painter):
        # Where this step sits relative to the one you are on, as a tint.
        # The current card keeps its brighter card and coloured edge; it must
        # stay the thing the eye lands on, so the other two are only a breath
        # away from the plain background.
        if self._role == "current":
            fill = QColor(CURRENT_BACKGROUND)
            painter.setPen(QColor(AHEAD_COLOR))
        else:
            fill = QColor(PAST_BACKGROUND if self._role == "previous"
                          else UPCOMING_BACKGROUND)
            painter.setPen(BORDER)
        # A per-paint copy at the player's opacity, never a change to the
        # module constants - the overlay's panel_background records why that
        # is load-bearing: these colours are shared art, and one window's
        # setting must not fade the other window.
        fill.setAlpha(card_alpha(fill.alpha(), self._fill_opacity))
        painter.setBrush(fill)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1),
                                self._s(10), self._s(10))

    def _draw_header(self, painter):
        painter.setFont(QFont("sans", self._pt(9), QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(self._s(16), self._sy(22),
                         f"STEP {self._index + 1} OF {self._total}")

        # The right side of the header: targets normally; live truth plus
        # pace on the current card while a game is on.
        right = self.width() - self._s(16)
        if self._live is not None:
            villagers, game_time, delta, _ = self._live
            pace_text, pace_color = describe_pace(delta)
            painter.setFont(QFont("sans", self._pt(9), QFont.Weight.Bold))
            painter.setPen(pace_color)
            width = painter.fontMetrics().horizontalAdvance(pace_text)
            painter.drawText(right - width, self._sy(22), pace_text)
            right -= width + self._s(12)

            painter.setFont(QFont("sans", self._pt(9)))
            painter.setPen(DIM_TEXT)
            live_text = f"{format_time(game_time)} · {villagers} vills"
            width = painter.fontMetrics().horizontalAdvance(live_text)
            painter.drawText(right - width, self._sy(22), live_text)
        else:
            when = ""
            if self._step.time is not None:
                when = f"by {format_time(self._step.time)} · "
            when += f"{self._step.villager_count} vills"
            painter.setFont(QFont("sans", self._pt(9)))
            painter.setPen(FAINT_TEXT)
            width = painter.fontMetrics().horizontalAdvance(when)
            painter.drawText(right - width, self._sy(22), when)

    def _draw_items(self, painter):
        """The step's instructions, as a checklist of equal items.

        Every one of them. The old version drew the first at 15pt bold and
        capped the rest at two, which in the busiest shipped step showed
        three of seven and said nothing about the four it dropped.
        """
        plan = self._plan
        rows = self._step.items_segments[:plan.shown]
        if not rows:
            return
        text = self._scale * self._text
        draw_items(
            painter, rows, self._states[:plan.shown],
            x=self._s(16), baseline=self._sy(56),
            width=self.width() - self._s(32),
            columns=plan.columns,
            points=max(1, round(plan.points * text)),
            row_height=round(plan.row_height * text),
            icon_height=max(1, round(plan.points * text)),
            column_gap=round(steplayout.COLUMN_GAP * self._scale),
            spacing=self._scale,
            more_text=steplayout.more_label(plan.hidden))

    def _draw_resources(self, painter):
        y = self.height() - self._sy(14)
        painter.setFont(QFont("sans", self._pt(9), QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(self._s(16), y, "VILLS")

        # Live have/want only on the current card during a game; every other
        # card shows what the build says you SHOULD have at that step.
        actual = {}
        if self._live is not None and self._live[3]:
            actual = self._live[3]
        painter.setFont(QFont("sans", self._pt(11), QFont.Weight.Bold))
        draw_resource_row(painter, self._icons, self._step.villagers, actual,
                          self._s(66), y, spacing=self._scale)


class AlertBands(QWidget):
    """The overlay's alert bands, drawn in the preview.

    The same colours, the same flash, and the same text - imported from
    loom/overlay.py rather than copied, because two windows warning about the
    same thing in two different reds is how a player learns to trust neither.

    It is given the alerts; it never works them out. The policy behind them
    is thresholds, hysteresis and the player's own settings (see
    loom/alerts.py), and a second implementation would drift from the first
    the day any of those changed. They ride the statefeed instead.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.alerts = []
        self._scale = 1.0
        # Repaints are what pace the strobe. The overlay gets them free from
        # its poll; this window only repaints when something changes, so it
        # needs its own heartbeat or a band would light up and stay lit.
        self._flash = QTimer(self)
        self._flash.setInterval(round(FLASH_SECONDS * 1000))
        self._flash.timeout.connect(self.update)
        self.setVisible(False)

    def show_alerts(self, alerts, scale):
        """Show these alerts, or nothing at all when there are none."""
        self.alerts = list(alerts)[:MAX_ALERT_BANDS]
        self._scale = scale
        wanted = bool(self.alerts)
        height = 0
        if wanted:
            band = round(ALERT_BAND_HEIGHT * scale)
            gap = round(ALERT_GAP * scale)
            height = len(self.alerts) * band + (len(self.alerts) - 1) * gap
        self.setFixedHeight(height)
        self.setVisible(wanted)
        # The timer only runs when there is something to flash: a window on a
        # second monitor should not repaint three times a second all match
        # for a strip that is empty.
        if wanted and not self._flash.isActive():
            self._flash.start()
        elif not wanted and self._flash.isActive():
            self._flash.stop()
        self.update()

    def paintEvent(self, event):
        if not self.alerts:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        band = round(ALERT_BAND_HEIGHT * self._scale)
        gap = round(ALERT_GAP * self._scale)
        radius = round(8 * self._scale)
        # One phase for every band, so two of them blink together instead of
        # chasing each other - the same sharing the overlay does.
        phase = int(time.monotonic() / FLASH_SECONDS) % 2

        for index, (text, severity) in enumerate(self.alerts):
            if severity == alerts_module.FULL:
                fill = ALERT_FULL_BRIGHT if phase == 0 else ALERT_FULL_DIM
                pen = ALERT_FULL_TEXT
            else:
                fill = ALERT_SOFT_FILL
                pen = ALERT_SOFT_TEXT
            top = index * (band + gap)
            path = QPainterPath()
            path.addRoundedRect(0, top, self.width(), band, radius, radius)
            painter.fillPath(path, fill)
            painter.setPen(pen)
            painter.setFont(QFont("sans", max(7, round(11 * self._scale)),
                                  QFont.Weight.Bold))
            painter.drawText(0, top, self.width(), band,
                             Qt.AlignmentFlag.AlignCenter, text)


class ChromeBar(QWidget):
    """Everything in the preview that is not a card, floating over the cards.

    Deliberately NOT in the window's layout, and that is the whole design.
    Chrome that collapsed out of the layout would hand its height back to the
    scroll viewport, _relayout would re-derive the card count from the taller
    viewport, and the stack would redeal - every card sliding down as the
    pointer approached the one it was aiming at, and back up again when it
    left. This file already carries two comments about that oscillation (see
    STACK_MARGIN, and the viewport eventFilter); this would have been the
    third place it appeared, and the first where it moved a click target out
    from under a moving mouse.

    Floating costs one setGeometry per resize and changes no layout at all,
    so the cards do not move by a pixel whether the chrome is up or not. What
    it covers while it is up is card 0 - the step already behind you, drawn at
    PREVIOUS_OPACITY and the least important thing on screen.
    """

    def __init__(self, controls, parent=None):
        super().__init__(parent)
        self.title = QLabel()
        self.title.setStyleSheet(f"color: rgb({DIM_TEXT.red()},"
                                 f" {DIM_TEXT.green()},"
                                 f" {DIM_TEXT.blue()}); font-size: 9pt;")

        # The X the native frame used to carry. Losing it would strand
        # nobody either way - the launcher's "Show build preview" checkbox is
        # the real switch and is always reachable - but a window with no
        # visible way to close it is a window people distrust.
        self.close_button = QPushButton("✕")
        self.close_button.setFixedWidth(ZOOM_BUTTON_WIDTH)
        self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.close_button.setToolTip(wrapped("Close the preview."))

        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.addWidget(self.title)
        strip.addStretch()
        strip.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(WINDOW_MARGIN, 2, WINDOW_MARGIN, 2)
        layout.setSpacing(2)
        layout.addLayout(strip)
        layout.addWidget(controls)

        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        # The chrome covers the window's top edge, so it has to take part in
        # the resize hit-test or that edge could never be grabbed.
        self.setMouseTracking(True)

    def set_title(self, text):
        self.title.setText(text)

    def heightForWidth(self, width):
        """Tall enough for the controls at this width, wrapped rows included.

        Asked explicitly because this widget's geometry is set by hand rather
        than negotiated by a parent layout - nothing else is going to ask,
        and a wrapped row given one row's height draws its second row over
        the cards.
        """
        layout = self.layout()
        wrapped = layout.heightForWidth(width) if layout.hasHeightForWidth() \
            else -1
        return max(wrapped, self.sizeHint().height(),
                   self.minimumSizeHint().height())

    def paintEvent(self, event):
        """A solid ground of its own, so text stays readable over a card.

        Rounded across the top to sit inside the window's own corners, and
        square across the bottom where it meets the cards. Both at once by
        rounding a rectangle that extends a radius BELOW this widget: the
        bottom curves fall outside the paint area and never arrive.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(
            QRectF(self.rect()).adjusted(0, 0, 0, WINDOW_RADIUS),
            WINDOW_RADIUS, WINDOW_RADIUS)
        painter.fillPath(shape, CHROME_BACKGROUND)
        painter.setPen(BORDER)
        painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)

    def mouseMoveEvent(self, event):
        """Show the resize cursor for the window edge underneath this bar."""
        super().mouseMoveEvent(event)
        window = self.window()
        shape = EDGE_CURSORS.get(
            window.edge_at(event.globalPosition().toPoint()))
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(shape)

    def mousePressEvent(self, event):
        """Drag the window by its chrome, using the window manager's own move.

        Qt hands the drag to Windows (or to the X server) rather than moving
        the window per mouse event, so Aero Snap, edge tiling and the native
        feel all survive losing the caption. A press on a control never
        reaches here - the control accepts it - so only the empty parts of
        the strip drag the window.

        Resizing is tried FIRST, because this bar lies across the window's
        own top edge. Without that the top edge and both top corners would be
        the only ones that could not be grabbed, and reaching for them would
        silently move the window instead.
        """
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if window.begin_resize(
                    window.edge_at(event.globalPosition().toPoint())):
                event.accept()
                return
            handle = window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)


class BuildBrowser(QWidget):
    """The preview window: a stack of step cards, follow/browse switching, and
    a card size the player controls."""

    closed = pyqtSignal()   # the player closed the window with its X
    # The overlay-disabled preference changed. The launcher owns the overlay
    # process, so it is the only thing that can act on this for a session
    # already running.
    overlay_disabled_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        # Parented to the launcher, but with the Window flag so it stays a
        # real top-level window - resizable and movable like anything else on
        # the desktop, just never BEHIND the launcher. It used to have no
        # parent at all, which left stacking to the window manager, and the
        # preview routinely opened hidden behind the launcher that spawned it.
        # A parent is the one fix that works the same on X11, Wayland, macOS
        # and Windows, none of which agree about a client positioning itself.
        #
        # Frameless as well, with Loom drawing what the caption used to. Qt
        # cannot fade a native title bar, and toggling FramelessWindowHint on
        # and off recreates the native window - which flickers, shifts the
        # geometry, and can steal focus. Focus theft is not cosmetic here: it
        # would minimise a fullscreen game every time the pointer crossed
        # this window. So the caption goes for good and the chrome carries a
        # strip of its own that fades with everything else.
        super().__init__(parent,
                         Qt.WindowType.Window
                         | Qt.WindowType.FramelessWindowHint)
        # Still worth setting with no caption to draw it: this is the name in
        # alt-tab and in the taskbar.
        self.setWindowTitle("Loom — Build preview")
        self.build = None
        self.focus = 0
        self.following = False
        # Which items of which steps are done. The preview keeps its OWN,
        # fed from the same state line the focus comes from: the assumption
        # rule is a pure function of the step index and both windows have
        # that already, so only SIGHTINGS have to travel.
        self.checklist = None
        # The last index seen from the game, so a new match can be spotted.
        self._last_index = None
        # What the overlay says it is doing, from the statefeed. None when
        # there is no overlay to ask.
        self.follow_mode = None
        # Seconds until automatic following resumes, while it is held off.
        self._hold = None
        self._scale = 1.0
        # The size the player asked for with + and -, or None for automatic.
        # A ceiling rather than a size - see zoomed().
        self._chosen = None
        # How many cards are on screen. Follows the window's height.
        self._visible = CARD_SLOTS
        # Whether this window shows the overlay's alert bands. The launcher
        # owns the checkbox; this is just where the answer is kept.
        self._show_alerts = config.preview_alerts()
        # The last alerts the overlay sent, kept so switching the checkbox on
        # mid-match shows what is happening now rather than waiting for the
        # next one to arrive.
        self._alerts = []
        # The appearance settings, re-read live by apply_appearance whenever
        # the launcher's Preview tab changes one.
        self._rest_ground = config.preview_rest_opacity()
        self._hover_ground = config.preview_hover_opacity()
        self._card_opacity = config.preview_card_opacity()
        self._text_scale = config.preview_text_scale()

        # The chrome fade. _chrome_opacity is where it is, _chrome_wanted is
        # where it is going, and the two timers below close the gap.
        self._chrome_opacity = 1.0
        self._chrome_wanted = 1.0
        # When the pointer left, for the linger; when the window opened, for
        # the one showing that teaches the rule; and when the fade last
        # stepped, so a step is measured in elapsed time rather than ticks.
        self._left_at = None
        self._opened_at = time.monotonic()
        self._faded_at = time.monotonic()
        # Whether the chip is saying something the player must not miss. Set
        # by _show_mode, read by chrome_target - a warning pins the chrome up
        # with no pointer anywhere near the window.
        self._chip_warning = False

        self.chip = QLabel()

        # Zoom, top left. The buttons resize the CARDS and nothing else; the
        # window is the player's own business.
        self.zoom_out = QPushButton("−")
        self.zoom_out.setToolTip(wrapped("Smaller cards."))
        self.zoom_out.clicked.connect(lambda: self._zoom(-ZOOM_STEP))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setToolTip(wrapped("Bigger cards."))
        self.zoom_in.clicked.connect(lambda: self._zoom(ZOOM_STEP))
        self.zoom_auto = QPushButton("↺")
        self.zoom_auto.setToolTip(wrapped(
            "Back to fitting the cards to the window automatically."))
        self.zoom_auto.clicked.connect(self._zoom_auto)
        self.zoom_label = QLabel()
        self.zoom_label.setStyleSheet(f"color: rgb({DIM_TEXT.red()},"
                                      f" {DIM_TEXT.green()},"
                                      f" {DIM_TEXT.blue()}); font-size: 9pt;")
        for button in (self.zoom_out, self.zoom_in, self.zoom_auto):
            button.setFixedWidth(ZOOM_BUTTON_WIDTH)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # The two switches that make this window somewhere to play from,
        # put in the window they are about: reaching across to the launcher
        # on another screen to turn on alerts HERE was the wrong shape.
        self.alerts_toggle = QCheckBox("Alerts here")
        self.alerts_toggle.setToolTip(wrapped(
            "Show the overlay's TC IDLE and HOUSE SOON bands in this window,"
            " so it can be played from on a second monitor."))
        self.alerts_toggle.setChecked(config.preview_alerts())
        self.alerts_toggle.toggled.connect(self._set_alerts_enabled)

        self.disable_overlay_toggle = QCheckBox("No overlay")
        self.disable_overlay_toggle.setToolTip(wrapped(
            "Keep the overlay panel off the game permanently: it will not"
            " appear when you press Start overlay, and stays away until this"
            " is unticked. Loom still reads the game, records the match and"
            " feeds this window - only the panel over the game is gone.\n\n"
            "This is the REMEMBERED setting. The launcher's Hide overlay"
            " button and Ctrl+Shift+Minus are the temporary version and are"
            " forgotten when Loom closes."))
        self.disable_overlay_toggle.setChecked(config.overlay_disabled())
        self.disable_overlay_toggle.toggled.connect(self._set_overlay_disabled)

        # A wrapping row, like every row of controls in the launcher: at the
        # 350px minimum these do not fit beside the zoom buttons, and the
        # manual warning below is longer than the whole window.
        header = flow_row([self.zoom_out, self.zoom_in, self.zoom_auto,
                            self.zoom_label, self.alerts_toggle,
                            self.disable_overlay_toggle, self.chip])

        self._icons = load_resource_icons()
        # Built once at the maximum and hidden when not wanted. Creating and
        # destroying widgets on every resize would be the obvious alternative
        # and is the wrong one: a resize is a drag, so it happens dozens of
        # times a second.
        self.cards = [StepCard(self._icons) for _ in range(MAX_VISIBLE_CARDS)]
        for index, card in enumerate(self.cards):
            card.clicked.connect(self._card_clicked)
            # Only the starting count is on screen; the pool waits hidden
            # until a taller window asks for it.
            card.setVisible(index < self._visible)

        # The cards live in a scroll area so a wide-but-short window scrolls
        # instead of clipping the stack.
        column = QWidget()
        stack = QVBoxLayout(column)
        stack.setContentsMargins(STACK_MARGIN, STACK_MARGIN,
                                 STACK_MARGIN, STACK_MARGIN)
        stack.setSpacing(CARD_GAP)
        for card in self.cards:
            stack.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)
        stack.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(column)
        # Vertical only. A card is sized to fit the width it is given, so a
        # horizontal bar here has never meant "the content is wider than the
        # window" - it has only ever meant the arithmetic that sizes the card
        # forgot something. See STACK_MARGIN.
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The scale is a function of the VIEWPORT's width, so it has to be
        # driven by the viewport's own resizes. Hanging it off the window's
        # resizeEvent looks equivalent and is not: the two do not change in
        # lockstep. Measured - dragging the window to its 330px minimum fired
        # the window's event while the viewport still reported the old 638,
        # the scale was computed from that, and nothing fired again when the
        # layout settled to 292. The cards stayed 612px wide inside a 292px
        # viewport, and with the horizontal bar off that is not a scroll, it
        # is content that has simply gone.
        self.scroll.viewport().installEventFilter(self)
        self.scroll.setToolTip(wrapped(
            "Click a step, scroll, or use the arrow keys to browse. Follows"
            " the game automatically while the overlay runs."))

        # A scrollbar that means WHERE AM I IN THE BUILD, which is the only
        # thing scrolling has ever meant in this window.
        #
        # There used to be two answers to that question and they disagreed.
        # The scroll area owned a bar whose range was the card stack, while
        # the wheel walked the build - so the bar reached its end and stopped
        # while the wheel kept going, its thumb never matched how much build
        # was left, and its arrows moved a few pixels where the wheel moved a
        # step. All three of those were reported separately; they were one
        # bug. The card count now follows the window's height, so the stack
        # always fits and the scroll area has nothing of its own to scroll -
        # leaving this bar as the single meaning.
        self.position = QScrollBar(Qt.Orientation.Vertical, self)
        self.position.setToolTip(wrapped("Where you are in the build."))
        self.position.valueChanged.connect(self._position_moved)
        self.position.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.scroll)
        # The position bar is NOT in this layout. It floats over the right
        # edge of the card area the way the chrome floats over the top, so
        # the strip it used to reserve goes back to the cards - fourteen
        # pixels that were spent all match on a control nobody can use while
        # the overlay is following the game.
        #
        # The cost is that it overlays the right edge of a card while it is
        # up, where "by M:SS · N vills" is right-aligned. That is the trade
        # the author asked for, and it is only paid while the pointer is
        # actually on the window.

        # The alert bands sit under the cards, where the overlay puts them
        # too. Hidden and zero-height unless there is something to say, so a
        # quiet match costs this window no space at all.
        self.bands = AlertBands()

        layout = QVBoxLayout(self)
        # Set rather than inherited from the platform, because the gutter is
        # load-bearing now: it is the strip of window belonging to no child
        # widget, and so the only place the resize hit-test can see the
        # pointer at all.
        layout.setContentsMargins(WINDOW_MARGIN, WINDOW_MARGIN,
                                  WINDOW_MARGIN, WINDOW_MARGIN)
        layout.addLayout(body)
        layout.addWidget(self.bands)

        # The chrome is a child of the window and NOT of the layout - see
        # ChromeBar for why that is the whole point. Raised so it sits over
        # the top card rather than under it.
        self.chrome = ChromeBar(header, self)
        self.chrome.close_button.clicked.connect(self.close)
        self.chrome.set_title("Loom — Build preview")
        self.chrome.raise_()
        self._chrome_effect = QGraphicsOpacityEffect(self.chrome)
        self.chrome.setGraphicsEffect(self._chrome_effect)
        # The position bar fades with the chrome, but is NOT hidden with it:
        # it lives in `body`, so hiding it would reflow the cards, which is
        # exactly what floating the chrome exists to avoid.
        self._position_effect = QGraphicsOpacityEffect(self.position)
        self.position.setGraphicsEffect(self._position_effect)

        # The ground the cards sit on. With no native frame this is the whole
        # window, and the platform's window grey behind dark cards reads as
        # an unfinished dialog. Set as a palette rather than a stylesheet so
        # it inherits to the scroll area and its viewport without fighting
        # the widgets' own one-line colour rules.
        ground = self.palette()
        ground.setColor(QPalette.ColorRole.Window, WINDOW_BACKGROUND)
        ground.setColor(QPalette.ColorRole.Base, WINDOW_BACKGROUND)
        ground.setColor(QPalette.ColorRole.WindowText, TEXT)
        ground.setColor(QPalette.ColorRole.Text, TEXT)
        self.setPalette(ground)
        # NOT autoFillBackground: the ground is painted by hand in
        # paintEvent, because a rounded window has to leave the pixels
        # outside its corners genuinely empty rather than merely dark - and
        # because the ground fades with the chrome, which a palette fill
        # cannot do.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # The viewport and the scroll area's frame paint NOTHING of their
        # own. Whatever shows between and around the cards is the window's
        # ground while the chrome is up, and the desktop once it has gone -
        # the disappearing is the point, so anything that fills a rectangle
        # here would quietly put the box back.
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        # setWidget() silently turned autoFillBackground ON for the column
        # when it was adopted - Qt's documented behaviour, and invisible in
        # this file because nothing here asked for it. Found by rendering
        # the window at rest and mapping which pixels were opaque: a solid
        # block exactly the scroll area's rectangle, surviving both switches
        # above.
        self.scroll.widget().setAutoFillBackground(False)

        # Arrow keys move through the build, so the window has to be able to
        # hold focus. The zoom buttons and the bar decline it (NoFocus) so a
        # click on them does not take the keys away from the build.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Writing the window geometry per pixel of a drag would hammer the
        # settings file, so the save waits for the drag to hold still.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._remember_geometry)

        # Where the pointer is, and where the fade has got to. Two jobs, two
        # timers, both stopped while the window is not on screen.
        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(HOVER_POLL_MS)
        self._hover_timer.timeout.connect(self._check_pointer)
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_TICK_MS)
        self._fade_timer.timeout.connect(self._fade_step)

        # Without this the window is told about the pointer only while a
        # button is held, and the resize cursors never appear.
        self.setMouseTracking(True)

        # Wear the saved appearance from the first paint - the cards were
        # built at their designed defaults above.
        for card in self.cards:
            card.set_appearance(self._card_opacity, self._text_scale)

        self._apply_minimum_size()
        self.resize(*(config.browser_window() or DEFAULT_WINDOW))
        remembered = config.browser_position()
        # The other half of the launcher's bug. This one never clamped at all,
        # so it remembers a second monitor correctly and is stranded off the
        # desktop when that monitor goes away - a window nobody can find,
        # which looks exactly like the checkbox not working. Same rule as the
        # launcher and the overlay panel now: believed if it lands anywhere.
        if remembered is not None:
            screens = placement.screen_rects(QApplication.instance())
            if placement.visible_on(screens, *remembered,
                                    self.width(), self.height()):
                self.move(*remembered)
            else:
                print(f"[preview] the saved window position {remembered} is "
                      f"off every screen - opening in the default spot")
        self._show_mode()

    # ---- window behaviour ----------------------------------------------

    def _remember_geometry(self):
        """Save where and how big the player left this window."""
        config.set_browser_window(self.width(), self.height())
        config.set_browser_position(self.x(), self.y())

    def closeEvent(self, event):
        """The titlebar X hides the preview rather than destroying it, and
        tells the launcher so its checkbox can follow."""
        event.accept()
        self.closed.emit()

    def moveEvent(self, event):
        super().moveEvent(event)
        self._save_timer.start(SAVE_SIZE_AFTER_MS)

    def eventFilter(self, watched, event):
        """Rescale the cards whenever the viewport's width changes.

        Cannot oscillate: _card_width subtracts a scrollbar's width whether
        or not one is showing, so the answer does not depend on whether the
        last change made the bar appear.
        """
        if (watched is self.scroll.viewport()
                and event.type() == QEvent.Type.Resize):
            self._relayout()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The chrome is placed by hand because it is not in the layout, so
        # this is the only thing that will ever move it.
        self._place_chrome()
        # Otherwise only the geometry save. The card scale rides the
        # viewport's resize instead - see eventFilter.
        self._save_timer.start(SAVE_SIZE_AFTER_MS)

    def showEvent(self, event):
        """Open with the chrome up, then take it away.

        A window that opened showing no controls at all would read as broken
        rather than as designed, and nothing would teach the player that
        moving the pointer onto it brings them back. Showing them once and
        then fading them demonstrates the rule in two seconds.
        """
        super().showEvent(event)
        self._opened_at = time.monotonic()
        self._left_at = None
        self._set_chrome_opacity(1.0)
        self._place_chrome()
        self._hover_timer.start()

    def hideEvent(self, event):
        """A closed preview costs nothing: both timers stop with the window."""
        super().hideEvent(event)
        self._hover_timer.stop()
        self._fade_timer.stop()

    def _place_chrome(self):
        """Across the top of the window, as tall as its controls need - and
        the position bar down the right edge of the cards."""
        width = max(1, self.width())
        height = max(1, self.chrome.heightForWidth(width))
        self.chrome.setGeometry(0, 0, width, height)
        # Over the card area's right edge rather than beside it. Asked of the
        # scroll area rather than computed, so it follows the layout's own
        # margins instead of a second copy of them that could drift.
        area = self.scroll.geometry()
        bar = max(1, self.position.sizeHint().width())
        # It starts BELOW the chrome rather than at the top of the card area,
        # because the chrome's close button is in the same corner and the two
        # would otherwise be drawn on top of each other. Nothing is lost by
        # it: the bar and the chrome come and go together, so the strip this
        # gives up is a strip the bar is never visible in anyway.
        top = max(area.top(), height)
        self.position.setGeometry(area.right() - bar + 1, top,
                                  bar, max(1, area.bottom() - top + 1))
        self.position.raise_()
        # Last, so the chrome stays above the bar wherever they still meet.
        self.chrome.raise_()

    def _check_pointer(self):
        """Where the pointer is, and therefore where the chrome is going."""
        now = time.monotonic()
        inside = self.frameGeometry().contains(QCursor.pos())
        if inside:
            self._left_at = None
        elif self._left_at is None:
            self._left_at = now
        lingering = (self._left_at is not None
                     and now - self._left_at < CHROME_LINGER_SECONDS)
        opening = now - self._opened_at < CHROME_OPENING_SECONDS
        self._chrome_wanted = chrome_target(inside or lingering or opening,
                                            self._chip_warning)
        if (self._chrome_wanted != self._chrome_opacity
                and not self._fade_timer.isActive()):
            self._faded_at = now
            self._fade_timer.start()

    def _fade_step(self):
        """One step of the fade, measured in elapsed time rather than ticks."""
        now = time.monotonic()
        seconds = now - self._faded_at
        self._faded_at = now
        self._set_chrome_opacity(
            stepped_opacity(self._chrome_opacity, self._chrome_wanted,
                            seconds))
        if self._chrome_opacity == self._chrome_wanted:
            self._fade_timer.stop()

    def _set_chrome_opacity(self, value):
        self._chrome_opacity = value
        self._chrome_effect.setOpacity(value)
        self._position_effect.setOpacity(value)
        gone = value <= CHROME_DEAD_OPACITY
        # Hidden rather than merely invisible, because an invisible button
        # that still takes clicks is a trap - and because a hidden widget
        # lets the click through to the card underneath instead of eating it.
        # Hiding costs nothing here precisely because the chrome is not in
        # the layout: nothing moves.
        self.chrome.setVisible(not gone)
        # The bar can be hidden outright now that it is out of the layout,
        # which is better than merely making it invisible: a hidden widget
        # takes no clicks and lets them through to the card underneath.
        self.position.setVisible(not gone)
        self.update()

    # ---- moving and resizing a window with no frame --------------------

    def edge_at(self, global_point):
        """Which of this window's edges a point on the screen is reaching for.

        Takes a screen position rather than a local one so the chrome can ask
        the same question about itself - it lies across the top edge, and the
        answer has to be about the WINDOW either way.
        """
        local = self.mapFromGlobal(global_point)
        return resize_edges(local.x(), local.y(),
                            self.width(), self.height())

    def begin_resize(self, edges):
        """Hand a resize to the window manager. False if there was no edge."""
        handle = self.windowHandle()
        if not edges or handle is None:
            return False
        wanted = Qt.Edge(0)
        for name in edges:
            wanted |= QT_EDGES[name]
        return handle.startSystemResize(wanted)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        shape = EDGE_CURSORS.get(
            self.edge_at(event.globalPosition().toPoint()))
        if shape is None:
            self.unsetCursor()
        else:
            self.setCursor(shape)

    def leaveEvent(self, event):
        """Drop the resize cursor on the way out.

        Qt sends this when the pointer crosses into a CHILD as well as when
        it leaves the window - useless for the fade, which is why that polls
        instead, and exactly right here. Once the pointer is over a card the
        window stops hearing about it, and a resize cursor left set would be
        inherited by every child that has none of its own.
        """
        super().leaveEvent(event)
        self.unsetCursor()

    def mousePressEvent(self, event):
        """Grab an edge and let the window manager do the resize itself."""
        if event.button() == Qt.MouseButton.LeftButton:
            if self.begin_resize(
                    self.edge_at(event.globalPosition().toPoint())):
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        """The rounded ground the cards sit on, and the outline around it.

        Both ride the chrome fade, and so does the repaint that carries
        them: at rest this window paints NOTHING of its own. The cards float
        directly on the desktop, which is the disappearing the author asked
        for - the ground turned out to be chrome like everything else, just
        chrome shaped like a background.

        Painted rather than filled by the palette because the window is
        translucent: everything outside the corner arcs - and at rest, the
        whole rectangle - has to be left genuinely empty, not merely dark.
        """
        solidity = ground_opacity(self._chrome_opacity,
                                  self._rest_ground, self._hover_ground)
        if solidity <= 0.01 and self._chrome_opacity <= CHROME_DEAD_OPACITY:
            # Nothing at all. At rest with no ground asked for, the window
            # has no ground, no frame and no outline - the cards paint
            # themselves and everything between them is desktop.
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if solidity > 0.01:
            painter.setOpacity(solidity)
            ground = QPainterPath()
            ground.addRoundedRect(QRectF(self.rect()),
                                  WINDOW_RADIUS, WINDOW_RADIUS)
            painter.fillPath(ground, WINDOW_BACKGROUND)
        if self._chrome_opacity <= CHROME_DEAD_OPACITY:
            # A resting ground is the player's setting; the outline is still
            # chrome and stays hover-only.
            return
        painter.setOpacity(self._chrome_opacity)
        # Half a pixel in, so the one-pixel stroke lands inside the window
        # instead of straddling its edge and losing half its width.
        outline = QPainterPath()
        outline.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
            WINDOW_RADIUS, WINDOW_RADIUS)
        painter.setOpacity(self._chrome_opacity)
        painter.strokePath(outline, QPen(BORDER))

    def _apply_minimum_size(self):
        """Stop the window shrinking past what the smallest card needs.

        This is what makes turning the horizontal scrollbar off safe rather
        than a way to clip content. The card can never be wider than the
        viewport by construction, but only while a card at MIN_CARD_SCALE
        still FITS - drag the window narrower than that and the floor wins,
        the card overflows, and with no bar to reach it the right-hand side
        is simply gone. So the floor decides the window's minimum, measured
        from the same constants rather than guessed at.
        """
        chrome = self.layout().contentsMargins()
        # Everything the window spends before a card gets any of it.
        #
        # The position bar used to count, and getting that wrong once cost
        # six pixels of horizontal overflow at the minimum width - with the
        # bar switched off, six pixels of card that simply were not there.
        # It is out of the sum again now, but for the opposite reason and
        # safely: it no longer sits in the layout at all, so it takes none of
        # the width a card is measured against. It floats over the cards
        # instead - see _place_chrome.
        spent = (2 * STACK_MARGIN
                 + self.scroll.verticalScrollBar().sizeHint().width()
                 + chrome.left() + chrome.right()
                 + 2 * self.scroll.frameWidth()
                 + MINIMUM_SLACK)
        width = round(CARD_WIDTH * MIN_CARD_SCALE) + spent

        # MIN_VISIBLE_CARDS' worth of height: the step you are on, the one
        # before it and at least one ahead. Fewer than that and it has
        # stopped being a preview of anything.
        # The text multiplier counts: it makes every card taller, and a
        # minimum that ignored it would let the window shrink below three
        # cards of the text the player actually reads.
        # And the tallest step in THIS build counts, for the same
        # reason: cards grow to fit their instructions, so a build
        # holding a seven-item step needs a taller floor than one that
        # never exceeds three. Generous by construction - it assumes
        # three tall steps in a row, which no shipped build has - and a
        # floor a few pixels roomy costs nothing where one a few pixels
        # short costs a scrollbar that must never appear.
        tallest = max(self._designed_heights(), default=CARD_HEIGHT)
        card = round(tallest * MIN_CARD_SCALE * self._text_scale)
        cards = card * MIN_VISIBLE_CARDS + CARD_GAP * (MIN_VISIBLE_CARDS - 1)
        height = (cards + 2 * STACK_MARGIN
                  + chrome.top() + chrome.bottom()
                  + 2 * self.scroll.frameWidth()
                  + MIN_HEIGHT_CHROME + MINIMUM_SLACK)
        self.setMinimumSize(width, height)

    def _relayout(self):
        """Settle the card size and how many cards there are, together.

        They are one decision: the size decides how many fit, and the count
        has to be right or the stack does not fit the window it is in - which
        is what keeps the scroll area with nothing of its own to scroll, and
        the position bar the only meaning of scrolling here.
        """
        fit = card_scale(self._card_width())
        scale = zoomed(self._chosen, fit)

        # The height the stack may actually use - the viewport less the same
        # margins the width calculation takes off. Missing these is how the
        # cards ended up 12px taller than the space they had.
        height = max(1, (self.scroll.viewport().height() or self.height())
                     - 2 * STACK_MARGIN)
        count = cards_for_steps(self._card_heights(scale), height,
                                CARD_GAP)

        # A chosen size too tall for the window shrinks, exactly as one too
        # wide does: the floor is the only thing the window may not override.
        # Without this the stack overflows and the scroll area grows a bar of
        # its own, which is the disagreement the position bar exists to end.
        if count <= MIN_VISIBLE_CARDS:
            room = height - (MIN_VISIBLE_CARDS - 1) * CARD_GAP
            # A pixel off the top: the card's height is ROUNDED from this, so
            # a scale that fits exactly can round up into one pixel of
            # overflow - and one pixel is enough for a scrollbar to appear.
            per_card = max(1, (room - 1) / MIN_VISIBLE_CARDS)
            # Measured against the TALLEST of the cards that must fit,
            # not the designed height: a seven-item step is half again as
            # tall as a one-item step, and dividing the room by the small
            # one leaves the big one hanging out of the window.
            tallest = max(self._designed_heights()[:MIN_VISIBLE_CARDS],
                          default=CARD_HEIGHT)
            scale = max(MIN_CARD_SCALE,
                        min(scale, per_card / (tallest * self._text_scale)))
            count = cards_for_steps(self._card_heights(scale), height,
                                    CARD_GAP)

        self._apply_scale(scale)
        self._apply_count(count)
        self._update_zoom_label()
        # The bar is placed from the scroll area's geometry, so it has to be
        # re-placed whenever that settles rather than only on a resize.
        #
        # QUEUED, and it has to be. Placing it inline here recurses until the
        # stack gives out: this method is reached from the viewport's resize
        # filter, and moving the chrome invalidates the layout that owns the
        # viewport, which resizes it, which arrives back here. Measured as a
        # hard 0xC0000409 with no traceback at all. Deferring to the next turn
        # of the event loop breaks the cycle without needing a re-entrancy
        # flag to paper over it.
        QTimer.singleShot(0, self._place_chrome)
        # The bands are sized in the same designed pixels the cards are, so
        # they grow and shrink with them instead of staying a fixed strip.
        self._refresh_bands()

    def _apply_count(self, count):
        """Show this many cards and hide the rest."""
        if count == self._visible:
            return
        self._visible = count
        for index, card in enumerate(self.cards):
            card.setVisible(index < count)
        self._deal()

    def _zoom(self, delta):
        """One press of + or -. Starts from whatever is on screen now, so the
        first press nudges what the player can see rather than jumping to some
        remembered number."""
        self._chosen = max(MIN_CARD_SCALE,
                           min(MAX_CARD_SCALE, self._scale + delta))
        self._relayout()

    def _zoom_auto(self):
        """Back to fitting the window, which is where this window started."""
        self._chosen = None
        self._relayout()

    def _update_zoom_label(self):
        self.zoom_label.setText(
            f"{round(self._scale * 100)}%"
            + ("" if self._chosen is None else " · manual"))
        # + is pointless once the cards already fill the width, and - once
        # they are as small as they are allowed to get.
        self.zoom_in.setEnabled(self._scale < MAX_CARD_SCALE
                                and self._scale < card_scale(
                                    self._card_width()) - 0.001)
        self.zoom_out.setEnabled(self._scale > MIN_CARD_SCALE)
        self.zoom_auto.setEnabled(self._chosen is not None)

    def _designed_heights(self):
        """Each card's height in DESIGNED pixels, in stack order.

        Scale-free, so it can help CHOOSE a scale without the circular
        dependency of measuring at one not yet decided. The column decision
        is made at scale 1.0 for the same reason; columns only ever reduce
        the height, so this is the safe side to be wrong on.
        """
        steps = self.build.steps if self.build else []
        return [CARD_HEIGHT + step_plan(None if index is None
                                        else steps[index], 1.0).extra
                for index in visible_indices(self.focus, len(steps),
                                             MAX_VISIBLE_CARDS)]

    def _card_heights(self, scale):
        """Each card's real height at this scale, in stack order."""
        steps = self.build.steps if self.build else []
        return [card_height(None if index is None else steps[index],
                            scale, self._text_scale)
                for index in visible_indices(self.focus, len(steps),
                                             MAX_VISIBLE_CARDS)]

    def _card_width(self):
        """The width a card may take, this instant."""
        viewport = self.scroll.viewport().width() or (self.width()
                                                      - CARD_MARGINS)
        bar = self.scroll.verticalScrollBar().sizeHint().width()
        return usable_card_width(viewport, bar)

    def _apply_scale(self, scale):
        if scale == self._scale:
            return
        self._scale = scale
        # Resource icons are baked to a height at load, so a new scale means
        # loading them again - once, shared by all four cards.
        self._icons = load_resource_icons(round(ICON_HEIGHT * scale))
        for card in self.cards:
            card.set_scale(scale, self._icons)

    # ---- the build and where I am in it --------------------------------

    def set_build(self, build):
        """A different build order: start the view from the top."""
        self.build = build
        self.focus = 0
        self.checklist = (checklist_module.Checklist(build)
                          if build is not None else None)
        self._last_index = None
        # A step's height depends on how many instructions it holds, so the
        # window's floor depends on the build. Asked again here rather than
        # only at startup.
        self._apply_minimum_size()
        # The chrome's strip names the build, which is a better use of it
        # than the caption's "Loom — Build preview" ever was.
        self.chrome.set_title(build.name if build is not None
                              else "Loom — Build preview")
        self._deal()

    def set_focus(self, index):
        if self.build is None:
            return
        self.focus = max(0, min(index, len(self.build.steps) - 1))
        # Moving the focus changes WHICH steps are on the stack, and steps
        # are no longer all one height - so how many fit has to be settled
        # again before they are dealt.
        self._relayout()
        self._deal()

    def _deal(self):
        """Hand each visible card its step for the current focus."""
        steps = self.build.steps if self.build else []
        total = len(steps)
        showing = self.cards[:self._visible]
        # One step of history, the step you are on, and the rest ahead. The
        # focused card stays second from the top whatever the count.
        roles = ["previous", "current"] + ["next"] * max(0, len(showing) - 2)
        for card, role, index in zip(
                showing, roles,
                visible_indices(self.focus, total, len(showing))):
            if index is None:
                card.show_empty()
            else:
                states = ()
                if self.checklist is not None:
                    states = self.checklist.states(
                        index, len(steps[index].items))
                card.show_step(index, steps[index], total, role, states)
            if role != "current":
                card.clear_live()
        self._sync_position()

    def _sync_position(self):
        """Point the position bar at the focused step without echoing back.

        blockSignals because setValue would otherwise call _position_moved,
        which calls set_focus, which deals again - a loop that ends in a
        recursion error rather than anything visible.
        """
        steps = len(self.build.steps) if self.build else 0
        self.position.blockSignals(True)
        self.position.setRange(0, max(0, steps - 1))
        # The thumb is as long a slice of the bar as the window is of the
        # build, which is what makes it mean "how much is left".
        self.position.setPageStep(max(1, self._visible - 1))
        self.position.setValue(self.focus)
        self.position.setEnabled(steps > 1 and not self.following)
        self.position.blockSignals(False)

    def _position_moved(self, value):
        if not self.following:
            self.set_focus(value)

    # ---- live state from the overlay -----------------------------------

    def apply_state(self, payload):
        """One decoded statefeed payload. Live wins; unusable frees the view."""
        if self.build is None:
            return
        if not payload.get("usable"):
            self._stop_following()
            return

        self.following = True
        # What the OVERLAY is doing, so this window never claims to be
        # following the game while the panel says it is not. The step is
        # still driven by "idx" either way - a manual cursor rides in the
        # same semantics, so live_focus needs no special case.
        self.follow_mode = payload.get("mode")
        self._hold = payload.get("hold")
        index = payload.get("idx", -1)
        # The checklist follows where the GAME is, which is not where the
        # panel is looking while a hotkey holds the cursor somewhere else.
        # Older overlays send no "auto"; falling back to "idx" is what they
        # always meant, since without the field there was no cursor either.
        reached = payload.get("auto", index)
        if self.checklist is not None:
            # A match that has plainly restarted takes the observed ticks
            # with it. Assumptions need no clearing - they are a view of the
            # index and follow it backwards on their own.
            if self._last_index is not None and reached < self._last_index - 1:
                self.checklist.reset()
            self.checklist.observe(reached)
            self.checklist.apply_observed(payload.get("ticks"))
        self._last_index = reached
        self.focus = live_focus(index, len(self.build.steps))
        # Steps differ in height, so which ones are on the stack changes how
        # many fit. Settle that before dealing them.
        self._relayout()
        self._deal()
        self.cards[1].set_live(payload.get("vills"), payload.get("t"),
                               payload.get("pace"), payload.get("res"))
        self._alerts = [tuple(entry) for entry in payload.get("alerts") or []]
        self._refresh_bands()
        self._show_mode()

    def overlay_stopped(self):
        """The overlay process ended; the stack stays put, browsing resumes."""
        self._stop_following()

    def _set_alerts_enabled(self, enabled):
        config.set_preview_alerts(enabled)
        self.set_show_alerts(enabled)

    def _set_overlay_disabled(self, disabled):
        config.set_overlay_disabled(disabled)
        # The launcher owns the process; it decides whether anything needs
        # doing to a session that is already running.
        self.overlay_disabled_changed.emit(bool(disabled))

    def apply_appearance(self):
        """Re-read the appearance settings and wear them, immediately.

        The launcher's Preview tab calls this on every slider tick. It can,
        because this window lives in the launcher's own process - the one
        settings page whose changes do not wait for a restart.
        """
        self._rest_ground = config.preview_rest_opacity()
        self._hover_ground = config.preview_hover_opacity()
        self._card_opacity = config.preview_card_opacity()
        self._text_scale = config.preview_text_scale()
        for card in self.cards:
            card.set_appearance(self._card_opacity, self._text_scale)
        # Text size changes every card's height, so the count and the
        # minimum both have to resettle around the new stack.
        self._apply_minimum_size()
        self._relayout()
        self.update()

    def set_show_alerts(self, enabled):
        """Turn the alert bands on or off. The launcher's checkbox calls this."""
        self._show_alerts = bool(enabled)
        self._refresh_bands()

    def _refresh_bands(self):
        """Show whatever the overlay last said, if bands are wanted at all.

        Nothing is shown while the overlay is not following: an alert is a
        statement about a game in progress, and holding the last one on screen
        after the game went away is the stale-reading failure in a new place.
        """
        wanted = self._alerts if (self._show_alerts and self.following) else []
        self.bands.show_alerts(wanted, self._scale)

    def _stop_following(self):
        if self.following:
            self.following = False
            self.cards[1].clear_live()
            self._alerts = []
            self._refresh_bands()
        self.follow_mode = None
        # The bar is dead while the game drives; browsing gives it back.
        self._sync_position()
        self._show_mode()

    # ---- browsing ------------------------------------------------------

    def _card_clicked(self, index):
        # Dead while following: the game decides where to look, not the
        # mouse. Browsing resumes the moment there is no game to follow.
        if not self.following:
            self.set_focus(index)

    def wheelEvent(self, event):
        if self.following or self.build is None:
            return
        step = -1 if event.angleDelta().y() > 0 else 1
        self.set_focus(self.focus + step)

    def keyPressEvent(self, event):
        """Arrow keys walk the build, the same cursor everything else moves.

        Dead while following, exactly like the mouse: the game decides where
        to look, and a key that silently fought it would be the "panel quietly
        stopped following" failure in a second window.
        """
        if self.following or self.build is None:
            super().keyPressEvent(event)
            return

        key = event.key()
        page = max(1, self._visible - 1)
        moves = {
            Qt.Key.Key_Up: -1, Qt.Key.Key_Left: -1,
            Qt.Key.Key_Down: 1, Qt.Key.Key_Right: 1,
            Qt.Key.Key_PageUp: -page, Qt.Key.Key_PageDown: page,
        }
        if key in moves:
            self.set_focus(self.focus + moves[key])
        elif key == Qt.Key.Key_Home:
            self.set_focus(0)
        elif key == Qt.Key.Key_End:
            self.set_focus(len(self.build.steps) - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _show_mode(self):
        if self.following and self.follow_mode == "manual":
            # The overlay is being driven by hand. Saying "following game"
            # here would contradict the panel, and one of the two windows
            # would be lying.
            self.chip.setText("manual — the overlay is not following the game")
            color = NOT_FOLLOWING_COLOR
        elif self.following and self.follow_mode == "holding":
            # A step key was pressed and automatic following resumes shortly.
            # This window used to call that "following game", on the grounds
            # that a hold resolves itself in seconds - but for those seconds
            # it was saying the opposite of what the panel said, which is the
            # one thing these two windows must never do. The countdown comes
            # over the statefeed so both are reading the same clock.
            self.chip.setText(
                f"manual — resuming in {self._hold}s" if self._hold
                else "manual — resuming shortly")
            color = HOLDING_COLOR
        elif self.following:
            self.chip.setText("following game")
            color = ON_PACE_COLOR
        else:
            # Nothing worth saying: there is a scrollbar, the cards respond to
            # clicks and the arrow keys, and the space is better spent on the
            # two switches beside it. The other two states are NOT decoration
            # - "manual" is the panel telling you it has stopped following the
            # game, which CLAUDE.md requires it never do quietly, and this
            # window must not contradict the panel about it.
            self.chip.setText("")
            color = FAINT_TEXT
        # Whether that chip is a warning decides whether the chrome may
        # fade at all - see chrome_target. "manual" and "holding" are the
        # panel saying it has stopped following the game, which the player
        # must be able to see without reaching for the window.
        self._chip_warning = bool(
            self.following and self.follow_mode in ("manual", "holding"))
        self.chip.setStyleSheet(
            f"color: rgb({color.red()}, {color.green()}, {color.blue()});"
            " font-size: 9pt;")
