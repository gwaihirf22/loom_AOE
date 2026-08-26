"""
Loom — the How-to-use window's content and its once-only gate.

The window is widgets, which I check by using it. What earns automated tests
is the content list and the first-run flag: an empty page would ship as a
blank window nobody notices until a user meets it, and a broken gate either
nags every launch or never appears at all.

The compatibility page is the reason this window exists. Loom's templates
were cut from the Anne_HK resource panel, and on the stock panel the anchor
match falls under threshold - so Loom never finds the HUD and waits forever
without explaining itself. A test pins that the page still names the mods,
because the day someone tidies that text away is the day new players start
concluding Loom is broken.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import re

import pytest

from loom import about, checklist, config, hud, overlay, paths


def test_every_page_has_a_title_and_a_body():
    for index, page in enumerate(about.PAGES):
        title, html = page
        assert title.strip(), f"page {index} has no title"
        assert len(html.strip()) > 80, f"page {index} has no real content"


def test_the_compatibility_page_names_every_supported_skin():
    """The one piece of information that stops a new player concluding Loom
    is broken. It must also stay TRUE: this page once said the stock HUD was
    unsupported, which was correct when written and wrong a day later, so the
    test now checks the page against loom.hud.PROFILES rather than against a
    fixed list somebody has to remember to update."""
    # Punctuation is stripped from both sides: the profile ids are internal
    # ("annehk") while the page names skins the way a player would read them
    # ("Anne_HK"). The test is that every supported skin is mentioned, not
    # that the prose adopts the code's spelling.
    def squashed(text):
        return "".join(c for c in text.lower() if c.isalnum())

    everything = squashed(" ".join(html for _title, html in about.PAGES))
    for profile in hud.PROFILES:
        assert squashed(profile.name) in everything, (
            f"the {profile.name} HUD profile is supported but the How-to-use "
            "page never mentions it")
    assert "100%" in " ".join(html for _t, html in about.PAGES), \
        "the HUD scale advice went missing"


def test_pages_are_a_plain_list_so_adding_one_is_appending():
    assert isinstance(about.PAGES, list)
    assert all(isinstance(page, tuple) and len(page) == 2
               for page in about.PAGES)


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")


def test_about_is_unseen_on_a_fresh_install(clean_config):
    assert config.about_seen() is False


def test_dismissing_it_sticks(clean_config):
    config.set_about_seen(True)
    assert config.about_seen() is True


def test_a_corrupt_flag_does_not_hide_the_window(clean_config):
    """Default-off polarity: anything but a deliberate True means show it.
    A mangled settings file should cost a player one extra dialog, not the
    explanation of why Loom cannot see their HUD."""
    config.save({"about_seen": "yes please"})
    assert config.about_seen() is False


def test_the_hotkeys_page_names_every_default_binding():
    """Same discipline as the HUD-skins test above: derive the truth from the
    code rather than from a list somebody has to remember to update.

    A player who has never opened the launcher's settings learns the bindings
    here or nowhere, so a default that changed without the page changing
    would teach them a key that does nothing.
    """
    def squashed(text):
        return "".join(c for c in text.lower() if c.isalnum())

    everything = squashed(" ".join(html for _title, html in about.PAGES))
    for action, binding in config.DEFAULT_HOTKEYS.items():
        assert squashed(binding) in everything, (
            f"{action} defaults to {binding} but the How-to-use page never "
            f"mentions it")


def test_the_hotkeys_page_explains_that_the_game_loses_the_keys():
    """The one genuinely surprising consequence, and the one that could cost
    somebody a match: a combination Loom registers is taken system-wide, so
    Age of Empires stops seeing it."""
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "does not see" in everything or "not see them" in everything


def test_the_hotkeys_page_says_the_hold_expires_by_itself():
    """The whole shape of the feature. If a player thinks pressing a step key
    switches following off for good, they will stop using it - or worse, keep
    playing while believing the panel is still tracking the game."""
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "ten seconds" in everything
    assert "manual" in everything


def test_the_hotkeys_page_mentions_the_start_stop_key():
    """Pins the feature by name, and since 1.0.5 the page must also carry
    the fact that replaced "ships unbound": hotkeys as a whole ship
    switched off, and the master switch is what turns the working set on.
    A player learns both here or nowhere."""
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "start/stop overlay" in everything
    assert "switched off" in everything
    assert "use hotkeys" in everything


def test_the_recommended_mods_are_linked():
    """Both companion mods, by their mod-hub URLs rather than their display
    names - the URL is the part that must not rot, and the names on the hub
    can change under us."""
    everything = " ".join(html for _title, html in about.PAGES)

    assert "ageofempires.com/mods/details/3762" in everything   # Anne_HK
    assert "ageofempires.com/mods/details/2532" in everything   # transparent UI


def test_the_mods_are_recommended_not_required():
    """The framing matters: Loom reads the stock HUD as it ships, and a page
    that read as a requirements list would turn away exactly the new player
    it exists to help."""
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "neither is required" in everything


def test_the_panel_page_explains_the_states_that_matter():
    """The two states a player must not misread: MANUAL (the panel is not
    following the game) and waiting (Loom cannot see, and says so instead of
    showing stale advice)."""
    everything = " ".join(html for _title, html in about.PAGES)

    assert "MANUAL" in everything
    assert "waiting for the game" in everything


def test_the_panel_page_tells_the_two_kinds_of_tick_apart():
    """The one thing a player must not misread about the checklist.

    A green bullet means Loom READ the game announcing it; a faded one means
    Loom is assuming because the build moved on. Those carry very different
    weight, and a page that described only "ticked off" would invite the
    player to trust a guess as if it were a reading - which is the failure
    the whole project is built to avoid.

    Derived from checklist's own states, on the same principle as the
    HUD-skin page: if a state exists, the page has to account for it. Not by
    looking for the state's NAME, though - "observed" is the code's word and
    would be jargon on a page a player reads. The mapping below is the point
    of the test: adding a state to checklist.py without deciding how to
    explain it in English fails here.
    """
    explained = {
        checklist.NOT_DONE: "still to do",
        checklist.ASSUMED: "assumed",
        checklist.OBSERVED: "loom saw it happen",
        checklist.UNCONFIRMED: "not confirmed",
    }
    everything = " ".join(
        " ".join(html.split()) for _title, html in about.PAGES).lower()

    for state in (checklist.NOT_DONE, checklist.ASSUMED,
                  checklist.UNCONFIRMED, checklist.OBSERVED):
        phrase = explained.get(state)
        assert phrase, f"no player-facing wording decided for {state!r}"
        assert phrase in everything,             f"the page never explains {state!r} items ({phrase!r})"


def test_the_panel_page_admits_what_can_never_be_confirmed():
    """Assume-only items are a property of the game, not a bug in Loom.

    Villager re-tasking is never announced, so those items can only ever be
    assumed. Undocumented, a bullet that never goes green reads as broken.
    """
    everything = " ".join(
        " ".join(html.split()) for _title, html in about.PAGES).lower()

    assert "can never go green" in everything


def test_the_appearance_page_offers_the_suggested_mix_as_taste():
    """The author's own 20%/90% mix is a suggestion and must read as one -
    "entirely your taste" - not as the correct setting."""
    everything = " ".join(
        " ".join(html.split()) for _title, html in about.PAGES).lower()

    assert "20%" in everything and "90%" in everything
    assert "your taste" in everything


def test_the_builds_page_names_both_community_sources():
    """Where to GET builds and where to WRITE them - by URL, the part that
    must not rot."""
    everything = " ".join(html for _title, html in about.PAGES)

    assert "buildorderguide.com" in everything
    assert "rts-overlay.github.io" in everything


def test_the_placement_page_mentions_the_reset():
    everything = " ".join(html for _title, html in about.PAGES)

    assert "Reset position" in everything


def test_the_builds_page_names_the_folder_the_files_actually_go_in():
    """Derived from paths, not typed into the prose.

    The folder differs per OS, and a player who is told the wrong one is
    worse off than a player told nothing: the file lands somewhere Loom
    never looks and the build simply does not appear. Same discipline as
    the HUD-skins and hotkeys pages above.
    """
    everything = " ".join(html for _title, html in about.PAGES)

    assert str(paths.DATA_DIR / "builds") in everything


def test_the_builds_page_explains_the_icon_tokens():
    """Why one build draws pictures and another shows words - the question
    that looked like a bug in Loom and was only ever an absence of tokens.
    The example must be a real token, so it is checked against the library
    rather than merely being present as text."""
    everything = " ".join(html for _title, html in about.PAGES)

    tokens = re.findall(r"@([^@\s<]+/[^@\s<]+)@", everything)
    assert tokens, "the page no longer shows what a token looks like"
    for token in tokens:
        assert overlay.find_icon_file(token) is not None, (
            f"the page teaches {token!r}, which Loom cannot draw")


def test_the_builds_page_says_a_missing_picture_falls_back_to_words():
    """The reassurance that stops somebody concluding their build is
    broken when it is merely written without icons."""
    everything = " ".join(
        " ".join(html.split()) for _title, html in about.PAGES).lower()

    assert "in words" in everything


def test_the_recorded_game_page_states_the_after_the_match_boundary():
    """The one promise on these pages that is about trust rather than use.

    Loom reads the game's own .aoe2record for statistics, and that file
    contains every command BOTH players issued, fog included. The whole
    defence of doing it at all is that it happens only after the match has
    ended - so a page that describes the capability without the boundary
    would be advertising something that sounds exactly like a maphack.

    Pinned as words rather than derived, because the fact being asserted
    lives in loom/replay.py's is_finished and there is nothing on these
    pages to derive it FROM. What this catches is the edit that trims the
    caveat while keeping the feature.
    """
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "recorded game" in everything, "the capability is not described"
    # The caveat, not the feature. "maphack" is the word that carries it -
    # an edit that keeps the capability and drops the reason why it waits
    # for the match to end is exactly what this is here to fail on.
    assert "maphack" in everything, "the page describes the feature but not why it waits"
    assert "after a match ends" in everything


def test_the_fair_play_promise_is_backed_by_the_code(tmp_path):
    """The docs must not outrun the program.

    The How-to-use page and the README both tell a player that Loom will
    not open a recorded game the match is still writing. That is a claim
    about *behaviour*, and a prose test can only ever check that the
    sentence is present - it would go on passing if the guarantee were
    removed tomorrow, which is precisely how the original hole survived:
    the test guarding it read is_finished's SOURCE for a constant name and
    passed for the whole time harvest() was walking past it.

    So this asks the program instead. If either refusal goes away, the
    pages are lying and this fails - which is the only way a documentation
    promise about behaviour can be kept honest.
    """
    from loom import replay

    live = tmp_path / "rec.aoe2record"
    live.write_bytes(bytes(64))
    with pytest.raises(replay.GameStillRunning):
        replay.harvest(live)

    still_writing = tmp_path / "MP Replay v101.103 @2026.08.26 010000 (2).aoe2record"
    still_writing.write_bytes(bytes(64))
    with pytest.raises(replay.GameStillRunning):
        replay.harvest(still_writing)


def test_declining_to_look_is_not_reported_as_a_broken_file():
    """Its own exception type, so a caller cannot flatten it into "could
    not read that recorded game". The picker used to do exactly that, which
    would have reported Loom's own boundary as a fault in the player's
    file."""
    from loom import replay

    assert issubclass(replay.GameStillRunning, Exception)
    assert replay.GameStillRunning is not Exception
