"""
Loom — live view of the TC tracker's thoughts (development tool).

    python -m tools.tc_debug

Runs the same reader + production tracker the overlay runs, against the
live game, and prints what Loom BELIEVES and what it SEES every time
either changes: the TC count with both evidence channels, busy/idle, and
every queue slot with its identity, tint, score and margin.

Built for discrepancy hunting: watch it beside the game, and the moment
the numbers disagree with reality, pause the game. A paused game changes
nothing, so this prints nothing - the evidence stays on screen instead of
scrolling away - and the frozen queue can be compared cell by cell.

Reading the slot lines:

    [3] villager_female  green 0.70        counts as TC work and evidence
    [4] villager_male*   green 0.42~0.00   * = would be TC EVIDENCE but is
                                           refused: unconfident identity
    [5] militia          amber 0.64        never TC-relevant

A '#' after the count marks slots feeding tcs_seen this poll; 'b' marks
slots counting a TC as busy. '~' is the identity margin when the full
search ran this poll (cache hits show none - the score speaks for those).
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

from loom import production, reader


def describe_slot(slot):
    """One queue slot as a compact, comparison-friendly line."""
    is_tc_id = (slot.identity in production.TC_UNIT_IDENTITIES
                or slot.identity in production.TC_TECH_IDENTITIES)
    confident = (slot.identity_score >= production.TC_EVIDENCE_SCORE
                 or (slot.identity_margin is not None
                     and slot.identity_margin >= production.TC_EVIDENCE_MARGIN))
    marks = ""
    if slot.tint == "green" and is_tc_id:
        marks += "#" if confident else "*"
    if is_tc_id:
        marks += "b"
    margin = ("" if slot.identity_margin is None
              else f"~{slot.identity_margin:.2f}")
    return (f"  [{slot.index}] {slot.identity or '?'}{marks:3s} "
            f"{slot.tint or 'dark':5s} {slot.identity_score:.2f}{margin}")


def render(game_time, tracker, slots, tc_events):
    """The whole believed state as one printable block."""
    minutes, seconds = divmod(int(game_time), 60)
    lines = [f"--- {minutes}:{seconds:02d}  TCs={tracker.tcs_seen} "
             f"(notified {tracker._tcs_notified}, "
             f"queue {tracker._tcs_queue_high})  "
             f"busy={tracker.tc_busy} idle={tracker.idle_tcs}"]
    for event in tc_events:
        lines.append(f"  !! notification: {event}")
    if slots is None:
        lines.append("  (queue unreadable)")
    else:
        lines.extend(describe_slot(slot) for slot in slots)
    return "\n".join(lines)


def main():
    hud = reader.HudReader()
    tracker = production.ProductionTracker()
    print("Waiting for the Age of Empires II window... (Ctrl+C to quit)")
    hud.connect()
    print("Waiting for a match to start...")
    hud.wait_for_hud()
    print(f"HUD: {hud.hud['profile'].name} at {hud.hud['scale']:.2f} "
          f"(score {hud.hud['score']:.3f})")
    print("Watching. Pause the game to freeze this readout.\n"
          "  # counts toward the TC total   * refused (unconfident)   "
          "b counts a TC busy   ~ identity margin")

    last_block = None
    while True:
        reading = hud.poll()
        if reading.game_time is None:
            continue
        tc_events = [e for e in reading.game_events
                     if e == "town_center_built"]
        for _ in tc_events:
            tracker.register_tc_built()
        tracker.update(reading.game_time, reading.queue)

        # Print only on CHANGE, with the time excluded from the
        # comparison: a paused game repeats itself exactly, so the last
        # block stays on screen; a mere clock tick is not worth a reprint.
        block = render(reading.game_time, tracker, reading.queue, tc_events)
        comparable = block.split("  TCs=", 1)[1]
        if comparable != last_block:
            print(block)
            last_block = comparable


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
