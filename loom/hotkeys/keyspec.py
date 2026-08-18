"""
Loom — what "Ctrl+Shift+Q" means, without knowing which OS is asking.

Every backend needs the same thing from a binding: which modifiers, and which
key. What each of those is *called* at the OS level differs wildly - Windows
wants MOD_CONTROL and virtual key 0x51, X11 wants ControlMask and a keysym -
so the shared part is parsing and validating the text, and the backends own
the translation.

Keeping that split means the whole grammar is testable on any machine, which
matters here: Linux and Windows are a dual boot, so a rule that can only be
checked on one of them is a rule that gets checked half as often.

Two rules are enforced rather than merely documented.

A binding MUST carry at least one modifier. A global hotkey is registered
with the operating system and SWALLOWED - the game never sees that key again
while Loom runs. Binding a bare "Q" would silently take Q away from the
player mid-match, which is a spectacular way to lose a game, so the grammar
refuses it.

And the key must be one this module knows. A typo like "Ctrl+Shift+Qq" should
fail in the launcher, where somebody can read the message, rather than at
registration time inside the overlay where the only symptom is a hotkey that
never fires.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

# The canonical spelling and order of the modifiers. Order is fixed so that
# "Shift+Ctrl+Q" and "Ctrl+Shift+Q" are the same binding when written back
# out - otherwise two settings that behave identically would compare unequal
# and conflict detection would miss them.
MODIFIERS = ("Ctrl", "Alt", "Shift", "Win")

# What a player might type for each. Lowercased on both sides before lookup.
MODIFIER_ALIASES = {
    "ctrl": "Ctrl", "control": "Ctrl", "ctl": "Ctrl",
    "alt": "Alt", "option": "Alt", "opt": "Alt",
    "shift": "Shift",
    "win": "Win", "super": "Win", "meta": "Win", "cmd": "Win",
    "command": "Win",
}

# The keys a binding may name. Backends map these to their own codes; this is
# the vocabulary they all agree on.
_LETTERS = tuple(chr(code) for code in range(ord("A"), ord("Z") + 1))
_DIGITS = tuple(str(digit) for digit in range(10))
_FUNCTION = tuple(f"F{number}" for number in range(1, 25))
_NAMED = (
    "Space", "Tab", "Enter", "Escape", "Backspace", "Insert", "Delete",
    "Home", "End", "PageUp", "PageDown", "Up", "Down", "Left", "Right",
    "Minus", "Equal", "Comma", "Period", "Slash", "Backslash", "Semicolon",
    "Quote", "LeftBracket", "RightBracket", "Backtick",
)
KEYS = _LETTERS + _DIGITS + _FUNCTION + _NAMED

# What a player might type for the named keys, beyond the canonical spelling.
_KEY_ALIASES = {
    "esc": "Escape", "return": "Enter", "del": "Delete", "ins": "Insert",
    "pgup": "PageUp", "pageup": "PageUp",
    "pgdn": "PageDown", "pagedown": "PageDown",
    "bksp": "Backspace", "backspace": "Backspace",
    "spacebar": "Space",
    "-": "Minus", "=": "Equal", ",": "Comma", ".": "Period",
    "/": "Slash", "\\": "Backslash", ";": "Semicolon", "'": "Quote",
    "[": "LeftBracket", "]": "RightBracket", "`": "Backtick",
}

_KEYS_BY_LOWER = {key.lower(): key for key in KEYS}


class KeySpec:
    """One parsed binding: a set of modifiers and exactly one key.

    Compares and hashes by value, so two spellings of the same combination are
    the same object as far as conflict detection is concerned.
    """

    __slots__ = ("modifiers", "key")

    def __init__(self, modifiers, key):
        # A frozenset rather than a list: "Ctrl+Shift" and "Shift+Ctrl" are
        # the same chord, and order must not make them differ.
        self.modifiers = frozenset(modifiers)
        self.key = key

    def __eq__(self, other):
        return (isinstance(other, KeySpec)
                and self.modifiers == other.modifiers
                and self.key == other.key)

    def __hash__(self):
        return hash((self.modifiers, self.key))

    def __repr__(self):
        return f"KeySpec({text(self)!r})"

    def ordered_modifiers(self):
        """The modifiers in MODIFIERS order, for display and for backends."""
        return tuple(name for name in MODIFIERS if name in self.modifiers)


def parse(binding):
    """Turn "ctrl+shift+q" into a KeySpec. Raises ValueError with a reason.

    The message is written to be shown to a player, because it will be: the
    launcher puts it in the output pane when a binding will not register.
    """
    if not isinstance(binding, str) or not binding.strip():
        raise ValueError("a hotkey needs some keys in it")

    parts = [part.strip() for part in binding.split("+")]
    # "Ctrl++" is a plausible way to mean the + key, and splitting leaves an
    # empty part behind. Rejecting it is honest: "Plus" is not in the
    # vocabulary, so there is nothing useful to do with it.
    if any(not part for part in parts):
        raise ValueError(f"{binding!r} has an empty piece between its + signs")

    modifiers = []
    key = None
    for part in parts:
        lowered = part.lower()
        if lowered in MODIFIER_ALIASES:
            modifier = MODIFIER_ALIASES[lowered]
            if modifier in modifiers:
                raise ValueError(f"{binding!r} names {modifier} twice")
            modifiers.append(modifier)
            continue
        if key is not None:
            raise ValueError(
                f"{binding!r} names two keys ({key} and {part}); a hotkey is "
                f"modifiers plus exactly one key")
        key = _KEY_ALIASES.get(lowered) or _KEYS_BY_LOWER.get(lowered)
        if key is None:
            raise ValueError(f"{binding!r} contains an unknown key: {part!r}")

    if key is None:
        raise ValueError(f"{binding!r} is only modifiers, with no key to press")
    if not modifiers:
        # The load-bearing rule. See the module docstring.
        raise ValueError(
            f"{binding!r} has no modifier. A global hotkey is taken from every "
            f"other program including the game, so a bare key would stop "
            f"working in Age of Empires while Loom is running. Add Ctrl, Alt, "
            f"Shift or Win.")

    return KeySpec(modifiers, key)


def text(spec):
    """The canonical spelling of a KeySpec: "Ctrl+Shift+Q"."""
    return "+".join(spec.ordered_modifiers() + (spec.key,))


def normalise(binding):
    """Canonical spelling of a binding string, or raise ValueError."""
    return text(parse(binding))


def is_disabled(binding):
    """Is this binding switched off?

    An empty string is how a player says "no key for this action", and it has
    to be distinguishable from a broken one - a disabled action is a choice,
    a broken one is a complaint.
    """
    return binding is None or not str(binding).strip()


def problem(binding):
    """Why this binding is unusable, or None if it is fine.

    Disabled counts as fine: nothing is wrong with switching an action off.
    Lets a caller check without writing a try/except round every read.
    """
    if is_disabled(binding):
        return None
    try:
        parse(binding)
    except ValueError as reason:
        return str(reason)
    return None


def conflicts(bindings):
    """Which actions in {action: binding} share a combination.

    Returns a sorted list of (action, other_action) pairs. Two actions on one
    combination is not an error the OS will report - whichever registers first
    simply wins and the other silently never fires - so it has to be caught
    here.
    """
    seen = {}
    clashes = []
    for action in sorted(bindings):
        binding = bindings[action]
        if is_disabled(binding):
            continue
        try:
            spec = parse(binding)
        except ValueError:
            continue                 # a broken binding is problem()'s business
        if spec in seen:
            clashes.append((seen[spec], action))
        else:
            seen[spec] = action
    return clashes
