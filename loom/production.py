"""
Loom — production tracking over the global queue.

queue.py answers "what does the queue show right now". This turns that into a
sense of what is *happening* to production: whether everything has gone idle,
whether it recovered, and whether production is blocked by housing or the
population cap. It is pure logic over a stream of per-poll readings, so it is
unit-testable without a game, exactly like session.py.

Debouncing lives here, not in queue.py: a reading is one glance, and one
glance can be wrong (a menu fade, a mid-shift frame). A state only changes
after the same thing is seen twice in a row, which follows the filter
philosophy: a value that repeats is believed, and nothing can get stuck,
because the belief is only ever two agreeing polls away from the truth.

The idle event carries when the idleness *started* (in game time), so the
alert layer can grade severity by duration instead of just nagging.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# The events this class reports.
PRODUCTION_IDLE = "production_idle"          # queue went empty: nothing anywhere
PRODUCTION_RESUMED = "production_resumed"    # something is producing again
HOUSED = "housed"                            # a group is blocked: build a house
# No longer emitted by the tracker: the frame audit showed the amber wash on
# any merely-waiting item, so it cannot mean "at the population cap". The
# constant remains because the alert policy's severity table names it.
POP_CAPPED = "pop_capped"
UNBLOCKED = "unblocked"                      # no group is blocked any more
TC_IDLE = "tc_idle"                          # fewer TCs working than I know exist
TC_RECOVERED = "tc_recovered"                # every known TC is working again

# How many agreeing polls before a state change is believed.
POLLS_TO_BELIEVE = 2

# How long queue evidence must hold, on every poll without a gap, before it
# may add a Town Centre to the believed count.
QUEUE_TC_CONFIRM_SECONDS = 3

# Queue evidence may only ADD Town Centres during the opening. It exists
# for one job: noticing a multi-TC start (scenarios, nomad-style setups)
# before anything has been built. Past the opening, every new TC announces
# itself with "--Town Center Built--", and the notification channel is the
# only one allowed to raise the count - a queue misread that mints a
# phantom TC nags for the whole game, while a TC the queue merely failed
# to corroborate costs nothing.
QUEUE_TC_WINDOW_SECONDS = 120

# Identities that mean a Town Centre is doing something. Only one group per
# building can be green at a time, so each green TC-unit group IS one TC
# actively training - which is how I count TCs without ever seeing one: the
# most green groups ever on screen at once is how many TCs I know about.
# A green group of one of the TC techs means a TC is busy researching rather
# than training; that is still a working TC, not an idle one.
#
# TC units are villagers plus exactly one exception: Flemish Militia, which
# Burgundians train at the TC after Flemish Revolution. TC techs are the
# complete set from the tech tree - Dark: Loom; Feudal: Town Watch,
# Wheelbarrow, Feudal->Castle; Castle: Town Patrol, Hand Cart,
# Castle->Imperial. Unique techs never show here: they research at the
# Castle, however much they affect the TC.
TC_UNIT_IDENTITIES = {"villager_male", "villager_female", "flemish_militia"}
TC_TECH_IDENTITIES = {"loom", "town_watch", "town_patrol", "wheelbarrow",
                      "hand_cart", "feudal_age", "castle_age", "imperial_age"}


class ProductionTracker:
    """Tracks the production queue's state across polls and reports changes.

    Feed it the game time and the slot readings once per poll; it returns a
    list of event strings (usually empty). Pass slots=None when the queue was
    unreadable this poll - unreadable is not the same as empty, so it never
    changes any state.
    """

    def __init__(self, polls_to_believe=POLLS_TO_BELIEVE):
        self.polls_to_believe = polls_to_believe

        # What Loom currently believes.
        self.idle = False
        self.idle_since = None        # game time when the queue went empty
        self.blocked = None           # None | 'housed' | 'pop_capped'
        self.slots = []               # the last believed readings

        # Town Centres. tcs_seen is a high-water mark: the most TC-work
        # groups (training or researching) ever simultaneously green. It only
        # grows during a game - reset() clears it - so a TC lost to a raid
        # leaves it overcounting; the villager-count policy in alerts.py is
        # what stops that becoming a late-game nag.
        #
        # It STARTS at 1, not 0: every standard match begins with a Town
        # Centre, and starting from 0 meant the idle warning could never fire
        # until a villager had been seen training - the exact seconds where
        # an idle TC costs the most. KNOWN LIMITATION: Nomad starts with no
        # TC, so Loom would nag before the first TC stands; a Nomad toggle
        # belongs in the launcher later.
        #
        # Two independent lines of evidence feed it, reconciled by max(),
        # never added: the notification feed announces a TC at completion,
        # and the queue shows it once it trains - the SAME TC produces both
        # signals, so adding them invented a third TC in live testing.
        self.tcs_seen = 1
        self._tcs_notified = 1        # starting TC + "--Town Center Built--"
        self._tcs_queue_high = 1      # high-water of green villager groups
        # A queue-evidence raise must hold CONTINUOUSLY for this many game
        # seconds. The notification channel is the dominant TC source (and
        # is double-checked by ink agreement); queue evidence is the
        # subordinate witness, and persistent misreads have fooled shorter
        # streaks into minting phantom TCs.
        self._training_candidate = None
        self._training_since = None
        self.tc_busy = 0              # TCs working right now (or blocked)
        self.idle_tcs = 0             # believed idle TCs, debounced

        # Streaks of polls that disagree with the current belief.
        self._idle_streak = 0
        self._busy_streak = 0
        self._idle_candidate_since = None
        self._blocked_streaks = {}    # candidate blocked-state -> streak
        self._tc_streaks = {}         # candidate idle-TC count -> streak

    def update(self, game_time, slots):
        """Feed in one poll. Returns a list of event strings."""
        if slots is None:
            return []

        self.slots = slots
        events = []
        events += self._track_idleness(game_time, slots)
        events += self._track_blockage(slots)
        events += self._track_tcs(slots, game_time)
        return events

    def reset(self):
        """Forget everything. Call when a new game starts."""
        self.__init__(self.polls_to_believe)

    def _track_idleness(self, game_time, slots):
        empty = len(slots) == 0

        if empty and not self.idle:
            self._idle_streak += 1
            if self._idle_streak == 1:
                self._idle_candidate_since = game_time
            if self._idle_streak >= self.polls_to_believe:
                self.idle = True
                # Date the idleness from the first empty glance, not the
                # confirming one - the TC has already been idle that long.
                self.idle_since = self._idle_candidate_since
                self._idle_streak = 0
                return [PRODUCTION_IDLE]
        elif not empty and self.idle:
            self._busy_streak += 1
            if self._busy_streak >= self.polls_to_believe:
                self.idle = False
                self.idle_since = None
                self._busy_streak = 0
                return [PRODUCTION_RESUMED]
        else:
            # The poll agrees with what I already believe; clear dissent.
            self._idle_streak = 0
            self._busy_streak = 0
        return []

    def _track_blockage(self, slots):
        """Watch for the RED wash: production blocked because housed.

        Amber deliberately does not register here. The frame audit showed
        amber on any item merely waiting - behind a training villager,
        behind an age research - so it is routine queue life, not a
        blockage event. Red stays meaningful: housed. Pop-capped detection
        belongs to the population reading (current at the 200 cap), not to
        tints.
        """
        tints = {slot.tint for slot in slots}
        seen = "housed" if "red" in tints else None

        if seen == self.blocked:
            self._blocked_streaks.clear()
            return []

        streak = self._blocked_streaks.get(seen, 0) + 1
        self._blocked_streaks = {seen: streak}
        if streak < self.polls_to_believe:
            return []

        self.blocked = seen
        self._blocked_streaks.clear()
        if seen == "housed":
            return [HOUSED]
        return [UNBLOCKED]

    def register_tc_built(self):
        """A Town Centre finished building - the notification feed says so.

        This is the one EXACT source of TC count: the game's own "--Town
        Center Built--" line. Queue inference merely corroborates it.
        """
        self._tcs_notified += 1
        self.tcs_seen = max(self._tcs_notified, self._tcs_queue_high)

    def _track_tcs(self, slots, game_time):
        """Notice when a known Town Centre stops doing anything.

        Existence and busyness use different evidence, and the asymmetry is
        deliberate. Only a GREEN VILLAGER group proves an extra TC exists:
        the tech icons are dark silhouettes that mis-match military groups,
        and letting them raise the count invented phantom TCs in a one-TC
        game. The assumed starting TC covers "researching at 0:01", and the
        notification feed reports real additions (register_tc_built).

        For busyness the net is wide on purpose: ANY villager group counts
        (a queued unit starts by itself), and ANY TC-tech group counts too -
        a tech group is either researching right now or waiting behind a
        green group that is already counted, and a just-clicked age-up's
        wash covers too few pixels to classify for its first quarter, which
        used to flash TC IDLE at the exact moment the player did the right
        thing. Erring busy costs a missed alert; erring idle costs the
        player's trust in the warning.
        """
        training = sum(1 for slot in slots
                       if slot.tint == "green"
                       and slot.identity in TC_UNIT_IDENTITIES)
        queued = sum(1 for slot in slots
                     if slot.identity in TC_UNIT_IDENTITIES)
        researching = sum(1 for slot in slots
                          if slot.identity in TC_TECH_IDENTITIES)

        # Raising the queue's TC evidence takes CONTINUOUS proof: the count
        # above the current high-water must hold on every poll for three
        # game-seconds. Persistent misreads fooled shorter streaks into
        # minting phantom TCs, and a high-water mark never forgets. One gap
        # resets the window. The candidate tracks the LOWEST count sustained
        # throughout - three greens shrinking to two still proves two.
        # And it only works at all during the opening (see
        # QUEUE_TC_WINDOW_SECONDS): after two minutes, only the
        # notification feed may add a Town Centre.
        if (game_time is None
                or game_time > QUEUE_TC_WINDOW_SECONDS
                or training <= self._tcs_queue_high):
            self._training_candidate = None
            self._training_since = None
        else:
            if self._training_candidate is None:
                self._training_candidate = training
                self._training_since = game_time
            else:
                self._training_candidate = min(self._training_candidate,
                                               training)
            if (game_time - self._training_since
                    >= QUEUE_TC_CONFIRM_SECONDS):
                self._tcs_queue_high = self._training_candidate
                self._training_candidate = None
                self._training_since = None
        self.tcs_seen = max(self._tcs_notified, self._tcs_queue_high)
        self.tc_busy = min(self.tcs_seen, queued + researching)
        idle_now = self.tcs_seen - self.tc_busy

        if idle_now == self.idle_tcs:
            self._tc_streaks.clear()
            return []

        streak = self._tc_streaks.get(idle_now, 0) + 1
        self._tc_streaks = {idle_now: streak}
        if streak < self.polls_to_believe:
            return []

        was_idle = self.idle_tcs > 0
        self.idle_tcs = idle_now
        self._tc_streaks.clear()
        if idle_now > 0 and not was_idle:
            return [TC_IDLE]
        if idle_now == 0 and was_idle:
            return [TC_RECOVERED]
        return []   # 1 idle became 2 idle: still idle, no fresh event

    def idle_duration(self, game_time):
        """How long production has been idle, in game seconds. 0 when busy."""
        if not self.idle or self.idle_since is None or game_time is None:
            return 0
        return max(0, game_time - self.idle_since)
