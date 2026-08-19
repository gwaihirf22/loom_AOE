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

from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QPixmap
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
                             QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPlainTextEdit, QPushButton, QSlider, QSpinBox,
                             QVBoxLayout, QWidget)

from . import apm, buildcheck, config, hotkeys, overlay, paths, statefeed
from .hotkeys import keyspec
from . import __version__ as loom_version
from .about import AboutWindow
from .browser import BuildBrowser
from .statsview import StatsWindow
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

# Placement is an everyday control, not a developer tool, so it lives with
# the Start/Stop buttons - but its argv stays module data like DEV_COMMANDS,
# so the same test can check it without any Qt.
PLACE_COMMAND = ("place",
                 lambda stem: ["loom_overlay.py", "--place", "--build", stem])

# The gap left between the launcher and a window placed beside it.
WINDOW_GAP = 12


def beside(anchor, size, area):
    """Where to put a window so it sits next to another one, on screen.

    anchor is (x, y, width) of the window to sit beside, size is (width,
    height) of the window being placed, and area is the screen's work area as
    (left, top, right, bottom). Returns (x, y).

    Pure arithmetic on purpose: the interesting cases are a preview too wide
    for the space to the right, and a monitor left of the primary one whose
    coordinates are negative. Neither is convenient to reproduce by opening
    real windows, and both would put the preview somewhere the player cannot
    reach - so they are worth testing with fake inputs instead.

    To the right by preference, flipping left when the right would hang off
    the edge, and clamped into the work area either way.
    """
    anchor_x, anchor_y, anchor_width = anchor
    width, height = size
    left, top, right, bottom = area

    x = anchor_x + anchor_width + WINDOW_GAP
    if x + width > right:
        x = anchor_x - width - WINDOW_GAP
    # Clamped last, so a window wider than the space still lands on screen
    # rather than half off it.
    x = max(left, min(x, right - width))
    y = max(top, min(anchor_y, bottom - height))
    return x, y


class OutputPane(QPlainTextEdit):
    """Where every child process's output lands, newest at the bottom."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setToolTip("Output from everything the launcher runs - the"
                        " overlay, the tools, the test suite.")
        # A fixed-width font, or pytest's aligned output turns to soup.
        font = QFont("Monospace")
        # StyleHint tells Qt what to substitute if there is no font actually
        # called "Monospace" on the system.
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(font)
        # QPlainTextEdit drops the oldest block (line) beyond this count, so
        # the pane cannot grow without bound during a long session.
        self.setMaximumBlockCount(OUTPUT_SCROLLBACK_LINES)

    def append_line(self, text):
        self.appendPlainText(text)


def _as_list(findings):
    """Findings as an HTML list for a message box.

    Qt's message boxes render rich text, and one paragraph holding four
    problems runs together into something nobody reads to the end.
    """
    items = "".join(f"<li>{finding.message}</li>" for finding in findings)
    return f"<ul>{items}</ul>"


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
        self.list.setToolTip(
            "Which build order the overlay and the preview follow.\n"
            "Import build adds one; they are kept in\n"
            f"{self.builds_dir}")
        self.list.setUniformItemSizes(True)
        self.list.setMinimumHeight(LIBRARY_ROWS * LIBRARY_ROW_HEIGHT)

        # Narrowing the library, not choosing from it. Neither of these is
        # remembered between sessions: a filter silently restored next time
        # is a library that looks half empty for no visible reason.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search builds…")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Matches the name, civilization and author. Every word has to "
            "match, so \"hera arena\" narrows to one build.")
        self.search.textChanged.connect(self._apply_filter)

        self.civ_filter = QComboBox()
        self.civ_filter.setToolTip(
            "Show the builds you could play as one civilization.\n"
            "Generic builds are included, because they work for every civ.")
        self.civ_filter.currentIndexChanged.connect(self._apply_filter)

        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: gray;")

        finder = QHBoxLayout()
        finder.addWidget(self.search, stretch=2)
        finder.addWidget(self.civ_filter, stretch=1)
        finder.addWidget(self.count_label)

        self.import_button = QPushButton("Import build…")
        self.import_button.setToolTip(
            "Add a build order from an RTS Overlay JSON file. Loom checks it "
            "first and says what it finds.")
        self.import_button.clicked.connect(self._import)

        self.open_button = QPushButton("Open builds folder")
        self.open_button.setToolTip(f"Open {self.builds_dir}")
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
        chosen = self.selected_stem() or config.active_build()
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
        # Falls back to the first row when the chosen build's file has since
        # been deleted, rather than leaving nothing selected.
        row = self._row_of(chosen)
        self.list.setCurrentRow(row if row >= 0 else 0)
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
        """A build was picked: remember it, and tell the launcher."""
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
        """Enter in the search box picks the top row."""
        if self.list.count():
            self.list.setCurrentRow(0)

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
                f"<b>{source.name}</b> was not imported.<br><br>"
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
                "Loom can use this build. Worth knowing first:<br><br>"
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

    def selected_stem(self):
        """The chosen build's file stem, or None if the library is empty."""
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def selected_build(self):
        """The chosen build, already loaded. None if the library is empty."""
        return self._builds.get(self.selected_stem())


def _next_launch_hint():
    """The one non-obvious fact about every setting box: a running overlay
    keeps the settings it started with."""
    hint = QLabel("Changes apply the next time the overlay starts.")
    hint.setStyleSheet("color: gray;")
    return hint


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
        self.soften.setToolTip(
            "Below this many villagers the idle-TC alert is loud and red.")
        self.silence = QSpinBox()
        self.silence.setRange(0, 200)
        self.silence.setValue(silence)
        self.silence.setToolTip(
            "At this many villagers the idle-TC alert stops entirely.")
        self.soften.valueChanged.connect(self._save_limits)
        self.silence.valueChanged.connect(self._save_limits)

        taper = QHBoxLayout()
        taper.addWidget(QLabel("Idle-TC alert softens at"))
        taper.addWidget(self.soften)
        taper.addWidget(QLabel("villagers, silences at"))
        taper.addWidget(self.silence)
        taper.addStretch()

        # The pre-emptive HOUSE SOON threshold: how much pop space remaining
        # should raise the warning. A boom eats more per house than a
        # one-TC opening, so the right number is the player's to pick.
        self.headroom = QSpinBox()
        self.headroom.setRange(*config.HOUSE_HEADROOM_BOUNDS)
        self.headroom.setValue(config.house_headroom())
        self.headroom.setToolTip(
            "Warn HOUSE SOON when this little population space is left -"
            " raise it if you keep getting housed anyway.")
        self.headroom.valueChanged.connect(config.set_house_headroom)

        house = QHBoxLayout()
        house.addWidget(QLabel("HOUSE SOON warns at"))
        house.addWidget(self.headroom)
        house.addWidget(QLabel("pop space left"))
        house.addStretch()

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
        boxes = QHBoxLayout()
        for name, text, tip in labels:
            box = QCheckBox(text)
            box.setChecked(toggles[name])
            box.setToolTip(tip)
            # The lambda needs name=name: without it, every lambda would
            # close over the same loop variable and toggle "house_warning".
            box.toggled.connect(
                lambda checked, name=name:
                config.set_alert_toggle(name, checked))
            self.checkboxes[name] = box
            boxes.addWidget(box)
        boxes.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(taper)
        layout.addLayout(house)
        layout.addLayout(boxes)
        layout.addWidget(_next_launch_hint())

    def _save_limits(self):
        config.set_idle_tc_limits(self.soften.value(), self.silence.value())


class OverlaySizeBox(QGroupBox):
    """The overlay's two size knobs, as percentages.

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

    def __init__(self, parent=None):
        super().__init__("Overlay size", parent)

        self.overall = QSpinBox()
        self.overall.setRange(round(config.OVERLAY_SCALE_BOUNDS[0] * 100),
                              round(config.OVERLAY_SCALE_BOUNDS[1] * 100))
        self.overall.setSingleStep(5)
        self.overall.setSuffix(" %")
        self.overall.setValue(round(config.overlay_scale() * 100))
        self.overall.setToolTip(
            "Grow the whole overlay panel - geometry, writing and icons"
            " together.")
        self.overall.valueChanged.connect(
            lambda value: config.set_overlay_scale(value / 100))

        self.text = QSpinBox()
        self.text.setRange(round(config.TEXT_SCALE_BOUNDS[0] * 100),
                           round(config.TEXT_SCALE_BOUNDS[1] * 100))
        self.text.setSingleStep(5)
        self.text.setSuffix(" %")
        self.text.setValue(round(config.text_scale() * 100))
        self.text.setToolTip(
            "Grow only the overlay's writing. The panel gets taller to fit"
            " it, but never wider.")
        self.text.valueChanged.connect(
            lambda value: config.set_text_scale(value / 100))

        row = QHBoxLayout()
        row.addWidget(QLabel("Overall size"))
        row.addWidget(self.overall)
        row.addWidget(QLabel("Text size"))
        row.addWidget(self.text)
        row.addStretch()

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(_next_launch_hint())


class OverlayTransparencyBox(QGroupBox):
    """The overlay's two transparency sliders, in their own titled group.

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

    def __init__(self, parent=None):
        super().__init__("Overlay transparency", parent)

        layout = QVBoxLayout(self)
        self.background = self._slider_row(
            layout, "Background", "0% invisible / 100% solid",
            config.background_opacity(),
            config.set_background_opacity,
            "How solid the overlay's dark card is. At 0% there is no card at"
            " all; at 100% the game cannot be seen through it. 80% is the"
            " designed look.")
        self.text = self._slider_row(
            layout, "Text && icons", "50% normal / 100% bright && bold",
            config.text_visibility(),
            config.set_text_visibility,
            "How visible the overlay's writing is. 50% is the designed look;"
            " lower fades it out, higher makes it solid and brighter for"
            " reading over bright terrain. Alert bands always stay at full"
            " strength - they are alarms.")
        layout.addWidget(_next_launch_hint())

    def _slider_row(self, layout, caption, scale_hint, value, setter, tip):
        """One captioned slider with a live percent label beside it.

        A QSlider cannot display its own value the way a spinbox shows a
        suffix, so the label does it - updated on every change, including
        mid-drag, which is half the point of a slider.
        """
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setPageStep(10)
        slider.setValue(round(value * 100))
        slider.setToolTip(tip)

        percent = QLabel(f"{slider.value()} %")
        percent.setMinimumWidth(40)

        def changed(new_value):
            percent.setText(f"{new_value} %")
            setter(new_value / 100)
        slider.valueChanged.connect(changed)

        caption_label = QLabel(caption)
        caption_label.setMinimumWidth(90)
        caption_label.setToolTip(tip)
        hint = QLabel(scale_hint)
        hint.setStyleSheet("color: gray;")

        row = QHBoxLayout()
        row.addWidget(caption_label)
        row.addWidget(slider, stretch=1)
        row.addWidget(percent)
        row.addWidget(hint)
        layout.addLayout(row)
        return slider


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

    def __init__(self, parent=None):
        super().__init__("Build-order hotkeys", parent)

        self.enabled = QCheckBox("Use hotkeys")
        self.enabled.setChecked(config.hotkeys_enabled())
        self.enabled.setToolTip(
            "Register these key combinations system-wide. While Loom holds"
            " them, the game does not see them - so switch this off to hand"
            " them all back at once.")
        self.enabled.toggled.connect(config.set_hotkeys_enabled)
        self.enabled.toggled.connect(
            lambda _checked: self.bindings_changed.emit())

        bindings = config.hotkeys()
        self.fields = {}
        rows = QVBoxLayout()
        for action in config.HOTKEY_ACTIONS:
            label, tip = self.LABELS[action]
            field = QLineEdit(bindings[action])
            field.setPlaceholderText("(no key)")
            field.setToolTip(
                f"{tip} Type something like Ctrl+Shift+W. Leave it empty to"
                f" switch this action off and give the keys back to the"
                f" game.")
            # name=action for the same reason the alert checkboxes need it:
            # without it every lambda closes over the last loop variable.
            field.textChanged.connect(
                lambda text, name=action: self._save(name, text))
            self.fields[action] = field

            row = QHBoxLayout()
            caption = QLabel(label)
            caption.setMinimumWidth(150)
            row.addWidget(caption)
            row.addWidget(field)
            rows.addLayout(row)

        self.hold = QSpinBox()
        low, high = config.MANUAL_HOLD_BOUNDS
        self.hold.setRange(low, high)
        self.hold.setSuffix(" s")
        self.hold.setValue(config.manual_hold_seconds())
        self.hold.setToolTip(
            "How long a step key stops the overlay following the game before"
            " it picks the game back up by itself. The step keys are meant as"
            " a correction, not a mode - this is how long the correction"
            " lasts.")
        self.hold.valueChanged.connect(config.set_manual_hold_seconds)

        hold_row = QHBoxLayout()
        hold_row.addWidget(QLabel("A step key holds sync off for"))
        hold_row.addWidget(self.hold)
        hold_row.addStretch()

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: rgb(235, 190, 90);")

        layout = QVBoxLayout(self)
        layout.addWidget(self.enabled)
        layout.addLayout(rows)
        layout.addLayout(hold_row)
        layout.addWidget(self.warning)
        if not hotkeys.available():
            unsupported = QLabel(
                "Hotkeys are not available on this system, so the overlay"
                " will only follow the game automatically.")
            unsupported.setWordWrap(True)
            unsupported.setStyleSheet("color: gray;")
            layout.addWidget(unsupported)
        # Two contracts, one per owner, stated rather than implied: the
        # overlay reads its keys once at startup; the launcher re-registers
        # its own the moment a binding changes.
        hint = QLabel("The step keys apply the next time the overlay starts;"
                      " the start/stop key applies immediately.")
        hint.setStyleSheet("color: gray;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._check()

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


class DevPanel(QGroupBox):
    """The developer tools: debug launchers and the test runner.

    Only one dev task runs at a time - a shared output pane showing two
    interleaved programs is worse than useless - so every button funnels
    through the window's single dev slot.
    """

    def __init__(self, run_command, stop_task, parent=None):
        """run_command(prefix, argv) and stop_task() come from the window,
        which owns the actual process slot."""
        super().__init__("Developer tools", parent)
        self.scenario = QComboBox()
        self.scenario.addItems(COACH_SCENARIOS)
        self.scenario.setToolTip(
            "Which synthetic match Coach simulate replays: on pace, running"
            " late, or stalling out.")

        buttons = QHBoxLayout()
        for label, prefix, build_args, tip in DEV_COMMANDS:
            button = QPushButton(label)
            button.setToolTip(tip)
            # name=... defaults again, for the same closure-over-loop reason.
            button.clicked.connect(
                lambda _checked, prefix=prefix, build_args=build_args:
                run_command(prefix, build_args))
            buttons.addWidget(button)

        stop = QPushButton("Stop task")
        stop.setToolTip("Terminate whichever developer task is running.")
        stop.clicked.connect(stop_task)

        row = QHBoxLayout()
        row.addWidget(QLabel("Coach scenario:"))
        row.addWidget(self.scenario)
        row.addStretch()
        row.addWidget(stop)

        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
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
        self.overlay_process = None
        self.dev_process = None

        self.picker = BuildPicker()
        self.settings = AlertSettingsBox()
        self.appearance = OverlaySizeBox()
        self.transparency = OverlayTransparencyBox()
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
        # Go/stop colors, but only while enabled - the :enabled scope keeps
        # Qt's greyed look on the inactive one, so a grey button still reads
        # as "not clickable" rather than as a colorless clickable button.
        self.start_button.setStyleSheet(
            "QPushButton:enabled { background-color: #2e7d32; color: white; }")
        self.stop_button.setStyleSheet(
            "QPushButton:enabled { background-color: #b03a2e; color: white; }")
        self.start_button.setToolTip(
            "Run the overlay over the game with the chosen build order.")
        self.stop_button.setToolTip("Stop the running overlay.")
        self.place_button.setToolTip(
            "Open a movable copy of the panel - drag it where you want the"
            " overlay, then close it to save the position.")
        self.status.setToolTip("Whether the overlay is currently running.")
        self.start_button.clicked.connect(self.start_overlay)
        self.stop_button.clicked.connect(self.stop_overlay)
        self.place_button.clicked.connect(self.place_overlay)
        self.reset_place_button = QPushButton("Reset position")
        self.reset_place_button.setToolTip(
            "Forget where the overlay was placed and go back to the default"
            " spot (top right, under the game's bar). The rescue for a"
            " position that ended up off the screen.")
        self.reset_place_button.clicked.connect(self.reset_overlay_position)
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.place_button)
        controls.addWidget(self.reset_place_button)
        controls.addWidget(self.status)
        controls.addStretch()

        # Developer mode: a persisted checkbox revealing the tools panel.
        self.dev_toggle = QCheckBox("Developer mode")
        self.dev_toggle.setToolTip(
            "Show the debug tools: demo mode, the coach simulator, capture"
            " tools and the test runner.")
        self.dev_panel = DevPanel(self.run_dev_command, self.stop_dev_task)
        self.dev_toggle.setChecked(config.developer_mode())
        self.dev_panel.setVisible(config.developer_mode())
        self.dev_toggle.toggled.connect(self._set_developer_mode)

        # The build preview: its own window, so the window manager is the
        # size control. The checkbox and the window's own X both hide it,
        # and stay in sync with each other. Parented to the launcher so it
        # can never open behind it.
        self.browser = BuildBrowser(self)
        self.browser_toggle = QCheckBox("Show build preview")
        self.browser_toggle.setToolTip(
            "Show the build order in its own resizable window - browse it"
            " before a match, watch it follow along during one.")
        self.browser_toggle.setChecked(config.build_browser())
        self.browser_toggle.toggled.connect(self._set_build_browser)
        self.browser.closed.connect(
            lambda: self.browser_toggle.setChecked(False))
        if config.build_browser():
            self._show_browser()

        # APM tracking: a counter child that runs alongside the overlay.
        self.apm_toggle = QCheckBox("Track APM")
        self.apm_toggle.setToolTip(
            "Count keystrokes and clicks per minute while the overlay runs."
            " Counts only - the counter is built so it never knows which"
            " key. Written into the game's stats file.")
        self.apm_toggle.setChecked(config.track_apm())
        self.apm_toggle.toggled.connect(config.set_track_apm)

        # Past games, in their own window like the preview.
        self.stats_window = StatsWindow()
        self.stats_button = QPushButton("Statistics")
        self.stats_button.setToolTip(
            "Past games: the build report, the post-game summary, and"
            " graphs. One file per game in stats/.")
        self.stats_button.clicked.connect(self._open_stats)

        # How to use: which HUD mods work, and how to get going. Shown once
        # on a fresh install by loom_app; this button is how it comes back.
        self.about_window = AboutWindow(self)
        self.about_button = QPushButton("How to use")
        self.about_button.setToolTip(
            "Which HUD mods Loom works with, and how to set it up.")
        self.about_button.clicked.connect(self._open_about)

        toggles = QHBoxLayout()
        toggles.addWidget(self.dev_toggle)
        toggles.addWidget(self.browser_toggle)
        toggles.addWidget(self.apm_toggle)
        toggles.addWidget(self.stats_button)
        toggles.addStretch()

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

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self.picker)
        layout.addLayout(controls)
        layout.addWidget(self.settings)
        layout.addWidget(self.appearance)
        layout.addWidget(self.transparency)
        layout.addWidget(self.hotkeys_box)
        layout.addLayout(toggles)
        layout.addWidget(self.dev_panel)
        layout.addWidget(self.output)

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

    # ---- the launcher's own hotkey --------------------------------------

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
        stem = self.picker.selected_stem()
        if stem is None:
            self.output.append_line(
                "[launcher] no build orders found. Put RTS Overlay JSON "
                f"files in {paths.DATA_DIR / 'builds'}")
            return
        self.overlay_process = self._spawn(
            "overlay", ["loom_overlay.py", "--build", stem],
            self._overlay_finished)
        # The APM counter rides along, collecting buckets the whole session;
        # they are joined to game time and written after the overlay ends.
        self._apm_buckets = []
        self._time_pairs = []
        # Only where APM is a separate process. On Windows the overlay
        # counts it itself with Raw Input, and spawning this too would count
        # every action twice - which would not look like a bug, it would look
        # like the player having a very good game.
        if config.track_apm() and not apm.counted_in_the_overlay(sys.platform):
            self.apm_process = self._spawn("apm", ["-m", "tools.apm_counter"],
                                           self._apm_finished)
        self._show_overlay_state(running=True)

    def stop_overlay(self):
        if self.overlay_process is not None:
            self.overlay_process.stop()
        if self.apm_process is not None:
            self.apm_process.stop()

    def reset_overlay_position(self):
        """Forget the saved overlay spot. Applies on the next overlay start,
        like every overlay setting."""
        config.clear_overlay_offset()
        self.output.append_line(
            "[launcher] overlay position reset to the default (top right,"
            " under the game's bar) - applies the next time the overlay"
            " starts")

    def place_overlay(self):
        """Open the overlay's placement mode: drag it, close it to save.

        Runs through the dev-task slot so stop/cleanup/output routing all
        come free. Disabled while the overlay runs - two panels at once
        would confuse, and a new offset only applies on the next launch
        anyway.
        """
        prefix, build_argv = PLACE_COMMAND
        self.run_dev_command(prefix,
                             lambda stem, _scenario: build_argv(stem))

    def _overlay_finished(self, label, exit_code):
        self.output.append_line(f"[{label}] exited with code {exit_code}")
        self._show_overlay_state(running=False)
        self.browser.overlay_stopped()
        if self.apm_process is not None:
            self.apm_process.stop()
        self._write_apm_section()
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
        section = apm.align(self._apm_buckets, self._time_pairs)
        self._apm_buckets = []
        self._time_pairs = []
        if section is None:
            self.output.append_line(
                "[launcher] APM was counted but no game time overlapped it")
            return
        newest = max(paths.STATS_DIR.glob("*.json"), default=None,
                     key=lambda p: p.stat().st_mtime)
        if newest is None:
            return
        try:
            data = json.loads(newest.read_text(encoding="utf-8"))
            data["apm"] = section
            newest.write_text(json.dumps(data, indent=1) + "\n",
                              encoding="utf-8")
            self.output.append_line(f"[launcher] APM written into {newest.name}")
        except (OSError, json.JSONDecodeError) as error:
            self.output.append_line(f"[launcher] could not add APM: {error}")

    def _show_overlay_state(self, running):
        # Disabling the irrelevant button is the status display doing double
        # duty: it also makes double-starts impossible.
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.place_button.setEnabled(not running)
        self.reset_place_button.setEnabled(not running)
        self.status.setText("overlay: running" if running
                            else "overlay: not running")

    # ---- the dev slot --------------------------------------------------

    def run_dev_command(self, prefix, build_args):
        if self.dev_process is not None and self.dev_process.is_running():
            self.output.append_line(
                "[launcher] a task is already running — stop it first")
            return
        argv = build_args(self.picker.selected_stem() or "fast_castle",
                          self.dev_panel.scenario.currentText())
        self.output.append_line(f"[launcher] running: {' '.join(argv)}")
        self.dev_process = self._spawn(prefix, argv, self._dev_finished)

    def stop_dev_task(self):
        if self.dev_process is not None:
            self.dev_process.stop()

    def _dev_finished(self, label, exit_code):
        self.output.append_line(f"[{label}] exited with code {exit_code}")
        self.browser.overlay_stopped()

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
        # Overlay state: feed the preview, and remember the wall<->game
        # pairing that later places APM buckets on the game clock.
        if payload.get("usable") and payload.get("t") is not None:
            self._time_pairs.append((time.monotonic(), payload["t"]))
        self.browser.apply_state(payload)

    def _set_developer_mode(self, enabled):
        config.set_developer_mode(enabled)
        self.dev_panel.setVisible(enabled)

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
