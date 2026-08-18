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

import pytest

from loom import about, config, hud


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
    """The start/stop key ships unbound, so its empty default is vacuously
    "present" to the derived binding test above - this pins the feature by
    name instead. A player learns it exists here or nowhere."""
    everything = " ".join(html for _title, html in about.PAGES).lower()

    assert "start/stop overlay" in everything
    assert "unbound" in everything


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
