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


def align(buckets, pairs):
    """APM per bucket, on the game clock.

    buckets: [(wall_end, keys, clicks)]; pairs: [(wall, game_t)], sorted.
    Returns the stats file's "apm" section, or None if nothing aligned.
    """
    t_series = []
    apm_series = []
    keys_total = 0
    clicks_total = 0
    for wall_end, keys, clicks in buckets:
        keys_total += keys
        clicks_total += clicks
        moment = game_time_at(wall_end, pairs)
        if moment is None:
            continue
        actions = keys + clicks
        t_series.append(round(moment))
        apm_series.append(round(actions * (60 / BUCKET_SECONDS)))
    if not t_series:
        return None
    return {"t": t_series, "apm": apm_series,
            "keys_total": keys_total, "clicks_total": clicks_total,
            "bucket_seconds": BUCKET_SECONDS}
