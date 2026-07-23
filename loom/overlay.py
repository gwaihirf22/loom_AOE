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

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import QWidget

from . import paths

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


def load_resource_icons():
    """Load whatever resource icons the player has put in icons/.

    Returns {name: QPixmap} for the icons that loaded. A resource with no icon
    is simply absent from the result, and the overlay shows its word instead.

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
                    ICON_HEIGHT, Qt.TransformationMode.SmoothTransformation)
                break
    return icons

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


class Overlay(QWidget):
    """The panel itself. Told what to show; never reads the game directly."""

    def __init__(self, placing=False):
        """placing=True gives an ordinary movable window instead of an
        overlay so the player can drag it where they want it.

        The overlay proper is clic-through, which means it can never receive a
        mouse drag - so there is no way to move it dirctly. Rather than invent
        a hotkey, placement mode just makes it a normal window and lets the
        window manager move it, like anything else on the desktop.
        """
        super().__init__()
        self.placing = placing

        if placing:
            self.setWindowTitle("Loom — drag me where you want the overlay, then close")
            self.setWindowFlags(Qt.WindowType.Window
                                | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint       # no title bar
                | Qt.WindowType.WindowStaysOnTopHint    # ask to sit above others
                # ToolTip, NOT Tool. A focused full-screen game sits in KWin's
                # "active" layer, which outranks an ordinary always-on-top
                # window; Tool draws over every other window but loses to the
                # game. Tooltip windows live in a higher layer - the one KDE's
                # own volume popup uses - and do draw over it. Tested; do not
                # "simplify" this.
                | Qt.WindowType.ToolTip
            )

            # Clicks pass straight through to the game. An overlay that
            # swallows clicks is worse than no overlay at all.
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

            # Never take focus: doing so would minimise a full-screen game.
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        # Only what I paint is visible; the rest of the rectangle is see-through.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Resource icons, if the player supplied any. Loaded once - reading the
        # disk on every repaint would be wasteful, and the icons never change
        # while Loom is running.
        self._icons = load_resource_icons()

        self.resize(PANEL_WIDTH, PANEL_HEIGHT)

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
        self.pace_text = ""
        self.pace_color = DIM_TEXT
        self.have_reading = False

    # ---- what to show --------------------------------------------------

    def show_waiting(self, message):
        """No usable reading: say so rather than leaving stale advice up."""
        self.have_reading = False
        self.status_line = message
        self.update()

    def show_step(self, build, villagers, game_time, active, following, delta,
                  per_resource=None):
        """Update the panel from one poll's worth of state.

        `active` is the step to be working on NOW - the first one not yet
        finished - not the last one completed. Showing the completed step puts
        the player an instruction behind their own hands.

        `per_resource` is what the game shows for villagers-on-each-resource,
        for comparison against the target. May be None or partial.
        """
        self.have_reading = True
        self.build_name = build.name
        self.actual = per_resource or {}

        minutes, seconds = divmod(int(game_time), 60)
        self.status_line = f"{minutes}:{seconds:02d}   {villagers} villagers"

        if active is None:
            self.headline = "build complete"
            self.footnotes = []
            self.headline_when = ""
            self.targets = None
        else:
            self.headline = active.details
            self.footnotes = active.footnotes[:2]  # two lines is plenty
            when_minutes, when_seconds = divmod(int(active.time or 0), 60)
            self.headline_when = f"by {when_minutes}:{when_seconds:02d} · {active.villager_count} vills"
            # Where the villagers should be working. This comes straight from
            # the build order file - nothing is inferred.
            self.targets = active.villagers

        if following is None:
            self.next_text = "" if active is None else "last step"
            self.next_when = ""
        else:
            self.next_text = following.details
            when_minutes, when_seconds = divmod(int(following.time or 0), 60)
            self.next_when = f"{when_minutes}:{when_seconds:02d} · {following.villager_count} vills"

        self.pace_text, self.pace_color = describe_pace(delta)
        self.update()

    # ---- drawing -------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        self._draw_background(painter)

        if not self.have_reading:
            painter.setPen(DIM_TEXT)
            painter.setFont(QFont("sans", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             self.status_line)
            return

        self._draw_header(painter)
        self._draw_headline(painter)
        self._draw_targets(painter)
        self._draw_next(painter)

    def _draw_background(self, painter):
        path = QPainterPath()
        path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, 10, 10)
        painter.fillPath(path, BACKGROUND)
        painter.setPen(BORDER)
        painter.drawPath(path)

    def _draw_header(self, painter):
        painter.setFont(QFont("sans", 10))
        painter.setPen(DIM_TEXT)
        painter.drawText(16, 24, self.status_line)

        painter.setFont(QFont("sans", 10, QFont.Weight.Bold))
        painter.setPen(self.pace_color)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(self.pace_text)
        painter.drawText(self.width() - 16 - width, 24, self.pace_text)

        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawLine(14, 34, self.width() - 14, 34)

    def _draw_headline(self, painter):
        # Measure the timing text first, so the headline gets all the space
        # actually left over. Reserving a fixed width truncated instructions
        # that would have fitted.
        painter.setFont(QFont("sans", 9))
        when_width = 0
        if self.headline_when:
            when_width = painter.fontMetrics().horizontalAdvance(self.headline_when) + 14

        painter.setFont(QFont("sans", 15, QFont.Weight.Bold))
        painter.setPen(TEXT)
        painter.drawText(16, 62,
                         elide(painter, self.headline, self.width() - 32 - when_width))

        if self.headline_when:
            painter.setFont(QFont("sans", 9))
            painter.setPen(FAINT_TEXT)
            width = painter.fontMetrics().horizontalAdvance(self.headline_when)
            painter.drawText(self.width() - 16 - width, 62, self.headline_when)

        painter.setFont(QFont("sans", 10))
        painter.setPen(DIM_TEXT)
        y = 84
        for footnote in self.footnotes:
            painter.drawText(16, y, elide(painter, "· " + footnote, self.width() - 32))
            y += 18

    def _draw_targets(self, painter):
        """Villagers on each resource: what the build wants, and what you have.

        Each resource leads with its icon if the player supplied one, or its
        full word if not - so it is always clear which resource is which. When
        Loom can read the actual counts off the HUD it shows "have/want" and
        flags anything off the build; otherwise it shows just the target.
        """
        if not self.targets:
            return

        y = self.height() - 56
        painter.setFont(QFont("sans", 9, QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(16, y, "VILLS")

        painter.setFont(QFont("sans", 11, QFont.Weight.Bold))
        x = 66
        for name in RESOURCE_ORDER:
            want = self.targets.get(name, 0)
            have = self.actual.get(name)

            off = have is not None and abs(have - want) > RESOURCE_TOLERANCE
            dim = have is None and want == 0     # nothing wanted here yet
            resource_color = QColor(90, 90, 98) if dim else RESOURCE_COLORS[name]

            # Say which resource this is: an icon if the player has one, its
            # full word if not. The word is deliberately spelled out rather
            # than abbreviated, so there is no doubt which resource it is.
            icon = self._icons.get(name)
            if icon is not None:
                painter.drawPixmap(x, y - ICON_HEIGHT + 3, icon)
                x += icon.width() + 6
            else:
                painter.setPen(resource_color)
                label = RESOURCE_LABELS[name]
                painter.drawText(x, y, label)
                x += painter.fontMetrics().horizontalAdvance(label) + 6

            # The count. Color normally says which resource - but "off the
            # build" cannot also be a color, because food's own color is red
            # and a correct food count would then look like a warning. So an
            # off-target number is white with a red underline instead, which
            # reads as an alert against every resource color, food included.
            text = str(want) if have is None else f"{have}/{want}"
            painter.setPen(TEXT if off else resource_color)
            painter.drawText(x, y, text)
            width = painter.fontMetrics().horizontalAdvance(text)

            if off:
                painter.setPen(OFF_TARGET_COLOR)
                painter.drawLine(x, y + 3, x + width, y + 3)

            x += width + 18

    def _draw_next(self, painter):
        baseline = self.height() - 18

        painter.setPen(QColor(255, 255, 255, 28))
        painter.drawLine(14, baseline - 26, self.width() - 14, baseline - 26)

        painter.setFont(QFont("sans", 9, QFont.Weight.Bold))
        painter.setPen(FAINT_TEXT)
        painter.drawText(16, baseline, "THEN")

        painter.setFont(QFont("sans", 10))
        painter.setPen(DIM_TEXT)

        when_width = 0
        if self.next_when:
            metrics = painter.fontMetrics()
            when_width = metrics.horizontalAdvance(self.next_when) + 12

        available = self.width() - 32 - 46 - when_width
        painter.drawText(62, baseline, elide(painter, self.next_text, available))

        if self.next_when:
            painter.setPen(FAINT_TEXT)
            metrics = painter.fontMetrics()
            width = metrics.horizontalAdvance(self.next_when)
            painter.drawText(self.width() - 16 - width, baseline, self.next_when)


def describe_pace(delta):
    """Turn a pace delta in seconds into (text, color)."""
    if delta is None:
        return "—", FAINT_TEXT
    if delta < -ON_PACE_SECONDS:
        return f"AHEAD {abs(delta):.0f}s", AHEAD_COLOR
    if abs(delta) <= ON_PACE_SECONDS:
        return "ON PACE", ON_PACE_COLOR
    if delta <= SLIGHTLY_BEHIND_SECONDS:
        return f"BEHIND {delta:.0f}s", SLIGHTLY_BEHIND_COLOR
    return f"BEHIND {delta:.0f}s", BEHIND_COLOR


def elide(painter, text, available_width):
    """Shorten text with an ellipsis so it never overflows the panel."""
    metrics = painter.fontMetrics()
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight,
                              max(0, int(available_width)))
