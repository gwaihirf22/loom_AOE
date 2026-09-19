"""
Loom — the launcher window.

One ordinary window that does what I otherwise do from four terminal tabs:
pick a build order, start and stop the overlay, adjust the alert settings,
and (in developer mode) reach the debug tools and the test suite.

Everything the launcher starts runs as a child process (see runner.py for
why), so this file is only widgets and wiring. The one design point worth
stating: settings are written to config.json the moment they change, but the
overlay reads its config once at startup - so changes apply the next time
the overlay starts, and the UI says so rather than pretending otherwise.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import shutil
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QDesktopServices, QFont, QFontDatabase,
                         QPixmap)
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                             QFrame, QGridLayout, QGroupBox,
                             QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QPlainTextEdit, QPushButton,
                             QScrollArea, QSlider, QSpinBox, QTabWidget,
                             QToolButton,
                             QVBoxLayout, QWidget)

from . import (apm, buildcheck, config, entry, hotkeys, overlay, paths,
               placement, replay, statefeed)
from .flowlayout import flow_row
# One home for the window arithmetic. These used to be defined
# here, back when the launcher was the only window that placed
# another one beside itself.
from .placement import WINDOW_GAP, beside, clamped_position
from .tooltips import wrapped
from .hotkeys import keyspec, qtkeys
from . import __version__ as loom_version
from .about import AboutWindow
from .browser import BuildBrowser
from .statsview import RECORD_COLOR, StatsWindow, css_rgb
from .build_order import (GENERIC_CIVILIZATION, available_builds,
                          civilization_label, civilization_names,
                          civilizations, filtered_builds)
from .runner import ChildProcess

# How tall the build list stands: enough rows that a filtered search shows
# its whole answer without scrolling, few enough that the library does not
# push the settings below it off the window.
LIBRARY_ROWS = 6
LIBRARY_ROW_HEIGHT = 22

# How many lines the output pane keeps before dropping the oldest. Enough to
# scroll back through a pytest run; small enough to never matter for memory.
OUTPUT_SCROLLBACK_LINES = 2000

# The developer-mode commands, as data rather than widget code so a test can
# check the argv each button produces without instantiating any Qt. Each row:
# (button label, output-pane prefix, argv builder, tooltip). The builders
# take the chosen build stem and coach scenario even when they ignore them,
# so every row is called the same way.
DEV_COMMANDS = [
    ("Overlay demo", "demo",
     lambda stem, scenario: ["loom_overlay.py", "--demo", "--build", stem],
     "Run the overlay against a replayed match - no game needed."),
    ("Coach simulate", "coach",
     lambda stem, scenario: ["loom_coach.py", "--simulate",
                             "--scenario", scenario, "--build", stem],
     "Run the terminal coach against the chosen synthetic scenario."),
    ("Readout (log misreads)", "read",
     lambda stem, scenario: ["loom_read.py", "--debug-pop"],
     "Raw readout of the HUD numbers; saves a crop of every failed"
     " population read."),
    ("Grab frames", "frames",
     lambda stem, scenario: ["-m", "tools.grab_frames"],
     "Screenshot the game window on a timer, for building test data."),
    # The build stem is BOTH the overlay's build and the capture folder's
    # label, so the folder name says which build it was recorded against.
    # That is not decoration: a session recorded against the wrong build is
    # indistinguishable from a good one afterwards, and one already was.
    ("Record session", "session",
     lambda stem, scenario: ["-m", "tools.dev_session",
                             "--build", stem, "--label", stem],
     "Play a real game with the overlay AND frame capture running, then"
     " gather the log, the stats and the frames into one folder."),
    # Needs the game running: proves the panel does not steal the pointer and
    # so does not break the game's hold on the cursor. --passthrough off
    # reproduces the old bug on purpose.
    ("Passthrough check", "passthrough",
     lambda stem, scenario: ["-m", "tools.overlay_test",
                             "--style", "tooltip", "--passthrough", "on"],
     "With the game running: prove the overlay cannot steal the mouse from"
     " the game."),
    ("Run tests", "pytest",
     lambda stem, scenario: ["-m", "pytest", "tests/", "-q"],
     "Run the whole test suite; output streams below."),
]

COACH_SCENARIOS = ("perfect", "behind", "stall")

# Dev commands that start a REAL overlay against the REAL game, and so must
# not run while the overlay slot already has one. Two overlays reading the
# same HUD both write a statistics file and both count APM, and neither says
# so - the same duplicate-counting failure apm.counted_in_the_overlay exists
# to prevent, arriving by a different door. Held as output-pane prefixes
# rather than a fifth field in DEV_COMMANDS so the row shape stays as it is,
# and because the prefix is what the dev slot has in hand.
NEEDS_THE_OVERLAY_SLOT_FREE = {"session"}

# Placement is an everyday control, not a developer tool, so it lives with
# the Start/Stop buttons - but its argv stays module data like DEV_COMMANDS,
# so the same test can check it without any Qt.
PLACE_COMMAND = ("place",
                 lambda stem: ["loom_overlay.py", "--place", "--build", stem])

# The two build-free modes, in the order they are pinned above the library.
# Module data like DEV_COMMANDS and for the same reason: the labels and the
# argv they produce can then be checked by a test with no Qt at all.
TRACKING_ROWS = (
    (config.TRACKING_MODE,
     "NO BUILD ORDER — Tracking and Alerts only"),
    (config.TRACKING_PANEL_MODE,
     "NO BUILD ORDER — with basic overlay — Alerts and Tracking"),
)

# Which --no-build shape each mode asks the overlay for.
TRACKING_FLAGS = {config.TRACKING_MODE: "bands",
                  config.TRACKING_PANEL_MODE: "panel"}


def overlay_argv(mode, stem, place=False):
    """The argv that runs this mode, as plain data.

    One function for Start and for Place overlay, because the two must agree
    about what a mode IS - a placement panel that showed a different thing
    from the one that plays would be placing the wrong window.

    A tracking mode never carries --build. The flag says what it is instead
    of a sentinel stem, so nothing downstream has to learn to refuse a stem
    that is not one - BuildOrder.load_by_name is never handed a mode.
    """
    argv = ["loom_overlay.py"]
    if place:
        argv.append("--place")
    if mode in TRACKING_FLAGS:
        argv += ["--no-build", TRACKING_FLAGS[mode]]
    else:
        argv += ["--build", stem]
    return argv


# How the settings are grouped into tabs. Data rather than widget code for
# the same reason DEV_COMMANDS is: a test can check the grouping without
# instantiating any Qt. Each row is (tab label, attribute names on the
# window, whether the group box keeps its own title).
#
# The boxes that are alone under a tab lose their titles - the tab already
# says "Alerts", and a box captioned Alerts inside it says it twice. The
# Appearance tab holds two boxes, so those keep theirs to tell them apart.
SETTINGS_TABS = [
    ("Alerts", ["settings"], False),
    ("Appearance", ["appearance", "transparency"], True),
    ("Preview", ["preview_appearance"], False),
    ("Hotkeys", ["hotkeys_box"], False),
]

# What the developer tab is called when it is present.
DEV_TAB_LABEL = "Developer tools"

# The launcher's size on a screen with room for it, and the smallest it may
# be dragged to. Below the minimum the group boxes start eliding their own
# captions, and a scroll area cannot help with width the way it helps with
# height.
PREFERRED_SIZE = (720, 840)
MINIMUM_SIZE = (560, 380)

# How long the window waits after a resize or a move before writing the
# geometry to config.json. Saving per pixel of a drag would hammer the file;
# this is the same debounce loom/browser.py uses for the preview window.
SAVE_GEOMETRY_AFTER_MS = 1000

# Room kept for a hotkey row's "restore default" button whether or not one
# is showing, so the fields do not jump sideways as bindings change.
RESET_COLUMN_WIDTH = 26

# How a findings list opens inside a Qt message box.
#
# MARGINS ONLY, and the indent is deliberately left alone. Rendered and
# measured at 560px wide, because two guesses at this were wrong:
#
#   plain <ul>              bullets 28px in
#   -qt-list-indent: 1      bullets 28px in - it is Qt's DEFAULT, so the
#                           property was present and doing nothing
#   -qt-list-indent: 0      bullets 4px in, AND NO BULLETS AT ALL: with no
#                           indent there is nowhere to draw the marker, so
#                           Qt drops it and the list reads as a paragraph
#   margin-left: 0          ignored; Qt honours its own property, not this
#
# Losing the markers would recreate the thing this helper exists to
# prevent - "one paragraph holding four problems runs together into
# something nobody reads to the end" - so the indent stays and the report's
# other half stands. The margins are what close the doubled gap, with the
# caller's own <br> providing the separation: 47px above the list becomes
# 21px, and the whole dialog goes from 192px to 166px.
LIST_STYLE = "<ul style='margin-top: 0; margin-bottom: 0;'>"

# What the hotkey panel says about itself, in its two states.
#
# The hint is the one that has always been there, and it is why the lock
# exists: the two owners apply a change at different moments, so an edit
# made mid-run is half-live. Saying that is true and useless - the player
# still gets to make the edit and still gets the confusing result.
HOTKEY_HINT = ("The step keys apply the next time the overlay starts;"
               " the start/stop key applies immediately.")
HOTKEY_LOCKED = ("Stop the overlay to change these. It registered these"
                 " combinations when it started and will not see a change"
                 " until it starts again.")

# Amber, the same colour the clash warning above it uses. This is the one
# thing on the panel that must stay readable at the moment everything else
# greys out, so it does not grey with them.
LOCKED_NOTICE = QColor(235, 190, 90)

# How long to wait before asking a second time for the match's recorded
# game. Derived from `replay.SETTLED_SECONDS` rather than written next to
# it as a number: the wait exists ONLY because that window has to pass, so
# a change there that this did not follow would put the retry back inside
# the refusal it was written to outlast. The margin is for the clock the
# refusal measures with being the file's mtime, not this timer's start.
RETRY_ATTACH_AFTER_MS = (replay.SETTLED_SECONDS + 5) * 1000

# How far the record's chart colour is darkened to become a button fill,
# and how far the hover lightens back. Measured against white text: the
# chart colour itself is 2.58:1, these are 5.88:1 and 4.73:1, and 4.5:1
# is the floor for comfortable reading. Kept as numbers rather than two
# hex codes so the button stays tied to RECORD_COLOR.
STATS_BUTTON_DARKEN = 160
STATS_BUTTON_HOVER_DARKEN = 140

# How often, at most, a settings change is announced to a RUNNING overlay.
#
# A throttle rather than the debounce above, and the difference matters: a
# debounce restarts its timer on every change, so a slider being dragged
# would show nothing at all until the player stopped moving - which is the
# complaint this whole feature exists to answer. This sends one line, then
# at most one more per interval while changes keep arriving, then a last one
# when they stop, so the panel follows the drag and the pipe never carries a
# line per pixel. Ten a second is smooth to the eye and cheap to the child.
ANNOUNCE_SETTINGS_EVERY_MS = 100

# One base stylesheet on every overlay-control button, applied at
# construction and NEVER removed. The padding is not the point - the style
# engine is. Setting any stylesheet on a widget moves its whole rendering
# to Qt's stylesheet style, while an unstyled widget draws with the
# platform's native one, and on macOS those two disagree about a button's
# size. The five controls used to mix the two paths - Start and Stop
# styled, Place and Reset native, Hide flipping between the paths at
# runtime - so a size hint computed under one style was drawn under the
# other, and on macOS the disabled buttons shrank until their own text no
# longer fit. One always-present base rule keeps every button on the
# stylesheet path in every state, on every platform, so the hint and the
# drawing can never come from different engines. The padding itself is
# the same 4px 14px the Statistics and How-to-use buttons already wear.
#
# The coloured variants CONTAIN the base rather than replacing it, and
# they are :enabled-scoped on purpose: the scope keeps Qt's greyed look on
# the inactive button, so a grey button still reads as "not clickable"
# rather than as a colorless clickable one.
CONTROL_BUTTON_STYLE = "QPushButton { padding: 4px 14px; }"
GO_BUTTON_STYLE = (CONTROL_BUTTON_STYLE +
                   " QPushButton:enabled {"
                   " background-color: #2e7d32; color: white; }")
STOP_BUTTON_STYLE = (CONTROL_BUTTON_STYLE +
                     " QPushButton:enabled {"
                     " background-color: #b03a2e; color: white; }")


def overlay_status_text(running, hidden=False):
    """What the launcher says the overlay is doing.

    Pure, like beside and fitted_size below, so the wording is testable
    without building a window - which is how everything else in this
    module's suite is checked.

    "hidden" rather than "running, hidden": hiding only means anything to an
    overlay that IS running, and the Stop button staying enabled beside it
    already says the process is alive. The key that brings it back is not
    named here because the button next to this label does the same job and
    cannot be forgotten or rebound out from under the player.
    """
    if not running:
        return "overlay: not running"
    return "overlay: hidden" if hidden else "overlay: running"


def fitted_size(preferred, minimum, area):
    """How big to open a window on the screen it actually lands on.

    preferred and minimum are (width, height); area is the screen's work area
    as (left, top, right, bottom) - the same shape beside() takes. Returns
    (width, height).

    Pure arithmetic for the same reason as beside(): the interesting cases
    are screens I do not own. This exists because the launcher opened at a
    fixed 720x840 whatever it was opened on, and Qt will not honour a resize
    below the layout's own minimumSizeHint - so on a 1080p screen with the
    developer panel showing, the window came up taller than the desktop with
    Start and the output pane below the bottom edge. Reported from a 1920x1080
    Windows 10 machine; invisible here, where the work area is 1440 tall.

    Never bigger than the work area, and never smaller than the minimum
    unless the screen itself is smaller than that - a window that will not
    fit is still better placed inside the screen than hanging off it.
    """
    preferred_width, preferred_height = preferred
    minimum_width, minimum_height = minimum
    left, top, right, bottom = area
    available_width = max(1, right - left)
    available_height = max(1, bottom - top)

    width = max(min(preferred_width, available_width),
                min(minimum_width, available_width))
    height = max(min(preferred_height, available_height),
                 min(minimum_height, available_height))
    return width, height


def fixed_width_font():
    """A font that is actually fixed-pitch, on every platform.

    `QFont("Monospace")` with a Monospace style hint is the obvious spelling
    and it does not work on Windows. Measured: it resolves to **Tahoma**,
    with `QFontInfo.fixedPitch()` False - so the pane the comment below
    calls fixed-width has been proportional for every Windows user, and
    pytest's aligned output has been the soup it warns about. There is no
    family called "Monospace" there and the style hint is a preference, not
    a requirement; Qt satisfied the family lookup with the default UI font
    and never applied the hint.

    It also produced a visible symptom nobody connected to the font. Windows
    offers `8514oem` - a legacy OEM raster face - as a Monospace substitute,
    DirectWrite cannot load it, and Qt printed a CreateFontFaceFromHDC error
    to the terminal. Intermittently, because substitution is only attempted
    when a glyph is actually wanted, which made it look like a random fault
    rather than a font that was wrong from the first paint.

    So this names real families and lets the platform's own fixed font have
    the last word rather than a style hint nobody is obliged to honour.
    QFontDatabase answers that per platform - Courier New on Windows, and
    whatever the desktop is configured for elsewhere.
    """
    system = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font = QFont(system)
    # Preferred first, the platform's own answer last, so an unusual desktop
    # still gets something fixed-pitch rather than the UI font. The list is
    # the same everywhere; only the ORDER is per platform, because a missing
    # family at the head of the list is not free: Qt builds its whole
    # font-alias table to rule it out, and macOS printed "Populating font
    # family aliases took 61 ms... missing font family Consolas" at every
    # launch until the family that machine actually has came first.
    families = {
        "darwin": ["Menlo", "Consolas", "DejaVu Sans Mono",
                   "Liberation Mono"],
        "linux": ["DejaVu Sans Mono", "Liberation Mono", "Consolas",
                  "Menlo"],
    }.get(sys.platform, ["Consolas", "DejaVu Sans Mono", "Menlo",
                         "Liberation Mono"])
    font.setFamilies(families + [system.family()])
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font


class OutputPane(QPlainTextEdit):
    """Where every child process's output lands, newest at the bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setToolTip(wrapped(
            "Output from everything the launcher runs - the"
                        " overlay, the tools, the test suite."))
        self.setFont(fixed_width_font())
        # QPlainTextEdit drops the oldest block (line) beyond this count, so
        # the pane cannot grow without bound during a long session.
        self.setMaximumBlockCount(OUTPUT_SCROLLBACK_LINES)

    def append_line(self, text):
        self.appendPlainText(text)


def _as_list(findings):
    """Findings as an HTML list for a message box.

    Qt's message boxes render rich text, and one paragraph holding four
    problems runs together into something nobody reads to the end.

    Styled, because a bare `<ul>` in a Qt message box is not the neutral
    thing it looks like. Two defaults stack up and both were reported as
    excess whitespace (issue #11): the list takes a top margin of its own,
    which lands under whatever `<br>` the caller already wrote, and Qt's
    default list indent pushes the bullets so far right that the dialog
    sizes itself around text starting a third of the way across it.
    `-qt-list-indent` is Qt's own property for the second.
    """
    items = "".join(f"<li>{finding.message}</li>" for finding in findings)
    return (LIST_STYLE + items + "</ul>")


class BuildPicker(QGroupBox):
    """The build-order library as a drop-down, metadata on each entry.

    The drop-down shows the human name from inside each JSON; the value
    carried on each entry is the file stem, which is what --build takes.

    Adding a build used to be an instruction rather than a feature: find a
    folder that Loom never created, make it yourself, drop a file in,
    restart. Import does the whole of that, and checks the file first -
    see loom/buildcheck.py for why the checking happens here of all places.
    """

    # One line for the launcher's output pane. The picker does its own
    # dialogs, but what it did should also be in the log with everything
    # else that happened this session.
    note = pyqtSignal(str)

    # A different build is now chosen. The launcher's preview follows this
    # rather than reaching into the widget, so how the picker shows its
    # library stays the picker's business.
    selection_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Build order", parent)
        # Created, not just named. Nothing in Loom ever made this folder
        # before, so the docs told players to make it by hand - work the
        # program should have been doing for them.
        self.builds_dir = paths.user_asset_dir("builds")

        # A list rather than a drop-down. Typing into a search box whose
        # results are hidden behind a click is not searching - the player
        # types, sees nothing change, and has to open the list to find out
        # whether it worked. The list is always open, so every keystroke
        # shows its own answer and the build wanted is one click away.
        self.list = QListWidget()
        self.list.setToolTip(wrapped(
            "Which build order the overlay and the preview follow.\n"
            "Import build adds one; they are kept in\n"
            f"{self.builds_dir}"))
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(LIBRARY_ROWS * LIBRARY_ROW_HEIGHT)

        # Narrowing the library, not choosing from it. Neither of these is
        # remembered between sessions: a filter silently restored next time
        # is a library that looks half empty for no visible reason.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search builds…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(wrapped(
            "Matches the name, civilization and author. Every word has to "
            "match, so \"hera arena\" narrows to one build."))
        self.search.textChanged.connect(self._apply_filter)

        self.civ_filter = QComboBox()
        self.civ_filter.setToolTip(wrapped(
            "Show the builds you could play as one civilization.\n"
            "Generic builds are included, because they work for every civ."))
        self.civ_filter.currentIndexChanged.connect(self._apply_filter)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: gray;")

        finder = QHBoxLayout()
        finder.addWidget(self.search, stretch=2)
        finder.addWidget(self.civ_filter, stretch=1)
        finder.addWidget(self.count_label)

        self.import_button = QPushButton("Import build…")
        self.import_button.setToolTip(wrapped(
            "Add a build order from an RTS Overlay JSON file. Loom checks it "
            "first and says what it finds."))
        self.import_button.clicked.connect(self._import)

        self.open_button = QPushButton("Open builds folder")
        self.open_button.setToolTip(wrapped(f"Open {self.builds_dir}"))
        self.open_button.clicked.connect(self._open_folder)

        buttons = QHBoxLayout()
        buttons.addWidget(self.import_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(finder)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

        self.problems = self._populate()
        # Whenever the choice changes, remember it - so the next session and
        # the bare command line default to the same build. Connected after
        # populating, so restoring the saved choice does not re-save it.
        self.list.currentItemChanged.connect(self._chosen)
        # Enter in the search box takes the first result, so a search that
        # has already found the build does not need the mouse at all.
        self.search.returnPressed.connect(self._take_first_result)

    def _populate(self):
        """Read the library from disk, then show it through the filter."""
        builds, problems = available_builds()
        # Keep every loaded build, filtered or not: the preview panel looks
        # them up by stem, and reloading files just read would be pointless.
        self._library = builds
        self._builds = dict(builds)
        self._fill_civilizations()
        self._render()
        return problems

    def _render(self):
        """Rebuild the drop-down from the library and the current filter."""
        # The choice that must survive: what is in the box now, or failing
        # that what was saved. filtered_builds keeps it whatever the filter
        # says, so narrowing the list can never move the selection onto a
        # build the player did not pick - Start would then run it.
        chosen = self.selected_row() or config.overlay_mode()
        if chosen == config.BUILD_MODE:
            chosen = config.active_build()
        matched = filtered_builds(self._library,
                                  query=self.search.text(),
                                  civilization=self.civ_filter.currentData())
        shown = filtered_builds(self._library,
                                query=self.search.text(),
                                civilization=self.civ_filter.currentData(),
                                keep=chosen)
        # Which row is only there because it is the current choice. Marked
        # rather than left to puzzle over: a build that does not match what
        # you typed, sitting in the list with no explanation, reads as a
        # broken search rather than as the safety rule it is.
        kept = {stem for stem, _build in shown} - {s for s, _b in matched}

        # Silenced while rebuilding: clearing the list would otherwise save
        # an empty choice on the way past, and this runs on every keystroke.
        blocked = self.list.blockSignals(True)
        self.list.clear()
        # The two build-free modes, pinned above the library. Added OUTSIDE
        # filtered_builds rather than passed through it: they are modes, not
        # builds, and a search box that could hide them would let Start run
        # something the player never picked - the same failure the `keep`
        # rule exists to prevent, arriving by a different door.
        for mode, label in TRACKING_ROWS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mode)
            font = item.font()
            font.setItalic(True)      # reads as a different kind of thing
            item.setFont(font)
            self.list.addItem(item)
        for stem, build in shown:
            label = (f"{build.name} — {civilization_label(build)}"
                     f" — {build.author or 'unknown'}"
                     f" — {len(build.steps)} steps")
            if stem in kept:
                label += "   · your current choice"
            item = QListWidgetItem(label)
            # The stem rides along invisibly - it is what --build takes.
            item.setData(Qt.ItemDataRole.UserRole, stem)
            self.list.addItem(item)
        # Falls back to the first BUILD when the chosen build's file has
        # since been deleted, rather than leaving nothing selected - and
        # deliberately not to row 0, which is now a tracking mode. Landing
        # there would silently switch a player whose build file went missing
        # into a mode that draws no card, which looks exactly like Loom
        # broken rather than like a build that disappeared.
        row = self._row_of(chosen)
        self.list.setCurrentRow(row if row >= 0 else self._first_build_row())
        self.list.scrollToItem(self.list.currentItem())
        self.list.blockSignals(blocked)

        self._show_count(matched)

    def _show_count(self, shown):
        """Say how much of the library is on show, and why it is that many.

        The generic tally is what makes including Generic builds under a
        specific civilization honest rather than surprising: without it,
        asking for Mongols and getting eight builds reads like a filter that
        is not working.
        """
        total = len(self._library)
        if len(shown) >= total:
            self.count_label.setText(f"{total} builds")
            return

        if not shown:
            return self.count_label.setText(f"no matches in {total} builds")

        text = f"{len(shown)} of {total}"
        if self.civ_filter.currentData():
            generic = sum(1 for _stem, build in shown
                          if GENERIC_CIVILIZATION in civilization_names(build))
            if generic:
                text += f" ({generic} generic)"
        self.count_label.setText(text)

    def _fill_civilizations(self):
        """The civilization drop-down, from the civs the library actually
        holds - never a list of the game's civs, which would offer forty
        entries with nothing behind most of them."""
        wanted = self.civ_filter.currentData()
        blocked = self.civ_filter.blockSignals(True)
        self.civ_filter.clear()
        self.civ_filter.addItem("All civilizations", None)
        for name in civilizations(self._library):
            self.civ_filter.addItem(name, name)
        index = self.civ_filter.findData(wanted)
        self.civ_filter.setCurrentIndex(max(index, 0))
        self.civ_filter.blockSignals(blocked)

    def _apply_filter(self, *_args):
        self._render()

    def clear_filters(self):
        """Show the whole library again, without saving anything."""
        blocked = self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(blocked)
        blocked = self.civ_filter.blockSignals(True)
        self.civ_filter.setCurrentIndex(0)
        self.civ_filter.blockSignals(blocked)
        self._render()

    def _row_of(self, stem):
        """Which row carries this build, or -1."""
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == stem:
                return row
        return -1

    def _chosen(self, *_args):
        """A row was picked: remember it, and tell the launcher.

        The mode and the build are remembered SEPARATELY, so picking a
        tracking mode does not overwrite which build you were on. Coming
        back to build mode then returns you to it rather than to whatever
        the default happens to be.
        """
        config.set_overlay_mode(self.selected_mode())
        stem = self.selected_stem()
        if stem is not None:
            config.set_active_build(stem)
        # Re-draw, because a row kept only for being the current choice has
        # just stopped being one. Left alone it would sit there still
        # labelled "your current choice" while the highlight is plainly on
        # another build - two answers to "what is selected?", which is worse
        # than the hidden selection the label was protecting against.
        #
        # Deferred by a zero-length timer rather than called outright: this
        # runs inside currentItemChanged, whose arguments are the very items
        # a re-draw deletes. Letting Qt finish delivering the signal first
        # keeps that safe.
        QTimer.singleShot(0, self._render)
        self.selection_changed.emit()

    def _take_first_result(self):
        """Enter in the search box picks the top matching BUILD.

        Not row 0, which is a tracking mode: someone who has typed a search
        and pressed Enter is looking for a build, and handing them a mode
        that draws no card would read as the search having failed strangely.
        """
        if self.list.count():
            self.list.setCurrentRow(self._first_build_row())

    # ---- adding one ----------------------------------------------------

    def _can_draw(self, token):
        """Does Loom have a picture for this icon token? The overlay's own
        answer, so the dialog cannot promise pictures it will not draw."""
        return overlay.find_icon_file(token) is not None

    def _import(self):
        """Check a build order file, then copy it into the library."""
        filename, _filter = QFileDialog.getOpenFileName(
            self, "Import a build order", "",
            "Build orders (*.json);;All files (*)")
        if not filename:
            return

        source = Path(filename)
        build, findings = buildcheck.inspect(source, self._can_draw)

        refusals = buildcheck.fatal(findings)
        if refusals:
            self.note.emit(f"[builds] {source.name} was not imported")
            QMessageBox.critical(
                self, "Loom cannot use that file",
                f"<b>{source.name}</b> was not imported.<br>"
                + _as_list(refusals))
            return

        cautions = buildcheck.warnings(findings)
        if cautions:
            # Warnings are said, not enforced. Everything reachable here
            # loads and runs; the player is the one who knows whether the
            # oddity was deliberate.
            answer = QMessageBox.warning(
                self, "Import this build?",
                f"<b>{buildcheck.describe(build)}</b><br><br>"
                "Loom can use this build. Worth knowing first:<br>"
                + _as_list(cautions),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if answer != QMessageBox.StandardButton.Yes:
                self.note.emit(f"[builds] {source.name} was not imported")
                return

        destination = self.builds_dir / source.name
        if destination.exists():
            answer = QMessageBox.question(
                self, "Replace that build?",
                f"<b>{destination.name}</b> is already in your builds "
                "folder.<br><br>Replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return

        try:
            shutil.copy2(source, destination)
        except OSError as problem:
            QMessageBox.critical(
                self, "The build could not be saved",
                f"Loom could not write to<br><b>{self.builds_dir}</b>"
                f"<br><br>{problem}")
            self.note.emit(f"[builds] could not save {source.name}: {problem}")
            return

        # Listed and selected immediately: an import that needed a restart
        # to show up would leave the player wondering whether it worked. The
        # filters go with it, for the same reason - a build hidden behind a
        # search the player forgot was on is the confusion Import exists to
        # remove, wearing a different hat.
        self.clear_filters()
        for problem in self.refresh(select=destination.stem):
            self.note.emit(f"[builds] {problem}")
        self.note.emit(f"[builds] imported {destination.name}")
        QMessageBox.information(
            self, "Build imported",
            f"<b>{buildcheck.describe(build)}</b><br><br>"
            "It is selected now, and will be there next time.")

    def _open_folder(self):
        """Show the builds folder in the system's file manager."""
        # Qt's own opener rather than a per-OS command line: this is one of
        # the few places a wrong guess would launch something unexpected.
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.builds_dir)))

    def refresh(self, select=None):
        """Re-read the library. Returns the same problems _populate does."""
        problems = self._populate()
        if select is not None:
            row = self._row_of(select)
            if row >= 0:
                self.list.setCurrentRow(row)
        return problems

    def selected_row(self):
        """What the highlighted row carries: a build stem, or a mode.

        The raw value. Everything else here narrows it - which is the point,
        because the two are not interchangeable and a caller that treated a
        mode as a stem would hand it to BuildOrder.load_by_name.
        """
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_mode(self):
        """Which of the three things the overlay should be. Never None."""
        row = self.selected_row()
        return row if row in config.OVERLAY_MODES else config.BUILD_MODE

    def selected_stem(self):
        """The chosen build's file stem.

        None when a tracking mode is picked as well as when the library is
        empty, and deliberately the same answer for both: in each case there
        is no build to run, which is what every caller of this actually
        wants to know. It is also what keeps run_dev_command's `or` fallback
        working - a mode string is truthy and would otherwise sail through
        it into --build.
        """
        row = self.selected_row()
        return None if row in config.OVERLAY_MODES else row

    def _first_build_row(self):
        """The topmost row that is a real build, or 0 if there are none.

        0 only when the library is empty, and then it is the honest answer:
        the tracking modes are the only things Loom can run.
        """
        for row in range(self.list.count()):
            data = self.list.item(row).data(Qt.ItemDataRole.UserRole)
            if data not in config.OVERLAY_MODES:
                return row
        return 0

    def selected_build(self):
        """The chosen build, already loaded. None if the library is empty."""
        return self._builds.get(self.selected_stem())


def _stacked(widgets):
    """Several widgets in one, for a tab that holds more than one box."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    # Keeps the boxes at their natural height instead of stretching the last
    # one down the tab.
    layout.addStretch()
    return holder


def _next_launch_hint():
    """The one non-obvious fact about the alert settings: a running overlay
    keeps the ones it started with."""
    hint = QLabel("Changes apply the next time the overlay starts.")
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")
    return hint


def _live_hint():
    """For the boxes a running overlay follows immediately.

    Its own label rather than an argument to the one above, because the two
    say opposite things and a box that got the wrong one would be worse than
    a box with no hint at all - the whole reason this exists is that players
    read "next time the overlay starts" and concluded Loom was broken.
    """
    hint = QLabel("These apply immediately, including while the overlay"
                  " is running.")
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")
    return hint


def _announcing_setter(box, save, scale=1):
    """A settings setter that writes the file, then says so.

    Shared by the two Appearance boxes. Config FIRST, then the signal, so a
    listener that re-reads config - which is exactly what the overlay does -
    can never read the old value. Same order as the preview's own switches.
    """
    def apply(value):
        save(value / scale if scale != 1 else value)
        box.changed.emit()
    return apply


class AlertSettingsBox(QGroupBox):
    """The alert thresholds and switches, written to config as they change.

    No Apply button: these are set-once-and-stay-put settings, and the config
    file is the source of truth. The hint label carries the one thing that
    is not obvious - a running overlay keeps the settings it started with.
    """

    def __init__(self, parent=None):
        super().__init__("Alerts", parent)
        soften, silence = config.idle_tc_limits()

        # The idle-TC taper: full alert below soften, calm up to silence,
        # nothing above. Range 0-200 because that is the standard pop cap.
        self.soften = QSpinBox()
        self.soften.setRange(0, 200)
        self.soften.setValue(soften)
        self.soften.setToolTip(wrapped(
            "Below this many villagers the idle-TC alert is loud and red."))
        self.silence = QSpinBox()
        self.silence.setRange(0, 200)
        self.silence.setValue(silence)
        self.silence.setToolTip(wrapped(
            "At this many villagers the idle-TC alert stops entirely."))
        self.soften.valueChanged.connect(self._save_limits)
        self.silence.valueChanged.connect(self._save_limits)

        # Wrapping, like every other row of controls in this window: a
        # sentence with spinboxes in it is the widest thing in the Alerts box
        # and reported a 792px minimum, which the launcher cannot honour.
        taper = flow_row([QLabel("Idle-TC alert softens at"), self.soften,
                           QLabel("villagers, silences at"), self.silence])

        # The pre-emptive HOUSE SOON threshold: how much pop space remaining
        # should raise the warning. A boom eats more per house than a
        # one-TC opening, so the right number is the player's to pick.
        self.headroom = QSpinBox()
        self.headroom.setRange(*config.HOUSE_HEADROOM_BOUNDS)
        self.headroom.setValue(config.house_headroom())
        self.headroom.setToolTip(wrapped(
            "Warn HOUSE SOON when this little population space is left -"
            " raise it if you keep getting housed anyway."))
        self.headroom.valueChanged.connect(config.set_house_headroom)

        house = flow_row([QLabel("HOUSE SOON warns at"), self.headroom,
                           QLabel("pop space left")])

        toggles = config.alert_toggles()
        self.checkboxes = {}
        labels = [
            ("idle_tc", "TC idle warning",
             "Alert when a Town Center is sitting idle - the most expensive"
             " routine mistake in the game."),
            ("housed", "Housed alert",
             "Alert when production has actually stalled against the pop"
             " cap."),
            ("house_warning", "Pre-emptive HOUSE SOON warning",
             "Alert just BEFORE hitting the pop cap, while a house can"
             " still prevent the stall."),
        ]
        made = []
        for name, text, tip in labels:
            box = QCheckBox(text)
            box.setChecked(toggles[name])
            box.setToolTip(wrapped(tip))
            # The lambda needs name=name: without it, every lambda would
            # close over the same loop variable and toggle "house_warning".
            box.toggled.connect(
                lambda checked, name=name:
                config.set_alert_toggle(name, checked))
            self.checkboxes[name] = box
            made.append(box)
        boxes = flow_row(made)

        layout = QVBoxLayout(self)
        layout.addWidget(taper)
        layout.addWidget(house)
        layout.addWidget(boxes)
        layout.addWidget(_next_launch_hint())

    def _save_limits(self):
        config.set_idle_tc_limits(self.soften.value(), self.silence.value())


class OverlaySizeBox(QGroupBox):
    """The overlay's two size knobs, as percentages.

    Applies to a running overlay, live - see LauncherWindow._apply_overlay_
    appearance. Growing the panel moves it too, because its default spot is
    right-aligned against the game window.

    Stored as float multipliers in config (1.25, not 125) because the
    multiplier is the semantic value - percent is just the friendlier face
    for a spinbox. Overall size grows the whole panel, writing included;
    text size grows only the writing and the panel's height, never its
    width, so a bigger font never widens the overlay's footprint on the
    game.

    Size ONLY. Transparency lives in its own box below - the beta feedback
    was that transparency controls sitting beside size controls read as
    more size controls, and a separate titled group is what actually
    removes that ambiguity.
    """

    # Something changed; the launcher relays it to a running overlay.
    changed = pyqtSignal()

    def _setter(self, save, scale=1):
        return _announcing_setter(self, save, scale)

    def __init__(self, parent=None):
        super().__init__("Overlay size", parent)

        self.overall = QSpinBox()
        self.overall.setRange(round(config.OVERLAY_SCALE_BOUNDS[0] * 100),
                              round(config.OVERLAY_SCALE_BOUNDS[1] * 100))
        self.overall.setSingleStep(5)
        self.overall.setSuffix(" %")
        self.overall.setValue(round(config.overlay_scale() * 100))
        self.overall.setToolTip(wrapped(
            "Grow the whole overlay panel - geometry, writing and icons"
            " together."))
        self.overall.valueChanged.connect(
            self._setter(config.set_overlay_scale, scale=100))

        self.text = QSpinBox()
        self.text.setRange(round(config.TEXT_SCALE_BOUNDS[0] * 100),
                           round(config.TEXT_SCALE_BOUNDS[1] * 100))
        self.text.setSingleStep(5)
        self.text.setSuffix(" %")
        self.text.setValue(round(config.text_scale() * 100))
        self.text.setToolTip(wrapped(
            "Grow only the overlay's writing. The panel gets taller to fit"
            " it, but never wider."))
        self.text.valueChanged.connect(
            self._setter(config.set_text_scale, scale=100))

        row = QHBoxLayout()
        row.addWidget(QLabel("Overall size"))
        row.addWidget(self.overall)
        row.addWidget(QLabel("Text size"))
        row.addWidget(self.text)
        row.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(_live_hint())


def _slider_row(layout, caption, scale_hint, value, setter, tip):
    """One captioned slider with a live percent label beside it.

    A QSlider cannot display its own value the way a spinbox shows a
    suffix, so the label does it - updated on every change, including
    mid-drag, which is half the point of a slider.

    Module-level because two boxes build their rows from it: the overlay's
    transparency box and the preview's appearance box. Two hand-rolled
    copies is how the two windows' settings drift into looking unrelated.
    """
    slider = QSlider(Qt.Orientation.Horizontal)
    slider.setRange(0, 100)
    slider.setPageStep(10)
    slider.setValue(round(value * 100))
    slider.setToolTip(wrapped(tip))

    percent = QLabel(f"{slider.value()} %")
    percent.setMinimumWidth(40)

    def changed(new_value):
        percent.setText(f"{new_value} %")
        setter(new_value / 100)
    slider.valueChanged.connect(changed)

    caption_label = QLabel(caption)
    # Wrapped rather than pinned to a width. A wrapped label's minimum is
    # its longest WORD, not its longest line, and that is what lets the
    # settings column shrink instead of forcing a sideways scroll.
    caption_label.setWordWrap(True)
    caption_label.setToolTip(wrapped(tip))
    hint = QLabel(scale_hint)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")

    row = QHBoxLayout()
    row.addWidget(caption_label)
    row.addWidget(slider, stretch=1)
    row.addWidget(percent)
    row.addWidget(hint)
    layout.addLayout(row)
    return slider


class OverlayTransparencyBox(QGroupBox):
    """The overlay's two transparency sliders, in their own titled group.

    Applies to a running overlay, live: drag one with the panel on screen
    and watch it follow. Transparency is the cheapest of these to change -
    both values are read fresh on every repaint - which is why players
    dragging this slider and seeing nothing happen was the complaint that
    started the whole live-settings idea.

    Sliders rather than spinboxes, and a separate box rather than a row in
    the size box - both straight from beta feedback: the spinboxes read as
    more size controls, and transparency wants to be dragged and eyeballed,
    not typed.

    The two do different jobs. Background is TRUE opacity of the dark card:
    0% none, 100% solid enough to hide the game behind it. Text is
    VISIBILITY on a scale whose midpoint is the designed look: below 50% the
    writing fades toward invisible, above it the colours climb toward full
    contrast - the finding being that with the card thinned, the designed
    greys are unreadable over bright terrain, and the useful direction is
    up. Alert bands follow neither; they are alarms.
    """

    # Something changed; the launcher relays it to a running overlay.
    changed = pyqtSignal()

    def _setter(self, save, scale=1):
        return _announcing_setter(self, save, scale)

    def __init__(self, parent=None):
        super().__init__("Overlay transparency", parent)

        layout = QVBoxLayout(self)
        self.background = _slider_row(
            layout, "Background", "0% invisible / 100% solid",
            config.background_opacity(),
            self._setter(config.set_background_opacity),
            "How solid the overlay's dark card is. At 0% there is no card at"
            " all; at 100% the game cannot be seen through it. 80% is the"
            " designed look.")
        self.text = _slider_row(
            # One ampersand, not two. A doubled one is how you escape a
            # mnemonic in a QPushButton or a menu; a QLabel with no buddy does
            # no mnemonic handling at all, so it renders exactly what it is
            # given and the player reads "Text && icons". about.py and the
            # README have always spelled it with one - this line was the only
            # place that did not.
            layout, "Text & icons", "50% normal / 100% bright & bold",
            config.text_visibility(),
            self._setter(config.set_text_visibility),
            "How visible the overlay's writing is. 50% is the designed look;"
            " lower fades it out, higher makes it solid and brighter for"
            " reading over bright terrain. Alert bands always stay at full"
            " strength - they are alarms.")
        layout.addWidget(_live_hint())


class PreviewAppearanceBox(QGroupBox):
    """The build preview's appearance: its ground, its cards, its text.

    Its own tab rather than more rows under Appearance, because the two
    windows sit over different things - the overlay over the game, the
    preview over the desktop - and a knob that is right against terrain says
    nothing about what is right against a wallpaper.

    Applies IMMEDIATELY, like the Appearance tab - but by a different
    route, and the difference is the whole reason this was easy and that
    was not. The preview is a widget in the launcher's own process, so
    `changed` reaches it directly. The overlay is a separate process and
    has to be told down its stdin pipe. Only the Alerts tab still waits
    for a restart.
    """

    # Something changed; the launcher relays this to the open preview.
    # The same in-process live-apply the hotkeys use - see bindings_changed.
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Preview window", parent)

        layout = QVBoxLayout(self)
        self.rest = _slider_row(
            layout, "Background at rest", "0% cards on the desktop",
            config.preview_rest_opacity(),
            self._setter(config.set_preview_rest_opacity),
            "How much ground stays under the cards while the pointer is"
            " away. At 0% the cards float straight on the desktop; at 100%"
            " the ground never fades at all.")
        self.hover = _slider_row(
            layout, "Background when hovered", "100% solid",
            config.preview_hover_opacity(),
            self._setter(config.set_preview_hover_opacity),
            "How solid the ground becomes when the pointer is on the"
            " window. The controls always come back regardless - this is"
            " only about the ground they sit on.")
        self.cards = _slider_row(
            layout, "Card opacity", "92% is the designed look",
            config.preview_card_opacity(),
            self._setter(config.set_preview_card_opacity),
            "How solid the step cards are. Text stays at full strength;"
            " this fades only the dark card behind it.")

        self.text = QSpinBox()
        self.text.setRange(round(config.PREVIEW_TEXT_SCALE_BOUNDS[0] * 100),
                           round(config.PREVIEW_TEXT_SCALE_BOUNDS[1] * 100))
        self.text.setSingleStep(5)
        self.text.setSuffix(" %")
        self.text.setValue(round(config.preview_text_scale() * 100))
        self.text.setToolTip(wrapped(
            "Grow only the cards' writing. A card gets taller to fit it,"
            " but never wider."))
        self.text.valueChanged.connect(
            self._setter(config.set_preview_text_scale, scale=100))

        row = QHBoxLayout()
        row.addWidget(QLabel("Card text size"))
        row.addWidget(self.text)
        row.addStretch()
        layout.addLayout(row)

        hint = QLabel("These apply immediately, including while the"
                      " preview is open.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout.addWidget(hint)

    def _setter(self, save, scale=1):
        """Write the config first, then announce - the same order the
        preview's own checkboxes use, so a listener that re-reads config
        always reads the new value."""
        def apply(value):
            save(value / scale if scale != 1 else value)
            self.changed.emit()
        return apply


class KeyCaptureField(QLineEdit):
    """A binding field that listens for a key press instead of being typed in.

    Click it and press the combination. The keys the player presses are the
    keys Loom registers, so there is no spelling step in between for anyone
    to get wrong, and no run of half-typed settings written to disk on the
    way to a good one.

    THE UNMODIFIED KEYS ARE FREE, and that falls out of a rule that was
    already there. keyspec refuses a binding with no modifier, because a
    global hotkey is taken from the game and a bare "Q" would stop working
    in Age of Empires while Loom ran. So no unmodified press can ever BE a
    binding, which leaves the whole of that keyspace available here:

        Esc                cancel, keep what was there
        Delete/Backspace   clear it, switching the action off
        Ctrl+Shift+Esc     binds Escape, like any other key

    Nothing is taken away - all three keys stay bindable with a modifier.

    It also turns that rule from an error into a state. Holding Ctrl+Shift
    shows "Ctrl+Shift+..." and waits, rather than accepting a bare key and
    then explaining why it was no good.
    """

    # The launcher holds its own start/stop key registered globally, and a
    # registered hotkey is SWALLOWED - Windows hands it to the registering
    # window as WM_HOTKEY and the focused widget never sees the keystrokes.
    # Without dropping that grab first, pressing the current start/stop
    # combination into a field would capture nothing AND start the overlay.
    capture_started = pyqtSignal()
    capture_ended = pyqtSignal()

    # A completed capture: the canonical binding text, or "" to switch off.
    captured = pyqtSignal(str)

    # Something the press could not be. Not an error state on the settings
    # as a whole - the field is still waiting - so it says so and carries on.
    refused = pyqtSignal(str)

    PROMPT = "press a combination..."

    # Class-level defaults, not just instance ones, and this is load-bearing:
    # QLineEdit's own constructor delivers events, which reach the event()
    # override below BEFORE __init__ has run its own body. Reading an unset
    # attribute there raises inside a Qt handler, which aborts the process
    # with no traceback rather than propagating - the widget simply never
    # finished being built.
    _capturing = False
    _settled = ""

    def __init__(self, binding, parent=None):
        super().__init__(binding, parent)
        # Read-only to the keyboard's text, not to key events: keyPressEvent
        # still runs, which is the whole trick. Typing cannot put a
        # half-spelled binding in here any more.
        self.setReadOnly(True)
        self.setPlaceholderText("(no key)")
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._capturing = False
        self._settled = binding

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._capturing = True
        self._settled = self.text()
        self.setText("")
        self.setPlaceholderText(self.PROMPT)
        self.capture_started.emit()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self._finish(self._settled)

    def accept(self, binding):
        """The box took this capture: settle on it and leave capture mode.

        Separate from `captured` because the field cannot know whether the
        binding will be taken - only the box can see the other four actions,
        and a clash is refused. So the field offers, the box decides, and a
        refused press leaves the field still listening.
        """
        self._finish(binding)

    def cancel_capture(self):
        """Stop listening, keeping whatever was already settled.

        For the box locking itself while the overlay runs. Disabling a
        widget does NOT give it a focus-out, so a field armed at that
        moment would sit in capture forever - and, worse, `capture_ended`
        would never fire, so the launcher would never take back the
        global registration it handed over when capture began. One key
        silently dead for the rest of the session.

        Same landing as pressing Esc, which is the honest one: a capture
        interrupted by something else is not an edit the player made.
        """
        if self._capturing:
            self._finish(self._settled)

    def _finish(self, binding):
        """Leave capture showing `binding`, and hand the grabs back."""
        self._capturing = False
        self._settled = binding
        self.setText(binding)
        self.setPlaceholderText("(no key)")
        if self.hasFocus():
            self.clearFocus()
        self.capture_ended.emit()

    def event(self, event):
        """Take Tab before Qt spends it on focus.

        Qt consumes Tab for focus navigation before keyPressEvent is ever
        reached, and Tab is in keyspec.KEYS - so without this, Ctrl+Shift+Tab
        is a binding the grammar accepts and the settings window cannot
        capture.
        """
        if (self._capturing and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)):
            self.keyPressEvent(event)
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key, modifiers = event.key(), event.modifiers()
        bare = modifiers == Qt.KeyboardModifier.NoModifier

        # The verbs, which only exist because an unmodified press can never
        # be a binding. See the class docstring.
        if bare and key == Qt.Key.Key_Escape:
            self._finish(self._settled)
            return
        if bare and key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.captured.emit("")
            return

        if qtkeys.is_modifier_only(key):
            # Show the combination gathering rather than ignoring the press.
            self.setText(qtkeys.describe(modifiers, key))
            return

        # nativeVirtualKey is handed over because Shift changes what Qt
        # CALLS a key - Ctrl+Shift+9 arrives as Key_ParenLeft - and the
        # native code is the one that does not move. See qtkeys.
        binding = qtkeys.binding_for(key, modifiers,
                                     native=event.nativeVirtualKey())
        if binding is None:
            self.refused.emit(
                "That key cannot be bound - Loom has no name for it on both"
                " Windows and Linux, so a binding using it would save and"
                " then never fire.")
            return

        trouble = keyspec.problem(binding)
        if trouble:
            # Chiefly the no-modifier rule, whose message explains what
            # binding a bare key would cost the player in-game.
            self.setText(binding)
            self.refused.emit(trouble)
            return

        self.captured.emit(binding)


class HotkeysBox(QGroupBox):
    """The build-order hotkeys, and how long a nudge holds sync off.

    Every binding is editable and every one can be emptied, and that is not
    politeness. A hotkey Loom registers is TAKEN FROM THE GAME - while Loom
    holds Ctrl+Shift+W, Age of Empires never sees it - and AoE2 players remap
    heavily, so a binding somebody cannot change is a binding that breaks
    their game.

    Bindings are validated as they are typed rather than on save, because the
    alternative is finding out at the next overlay launch that a key does
    nothing, which looks exactly like the feature being broken.
    """

    LABELS = {
        "previous_step": ("Previous step",
                          "Step the overlay back one step in the build."),
        "next_step": ("Next step",
                      "Step the overlay forward one step in the build."),
        "toggle_follow": ("Stop / resume following",
                          "Stop the overlay following the game, or start it"
                          " again. Unlike the two step keys this does not"
                          " time out - the panel says MANUAL until you press"
                          " it again."),
        "toggle_hidden": ("Hide / show the panel",
                          "Take the overlay off the screen without stopping"
                          " it, and bring it back. Loom keeps reading the"
                          " game, recording the match and counting APM the"
                          " whole time - only the window goes away. The"
                          " launcher's Hide button does the same thing."),
        "start_stop_overlay": (
            "Start / stop overlay",
            "One key that does what the Start and Stop buttons do, so the"
            " overlay can be launched mid-game without alt-tabbing out."
            " Registered by the launcher itself, so it works while the game"
            " has focus - and unlike the keys above, changing it applies"
            " immediately. Empty by default: bind it here to switch it on."),
    }

    # Emitted whenever any binding or the master switch changes, so the
    # launcher can re-register its own hotkey live - the settings and that
    # listener share this process, which is what makes "applies immediately"
    # possible for the launcher's key where the overlay's read-once contract
    # makes it impossible for the others.
    bindings_changed = pyqtSignal()

    # A field is listening for a key press, so the launcher must hand its own
    # global registrations back for the moment - otherwise the one
    # combination the player is most likely to re-press is the one the
    # capture cannot see.
    capture_started = pyqtSignal()
    capture_ended = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Build-order hotkeys", parent)

        self.enabled = QCheckBox("Use hotkeys")
        self.enabled.setChecked(config.hotkeys_enabled())
        self.enabled.setToolTip(wrapped(
            "Register these key combinations system-wide. While Loom holds"
            " them, the game does not see them - so switch this off to hand"
            " them all back at once."))
        self.enabled.toggled.connect(config.set_hotkeys_enabled)
        self.enabled.toggled.connect(
            lambda _checked: self.bindings_changed.emit())

        bindings = config.hotkeys()
        self.fields = {}
        # One per action, shown only while that row differs from the
        # shipped default - see the comment where they are built.
        self.resets = {}
        # Built before the rows, because each field reports a refused press
        # straight into it.
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: rgb(235, 190, 90);")
        # A grid, so the captions share one column and the fields share
        # another. They were a stack of separate rows, which meant every
        # field started wherever its own caption happened to end and no two
        # were the same width - fine while the captions were pinned to a
        # fixed 150px, ragged the moment that was dropped so the settings
        # column could shrink. A grid aligns them by construction, and still
        # lets the captions wrap when the window is narrow.
        rows = QGridLayout()
        rows.setColumnStretch(1, 1)
        for line, action in enumerate(config.HOTKEY_ACTIONS):
            label, tip = self.LABELS[action]
            # Right-aligned inside the field: the bindings share a
            # "Ctrl+Shift+" prefix and differ in the last character, so
            # ending them at the same place puts the part that actually
            # varies in one column.
            field = KeyCaptureField(bindings[action])
            field.setToolTip(wrapped(
                f"{tip} Click here and press the combination you want."
                f" Delete clears it, switching this action off and giving"
                f" the keys back to the game; Esc leaves it alone."))
            # name=action for the same reason the alert checkboxes need it:
            # without it every lambda closes over the last loop variable.
            field.captured.connect(
                lambda binding, name=action: self._captured(name, binding))
            field.refused.connect(self.warning.setText)
            # The launcher's own key is registered globally and would be
            # swallowed before a focused widget could see it - see
            # KeyCaptureField. Dropped while capturing, taken back after.
            field.capture_started.connect(self.capture_started)
            field.capture_ended.connect(self.capture_ended)
            self.fields[action] = field

            # The way back, for a binding that cannot be pressed back in.
            # Measured, and it is why this exists rather than being tidiness:
            # Ctrl+Shift+0 shipped as a default, registered perfectly, and
            # produced NO Qt key event at all when typed - so a player who
            # cleared it could never restore it from this window however
            # correct every line of the capture path was. Any key the OS
            # swallows has the same shape, Loom cannot detect it, and an
            # unbind was one-way whenever it happened.
            reset = QToolButton()
            reset.setText("↺")
            reset.setAutoRaise(True)
            reset.setToolTip(wrapped(
                f"Put this back to {config.DEFAULT_HOTKEYS[action]}, the"
                " combination Loom ships with. It writes the binding"
                " directly, so it works even for a key this window cannot"
                " capture."))
            reset.clicked.connect(
                lambda _checked=False, name=action: self._restore(name))
            self.resets[action] = reset

            caption = QLabel(label)
            rows.addWidget(caption, line, 0)
            rows.addWidget(field, line, 1)
            rows.addWidget(reset, line, 2)
        # Reserved whether or not a button is in it, so rows do not shift
        # sideways as bindings are changed and the buttons come and go.
        rows.setColumnMinimumWidth(2, RESET_COLUMN_WIDTH)

        self.hold = QSpinBox()
        low, high = config.MANUAL_HOLD_BOUNDS
        self.hold.setRange(low, high)
        self.hold.setSuffix(" s")
        self.hold.setValue(config.manual_hold_seconds())
        self.hold.setToolTip(wrapped(
            "How long a step key stops the overlay following the game before"
            " it picks the game back up by itself. The step keys are meant as"
            " a correction, not a mode - this is how long the correction"
            " lasts."))
        self.hold.valueChanged.connect(config.set_manual_hold_seconds)

        hold_caption = QLabel("A step key holds sync off for")
        hold_line = len(config.HOTKEY_ACTIONS)
        rows.addWidget(hold_caption, hold_line, 0)
        # Left in its column rather than stretched across it: a spinbox as
        # wide as a hotkey field would look like somewhere to type a binding.
        rows.addWidget(self.hold, hold_line, 1,
                       alignment=Qt.AlignmentFlag.AlignLeft)

        layout = QVBoxLayout(self)
        layout.addWidget(self.enabled)
        layout.addLayout(rows)
        layout.addWidget(self.warning)
        if not hotkeys.available():
            unsupported = QLabel(
                "Hotkeys are not available on this system, so the overlay"
                " will only follow the game automatically.")
            unsupported.setWordWrap(True)
            unsupported.setStyleSheet("color: gray;")
            layout.addWidget(unsupported)
        elif sys.platform == "darwin":
            # The binding text stays canonical everywhere - one spelling per
            # stored binding - so the Mac-specific fact is a note, not a
            # renamed modifier.
            command_note = QLabel("Win is the Command (⌘) key here.")
            command_note.setStyleSheet("color: gray;")
            layout.addWidget(command_note)
        # Two contracts, one per owner, stated rather than implied: the
        # overlay reads its keys once at startup; the launcher re-registers
        # its own the moment a binding changes.
        self.hint = QLabel(HOTKEY_HINT)
        self.hint.setStyleSheet("color: gray;")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self._check()

    def set_overlay_running(self, running):
        """Lock the bindings while the overlay holds them.

        Editing them mid-run gives results that look like a bug because
        they ARE half-applied, and the box has always said so in small
        grey text at the bottom: the overlay reads its keys ONCE at
        startup, while the launcher re-registers its own the moment a
        binding changes. So a step key edited mid-game silently does
        nothing until the next start, and the start/stop key changes
        under the player's fingers - two different behaviours from one
        panel, which is worse than either.

        Locking is honest where the note was merely true. Nothing here
        can be half-applied if none of it can be touched.

        The message stays at full contrast rather than greying with the
        rest, because it is the one thing on the panel that has to be
        readable at the moment everything else stops responding.
        """
        if running:
            # Before disabling anything. A disabled widget gets no
            # focus-out, so a field armed right now would stay in capture
            # and never hand the launcher's global registration back.
            for field in self.fields.values():
                field.cancel_capture()
        for widget in ([self.enabled, self.hold]
                       + list(self.fields.values())
                       + list(self.resets.values())):
            widget.setEnabled(not running)
        self.hint.setText(HOTKEY_LOCKED if running else HOTKEY_HINT)
        self.hint.setStyleSheet(
            f"color: {css_rgb(LOCKED_NOTICE)};" if running
            else "color: gray;")

    def _captured(self, action, binding):
        """One completed capture. Refused if another action owns those keys.

        Refusing rather than warning, which is the one behaviour change here:
        the clash was always detected, and the amber label has always said so.
        But a warning describes a state the player has already been put in,
        where at the moment of a keypress the fix is obvious and the press
        can simply not be taken. The field stays in capture, so the next
        press just works.

        Only a CLASH is refused. Everything else keyspec objects to was
        already refused one layer down, in the field itself.
        """
        if binding:
            wanted = {name: (binding if name == action else field.text())
                      for name, field in self.fields.items()}
            for first, second in keyspec.conflicts(wanted):
                other = second if first == action else first
                if action in (first, second):
                    self.warning.setText(
                        f"{self.LABELS[other][0]} is already on those keys."
                        f" Pick another combination, or clear that one first.")
                    return

        self.fields[action].accept(binding)
        self._save(action, binding)

    def _restore(self, action):
        """Put one binding back to what Loom ships with.

        Straight through `_captured`, deliberately, so the clash rule is
        answered in ONE place. A restored default can collide with a key
        the player has since moved onto that combination, and refusing it
        with the same message a keypress gets beats a second copy of the
        rule that could disagree with the first.

        What it does NOT go through is the capture field, which is the
        whole point: this path never asks the operating system for a
        keystroke, so it works for a combination the OS eats before Qt
        can see it.
        """
        self._captured(action, config.DEFAULT_HOTKEYS[action])

    def _show_resets(self):
        """A restore button only where there is something to restore.

        Hidden on a row already holding its default, because a control
        that would do nothing is one worth reading and then not pressing.
        The column keeps its width either way, so nothing moves as they
        come and go.
        """
        for action, button in self.resets.items():
            button.setVisible(
                self.fields[action].text() != config.DEFAULT_HOTKEYS[action])

    def _save(self, action, text):
        config.set_hotkey(action, text)
        self._check()
        self.bindings_changed.emit()

    def _check(self):
        """Say what is wrong with the current set, or nothing.

        Two failures are worth catching here rather than at launch: a
        combination that will not parse, and two actions on one combination -
        which no operating system reports, because whichever registers first
        simply wins and the other never fires.
        """
        bindings = {action: field.text()
                    for action, field in self.fields.items()}
        complaints = []
        for action in config.HOTKEY_ACTIONS:
            trouble = keyspec.problem(bindings[action])
            if trouble:
                complaints.append(f"{self.LABELS[action][0]}: {trouble}")
        for first, second in keyspec.conflicts(bindings):
            complaints.append(
                f"{self.LABELS[first][0]} and {self.LABELS[second][0]} are on"
                f" the same keys; only one of them will work.")
        self.warning.setText("\n".join(complaints))
        # Here rather than in _save: this already runs after every change
        # AND once at construction, so the buttons cannot start out of step
        # with the bindings they describe.
        self._show_resets()


# What a button says instead of its tooltip when this installation cannot
# start it. Named here so the test that pins the greying can check the reason
# is given, not just that the button is dead.
UNAVAILABLE_TIP = (
    "Not available in a packaged copy of Loom: this tool runs from the source"
    " tree and needs a Python interpreter, and a bundle has neither. Run Loom"
    " from a clone to use it.")


class DevPanel(QGroupBox):
    """The developer tools: debug launchers and the test runner.

    Only one dev task runs at a time - a shared output pane showing two
    interleaved programs is worse than useless - so every button funnels
    through the window's single dev slot.

    Half of these tools cannot run from a bundle at all: they are
    `-m tools.something` and `-m pytest`, and a frozen Loom has no source
    tree and no interpreter to hand them to. Those buttons are DISABLED here
    rather than left to fail, because failing was not a message - argv_for
    raised inside the clicked slot, PyQt6 aborted the process, and
    console=False meant the traceback went nowhere. From the outside Loom
    just disappeared. entry.can_run is the question this asks; see there.
    """

    def __init__(self, run_command, stop_task, parent=None):
        """run_command(prefix, argv) and stop_task() come from the window,
        which owns the actual process slot."""
        super().__init__("Developer tools", parent)
        self.scenario = QComboBox()
        self.scenario.addItems(COACH_SCENARIOS)
        self.scenario.setToolTip(wrapped(
            "Which synthetic match Coach simulate replays: on pace, running"
            " late, or stalling out."))

        # A wrapping row. This was a fixed three-column grid, which is the
        # same idea guessed in advance: three columns is right at one width
        # and wrong at every other. The flow layout picks the number of rows
        # from the width it is actually given.
        self.buttons = {}
        made = []
        for index, (label, prefix, build_args, tip) in enumerate(DEV_COMMANDS):
            button = QPushButton(label)
            runnable = entry.can_run(build_args("fast_castle", "perfect"))
            button.setEnabled(runnable)
            button.setToolTip(wrapped(
                tip if runnable else f"{label}: {UNAVAILABLE_TIP}"))
            # name=... defaults again, for the same closure-over-loop reason.
            button.clicked.connect(
                lambda _checked, prefix=prefix, build_args=build_args:
                run_command(prefix, build_args))
            made.append(button)
            self.buttons[label] = button
        buttons = flow_row(made)

        stop = QPushButton("Stop task")
        stop.setToolTip(
            wrapped("Terminate whichever developer task is running."))
        stop.clicked.connect(stop_task)

        row = QHBoxLayout()
        row.addWidget(QLabel("Coach scenario:"))
        row.addWidget(self.scenario)
        row.addStretch()
        row.addWidget(stop)

        layout = QVBoxLayout(self)
        layout.addWidget(buttons)
        layout.addLayout(row)


class LauncherWindow(QWidget):
    """The whole launcher: build picker, overlay controls, settings, tools.

    Owns the two process slots. The overlay slot has dedicated Start/Stop
    buttons because starting the overlay is the point of the app; everything
    else shares the one dev slot.
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Loom {loom_version}")

        # First, before any widget exists. Qt delivers a moveEvent while the
        # window is still being constructed - measured, on Windows, from
        # inside this __init__ - and the handler reaches for this timer.
        # Built last it was an AttributeError out of an event handler, which
        # is the silent-death path this release is closing: PyQt6 aborts on
        # an exception in a handler, and a packaged Loom has no console.
        # Debounced because a drag would otherwise write config.json once
        # per pixel - the same reason and the same interval as the preview
        # window, see loom/browser.py.
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._remember_geometry)

        self.overlay_process = None
        self.dev_process = None

        # The settings throttle: a single-shot that gates how often a
        # running overlay is told to re-read. _settings_pending remembers a
        # change that arrived while the gate was shut, so the last value of
        # a drag always lands even though most of the drag was dropped.
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(ANNOUNCE_SETTINGS_EVERY_MS)
        self._settings_timer.timeout.connect(self._settings_gate_opened)
        self._settings_pending = False

        self.picker = BuildPicker()
        self.settings = AlertSettingsBox()
        self.appearance = OverlaySizeBox()
        self.transparency = OverlayTransparencyBox()
        self.preview_appearance = PreviewAppearanceBox()
        self.hotkeys_box = HotkeysBox()
        self.output = OutputPane()
        self.apm_process = None
        # The APM join: buckets from the counter child, and (wall, game_t)
        # pairs from the overlay's state lines - the bridge between the
        # counter's wall clock and the game's own clock.
        self._apm_buckets = []
        self._time_pairs = []

        # Overlay controls. Place overlay sits here rather than in the dev
        # panel: repositioning the panel is an everyday act, not debugging.
        self.start_button = QPushButton("Start overlay")
        self.stop_button = QPushButton("Stop overlay")
        self.place_button = QPushButton("Place overlay")
        self.status = QLabel()
        # Go/stop colours on the two that act, the base style on all five -
        # see CONTROL_BUTTON_STYLE for why none of these may ever be bare.
        self.start_button.setStyleSheet(GO_BUTTON_STYLE)
        self.stop_button.setStyleSheet(STOP_BUTTON_STYLE)
        self.place_button.setStyleSheet(CONTROL_BUTTON_STYLE)
        self.start_button.setToolTip(wrapped(
            "Run the overlay over the game with the chosen build order."))
        self.stop_button.setToolTip(wrapped("Stop the running overlay."))
        self.place_button.setToolTip(wrapped(
            "Open a movable copy of the panel - drag it where you want the"
            " overlay, then close it to save the position."))
        self.status.setToolTip(
            wrapped("Whether the overlay is currently running."))
        self.start_button.clicked.connect(self.start_overlay)
        self.stop_button.clicked.connect(self.stop_overlay)
        self.place_button.clicked.connect(self.place_overlay)
        self.reset_place_button = QPushButton("Reset position")
        self.reset_place_button.setStyleSheet(CONTROL_BUTTON_STYLE)
        self.reset_place_button.setToolTip(wrapped(
            "Forget where the overlay was placed and go back to the default"
            " spot (top right, under the game's bar). The rescue for a"
            " position that ended up off the screen."))
        self.reset_place_button.clicked.connect(self.reset_overlay_position)
        self.hide_button = QPushButton("Hide overlay")
        self.hide_button.setStyleSheet(CONTROL_BUTTON_STYLE)
        self.hide_button.setToolTip(wrapped(
            "Take the panel off the screen without stopping it. Loom keeps"
            " reading the game, keeps recording the match and keeps counting"
            " APM - only the window goes away. The overlay's own hotkey does"
            " the same thing without alt-tabbing out here."))
        self.hide_button.clicked.connect(self.toggle_overlay_hidden)
        # A wrapping row, not a fixed one. Six controls side by side reported
        # a minimum width of 1078px - 838 of buttons plus a 240px status
        # label - against a window that can be dragged to 560, so the launcher
        # could not honour its own minimum without scrolling sideways.
        controls = flow_row([self.start_button, self.stop_button,
                              self.hide_button, self.place_button,
                              self.reset_place_button, self.status])

        # Developer mode: a persisted checkbox revealing the tools panel.
        self.dev_toggle = QCheckBox("Developer mode")
        self.dev_toggle.setToolTip(wrapped(
            "Show the debug tools: demo mode, the coach simulator, capture"
            " tools and the test runner."))
        self.dev_panel = DevPanel(self.run_dev_command, self.stop_dev_task)
        self.dev_toggle.setChecked(config.developer_mode())
        self.dev_toggle.toggled.connect(self._set_developer_mode)

        # The build preview: its own window, so the window manager is the
        # size control. The checkbox and the window's own X both hide it,
        # and stay in sync with each other. Parented to the launcher so it
        # can never open behind it.
        self.browser = BuildBrowser(self)
        self.browser_toggle = QCheckBox("Show build preview")
        self.browser_toggle.setToolTip(wrapped(
            "Show the build order in its own resizable window - browse it"
            " before a match, watch it follow along during one."))
        self.browser_toggle.setChecked(config.build_browser())
        self.browser_toggle.toggled.connect(self._set_build_browser)

        self.record_toggle = QCheckBox("Attach recorded game")
        self.record_toggle.setToolTip(wrapped(
            "When a game ends, find the match's own .aoe2record and add"
            " what it says to the statistics - who won, both civilisations,"
            " and what the game was actually told to do. Only ever after"
            " the match has ended."))
        self.record_toggle.setChecked(config.attach_recorded_game())
        self.record_toggle.toggled.connect(config.set_attach_recorded_game)
        self.browser.closed.connect(
            lambda: self.browser_toggle.setChecked(False))

        # Alerts in the preview, deliberately SEPARATE from hiding the
        # overlay. Two controls for two ideas, so a desk can be set up
        # whichever way suits it: warnings in both windows while trying it
        # out, the preview alone on a second monitor with the panel hidden,
        # or the panel hidden and the preview left as a quiet reference. One
        # combined switch would have made "hidden" and "warns me" the same
        # decision, and they are not.
        # Alerts and "no overlay" are the preview's own switches now - they
        # belong in the window they are about rather than on whichever screen
        # the launcher happens to be on. Only the overlay-disabled one comes
        # back here, because only the launcher owns the overlay process.
        self.browser.set_show_alerts(config.preview_alerts())
        self.browser.overlay_disabled_changed.connect(
            self._apply_overlay_disabled)
        # What the overlay last said about its own panel, so a preference
        # change can tell whether anything actually needs doing.
        self._overlay_hidden = False

        if config.build_browser():
            self._show_browser()

        # APM tracking: a counter child that runs alongside the overlay.
        self.apm_toggle = QCheckBox("Track APM")
        self.apm_toggle.setToolTip(wrapped(
            "Count keystrokes and clicks per minute while the overlay runs."
            " Counts only - the counter is built so it never knows which"
            " key. Written into the game's stats file."))
        self.apm_toggle.setChecked(config.track_apm())
        self.apm_toggle.toggled.connect(config.set_track_apm)
        if apm.how_counted(sys.platform) is None:
            # No counter exists on this platform, so a ticked box would
            # promise counting that never happens - the same honesty the
            # hotkey stub buys with its unsupported message. The saved
            # setting is left alone: it comes back the day a counter lands.
            self.apm_toggle.setEnabled(False)
            self.apm_toggle.setToolTip(wrapped(
                "APM counting has no backend on this platform yet, so"
                " nothing is counted and the stats file simply carries no"
                " APM section. The saved setting is untouched."))

        # Past games, in their own window like the preview.
        self.stats_window = StatsWindow()
        self.stats_button = QPushButton("Statistics")
        self.stats_button.setToolTip(wrapped(
            "Past games: the build report, the post-game summary, and"
            " graphs. One file per game in stats/."))
        self.stats_button.clicked.connect(self._open_stats)
        # Violet, the colour the recorded game wears throughout the
        # statistics window, so the button and what it opens are visibly
        # the same subject. DERIVED from that colour rather than written
        # out again: retune RECORD_COLOR and this follows.
        #
        # Darkened, though, and by measurement rather than by eye. The
        # chart colour is tuned to be legible as a thin line on a dark
        # ground, which makes it far too light to sit behind white text -
        # white on it is 2.58:1, under the 4.5:1 a person can comfortably
        # read. darker(160) is 5.88:1, and the hover lightens to 4.73:1,
        # which is still above the floor. A colour that works as a stroke
        # is not automatically one that works as a fill.
        tint = css_rgb(RECORD_COLOR.darker(STATS_BUTTON_DARKEN))
        lighter = css_rgb(RECORD_COLOR.darker(STATS_BUTTON_HOVER_DARKEN))
        self.stats_button.setStyleSheet(
            f"QPushButton {{ background-color: {tint}; color: white;"
            " font-weight: bold; padding: 4px 14px; border-radius: 3px; }"
            f"QPushButton:hover {{ background-color: {lighter}; }}")

        # How to use: which HUD mods work, and how to get going. Shown once
        # on a fresh install by loom_app; this button is how it comes back.
        self.about_window = AboutWindow(self)
        self.about_button = QPushButton("How to use")
        self.about_button.setToolTip(wrapped(
            "Which HUD mods Loom works with, and how to set it up."))
        self.about_button.clicked.connect(self._open_about)

        toggles = flow_row([self.dev_toggle, self.browser_toggle,
                             self.apm_toggle, self.record_toggle,
                             self.stats_button])

        # How to use sits alone at the TOP RIGHT, in blue - the one control
        # a lost new player needs, put where lost people look and coloured
        # so it cannot hide among a column of grey settings. It used to sit
        # in the toggles row at the bottom, which is where you find things
        # you already know exist.
        self.about_button.setStyleSheet(
            "QPushButton { background-color: #2f6fd0; color: white;"
            " font-weight: bold; padding: 4px 14px; border-radius: 3px; }"
            "QPushButton:hover { background-color: #3d7de0; }")
        header = QHBoxLayout()
        emblem = QPixmap(str(paths.LOGO_PATH))
        if not emblem.isNull():
            # The banner at the top left, the one place a logo goes. isNull
            # covers a clone with the image stripped - branding is the last
            # thing worth failing over.
            logo = QLabel()
            logo.setPixmap(emblem.scaledToHeight(
                40, Qt.TransformationMode.SmoothTransformation))
            header.addWidget(logo)
            name = QLabel("Loom")
            name_font = name.font()
            name_font.setPointSize(name_font.pointSize() + 4)
            name_font.setBold(True)
            name.setFont(name_font)
            header.addWidget(name)
        header.addStretch()
        header.addWidget(self.about_button)

        # The settings go into tabs. They used to be one tall column -
        # picker, Alerts, Overlay size, Overlay transparency, Hotkeys,
        # developer tools, output pane - and that column outgrew a 1080p
        # screen: the window opened with Start and the output below the
        # bottom edge, and maximising it made Qt squeeze every box PAST its
        # own minimum until the captions overlapped. A layout under pressure
        # shrinks its children rather than refusing, so the fix is to stop
        # asking it for the height in the first place.
        #
        # What stays OUT of the tabs is the spine of the app: pick a build,
        # start the overlay, read what it says. Those are not settings and
        # must never be a click away.
        self.tabs = QTabWidget()
        for label, names, keep_titles in SETTINGS_TABS:
            boxes = [getattr(self, name) for name in names]
            if not keep_titles:
                # The tab already says it; the box inside need not say it
                # twice. An empty title leaves the frame, which still reads
                # as a panel.
                for box in boxes:
                    box.setTitle("")
            # Always through _stacked, even for a single box: it carries the
            # trailing stretch that keeps a short page's rows together at
            # the top instead of spread down the height of the tallest tab.
            self.tabs.addTab(_stacked(boxes), label)
        self.dev_panel.setTitle("")
        self._dev_tab = _stacked([self.dev_panel])
        self._show_dev_tab(config.developer_mode())

        # Everything below the header scrolls. The tabs are the ergonomics;
        # this is the guarantee - it is what makes the window safe at a size
        # neither I nor anyone testing it has tried, which is the only kind
        # of screen this bug has ever appeared on.
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.picker)
        layout.addWidget(controls)
        layout.addWidget(self.tabs)
        layout.addWidget(toggles)
        layout.addWidget(self.output, stretch=1)

        scroll = QScrollArea()
        scroll.setWidget(column)
        # The column follows the window's width, so growing the window still
        # widens the build list and the output pane rather than leaving a
        # margin of nothing beside them.
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        # Vertical only, and that is a promise rather than a preference.
        # Sideways scrolling put the right-hand side of the window out of
        # reach and clipped the build list's own scrollbar on the way. Every
        # row of controls in here wraps now and every caption word-wraps, so
        # the column's minimum width is 490 against a 560 window - a
        # horizontal bar could only mean that stopped being true, and a test
        # holds the number.
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        outer = QVBoxLayout(self)
        outer.addLayout(header)
        outer.addWidget(scroll, stretch=1)
        self.setMinimumSize(*MINIMUM_SIZE)

        # The preview mirrors whichever build is picked, now and on change.
        self.browser.set_build(self.picker.selected_build())
        self.picker.selection_changed.connect(
            lambda: self.browser.set_build(self.picker.selected_build()))

        self.picker.note.connect(self.output.append_line)
        for problem in self.picker.problems:
            self.output.append_line(f"[builds] {problem}")
        self._show_overlay_state(running=False)

        # The launcher's own hotkey: one key toggling Start/Stop. It has to
        # live HERE - the one thing it does is start a process that does not
        # exist yet - and because the listener and the settings share this
        # process, a rebind re-registers immediately instead of waiting for
        # anything to restart.
        self._launcher_hotkeys = None
        self._register_launcher_hotkeys()
        self.hotkeys_box.bindings_changed.connect(
            self._register_launcher_hotkeys)
        # While a binding field is listening, the launcher must not be
        # holding any global combination: a registered hotkey is swallowed
        # by the OS and delivered to the registering window, so the one
        # combination a player is most likely to press into a capture field -
        # the one already bound - is precisely the one it could not see, and
        # pressing it would start the overlay instead. _register_launcher_
        # hotkeys is already stop-then-listen, so taking them back is the
        # same call the rest of this class makes.
        self.hotkeys_box.capture_started.connect(self._release_launcher_hotkeys)
        self.hotkeys_box.capture_ended.connect(self._register_launcher_hotkeys)
        # The Preview tab reaches the open preview instantly - both live in
        # this process, which is what the tab's own hint promises.
        self.preview_appearance.changed.connect(
            self.browser.apply_appearance)
        # The Appearance tab reaches the overlay down its stdin pipe, which
        # is slower to say but no slower to feel.
        self.appearance.changed.connect(self._announce_settings)
        self.transparency.changed.connect(self._announce_settings)

    # ---- window geometry -------------------------------------------------

    def fit_to_screen(self):
        """Size and place the window for the screen it is about to open on.

        Called before show(), from loom_app. The remembered geometry wins
        where there is one, but it is passed through the same clamp as a
        fresh one: a size and position saved on a 2560x1440 desktop must not
        reopen off the bottom of a 1920x1080 one, where the only symptom is
        that Loom appears not to have started.
        """
        area = self._work_area()
        width, height = fitted_size(config.launcher_window() or PREFERRED_SIZE,
                                    MINIMUM_SIZE, area)
        self.resize(width, height)

        remembered = config.launcher_position()
        if remembered is None:
            return

        # Believed if it lands on ANY screen, not clamped onto one.
        #
        # Clamping needs a screen to clamp against, and this picked the wrong
        # one: a window that has not been shown yet reports the PRIMARY screen
        # as its own, so a position saved on a second monitor was squashed
        # into the primary screen's work area and the launcher walked back
        # across the desk on every launch. Measured on a two-monitor desk - a
        # saved (2811, -236) came back as (1359, 0), which looks exactly like
        # the position was never saved at all.
        #
        # The weaker question is the right one, and loom/placement.py is the
        # same rule the overlay panel has always used for its own saved spot.
        screens = placement.screen_rects(QApplication.instance())
        if placement.visible_on(screens, *remembered, width, height):
            self.move(*remembered)
            return

        print(f"[launcher] the saved window position {remembered} is off "
              f"every screen - opening in the default spot instead")
        self.move(*clamped_position(remembered, (width, height), area))

    def _work_area(self):
        """The usable screen, as (left, top, right, bottom)."""
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        return area.left(), area.top(), area.right(), area.bottom()

    def _remember_geometry(self):
        """Save how big the player made the window, and where they left it.

        Not while maximised: the maximised size is the screen's, not a
        choice, and saving it would have the window reopen filling the
        desktop with no way back to the size before.
        """
        if self.isMaximized() or self.isMinimized():
            return
        config.set_launcher_window(self.width(), self.height())
        config.set_launcher_position(self.x(), self.y())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._geometry_timer.start(SAVE_GEOMETRY_AFTER_MS)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._geometry_timer.start(SAVE_GEOMETRY_AFTER_MS)

    # ---- the launcher's own hotkey --------------------------------------

    def _release_launcher_hotkeys(self):
        """Hand every global combination back, for as long as a field listens.

        Quiet on purpose - no output-pane line. This happens on every click
        into a binding field and comes straight back afterwards; narrating it
        would bury the lines that report a registration that actually failed.
        """
        if self._launcher_hotkeys is not None:
            hotkeys.stop(self._launcher_hotkeys)
            self._launcher_hotkeys = None

    def _register_launcher_hotkeys(self):
        """(Re)register the start/stop key from the current settings.

        Stop-then-listen every time, so a rebind hands the old combination
        back to the game in the same breath it takes the new one. Nothing
        here may break the launcher: a hotkey is a convenience, and every
        failure is a line in the output pane.
        """
        if self._launcher_hotkeys is not None:
            hotkeys.stop(self._launcher_hotkeys)
            self._launcher_hotkeys = None

        if not config.hotkeys_enabled():
            return
        bindings = {action: binding
                    for action, binding in config.hotkeys().items()
                    if action in config.LAUNCHER_HOTKEY_ACTIONS
                    and not keyspec.is_disabled(binding)}
        if not bindings:
            return

        try:
            listener = hotkeys.listen(bindings, self._on_launcher_hotkey)
        except hotkeys.HotkeyError as problem:
            self.output.append_line(f"[launcher] hotkeys unavailable: {problem}")
            return
        for action, binding, reason in listener.failures:
            self.output.append_line(
                f"[launcher] hotkey {action} ({binding}) could not be "
                f"registered: {reason}")
        if listener.actions:
            taken = ", ".join(sorted(bindings[action]
                                     for action in listener.actions.values()))
            self.output.append_line(
                f"[launcher] hotkey: {taken} starts and stops the overlay "
                f"(taken from the game while the launcher runs)")
        self._launcher_hotkeys = listener

    def _on_launcher_hotkey(self, action):
        if action != "start_stop_overlay":
            return
        # Exactly the buttons' semantics: both methods are guarded, so a
        # mashed key can neither double-start nor kill anything twice.
        if self.overlay_process is not None and self.overlay_process.is_running():
            self.stop_overlay()
        else:
            self.start_overlay()

    # ---- the overlay slot ----------------------------------------------

    def start_overlay(self):
        if self.overlay_process is not None and self.overlay_process.is_running():
            return
        mode = self.picker.selected_mode()
        stem = self.picker.selected_stem()
        if stem is None and mode == config.BUILD_MODE:
            self.output.append_line(
                "[launcher] no build orders found. Put RTS Overlay JSON "
                f"files in {paths.DATA_DIR / 'builds'} — or pick one of the "
                "NO BUILD ORDER modes, which need no library at all")
            return
        self.overlay_process = self._spawn(
            "overlay", overlay_argv(mode, stem), self._overlay_finished)
        # The APM counter rides along, collecting buckets the whole session;
        # they are joined to game time and written after the overlay ends.
        self._apm_buckets = []
        self._time_pairs = []
        # Only where APM is counted BY a separate process. On Windows the
        # overlay counts it itself with Raw Input, and spawning this too
        # would count every action twice - which would not look like a bug,
        # it would look like the player having a very good game. And on
        # macOS there is no counter at all yet, so the old "not in the
        # overlay, therefore a child" spawned tools.apm_counter, which
        # imports Xlib and dies. Asking for "child" by name spawns the
        # child only where a child counter exists.
        if config.track_apm() and apm.how_counted(sys.platform) == "child":
            self.apm_process = self._spawn("apm", ["-m", "tools.apm_counter"],
                                           self._apm_finished)
        self._show_overlay_state(running=True)

    def stop_overlay(self):
        if self.overlay_process is not None:
            self.overlay_process.stop()
        if self.apm_process is not None:
            self.apm_process.stop()

    def toggle_overlay_hidden(self):
        """Ask the overlay to hide or come back.

        Nothing about the button changes here. The overlay owns whether its
        panel is up, and says so on the statefeed - so pressing this and
        pressing the overlay's own hotkey take the same route and cannot
        leave the button showing one thing while the panel does another.
        """
        if self.overlay_process is not None:
            self.overlay_process.request_toggle_hidden()

    def reset_overlay_position(self):
        """Forget the saved overlay spot.

        Applies on the next overlay start - unlike the Appearance tab, which
        a running overlay follows live. Position is the odd one out because
        the panel may have been dragged since, and yanking it out from under
        a player mid-match to a spot they did not ask for is worse than
        waiting.
        """
        config.clear_overlay_offset()
        self.output.append_line(
            "[launcher] overlay position reset to the default (top right,"
            " under the game's bar) - applies the next time the overlay"
            " starts")

    def placing_now(self):
        """Is the dev slot currently holding a placement panel?

        The label is what identifies it. The slot is shared with the demo
        and the capture tools, and only placement may be closed by pressing
        its own button again - stopping a frame grab that way would throw
        away a capture the player is in the middle of taking.
        """
        prefix, _build_argv = PLACE_COMMAND
        return (self.dev_process is not None
                and self.dev_process.is_running()
                and self.dev_process.label == prefix)

    def place_overlay(self):
        """Open the overlay's placement mode, or close the one already open.

        A toggle, because the placement panel is frameless and so has no
        close button of its own. Its own button ends it by SAVING and Esc
        ends it by abandoning, but both need the panel to have the focus -
        and the player who has just clicked back to the launcher does not
        want to hunt for the panel to dismiss it. The button that opened it
        is the obvious thing to reach for, so it closes it too.

        Closing this way saves nothing, exactly like Esc: only the panel's
        own button writes a position down.

        Runs through the dev-task slot so stop/cleanup/output routing all
        come free. Disabled while the overlay runs - two panels at once
        would confuse, and a new offset only applies on the next launch
        anyway.
        """
        prefix, build_argv = PLACE_COMMAND
        if self.placing_now():
            self.dev_process.stop()
            self.output.append_line(
                "[launcher] placement closed — nothing saved")
            return
        # Placement shows the panel the player will ACTUALLY play with, so
        # it carries the mode. A tracking mode placed against a build panel
        # would be positioning a window that never appears.
        mode = self.picker.selected_mode()
        self.run_dev_command(
            prefix,
            lambda stem, _scenario: overlay_argv(mode, stem, place=True))
        # Asked, not assumed: run_dev_command refuses while another dev task
        # holds the slot, and a button that said "Close placement" over a
        # panel that never opened would be a lie about what pressing it does.
        self._show_place_state(placing=self.placing_now())

    def _overlay_finished(self, label, exit_code):
        self.output.append_line(f"[{label}] exited with code {exit_code}")
        self._show_overlay_state(running=False)
        self.browser.overlay_stopped()
        if self.apm_process is not None:
            self.apm_process.stop()
        self._write_apm_section()
        self._attach_recorded_game()
        # A finished overlay usually means a fresh stats file just landed.
        if self.stats_window.isVisible():
            self.stats_window.refresh()

    def _apm_finished(self, label, exit_code):
        # Quiet on the clean path; the counter's own prints already went to
        # the pane through output_line.
        self.apm_process = None

    def _write_apm_section(self):
        """Join the session's APM buckets to game time and put them in the
        newest stats file. The overlay has exited, so there is exactly one
        writer touching the file."""
        if not self._apm_buckets:
            return
        newest = max(paths.STATS_DIR.glob("*.json"), default=None,
                     key=lambda p: p.stat().st_mtime)
        if newest is None:
            return
        try:
            data = json.loads(newest.read_text(encoding="utf-8"))
            # Read FIRST, so the file can say which game it is about. One
            # overlay session can span two matches; the buckets run across
            # both and only the timeline knows where the join is.
            recorded = (data.get("timeline") or {}).get("t") or []
            section = apm.align(self._apm_buckets, self._time_pairs,
                                game_from=recorded[0] if recorded else None)
            self._apm_buckets = []
            self._time_pairs = []
            if section is None:
                self.output.append_line(
                    "[launcher] APM was counted but no game time overlapped it")
                return
            data["apm"] = section
            newest.write_text(json.dumps(data, indent=1) + "\n",
                              encoding="utf-8")
            self.output.append_line(f"[launcher] APM written into {newest.name}")
        except (OSError, json.JSONDecodeError) as error:
            self.output.append_line(f"[launcher] could not add APM: {error}")

    def _show_place_state(self, placing):
        """What the Place overlay button is offering right now.

        The button is the placement panel's only reliable way out - the
        panel is frameless, so it has no close button, and its own two
        exits both need it to hold the focus. Saying which of the two
        things it will do is what stops a second press being a surprise.
        """
        self.place_button.setText(
            "Close placement" if placing else "Place overlay")

    def _attach_recorded_game(self):
        """Attach the match's own recorded game, once the game has ended.

        This is the boundary the whole feature is built around, and this
        is the moment it is allowed: the overlay has exited, so the match
        is over. `loom/replay.py` refuses anything still being written
        regardless, so this is the second lock rather than the only one.

        Three deliberate refusals:

        * Only a CERTAIN match. An AMBIGUOUS one - two records whose
          windows both contain the session - is left for a person, because
          attaching the wrong game's truth to this game's readings is
          exactly the mistake that would be hardest to notice later.
        * Never fatal. A missing record, an unreadable one, a match that
          was a replay being watched: all of them leave the stats file
          exactly as it was and say so in the output pane.
        * Off is a real setting. Some people will not want Loom opening
          files in their savegame folder at all.

        The stats file is chosen ONCE, here, and carried into the retry.
        Picking "newest by mtime" a second time would be answering a
        different question: attaching a record rewrites a stats file, so a
        manual attach in the statistics window moves an mtime and "newest"
        quietly stops meaning "the game that just ended".
        """
        if not config.attach_recorded_game():
            return
        newest = max(paths.STATS_DIR.glob("*.json"), default=None,
                     key=lambda p: p.stat().st_mtime)
        if newest is None:
            return
        self._try_attaching(newest, retry=True)

    def _try_attaching(self, stats_path, retry):
        """One attempt at attaching a record, with at most one more.

        The retry exists because the first attempt cannot win. Measured on
        this machine: the game finished writing its record at 12:13:50 and
        the stats file landed at 12:13:59 - NINE seconds, against the
        thirty-second window `replay.refusal_reason` insists on before it
        will read a file the game may still be writing. So the normal path
        was refused every single time, and nothing ever asked again.

        One retry rather than a loop. If the record is still unreadable
        thirty seconds after the match ended, something other than the
        write is wrong, and the banner and the scan button in the
        statistics window are both better places for a person to take over
        than a launcher quietly polling their savegame folder.

        The first attempt says nothing final when a retry is coming. Two
        lines for one question, the first of them wrong, is exactly how
        this looked like a missing record rather than a race.
        """
        try:
            said = statsview.enrich_with_record(stats_path)
        except Exception as error:          # never worth losing a game over
            self.output.append_line(
                f"[launcher] could not attach the recorded game: {error}")
            return
        if said.startswith("added"):
            self.output.append_line(
                f"[launcher] recorded game attached to {stats_path.name}"
                f" - {said}")
            self._record_attached(stats_path)
            return
        if retry:
            self.output.append_line(
                f"[launcher] no recorded game yet ({said}) - trying once"
                f" more in {RETRY_ATTACH_AFTER_MS // 1000}s")
            QTimer.singleShot(
                RETRY_ATTACH_AFTER_MS,
                lambda: self._try_attaching(stats_path, retry=False))
            return
        self.output.append_line(f"[launcher] no recorded game: {said}")

    def _record_attached(self, stats_path):
        """Show it, if the window that would show it is open.

        The statistics window reads these files when a game is selected,
        so one enriched behind its back is a view that has quietly stopped
        matching the disk.
        """
        if self.stats_window.isVisible():
            self.stats_window.reload(stats_path)


    def _show_overlay_state(self, running, hidden=False):
        # Disabling the irrelevant button is the status display doing double
        # duty: it also makes double-starts impossible.
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        # Normally the placement button is for a stopped overlay only - two
        # panels at once confuse, and a new offset applies on the next
        # launch anyway. The exception is a placement panel that is already
        # open: disabling its way out while it is on screen would strand it.
        self.place_button.setEnabled(not running or self.placing_now())
        self.reset_place_button.setEnabled(not running)
        self.status.setText(overlay_status_text(running, hidden))

        self.hide_button.setEnabled(running)
        self.hide_button.setText("Show overlay" if hidden else "Hide overlay")
        # The bindings are the overlay's while it holds them. Driven from
        # here rather than from the start and stop paths separately: this
        # is the one function that already knows, and two callers would be
        # two chances to forget one.
        self.hotkeys_box.set_overlay_running(running)
        # Green while hidden, so the one button that leaves no trace on
        # screen still says what it did. :enabled scoped like the others -
        # a stopped overlay's button should read as unclickable, not as a
        # colourful one that does nothing. Both branches carry the base
        # style: this used to clear the stylesheet entirely, which flipped
        # the button between Qt's stylesheet renderer and the platform's
        # native one mid-session - see CONTROL_BUTTON_STYLE.
        self.hide_button.setStyleSheet(
            GO_BUTTON_STYLE if hidden else CONTROL_BUTTON_STYLE)

    # ---- the dev slot --------------------------------------------------

    def run_dev_command(self, prefix, build_args):
        if self.dev_process is not None and self.dev_process.is_running():
            self.output.append_line(
                "[launcher] a task is already running — stop it first")
            return
        if (prefix in NEEDS_THE_OVERLAY_SLOT_FREE
                and self.overlay_process is not None
                and self.overlay_process.is_running()):
            self.output.append_line(
                "[launcher] this starts its own overlay — stop the running "
                "one first, or you get two sets of statistics")
            return
        # selected_stem() is None in a tracking mode, so the fallback fires
        # and the dev tools keep working on a real build. That is why
        # selected_stem narrows rather than returning the raw row: a mode
        # string is truthy and would have sailed into --build.
        argv = build_args(self.picker.selected_stem() or "fast_castle",
                          self.dev_panel.scenario.currentText())
        self.output.append_line(f"[launcher] running: {' '.join(argv)}")
        try:
            self.dev_process = self._spawn(prefix, argv, self._dev_finished)
        except ValueError as problem:
            # The braces to DevPanel's belt. Nothing that raises here may
            # escape: an exception out of a Qt slot aborts the process, and
            # a packaged Loom has no console for the traceback to land in.
            self.output.append_line(f"[launcher] cannot run this: {problem}")

    def stop_dev_task(self):
        if self.dev_process is not None:
            self.dev_process.stop()

    def _dev_finished(self, label, exit_code):
        self.output.append_line(f"[{label}] exited with code {exit_code}")
        self.browser.overlay_stopped()
        # Whatever it was doing is over, so the button goes back to
        # offering the thing it offers when nothing is placed. Unconditional
        # rather than checked against the label: the only state it can be in
        # after ANY dev task ends is "not placing".
        self._show_place_state(placing=False)

    # ---- shared --------------------------------------------------------

    def _spawn(self, label, argv, on_finished):
        child = ChildProcess(label, argv, parent=self)
        child.output_line.connect(self.output.append_line)
        # State lines feed the build preview - from either slot, so the dev
        # panel's demo overlay drives it just like the real one.
        child.state_line.connect(self._on_state_line)
        child.finished.connect(on_finished)
        child.start()
        return child

    def _on_state_line(self, line):
        payload = statefeed.decode(line)
        if payload is None:
            return
        if "apm" in payload:
            bucket = payload["apm"]
            self._apm_buckets.append((time.monotonic(),
                                      bucket.get("keys", 0),
                                      bucket.get("clicks", 0)))
            return
        if "hidden" in payload:
            # The overlay saying its panel went away or came back, however
            # it was asked. Told apart by key, the same way the APM payload
            # is - and returning here matters: falling through would hand
            # the browser a payload with no "usable" in it, which it reads
            # as "no game", freeing a preview that is still following one.
            self._overlay_hidden = bool(payload["hidden"])
            self._show_overlay_state(running=True,
                                     hidden=self._overlay_hidden)
            return
        # Overlay state: feed the preview, and remember the wall<->game
        # pairing that later places APM buckets on the game clock.
        if payload.get("usable") and payload.get("t") is not None:
            self._time_pairs.append((time.monotonic(), payload["t"]))
        self.browser.apply_state(payload)

    def _set_developer_mode(self, enabled):
        config.set_developer_mode(enabled)
        self._show_dev_tab(enabled)

    def _show_dev_tab(self, enabled):
        """Add or remove the developer tab.

        Removing rather than disabling: a tab nobody can open is still a tab
        everybody has to read past, and these tools are not part of the app
        for anyone who has not asked for them.
        """
        index = self.tabs.indexOf(self._dev_tab)
        if enabled and index < 0:
            self.tabs.addTab(self._dev_tab, DEV_TAB_LABEL)
        elif not enabled and index >= 0:
            self.tabs.removeTab(index)

    def _open_stats(self):
        self.stats_window.show()
        self.stats_window.raise_()
        self.stats_window.refresh()

    def _open_about(self):
        self.about_window.open_at(self._cascaded_from_me(self.about_window))

    def show_about_if_unseen(self):
        """Open the How-to-use window on a fresh install. Returns whether it
        opened, so a caller (and a test) can tell."""
        if config.about_seen():
            return False
        self.about_window.show_first_run(
            self._cascaded_from_me(self.about_window))
        return True

    def _cascaded_from_me(self, window):
        """A spot overlapping the launcher's top-left, clamped on screen.

        The classic offset a dialog opens at. It matters here because this
        window is SMALLER than the launcher: centred, it landed exactly
        behind it and looked like nothing had happened at all.
        """
        offset = 48
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = min(self.x() + offset, area.right() - (window.width() or 620))
        y = min(self.y() + offset, area.bottom() - (window.height() or 520))
        return max(area.left(), x), max(area.top(), y)

    def _announce_settings(self):
        """An Appearance setting changed. Tell a running overlay to re-read.

        The setting is already saved by the time this runs, so an overlay
        that is not running needs nothing from us - it will read the file
        when it starts, exactly as before. This only closes the gap for a
        session already on screen.

        Throttled rather than debounced: a slider fires on every tick of a
        drag, and a debounce would show the player nothing until they let
        go, which is the behaviour this feature exists to remove.
        """
        if self._settings_timer.isActive():
            # Inside the gate. Remember that something changed so the final
            # value is not the one that gets dropped.
            self._settings_pending = True
            return
        self._send_settings()
        self._settings_timer.start()

    def _settings_gate_opened(self):
        if self._settings_pending:
            self._settings_pending = False
            self._send_settings()
            self._settings_timer.start()

    def _send_settings(self):
        """One request down each pipe, to whoever is on the end of it.

        BOTH slots, not just the overlay's. Place overlay and the demo run
        in the dev slot, and they draw the same panel from the same settings
        - so an Appearance slider that moved a running overlay but not the
        placement panel would be at its least helpful in the one mode whose
        entire job is deciding what the panel should look like and where it
        should sit.

        The full liveness test, not just a null check: _overlay_finished
        leaves the object in place when a child exits, so `is not None`
        alone would write to a QProcess whose program is long gone.
        """
        for child in (self.overlay_process, self.dev_process):
            if child is not None and child.is_running():
                child.request_settings_changed()

    def _apply_overlay_disabled(self, disabled):
        """The preview's "No overlay" box changed. Make it so, now.

        The preference decides how the overlay STARTS, and loom_overlay reads
        it for itself - but ticking a box and watching the panel stay put is
        exactly the hassle this was meant to remove, so a session already
        running is brought into line too.

        Only when the two actually differ. The request is a TOGGLE, because
        the overlay owns the hidden state and everyone else asks - so firing
        it blindly at an overlay that is already hidden would show it.
        """
        if self.overlay_process is None or not self.overlay_process.is_running():
            return
        if bool(disabled) != self._overlay_hidden:
            self.overlay_process.request_toggle_hidden()

    def _set_build_browser(self, enabled):
        config.set_build_browser(enabled)
        if enabled:
            self._show_browser()
        else:
            self.browser.hide()

    def _show_browser(self):
        """Show the preview, placing it beside the launcher the first time.

        Only the first time: once the player has moved it, config remembers
        where, and that beats any guess this could make.

        The placement is best-effort by nature. Under Wayland a client is not
        allowed to position its own windows at all and the move is simply
        ignored - which is why the preview is PARENTED to the launcher rather
        than relying on this. Parenting is what guarantees it stops opening
        behind; this only makes it tidy where the platform permits.
        """
        if config.browser_position() is None:
            self.browser.move(*self._beside_me(self.browser))
        self.browser.show()
        self.browser.raise_()

    def _beside_me(self, window):
        """Where to put a window so it sits next to the launcher, on screen."""
        area = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        return beside(
            (self.x(), self.y(), self.frameGeometry().width()),
            (window.width() or 600, window.height() or 640),
            (area.left(), area.top(), area.right(), area.bottom()))

    def closeEvent(self, event):
        """Closing the launcher takes its children with it - no orphans.

        Blocking waits are fine here (the UI is going away), and shutdown()
        bounds them, so the window cannot hang open indefinitely either.
        """
        # Before anything else: the debounce means a window resized and
        # closed inside a second would otherwise forget the size it was just
        # given, which reads as the setting not sticking.
        self._geometry_timer.stop()
        self._remember_geometry()

        if self._launcher_hotkeys is not None:
            hotkeys.stop(self._launcher_hotkeys)
        for child in (self.overlay_process, self.dev_process,
                      self.apm_process):
            if child is not None:
                child.shutdown()
        # Take the preview and statistics windows along. Signals blocked so
        # the preview's closeEvent does not read as the player unticking the
        # checkbox - quitting the app must not flip the preview off in the
        # settings.
        self.browser.blockSignals(True)
        self.browser.close()
        self.stats_window.close()
        self.about_window.close()
        event.accept()
