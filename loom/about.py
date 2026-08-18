"""
Loom — the How-to-use window.

A few pages of plain guidance, shown once on a fresh install and reachable
from the launcher's "How to use" button afterwards.

The one page that earns the interruption is HUD compatibility. Loom reads the
HUD by matching pictures of its icons, so a UI mod that redraws the resource
bar changes what Loom is looking for. Two skins are supported - stock and
Anne_HK - and loom/hud.py picks between them at HUD acquisition. A third skin
is not read at all, and the failure looks like Loom hanging rather than
misreading, which is exactly the sort of thing a player blames on the program
unless somebody tells them first.

PAGES is a plain list of (title, html) pairs. Adding a page is appending to
it; nothing else knows how many there are.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from . import config, paths
from .overlay import BACKGROUND, BORDER, DIM_TEXT, TEXT

WINDOW_SIZE = (620, 520)


PAGES = [
    ("What Loom does", """
<h3>A build order that follows your game</h3>
<p>Loom watches the game's HUD and shows you the step you should be on -
plus whether you are on pace, where your villagers are versus where the
build wants them, and whether a Town Centre has gone idle or a house is
due.</p>
<p>It reads all of that off the screen: your <b>villager count</b> and the
<b>game clock</b>, your <b>population and housing</b>, your
<b>villagers on each resource</b>, the <b>production queue</b>, and the
game's own <b>event messages</b>. One thing is deliberate about how those
are used: only the villager count and the clock ever decide <i>which step
you are on</i>. Everything else informs alerts and statistics, so a
misread resource number can never drag the whole build off course.</p>
<p>It works entirely by looking at pixels. Loom never reads the game's
memory, never injects anything, and never sends it input. If it cannot see
the HUD clearly it says so rather than guessing, because a wrong villager
count would quietly put you on the wrong step for the rest of the match.</p>
<p>The overlay draws on top of the game and is invisible to the mouse, so
you can click straight through it. After each match, the <b>Statistics</b>
window has the full story: graphs of your villagers, pace, idle time and
actions per minute, game by game.</p>
"""),

    ("Which HUD skins work", """
<h3>This is the setting that matters most</h3>
<p>Loom finds the HUD by matching pictures of its icons, so the artwork of
your resource bar matters. Two skins are supported, and Loom works out which
one is on screen by itself when a match starts:</p>
<p><b>The stock resource panel</b> — the game as it ships. Supported.</p>
<p><b>Anne_HK — Better UI</b> — supported, and what Loom was originally
built against.</p>
<p>Any other UI mod that <i>replaces the resource-bar artwork</i> needs its
own profile before Loom can read it. It will not fail silently: it says so,
and names the closest skin it found and how well it scored. A mod that only
changes colours or transparency without redrawing the icons will usually be
fine.</p>
<h3>Two mods worth installing (optional)</h3>
<p><b><a href="https://www.ageofempires.com/mods/details/3762">Anne_HK —
Better UI</a></b> is the layout Loom was originally built against: it gives
the HUD more room and standardises where things sit, which makes every read
a little easier. Fully supported — Loom detects it automatically.</p>
<p><b><a href="https://www.ageofempires.com/mods/details/2532">The
transparent-UI mod</a></b> clears the civilization border artwork from
around the HUD. That artwork is drawn differently for every civ and is the
main source of reading trouble — so where the mod covers your civ, the
trouble simply is not there. One honest caveat: it does not cover every
civ, and the NEWEST civs — the very ones whose artwork causes the most
trouble — are the least likely to be covered yet.</p>
<p>Both are recommended, neither is required. Loom reads the stock HUD as
it ships.</p>
<p><b>Set the in-game HUD scale to 100%.</b> Loom follows the HUD at other
sizes, but it reads best at 100%, and below about 90% Loom may not find the
HUD at all — keep the slider at 90% or above. (It often reports 99% however
you set it — that is fine.) If you change the scale, or switch UI mods,
restart the overlay so it measures the new one.</p>
"""),

    ("Getting started", """
<h3>Four things, once</h3>
<p><b>1. Pick a build.</b> Choose it at the top of the launcher. The preview
window beside the launcher shows the whole build; during a match it follows
along on its own.</p>
<p><b>2. Place the panel.</b> Use <b>Place overlay</b> to drag the panel
wherever you want it, then close it - no game needed, though with one
running it lines up exactly. Loom remembers the spot relative to the game
window, so it survives a resolution change or a move to another monitor.
<b>Reset position</b> puts it back in the top-right corner if it ever ends
up somewhere unhelpful.</p>
<p><b>3. Start the overlay</b> before or during a match - either is fine. It
waits for the game, then picks up wherever the match already is.</p>
<p><b>4. Check the alerts.</b> Idle Town Centre and housing warnings can be
turned off individually if you would rather not see them.</p>
<p>Loom writes a statistics file for every game, which the
<b>Statistics</b> button opens.</p>
"""),

    ("Reading the panel", """
<h3>What the overlay is telling you</h3>
<p><b>The big line</b> is the step to do now, with its details beneath and
its deadline to the right ("by 7:30 &middot; 22 vills"). The <b>THEN</b>
row underneath is the step after it, so you can read ahead.</p>
<p><b>The VILLS row</b> is your villagers per resource against what the
build wants, each resource in its own colour. A number goes white with a
red underline when you are more than one villager off the plan.</p>
<p><b>The pace chip</b>, top right, is measured every time a villager
arrives: <span style="color:#78dc82;">green</span> on pace,
<span style="color:#78c8eb;">blue</span> ahead,
<span style="color:#ebc864;">yellow</span> a little behind,
<span style="color:#f07869;">red</span> behind.</p>
<p><b>Alert bands</b> appear BELOW the panel, so the step you are reading
never jumps: a flashing red band for an idle Town Centre or being housed,
a steady amber one for the gentler warnings. Every family of alert can be
switched off in the launcher.</p>
<p>If the panel says <b>MANUAL</b> across the top, it has stopped following
the game because you told it to - see "Nudging the step". And when Loom
cannot read the HUD at all it says <i>waiting for the game</i> rather than
showing stale advice.</p>
"""),

    ("Placing and appearance", """
<h3>Put it where you want it - and make it as subtle as you like</h3>
<p><b>Place overlay</b> opens the panel as a normal draggable window: drag
it anywhere, close it, and the spot is saved. You do not need the game
running, though with it running the position lines up exactly. The saved
spot is relative to the game window, so it survives resolution changes.
<b>Reset position</b> returns it to the default - top right, tucked under
the game's bar - and if a saved spot would ever land off your screens
entirely, Loom ignores it and uses the default rather than vanishing.</p>
<p><b>Overlay size</b> has two knobs: overall size grows the whole panel,
text size grows only the writing (the panel gets taller, never wider).</p>
<p><b>Overlay transparency</b> has two sliders. <b>Background</b> is the
dark card behind the writing: 100% is solid, 0% removes it entirely.
<b>Text &amp; icons</b> fades the writing below 50% and makes it brighter
and bolder above it - useful over bright terrain with the card thinned.
A combination the author likes: background around <b>20%</b> with text at
<b>90%</b> - a faint card with vivid writing - but it is entirely your
taste. Alert bands always stay at full strength; they are alarms.</p>
"""),

    ("Nudging the step", """
<h3>When Loom is on the wrong step</h3>
<p>Loom follows your villager count and the game clock, so almost always it
is already on the right step. When it is not — an odd build, a reading it
could not make — you can move it by hand.</p>
<p><b>Ctrl+Shift+W</b> — forward one step.<br>
<b>Ctrl+Shift+Q</b> — back one step.</p>
<p>These are a <i>correction</i>, not a mode. After you press one, Loom stops
following the game for ten seconds so you can read the step, then picks the
game back up by itself. You never have to switch anything back on. The ten
seconds is adjustable in the launcher.</p>
<p><b>Ctrl+Shift+R</b> — stop following the game, or start again.</p>
<p>Unlike the two step keys, this one does not time out. While it is off, the
overlay drives on your keys alone and the panel says <b>MANUAL</b> across the
top, naming the key that gets you back. A new match always returns to
following the game.</p>
<p>There is also an optional <b>start/stop overlay</b> key — one key doing
what the launcher's Start and Stop buttons do, so the overlay can be launched
mid-game without alt-tabbing. It ships unbound; give it keys in the launcher
to switch it on.</p>
<h3>Changing them</h3>
<p>All of these are editable in the launcher, under <b>Build-order
hotkeys</b>, and any of them can be left empty to switch that action off.
There is also a single <b>Use hotkeys</b> switch for all of them.</p>
<p>Worth knowing: these are registered with the operating system, so
<b>while Loom is running, the game does not see them</b>. If one of them
clashes with a hotkey you use in Age of Empires, change it here — Loom will
also tell you in the launcher's output if a combination is already taken by
another program.</p>
"""),

    ("Settings and alerts", """
<h3>Tuning what Loom says, and when</h3>
<p><b>Alerts.</b> The idle Town Centre warning tapers as your economy
matures: you choose the villager count where it softens and the one where
it goes quiet - late game, an idle TC is often deliberate. <b>HOUSE
SOON</b> warns while a house can still prevent the stall; you choose how
much population space is left when it speaks. <b>HOUSED</b> means
production has actually hit the wall. Each family has its own switch.</p>
<p><b>A step key holds sync off for</b> - how long the hotkeys pause
automatic following before Loom picks the game back up (ten seconds unless
you say otherwise).</p>
<p><b>Track APM</b> counts your keystrokes and clicks for the post-game
graphs. It counts and nothing more - Loom never knows <i>which</i> key was
pressed; the code cannot see it, by construction.</p>
<p><b>More build orders.</b> Loom uses the community's own format - RTS
Overlay JSON - so builds made elsewhere load unchanged. Browse ready-made
builds at <a href="https://buildorderguide.com">buildorderguide.com</a>,
or design your own with the
<a href="https://rts-overlay.github.io">RTS Overlay web tool</a> and save
the JSON. Drop the files into Loom's builds folder and the launcher lists
them at its next start.</p>
<p>Settings apply <b>the next time the overlay starts</b> - a running
overlay keeps what it launched with. The one exception is the start/stop
hotkey, which re-registers the moment you change it.</p>
"""),
]


class AboutWindow(QWidget):
    """The paged How-to-use window.

    A parentless top-level window rather than a QDialog, matching how the
    statistics window works - nothing here needs to block the launcher, and a
    player should be able to leave this open while they set Loom up.
    """

    def __init__(self, parent=None):
        # Parented to the launcher, with the Window flag so it stays a real
        # window rather than becoming a panel inside it. The parent is what
        # keeps it IN FRONT: this window is smaller than the launcher, and
        # unparented it opened perfectly hidden behind it - the launcher was
        # 896x715, this is 620x548, and a help window nobody can see is worse
        # than no help window at all. Exactly the bug the build preview had.
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Loom — How to use")
        self.resize(*WINDOW_SIZE)
        self.page = 0
        self._placed = False

        self.title = QLabel()
        title_font = self.title.font()
        title_font.setPointSize(title_font.pointSize() + 3)
        title_font.setBold(True)
        self.title.setFont(title_font)

        # The banner, small, beside every page's title. isNull covers a
        # clone with the image stripped: the window must not care.
        self.logo = QLabel()
        emblem = QPixmap(str(paths.LOGO_PATH))
        if not emblem.isNull():
            self.logo.setPixmap(emblem.scaledToHeight(
                44, Qt.TransformationMode.SmoothTransformation))

        self.body = QLabel()
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.body.setWordWrap(True)
        self.body.setMargin(10)
        self.body.setOpenExternalLinks(True)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.body)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {BACKGROUND.name()};"
            f" border: 1px solid {BORDER.name()}; }}"
            f" QLabel {{ color: {TEXT.name()}; }}")

        self.counter = QLabel()
        self.counter.setStyleSheet(f"color: {DIM_TEXT.name()};")

        self.back = QPushButton("< Back")
        self.back.clicked.connect(lambda: self._go(self.page - 1))
        self.forward = QPushButton("Next >")
        self.forward.clicked.connect(lambda: self._go(self.page + 1))

        # Ticked means "stop showing this on startup". Reading the current
        # setting rather than assuming, so reopening from the launcher shows
        # the truth instead of an unticked box that would undo the choice on
        # close.
        self.dont_show = QCheckBox("Don't show this automatically")
        self.dont_show.setChecked(config.about_seen())
        self.dont_show.toggled.connect(config.set_about_seen)

        close = QPushButton("Close")
        close.clicked.connect(self.close)

        buttons = QHBoxLayout()
        buttons.addWidget(self.back)
        buttons.addWidget(self.forward)
        buttons.addWidget(self.counter)
        buttons.addStretch()
        buttons.addWidget(self.dont_show)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        heading.addWidget(self.logo)
        heading.addWidget(self.title)
        heading.addStretch()
        layout.addLayout(heading)
        layout.addWidget(scroll, stretch=1)
        layout.addLayout(buttons)

        self._go(0)

    def _go(self, page):
        """Show a page, clamped - the ends of the list are the ends."""
        self.page = max(0, min(page, len(PAGES) - 1))
        title, html = PAGES[self.page]
        self.title.setText(title)
        self.body.setText(html)
        self.counter.setText(f"page {self.page + 1} of {len(PAGES)}")
        self.back.setEnabled(self.page > 0)
        self.forward.setEnabled(self.page < len(PAGES) - 1)

    def open_at(self, position):
        """Show the window, offset from the launcher the first time.

        Offset rather than centred, so it reads as a window in front of the
        launcher rather than a panel that replaced it - and offset rather
        than placed beside, because the build preview already claims the
        space beside the launcher and two windows fighting for one slot is
        how this whole class of bug started.
        """
        if not self._placed and position is not None:
            self.move(*position)
            self._placed = True
        self.show()
        self.raise_()
        self.activateWindow()

    def show_first_run(self, position=None):
        """Open this because it has never been seen, and mark it seen.

        Marked on the way in, not on close: someone who reads the first page
        and dismisses the window has been told, and a program that keeps
        reappearing after being closed is a program people learn to resent.
        The checkbox stays available for anyone who wants it back.
        """
        config.set_about_seen(True)
        self.dont_show.setChecked(True)
        self._go(0)
        self.open_at(position)
