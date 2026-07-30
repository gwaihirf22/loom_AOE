"""
Loom — the statistics window: past games, three tabs each.

One row per game in stats/, three tabs for the selected one: the build
report (the overlay's payoff screen, preserved), the post-game summary
(what the whole match looked like), and graphs drawn with plain QPainter -
villagers and population over the match, the pace meter, and APM when the
counter ran. No charting library: three line charts are not worth a
dependency, and drawing them keeps the style of the overlay.

Everything read from disk is treated as untrusted: a corrupt or
foreign-schema file is listed as unreadable rather than crashing the
window, the same stance available_builds() takes with build files.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import math

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QScrollArea, QTabWidget,
                             QVBoxLayout, QWidget)

from . import gamestats, paths
from .build_order import format_time
from .overlay import (AHEAD_COLOR, BACKGROUND, BEHIND_COLOR, BORDER,
                      DIM_TEXT, FAINT_TEXT, ON_PACE_COLOR, TEXT)

APM_COLOR = QColor(200, 160, 235)


# ---- reading the stats folder (pure, testable) --------------------------

def load_stats(path):
    """One stats file as a dict, or None if it is not one of ours.

    Tolerant on purpose: the folder is user-visible and a stray or corrupt
    file must not take the whole window down.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("schema") != gamestats.SCHEMA:
        return None
    if not isinstance(data.get("game"), dict):
        return None
    return data


def list_stats():
    """Every stats file, newest first: [(path, label, data-or-None)].

    Unreadable files still get a row - saying "unreadable" beats silently
    hiding a file the player can see in the folder.
    """
    if not paths.STATS_DIR.exists():
        return []
    rows = []
    for path in sorted(paths.STATS_DIR.glob("*.json"), reverse=True):
        data = load_stats(path)
        if data is None:
            rows.append((path, f"{path.name} — unreadable", None))
            continue
        meta = data.get("meta", {})
        game = data.get("game", {})
        started = str(meta.get("started", ""))[:16].replace("T", " ")
        label = (f"{started} — {meta.get('build_name', '?')}"
                 f" — {format_time(game.get('duration', 0))}")
        rows.append((path, label, data))
    return rows


def tc_efficiency(game):
    """Fraction of TC capacity that was actually working, or None.

    Capacity = duration × the highest TC count seen; idle seconds come off
    it. An estimate, and labelled as one in the UI - the TC count is a
    high-water mark, so a lost TC flatters nobody.
    """
    duration = game.get("duration") or 0
    tcs = game.get("tc_count") or 0
    idle = game.get("tc_idle_seconds") or 0
    if duration <= 0 or tcs <= 0:
        return None
    capacity = duration * tcs
    return max(0.0, min(1.0, 1 - idle / capacity))


def nice_ticks(low, high, count=5):
    """Rounded axis tick values covering [low, high]."""
    if high <= low:
        return [low]
    raw = (high - low) / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    for step in (1, 2, 5, 10):
        if raw <= step * magnitude:
            step = step * magnitude
            break
    else:
        step = 10 * magnitude
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step / 1000:
        ticks.append(round(value, 10))
        value += step
    return ticks


# ---- the tabs -----------------------------------------------------------

def rows_as_html(rows):
    """(label, value, good) rows as colored HTML for a QLabel."""
    colors = {True: ON_PACE_COLOR, False: BEHIND_COLOR, None: TEXT}
    parts = ["<table cellspacing='6'>"]
    for label, value, good in rows:
        color = colors.get(good, TEXT)
        parts.append(
            f"<tr><td style='color: rgb(160,160,168);'>{label}</td>"
            f"<td style='color: rgb({color.red()},{color.green()},"
            f"{color.blue()});'><b>{value}</b></td></tr>")
    parts.append("</table>")
    return "".join(parts)


def game_rows(game):
    """The post-game summary as (label, value, good) rows."""
    rows = [("game length", format_time(game.get("duration", 0)), None),
            ("peak villagers", str(game.get("max_villagers", 0)), None),
            ("town centers (high water)", str(game.get("tc_count", 1)), None)]

    efficiency = tc_efficiency(game)
    if efficiency is not None:
        rows.append(("TC efficiency (estimate)", f"{efficiency:.0%}",
                     efficiency >= 0.9))
    rows.append(("TC idle time", f"{game.get('tc_idle_seconds', 0):.0f}s",
                 game.get("tc_idle_seconds", 0) <= 30))
    if game.get("housed_seconds", 0) > 0:
        rows.append(("housed", f"{game['housed_seconds']:.0f}s", False))
    if game.get("pop_capped_seconds", 0) > 0:
        rows.append(("at the pop cap",
                     f"{game['pop_capped_seconds']:.0f}s", None))

    deaths = game.get("deaths", [])
    if deaths:
        lost = sum(d[1] for d in deaths)
        raided = sum(d[1] for d in deaths if len(d) > 2 and d[2])
        note = f" ({raided} to raids)" if raided else ""
        rows.append((f"villagers lost", f"{lost}{note}", False))
    attacks = game.get("attacks", [])
    if attacks:
        rows.append((f"attacked ×{len(attacks)}",
                     ", ".join(format_time(t) for t in attacks[:5]), False))

    queued = game.get("queued", {})
    if queued:
        # First sightings in the production queue - NOT produced counts;
        # the queue hides duplicates and never reports completion.
        for identity, seen in sorted(queued.items(), key=lambda kv: kv[1]):
            rows.append((identity.replace("_", " "),
                         f"first queued {format_time(seen)}", None))
    alerts = game.get("alerts", [])
    if alerts:
        rows.append((f"alerts fired ×{len(alerts)}",
                     ", ".join(f"{a[1]} {format_time(a[0])}"
                               for a in alerts[:4]), None))

    # The notification feed, aggregated: "created:villager x23" reads
    # better than twenty-three rows, and the raw list stays in the file.
    # Labelled as what it is - lines SEEN in the feed - because events
    # can outpace the polls; this is a floor, not a census.
    events = game.get("events", [])
    if events:
        rows.append(("— seen in the notification feed —", "", None))
        grouped = {}
        for t, name in events:
            grouped.setdefault(name, []).append(t)
        for name, times in sorted(grouped.items(),
                                  key=lambda kv: kv[1][0]):
            label = name.replace(":", " ").replace("_", " ")
            if len(times) == 1:
                rows.append((label, f"at {format_time(times[0])}", None))
            else:
                rows.append((f"{label} ×{len(times)}",
                             f"first {format_time(times[0])}, "
                             f"last {format_time(times[-1])}", None))
    return rows


class ChartView(QWidget):
    """Three stacked charts over one shared game-time axis: villagers and
    population, pace delta, and APM when the counter ran."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timeline = None
        self.apm = None
        self.setMinimumSize(480, 420)

    def show_game(self, data):
        self.timeline = data.get("timeline") if data else None
        self.apm = data.get("apm") if data else None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), BACKGROUND)

        timeline = self.timeline
        if not timeline or not timeline.get("t"):
            painter.setPen(FAINT_TEXT)
            painter.setFont(QFont("sans", 11))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "no timeline in this game")
            return

        charts = [("villagers / population", self._draw_villagers),
                  ("pace (seconds behind)", self._draw_pace)]
        if self.apm and self.apm.get("t"):
            charts.append(("APM", self._draw_apm))

        margin = 12
        height = (self.height() - margin * (len(charts) + 1)) // len(charts)
        top = margin
        for title, draw in charts:
            draw(painter, margin, top, self.width() - 2 * margin, height,
                 title)
            top += height + margin

    # Each chart: a titled box with a time axis and one or two series.

    def _frame(self, painter, x, y, width, height, title):
        painter.setPen(BORDER)
        painter.drawRect(x, y, width, height)
        painter.setPen(FAINT_TEXT)
        painter.setFont(QFont("sans", 8, QFont.Weight.Bold))
        painter.drawText(x + 6, y + 14, title.upper())
        # Inner plotting rect, leaving room for the title and axis labels.
        return x + 44, y + 20, width - 52, height - 40

    def _time_axis(self, painter, plot_x, plot_y, plot_w, plot_h, t_max):
        painter.setFont(QFont("sans", 8))
        painter.setPen(FAINT_TEXT)
        for tick in nice_ticks(0, t_max, 6):
            px = plot_x + (tick / t_max) * plot_w if t_max else plot_x
            painter.drawText(int(px) - 12, plot_y + plot_h + 14,
                             format_time(tick))

    def _draw_series(self, painter, points, color, plot, t_max, lo, hi):
        plot_x, plot_y, plot_w, plot_h = plot
        span = (hi - lo) or 1
        pen = QPen(color)
        pen.setWidth(2)
        painter.setPen(pen)
        last = None
        for t, value in points:
            if value is None:
                last = None
                continue
            px = plot_x + (t / t_max) * plot_w if t_max else plot_x
            py = plot_y + plot_h - ((value - lo) / span) * plot_h
            if last is not None:
                painter.drawLine(int(last[0]), int(last[1]), int(px), int(py))
            last = (px, py)

    def _value_axis(self, painter, plot, lo, hi):
        plot_x, plot_y, plot_w, plot_h = plot
        painter.setFont(QFont("sans", 8))
        span = (hi - lo) or 1
        for tick in nice_ticks(lo, hi, 4):
            py = plot_y + plot_h - ((tick - lo) / span) * plot_h
            painter.setPen(QColor(255, 255, 255, 20))
            painter.drawLine(plot_x, int(py), plot_x + plot_w, int(py))
            painter.setPen(FAINT_TEXT)
            painter.drawText(plot_x - 38, int(py) + 3, f"{tick:g}")

    def _draw_villagers(self, painter, x, y, width, height, title):
        timeline = self.timeline
        plot = self._frame(painter, x, y, width, height, title)
        t_max = timeline["t"][-1] or 1
        villagers = [v for v in timeline["villagers"] if v is not None]
        caps = [c for c in timeline.get("pop_cap", []) if c is not None]
        hi = max(villagers + caps + [10])
        self._value_axis(painter, plot, 0, hi)
        self._time_axis(painter, *plot, t_max)
        if caps:
            self._draw_series(painter,
                              zip(timeline["t"], timeline["pop_cap"]),
                              FAINT_TEXT, plot, t_max, 0, hi)
            self._draw_series(painter, zip(timeline["t"], timeline["pop"]),
                              DIM_TEXT, plot, t_max, 0, hi)
        self._draw_series(painter, zip(timeline["t"], timeline["villagers"]),
                          ON_PACE_COLOR, plot, t_max, 0, hi)

    def _draw_pace(self, painter, x, y, width, height, title):
        timeline = self.timeline
        plot = self._frame(painter, x, y, width, height, title)
        t_max = timeline["t"][-1] or 1
        pace = [p for p in timeline.get("pace", []) if p is not None]
        if not pace:
            painter.setPen(FAINT_TEXT)
            painter.drawText(plot[0], plot[1] + 20, "no pace data")
            return
        lo, hi = min(pace + [0]), max(pace + [30])
        self._value_axis(painter, plot, lo, hi)
        self._time_axis(painter, *plot, t_max)
        # The zero line is the story: above it is behind, below is ahead.
        self._draw_series(painter, zip(timeline["t"], timeline["pace"]),
                          BEHIND_COLOR, plot, t_max, lo, hi)

    def _draw_apm(self, painter, x, y, width, height, title):
        plot = self._frame(painter, x, y, width, height, title)
        t_max = (self.timeline["t"][-1]
                 if self.timeline and self.timeline.get("t") else 1) or 1
        values = [v for v in self.apm["apm"] if v is not None]
        hi = max(values + [60])
        self._value_axis(painter, plot, 0, hi)
        self._time_axis(painter, *plot, t_max)
        self._draw_series(painter, zip(self.apm["t"], self.apm["apm"]),
                          APM_COLOR, plot, t_max, 0, hi)


class StatsWindow(QWidget):
    """Past games on the left, the selected game's three tabs on the right."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Loom — Statistics")
        self.resize(980, 640)

        self.games = QListWidget()
        self.games.setMaximumWidth(300)
        self.games.currentItemChanged.connect(self._show_selected)

        self.build_tab = self._label_tab()
        self.game_tab = self._label_tab()
        self.charts = ChartView()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_tab["scroll"], "Build report")
        self.tabs.addTab(self.game_tab["scroll"], "Post-game")
        self.tabs.addTab(self.charts, "Graphs")

        layout = QHBoxLayout(self)
        layout.addWidget(self.games)
        layout.addWidget(self.tabs, stretch=1)

    def _label_tab(self):
        label = QLabel()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setAlignment(Qt.AlignmentFlag.AlignTop)
        label.setWordWrap(True)
        label.setMargin(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(label)
        return {"label": label, "scroll": scroll}

    def refresh(self):
        """Re-read the stats folder, keeping the selection if possible."""
        selected = self.games.currentItem()
        keep = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.games.clear()
        for path, label, data in list_stats():
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.games.addItem(item)
            if keep == str(path):
                self.games.setCurrentItem(item)
        if self.games.currentItem() is None and self.games.count():
            self.games.setCurrentRow(0)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def _show_selected(self, current, _previous):
        if current is None:
            return
        data = load_stats(current.data(Qt.ItemDataRole.UserRole))
        if data is None:
            self.build_tab["label"].setText("This file could not be read.")
            self.game_tab["label"].setText("This file could not be read.")
            self.charts.show_game(None)
            return

        build = data.get("build")
        if build and build.get("rows"):
            self.build_tab["label"].setText(rows_as_html(build["rows"]))
        else:
            self.build_tab["label"].setText(
                "The build order was not completed in this game.")
        self.game_tab["label"].setText(
            rows_as_html(game_rows(data.get("game", {})))
            + "<p style='color: rgb(120,120,128);'>Game length means the"
            " last usable reading - the game does not announce its end."
            " Queue rows are first sightings, not produced counts.</p>")
        self.charts.show_game(data)
