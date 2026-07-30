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
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGroupBox, QHBoxLayout,
                             QLabel, QPlainTextEdit, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from . import apm, config, paths, statefeed
from . import __version__ as loom_version
from .browser import BuildBrowser
from .statsview import StatsWindow
from .build_order import available_builds
from .runner import ChildProcess

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


class BuildPicker(QGroupBox):
    """The build-order library as a drop-down, metadata on each entry.

    The drop-down shows the human name from inside each JSON; the value
    carried on each entry is the file stem, which is what --build takes.
    """

    def __init__(self, parent=None):
        super().__init__("Build order", parent)
        self.combo = QComboBox()
        self.combo.setToolTip(
            "Which build order the overlay and the preview follow. Drop new"
            " ones into builds/ as RTS Overlay JSON files.")
        layout = QVBoxLayout(self)
        layout.addWidget(self.combo)

        self.problems = self._populate()
        # Whenever the choice changes, remember it - so the next session and
        # the bare command line default to the same build. Connected after
        # populating, so restoring the saved choice does not re-save it.
        self.combo.currentIndexChanged.connect(self._remember)

    def _populate(self):
        builds, problems = available_builds()
        # Keep the loaded builds: the preview panel walks their steps, and
        # reloading files it just read would be pointless.
        self._builds = dict(builds)
        for stem, build in builds:
            label = (f"{build.name} — {build.civilization}"
                     f" — {build.author or 'unknown'}"
                     f" — {len(build.steps)} steps")
            # The second argument is invisible userData behind the display
            # text - what currentData() hands back later.
            self.combo.addItem(label, stem)
        # Restore the saved choice. findData returns -1 if that build's file
        # has since been deleted, and setCurrentIndex(-1) would blank the
        # box - so fall back to the first entry instead.
        index = self.combo.findData(config.active_build())
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        return problems

    def _remember(self, _index):
        stem = self.combo.currentData()
        if stem is not None:
            config.set_active_build(stem)

    def selected_stem(self):
        """The chosen build's file stem, or None if the library is empty."""
        return self.combo.currentData()

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

        # The pre-emptive HOUSE NOW threshold: how much pop space remaining
        # should raise the warning. A boom eats more per house than a
        # one-TC opening, so the right number is the player's to pick.
        self.headroom = QSpinBox()
        self.headroom.setRange(*config.HOUSE_HEADROOM_BOUNDS)
        self.headroom.setValue(config.house_headroom())
        self.headroom.setToolTip(
            "Warn HOUSE NOW when this little population space is left -"
            " raise it if you keep getting housed anyway.")
        self.headroom.valueChanged.connect(config.set_house_headroom)

        house = QHBoxLayout()
        house.addWidget(QLabel("HOUSE NOW warns at"))
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
            ("house_warning", "Pre-emptive HOUSE NOW warning",
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
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.place_button)
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
        # and stay in sync with each other.
        self.browser = BuildBrowser()
        self.browser_toggle = QCheckBox("Show build preview")
        self.browser_toggle.setToolTip(
            "Show the build order in its own resizable window - browse it"
            " before a match, watch it follow along during one.")
        self.browser_toggle.setChecked(config.build_browser())
        self.browser_toggle.toggled.connect(self._set_build_browser)
        self.browser.closed.connect(
            lambda: self.browser_toggle.setChecked(False))
        if config.build_browser():
            self.browser.show()

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

        toggles = QHBoxLayout()
        toggles.addWidget(self.dev_toggle)
        toggles.addWidget(self.browser_toggle)
        toggles.addWidget(self.apm_toggle)
        toggles.addWidget(self.stats_button)
        toggles.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self.picker)
        layout.addLayout(controls)
        layout.addWidget(self.settings)
        layout.addWidget(self.appearance)
        layout.addLayout(toggles)
        layout.addWidget(self.dev_panel)
        layout.addWidget(self.output)

        # The preview mirrors whichever build is picked, now and on change.
        self.browser.set_build(self.picker.selected_build())
        self.picker.combo.currentIndexChanged.connect(
            lambda _index: self.browser.set_build(self.picker.selected_build()))

        for problem in self.picker.problems:
            self.output.append_line(f"[builds] {problem}")
        self._show_overlay_state(running=False)

    # ---- the overlay slot ----------------------------------------------

    def start_overlay(self):
        if self.overlay_process is not None and self.overlay_process.is_running():
            return
        stem = self.picker.selected_stem()
        if stem is None:
            self.output.append_line("[launcher] no build orders in builds/")
            return
        self.overlay_process = self._spawn(
            "overlay", ["loom_overlay.py", "--build", stem],
            self._overlay_finished)
        # The APM counter rides along, collecting buckets the whole session;
        # they are joined to game time and written after the overlay ends.
        self._apm_buckets = []
        self._time_pairs = []
        if config.track_apm():
            self.apm_process = self._spawn("apm", ["-m", "tools.apm_counter"],
                                           self._apm_finished)
        self._show_overlay_state(running=True)

    def stop_overlay(self):
        if self.overlay_process is not None:
            self.overlay_process.stop()
        if self.apm_process is not None:
            self.apm_process.stop()

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

    def _set_build_browser(self, enabled):
        config.set_build_browser(enabled)
        self.browser.setVisible(enabled)

    def closeEvent(self, event):
        """Closing the launcher takes its children with it - no orphans.

        Blocking waits are fine here (the UI is going away), and shutdown()
        bounds them, so the window cannot hang open indefinitely either.
        """
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
        event.accept()
