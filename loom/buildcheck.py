"""
Loom — will this file actually work as a build order?

Importing is the moment to say what is wrong with a build, because every
later moment is worse: a build that fails to load becomes a line in the
launcher's output pane that nobody reads, and a build that loads but is
written oddly only shows itself mid-match, when the panel sits on the wrong
step and the player is busy.

Two rules shape what is checked here.

First, "will it load" is answered by LOADING IT, not by a second opinion
about the format. BuildOrder is what the launcher runs, so a re-implemented
schema check here would be a rule that drifts away from the code it
describes. inspect() constructs the real thing and catches what
available_builds catches.

Second, only a build that cannot work at all is refused. Everything else is
said out loud and left to the player: a build with no icon tokens reads
perfectly well in words, and resource rows that do not add up are somebody's
deliberate shorthand more often than a mistake. Refusing those would make
Loom pickier than the community whose format it borrowed.

No Qt in here: the launcher shows these findings in a dialog, and the tests
read them as data.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json
import re
from collections import namedtuple

from .build_order import BuildOrder, civilization_label

# A finding is one thing worth telling the player, and how much it matters.
# FATAL means Loom cannot use this file at all; WARNING means it will load
# and something about it is worth knowing first.
FATAL = "fatal"
WARNING = "warning"

Finding = namedtuple("Finding", "severity message")

# The @folder/file.ext@ markers that become pictures in the overlay. A slash
# inside and no whitespace, so a stray "@" in an instruction is not mistaken
# for one.
ICON_TOKEN = re.compile(r"@([^@\s]+/[^@\s]+)@")

# How many examples to name before saying "and N more". A dialog listing
# forty tokens teaches less than one listing three.
MAX_EXAMPLES = 3


def _sample(items):
    """A readable few of a possibly long list."""
    shown = ", ".join(items[:MAX_EXAMPLES])
    extra = len(items) - MAX_EXAMPLES
    return f"{shown} and {extra} more" if extra > 0 else shown


def inspect(path, can_draw=None):
    """Look at one candidate build order file.

    Returns (build, findings). build is a loaded BuildOrder, or None when the
    file cannot be used at all - in which case findings holds at least one
    FATAL entry saying why.

    can_draw is an optional callable taking an icon token and answering
    whether Loom has a picture for it. Left out, the icon check is skipped:
    that keeps this module free of the overlay, and so free of Qt.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as problem:
        return None, [Finding(FATAL, f"the file could not be read: {problem}")]
    except UnicodeDecodeError:
        return None, [Finding(FATAL, "this is not text Loom can read - build "
                                     "orders are UTF-8 JSON")]
    except json.JSONDecodeError as problem:
        return None, [Finding(
            FATAL,
            f"this is not valid JSON (line {problem.lineno}: {problem.msg}). "
            "If it was pasted into a text editor, check that nothing was cut "
            "off at either end.")]

    # The real loader, so this answer cannot drift from what the launcher
    # will do with the same file five seconds later.
    try:
        build = BuildOrder(data)
    except (AttributeError, KeyError, TypeError, ValueError) as problem:
        return None, [Finding(
            FATAL,
            f"this is valid JSON but not a build order Loom understands "
            f"({problem}). Loom reads the RTS Overlay format: an object with "
            "a build_order list inside it.")]

    if not build.steps:
        return None, [Finding(
            FATAL,
            "there are no steps in it. A build order needs a build_order "
            "list holding at least one step.")]

    findings = list(_step_findings(build))
    if can_draw is not None:
        findings.extend(_icon_findings(path, can_draw))
    return build, findings


def _step_findings(build):
    """What is worth saying about the steps themselves."""
    findings = []

    # Loom walks the steps in order and stops at the first one the player has
    # not reached, by villager count AND by time. So a step that goes
    # backwards on either is not merely untidy - it is a step the build can
    # sit behind, and one the player may never be shown.
    out_of_order = []
    previous = build.steps[0]
    for step in build.steps[1:]:
        if step.villager_count < previous.villager_count:
            out_of_order.append(
                f"villagers drop from {previous.villager_count} to "
                f"{step.villager_count}")
        if (step.time is not None and previous.time is not None
                and step.time < previous.time):
            out_of_order.append("a step's time goes backwards")
        previous = step
    if out_of_order:
        findings.append(Finding(
            WARNING,
            "the steps are not in order (" + _sample(out_of_order) + "). "
            "Loom follows villager count and game time together, so a step "
            "that goes backwards may never become the current one."))

    untimed = sum(1 for step in build.steps if step.time is None)
    if untimed == len(build.steps):
        findings.append(Finding(
            WARNING,
            "no step has a time. Loom will follow this build by villager "
            "count alone, which cannot tell apart steps that share a count - "
            "and real builds repeat a count while a Town Centre ages up. "
            "Expect it to move early through those."))
    elif untimed:
        findings.append(Finding(
            WARNING,
            f"{untimed} of {len(build.steps)} steps have no time. Those are "
            "placed by villager count alone."))

    silent = sum(1 for step in build.steps if not step.details)
    if silent:
        findings.append(Finding(
            WARNING,
            f"{silent} of {len(build.steps)} steps have no instruction text, "
            "so the panel will show their targets and nothing to do."))

    # Only the impossible direction is worth saying. A step whose resource
    # rows add up to LESS than its villager count is the normal case, not a
    # mistake: the villager walling, luring or putting up a Barracks is on
    # no resource at all, and published builds are full of those. Steps
    # carrying a negative are skipped outright - the format uses -1 for
    # "not specified", so summing them means nothing. Warning on either
    # would fire on half the community's builds, and a warning that common
    # is one nobody reads.
    impossible = [step for step in build.steps
                  if min(step.villagers.values()) >= 0
                  and step.assigned_villagers() > step.villager_count]
    if impossible:
        worst = max(impossible,
                    key=lambda s: s.assigned_villagers() - s.villager_count)
        findings.append(Finding(
            WARNING,
            f"{len(impossible)} step(s) put more villagers on resources than "
            f"the step has - {worst.assigned_villagers()} across food, wood, "
            f"gold and stone at {worst.villager_count} villagers. Loom shows "
            "those numbers as the build's targets, so its VILLS row will ask "
            "for villagers you cannot have yet."))

    return findings


def _icon_findings(path, can_draw):
    """Icon tokens the library has no picture for - words, not failure."""
    with open(path, encoding="utf-8") as handle:
        text = handle.read()

    unknown = sorted({token for token in ICON_TOKEN.findall(text)
                      if not can_draw(token)})
    if unknown:
        return [Finding(
            WARNING,
            f"{len(unknown)} icon(s) are not in Loom's picture library "
            f"({_sample(unknown)}). Those instructions will be written out in "
            "words instead, which reads perfectly well.")]
    return []


def fatal(findings):
    """The findings that mean Loom cannot use the file."""
    return [finding for finding in findings if finding.severity == FATAL]


def warnings(findings):
    return [finding for finding in findings if finding.severity == WARNING]


def describe(build):
    """One line naming a build, for a dialog that has just loaded it."""
    return (f"{build.name} — {civilization_label(build)} — "
            f"{build.author or 'unknown author'} — {len(build.steps)} steps")
