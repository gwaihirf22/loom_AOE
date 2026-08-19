"""
Loom — resolving a build's @icon@ tokens against the shipped library.

The first community build loaded from buildorderguide.com showed words on
every instruction, and the reason was nothing but file extensions: the
site's export writes @resource/MaleVillDE.jpg@ and @animal/Sheep.png@
where the shipped library holds the identical pictures as .webp. Every
token pointed at the right folder and the right name. Users paste builds
verbatim from community sites - asking them to hand-edit each token's
suffix is exactly the busywork Loom exists to remove - so the lookup
forgives the extension instead.

What it must NOT forgive is the folder. Different icons can legitimately
share a base name across the library's folders, and a wrong picture on an
instruction would be worse than the words: the words are always right.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import pytest

from loom import overlay, paths


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A tiny icon library on the real search path: a player half and a
    shipped half, like the ones asset_search_path answers with."""
    player = tmp_path / "player" / "master_aoe2_images"
    shipped = tmp_path / "shipped" / "master_aoe2_images"
    (shipped / "resource").mkdir(parents=True)
    (shipped / "animal").mkdir(parents=True)
    (shipped / "resource" / "MaleVillDE.webp").write_bytes(b"vill")
    (shipped / "animal" / "Sheep_aoe2DE.webp").write_bytes(b"sheep")
    monkeypatch.setattr(
        paths, "asset_search_path",
        lambda kind: (player.parent / kind, shipped.parent / kind))
    monkeypatch.setattr(
        paths, "find_asset",
        lambda kind, name: next(
            (base / name for base in paths.asset_search_path(kind)
             if (base / name).exists()), None))
    return player, shipped


def test_an_exact_token_resolves_exactly(library):
    _, shipped = library
    found = overlay.find_icon_file("resource/MaleVillDE.webp")
    assert found == shipped / "resource" / "MaleVillDE.webp"


def test_a_different_extension_still_finds_the_picture(library):
    """The buildorderguide case: .jpg and .png tokens, .webp files."""
    _, shipped = library
    assert (overlay.find_icon_file("resource/MaleVillDE.jpg")
            == shipped / "resource" / "MaleVillDE.webp")
    assert (overlay.find_icon_file("animal/Sheep_aoe2DE.png")
            == shipped / "animal" / "Sheep_aoe2DE.webp")


def test_the_name_is_matched_case_insensitively(library):
    _, shipped = library
    assert (overlay.find_icon_file("resource/malevillde.png")
            == shipped / "resource" / "MaleVillDE.webp")


def test_the_folder_is_not_forgiven(library):
    """A name that exists in another folder must NOT be borrowed - the
    words fallback is always right, a wrong picture is not."""
    assert overlay.find_icon_file("animal/MaleVillDE.png") is None


def test_a_picture_the_library_lacks_is_none(library):
    assert overlay.find_icon_file("resource/Trebuchet.png") is None


def test_the_player_s_copy_still_shadows_the_shipped_one(library):
    """The loose lookup walks the same search path as everything else, so
    a player's own picture wins over the shipped one."""
    player, _ = library
    (player / "resource").mkdir(parents=True)
    (player / "resource" / "MaleVillDE.png").write_bytes(b"custom")

    found = overlay.find_icon_file("resource/MaleVillDE.jpg")

    assert found == player / "resource" / "MaleVillDE.png"


def test_every_shipped_build_s_tokens_resolve():
    """The real library against the real builds - the whole point, checked
    end to end. Extension-forgiving on purpose: this must keep passing if
    a build arrives with .png tokens for pictures shipped as .webp."""
    import json
    import re

    tokens = set()
    for build in (paths.PROJECT_ROOT / "builds").glob("*.json"):
        tokens |= set(re.findall(r"@([^@\s]+/[^@\s]+)@",
                                 build.read_text(encoding="utf-8")))
    assert tokens, "no icon tokens found - did the builds move?"
    missing = sorted(t for t in tokens if overlay.find_icon_file(t) is None)
    assert missing == []
