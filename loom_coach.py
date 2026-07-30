"""
Loom — the coach, in a terminal.

Shows the current build-order step, the next one, and whether you are on pace.
This is the same job the overlay will do in Milestone 4; doing it in a terminal
first means the logic can be finished and trusted before any UI exists.

Live, against a running game:
    python loom_coach.py
    python loom_coach.py --build fast_castle

Simulated, with no game running:
    python loom_coach.py --simulate
    python loom_coach.py --simulate --scenario behind
    python loom_coach.py --simulate --scenario stall --speed 40

Simulation replays a whole match in under a minute, so "does it show the right
step at the right time?" can be checked in seconds instead of by playing a
sixteen-minute game.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import argparse
import time

from loom import pace, reader, session
from loom.build_order import BuildOrder, format_time

# Plain ANSI escape codes: no extra dependency just to color some text.
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
CYAN = "\033[36m"

HOME = "\033[H"          # move the cursor to the top-left
CLEAR_LINE = "\033[K"    # clear from the cursor to the end of the line
CLEAR_BELOW = "\033[J"   # clear everything below the cursor

AGE_NAMES = {1: "Dark Age", 2: "Feudal Age", 3: "Castle Age", 4: "Imperial Age"}

# How far off pace counts as still fine, and as merely slightly late.
#
# Fifteen seconds looks generous but is not: villagers arrive about every
# twenty-five seconds, so that is the finest resolution this measurement has.
# Reporting "behind by 8 seconds" would be false precision - it is well within
# the gap between one villager and the next.
ON_PACE_SECONDS = 15
SLIGHTLY_BEHIND_SECONDS = 35

WIDTH = 66


def describe_pace(delta):
    """Turn a pace delta in seconds into colored text."""
    if delta is None:
        return f"{DIM}no pace yet{RESET}"

    if delta < -ON_PACE_SECONDS:
        return f"{CYAN}{BOLD}AHEAD {abs(delta):.0f}s{RESET}"
    if abs(delta) <= ON_PACE_SECONDS:
        return f"{GREEN}{BOLD}ON PACE{RESET}"
    if delta <= SLIGHTLY_BEHIND_SECONDS:
        return f"{YELLOW}{BOLD}BEHIND {delta:.0f}s{RESET}"
    return f"{RED}{BOLD}BEHIND {delta:.0f}s{RESET}"


def render(build, villagers, game_time, delta, note=""):
    """Build the screen as a list of lines."""
    rule = DIM + "─" * WIDTH + RESET
    lines = [f"{BOLD}{build.name}{RESET} {DIM}· {build.civilization}{RESET}", rule]

    if villagers is None or game_time is None:
        lines += ["", f"  {DIM}waiting for the game...{RESET}", ""]
        if note:
            lines += [f"  {note}"]
        return lines

    # The step to work on NOW is the first one not yet finished, not the last
    # one completed. Showing the completed step reads as a lag.
    active = build.active_step(villagers, game_time)
    following = build.following_step(villagers, game_time)

    age = AGE_NAMES.get(active.age, "") if active else AGE_NAMES[1]

    # Once the build is finished the pace meter has retired (pace.py returns
    # None for the rest of the game), so show a quiet dash instead of the
    # misleading "no pace yet".
    pace_text = f"{DIM}—{RESET}" if active is None else describe_pace(delta)
    lines.append(
        f"  {BOLD}{format_time(game_time):>6}{RESET}   "
        f"{BOLD}{villagers:>3}{RESET} villagers   {age:<12} {pace_text}"
    )
    lines.append(rule)

    # What to do right now.
    if active is None:
        lines.append(f"  {BOLD}NOW {RESET}  {DIM}build complete{RESET}")
    else:
        by = f"by {format_time(active.time)} · {active.villager_count} vills"
        lines.append(f"  {BOLD}NOW {RESET}  {BOLD}{active.details}{RESET}  {DIM}{by}{RESET}")
        for extra in active.footnotes:
            lines.append(f"        {DIM}· {extra}{RESET}")

        target = active.villagers
        lines.append("")
        lines.append(
            f"  {DIM}target{RESET}  food {target['food']}   wood {target['wood']}   "
            f"gold {target['gold']}   stone {target['stone']}"
        )

    lines.append("")

    # What is coming after that.
    if following is None:
        lines.append(f"  {BOLD}THEN{RESET}  {DIM}last step{RESET}")
    else:
        when = f"{format_time(following.time)} · {following.villager_count} vills"
        lines.append(f"  {BOLD}THEN{RESET}  {following.details}")
        lines.append(f"        {DIM}at {when}{RESET}")

    lines.append(rule)

    position = "-" if active is None else build.steps.index(active) + 1
    lines.append(f"  {DIM}step {position} of {len(build.steps)}{RESET}   {note}")
    return lines


def draw(lines):
    """Redraw the screen in place, without scrolling."""
    output = HOME
    for line in lines:
        output += line + CLEAR_LINE + "\n"
    output += CLEAR_BELOW
    print(output, end="", flush=True)


# ---- simulation --------------------------------------------------------

def villagers_at(build, moment):
    """How many villagers a player following the build exactly would have.

    Note this interpolates rather than stepping straight from one build-order
    step to the next. Real villagers arrive one at a time, roughly every
    twenty-five seconds; a build order that jumps from 11 villagers to 13 is
    summarizing, not describing a player who gains two at once.

    An earlier version of this used the step values directly, which made the
    simulated player look badly behind for fifty seconds at a stretch - a
    problem in the simulation, being blamed on the pace meter.
    """
    first = build.steps[0]

    # Before the build's first checkpoint, ramp up from the three villagers
    # every civilisation starts with, rather than jumping straight to the
    # count the first step expects.
    if first.time and moment < first.time:
        fraction = moment / first.time
        return int(3 + fraction * (first.villager_count - 3))

    expected = build.expected_villagers(moment)
    if expected is None:
        return 3
    return int(expected)


def simulated_readings(build, scenario):
    """Yield (villagers, game_time) pairs for a whole match.

    Three scenarios, because the interesting bugs live at the extremes:
      perfect - exactly on the build's own schedule
      behind  - villagers arriving late throughout
      stall   - production stops dead part way, like a forgotten Town Center
    """
    last = max(s.time for s in build.steps if s.time is not None)

    for moment in range(0, int(last) + 90):
        if scenario == "behind":
            # Villagers lag: at time T the player has what they should have
            # had some way back.
            villagers = villagers_at(build, moment * 0.82)
        elif scenario == "stall":
            villagers = villagers_at(build, min(moment, 330))
        else:
            villagers = villagers_at(build, moment)

        yield villagers, moment


def run_simulation(build, scenario, speed):
    print(f"Simulating '{scenario}' at {speed}x. Ctrl+C to stop.")
    time.sleep(1)
    print("\033[2J", end="")  # clear once, then redraw in place

    tracker = pace.PaceTracker(build)
    for villagers, moment in simulated_readings(build, scenario):
        note = f"{DIM}simulated · {scenario}{RESET}"
        draw(render(build, villagers, moment,
                    tracker.update(villagers, moment), note))
        time.sleep(1.0 / speed)

    print("\nSimulation finished.")


# ---- live --------------------------------------------------------------

def run_live(build, poll_interval):
    hud = reader.HudReader()

    # Both waits are open-ended: start the coach first, then the game.
    try:
        print("Waiting for the Age of Empires II window... (Ctrl+C to quit)")
        hud.connect()
        width, height = hud.window_size()
        print(f"Found game window: {width} x {height}")

        print("Waiting for a match to start...")
        hud.wait_for_hud()
    except KeyboardInterrupt:
        print("\nStopped.")
        return

    print(f"HUD found (match {hud.hud['score']:.3f}, scale {hud.hud['scale']:.2f})")
    time.sleep(1)
    print("\033[2J", end="")

    tracker = pace.PaceTracker(build)
    note = ""
    try:
        while True:
            reading = hud.poll()

            if reading.event == session.GAME_STARTED:
                tracker.reset()

            if reading.event:
                note = f"{CYAN}{reading.event}{RESET}"
            elif not reading.hud_visible:
                note = f"{DIM}HUD not visible{RESET}"
            else:
                note = ""

            draw(render(build, reading.villagers, reading.game_time,
                        tracker.update(reading.villagers, reading.game_time),
                        note))
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description="Loom build-order coach")
    parser.add_argument("--build", default="fast_castle",
                        help="name of a build in builds/ (default: fast_castle)")
    parser.add_argument("--simulate", action="store_true",
                        help="replay a match instead of reading the game")
    parser.add_argument("--scenario", default="perfect",
                        choices=["perfect", "behind", "stall"],
                        help="what kind of match to simulate")
    parser.add_argument("--speed", type=float, default=20.0,
                        help="simulated seconds per real second")
    parser.add_argument("--poll", type=float, default=0.3,
                        help="seconds between reads of the screen")
    args = parser.parse_args()

    build = BuildOrder.load_by_name(args.build)

    problems = build.validate()
    if problems:
        print(f"Warnings in '{build.name}':")
        for problem in problems:
            print(f"  - {problem}")
        print()

    if args.simulate:
        run_simulation(build, args.scenario, args.speed)
    else:
        run_live(build, args.poll)


if __name__ == "__main__":
    main()
