"""
Loom — alert policy: which production events deserve how much noise.

production.py reports what happened; this decides how loudly to say it. The
two are separate because the facts do not change with the game phase, but the
right volume does:

* An idle TC early is the worst routine mistake in the game, so it starts
  obnoxious. But once the economy is built, more villagers stop being the
  point - by 100 villagers the warning softens, and by 120 it goes quiet.
  Both numbers are the player's to change in config.json ("idle_tc_alert"),
  because the right villager target varies by map, civ and taste.

* Housed is almost never good: production is stalled and a house fixes it
  right now. It stays a full alert.

* Pop-capped is *mostly* good news - it means production is running flat out
  at the 200 cap - so by default it makes no noise at all. (The exception,
  too many villagers to fit an army, is real but rare, and a warning that is
  usually wrong teaches the player to ignore it.)
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from . import production

# How loud an alert should be.
FULL = "full"      # the obnoxious treatment: this is costing the game
SOFT = "soft"      # visible but calm: worth knowing, not worth a klaxon
OFF = "off"        # say nothing

# Default villager counts where the idle-TC warning softens and shuts off.
SOFTEN_AT = 100
SILENCE_AT = 120

# How loudly each blockage state is reported, regardless of game phase.
BLOCK_SEVERITY = {
    production.HOUSED: FULL,
    production.POP_CAPPED: OFF,
}


# The pop cap in a standard game. At or above it, a full population is
# "pop-capped" (usually good) rather than "housed" (build a house).
STANDARD_POP_CAP = 200

# Warn when this little population space remains. Being warned once already
# housed is an autopsy - production has stalled and the house takes time to
# build. Four space is roughly what a boom eats while a house goes up
# (several TCs with villagers in flight), so the warning lands while acting
# on it still prevents the stall.
HOUSE_WARNING_HEADROOM = 4


class AlertToggles:
    """Which alerts the player wants at all. A plain value object.

    This exists so the policy stays pure: the launcher writes the player's
    choices to config.json, the entry point reads them ONCE at startup and
    hands them in here. production_alert never touches config itself, so it
    stays testable without a file.
    """

    def __init__(self, idle_tc=True, housed=True, house_warning=True):
        self.idle_tc = idle_tc
        self.housed = housed
        self.house_warning = house_warning


def production_alerts(tracker, villagers, policy, game_time=None,
                      population=None, toggles=None, house_headroom=None):
    """Every production warning worth showing right now, most urgent first.

    Returns a list of (text, severity) - housing trouble and an idle TC are
    separate facts that are often true at the same time (a TC sitting quiet
    while the pop cap closes in), and hiding one behind the other taught
    the player nothing. The overlay stacks them. Pure logic over believed
    state, so every front end shares it and it tests without a game.

    Housed is judged from the population indicator alone - current at cap,
    below the standard 200. The queue's red wash is deliberately NOT used
    for this: bare skin in the villager portrait votes red, and an alert
    that cries wolf on every bare-chested villager teaches the player to
    ignore it (found the hard way, live).

    Pop-capped never appears here: it usually means production is maxed
    out, which is what the player wants. toggles switches whole alert
    families off; None means everything on. house_headroom is how much pop
    space remaining triggers the pre-emptive warning - the player's own
    number from config, or None for the default; a boom eats more per house
    than a one-TC opening, so the right value is the player's to pick.
    """
    if toggles is None:
        toggles = AlertToggles()
    if house_headroom is None:
        house_headroom = HOUSE_WARNING_HEADROOM
    found = []

    if population is not None:
        current, cap = population
        headroom = cap - current
        if cap < STANDARD_POP_CAP and headroom <= house_headroom:
            if headroom <= 0:
                if toggles.housed:
                    found.append(("HOUSED — build a house", FULL))
            elif toggles.house_warning:
                # SOON, not NOW: a build order already tells the player when
                # to build a house, so shouting NOW at somebody following the
                # plan is a nag, not a warning. The headroom number is theirs
                # to tune in the launcher.
                found.append((f"HOUSE SOON — {headroom} pop space left", FULL))

    if toggles.idle_tc and tracker.idle_tcs > 0:
        severity = policy.severity(villagers)
        if severity != OFF:
            text = ("TC IDLE" if tracker.idle_tcs == 1
                    else f"{tracker.idle_tcs} TCs IDLE")
            # Only the whole-queue-empty case has a trustworthy start time;
            # when one TC of several stops, all I know is that it stopped.
            if tracker.idle:
                duration = tracker.idle_duration(game_time)
                if duration > 0:
                    text += f" — {duration:.0f}s"
            found.append((text, severity))

    return found


def production_alert(tracker, villagers, policy, game_time=None,
                     population=None, toggles=None, house_headroom=None):
    """The single most urgent warning, or (None, None). Kept for callers
    that can only show one thing; the overlay uses the plural form."""
    found = production_alerts(tracker, villagers, policy, game_time,
                              population, toggles, house_headroom)
    return found[0] if found else (None, None)


class IdleTcPolicy:
    """Grades idle-TC alerts by how much more economy the player wants.

    Construct it from config.idle_tc_limits() so the player's own numbers
    apply; the defaults suit a standard 1v1 economy.
    """

    def __init__(self, soften_at=SOFTEN_AT, silence_at=SILENCE_AT):
        self.soften_at = soften_at
        self.silence_at = max(silence_at, soften_at)

    def severity(self, villagers):
        """How loud an idle-TC warning should be right now.

        An unknown villager count gets the full alert: the count is only
        unknown early (before the first stable reading), and early is exactly
        when an idle TC hurts most.
        """
        if villagers is None:
            return FULL
        if villagers >= self.silence_at:
            return OFF
        if villagers >= self.soften_at:
            return SOFT
        return FULL
