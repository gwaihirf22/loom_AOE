"""
Loom — turning raw input counts into game-time APM.

The counter process (tools/apm_counter.py) reports buckets of "this many
keys and clicks in the last five wall-clock seconds". The rest of Loom
lives in GAME time, which runs at 1.7x in multiplayer and pauses - so the
launcher records, for every state line the overlay sends, the wall moment
it arrived and the game time it carried. Those pairs are the bridge: each
APM bucket is placed on the game clock by interpolating between the pairs
around it. Buckets with no nearby pair (menus, pauses, before the match)
are dropped from the game-time series but still counted in the totals.

Pure arithmetic, no Qt, no X - the whole module tests headless.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# How long a bucket is, in wall seconds. Five seconds is fine enough to see
# a fight spike and coarse enough that the counts mean something.
BUCKET_SECONDS = 5

# A bucket further than this from any wall<->game pair is not inside the
# match as far as anyone can tell, and is left off the game-time series.
MAX_PAIR_GAP = 10


def game_time_at(wall, pairs):
    """The game time at a wall moment, from (wall, game_t) pairs, or None.

    Interpolates between the surrounding pairs; a pause shows up as game
    time standing still between pairs, which interpolation handles by
    construction. Outside the observed span, only a nearby edge pair
    counts - extrapolating across a menu would invent game time.
    """
    if not pairs:
        return None
    if wall <= pairs[0][0]:
        return pairs[0][1] if pairs[0][0] - wall <= MAX_PAIR_GAP else None
    if wall >= pairs[-1][0]:
        return pairs[-1][1] if wall - pairs[-1][0] <= MAX_PAIR_GAP else None
    for (w0, t0), (w1, t1) in zip(pairs, pairs[1:]):
        if w0 <= wall <= w1:
            if w1 - w0 > MAX_PAIR_GAP * 6:
                # A long hole in the mapping (alt-tab, menu): readings on
                # either side exist but the middle is unknowable.
                return None
            if w1 == w0:
                return t0
            fraction = (wall - w0) / (w1 - w0)
            return t0 + fraction * (t1 - t0)
    return None


# A backwards step bigger than this is a NEW GAME; anything smaller is the
# clock wobbling. The same five seconds events.SEAM_TOLERANCE_SECONDS uses,
# and measured the same way: real restarts drop by hundreds of seconds
# (64 to 6 in the game that found this), jitter by one to three.
RESTART_DROP_SECONDS = 5


def align(buckets, pairs, game_from=None):
    """APM per bucket, on the game clock.

    buckets: [(wall_end, keys, clicks)]; pairs: [(wall, game_t)], sorted.
    Returns the stats file's "apm" section, or None if nothing aligned.

    `game_from` is the first game-second the stats file's own timeline
    recorded, and it is how the seam is TOLD rather than inferred. One
    overlay session can span two matches - attach to a game already at
    0:54, lose it, and a new one begins - and the buckets run across both
    while the recorder starts a fresh file for the second. Nothing in the
    bucket stream says where the join is; the timeline does, because it
    only ever held the game the file is about.

    Without it this falls back to spotting a backwards step, which works
    on the data seen so far and is strictly weaker: it can only notice a
    restart the clock happened to make obvious.
    """
    t_series = []
    apm_series = []
    keys_total = 0
    clicks_total = 0
    backwards = 0
    stale = 0
    for wall_end, keys, clicks in buckets:
        keys_total += keys
        clicks_total += clicks
        moment = game_time_at(wall_end, pairs)
        if moment is None:
            continue
        moment = round(moment)
        if game_from is not None and moment < game_from - BUCKET_SECONDS:
            # Before the game this file records. Not unplaceable - placed
            # perfectly well, in a match that is not this one.
            stale += 1
            continue
        # The game clock must only ever go forward. gamestats' timeline has
        # said so since it was written; this series never did, and it shows:
        # across 266 recorded games, timeline.t, events, ages and alerts hold
        # ZERO backwards steps and apm.t holds 29. Every seam anyone has ever
        # seen on a graph was this one series.
        #
        # Two things put a bucket behind the last one and neither is APM's
        # to solve. The clock can have MISREAD - measured, 35:41 read as
        # 55:42, the tens digit as 5 instead of 3 - or the file can hold two
        # games because a restart went unnoticed. From in here they are
        # identical, and both make the bucket unplaceable rather than early.
        #
        # So it is dropped, and the drop is COUNTED. A silently shorter
        # series would read as a quiet game; a number that says how much
        # could not be placed is a gap that admits it is one.
        if t_series and moment <= t_series[-1]:
            if t_series[-1] - moment > RESTART_DROP_SECONDS:
                # A NEW GAME, not a bad clock. The stats file is about the
                # game that was running when it was written, and the
                # recorder's timeline has already restarted for it - this
                # series had not, which is the whole reason apm.t was the
                # only series in the corpus ever going backwards.
                #
                # So the earlier game is DISCARDED, not this one. Dropping
                # the later buckets instead is the tempting version and is
                # actively harmful: measured on a 174-second game, it kept
                # the previous game's four buckets and threw away the
                # first sixty-three seconds of the real one, leaving a
                # series that looked monotonic and clean and belonged to
                # two different matches. A plausible hybrid is worse than
                # an obvious seam.
                stale += len(t_series)
                t_series, apm_series = [], []
            else:
                # A wobble or a second the clock did not advance past.
                # Nowhere honest to put it.
                backwards += 1
                continue
        actions = keys + clicks
        t_series.append(moment)
        apm_series.append(round(actions * (60 / BUCKET_SECONDS)))
    if not t_series:
        return None
    section = {"t": t_series, "apm": apm_series,
               "keys_total": keys_total, "clicks_total": clicks_total,
               "bucket_seconds": BUCKET_SECONDS}
    if backwards:
        # Only present when it happened, so its absence is not a claim.
        section["unplaceable_buckets"] = backwards
    if stale:
        # Named for what it was, not for the fact that it went: these
        # actions happened in an EARLIER game that shared this overlay
        # session, and reporting them as unplaceable would invite someone
        # to go looking for a fault there is not.
        section["buckets_from_an_earlier_game"] = stale
    return section


def how_counted(platform):
    """How this platform counts APM: "overlay", "child", or None for not at all.

    Windows uses Raw Input, which needs a window and a message pump, and the
    overlay already has both - so loom/apmwin.py runs there and there is no
    APM child at all. Linux selects XInput2 raw events on the root window,
    which needs neither, so tools/apm_counter.py stays a separate process.

    macOS is None on purpose: it has no counter yet. That None is honest
    absence, not a default - a counter there would need a CGEventTap, which
    is deferred alongside the hotkey backend (see CLAUDE.md). The launcher
    used to take the not-in-the-overlay answer as "so spawn the child", and
    on macOS that child imports Xlib and dies; the platform's real answer
    was neither, and nothing could say so.

    One function everything asks, because two of these answers held in two
    places will drift - and the failure mode of the launcher and the overlay
    drifting is counting every action twice, which would not look like a
    bug, it would look like the player having a very good game.

    Takes the platform rather than reading sys.platform, so every answer is
    testable from any machine and this module stays import-free.
    """
    if platform == "win32":
        return "overlay"
    if platform == "linux":
        return "child"
    return None


def counted_in_the_overlay(platform):
    """Does this platform count APM inside the overlay?

    A view of how_counted rather than a second opinion, so the two can
    never disagree about a platform.
    """
    return how_counted(platform) == "overlay"
