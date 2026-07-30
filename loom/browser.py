"""
Loom — the build preview: a stack of step cards in its own window.

Four cards in a column: the step just done, the CURRENT step (highlighted),
and the two after it. Each card shows what the overlay shows for a step -
instruction with icons, "by M:SS · N vills", the villagers-per-resource
targets - so the panel reads as four overlays stacked, which is the point:
study the whole build before a match on the second monitor, then let it
follow along during one.

The preview is a separate top-level window rather than a launcher column,
because the window manager is the best size control there is: drag the
window bigger and the cards scale with it - one uniform factor derived from
the window's width. No spinboxes, no settings; the size persists like any
window size should. An ordinary native Wayland window on purpose: nothing
here needs the overlay's XWayland/ToolTip machinery.

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

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (QLabel, QScrollArea, QVBoxLayout, QHBoxLayout,
                             QWidget)

from . import config
from .build_order import format_time
from .overlay import (AHEAD_COLOR, BACKGROUND, BORDER, DIM_TEXT, FAINT_TEXT,
                      ICON_HEIGHT, ON_PACE_COLOR, TEXT, describe_pace,
                      draw_resource_row, draw_segments, elide,
                      load_resource_icons)

# The slots in the stack, top to bottom: previous, current, next, next-after.
CARD_SLOTS = 4

# The designed card, at scale 1.0. Same content width as the overlay panel,
# so instructions elide identically in both windows; shorter, because a card
# has no THEN row or alert bands.
CARD_WIDTH = 560
CARD_HEIGHT = 132

# How far window-resizing can push the cards. The lower bound keeps a
# carelessly small window readable-ish; the upper stops a full-screen window
# from producing poster-sized villagers.
MIN_CARD_SCALE = 0.5
MAX_CARD_SCALE = 3.0

# Room the window chrome takes around the cards: layout margins plus a
# vertical scrollbar's worth, so the scale settles instead of oscillating
# when the scrollbar appears.
CARD_MARGINS = 40

# The window's size before the player has ever resized it.
DEFAULT_WINDOW = (CARD_WIDTH + CARD_MARGINS, 640)

# The current card is brighter than BACKGROUND, the way the eye should land.
CURRENT_BACKGROUND = QColor(26, 26, 32, 235)

# How much the neighbours fade: one knob dims text, icons and resource colors
# uniformly, instead of hand-picking a faint variant of every color.
PREVIOUS_OPACITY = 0.55
EMPTY_OPACITY = 0.35

# How long a resize has to hold still before the window size is saved.
# Saving per resize event would write the settings file once per pixel of
# the drag.
SAVE_SIZE_AFTER_MS = 1000


def visible_indices(focus, step_count):
    """Which step index each of the 4 slots shows: [prev, focus, +1, +2].

    None for a slot that falls outside the build, so the first step has an
    empty slot above it and the last steps have empty slots below - the
    stack keeps its shape at the ends instead of jumping. A wild focus is
    clamped rather than refused; the caller's arithmetic stays simple.
    """
    if step_count <= 0:
        return [None] * CARD_SLOTS
    focus = max(0, min(focus, step_count - 1))
    return [index if 0 <= index < step_count else None
            for index in (focus - 1, focus, focus + 1, focus + 2)]


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
        if self._role == "current":
            painter.setBrush(CURRENT_BACKGROUND)
            painter.setPen(QColor(AHEAD_COLOR))
        else:
            painter.setBrush(BACKGROUND)
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


class BuildBrowser(QWidget):
    """The preview window: four cards, follow/browse switching, and a size
    that is simply the window's size."""

    closed = pyqtSignal()   # the player closed the window with its X

    def __init__(self):
        # No parent: a top-level window in its own right, resizable and
        # movable like anything else on the desktop.
        super().__init__()
        self.setWindowTitle("Loom — Build preview")
        self.build = None
        self.focus = 0
        self.following = False
        self._scale = 1.0

        self.chip = QLabel()
        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(self.chip)

        self._icons = load_resource_icons()
        self.cards = [StepCard(self._icons) for _ in range(CARD_SLOTS)]
        for card in self.cards:
            card.clicked.connect(self._card_clicked)

        # The cards live in a scroll area so a wide-but-short window scrolls
        # instead of clipping the stack.
        column = QWidget()
        stack = QVBoxLayout(column)
        for card in self.cards:
            stack.addWidget(card, alignment=Qt.AlignmentFlag.AlignHCenter)
        stack.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(column)
        self.scroll.setToolTip(
            "Click a step or scroll to browse; resize the window to grow the"
            " cards. Follows the game automatically while the overlay runs.")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.scroll)

        # Writing the window size per pixel of a drag would hammer the
        # settings file, so the save waits for the resize to hold still.
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(
            lambda: config.set_browser_window(self.width(), self.height()))

        self.resize(*(config.browser_window() or DEFAULT_WINDOW))
        self._show_mode()

    # ---- window behaviour ----------------------------------------------

    def closeEvent(self, event):
        """The titlebar X hides the preview rather than destroying it, and
        tells the launcher so its checkbox can follow."""
        event.accept()
        self.closed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The scale follows the width the viewport actually offers.
        self._apply_scale(card_scale(self.scroll.viewport().width()
                                     or self.width() - CARD_MARGINS))
        self._save_timer.start(SAVE_SIZE_AFTER_MS)

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
        """Hand each card its step for the current focus."""
        steps = self.build.steps if self.build else []
        total = len(steps)
        roles = ("previous", "current", "next", "next")
        for card, role, index in zip(self.cards, roles,
                                     visible_indices(self.focus, total)):
            if index is None:
                card.show_empty()
            else:
                card.show_step(index, steps[index], total, role)
            if role != "current":
                card.clear_live()

    # ---- live state from the overlay -----------------------------------

    def apply_state(self, payload):
        """One decoded statefeed payload. Live wins; unusable frees the view."""
        if self.build is None:
            return
        if not payload.get("usable"):
            self._stop_following()
            return

        self.following = True
        self.focus = live_focus(payload.get("idx", -1), len(self.build.steps))
        self._deal()
        self.cards[1].set_live(payload.get("vills"), payload.get("t"),
                               payload.get("pace"), payload.get("res"))
        self._show_mode()

    def overlay_stopped(self):
        """The overlay process ended; the stack stays put, browsing resumes."""
        self._stop_following()

    def _stop_following(self):
        if self.following:
            self.following = False
            self.cards[1].clear_live()
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

    def _show_mode(self):
        if self.following:
            self.chip.setText("following game")
            color = ON_PACE_COLOR
        else:
            self.chip.setText("browsing — click a step or scroll")
            color = FAINT_TEXT
        self.chip.setStyleSheet(
            f"color: rgb({color.red()}, {color.green()}, {color.blue()});"
            " font-size: 9pt;")
