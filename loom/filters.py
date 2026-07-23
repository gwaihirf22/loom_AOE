"""
Loom — read filters.

Reading digits off the screen goes wrong occasionally: the game redraws
mid-capture, a unit walks over the HUD, a number is half-way through changing.
These filters stop a single bad reading from reaching the rest of the program.

There are two filters because the two numbers behave differently:

  * Villager count changes rarely, so I can insist on seeing the same value
    twice in a row before believing it.
  * Game time changes on EVERY poll, so insisting on two identical readings
    would mean the clock never updated at all. Instead I check that it moved
    forward by a sensible amount.

Both follow the same principle: never trust a single frame.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

class StableCount:
    """Filters a slowly-changing whole number, like the villager count.

    One rule: a value must appear twice in a row before I believe it. A
    half-drawn digit caught mid-redraw can still classify confidently as the
    wrong number, so I make a bad reading prove itself twice.

    I deliberately do NOT reject large changes. An earlier version refused any
    jump bigger than 3, which looked sensible until a new game started: the
    count went 22 -> 4, every reading was rejected as "impossible", and it was
    stuck on 22 forever. A briefly wrong count fixes itself on the next poll; a
    permanently stuck one silently desynchronises the whole build order.

    I also do not require the count to only ever rise. Villager count really
    does fall: a boar kills a villager, an early rush kills two. A rule of
    "can only go up" would ignore that forever and desynchronise permanently.
    """

    def __init__(self, required_repeats=2):
        self.required_repeats = required_repeats

        self.value = None        # the value I currently believe
        self._candidate = None   # a value I have seen but not yet accepted
        self._seen_count = 0     # how many times in a row I have seen it

    def update(self, reading):
        """Feed in one reading. Returns the currently believed value."""
        # An unreadable frame tells me nothing, so break any run in progress.
        if reading is None:
            self._candidate = None
            self._seen_count = 0
            return self.value

        # Count how many times in a row I have seen this same reading.
        if reading == self._candidate:
            self._seen_count += 1
        else:
            self._candidate = reading
            self._seen_count = 1

        if self._seen_count < self.required_repeats:
            return self.value

        # Seen it enough times. Accept it.
        self.value = reading
        return self.value


class StableClock:
    """Filters the game clock, in seconds.

    The clock normally creeps forward a little each poll. So:
      * A small step forward is accepted straight away.
      * Anything else - jumping backwards, or leaping ahead - needs a second
        reading to agree before I believe it.

    That second rule is what lets a genuine new game through. When a new match
    starts the clock drops to near zero, which looks exactly like a misread on
    a single frame, so I wait for confirmation instead of guessing.
    """

    def __init__(self, max_step=30, required_repeats=2):
        self.max_step = max_step
        self.required_repeats = required_repeats

        self.value = None
        self._candidate = None
        self._seen_count = 0

    def update(self, reading):
        """Feed in one reading, in seconds. Returns the believed game time."""
        if reading is None:
            self._candidate = None
            self._seen_count = 0
            return self.value

        # The very first reading has nothing to be compared against, so it
        # has to earn trust by repeating.
        if self.value is None:
            return self._confirm(reading)

        step = reading - self.value

        # Normal case: time crept forward a bit. Believe it immediately.
        if 0 <= step <= self.max_step:
            self.value = reading
            self._candidate = None
            self._seen_count = 0
            return self.value

        # Backwards, or a big leap. Could be a new game, could be a misread.
        return self._confirm(reading)

    def _confirm(self, reading):
        """Only accept a surprising reading if a later one backs it up.

        Two readings "agree" when the clock moved forward by a plausible
        amount between them - the same test used for normal ticking. I
        cannot compare them for equality, because the clock is still running
        while I wait for confirmation.

        Note this must not assume how often I poll. Polling four times a
        second moves the clock about half a second; a slower poll moves it
        several seconds. Both are equally valid, so the test is "did it move
        forward sensibly", not "is it within N seconds".
        """
        moved_forward_sensibly = (
            self._candidate is not None
            and 0 <= reading - self._candidate <= self.max_step
        )

        if moved_forward_sensibly:
            self._seen_count += 1
        else:
            self._candidate = reading
            self._seen_count = 1

        if self._seen_count >= self.required_repeats:
            # Trust the newer reading: it is the more current one.
            self.value = reading
            self._candidate = None
            self._seen_count = 0

        return self.value


def format_time(seconds):
    """900 -> '15:00'. Returns '--:--' for no reading yet."""
    if seconds is None:
        return "--:--"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
