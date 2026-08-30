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

import math
import time
from pathlib import PurePosixPath

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QPainter,
                         QPainterPath, QPixmap)
from PyQt6.QtWidgets import QPushButton, QWidget

from . import alerts, build_order, checklist, config, paths, steplayout

# Colors. Kept dark and low-contrast on purpose: this sits on top of a game
# and must be readable without dragging the eye away from it.
BACKGROUND = QColor(18, 18, 22, 205)
BORDER = QColor(255, 255, 255, 40)
TEXT = QColor(238, 238, 238)
DIM_TEXT = QColor(160, 160, 168)
FAINT_TEXT = QColor(120, 120, 128)

# The header note that says the panel is not following the game. Amber
# rather than red: this is a state the player CHOSE, not an alarm - but it
# must be impossible to miss, because a panel that has quietly stopped
# following while looking exactly as it always does is the one failure this
# whole project is built to avoid.
NOT_FOLLOWING_COLOR = QColor(235, 190, 90)
HOLDING_COLOR = QColor(150, 150, 160)

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
            path = paths.find_asset("icons", f"{name}{extension}")
            if path is None:
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


def find_icon_file(token):
    """The library file for one @icon@ token, forgiving about the extension.

    Community builds name the same pictures with whatever suffix the site
    they came from used - buildorderguide's export writes .png and .jpg
    where the shipped library holds .webp - and every such token still
    points at the right folder and the right name. So after the exact
    match, any file in the SAME folder with the same name and a different
    extension counts, case-insensitively. The folder is not forgiven:
    different icons can share a name across folders, and a wrong picture
    on an instruction is worse than the words.
    """
    exact = paths.find_asset("master_aoe2_images", token)
    if exact is not None:
        return exact

    relative = PurePosixPath(token)
    wanted = relative.stem.lower()
    for base in paths.asset_search_path("master_aoe2_images"):
        folder = base / relative.parent
        if not folder.is_dir():
            continue
        for candidate in sorted(folder.iterdir()):
            if candidate.is_file() and candidate.stem.lower() == wanted:
                return candidate
    return None


def load_step_icon(token, height):
    """The picture for one @icon@ token, scaled to a text line. None if the
    library does not have it - the caller falls back to words."""
    key = (token, height)
    if key in _step_icon_cache:
        return _step_icon_cache[key]

    pixmap = None
    path = find_icon_file(token)
    if path is not None:
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

# What a panel draws. Not the same question as `placing`, which is about how
# the WINDOW behaves - a placing window can be in any of these modes, because
# the whole point of placement is looking at the panel you will actually play
# with.
BUILD_PANEL = "build"           # the build order: steps, targets, what's next
DASHBOARD_PANEL = "dashboard"   # clock, villagers, resources, age, TCs
BANDS_PANEL = "bands"           # the alert bands, and nothing whatsoever else

# The dashboard is shorter than the build panel: it has a header, a resource
# row and two lines, and none of that grows. A fixed height rather than one
# derived from the build panel's, because the two have no rows in common and
# tying them together would make every future change to one move the other.
DASHBOARD_HEIGHT = 132

# The production alert bands hang below the panel, so the content itself never
# moves when an alert appears - a player's saved position keeps meaning what
# it meant. Housing trouble and an idle TC are separate facts that are often
# true together, so there is room for two bands, stacked most-urgent nearest
# the panel. Unpainted bands stay invisible on the translucent window.
ALERT_BAND_HEIGHT = 26
ALERT_GAP = 4
MAX_ALERT_BANDS = 2

# Placement mode's own button, hanging below the alert bands. It is chrome
# rather than panel: it exists only while the player is choosing a spot, and
# what gets SAVED is the panel's top-left corner, which nothing down here can
# move. Sized in designed pixels like everything else, so it grows with the
# two size knobs instead of becoming a postage stamp on a scaled-up panel.
PLACE_BUTTON_HEIGHT = 34
PLACE_BUTTON_GAP = 8

# Deliberately solid, and deliberately NOT faded by the transparency
# sliders - the same reasoning as the alert bands. The player may well drag
# the background down to nothing to see what that looks like, and the one
# control that ends the exercise has to survive it. Green because it commits.
PLACE_BUTTON_STYLE = (
    "QPushButton {"
    " background-color: #2e7d32; color: white; border: none;"
    " border-radius: 6px; font-weight: bold; }"
    "QPushButton:hover { background-color: #388e3c; }"
    "QPushButton:pressed { background-color: #1b5e20; }"
)

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

# An urge band flashes like a full alert but in blue: it is an instruction
# ("CLICK UP"), not a failure, and a player mid-fight must be able to tell
# "do the next thing" from "something is bleeding" without reading a word.
ALERT_URGE_BRIGHT = QColor(40, 90, 200, 235)
ALERT_URGE_DIM = QColor(30, 60, 140, 200)
ALERT_URGE_TEXT = QColor(235, 242, 255)


def panel_background(opacity):
    """The panel's backdrop and border at one background opacity.

    Returns (fill, border) QColors. opacity is TRUE opacity: 1.0 is a solid
    card the game cannot be seen through, 0.0 is no card at all. The
    designed card is 205/255 - exactly alpha 205 back out, the golden case
    the tests pin. The DEFAULT moved a touch above it in 1.0.5
    (config.DEFAULT_BACKGROUND_OPACITY, 0.83, the author's own card) and is
    just another slider position to this formula.

    The border's 50 is chosen for that golden case: round(50 * 205/255)
    is exactly the designed 40, while the full-range card still gets a
    slightly stronger edge at 100% and none at 0%.

    Instance-level rather than a change to the BACKGROUND constant, and that
    is load-bearing: loom/browser.py imports the same constant for the build
    preview's cards, and the launcher must not fade because the overlay did.
    """
    fill = QColor(BACKGROUND)
    fill.setAlpha(round(255 * opacity))
    border = QColor(BORDER)
    border.setAlpha(round(50 * opacity))
    return fill, border


def content_style(visibility):
    """One slider value into (opacity, contrast) for the panel's content.

    The scale puts the DESIGNED look at 0.5, because the beta finding was
    that fading DOWN was only half of what the slider is for: with the card
    thinned, the designed greys are unreadable over bright terrain, and the
    useful direction is UP - solid and brighter than designed.

        0.0 .. 0.5   contrast 0, opacity rising 0 -> 1 (fade toward gone)
        0.5          exactly (1.0, 0.0): the designed look, byte-identical
        0.5 .. 1.0   opacity 1, contrast rising 0 -> 1 (climb toward vivid)
    """
    if visibility <= 0.5:
        return visibility / 0.5, 0.0
    return 1.0, (visibility - 0.5) / 0.5


def boosted(color, contrast):
    """A pen colour pushed toward its vivid extreme by the contrast knob.

    HSV value climbs toward 255 while hue and saturation stay put, so grey
    text whitens and the pace and resource colours become brighter versions
    of THEMSELVES rather than washing out toward white. Alpha is kept.
    contrast 0 returns the colour bit-identical - the golden case.
    """
    if contrast <= 0:
        return color
    hue, saturation, value, alpha = color.getHsv()
    value = round(value + (255 - value) * contrast)
    return QColor.fromHsv(hue, saturation, value, alpha)


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

    @property
    def place_button_height(self):
        # It holds a word, so it follows the text axis like the bands do.
        return self.y(PLACE_BUTTON_HEIGHT)

    @property
    def place_button_gap(self):
        return self.x(PLACE_BUTTON_GAP)


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
# Placement mode. Frameless like the overlay proper, because the whole point
# of looking at it is judging how the translucent card sits over the game -
# a title bar and a border are opaque chrome the real panel does not have,
# and they make the one thing being judged impossible to see.
#
# What it must NOT borrow from OVERLAY_WINDOW_FLAGS is WindowTransparentForInput:
# a window with an empty X11 input region cannot be picked up at all. And the
# type stays Window rather than ToolTip - a ToolTip is not something the
# window manager or Qt will hand keyboard focus, and this one needs both the
# mouse (to be dragged) and Escape (to be abandoned).
#
# Frameless means the window manager will not move it, so the panel moves
# itself - see mousePressEvent below.
PLACING_WINDOW_FLAGS = (Qt.WindowType.Window
                        | Qt.WindowType.FramelessWindowHint
                        | Qt.WindowType.WindowStaysOnTopHint)


class Overlay(QWidget):
    """The panel itself. Told what to show; never reads the game directly."""

    # Placement mode only: the player pressed "Set Overlay Position". The
    # panel does not save anything itself - it does not know what corner the
    # offset is measured from - so it announces the press and the entry
    # point, which does know, writes it down. See loom_overlay.remember_position.
    position_accepted = pyqtSignal()

    def __init__(self, placing=False, layout=None, panel_mode=BUILD_PANEL):
        """placing=True gives an ordinary movable window instead of an
        overlay so the player can drag it where they want it.

        The overlay proper is click-through - the X server does not even
        consider it input-receiving - so it can never receive a mouse drag,
        and there is no way to move it directly. Rather than invent a hotkey,
        placement mode just makes it a normal window and lets the window
        manager move it, like anything else on the desktop.

        layout overrides the size knobs read from config - tests pass one so
        they never depend on the player's settings file.

        panel_mode says WHAT this panel draws, which is a different question
        from how it behaves as a window: BUILD_PANEL is the build order,
        DASHBOARD_PANEL is the live readout with no build behind it, and
        BANDS_PANEL is the alert bands and nothing else. The two tracking
        modes exist because everything Loom reads except the build order -
        the clock, the villagers, the queue, the age - is worth having on its
        own, and the alerts most of all.
        """
        super().__init__()
        self.placing = placing
        self.panel_mode = panel_mode
        # Named _layout: QWidget.layout() is a real Qt method, and shadowing
        # it would break the widget in confusing ways.
        self._layout = layout or OverlayLayout(config.overlay_scale(),
                                               config.text_scale())
        # Whether the size knobs are mine to re-read. A layout handed in is
        # the caller's fixed choice - tests pass one precisely so they never
        # depend on the player's settings file, and a live reload that
        # overwrote it would quietly reintroduce that dependency.
        self._layout_from_config = layout is None

        if placing:
            # Frameless, so this is never drawn - it is what the taskbar
            # and alt-tab call the window, which is the only place it shows.
            self.setWindowTitle("Loom — place the overlay")
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

        # The two transparency knobs, read once like the size knobs. The
        # text knob splits into a painter opacity (below the designed look)
        # and a colour-contrast boost (above it) - see content_style.
        self._background, self._border = panel_background(
            config.background_opacity())
        self._content_opacity, self._content_contrast = content_style(
            config.text_visibility())

        # Resource icons, if the player supplied any. Loaded once, baked at
        # the scaled height - reading the disk on every repaint would be
        # wasteful, and the icons never change while Loom is running.
        self._icons = load_resource_icons(self._layout.icon(ICON_HEIGHT))

        # How far the panel has grown beyond its designed height to fit a
        # long step's items, in real pixels. Zero for the 126 of 150 shipped
        # steps that have three items or fewer, so the common case is the
        # panel that shipped in 1.0.4, pixel for pixel.
        self._extra = 0
        self._plan = steplayout.plan_items(0)

        # Placement mode's commit button. Built BEFORE the first resize
        # below, because resizeEvent lays it out and would otherwise be
        # laying out a widget that does not exist yet.
        self.place_button = None
        # Where in the window a drag was picked up, while one is in progress.
        self._drag_from = None
        if placing:
            self.place_button = QPushButton("Set Overlay Position", self)
            self.place_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.place_button.setStyleSheet(PLACE_BUTTON_STYLE)
            self.place_button.clicked.connect(self.position_accepted.emit)
            # The panel keeps the keyboard, not the button: Escape has to
            # reach keyPressEvent below, and a focused button would eat it
            # (and answer Space by saving, which nobody asked it to).
            self.place_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            # Frameless, so there is no title bar to grab. Saying so with
            # the cursor is the only affordance left.
            self.setCursor(Qt.CursorShape.SizeAllCursor)

        # Tall enough for the content plus the chrome hanging below it.
        self._resize_to_content()

        # Everything the panel draws. Updated by the entry point each poll.
        self.build_name = ""
        self.status_line = "waiting for the game..."
        # The step's items, as segment lists, and one checklist state each.
        # Equal citizens: the first is not a headline. See
        # build_order.split_notes for what the shipped builds actually put
        # first, and why drawing it three times the size buried the age-up
        # click in a third of the steps.
        self.item_rows = []
        self.item_states = []
        self.step_when = ""
        self.targets = None
        self.actual = {}
        self.next_text = ""
        self.next_when = ""
        self.next_segments = None
        self.pace_text = ""
        self.pace_color = DIM_TEXT
        # The header's centre slot: "" while the game is driving, which is
        # the normal case and draws nothing at all. Two different things
        # borrow it, never at once - the MANUAL note once a game is running,
        # and the waiting banner before one is.
        self.header_note = ""
        self.header_note_color = NOT_FOLLOWING_COLOR
        self.have_reading = False
        self.alerts = []            # [(text, severity)], most urgent first
        self.report_rows = None     # build-complete report, replaces the step
        # The dashboard's three lines. Empty means "not read", which is drawn
        # as an admission rather than left blank - see _draw_dashboard.
        self.age_text = ""
        self.tc_text = ""
        self.population_text = ""

    # ---- size ------------------------------------------------------------

    def panel_height(self):
        """The card's height right now, grown to fit the step on show.

        Everything below the items measures from here - the VILLS row, the
        THEN row, the alert bands - so they all follow the growth without
        knowing about it. Growth is DOWNWARD from a fixed top-left, because
        that corner is what the player's saved position means.

        ZERO in bands mode, and that one number is the whole of how "alert
        bands and nothing else" is drawn. _draw_alert_bands already measures
        from here and _resize_to_content already sizes from here, so a card
        of no height puts the bands at the top of a window with room for
        nothing but them. Nothing else had to learn about the mode.
        """
        if self.panel_mode == BANDS_PANEL:
            return 0
        if self.panel_mode == DASHBOARD_PANEL:
            # No steps behind it, so nothing can make it taller: self._extra
            # is the build panel growing to fit a long step, and there is no
            # step here.
            return self._layout.y(DASHBOARD_HEIGHT)
        return self._layout.panel_height + self._extra

    def chrome_height(self):
        """Everything the window reserves BELOW the card.

        Always the full two alert bands, whether or not any alert is up: the
        bands hang below the panel precisely so the content never moves when
        one appears, and a window that grew to admit them would move the
        card's bottom edge instead. Placement mode adds its button under
        those, so what the player is looking at while they drag is the panel
        at its tallest - which is the question placement is answering.

        One method rather than the same sum written at each of the three
        places that resize, because they must agree: a window sized by one
        formula and drawn by another clips whatever the difference is.
        """
        L = self._layout
        height = MAX_ALERT_BANDS * (L.band_gap + L.band_height)
        if self.place_button is not None:
            height += L.place_button_gap + L.place_button_height
        return height

    def _resize_to_content(self):
        """Size the window to the card plus its chrome, and lay the chrome out.

        The single owner of that sum. There are three moments it has to be
        recomputed - startup, a step with more items, a live appearance
        change - and they must agree, because a window sized by one formula
        and drawn by another clips whatever the difference is.
        """
        L = self._layout
        self.resize(L.panel_width, self.panel_height() + self.chrome_height())
        self._layout_place_button()

    def _layout_place_button(self):
        """Put the placement button under the alert bands, at this scale."""
        if self.place_button is None:
            return
        L = self._layout
        top = (self.panel_height()
               + MAX_ALERT_BANDS * (L.band_gap + L.band_height)
               + L.place_button_gap)
        # Inset by the same L.x(1) the bands use, so the button lines up
        # with the column of chrome above it rather than with the window.
        self.place_button.setGeometry(L.x(1), top, self.width() - L.x(2),
                                      L.place_button_height)
        # The label follows the size knobs like every other word on the
        # panel; a stylesheet alone would leave it at Qt's default and the
        # button would read as a different size at every scale.
        self.place_button.setFont(QFont("sans", L.pt(11), QFont.Weight.Bold))

    def mousePressEvent(self, event):
        """Start a drag. Placement mode only; the real overlay never sees one.

        Frameless windows get no help from the window manager, so the panel
        carries the pointer itself. What is remembered is the vector from
        the window's top-left to the press, so the card does not jump under
        the cursor on the first move.
        """
        if self.place_button is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = (event.globalPosition().toPoint()
                               - self.frameGeometry().topLeft())
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_from is None:
            return
        self.move(event.globalPosition().toPoint() - self._drag_from)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_from = None

    def keyPressEvent(self, event):
        """Escape abandons placement without saving.

        Frameless means there is no close button, so without this the only
        ways out would be the launcher's Stop and saving a position the
        player may not want. Cancelling has to stay as easy as committing,
        or the button stops being a decision.
        """
        if self.place_button is not None and event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        """Anything that resizes this window re-lays the chrome.

        _resize_to_content already does it for the three resizes Loom asks
        for; this catches the ones it does not - placement mode is an
        ORDINARY window, so the player can drag its edges and the window
        manager can resize it at map time, neither of which goes through
        any code here.
        """
        super().resizeEvent(event)
        self._layout_place_button()

    def fit_to_items(self):
        """Work out the item layout for what is on show, and resize to it.

        Called whenever the items change rather than at paint time: a
        widget cannot resize itself in the middle of its own paintEvent,
        and the two-column decision needs measured text, so both have to
        happen before the paint that uses them.
        """
        L = self._layout
        rows = self.item_rows
        if not rows:
            self._plan = steplayout.plan_items(0)
        else:
            points = L.pt(steplayout.ITEM_POINTS)
            metrics = QFontMetrics(QFont("sans", points))
            _radius, indent = item_indent(metrics, L.spacing)
            icon_height = L.icon(steplayout.ITEM_POINTS)
            widest = max(measure_segments(metrics, row, icon_height,
                                          L.spacing) for row in rows)
            columns = steplayout.columns_for(
                L.panel_width - L.x(32), widest + indent, len(rows),
                L.x(steplayout.COLUMN_GAP))
            self._plan = steplayout.plan_items(len(rows), columns)

        extra = L.y(self._plan.extra)
        if extra != self._extra:
            self._extra = extra
            self._resize_to_content()

    # ---- what to show --------------------------------------------------

    def show_waiting(self, message):
        """No usable reading: say so rather than leaving stale advice up."""
        self.have_reading = False
        self.status_line = message
        self.alerts = []
        self.update()

    def show_pregame(self, build, stage):
        """The chosen build, with no game behind it yet.

        The overlay now appears the moment it is started rather than when a
        match does, so that a player who launches Loom first can see that it
        is up and waiting instead of an empty screen that looks identical to
        a program that failed to start.

        Everything real is at zero and the banner says why. The numbers come
        from nowhere: villagers and the clock are literally 0, and passing no
        per-resource reading makes the resource row print the build's bare
        targets rather than have/want pairs. Step -1 is the documented
        "before the first step", so what shows is the build's opening move.

        The zeros are the part worth being careful about, because a panel
        showing numbers it did not read is the exact failure the rest of Loom
        is built to avoid. What makes them honest is the banner - and that
        show_step overwrites it on the first real reading, so it cannot be
        left behind once the game is driving.
        """
        self.show_step(build, 0, 0,
                       build.active_step_at(-1),
                       build.following_step_at(-1),
                       None)
        self.header_note, self.header_note_color = describe_waiting(stage)
        self.update()

    def apply_appearance(self):
        """Re-read the appearance settings and wear them now.

        Returns True when the panel changed SIZE, because the caller then
        has to put it back where it belongs - see place_panel. Colour-only
        changes need nothing from the caller.

        The launcher sends a line down stdin on every tick of a slider and
        this is what it lands on. Nothing is passed in: config.load() reads
        the file on each getter, so re-reading is exactly what __init__ did,
        and a setting added later becomes live without touching this.
        """
        # Always. Both are read fresh on every paint - the card colours in
        # _draw_background, the content pair in paintEvent and _pen - so
        # rebinding them is the whole of a transparency change.
        self._background, self._border = panel_background(
            config.background_opacity())
        self._content_opacity, self._content_contrast = content_style(
            config.text_visibility())

        resized = False
        if self._layout_from_config:
            wanted = (config.overlay_scale(), config.text_scale())
            current = (self._layout.overlay_scale, self._layout.text_scale)
            if wanted != current:
                # These three go together or not at all. The drawing code
                # mixes self.width(), which resize() sets, with
                # L.panel_height, which is recomputed every paint - so a
                # layout swapped without a resize draws the new height
                # inside the old width, clipped, with every right-aligned
                # number anchored to a width that no longer exists.
                self._layout = OverlayLayout(*wanted)
                # Baked to a pixel height at load, and the only thing here
                # that a repaint will not re-derive for itself. Fonts are
                # built inline from L.pt() so they need nothing.
                self._icons = load_resource_icons(
                    self._layout.icon(ICON_HEIGHT))
                # self._extra was measured through the OLD layout, so it is
                # the wrong number of pixels the moment the knobs move.
                # Re-measuring it here rather than waiting for the next poll
                # to do it matters in placement mode, where there is no next
                # poll - the panel would wear a stale height for as long as
                # the player looked at it.
                self.fit_to_items()
                self._resize_to_content()
                resized = True

        self.update()
        return resized

    def show_tracking(self, villagers, game_time, per_resource=None,
                      population=None, age_text="", tc_text="",
                      villager_gap=None):
        """Update the dashboard panel: what Loom reads, with no build behind it.

        Everything arrives as strings and numbers, never objects. The panel's
        contract is that it is TOLD what to show and never reads the game
        itself, and passing an age object rather than its name would have
        loom/overlay.py importing loom/age.py - an arrow the architecture map
        does not have, for no gain.

        age_text is "" when the crest has not been read. That is not the same
        as a missing age and must not be drawn as one - see _draw_dashboard.
        """
        self.have_reading = True
        self.report_rows = None
        self.actual = per_resource or {}
        # No build, so nothing to be off: the row shows what you have and
        # flags nothing. `None` rather than `{}` - see draw_resource_row.
        self.targets = None
        self.item_rows = []
        self.item_states = []

        minutes, seconds = divmod(int(game_time), 60)
        self.status_line = f"{minutes}:{seconds:02d}   {villagers} villagers"

        # The header's right-hand slot carries the age here, where the build
        # panel carries pace. Same slot, same drawing, different fact - there
        # is no pace to report without a build to be behind.
        self.age_text = age_text
        self.tc_text = tc_text
        self.population_text = (f"{population[0]}/{population[1]}"
                                if population else "")
        self.pace_text = age_text or "age not read"
        self.pace_color = TEXT if age_text else FAINT_TEXT

        self.header_note, self.header_note_color = describe_staleness(
            villager_gap)
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

    def show_report(self, rows, build_name, status_line, extra=0):
        """Switch the panel to the build-complete report.

        rows come from report.BuildReport.summary(). The panel stays in
        report mode until show_step is called again (a new game), and the
        alert bands keep working underneath - the game goes on.

        The header is drawn on this page too, so the chip is set HERE rather
        than left to whatever the last step-page poll happened to write.
        That inheritance is why a runaway "+55 VILL" once sat directly above
        a report row correctly reading "+4 beyond the build" - the report
        page never wrote the chip and never knew it was showing a stale one.
        Setting it means the two numbers on this page come from one value.
        """
        self.have_reading = True
        self.report_rows = rows
        self.build_name = build_name
        self.status_line = status_line
        self.pace_text, self.pace_color = describe_pace(None, extra,
                                                        complete=True)
        self.update()

    def show_step(self, build, villagers, game_time, active, following, delta,
                  per_resource=None, extra=0, complete=False,
                  milestone_queued=False,
                  follow_mode=None, resume_hint=None, seconds_left=None,
                  item_states=None, villager_gap=None, no_key=None):
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

        `item_states` is one checklist state per item of the active step,
        from loom/checklist.py. None means nothing is ticked, which is what
        demo mode and the pre-game panel want.

        `follow_mode` is loom.follow's answer to "is the game still driving
        this?", and `resume_hint` the binding that switches following back
        on. Together they are the one thing on this panel that is NOT about
        the game: they say whether what is on show can be trusted to track
        it. `no_key` says why there is no binding to name, when there is
        none - see describe_follow.
        """
        self.have_reading = True
        self.report_rows = None      # a step on show means no report page
        self.build_name = build.name
        self.actual = per_resource or {}

        minutes, seconds = divmod(int(game_time), 60)
        self.status_line = f"{minutes}:{seconds:02d}   {villagers} villagers"

        if active is None:
            self.item_rows = [[("text", "build complete")]]
            self.item_states = [checklist.NOT_DONE]
            self.step_when = ""
            self.targets = None
        else:
            # EVERY item, not a headline plus two. The old cap silently
            # dropped four of the seven instructions in the busiest shipped
            # step, and said nothing about it.
            self.item_rows = list(active.items_segments)
            self.item_states = list(item_states or [])
            when_minutes, when_seconds = divmod(int(active.time or 0), 60)
            self.step_when = f"by {when_minutes}:{when_seconds:02d} · {active.villager_count} vills"
            if milestone_queued:
                # The step's tech/age-up is already in the queue: one quiet
                # word of reassurance, no new lines.
                self.step_when += " · ✓ queued"
            # Where the villagers should be working. This comes straight from
            # the build order file - nothing is inferred.
            self.targets = active.villagers
        self.fit_to_items()

        if following is None:
            self.next_text = "" if active is None else "last step"
            self.next_when = ""
            self.next_segments = None
        else:
            self.next_text = following.details
            self.next_segments = following.details_segments or None
            when_minutes, when_seconds = divmod(int(following.time or 0), 60)
            self.next_when = f"{when_minutes}:{when_seconds:02d} · {following.villager_count} vills"

        self.pace_text, self.pace_color = describe_pace(delta, extra,
                                                        complete)
        self.header_note, self.header_note_color = describe_follow(
            follow_mode, resume_hint, seconds_left, no_key)
        # A follow note keeps the slot: MANUAL already says the panel is
        # not to be trusted to track the game, which covers the stale
        # count's warning and more. Otherwise a count that has stopped
        # being read says so where a fresh one would say nothing.
        if not self.header_note:
            self.header_note, self.header_note_color = describe_staleness(
                villager_gap)
        self.update()

    # ---- drawing -------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Bands mode draws no card and no writing - just the alarms, wherever
        # the player put them. It leaves before the content branch below
        # rather than being threaded through it, because every one of those
        # branches assumes a card exists to draw on.
        if self.panel_mode == BANDS_PANEL:
            if self.alerts:
                painter.setOpacity(1.0)
                self._draw_alert_bands(painter)
            return

        self._draw_background(painter)

        # Everything WRITTEN on the card fades with the text slider; the
        # card itself already faded with the background slider above. The
        # alert bands are deliberately outside both: TC IDLE and HOUSE SOON
        # are alarms, and an alarm the player faded three settings ago is an
        # alarm that fails at the one moment it exists for.
        painter.setOpacity(self._content_opacity)

        if not self.have_reading:
            painter.setPen(self._pen(DIM_TEXT))
            painter.setFont(QFont("sans", self._layout.pt(11)))
            painter.drawText(0, 0, self.width(), self.panel_height(),
                             Qt.AlignmentFlag.AlignCenter, self.status_line)
            # The bands still go up. A panel with no usable reading has
            # nothing to SAY, but an alert it was given is still true - and
            # this early return used to swallow them, which is why bands
            # mode could not simply reuse this path.
            if self.alerts:
                painter.setOpacity(1.0)
                self._draw_alert_bands(painter)
            return

        if self.report_rows is not None:
            self._draw_header(painter)
            self._draw_report(painter)
        elif self.panel_mode == DASHBOARD_PANEL:
            self._draw_header(painter)
            self._draw_dashboard(painter)
        else:
            self._draw_header(painter)
            self._draw_items(painter)
            self._draw_targets(painter)
            self._draw_next(painter)
        if self.alerts:
            painter.setOpacity(1.0)
            self._draw_alert_bands(painter)

    def _draw_report(self, painter):
        """The build-complete report: one stat per row, verdicts coloured."""
        L = self._layout
        painter.setFont(QFont("sans", L.pt(12), QFont.Weight.Bold))
        painter.setPen(self._pen(TEXT))
        painter.drawText(L.x(16), L.y(58), "BUILD COMPLETE")

        painter.setFont(QFont("sans", L.pt(10)))
        y = L.y(80)
        for label, value, good in self.report_rows[:6]:
            painter.setPen(self._pen(DIM_TEXT))
            painter.drawText(L.x(16), y, elide(painter, label, L.x(260)))

            if good is None:
                painter.setPen(self._pen(TEXT))
            elif good:
                painter.setPen(self._pen(ON_PACE_COLOR))
            else:
                painter.setPen(self._pen(BEHIND_COLOR))
            width = painter.fontMetrics().horizontalAdvance(value)
            painter.drawText(self.width() - L.x(16) - width, y, value)
            y += L.y(18)

    def _draw_background(self, painter):
        L = self._layout
        path = QPainterPath()
        path.addRoundedRect(L.x(1), L.x(1), self.width() - L.x(2),
                            self.panel_height() - L.x(2),
                            L.x(10), L.x(10))
        painter.fillPath(path, self._background)
        painter.setPen(self._border)
        painter.drawPath(path)

    def _pen(self, color):
        """A content colour through the contrast knob. Every pen in the
        step view goes through here; the alert bands deliberately do not -
        they are alarms and already at full strength."""
        return boosted(color, self._content_contrast)

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
            elif severity == alerts.URGE:
                fill = ALERT_URGE_BRIGHT if phase == 0 else ALERT_URGE_DIM
                text_color = ALERT_URGE_TEXT
            else:
                fill = ALERT_SOFT_FILL
                text_color = ALERT_SOFT_TEXT

            L = self._layout
            top = (self.panel_height() + L.band_gap
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
        painter.setPen(self._pen(DIM_TEXT))
        painter.drawText(L.x(16), L.y(24), self.status_line)

        painter.setFont(QFont("sans", L.pt(10), QFont.Weight.Bold))
        painter.setPen(self._pen(self.pace_color))
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(self.pace_text)
        painter.drawText(self.width() - L.x(16) - width, L.y(24),
                         self.pace_text)

        # The header note sits in the gap between the two, which is empty
        # in every normal frame - the status line runs to about a quarter of
        # the width and pace is a few characters right-aligned. Centring it
        # there means the usual panel is untouched and this costs no height,
        # which a 186px panel cannot spare.
        #
        # The step's own target ("by 7:55 - 22 vills") shares that slot and
        # loses to the note, which is the right priority: both are metadata
        # about what is on show, and MANUAL or WAITING says something about
        # whether the panel can be trusted at all. It moved here when the
        # items were equalised - it used to hang off the right of the
        # headline row, and with no headline row there is nowhere for it to
        # hang, nor any row it belongs to more than the others.
        if self.header_note:
            painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
            painter.setPen(self._pen(self.header_note_color))
            note_metrics = painter.fontMetrics()
            note_width = note_metrics.horizontalAdvance(self.header_note)
            painter.drawText((self.width() - note_width) // 2, L.y(24),
                             self.header_note)
        elif self.step_when:
            painter.setFont(QFont("sans", L.pt(9)))
            painter.setPen(self._pen(FAINT_TEXT))
            when_width = (painter.fontMetrics()
                          .horizontalAdvance(self.step_when))
            painter.drawText((self.width() - when_width) // 2, L.y(24),
                             self.step_when)

        painter.setPen(self._pen(QColor(255, 255, 255, 28)))
        painter.drawLine(L.x(14), L.y(34), self.width() - L.x(14), L.y(34))

    def _draw_items(self, painter):
        """The step's instructions, as a checklist of equal items."""
        L = self._layout
        plan = self._plan
        if not self.item_rows or plan.shown == 0:
            return
        rows = self.item_rows[:plan.shown]
        states = self.item_states[:plan.shown]
        draw_items(
            painter, rows, states,
            x=L.x(16), baseline=L.y(56), width=self.width() - L.x(32),
            columns=plan.columns, points=L.pt(plan.points),
            row_height=L.y(plan.row_height),
            icon_height=L.icon(plan.points),
            column_gap=L.x(steplayout.COLUMN_GAP),
            spacing=L.spacing, pen=self._pen,
            more_text=steplayout.more_label(plan.hidden))

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
        y = self.panel_height() - L.y(56)
        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        painter.setPen(self._pen(FAINT_TEXT))
        painter.drawText(L.x(16), y, "VILLS")
        # The row starts at its designed spot unless the label itself has
        # outgrown it - label width follows the text knob, the anchor only
        # the overlay knob, so at big text sizes the label needs more room.
        label_end = (L.x(16) + L.x(8)
                     + painter.fontMetrics().horizontalAdvance("VILLS"))

        painter.setFont(QFont("sans", L.pt(11), QFont.Weight.Bold))
        draw_resource_row(painter, self._icons, self.targets, self.actual,
                          max(L.x(66), label_end), y, spacing=L.spacing,
                          pen=self._pen)

    def _draw_dashboard(self, painter):
        """The tracking panel's body: what Loom knows that is not a build.

        Three labelled lines and the resource row, in the same idiom
        _draw_next uses for THEN - a bold faint label on the left, the value
        beside it - so the two panels look like the same program.

        An unread value says so rather than showing a plausible blank. "TCs
        not read" and an empty line are different facts and the player is
        owed the difference; a blank line reads as "no Town Centres", which
        is a claim Loom never made.
        """
        L = self._layout
        rows = (("TCS", self.tc_text, "not read"),
                ("POP", self.population_text, "not read"))
        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        label_end = L.x(16) + L.x(8) + max(
            painter.fontMetrics().horizontalAdvance(label)
            for label, _value, _absent in rows)
        value_x = max(L.x(62), label_end)

        for index, (label, value, absent) in enumerate(rows):
            baseline = L.y(58) + index * L.y(24)
            painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
            painter.setPen(self._pen(FAINT_TEXT))
            painter.drawText(L.x(16), baseline, label)

            painter.setFont(QFont("sans", L.pt(11),
                                  QFont.Weight.Bold if value
                                  else QFont.Weight.Normal))
            painter.setPen(self._pen(TEXT if value else FAINT_TEXT))
            painter.drawText(value_x, baseline, value or absent)

        # The villagers-per-resource row, in the same place and the same
        # label dance the build panel uses - it is the same reading.
        y = self.panel_height() - L.y(20)
        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        painter.setPen(self._pen(FAINT_TEXT))
        painter.drawText(L.x(16), y, "VILLS")
        vills_end = (L.x(16) + L.x(8)
                     + painter.fontMetrics().horizontalAdvance("VILLS"))
        painter.setFont(QFont("sans", L.pt(11), QFont.Weight.Bold))
        draw_resource_row(painter, self._icons, None, self.actual,
                          max(L.x(66), vills_end), y, spacing=L.spacing,
                          pen=self._pen)

    def _draw_next(self, painter):
        L = self._layout
        baseline = self.panel_height() - L.y(18)

        painter.setPen(self._pen(QColor(255, 255, 255, 28)))
        painter.drawLine(L.x(14), baseline - L.y(26),
                         self.width() - L.x(14), baseline - L.y(26))

        painter.setFont(QFont("sans", L.pt(9), QFont.Weight.Bold))
        painter.setPen(self._pen(FAINT_TEXT))
        painter.drawText(L.x(16), baseline, "THEN")
        # Same give-the-label-room rule as the VILLS row: the text starts at
        # its designed spot unless "THEN" has outgrown it.
        label_end = (L.x(16) + L.x(8)
                     + painter.fontMetrics().horizontalAdvance("THEN"))
        text_x = max(L.x(62), label_end)

        painter.setFont(QFont("sans", L.pt(10)))
        painter.setPen(self._pen(DIM_TEXT))

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
            painter.setPen(self._pen(FAINT_TEXT))
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

    Returns the x the line ended at, so a caller can rule a line through
    what was actually drawn. A strikethrough has to know where the ink
    stops, and it cannot come from the font: QFont's strikeOut misses the
    inline icons entirely, which is exactly half of some instructions.
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
                    return x + ellipsis
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
            return right
        painter.drawText(int(x), baseline, value)
        x += width + round(5 * spacing)
    return x


def measure_segments(metrics, segments, icon_height, spacing=1.0):
    """How wide this line wants to be, drawn in full with nothing elided.

    The advance arithmetic of draw_segments with the drawing taken out, so
    the two cannot disagree about how much room a line needs. Used to decide
    whether two columns fit, which has to be answered BEFORE a paint - the
    panel's height depends on it, and a widget cannot resize itself
    mid-paintEvent.
    """
    width = 0
    for kind, value in segments:
        if kind == "icon":
            pixmap = load_step_icon(value, icon_height)
            if pixmap is not None:
                width += pixmap.width() + round(4 * spacing)
                continue
            value = build_order.icon_to_words(value)
        width += metrics.horizontalAdvance(value) + round(5 * spacing)
    return width


def item_indent(metrics, spacing=1.0):
    """(bullet radius, text indent) for item rows in this font.

    Derived from the font's own ascent rather than fixed, so the bullet and
    the gap after it grow with the text at every overlay and text scale -
    a bullet measured in pixels would be the pixel-constant mistake again.
    Shared by the drawing and the two-column decision so they agree about
    how much width the bullet costs.
    """
    radius = max(2, round(metrics.ascent() * 0.28))
    return radius, radius * 2 + round(8 * spacing)


# The checklist colours. An OBSERVED tick is green because the game said so
# and green is what this panel already uses for "you are doing the right
# thing"; an ASSUMED one is the faintest grey on the card, because it is a
# guess and must never carry the weight of a reading. The two being
# distinguishable at a glance is the whole honesty of the feature - see
# loom/checklist.py.
ITEM_TODO_BULLET = DIM_TEXT
ITEM_TODO_TEXT = TEXT
ITEM_ASSUMED_BULLET = FAINT_TEXT
ITEM_ASSUMED_TEXT = FAINT_TEXT
ITEM_OBSERVED_BULLET = ON_PACE_COLOR
ITEM_OBSERVED_TEXT = DIM_TEXT

# An item the feed was watching for and never confirmed. Amber, the same
# colour MANUAL uses, because it means the same kind of thing: notice this,
# it is not an alarm. Deliberately NOT struck through - Loom does not know
# it was skipped, only that nothing confirmed it, and a line through it
# would claim the opposite of what is true.
ITEM_UNCONFIRMED_BULLET = NOT_FOLLOWING_COLOR
ITEM_UNCONFIRMED_TEXT = DIM_TEXT


def item_colors(state):
    """(bullet colour, text colour, struck, filled) for one item state.

    `filled` is what separates the two kinds of tick at a glance: only an
    OBSERVED item gets a solid bullet. An assumed one keeps the hollow ring
    of an unticked item and merely fades, which is the right shape for
    "probably, but nothing said so".
    """
    if state == checklist.OBSERVED:
        return ITEM_OBSERVED_BULLET, ITEM_OBSERVED_TEXT, True, True
    if state == checklist.UNCONFIRMED:
        return ITEM_UNCONFIRMED_BULLET, ITEM_UNCONFIRMED_TEXT, False, False
    if state == checklist.ASSUMED:
        return ITEM_ASSUMED_BULLET, ITEM_ASSUMED_TEXT, True, False
    return ITEM_TODO_BULLET, ITEM_TODO_TEXT, False, False


def draw_items(painter, rows, states, x, baseline, width, columns, points,
               row_height, icon_height, column_gap, spacing=1.0, pen=None,
               more_text=""):
    """Draw one step's items as a checklist. Returns the last baseline used.

    A module function beside draw_segments for the same reason that one is:
    the overlay panel and the preview's cards draw the same list, and two
    implementations of it would drift. Everything measured in pixels arrives
    already mapped through the caller's own scale - the overlay's two axes
    are not one number, so this cannot do the mapping itself.

    `rows` is a list of segment lists and `states` the matching checklist
    states; both are used only up to whatever the caller's ItemPlan says is
    shown. Columns are filled top to bottom and then across, so reading down
    the left column and carrying on at the top of the right one follows the
    build's own order.

    The bullets are PAINTED, not typed. A '●' from the font would depend on
    whatever glyph the platform's sans happens to have, and would not scale
    with the text; an ellipse of the font's own ascent always does.
    """
    if pen is None:
        pen = lambda color: color
    if not rows:
        return baseline
    # A card the player has scrolled past is drawn faded on purpose, and the
    # ONE thing worth seeing on it is the item nothing confirmed. So an
    # unconfirmed row is drawn at full strength whatever the caller set.
    faded = painter.opacity()

    painter.setFont(QFont("sans", points))
    metrics = painter.fontMetrics()
    radius, indent = item_indent(metrics, spacing)

    column_width = width
    if columns > 1:
        column_width = (width - column_gap) // columns
    per_column = math.ceil(len(rows) / columns)

    last = baseline
    for index, segments in enumerate(rows):
        column, row = divmod(index, per_column)
        left = x + column * (column_width + column_gap)
        y = baseline + row * row_height
        last = max(last, y)

        state = states[index] if index < len(states) else checklist.NOT_DONE
        bullet, text_color, struck, filled = item_colors(state)
        painter.setOpacity(1.0 if state == checklist.UNCONFIRMED else faded)

        # The bullet sits on the text's own centre line rather than its
        # baseline, so it stays visually level with the words at any size.
        centre = y - round(metrics.ascent() * 0.35)
        painter.setPen(pen(bullet))
        painter.setBrush(pen(bullet) if filled else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(left + radius, centre - radius,
                            radius * 2, radius * 2)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setPen(pen(text_color))
        text_left = left + indent
        end = draw_segments(painter, segments, text_left, y,
                            column_width - indent, icon_height=icon_height,
                            spacing=spacing)
        if struck:
            # Ruled across what was actually drawn, icons included, at the
            # font's own strikeout height.
            rule = y - round(metrics.ascent() * 0.32)
            painter.drawLine(int(text_left), rule, int(end), rule)

    painter.setOpacity(faded)
    if more_text:
        last += row_height
        painter.setPen(pen(FAINT_TEXT))
        painter.drawText(int(x + indent), last, more_text)
    return last


def resource_cell(want, have):
    """One resource's number, as (text, off_target, dim). Pure.

    Split out of draw_resource_row so the decision can be tested without a
    painter - which is how the "8/None" bug should have been caught. Every
    geometry assertion passed while the row read that on screen, because the
    only thing that knew the answer was a line in the middle of a drawing
    loop.

    want is None when there is no build behind the row. Then the count
    stands alone and nothing can be off target: a red underline against a
    plan that does not exist would be a verdict Loom never reached. An en
    dash where nothing was read, never a 0 - "no villagers on stone" and "I
    could not see the stone count" are different facts, and only one of them
    was established.

    THAT RULE NOW HOLDS IN BOTH BRANCHES, and it did not. With a build
    behind the row an unread count fell back to the TARGET alone, so a
    player with eleven villagers on wood against a build wanting five saw
    "5" - which reads as a wrong number rather than a missing one, and was
    reported as exactly that (issue #13). The dash is the honest half of
    the pair: "I could not see it, and the build wants five".
    """
    if want is None:
        return ("–" if have is None else str(have), False, have is None)
    off = have is not None and abs(have - want) > RESOURCE_TOLERANCE
    text = f"–/{want}" if have is None else f"{have}/{want}"
    return text, off, have is None and want == 0


def draw_resource_row(painter, icons, targets, actual, x, y, spacing=1.0,
                      pen=None):
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

    pen maps each colour before it is used - the overlay passes its contrast
    knob; the default identity keeps the preview exactly as designed.

    targets=None means there is no build behind this row, so there is nothing
    to be off. That is not the same as an empty dict: `{}` says the build
    wants nobody here, which is a target of zero and worth flagging when
    somebody is. Passing `{}` for "no build" would paint "8/0" with a red
    off-target underline on every resource the player is actually using.
    """
    if pen is None:
        pen = lambda color: color
    for name in RESOURCE_ORDER:
        want = None if targets is None else targets.get(name, 0)
        have = actual.get(name)

        text, off, dim = resource_cell(want, have)
        resource_color = QColor(90, 90, 98) if dim else RESOURCE_COLORS[name]

        icon = icons.get(name)
        if icon is not None:
            # The baked pixmap knows its own (possibly scaled) height, so no
            # icon-height parameter is needed to align it to the baseline.
            painter.drawPixmap(int(x), y - icon.height() + round(3 * spacing),
                               icon)
            x += icon.width() + round(6 * spacing)
        else:
            painter.setPen(pen(resource_color))
            label = RESOURCE_LABELS[name]
            painter.drawText(int(x), y, label)
            x += (painter.fontMetrics().horizontalAdvance(label)
                  + round(6 * spacing))

        painter.setPen(pen(TEXT if off else resource_color))
        painter.drawText(int(x), y, text)
        width = painter.fontMetrics().horizontalAdvance(text)

        if off:
            painter.setPen(pen(OFF_TARGET_COLOR))
            painter.drawLine(int(x), y + round(3 * spacing),
                             int(x) + width, y + round(3 * spacing))

        x += width + round(18 * spacing)
    return x


# The two things Loom can be waiting for before a match is on screen, named
# rather than left as a bare boolean because they mean different things to
# the player: one is "your game is not running", the other is "your game is
# running and I am watching for a match".
WAITING_FOR_GAME = "waiting_for_game"
WAITING_FOR_MATCH = "waiting_for_match"


def describe_tcs(busy, idle):
    """The dashboard's Town Centre line: "2 producing, 1 idle".

    Both numbers come straight from the tracker's two beliefs and NEITHER is
    derived from the other. production.py asks a slot's tint two different
    questions with two different gates - does another Town Centre exist, and
    is this one working - because erring busy is safe while erring towards a
    Town Centre that does not exist is permanent. Subtracting one count from
    the other here would manufacture a third answer that neither gate stands
    behind, which is the whole point of keeping them apart.

    "" when nothing is believed yet, which the dashboard draws as "not read"
    rather than as an absence of Town Centres.
    """
    if not busy and not idle:
        return ""
    parts = []
    if busy:
        parts.append("TC producing" if busy == 1 else f"{busy} producing")
    if idle:
        parts.append("TC idle" if idle == 1 and not busy else f"{idle} idle")
    return ", ".join(parts)


def waiting_band(stage):
    """What a bands-only panel says before there is a match to watch.

    A panel that draws no card and has no alerts is a completely transparent
    window, which is indistinguishable from a Loom that failed to start -
    the exact failure LiveSession was built to remove for the build panel.
    So the waiting message becomes a band, and the player can see Loom is
    alive.

    SOFT, not FULL: it is information, and SOFT does not flash. An alarm that
    fires because nothing is wrong teaches the player to ignore the ones that
    mean something.

    Returns a list ready for show_alerts, so the caller never assembles a
    band by hand. Reuses describe_waiting's vocabulary so the two panels
    cannot drift into saying different things about the same state.
    """
    text, _color = describe_waiting(stage)
    return [(f"LOOM — {text}", alerts.SOFT)] if text else []


def describe_waiting(stage):
    """The header banner for a panel with no game behind it yet.

    Returns (text, colour); "" means draw nothing, so the same slot is free
    the moment a real reading arrives.

    Pure, like describe_follow and describe_pace below it, so the wording is
    checkable without a window - which is how every test in this module's
    suite is written.

    Why the panel says this at all: it now appears as soon as the overlay
    starts, before the game is up, showing the chosen build with its numbers
    at zero. Zeros with nothing to explain them would be a panel claiming a
    reading it does not have, which is the one thing Loom must never do. The
    banner is what makes them honest.
    """
    if stage == WAITING_FOR_GAME:
        return "WAITING FOR THE GAME", NOT_FOLLOWING_COLOR
    if stage == WAITING_FOR_MATCH:
        return "WAITING FOR A MATCH", NOT_FOLLOWING_COLOR
    return "", NOT_FOLLOWING_COLOR


# Why the BUILD DONE note has no key to name, when it has none. Hotkeys
# ship switched off from 1.0.5, so "no key" is now the FIRST thing a new
# player meets at the end of their first build - and a note that just said
# BUILD DONE would leave the report unreachable and unmentioned, which is a
# feature that might as well not exist.
#
# Two reasons rather than one, because the remedies are different and a note
# naming the wrong one is worse than a note naming none: the switch is off,
# or the switch is on and no step key is bound. A machine with no hotkey
# backend at all is neither - there is nothing to throw and nothing to bind,
# so it gets the bare note. Pointing a player at a switch that cannot help
# them would be exactly the kind of confident wrong answer this panel exists
# to avoid.
HOTKEYS_OFF = "hotkeys_off"
HOTKEYS_UNBOUND = "hotkeys_unbound"


def describe_follow(mode, resume_hint=None, seconds_left=None, no_key=None):
    """The header note for a panel that is not following the game.

    Returns (text, colour); the text is "" while the game is driving, which
    is the normal case and draws nothing.

    A pure function of its arguments, so every phrasing is checkable without
    a window - the same reason describe_pace below is one.

    `no_key` is HOTKEYS_OFF or HOTKEYS_UNBOUND when there is no key to name
    and something can be done about it, and None otherwise.

    The wording matters more than it looks. Loom's entire claim is that it
    reads the game and keeps up by itself, so a panel showing a step it is no
    longer syncing to is a lie unless it says so. MANUAL names the state and
    the hint names the way out, because a player who toggled following off
    twenty minutes ago will not remember which key did it.
    """
    from . import follow

    if mode is None or mode == follow.FOLLOWING:
        return "", NOT_FOLLOWING_COLOR
    if mode == follow.DONE:
        # The build is finished and the panel is resting on its last card
        # - the author's ruling: the report must not steal the panel, some
        # builds' last card is exactly what a player wants in front of
        # them while they settle into the late game. The note names the
        # way forward, because a report one unnamed keypress away might as
        # well not exist - and with no key to name it names the way to GET
        # one, for the same reason.
        if resume_hint:
            return f"BUILD DONE · {resume_hint} for the report", \
                NOT_FOLLOWING_COLOR
        if no_key == HOTKEYS_OFF:
            return "BUILD DONE · enable hotkeys for the report", \
                NOT_FOLLOWING_COLOR
        if no_key == HOTKEYS_UNBOUND:
            return "BUILD DONE · bind a step key for the report", \
                NOT_FOLLOWING_COLOR
        return "BUILD DONE", NOT_FOLLOWING_COLOR
    if mode == follow.MANUAL:
        if resume_hint:
            return f"MANUAL · {resume_hint} to resume", NOT_FOLLOWING_COLOR
        # Hotkeys are off or unavailable, so there is no key to name. Saying
        # so anyway beats implying the panel is still tracking the game.
        return "MANUAL · not following the game", NOT_FOLLOWING_COLOR
    # Holding: this fixes itself in a few seconds, so it is stated quietly -
    # but it says HOW MANY. This comment used to argue the opposite, that a
    # number ticking down in the corner of a game is a distraction and the
    # panel returning to normal is the real signal. Living with it said
    # otherwise: "resuming shortly" leaves you watching the panel wondering
    # whether it has stuck, and a number answers that at a glance. The author
    # asked for the countdown; the seconds come from follow.seconds_left,
    # which has existed and been tested since the hold was written.
    if seconds_left:
        return f"manual · resuming in {seconds_left}s", HOLDING_COLOR
    return "manual · resuming shortly", HOLDING_COLOR


def describe_staleness(villager_gap):
    """The header note for a villager count that has stopped being read.

    Returns (text, colour); "" while the reads are fresh, which is every
    normal poll. A pure function for the same reason describe_follow is.

    This is the never-guess rule applied to the panel's own face. The
    villager filter holds its last belief through unreadable polls on
    purpose - right for a menu - but when the clock keeps reading and the
    villager band alone has gone quiet, the held number is an assumption
    wearing a reading's clothes. The author watched exactly that: a count
    frozen at 6 for a whole game with nothing anywhere admitting it. The
    note names the number of seconds because "how long has it been stuck"
    is the first question the frozen panel raises.
    """
    if not villager_gap:
        return "", NOT_FOLLOWING_COLOR
    return f"VILLAGERS UNREAD · {int(villager_gap)}s", NOT_FOLLOWING_COLOR


def describe_pace(delta, extra=0, complete=False):
    """Turn a pace delta in seconds into (text, color).

    extra is how many villagers beyond the build's ask are on the field.
    It prefixes the pace chip and turns it amber: the number that follows
    is still true, but the build's goal has shifted - an extra villager
    before an age-up slides the click 25-40 seconds, and the player should
    see the cause next to the effect. Red stays red: already-behind is
    still the louder fact.

    complete says the build is over, and it changes what the number MEANS
    rather than only how it is drawn. During the build "+2 VILL" is a live
    warning about a slip that is happening. Afterwards there is no build
    left to be over, so the same shape read as a count still running: it
    reached "+55 VILL · —" in a real game, twenty minutes after a build
    that ended at +4, beside a report row still correctly saying +4. So it
    is worded as the total it is - "+4 VILLS > BUILD" - and the "· —" tail
    goes, because a dash where a pace verdict used to be reads as a missing
    reading rather than as a meter that has honourably retired.
    """
    if complete:
        # Nothing to say when the player never went over: the header's
        # centre slot already carries BUILD DONE.
        if extra > 0:
            return f"+{extra} VILLS > BUILD", SLIGHTLY_BEHIND_COLOR
        return "", FAINT_TEXT

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
