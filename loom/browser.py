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

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (QApplication, QCheckBox, QLabel, QPushButton,
                             QScrollArea, QScrollBar, QVBoxLayout,
                             QHBoxLayout, QWidget)

from . import alerts as alerts_module
from . import config, placement
from .flowlayout import flow_row
from .build_order import format_time
from .overlay import (AHEAD_COLOR, ALERT_BAND_HEIGHT, ALERT_FULL_BRIGHT,
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


def cards_for_height(available_height, scale, gap):
    """How many cards fit a stack this tall, clamped to the sane range.

    The count is what makes the window fit its own contents: pick it from the
    height and the cards never need scrolling inside their own scroll area,
    which is what let the wheel and the scrollbar disagree about what
    scrolling meant.
    """
    card = max(1, round(CARD_HEIGHT * scale))
    step = card + max(0, gap)
    fits = (available_height + max(0, gap)) // step
    return max(MIN_VISIBLE_CARDS, min(MAX_VISIBLE_CARDS, int(fits)))


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
            self.setFixedSize(round(CARD_WIDTH * scale),
                              round(CARD_HEIGHT * scale))
            self.update()

    def _s(self, base):
        """A designed pixel value at the current scale."""
        return round(base * self._scale)

    def _pt(self, base):
        """A designed font size at the current scale, never 0."""
        return max(1, round(base * self._scale))

    # ---- what to show --------------------------------------------------

    def show_step(self, index, step, total, role):
        state = (index, step, total, role)
        if (self._index, self._step, self._total, self._role) != state:
            self._index, self._step, self._total, self._role = state
            self.update()

    def show_empty(self):
        if self._index is not None or self._live is not None:
            self._index = self._step = self._live = None
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
        self._draw_headline(painter)
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
            painter.setBrush(CURRENT_BACKGROUND)
            painter.setPen(QColor(AHEAD_COLOR))
        else:
            painter.setBrush(PAST_BACKGROUND if self._role == "previous"
                             else UPCOMING_BACKGROUND)
            painter.setPen(BORDER)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1),
                                self._s(10), self._s(10))

    def _draw_header(self, painter):
        painter.setFont(QFont("sans", self._pt(9), QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(self._s(16), self._s(22),
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
            painter.drawText(right - width, self._s(22), pace_text)
            right -= width + self._s(12)

            painter.setFont(QFont("sans", self._pt(9)))
            painter.setPen(DIM_TEXT)
            live_text = f"{format_time(game_time)} · {villagers} vills"
            width = painter.fontMetrics().horizontalAdvance(live_text)
            painter.drawText(right - width, self._s(22), live_text)
        else:
            when = ""
            if self._step.time is not None:
                when = f"by {format_time(self._step.time)} · "
            when += f"{self._step.villager_count} vills"
            painter.setFont(QFont("sans", self._pt(9)))
            painter.setPen(FAINT_TEXT)
            width = painter.fontMetrics().horizontalAdvance(when)
            painter.drawText(right - width, self._s(22), when)

    def _draw_headline(self, painter):
        painter.setFont(QFont("sans", self._pt(15), QFont.Weight.Bold))
        painter.setPen(TEXT)
        available = self.width() - self._s(32)
        if self._step.details_segments:
            draw_segments(painter, self._step.details_segments, self._s(16),
                          self._s(56), available,
                          icon_height=self._s(24), spacing=self._scale)
        else:
            painter.drawText(self._s(16), self._s(56),
                             elide(painter, self._step.details, available))

        painter.setFont(QFont("sans", self._pt(10)))
        painter.setPen(DIM_TEXT)
        y = self._s(78)
        # Cap at two footnotes, same as the overlay - a card is not a manual.
        for row, segments in enumerate(self._step.footnotes_segments[:2]):
            painter.drawText(self._s(16), y, "·")
            if segments:
                draw_segments(painter, segments, self._s(28), y,
                              self.width() - self._s(44),
                              icon_height=self._s(16), spacing=self._scale)
            elif row < len(self._step.footnotes):
                painter.drawText(self._s(28), y,
                                 elide(painter, self._step.footnotes[row],
                                       self.width() - self._s(44)))
            y += self._s(18)

    def _draw_resources(self, painter):
        y = self.height() - self._s(14)
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
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Loom — Build preview")
        self.build = None
        self.focus = 0
        self.following = False
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

        self.chip = QLabel()

        # Zoom, top left. The buttons resize the CARDS and nothing else; the
        # window is the player's own business.
        self.zoom_out = QPushButton("−")
        self.zoom_out.setToolTip("Smaller cards.")
        self.zoom_out.clicked.connect(lambda: self._zoom(-ZOOM_STEP))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setToolTip("Bigger cards.")
        self.zoom_in.clicked.connect(lambda: self._zoom(ZOOM_STEP))
        self.zoom_auto = QPushButton("↺")
        self.zoom_auto.setToolTip(
            "Back to fitting the cards to the window automatically.")
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
        self.alerts_toggle.setToolTip(
            "Show the overlay's TC IDLE and HOUSE SOON bands in this window,"
            " so it can be played from on a second monitor.")
        self.alerts_toggle.setChecked(config.preview_alerts())
        self.alerts_toggle.toggled.connect(self._set_alerts_enabled)

        self.disable_overlay_toggle = QCheckBox("No overlay")
        self.disable_overlay_toggle.setToolTip(
            "Keep the overlay panel off the game permanently: it will not"
            " appear when you press Start overlay, and stays away until this"
            " is unticked. Loom still reads the game, records the match and"
            " feeds this window - only the panel over the game is gone.\n\n"
            "This is the REMEMBERED setting. The launcher's Hide overlay"
            " button and Ctrl+Shift+0 are the temporary version and are"
            " forgotten when Loom closes.")
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
        self.scroll.setToolTip(
            "Click a step, scroll, or use the arrow keys to browse. Follows"
            " the game automatically while the overlay runs.")

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
        self.position = QScrollBar(Qt.Orientation.Vertical)
        self.position.setToolTip("Where you are in the build.")
        self.position.valueChanged.connect(self._position_moved)
        self.position.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.scroll)
        body.addWidget(self.position)

        # The alert bands sit under the cards, where the overlay puts them
        # too. Hidden and zero-height unless there is something to say, so a
        # quiet match costs this window no space at all.
        self.bands = AlertBands()

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addLayout(body)
        layout.addWidget(self.bands)

        # Arrow keys move through the build, so the window has to be able to
        # hold focus. The zoom buttons and the bar decline it (NoFocus) so a
        # click on them does not take the keys away from the build.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # Writing the window geometry per pixel of a drag would hammer the
        # settings file, so the save waits for the drag to hold still.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._remember_geometry)

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
        # Only the geometry save. The card scale rides the viewport's resize
        # instead - see eventFilter.
        self._save_timer.start(SAVE_SIZE_AFTER_MS)

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
        # Everything the window spends before a card gets any of it. The
        # position bar counts: it was added beside the scroll area and left
        # out of this sum, and the result was six pixels of horizontal
        # overflow at the minimum width - with the bar switched off, six
        # pixels of card that simply were not there.
        spent = (2 * STACK_MARGIN
                 + self.scroll.verticalScrollBar().sizeHint().width()
                 + self.position.sizeHint().width()
                 + chrome.left() + chrome.right()
                 + 2 * self.scroll.frameWidth()
                 + MINIMUM_SLACK)
        width = round(CARD_WIDTH * MIN_CARD_SCALE) + spent

        # MIN_VISIBLE_CARDS' worth of height: the step you are on, the one
        # before it and at least one ahead. Fewer than that and it has
        # stopped being a preview of anything.
        cards = (round(CARD_HEIGHT * MIN_CARD_SCALE) * MIN_VISIBLE_CARDS
                 + CARD_GAP * (MIN_VISIBLE_CARDS - 1))
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
        count = cards_for_height(height, scale, CARD_GAP)

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
            scale = max(MIN_CARD_SCALE, min(scale, per_card / CARD_HEIGHT))
            count = cards_for_height(height, scale, CARD_GAP)

        self._apply_scale(scale)
        self._apply_count(count)
        self._update_zoom_label()
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
        self._deal()

    def set_focus(self, index):
        if self.build is None:
            return
        self.focus = max(0, min(index, len(self.build.steps) - 1))
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
                card.show_step(index, steps[index], total, role)
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
        self.focus = live_focus(payload.get("idx", -1), len(self.build.steps))
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
        self.chip.setStyleSheet(
            f"color: rgb({color.red()}, {color.green()}, {color.blue()});"
            " font-size: 9pt;")
