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

# How long queue evidence must hold before it may add a Town Centre to the
# believed count: this many game seconds AND this many distinct queue
# reads, on every read without a gap. Both dimensions matter. Time alone
# proved too weak: under load shedding the queue is read ~0.9s apart, so
# "one game second sustained" was satisfiable by TWO glances - and a
# tech handover (Loom finishing as Feudal Age starts, both cells wearing
# green for a moment) minted a phantom second TC in a live one-TC game.
# Three reads across three game seconds outlive any single transition;
# real multi-TC evidence persists for minutes and never notices. This
# runs for the WHOLE game - the notification feed cannot carry the count
# alone, because the game never reprints a line that is already on
# screen, so a TC completing while "--Town Center Built--" still lingers
# announces nothing at all (a seven-TC game produced three lines).
QUEUE_TC_CONFIRM_SECONDS = 3
QUEUE_TC_CONFIRM_READS = 3

# A green item may only prove a TC exists when its IDENTITY is confident:
# a strong score, or a clear win over every other identity family. The
# queue reader's least-bad guess on an icon it has no template for lands
# on villagers and TC techs disturbingly often - measured three times in
# two days: an unknown Armenian icon read villager_male 0.42 with
# castle_age 0.42 right behind it, the Husbandry horseshoe read
# flemish_militia 0.30, an upgrade shield read villager_male 0.53 - and
# each one minted a phantom TC. A real villager scores 0.6+ on most polls
# or wins its family by a wide margin, and the high-water only needs one
# clean confirmation window; an unknown icon matches everything a little
# and nothing well, forever. Busyness deliberately IGNORES this gate:
# erring busy is safe, and a misnamed green cell still proves SOMETHING
# is producing.
TC_EVIDENCE_SCORE = 0.6
TC_EVIDENCE_MARGIN = 0.08

# Identities that mean a Town Centre is doing something. Each building
# shows at most ONE in-progress item in the global queue - completely
# untinted the moment it is placed, then washing green left to right -
# while anything queued behind it waits AMBER until the first finishes,
# and an item the population cannot fit turns RED (even a tech, if it sits
# behind a blocked unit). Villager batch depth is the white count on the
# one icon, not extra slots. So the number of untinted-or-green TC items
# on screen at once is a floor on how many TCs exist (tested in game,
# 2026-07-31).
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

        # Town Centres. tcs_seen is a high-water mark: the most in-progress
        # TC items (untinted or green, unit or tech) ever on screen at
        # once. It only grows during a game - reset() clears it - so a TC
        # lost to a raid leaves it overcounting; the villager-count policy
        # in alerts.py is what stops that becoming a late-game nag.
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
        self._tcs_queue_high = 1      # high-water of in-progress TC items
        # A queue-evidence raise must hold CONTINUOUSLY across both game
        # time and distinct reads (QUEUE_TC_CONFIRM_*) - a misread or a
        # handover artifact flickers, a real queue item stays.
        self._training_candidate = None
        self._training_since = None
        self._training_reads = 0
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
        """Count Town Centres by in-progress queue items, and notice when a
        known one stops doing anything.

        EXISTENCE: each building shows at most one in-progress item -
        untinted when just placed, then washing green - while anything
        waiting behind it is amber and anything the population cannot fit
        is red (see the identity-table comment). So every untinted-or-green
        TC item, unit or tech, is one distinct TC, and the most ever seen
        at once is how many exist. This is a high-water mark for the whole
        game: the notification feed cannot carry the count alone (the game
        never reprints a line already on screen), and it is never lowered -
        TCs do die, but that is accepted as uncountable; the villager-count
        policy in alerts.py is what stops overcounting becoming a
        late-game nag.

        For busyness, every tint counts EXCEPT amber. Green is working;
        untinted is a just-started item whose wash is too thin to classify
        (counting it stops TC IDLE flashing at the exact moment the player
        clicks an age-up); red is blocked-busy, which is the HOUSED
        alert's problem, not idleness. But amber means WAITING BEHIND the
        building's in-progress item - the front of that queue is already
        counted, so an amber item adds nothing. Counting it hid a real
        idle TC live: one TC held wheelbarrow(red) + town_watch(amber) +
        imperial_age(amber), read as THREE busy TCs, and the player's
        freshly-built second TC sat idle with no warning.
        """
        # GREEN or UNTINTED, per the author's tested rule (2026-08-01):
        # an untinted TC item is verifiable as producing - a just-placed
        # item whose wash is too young to classify - so counting it
        # confirms a new TC seconds earlier than waiting for green. The
        # residual ambiguity is the same-type waiting batch, which also
        # renders dark; the confidence gate below, the three-read window,
        # and the never-lowered high water carry that risk, and the one
        # game blamed on it turned out to have that many real TCs after
        # all. Amber and red never count: amber is waiting or pop-capped
        # (nothing being made), red is blocked.
        in_progress = sum(1 for slot in slots
                          if slot.tint in (None, "green")
                          and (slot.identity in TC_UNIT_IDENTITIES
                               or slot.identity in TC_TECH_IDENTITIES)
                          and (slot.identity_score >= TC_EVIDENCE_SCORE
                               or (slot.identity_margin is not None
                                   and slot.identity_margin
                                   >= TC_EVIDENCE_MARGIN)))
        queued = sum(1 for slot in slots
                     if slot.identity in TC_UNIT_IDENTITIES
                     and slot.tint != "amber")
        researching = sum(1 for slot in slots
                          if slot.identity in TC_TECH_IDENTITIES
                          and slot.tint != "amber")

        # Raising the queue's TC evidence takes CONTINUOUS proof in both
        # time and looks: the count above the current high-water must hold
        # on every read for QUEUE_TC_CONFIRM_SECONDS spanning at least
        # QUEUE_TC_CONFIRM_READS reads. A misread or a handover artifact
        # flickers; a real item stays. One gap resets the window. The
        # candidate tracks the LOWEST count sustained throughout - three
        # items shrinking to two still proves two.
        if game_time is None or in_progress <= self._tcs_queue_high:
            self._training_candidate = None
            self._training_since = None
            self._training_reads = 0
        else:
            if self._training_candidate is None:
                self._training_candidate = in_progress
                self._training_since = game_time
                self._training_reads = 1
            else:
                self._training_candidate = min(self._training_candidate,
                                               in_progress)
                self._training_reads += 1
            if (game_time - self._training_since
                    >= QUEUE_TC_CONFIRM_SECONDS
                    and self._training_reads >= QUEUE_TC_CONFIRM_READS):
                self._tcs_queue_high = self._training_candidate
                self._training_candidate = None
                self._training_since = None
                self._training_reads = 0
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
