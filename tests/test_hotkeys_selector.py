"""
Loom — the hotkey seam, and both backends' key tables, from anywhere.

loom/hotkeys is a package that picks a backend by platform, exactly as
loom/capture does. These tests guard the seam rather than any backend's
grabs: that every backend offers the whole contract, that the platform choice
is what it claims, and - the one that pays for itself on a dual boot - that
each backend's translation table covers every key the shared grammar accepts.

That last one matters more than it looks. A key in keyspec.KEYS with no entry
in a backend's table is a binding a player can save in the launcher and which
then silently never fires on that platform. Checking it needs no X server and
no Windows, because both tables are pure data.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import importlib

import pytest

from loom import hotkeys
from loom.hotkeys import keyspec


# ---- the seam --------------------------------------------------------------

def test_package_exports_the_whole_contract():
    for name in hotkeys.CONTRACT:
        assert callable(getattr(hotkeys, name)), f"{name} missing"


def test_every_known_backend_offers_the_whole_contract():
    """A backend missing a name would fail at the worst moment - the first
    time a player pressed the key, mid-match."""
    for platform, name in hotkeys.BACKENDS.items():
        try:
            module = importlib.import_module(f"loom.hotkeys.{name}")
        except ImportError:
            continue
        for wanted in hotkeys.CONTRACT:
            assert hasattr(module, wanted), f"{name} ({platform}) has no {wanted}"


def test_both_backends_import_off_their_own_platform():
    """The dual-boot guard, same as test_capture_selector's.

    Everything OS-specific in these modules is imported inside a function
    precisely so this works; if somebody hoists an import to the top, this
    fails here rather than on the other machine days later.
    """
    for name in ("windows", "x11"):
        module = importlib.import_module(f"loom.hotkeys.{name}")
        for wanted in hotkeys.CONTRACT:
            assert hasattr(module, wanted)


def test_platform_choice_without_an_override(monkeypatch):
    monkeypatch.delenv("LOOM_HOTKEY_BACKEND", raising=False)
    monkeypatch.setattr(hotkeys.sys, "platform", "win32")
    assert hotkeys.backend_name() == "windows"
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    assert hotkeys.backend_name() == "x11"


def test_override_wins_over_the_platform(monkeypatch):
    monkeypatch.setenv("LOOM_HOTKEY_BACKEND", "somebackend")
    assert hotkeys.backend_name() == "somebackend"


def test_a_platform_with_no_backend_is_a_hotkey_error(monkeypatch):
    """macOS is the real case: it has no backend and must say so rather than
    crash the overlay."""
    monkeypatch.delenv("LOOM_HOTKEY_BACKEND", raising=False)
    monkeypatch.setattr(hotkeys.sys, "platform", "darwin")
    assert hotkeys.backend_name() is None
    with pytest.raises(hotkeys.HotkeyError):
        hotkeys.load_backend()


def test_a_missing_backend_fails_on_use_not_on_import():
    """Importing loom.hotkeys must never take the overlay down. Hotkeys are a
    convenience over a program that advances itself; losing them is not worth
    losing the overlay for."""
    stub = hotkeys._stub(hotkeys.HotkeyError("no hotkeys here"))
    with pytest.raises(hotkeys.HotkeyError):
        stub({}, lambda action: None)


# ---- the Windows table -----------------------------------------------------

def test_windows_has_a_virtual_key_for_every_key_in_the_grammar():
    from loom.hotkeys import windows
    for key in keyspec.KEYS:
        assert isinstance(windows.virtual_key(key), int), key


@pytest.mark.parametrize("key, code", [
    ("A", 0x41), ("Z", 0x5A), ("0", 0x30), ("9", 0x39),
    ("F1", 0x70), ("F12", 0x7B), ("F24", 0x87),
    ("Space", 0x20), ("Escape", 0x1B),
])
def test_windows_virtual_keys_are_the_documented_codes(key, code):
    from loom.hotkeys import windows
    assert windows.virtual_key(key) == code


def test_windows_always_asks_for_no_repeat():
    """Without MOD_NOREPEAT, leaning on "next step" races through the whole
    build order at the keyboard's repeat rate."""
    from loom.hotkeys import windows
    flags = windows.modifier_flags(keyspec.parse("Ctrl+Shift+Q"))

    assert flags & windows.MOD_NOREPEAT
    assert flags & windows.MOD_CONTROL
    assert flags & windows.MOD_SHIFT
    assert not flags & windows.MOD_ALT


def test_windows_rejects_a_key_it_cannot_map():
    from loom.hotkeys import windows
    with pytest.raises(hotkeys.HotkeyError):
        windows.virtual_key("Fnord")


# ---- the X11 table ---------------------------------------------------------

def test_x11_has_a_keysym_name_for_every_key_in_the_grammar():
    from loom.hotkeys import x11
    for key in keyspec.KEYS:
        assert isinstance(x11.keysym_name(key), str), key


@pytest.mark.parametrize("key, name", [
    ("A", "a"), ("Z", "z"), ("5", "5"),
    ("F1", "F1"), ("F24", "F24"),
    ("Enter", "Return"), ("PageUp", "Prior"), ("PageDown", "Next"),
    ("Backspace", "BackSpace"), ("Space", "space"),
])
def test_x11_keysym_names_are_the_x_spellings(key, name):
    """X's names are not the obvious ones - Prior and Next for the page keys,
    BackSpace with a capital S - and getting one wrong is a hotkey that never
    fires with nothing logged."""
    from loom.hotkeys import x11
    assert x11.keysym_name(key) == name


def test_x11_rejects_a_key_it_cannot_map():
    from loom.hotkeys import x11
    with pytest.raises(hotkeys.HotkeyError):
        x11.keysym_name("Fnord")


def test_x11_grabs_every_lock_combination():
    """A grab is for an exact modifier mask, so Num Lock being on makes
    Ctrl+Shift+Q a different mask that does not match. Missing this is a
    hotkey that works until somebody presses Num Lock."""
    from loom.hotkeys import x11

    class FakeX:
        LockMask, Mod2Mask, Mod5Mask = 0x02, 0x10, 0x80

    variants = x11.lock_variants(FakeX)

    assert 0 in variants, "the plain mask must still be grabbed"
    assert 0x02 in variants and 0x10 in variants
    assert 0x02 | 0x10 in variants
    assert 0x02 | 0x10 | 0x80 in variants
    assert len(variants) == 8


def test_x11_modifier_mask_maps_the_four_modifiers():
    from loom.hotkeys import x11

    class FakeX:
        ControlMask, ShiftMask, Mod1Mask, Mod4Mask = 0x04, 0x01, 0x08, 0x40

    mask = x11.modifier_mask(keyspec.parse("Ctrl+Alt+Q"), FakeX)

    assert mask == 0x04 | 0x08
