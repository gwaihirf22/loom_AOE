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

from . import __version__, age, episodes, paths

SCHEMA = 2

# Every schema this reader can still open. Kept separate from SCHEMA, which
# is what it WRITES, because the two are different questions and conflating
# them is a one-character way to orphan every file already on disk: the
# statistics window tested `schema != SCHEMA`, so bumping the version alone
# would have made 265 recorded games "unreadable" in a single commit.
#
# 1 -> 2 added the optional "record" section. A version 1 file simply has
# no such key, which every consumer already handles, so nothing needs
# migrating and nothing is rewritten behind the author's back.
READABLE_SCHEMAS = (1, 2)

# Below this much observed game the file is not worth writing - a menu
# misread or an instantly-abandoned match, not a game.
MIN_DURATION = 60

# How often to rewrite the file while the game runs, in game seconds. A
# crash or SIGKILL then loses at most this much.
FLUSH_EVERY = 30

# Queue identities that are just villagers: the timeline already tells that
# story, so first-sighting them adds nothing.
VILLAGER_IDENTITIES = {"villager_male", "villager_female"}



def hud_meta(hud):
    """The reader's anchor, as the fields a stats file should carry.

    `hud` is HudReader.hud: profile, scale, score, frame, backdrop.
    """
    meta = {
        "profile": hud["profile"].name,
        "reference_scale": hud["scale"],
        "score": hud["score"],
        "frame": list(hud["frame"]),
    }

    if hud["backdrop"] is not None:
        meta["backdrop"] = hud["backdrop"]

    return meta


class GameRecorder:
    """Accumulates one whole game, then writes it as one JSON file."""

    def __init__(self, build_stem, build_name, started):
        """started is a wall-clock ISO string - display metadata only. All
        analysis time inside the file is game time, per the house rule."""
        self.meta = {"loom": __version__, "build": build_stem,
                     "build_name": build_name, "started": started}
        # WHICH BUILD wrote this, which meta.loom cannot say. The version
        # moves on releases; readers change between them, so files written
        # on either side of a reader fix carry the same version and a
        # corpus grouped by it silently mixes generations. Absent is a
        # legitimate answer and means "written before this was recorded".
        commit = paths.build_commit()
        if commit:
            self.meta["commit"] = commit
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
        self.army_losses = []          # (t, lost, raided) - non-villager pop
        self.max_army = 0
        self.attacks = []
        self.events = []               # (t, event name), every feed event
        # (t, "reached", age) from the HUD's own age crest. A genuinely new
        # statistic: the production queue has always seen the CLICK, because
        # the research sits in it, but a queue item that disappears has
        # either finished or been cancelled and those look identical. The
        # crest changing is the game stating what age you are in, so the
        # finish is a fact rather than an inference.
        #
        # ARRIVALS ONLY. Clicks were recorded here too until the graphs drew
        # them and showed what was really being written: the click is logged
        # on every poll that sees the red bar, not once per transition, so
        # one age-up left four entries and drew four rules. They are not
        # worth keeping even deduplicated (my ruling), so the filter is
        # here rather than a fix upstream - the overlay still needs clicks
        # for the age tracker, and this is the one consumer that does not.
        self.ages = []
        self.queued = {}               # identity -> first seen queued, t
        # Every item the queue was watched producing, decided once by vote
        # rather than believed on first sight. See loom/episodes.py.
        self._episodes = episodes.EpisodeTracker()
        self.alerts = []               # (t, text, severity) transitions only
        self.max_villagers = 0
        self.tc_count = 1
        self._last_time = None
        self._last_villagers = None
        self._last_army = None
        self._active_alerts = set()
        self._written_up_to = 0


    def describe_hud(self, hud):
        """Record what this game was READ FROM, once the anchor is found.

        Every other number in this file is about the game. These are about
        LOOM, and they are here because a stats file that cannot say what
        produced it cannot be diagnosed later. Two separate investigations
        have now needed exactly these fields and had to date commits
        instead: which skin wrote a run of impossible clocks, and whether a
        session with a twenty-minute misread was running the mod that
        breaks the clock reader.

        Called once. If the anchor is re-acquired mid-game the first answer
        stands - it is the one the readings were taken under.
        """
        if "hud" in self.meta or not hud:
            return
        self.meta["hud"] = hud_meta(hud)

    # ---- feeding -------------------------------------------------------

    def observe(self, game_time, villagers, delta, tracker=None,
                population=None, slots=None, game_events=None,
                alerts_list=None, age_events=()):
        """One usable poll's worth of believed state.

        Returns the production episodes that ENDED on this poll, which is
        the moment each one's vote is decided. The caller feeds those to
        the checklist; nothing here needs them.

        tracker is the ProductionTracker, or None when there is none (demo
        mode) - the timeline and pace still record, the queue-derived
        accumulators just stay empty.
        """
        if game_time is None:
            return ()
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
        for what, which in age_events or ():
            if what == age.REACHED:
                self.ages.append((moment, what, which))

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

        # Army losses, by the only definition Loom can honestly compute:
        # population minus villagers, falling. Both halves must have been
        # read or the difference means nothing - a subtraction with one
        # side missing is not a small army.
        #
        # A villager dying moves BOTH numbers, so it leaves this figure
        # alone and cannot be double-counted here. What it does include is
        # anything non-villager leaving the population: units killed, but
        # also deleted, converted, or a fishing ship sunk. That is the
        # game's own "units lost" definition and it is what the number is
        # called.
        #
        # It is only ever MY losses. The opponent's population is not on
        # my HUD and is not in the recorded game either, so kills stay
        # unknowable - see the note on the kind tabs.
        army = (None if population is None or villagers is None
                else max(0, population[0] - villagers))
        if (army is not None and self._last_army is not None
                and army < self._last_army):
            raided = bool(self.attacks) and moment - self.attacks[-1] <= 20
            self.army_losses.append((moment, self._last_army - army, raided))
        if army is not None:
            self._last_army = army
            self.max_army = max(self.max_army, army)

        # First sighting of everything the queue can name, villagers aside.
        #
        # KEPT, AND NO LONGER THE ONLY RECORD. This believes one glance:
        # anything the queue names once becomes a permanent fact, which put
        # nine things that never happened on a real Post-game page and 329
        # phantom subjects across the capture corpus. `episodes` below is
        # the replacement - it decides an item ONCE, by vote, from every
        # poll that watched it produce. This stays for one release so stats
        # files written by an older build, and every reader of them, keep
        # working.
        for slot in slots or []:
            if (slot.identity and slot.identity not in VILLAGER_IDENTITIES
                    and slot.identity not in self.queued):
                self.queued[slot.identity] = moment

        # What the queue was actually watching produce. Amber and red are
        # ignored - a waiting item has produced nothing - and an episode
        # nobody watched long enough reports no identity rather than its
        # best guess.
        # The return is not thrown away: an episode ENDING is the moment its
        # vote is decided, and the checklist wants those the same poll the
        # feed's events arrive. One tracker answers that question for both
        # the statistics and the panel - a second one in the overlay would
        # be the same question answered in two places, which is how a
        # feature ends up half-wired.
        finished = self._episodes.update(moment, slots)

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

        return finished

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
        """How far into the game the clock got. The game's LENGTH."""
        return self.t[-1] if self.t else 0

    def observed(self):
        """How much of it was actually watched, in game seconds.

        Not the same thing as the duration, and conflating them wrote a
        file for every restart. An overlay started at 44:00 and stopped
        five seconds later has a duration of 2645 - the clock really did
        say that - but it watched five seconds, and has nothing worth
        keeping. The author's stats folder held about a hundred such
        fragments, most of them a handful of timeline rows recorded at a
        constant villager count, because has_data asked the wrong one.

        It matters beyond the file count: statsview divides idle seconds by
        the duration times the Town Centre count to get a percentage, so a
        thirty-second fragment of a long game divided by forty-four minutes
        and reported a figure that meant nothing.
        """
        return (self.t[-1] - self.t[0]) if len(self.t) >= 2 else 0

    def has_data(self):
        """Worth a file? A menu misread or instant abandon is not - and
        neither is a few seconds of a game that was nearly over."""
        return self.observed() >= MIN_DURATION

    def due_flush(self):
        """Time for a crash-safety rewrite?"""
        return self.observed() - self._written_up_to >= FLUSH_EVERY

    def episodes(self):
        """Every production episode, closed and open alike.

        The schema everything downstream reads:

          subject   what the vote decided, or None when it refused
          started   game seconds, or None if the clock never read
          ended     game seconds
          polls     how many looks the vote had
          tally     identity -> summed identity score
          runner_up the identity that came second, or None

        `subject` being None is a REFUSAL and not an absence - the episode
        happened and was watched, and Loom declines to name it. A reader
        that drops those rows turns "I could not tell" into "nothing was
        there", which is the distinction this whole project is built on.

        Open episodes are included: a game ending mid-item is not the item
        never having existed.
        """
        found = []
        for episode in list(self._episodes.closed) + list(self._episodes.open):
            if not episode.tally:
                continue
            found.append({
                "subject": episode.identity,
                "started": episode.started,
                "ended": episode.ended,
                "polls": episode.polls,
                "tally": {name: round(score, 3)
                          for name, score in episode.tally.most_common()},
                "runner_up": episode.runner_up,
            })
        return found

    def to_dict(self):
        return {
            "schema": SCHEMA,
            "meta": dict(self.meta),
            "build": self.build_section,
            "game": {
                "duration": self.duration(),
                # How much of that was actually watched. Anything derived
                # per-second has to divide by THIS, not by the duration.
                "observed": self.observed(),
                "max_villagers": self.max_villagers,
                "tc_count": self.tc_count,
                "tc_idle_seconds": round(self.tc_idle_seconds, 1),
                "housed_seconds": round(self.housed_seconds, 1),
                "pop_capped_seconds": round(self.pop_capped_seconds, 1),
                "deaths": [list(d) for d in self.deaths],
                "army_losses": [list(d) for d in self.army_losses],
                "max_army": self.max_army,
                "attacks": list(self.attacks),
                "events": [list(e) for e in self.events],
                "ages": [list(a) for a in self.ages],
                "queued": dict(self.queued),
                "episodes": self.episodes(),
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
        self._written_up_to = self.observed()
