"""
Loom — the Windows APM counter's logic and layout, from any machine.

loom/apmwin.py keeps everything Windows-only inside a function, and describes
the raw input records with fixed-width ctypes rather than the platform's own
widths, so both the struct layout and the counting rule can be checked here
whatever the suite is running on. That is not a convenience: a wrong offset
would read the wrong field and count plausible nonsense - the failure Loom is
least able to notice - and the machine that could catch it is a reboot away.

The privacy contract is tested too, as far as a test can: that the field
naming the key is never read outside the layout. That claim is the reason
this module is Raw Input rather than a keyboard hook, and a claim nothing
checks is a claim that quietly stops being true.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ast
import ctypes
import pathlib

import pytest

from loom import apm, apmwin


# ---- which side of the seam counts APM -------------------------------------

def test_windows_counts_in_the_overlay():
    """Raw Input needs a window and a message pump; the overlay has both."""
    assert apm.counted_in_the_overlay("win32") is True


@pytest.mark.parametrize("platform", ["linux", "darwin", "plan9"])
def test_everywhere_else_uses_a_child_process(platform):
    """The failure mode of the launcher and the overlay disagreeing is
    counting everything twice, which would not look like a bug - it would
    look like the player having a very good game."""
    assert apm.counted_in_the_overlay(platform) is False


# ---- the record layout -----------------------------------------------------

def test_the_structs_are_the_documented_sizes():
    """Fixed-width fields, so these hold on any 64-bit platform. A wrong size
    here means every field after it is read from the wrong bytes."""
    assert ctypes.sizeof(apmwin.RAWINPUTHEADER) == 24
    assert ctypes.sizeof(apmwin.RAWKEYBOARD) == 16
    assert ctypes.sizeof(apmwin.RAWMOUSE) == 24


def test_the_fields_that_are_read_are_where_windows_puts_them():
    assert apmwin.RAWINPUT.data.offset == 24
    assert apmwin.RAWKEYBOARD.Flags.offset == 2
    assert apmwin.RAWMOUSE.usButtonFlags.offset == 4


# ---- what counts as an action ----------------------------------------------

def keyboard(flags=0):
    record = apmwin.RAWINPUT()
    record.header.dwType = apmwin.RIM_TYPEKEYBOARD
    record.data.keyboard.Flags = flags
    return record


def mouse(button_flags=0):
    record = apmwin.RAWINPUT()
    record.header.dwType = apmwin.RIM_TYPEMOUSE
    record.data.mouse.usButtonFlags = button_flags
    return record


def test_a_key_going_down_is_one_action():
    assert apmwin.counts_one_action(keyboard()) == (1, 0)


def test_a_key_coming_back_up_is_not_a_second_action():
    """Otherwise every keystroke counts twice and APM doubles."""
    assert apmwin.counts_one_action(
        keyboard(apmwin.RI_KEY_BREAK)) == (0, 0)


def test_a_mouse_button_going_down_is_one_click():
    assert apmwin.counts_one_action(mouse(0x0001)) == (0, 1)


def test_mouse_movement_is_not_an_action():
    """A record with no button flags is a move. Counting those would make APM
    a measure of how much the player waggles the mouse."""
    assert apmwin.counts_one_action(mouse(0x0000)) == (0, 0)


def test_a_mouse_button_coming_up_is_not_an_action():
    # 0x0002 is RI_MOUSE_LEFT_BUTTON_UP, deliberately not in the down mask.
    assert apmwin.counts_one_action(mouse(0x0002)) == (0, 0)


def test_two_buttons_in_one_record_are_two_actions():
    """A single raw input record can report more than one button."""
    assert apmwin.counts_one_action(mouse(0x0001 | 0x0004)) == (0, 2)


@pytest.mark.parametrize("flag", [0x0001, 0x0004, 0x0010, 0x0040, 0x0100])
def test_all_five_buttons_count(flag):
    assert apmwin.counts_one_action(mouse(flag)) == (0, 1)


def test_the_wheel_is_not_counted():
    """0x0400 is RI_MOUSE_WHEEL. Scrolling in AoE2 is zoom, and a scroll
    burst is not five actions. This differs from the Linux counter, where
    XInput2 reports the wheel as a button press - noted in apmwin's docstring
    so the two are known to differ rather than assumed to match."""
    assert apmwin.counts_one_action(mouse(0x0400)) == (0, 0)


def test_a_device_this_does_not_model_counts_nothing():
    """A gamepad or tablet arrives as RIM_TYPEHID and must fall through
    rather than being counted as something."""
    record = apmwin.RAWINPUT()
    record.header.dwType = 2          # RIM_TYPEHID
    assert apmwin.counts_one_action(record) == (0, 0)


# ---- buckets ---------------------------------------------------------------

def test_a_bucket_prints_the_shape_the_launcher_reads(capsys):
    """The wire contract between the counter and the launcher was pinned by
    nothing at all before this. launcher._on_state_line discriminates on the
    "apm" key and reads "keys" and "clicks"."""
    from loom import statefeed

    counter = apmwin.Counter()
    counter.keys, counter.clicks = 7, 3
    counter.flush()

    payload = statefeed.decode(capsys.readouterr().out.strip())
    assert set(payload) == {"apm"}
    assert payload["apm"]["keys"] == 7
    assert payload["apm"]["clicks"] == 3
    assert "wall" in payload["apm"]


def test_a_bucket_resets_after_it_is_emitted(capsys):
    counter = apmwin.Counter()
    counter.keys, counter.clicks = 7, 3
    counter.flush()
    capsys.readouterr()

    assert (counter.keys, counter.clicks) == (0, 0)


def test_an_empty_bucket_is_still_emitted(capsys):
    """Zero APM during a game is real news - a player who stopped doing
    anything is exactly what the statistic should show."""
    from loom import statefeed

    apmwin.Counter().flush()

    payload = statefeed.decode(capsys.readouterr().out.strip())
    assert payload["apm"]["keys"] == 0


def test_the_bucket_length_matches_the_shared_constant():
    """align() converts counts to a per-minute rate using apm.BUCKET_SECONDS,
    so a counter bucketing at some other length would report a wrong APM
    with no error anywhere."""
    assert apmwin.Counter().bucket_seconds == apm.BUCKET_SECONDS


# ---- the privacy contract --------------------------------------------------

def apmwin_syntax_tree():
    source = pathlib.Path(apmwin.__file__).read_text(encoding="utf-8")
    return ast.parse(source)


def attributes_named(tree, wanted):
    """Every real attribute access of a given name, ignoring prose.

    The AST rather than a text search on purpose: this module's docstring
    discusses both VKey and SetWindowsHookEx at length, precisely because
    they are the things it does NOT do, and a grep would flag the
    explanation as the offence.
    """
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == wanted]


def test_the_key_identity_is_never_read():
    """The claim this module rests on, checked rather than asserted in prose.

    RAWKEYBOARD.VKey is the field naming which key was pressed. It has to be
    declared, because the layout of everything after it depends on it - but
    it must appear only in the layout, never in an expression. If a future
    change starts reading it, this module stops being able to make its
    promise, and that should break a test rather than a trust.
    """
    tree = apmwin_syntax_tree()

    assert attributes_named(tree, "VKey") == [], (
        "something is reading RAWKEYBOARD.VKey - the field that says which "
        "key was pressed. The privacy contract in this module's docstring "
        "says it never does.")

    declared = [node for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value == "VKey"]
    assert len(declared) == 1, "VKey should still be declared in the layout"


def test_no_keyboard_hook_is_used():
    """SetWindowsHookEx is the mechanism keyloggers use, and the thing
    antivirus software looks for. Raw Input is the whole reason this module
    can claim what it claims."""
    tree = apmwin_syntax_tree()

    assert attributes_named(tree, "SetWindowsHookEx") == []
    assert attributes_named(tree, "SetWindowsHookExW") == []


def test_it_asks_for_raw_input_instead():
    """The positive half: the mechanism it does use is the one documented."""
    tree = apmwin_syntax_tree()

    assert attributes_named(tree, "RegisterRawInputDevices")
    assert attributes_named(tree, "GetRawInputData")
