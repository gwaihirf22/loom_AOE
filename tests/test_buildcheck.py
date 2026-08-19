"""
Loom — the check run on a build order before it joins the library.

The policy this file pins is the interesting part, more than any single
rule: **a build is refused only when Loom cannot use it at all.** Everything
else is said and allowed. That line matters because the format belongs to
the community, not to Loom - real published builds leave times off, write
resource rows as shorthand, and use icon names from whichever site exported
them. A checker that turned any of those into a refusal would reject builds
that work, which is a worse failure than a build that loads with a caveat.

The other rule worth stating: "will it load" is answered by loading it. The
fatal cases below are all real BuildOrder failures, not a second opinion
about the schema that could drift away from the loader.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import json

import pytest

from loom import buildcheck, overlay, paths


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def step(villagers, time="1:00", notes=None, resources=None):
    """A step that is fine unless the test makes it otherwise.

    None means "give me the sensible default"; an empty list is a test
    deliberately asking for a step with no instructions, so the two cannot
    be collapsed with `or`.
    """
    if notes is None:
        notes = ["do the thing"]
    if resources is None:
        resources = {"food": villagers, "wood": 0, "gold": 0, "stone": 0}
    return {
        "villager_count": villagers,
        "time": time,
        "resources": resources,
        "notes": notes,
    }


def build_file(tmp_path, *steps, name="Test"):
    return write(tmp_path, "candidate.json",
                 {"name": name, "build_order": list(steps)})


# ---- refused: Loom genuinely cannot use these -----------------------------

def test_a_file_that_is_not_json_is_refused(tmp_path):
    path = write(tmp_path, "candidate.json", "{ this is not json")

    build, findings = buildcheck.inspect(path)

    assert build is None
    assert buildcheck.fatal(findings)
    assert "JSON" in findings[0].message


def test_the_message_for_broken_json_says_where_to_look(tmp_path):
    """Pasting a build into a text editor is the documented way to get one,
    and truncating it is the way that goes wrong - so the message points at
    that rather than only naming the parser's complaint."""
    path = write(tmp_path, "candidate.json", '{"build_order": [{"vill')

    _build, findings = buildcheck.inspect(path)

    assert "cut off" in findings[0].message


def test_json_of_the_wrong_shape_is_refused(tmp_path):
    """A bare list is valid JSON and not a build order. BuildOrder raises
    AttributeError on it, which is exactly what available_builds catches."""
    path = write(tmp_path, "candidate.json", [{"villager_count": 6}])

    build, findings = buildcheck.inspect(path)

    assert build is None
    assert "build_order" in findings[0].message


def test_a_build_with_no_steps_is_refused(tmp_path):
    path = build_file(tmp_path)

    build, findings = buildcheck.inspect(path)

    assert build is None
    assert "no steps" in findings[0].message


def test_a_step_whose_numbers_are_not_numbers_is_refused(tmp_path):
    """int("six") is where BuildOrder raises, so this file would have
    appeared only as a line in the launcher's output pane."""
    path = build_file(tmp_path, {"villager_count": "six", "time": "1:00"})

    build, findings = buildcheck.inspect(path)

    assert build is None


def test_a_missing_file_is_refused_rather_than_raising(tmp_path):
    build, findings = buildcheck.inspect(tmp_path / "nothing.json")

    assert build is None
    assert buildcheck.fatal(findings)


# ---- allowed, with something said -----------------------------------------

def test_a_good_build_passes_with_nothing_to_say(tmp_path):
    path = build_file(tmp_path, step(6, "1:15"), step(10, "2:55"))

    build, findings = buildcheck.inspect(path)

    assert build is not None
    assert findings == []


def test_steps_that_go_backwards_are_a_warning_not_a_refusal(tmp_path):
    """The real consequence, and why it is worth saying: current_index stops
    at the first step the player has not reached, so a step that goes
    backwards can sit behind one that never lets it through."""
    path = build_file(tmp_path, step(20, "5:00"), step(10, "2:00"))

    build, findings = buildcheck.inspect(path)

    assert build is not None, "it still loads, so it is not refused"
    messages = " ".join(f.message for f in buildcheck.warnings(findings))
    assert "not in order" in messages


def test_a_build_with_no_times_is_warned_about_in_loom_s_own_terms(tmp_path):
    """Times are optional to the format and load fine - Step.time is None
    and current_index skips the check. What the player needs to know is the
    consequence: repeated villager counts cannot be told apart."""
    path = build_file(tmp_path, step(6, time=None), step(10, time=None))

    build, findings = buildcheck.inspect(path)

    assert build is not None
    messages = " ".join(f.message for f in findings)
    assert "villager count alone" in messages


def test_fewer_villagers_on_resources_than_the_step_has_is_normal(tmp_path):
    """The rule that had to be learned from the shipped library rather than
    guessed: across Loom's own builds, ten steps assign FEWER villagers to
    resources than the step has and only one assigns more. That is not
    sloppiness - the villager walling, luring or building a Barracks is on
    no resource at all. Warning here would fire on half the community's
    builds, and a warning that common is one nobody reads."""
    path = build_file(
        tmp_path,
        step(20, resources={"food": 5, "wood": 5, "gold": 0, "stone": 0}))

    _build, findings = buildcheck.inspect(path)

    assert findings == []


def test_more_villagers_on_resources_than_exist_is_flagged(tmp_path):
    """The direction that cannot be explained away: 25 villagers spread
    across the resources at a step with 21. Found in a real community build
    on the day this check was written."""
    path = build_file(
        tmp_path,
        step(21, resources={"food": 11, "wood": 10, "gold": 4, "stone": 0}))

    build, findings = buildcheck.inspect(path)

    assert build is not None, "still usable - it is a bad row, not a bad file"
    assert "more villagers on resources" in " ".join(
        f.message for f in findings)


def test_a_negative_resource_row_is_left_alone(tmp_path):
    """The format writes -1 for "not specified", so summing such a step
    means nothing. Loom's own 19popfeudaldrush has a step summing to -4."""
    path = build_file(
        tmp_path,
        step(20, resources={"food": -1, "wood": -1, "gold": -1, "stone": -1}))

    _build, findings = buildcheck.inspect(path)

    assert findings == []


def test_a_step_with_no_instruction_is_flagged(tmp_path):
    path = build_file(tmp_path, step(6, notes=[]))

    _build, findings = buildcheck.inspect(path)

    assert "no instruction text" in " ".join(f.message for f in findings)


# ---- icons ----------------------------------------------------------------

def test_icons_loom_cannot_draw_are_named_but_not_fatal(tmp_path):
    path = build_file(tmp_path, step(6, notes=["Send @animal/Wyvern.webp@"]))

    build, findings = buildcheck.inspect(path, can_draw=lambda _token: False)

    assert build is not None
    message = " ".join(f.message for f in findings)
    assert "animal/Wyvern.webp" in message
    assert "in words" in message, "the fallback must be named as harmless"


def test_the_icon_check_is_skipped_when_nobody_can_answer(tmp_path):
    """Left out, buildcheck stays free of the overlay - and so of Qt, which
    is what lets this whole module be tested without a display."""
    path = build_file(tmp_path, step(6, notes=["Send @animal/Wyvern.webp@"]))

    _build, findings = buildcheck.inspect(path)

    assert "Wyvern" not in " ".join(f.message for f in findings)


def test_a_stray_at_sign_is_not_mistaken_for_an_icon(tmp_path):
    path = build_file(tmp_path, step(6, notes=["Sell @ the market"]))

    _build, findings = buildcheck.inspect(path, can_draw=lambda _t: False)

    assert findings == []


# ---- the shipped library --------------------------------------------------

def can_draw(token):
    return overlay.find_icon_file(token) is not None


def test_no_shipped_build_would_be_refused_by_the_importer():
    """The end-to-end one, and the hard invariant: Loom must never ship a
    build its own Import button would turn away."""
    for path in sorted((paths.PROJECT_ROOT / "builds").glob("*.json")):
        build, findings = buildcheck.inspect(path, can_draw)
        assert build is not None, (
            f"{path.name} would be refused on import: "
            f"{[f.message for f in findings]}")


def test_loom_s_own_builds_have_nothing_at_all_to_report():
    """Softer than the check above, and aimed only at the two builds I
    wrote myself. The community transcriptions are somebody else's numbers
    and may carry a caveat honestly; mine have no such excuse."""
    for name in ("fast_castle.json", "uncounterable_fast_castle.json"):
        path = paths.PROJECT_ROOT / "builds" / name
        _build, findings = buildcheck.inspect(path, can_draw)
        assert findings == [], f"{name}: {[f.message for f in findings]}"


def test_describe_names_the_build_for_a_dialog():
    build, _findings = buildcheck.inspect(
        paths.PROJECT_ROOT / "builds" / "fast_castle.json")

    described = buildcheck.describe(build)

    assert build.name in described
    assert str(len(build.steps)) in described


def test_the_two_severities_partition_the_findings(tmp_path):
    path = build_file(tmp_path, step(20, "5:00"), step(10, "2:00"))

    _build, findings = buildcheck.inspect(path)

    assert (len(buildcheck.fatal(findings)) + len(buildcheck.warnings(findings))
            == len(findings))


@pytest.mark.parametrize("count", [1, 5, 20])
def test_long_lists_are_sampled_rather_than_dumped(count):
    """A dialog naming forty tokens teaches less than one naming three."""
    sample = buildcheck._sample([f"item{n}" for n in range(count)])

    assert sample.count(",") < buildcheck.MAX_EXAMPLES + 1
    if count > buildcheck.MAX_EXAMPLES:
        assert f"{count - buildcheck.MAX_EXAMPLES} more" in sample
