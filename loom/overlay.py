"""
Loom — the on-screen overlay.

A frameless, click-through, always-on-top panel that draws the current build
order step over the game.

Two details here are load-bearing and were found by testing, not by reading
documentation. Both have comments where they appear, because either one looks
like a mistake to tidy up later:

  * the window type must be ToolTip, not Tool
  * the process must run under XWayland, not Wayland

See the design notes for the full story.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

from . import alerts, build_order, config, paths

# Colors. Kept dark and low-contrast on purpose: this sits on top of a game
# and must be readable without dragging the eye away from it.
BACKGROUND = QColor(18, 18, 22, 205)
BORDER = QColor(255, 255, 255, 40)
TEXT = QColor(238, 238, 238)
DIM_TEXT = QColor(160, 160, 168)
FAINT_TEXT = QColor(120, 120, 128)

ON_PACE_COLOR = QColor(120, 220, 130)
AHEAD_COLOR = QColor(120, 200, 235)
SLIGHTLY_BEHIND_COLOR = QColor(235, 200, 100)
BEHIND_COLOR = QColor(240, 120, 110)

# Each resource in its own game color, so the numbers need no text labels - the
# color says which resource it is, the way the game's own HUD does.
RESOURCE_COLORS = {
    "food": QColor(230, 120, 120),   # red meat
    "wood": QColor(150, 200, 130),   # green
    "gold": QColor(235, 200, 90),    # yellow
    "stone": QColor(180, 185, 195),  # grey
}
RESOURCE_ORDER = ("wood", "food", "gold", "stone")

# The full word shown when there is no icon for a resource. Verbose on purpose:
# with no icon, a single letter would be ambiguous.
RESOURCE_LABELS = {"wood": "Wood", "food": "Food", "gold": "Gold", "stone": "Stone"}

# How tall to draw a resource icon, in pixels - roughly the height of the text
# it sits beside.
ICON_HEIGHT = 16


def load_resource_icons(height=ICON_HEIGHT):
    """Load whatever resource icons the player has put in icons/.

    Returns {name: QPixmap} for the icons that loaded, baked to the given
    height - the overlay passes its scaled height, everyone else gets the
    designed size. A resource with no icon is simply absent from the result,
    and the caller shows its word instead.

    Qt does not raise when an image file is missing or unreadable - it returns
    a "null" pixmap - so each one has to be checked with isNull().
    """
    icons = {}
    for name in RESOURCE_LABELS:
        for extension in (".png", ".webp", ".jpg"):
            path = paths.ICONS_DIR / f"{name}{extension}"
            if not path.exists():
                continue
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                icons[name] = pixmap.scaledToHeight(
                    height, Qt.TransformationMode.SmoothTransformation)
                break
    return icons

# Build-step icons (the @icon@ tokens in the build files), cached per token
# and per height. None is cached too: a missing image should cost one disk
# probe, not one per repaint.
_step_icon_cache = {}


def load_step_icon(token, height):
    """The picture for one @icon@ token, scaled to a text line. None if the
    library does not have it - the caller falls back to words."""
    key = (token, height)
    if key in _step_icon_cache:
        return _step_icon_cache[key]

    pixmap = None
    path = paths.ICON_LIBRARY_DIR / token
    if path.exists():
        loaded = QPixmap(str(path))
        if not loaded.isNull():
            pixmap = loaded.scaledToHeight(
                height, Qt.TransformationMode.SmoothTransformation)
    _step_icon_cache[key] = pixmap
    return pixmap


# A resource is only "off" the build if it is wrong by more than this. Being
# one villager out is not worth flagging.
RESOURCE_TOLERANCE = 1
OFF_TARGET_COLOR = QColor(240, 120, 110)

# How far off pace counts as fine, and as only slightly late. Villagers arrive
# about every 25 seconds, so that is the finest resolution this measurement
# honestly has - anything tighter would be false precision.
ON_PACE_SECONDS = 15
SLIGHTLY_BEHIND_SECONDS = 35

PANEL_WIDTH = 560
PANEL_HEIGHT = 186

# The production alert bands hang below the panel, so the content itself never
# moves when an alert appears - a player's saved position keeps meaning what
# it meant. Housing trouble and an idle TC are separate facts that are often
# true together, so there is room for two bands, stacked most-urgent nearest
# the panel. Unpainted bands stay invisible on the translucent window.
ALERT_BAND_HEIGHT = 26
ALERT_GAP = 4
MAX_ALERT_BANDS = 2

# A full alert flashes between these two reds; the flashing is the point -
# an idle TC early is the most expensive routine mistake in the game.
ALERT_FULL_BRIGHT = QColor(200, 40, 30, 235)
ALERT_FULL_DIM = QColor(140, 30, 25, 200)
ALERT_FULL_TEXT = QColor(255, 240, 235)
FLASH_SECONDS = 0.35

# A soft alert sits still and stays out of the way: worth a glance, not a
# klaxon. Same shape so it reads as the same kind of message.
ALERT_SOFT_FILL = QColor(120, 95, 20, 190)
ALERT_SOFT_TEXT = QColor(240, 220, 160)


class OverlayLayout:
    """Maps the panel's designed pixel values through the player's two size
    knobs, so the drawing code can keep its familiar numbers.

    The knobs compose without lines colliding because they own different
    axes. overlay_scale grows everything uniformly - the whole panel, the
    writing included. text_scale additionally grows the fonts, the icons
    beside them, and the VERTICAL axis (baselines, row heights, the panel's
    height): taller text needs taller rows, and giving it those rows is what
    keeps the knobs independent. Width deliberately does not follow text -
    the panel's footprint on the game is a placement decision, and elision
    absorbs the difference.

    Pure arithmetic, no Qt, so tests can pin the mapping without a display.
    round() rather than int(): truncation systematically shrinks at
    fractional scales, and at 1.0 round(n) == n, so the defaults reproduce
    the designed layout exactly.
    """

    def __init__(self, overlay_scale=1.0, text_scale=1.0):
        self.overlay_scale = overlay_scale
        self.text_scale = text_scale

    def x(self, base):
        """A horizontal position or width: overlay size only."""
        return round(base * self.overlay_scale)

    def y(self, base):
        """A vertical position or height: both knobs."""
        return round(base * self.overlay_scale * self.text_scale)

    def pt(self, base):
        """A font point size: both knobs. Never 0 - a 0pt QFont falls back
        to some default size unpredictably."""
        return max(1, round(base * self.overlay_scale * self.text_scale))

    def icon(self, base):
        """An icon height: icons track the text they sit beside."""
        return self.pt(base)

    @property
    def spacing(self):
        """The multiplier the shared drawing functions apply to their
        intra-line advances."""
        return self.overlay_scale * self.text_scale

    @property
    def panel_width(self):
        return self.x(PANEL_WIDTH)

    @property
    def panel_height(self):
        return self.y(PANEL_HEIGHT)

    @property
    def band_height(self):
        # Bands hold text, so their height follows the text axis.
        return self.y(ALERT_BAND_HEIGHT)

    @property
    def band_gap(self):
        return self.x(ALERT_GAP)


# The overlay's window flags, up here as a constant so the test suite can check
# the composition with no display and no QApplication - window flags are just
# bits. Two separately hard-won decisions are encoded in this one expression,
# and both look like clutter to someone who does not know what they cost.
OVERLAY_WINDOW_FLAGS = (
    Qt.WindowType.FramelessWindowHint       # no title bar
    | Qt.WindowType.WindowStaysOnTopHint    # ask to sit above others

    # ToolTip, NOT Tool. A focused full-screen game sits in KWin's "active"
    # layer, which outranks an ordinary always-on-top window; Tool draws over
    # every other window but loses to the game. Tooltip windows live in a
    # higher layer - the one KDE's own volume popup uses - and do draw over
    # it. Tested; do not "simplify" this.
    | Qt.WindowType.ToolTip

    # What actually keeps the mouse inside the game.
    #
    # WA_TransparentForMouseEvents (set below) is a Qt-internal filter: the
    # widget declines mouse events it is handed. It says nothing to the X
    # server, which went on believing this whole panel rectangle wanted
    # input - so the pointer genuinely ENTERED the panel, and the game, which
    # confines the cursor to its own window the way every full-screen RTS
    # does, lost that confinement the moment the pointer crossed out. Brushing
    # the panel threw my mouse onto the second monitor, mid-match.
    #
    # This flag is a different mechanism, not a stronger version of the same
    # one: Qt implements it through the X SHAPE extension, emptying the
    # window's *input region*. The server then routes the pointer straight to
    # the game and never reports a crossing at all. Measured with python-xlib:
    # shape_get_rectangles(SK.Input) returns the full panel rect without it,
    # and [] with it.
    #
    # It cannot cost me the tooltip decision above: this is a hint bit
    # (0x80000), outside WindowType_Mask (0xff), so the type bits stay ToolTip
    # - checked, along with _NET_WM_WINDOW_TYPE, which is byte-identical
    # either way. Do not "simplify" this either.
    | Qt.WindowType.WindowTransparentForInput
)

# Placement mode is the deliberate opposite: an ordinary window the window
# manager can pick up. It must NEVER be transparent for input, or there would
# be nothing left to grab hold of.
PLACING_WINDOW_FLAGS = (Qt.WindowType.Window
                        | Qt.WindowType.WindowStaysOnTopHint)


class Overlay(QWidget):
    """The panel itself. Told what to show; never reads the game directly."""

    def __init__(self, placing=False, layout=None):
        """placing=True gives an ordinary movable window instead of an
        overlay so the player can drag it where they want it.

        The overlay proper is click-through - the X server does not even
        consider it input-receiving - so it can never receive a mouse drag,
        and there is no way to move it directly. Rather than invent a hotkey,
        placement mode just makes it a normal window and lets the window
        manager move it, like anything else on the desktop.

        layout overrides the size knobs read from config - tests pass one so
        they never depend on the player's settings file.
        """
        super().__init__()
        self.placing = placing
        # Named _layout: QWidget.layout() is a real Qt method, and shadowing
        # it would break the widget in confusing ways.
        self._layout = layout or OverlayLayout(config.overlay_scale(),
                                               config.text_scale())

        if placing:
            self.setWindowTitle("Loom — drag me where you want the overlay, then close")
            self.setWindowFlags(PLACING_WINDOW_FLAGS)
        else:
            self.setWindowFlags(OVERLAY_WINDOW_FLAGS)

            # Belt and braces to WindowTransparentForInput above: this stops
            # Qt itself delivering mouse events to the panel on any platform
            # where an input region is not a thing. On its own it is NOT
            # enough - see the comment on OVERLAY_WINDOW_FLAGS.
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

            # Never take focus: doing so would minimise a full-screen game.
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Only what I paint is visible; the rest of the rectangle is see-through.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Resource icons, if the player supplied any. Loaded once, baked at
        # the scaled height - reading the disk on every repaint would be
        # wasteful, and the icons never change while Loom is running.
        self._icons = load_resource_icons(self._layout.icon(ICON_HEIGHT))

        # Tall enough for the content plus the alert bands below it.
        L = self._layout
        self.resize(L.panel_width, L.panel_height
                    + MAX_ALERT_BANDS * (L.band_gap + L.band_height))

        # Everything the panel draws. Updated by the entry point each poll.
        self.build_name = ""
        self.status_line = "waiting for the game..."
        self.headline = ""
        self.footnotes = []
        self.headline_when = ""
        self.targets = None
        self.actual = {}
        self.next_text = ""
        self.next_when = ""
        # Segment versions of the same lines (text runs + @icon@ tokens),
        # used when a real build step is on show; None means plain text.
        self.headline_segments = None
        self.footnote_segments = []
        self.next_segments = None
        self.pace_text = ""
        self.pace_color = DIM_TEXT
        self.have_reading = False
        self.alerts = []            # [(text, severity)], most urgent first
        self.report_rows = None     # build-complete report, replaces the step

    # ---- what to show --------------------------------------------------

    def show_waiting(self, message):
        """No usable reading: say so rather than leaving stale advice up."""
        self.have_reading = False
        self.status_line = message
        self.alerts = []
        self.update()

    def show_alerts(self, alerts_list):
        """Set the production alert bands, most urgent first. [] clears.

        Called every poll alongside show_step; the repaint show_step triggers
        covers this too, so no extra update() is needed here.
        """
        self.alerts = [(text, severity) for text, severity in alerts_list
                       if text][:MAX_ALERT_BANDS]

    def show_alert(self, text, severity):
        """Single-alert convenience for callers that only have one."""
        self.show_alerts([(text, severity)] if text else [])

    def show_report(self, rows, build_name, status_line):
        """Switch the panel to the build-complete report.

        rows come from report.BuildReport.summary(). The panel stays in
        report mode until show_step is called again (a new game), and the
        alert bands keep working underneath - the game goes on.
        """
        self.have_reading = True
        self.report_rows = rows
        self.build_name = build_name
        self.status_line = status_line
        self.update()

    def show_step(self, build, villagers, game_time, active, following, delta,
                  per_resource=None, extra=0, milestone_queued=False):
        """Update the panel from one poll's worth of state.

        `active` is the step to be working on NOW - the first one not yet
        finished - not the last one completed. Showing the completed step puts
        the player an instruction behind their own hands.

        `per_resource` is what the game shows for villagers-on-each-resource,
        for comparison against the target. May be None or partial.

        `extra` is villagers beyond the build's ask (see describe_pace), and
        `milestone_queued` marks that the active step's milestone is already
        seen in the production queue - both deliberately subtle; the full
        story belongs to the build-complete report.
        """
        self.have_reading = True
        self.report_rows = None      # a step on show means no report page
        self.build_name = build.name
        self.actual = per_resource or {}

        minutes, seconds = divmod(int(game_time), 60)
        self.status_line = f"{minutes}:{seconds:02d}   {villagers} villagers"

        if active is None:
            self.headline = "build complete"
            self.footnotes = []
            self.headline_segments = None
            self.footnote_segments = []
            self.headline_when = ""
            self.targets = None
        else:
            self.headline = active.details
            self.footnotes = active.footnotes[:2]  # two lines is plenty
            self.headline_segments = active.details_segments or None
            self.footnote_segments = active.footnotes_segments[:2]
            when_minutes, when_seconds = divmod(int(active.time or 0), 60)
            self.headline_when = f"by {when_minutes}:{when_seconds:02d} · {active.villager_count} vills"
            if milestone_queued:
                # The step's tech/age-up is already in the queue: one quiet
                # word of reassurance, no new lines.
                self.headline_when += " · ✓ queued"
            # Where the villagers should be working. This comes straight from
            # the build order file - nothing is inferred.
            self.targets = active.villagers

        if following is None:
            self.next_text = "" if active is None else "last step"
            self.next_when = ""
            self.next_segments = None
        else:
            self.next_text = following.details
            self.next_segments = following.details_segments or None
            when_minutes, when_seconds = divmod(int(following.time or 0), 60)
            self.next_when = f"{when_minutes}:{when_seconds:02d} · {following.villager_count} vills"

        self.pace_text, self.pace_color = describe_pace(delta, extra)
        self.update()

    # ---- drawing -------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        self._draw_background(painter)

        if not self.have_reading:
            painter.setPen(DIM_TEXT)
            painter.setFont(QFont("sans", self._layout.pt(11)))
            painter.drawText(0, 0, self.width(), self._layout.panel_height,
                             Qt.AlignmentFlag.AlignCenter, self.status_line)
            return

        if self.report_rows is not None:
            self._draw_header(painter)
            self._draw_report(painter)
        else:
            self._draw_header(painter)
            self._draw_headline(painter)
            self._draw_targets(painter)
            self._draw_next(painter)
        if self.alerts:
            self._draw_alert_bands(painter)

    def _draw_report(self, painter):
        """The build-complete report: one stat per row, verdicts coloured."""
        L = self._layout
        painter.setFont(QFont("sans", L.pt(12), QFont.Weight.Bold))
        painter.setPen(TEXT)
        painter.drawText(L.x(16), L.y(58), "BUILD COMPLETE")

        painter.setFont(QFont("sans", L.pt(10)))
        y = L.y(80)
        for label, value, good in self.report_rows[:6]:
            painter.setPen(DIM_TEXT)
            painter.drawText(L.x(16), y, elide(painter, label, L.x(260)))

            if good is None:
                painter.setPen(TEXT)
            elif good:
                painter.setPen(ON_PACE_COLOR)
            else:
                painter.setPen(BEHIND_COLOR)
            width = painter.fontMetrics().horizontalAdvance(value)
            painter.drawText(self.width() - L.x(16) - width, y, value)
            y += L.y(18)

    def _draw_background(self, painter):
        L = self._layout
        path = QPainterPath()
        path.addRoundedRect(L.x(1), L.x(1), self.width() - L.x(2),
                            L.panel_height - L.x(2), L.x(10), L.x(10))
        painter.fillPath(path, BACKGROUND)
        painter.setPen(BORDER)
        painter.drawPath(path)

    def _draw_alert_bands(self, painter):
        """The production alert bands, stacked below the panel."""
        # One flash phase for every band: repaints arrive every poll
        # (~300ms), which is what actually paces the strobe; the clock just
        # decides which phase this repaint lands in. Sharing it keeps two
        # full bands blinking together instead of chasing each other.
        phase = int(time.monotonic() / FLASH_SECONDS) % 2

        for index, (text, severity) in enumerate(self.alerts):
            if severity == alerts.FULL:
                fill = ALERT_FULL_BRIGHT if phase == 0 else ALERT_FULL_DIM
                text_color = ALERT_FULL_TEXT
            else:
                fill = ALERT_SOFT_FILL
                text_color = ALERT_SOFT_TEXT

            L = self._layout
            top = (L.panel_height + L.band_gap
                   + index * (L.band_height + L.band_gap))
            path = QPainterPath()
            path.addRoundedRect(L.x(1), top, self.width() - L.x(2),
                                L.band_height, L.x(8), L.x(8))
            painter.fillPath(path, fill)

            painter.setPen(text_color)
            painter.setFont(QFont("sans", L.pt(11), QFont.Weight.Bold))
            painter.drawText(0, top, self.width(), L.band_height,
                             Qt.AlignmentFlag.AlignCenter, text)

    def _draw_header(self, painter):
        L = self._layout
        painter.setFont(QFont("sans", L.pt(10)))
        painter.setPen(DIM_TEXT)
        painter.drawText(L.x(16), L.y(24), self.status_line)

        painter.setFont(QFont("sans", L.pt(10), QFont.Weight.Bold))
        painter.setPen(self.pace_color)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(self.pace_text)
        painter.drawText(self.width() - L.x(16) - width, L.y(24),
                         self.pace_text)

        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawLine(L.x(14), L.y(34), self.width() - L.x(14), L.y(34))

    def _draw_headline(self, painter):
        L = self._layout
        # Measure the timing text first, so the headline gets all the space
        # actually left over. Reserving a fixed width truncated instructions
        # that would have fitted.
        painter.setFont(QFont("sans", L.pt(9)))
        when_width = 0
        if self.headline_when:
            when_width = (painter.fontMetrics()
                          .horizontalAdvance(self.headline_when) + L.x(14))

        painter.setFont(QFont("sans", L.pt(15), QFont.Weight.Bold))
        painter.setPen(TEXT)
        available = self.width() - L.x(32) - when_width
        if self.headline_segments:
            draw_segments(painter, self.headline_segments, L.x(16), L.y(62),
                          available, icon_height=L.icon(24),
                          spacing=L.spacing)
        else:
            painter.drawText(L.x(16), L.y(62),
                             elide(painter, self.headline, available))

        if self.headline_when:
            painter.setFont(QFont("sans", L.pt(9)))
            painter.setPen(FAINT_TEXT)
            width = painter.fontMetrics().horizontalAdvance(self.headline_when)
            painter.drawText(self.width() - L.x(16) - width, L.y(62),
                             self.headline_when)

        painter.setFont(QFont("sans", L.pt(10)))
        painter.setPen(DIM_TEXT)
        y = L.y(84)
        rows = (self.footnote_segments
                if self.footnote_segments else
                [None] * len(self.footnotes))
        for index, segments in enumerate(rows):
            painter.drawText(L.x(16), y, "·")
            if segments:
                draw_segments(painter, segments, L.x(28), y,
                              self.width() - L.x(44), icon_height=L.icon(16),
                              spacing=L.spacing)
            else:
                painter.drawText(L.x(28), y,
                                 elide(painter, self.footnotes[index],
                                       self.width() - L.x(44)))
            y += L.y(18)

    def _draw_targets(self, painter):
        """Villagers on each resource: what the build wants, and what you have.

        Each resource leads with its icon if the player supplied one, or its
        full word if not - so it is always clear which resource is which. When
        Loom can read the actual counts off the HUD it shows "have/want" and
        flags anything off the build; otherwise it shows just the target.
        """
        if not self.targets:
            return

        L = self._layout
        y = L.panel_height - L.y(56)
        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(L.x(16), y, "VILLS")
        # The row starts at its designed spot unless the label itself has
        # outgrown it - label width follows the text knob, the anchor only
        # the overlay knob, so at big text sizes the label needs more room.
        label_end = (L.x(16) + L.x(8)
                     + painter.fontMetrics().horizontalAdvance("VILLS"))

        painter.setFont(QFont("sans", L.pt(11), QFont.Weight.Bold))
        draw_resource_row(painter, self._icons, self.targets, self.actual,
                          max(L.x(66), label_end), y, spacing=L.spacing)

    def _draw_next(self, painter):
        L = self._layout
        baseline = L.panel_height - L.y(18)

        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawLine(L.x(14), baseline - L.y(26),
                         self.width() - L.x(14), baseline - L.y(26))

        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(L.x(16), baseline, "THEN")
        # Same give-the-label-room rule as the VILLS row: the text starts at
        # its designed spot unless "THEN" has outgrown it.
        label_end = (L.x(16) + L.x(8)
                     + painter.fontMetrics().horizontalAdvance("THEN"))
        text_x = max(L.x(62), label_end)

        painter.setFont(QFont("sans", L.pt(10)))
        painter.setPen(DIM_TEXT)

        when_width = 0
        if self.next_when:
            metrics = painter.fontMetrics()
            when_width = metrics.horizontalAdvance(self.next_when) + L.x(12)

        available = self.width() - L.x(16) - text_x - when_width
        if self.next_segments:
            draw_segments(painter, self.next_segments, text_x, baseline,
                          available, icon_height=L.icon(16),
                          spacing=L.spacing)
        else:
            painter.drawText(text_x, baseline,
                             elide(painter, self.next_text, available))

        if self.next_when:
            painter.setPen(FAINT_TEXT)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(self.next_when)
            painter.drawText(self.width() - L.x(16) - width, baseline,
                             self.next_when)


def draw_segments(painter, segments, x, baseline, available, icon_height,
                  spacing=1.0):
    """Draw text runs and inline icons on one line, left to right.

    A module function rather than a method because the overlay and the
    launcher's build preview draw the same instruction lines - one
    implementation, two windows. Overflow is handled by hand: QFontMetrics'
    elide cannot see pixmaps, so each piece checks the space left before
    drawing, and a line that runs out ends in an ellipsis. Icons an image
    library does not have fall back to their words - a fresh clone still
    reads fine.

    spacing multiplies the little gaps between pieces, so a scaled overlay
    keeps its proportions. The default 1.0 leaves every gap exactly at its
    designed pixel count - the preview cards rely on that.
    """
    metrics = painter.fontMetrics()
    ellipsis = metrics.horizontalAdvance("…")
    right = x + available

    for kind, value in segments:
        if kind == "icon":
            pixmap = load_step_icon(value, icon_height)
            if pixmap is not None:
                if x + pixmap.width() > right - ellipsis:
                    painter.drawText(int(x), baseline, "…")
                    return
                painter.drawPixmap(int(x),
                                   baseline - icon_height + round(3 * spacing),
                                   pixmap)
                x += pixmap.width() + round(4 * spacing)
                continue
            value = build_order.icon_to_words(value)

        width = metrics.horizontalAdvance(value)
        if x + width > right:
            painter.drawText(int(x), baseline,
                             metrics.elidedText(
                                 value, Qt.TextElideMode.ElideRight,
                                 max(0, int(right - x))))
            return
        painter.drawText(int(x), baseline, value)
        x += width + round(5 * spacing)


def draw_resource_row(painter, icons, targets, actual, x, y, spacing=1.0):
    """The villagers-per-resource row, shared by the overlay and the preview.

    Each resource leads with its icon if the player supplied one, or its full
    word if not - so it is always clear which resource is which. With a live
    reading the count is "have/want" and anything off the build is flagged;
    without one it is just the target. Returns the x where the row ended.

    The off-target flag is a white number with a red underline, deliberately
    not a recolor: food's own color is red, and a correct food count would
    then look like a warning.

    spacing multiplies the gaps between resources, like draw_segments; the
    default keeps the designed pixel counts for the preview cards.
    """
    for name in RESOURCE_ORDER:
        want = targets.get(name, 0)
        have = actual.get(name)

        off = have is not None and abs(have - want) > RESOURCE_TOLERANCE
        dim = have is None and want == 0     # nothing wanted here yet
        resource_color = QColor(90, 90, 98) if dim else RESOURCE_COLORS[name]

        icon = icons.get(name)
        if icon is not None:
            # The baked pixmap knows its own (possibly scaled) height, so no
            # icon-height parameter is needed to align it to the baseline.
            painter.drawPixmap(int(x), y - icon.height() + round(3 * spacing),
                               icon)
            x += icon.width() + round(6 * spacing)
        else:
            painter.setPen(resource_color)
            label = RESOURCE_LABELS[name]
            painter.drawText(int(x), y, label)
            x += (painter.fontMetrics().horizontalAdvance(label)
                  + round(6 * spacing))

        text = str(want) if have is None else f"{have}/{want}"
        painter.setPen(TEXT if off else resource_color)
        painter.drawText(int(x), y, text)
        width = painter.fontMetrics().horizontalAdvance(text)

        if off:
            painter.setPen(OFF_TARGET_COLOR)
            painter.drawLine(int(x), y + round(3 * spacing),
                             int(x) + width, y + round(3 * spacing))

        x += width + round(18 * spacing)
    return x


def describe_pace(delta, extra=0):
    """Turn a pace delta in seconds into (text, color).

    extra is how many villagers beyond the build's ask are on the field.
    It prefixes the pace chip and turns it amber: the number that follows
    is still true, but the build's goal has shifted - an extra villager
    before an age-up slides the click 25-40 seconds, and the player should
    see the cause next to the effect. Red stays red: already-behind is
    still the louder fact.
    """
    if delta is None:
        text, color = "—", FAINT_TEXT
    elif delta < -ON_PACE_SECONDS:
        text, color = f"AHEAD {abs(delta):.0f}s", AHEAD_COLOR
    elif abs(delta) <= ON_PACE_SECONDS:
        text, color = "ON PACE", ON_PACE_COLOR
    elif delta <= SLIGHTLY_BEHIND_SECONDS:
        text, color = f"BEHIND {delta:.0f}s", SLIGHTLY_BEHIND_COLOR
    else:
        text, color = f"BEHIND {delta:.0f}s", BEHIND_COLOR

    if extra > 0:
        text = f"+{extra} VILL · {text}"
        if color is not BEHIND_COLOR:
            color = SLIGHTLY_BEHIND_COLOR
    return text, color


def elide(painter, text, available_width):
    """Shorten text with an ellipsis so it never overflows the panel."""
    metrics = painter.fontMetrics()
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                              max(0, int(available_width)))
