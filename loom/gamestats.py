"""
Loom — per-game statistics, written to one JSON file per match.

BuildReport judges THE BUILD and closes its book at the last step. This
module keeps watching for the whole game: a per-second timeline for graphs,
full-game accumulators (TC idle seconds, housed seconds, villager deaths,
peak villagers), when each unit and technology first appeared in the
production queue, and every alert that fired. At build completion it also
freezes a copy of the BuildReport verdict, so one file holds both stories -
the build and the game - as separate sections, which is exactly how the
launcher's statistics window shows them.

Honesty rules carried over from the rest of Loom:

* "queued" times are FIRST SIGHTINGS in the production queue, not produced
  counts. The queue hides duplicate groups and never says what finished, so
  a count would be a guess, and Loom does not guess.
* There is no game-ended event (quitting, pausing and alt-tabbing look
  identical from the pixels), so "duration" means the last usable reading.
* The schema carries a version number so a future reader - or the Windows
  build - can tell what it is looking at.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import os

from . import __version__

SCHEMA = 1

# Below this much observed game the file is not worth writing - a menu
# misread or an instantly-abandoned match, not a game.
MIN_DURATION = 60

# How often to rewrite the file while the game runs, in game seconds. A
# crash or SIGKILL then loses at most this much.
FLUSH_EVERY = 30

# Queue identities that are just villagers: the timeline already tells that
# story, so first-sighting them adds nothing.
VILLAGER_IDENTITIES = {"villager_male", "villager_female"}


class GameRecorder:
    """Accumulates one whole game, then writes it as one JSON file."""

    def __init__(self, build_stem, build_name, started):
        """started is a wall-clock ISO string - display metadata only. All
        analysis time inside the file is game time, per the house rule."""
        self.meta = {"loom": __version__, "build": build_stem,
                     "build_name": build_name, "started": started}
        self.build_section = None      # frozen BuildReport verdict
        # Timeline columns, one entry per observed whole game-second.
        self.t = []
        self.villagers = []
        self.pace = []
        self.idle_tcs = []
        self.pop = []
        self.pop_cap = []
        # Full-game accumulators.
        self.tc_idle_seconds = 0.0
        self.housed_seconds = 0.0
        self.pop_capped_seconds = 0.0
        self.deaths = []               # (t, lost, raided)
        self.attacks = []
        self.events = []               # (t, event name), every feed event
        self.queued = {}               # identity -> first seen queued, t
        self.alerts = []               # (t, text, severity) transitions only
        self.max_villagers = 0
        self.tc_count = 1
        self._last_time = None
        self._last_villagers = None
        self._active_alerts = set()
        self._written_up_to = 0

    # ---- feeding -------------------------------------------------------

    def observe(self, game_time, villagers, delta, tracker=None,
                population=None, slots=None, game_events=None,
                alerts_list=None):
        """One usable poll's worth of believed state.

        tracker is the ProductionTracker, or None when there is none (demo
        mode) - the timeline and pace still record, the queue-derived
        accumulators just stay empty.
        """
        if game_time is None:
            return
        moment = int(game_time)

        # Integrals over elapsed game time, with the same guard BuildReport
        # uses: a backwards or absurd jump is a misread or a new game, not
        # eleven minutes of idleness.
        if self._last_time is not None:
            elapsed = game_time - self._last_time
            if 0 < elapsed <= 30 and tracker is not None:
                if tracker.idle_tcs > 0:
                    self.tc_idle_seconds += tracker.idle_tcs * elapsed
                if tracker.blocked == "housed":
                    self.housed_seconds += elapsed
                elif tracker.blocked == "pop_capped":
                    self.pop_capped_seconds += elapsed
        self._last_time = game_time

        if tracker is not None:
            self.tc_count = max(self.tc_count, tracker.tcs_seen)

        # Raids, same reading as BuildReport: an attack warning without the
        # wild-animals phrase beside it (boar lures trip the same warning).
        events = game_events or []
        if "attacked" in events and "wild_animals" not in events:
            self.attacks.append(moment)
        # Every event the notification feed stated, verbatim with its time -
        # the whole point of reading the feed as text. The derived facts
        # above stay derived; this is the raw record.
        for name in events:
            self.events.append((moment, name))

        # Deaths, full game. The villager stream is the filtered count, so
        # a drop that arrives here is a real death, not a misread dip.
        if (villagers is not None and self._last_villagers is not None
                and villagers < self._last_villagers):
            raided = bool(self.attacks) and moment - self.attacks[-1] <= 20
            self.deaths.append((moment,
                                self._last_villagers - villagers, raided))
        if villagers is not None:
            self._last_villagers = villagers
            self.max_villagers = max(self.max_villagers, villagers)

        # First sighting of everything the queue can name, villagers aside.
        for slot in slots or []:
            if (slot.identity and slot.identity not in VILLAGER_IDENTITIES
                    and slot.identity not in self.queued):
                self.queued[slot.identity] = moment

        # Alerts as transitions: the moment a warning APPEARS is the story;
        # re-recording it every poll would just be the poll rate.
        current = set()
        for text, severity in alerts_list or []:
            # Idle-TC text carries a running duration; strip it so the same
            # ongoing alert is one transition, not one per second.
            key = text.split(" — ")[0]
            current.add(key)
            if key not in self._active_alerts:
                self.alerts.append((moment, key, severity))
        self._active_alerts = current

        # The timeline: one row per whole game-second.
        if not self.t or moment > self.t[-1]:
            self.t.append(moment)
            self.villagers.append(villagers)
            self.pace.append(None if delta is None else round(delta))
            self.idle_tcs.append(tracker.idle_tcs if tracker else 0)
            self.pop.append(population[0] if population else None)
            self.pop_cap.append(population[1] if population else None)

    def snapshot_build(self, report, build):
        """Freeze the build verdict at completion. Idempotent."""
        if self.build_section is None:
            self.build_section = {
                "rows": [list(row) for row in report.summary(build)],
                "completed_at": report.completed_at,
                "final_delta": report.final_delta,
                "worst_delta": report.worst_delta,
                "tc_idle_seconds": round(report.tc_idle_seconds, 1),
                "deaths": [list(d) for d in report.deaths],
                "milestones": dict(report.milestones),
            }

    # ---- writing -------------------------------------------------------

    def duration(self):
        return self.t[-1] if self.t else 0

    def has_data(self):
        """Worth a file? A menu misread or instant abandon is not."""
        return self.duration() >= MIN_DURATION

    def due_flush(self):
        """Time for a crash-safety rewrite?"""
        return self.duration() - self._written_up_to >= FLUSH_EVERY

    def to_dict(self):
        return {
            "schema": SCHEMA,
            "meta": dict(self.meta),
            "build": self.build_section,
            "game": {
                "duration": self.duration(),
                "max_villagers": self.max_villagers,
                "tc_count": self.tc_count,
                "tc_idle_seconds": round(self.tc_idle_seconds, 1),
                "housed_seconds": round(self.housed_seconds, 1),
                "pop_capped_seconds": round(self.pop_capped_seconds, 1),
                "deaths": [list(d) for d in self.deaths],
                "attacks": list(self.attacks),
                "events": [list(e) for e in self.events],
                "queued": dict(self.queued),
                "alerts": [list(a) for a in self.alerts],
            },
            "timeline": {
                "t": self.t, "villagers": self.villagers, "pace": self.pace,
                "idle_tcs": self.idle_tcs, "pop": self.pop,
                "pop_cap": self.pop_cap,
            },
            "apm": None,   # the launcher fills this in after the game
        }

    def write(self, path):
        """Write the whole file. Called at build completion, periodically
        for crash safety, and at exit - a rewrite each time, so the file on
        disk is always a complete, valid document."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1)
            handle.write("\n")
        self._written_up_to = self.duration()
