"""
Loom — the build-complete report.

While the build runs, this quietly accumulates what actually happened: how
long Town Centres sat idle, when raids landed, when each observable
milestone (Loom, Wheelbarrow, the age-ups) first appeared in the production
queue, and how the pace delta moved. When the build's last step completes,
summary() turns it into the rows the overlay shows - the payoff screen.

Pure logic over streams other modules already produce, so it tests without
a game. Rows only ever report what was actually observed: a milestone the
queue never showed simply is not in the report, per the never-guess rule.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from . import build_order, production


def format_time(seconds):
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"


class BuildReport:
    """Accumulates during-the-build facts, then summarises them."""

    def __init__(self):
        self.tc_idle_seconds = 0.0
        self.attacks = []            # game times of attack warnings
        self.milestones = {}         # identity -> game time first seen queued
        self.worst_delta = None      # most behind the build ever ran
        self.final_delta = None      # behind/ahead at the completion moment
        self.max_extra = 0           # most villagers beyond the build's ask
        self.deaths = []             # (game time, villagers lost, raided?)
        self.completed_at = None
        self._last_time = None
        self._last_delta = None
        self._last_villagers = None

    def update(self, game_time, tracker, delta, slots, game_events,
               extra=0, villagers=None):
        """Feed in one poll's worth of believed state."""
        # The report covers THE BUILD. A raid at 16 minutes or a late Loom
        # after the last step belongs to the rest of the game, not to this
        # verdict - once complete() has been called, the book is closed.
        if self.completed_at is not None:
            return

        if game_time is not None and self._last_time is not None:
            elapsed = game_time - self._last_time
            # A backwards or absurd jump means a misread or a new game, not
            # eleven minutes of idleness.
            if 0 < elapsed <= 30 and tracker.idle_tcs > 0:
                self.tc_idle_seconds += tracker.idle_tcs * elapsed
        if game_time is not None:
            self._last_time = game_time

        # A raid is an attack warning WITHOUT the wild-animals phrase beside
        # it: boar lures and stray wolves trip the same warning, and they
        # are normal play, not news. The two phrases render together (the
        # attacker wraps onto the next line), so one glance sees both.
        events = game_events or []
        if (game_time is not None and "attacked" in events
                and "wild_animals" not in events):
            self.attacks.append(game_time)

        for slot in slots or []:
            if (slot.identity in production.TC_TECH_IDENTITIES
                    and slot.identity not in self.milestones
                    and game_time is not None):
                self.milestones[slot.identity] = game_time

        if delta is not None:
            self._last_delta = delta
            if self.worst_delta is None or delta > self.worst_delta:
                self.worst_delta = delta

        self.max_extra = max(self.max_extra, extra)

        # The villager stream is the FILTERED count, which needs two
        # agreeing polls to change - a drop that arrives here is a real
        # death, not a misread dip. Within 20 game-seconds of an attack
        # warning it gets attributed to the raid.
        if (villagers is not None and self._last_villagers is not None
                and villagers < self._last_villagers
                and game_time is not None):
            raided = bool(self.attacks) and game_time - self.attacks[-1] <= 20
            self.deaths.append((game_time,
                                self._last_villagers - villagers, raided))
        if villagers is not None:
            self._last_villagers = villagers

    def milestone_queued(self, step):
        """Is this step's named milestone already sighted in the queue?

        Drives the overlay's quiet "✓ queued" chip: the step says click
        Feudal, and the queue shows the age-up underway.
        """
        if step is None:
            return False
        text = " ".join([step.details] + step.footnotes).lower()
        for phrase, identity in build_order.MILESTONE_WORDS.items():
            if phrase in text and identity in self.milestones:
                return True
        return False

    def complete(self, game_time):
        """The build's last step just finished; freeze the verdict."""
        if self.completed_at is None:
            self.completed_at = game_time
            # The pace meter retires with None on the completing poll, so
            # the verdict is the last delta it actually reported.
            self.final_delta = self._last_delta

    def summary(self, build):
        """The report as rows of (label, value, good) for display.

        good is True/False for coloured rows, None for neutral ones.
        """
        rows = []
        if self.completed_at is not None:
            rows.append(("Build complete", format_time(self.completed_at),
                         None))

        if self.final_delta is not None:
            behind = self.final_delta
            if behind > 15:
                rows.append(("vs perfect build", f"{behind:.0f}s behind",
                             False))
            elif behind < -15:
                rows.append(("vs perfect build", f"{-behind:.0f}s ahead",
                             True))
            else:
                rows.append(("vs perfect build", "on pace", True))
        if self.max_extra > 0:
            rows.append(("extra villagers",
                         f"+{self.max_extra} beyond the build", False))
        if self.worst_delta is not None and self.worst_delta > 15:
            rows.append(("worst moment", f"{self.worst_delta:.0f}s behind",
                         None))
        if self.deaths:
            lost = sum(count for _, count, _ in self.deaths)
            raids = [format_time(t) for t, _, raided in self.deaths if raided]
            note = f" (raid at {', '.join(raids[:3])})" if raids else ""
            rows.append((f"villagers lost ×{lost}",
                         ", ".join(format_time(t)
                                   for t, _, _ in self.deaths[:4]) + note,
                         False))

        idle = int(round(self.tc_idle_seconds))
        rows.append(("TC idle time", f"{idle}s", idle <= 10))

        if self.attacks:
            times = ", ".join(format_time(t) for t in self.attacks[:4])
            rows.append((f"attacked ×{len(self.attacks)}", times, False))

        targets = build_order.milestone_targets(build)
        for identity, seen in sorted(self.milestones.items(),
                                     key=lambda kv: kv[1]):
            target = targets.get(identity)
            if target is None:
                continue
            slip = seen - target
            label = identity.replace("_", " ")
            if abs(slip) <= 20:
                rows.append((label, f"{format_time(seen)}  on time", True))
            else:
                sign = "late" if slip > 0 else "early"
                rows.append((label,
                             f"{format_time(seen)}  {abs(slip):.0f}s {sign}",
                             slip <= 20))
        return rows
