"""
Loom — the overlay's forensic log, one file per session.

The overlay's console output dies with its console - and the shipped exe
has no console at all - so a live failure used to leave no evidence: the
villager count froze at 6 for a whole game and afterwards there was nothing
anywhere saying whether the band read wrong, read nothing, or was never
looked at. This file is the answer to that. One compact line per poll, raw
readings beside believed ones, so "what did the reader actually see" is a
question the disk can answer after the fact.

Always on, because nobody turns on debug logging BEFORE the bug. The cost
is small on purpose: a 45-minute game is about 1,300 lines, and old logs
are pruned so the folder never grows past KEEP_LOGS sessions.

Writing a log must never cost a game. Every write is guarded; a full disk
or a locked folder turns logging off for the session and the overlay plays
on.
"""

import time

from . import filters, paths

KEEP_LOGS = 20


def describe_reading(reading, alerts_list=None):
    """One poll as one compact line: believed values beside raw ones.

    The raw column is the whole point - a held belief and a fresh read
    print the same number, and telling those apart is exactly what the
    frozen-count investigation could not do from the outside.
    """
    raw_v = "-" if reading.raw_villagers is None else reading.raw_villagers
    raw_c = ("-" if reading.raw_clock is None
             else filters.format_time(reading.raw_clock))
    pop = ("-" if reading.population is None
           else f"{reading.population[0]}/{reading.population[1]}")
    queue = ("-" if reading.queue is None else len(reading.queue))
    parts = [
        f"t {filters.format_time(reading.game_time)} raw {raw_c}",
        f"vill {'-' if reading.villagers is None else reading.villagers}"
        f" raw {raw_v}",
        f"pop {pop}",
        f"q {queue}",
    ]
    if reading.villager_gap:
        parts.append(f"UNREAD {int(reading.villager_gap)}s")
    if reading.event:
        parts.append(f"EVENT {reading.event}")
    for game_event in reading.game_events:
        parts.append(f"feed {game_event}")
    if alerts_list:
        parts.append("alert " + ",".join(
            f"{text}:{severity}" for text, severity in alerts_list))
    return " | ".join(parts)


class NullLog:
    """The same interface, writing nothing.

    What a controller gets when nobody asked for a log - the tests build
    hundreds of controllers, and every one opening a real file would spend
    the KEEP_LOGS quota on junk and prune away the sessions that matter.
    """

    path = None

    def line(self, text):
        pass

    def poll(self, reading, alerts_list=None):
        pass

    def close(self):
        pass


class SessionLog:
    """A per-session log file under the player's data directory.

    Construction opens the file and prunes old sessions; failure at any
    point leaves a silent no-op logger rather than an error, because the
    log exists to help the overlay and must never be able to hurt it.
    """

    def __init__(self, stem="overlay", directory=None, keep=KEEP_LOGS):
        self._file = None
        try:
            directory = (paths.DATA_DIR / "logs" if directory is None
                         else directory)
            directory.mkdir(parents=True, exist_ok=True)
            self._prune(directory, stem, keep)
            name = f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.log"
            self.path = directory / name
            self._file = open(self.path, "a", encoding="utf-8")
        except OSError:
            self.path = None

    @staticmethod
    def _prune(directory, stem, keep):
        """Keep the newest sessions, delete the rest.

        Sorted by name rather than mtime: the timestamp is IN the name, and
        file times are whatever the OS and a syncing OneDrive left them.
        """
        logs = sorted(directory.glob(f"{stem}_*.log"))
        for old in logs[:max(0, len(logs) - (keep - 1))]:
            try:
                old.unlink()
            except OSError:
                pass

    def line(self, text):
        """Write one wall-clock-stamped line. Never raises."""
        if self._file is None:
            return
        try:
            self._file.write(f"{time.strftime('%H:%M:%S')}  {text}\n")
            self._file.flush()
        except OSError:
            # The disk said no. Say nothing and stop trying; the overlay
            # matters more than its diary.
            self._file = None

    def poll(self, reading, alerts_list=None):
        self.line(describe_reading(reading, alerts_list))

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
