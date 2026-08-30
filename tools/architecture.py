"""The oracle that keeps `docs/architecture.md` honest.

A diagram of a codebase is the most useful document there is right up until
the code moves, at which point it becomes the most confidently wrong one.
Nothing warns you: the picture still renders, still looks authoritative, and
quietly describes a system that no longer exists.

Loom has solved this once before. The How-to-use page names the supported HUD
skins, and `tests/test_about.py` reads them from `hud.PROFILES` rather than
from a list typed into the test - so the page "cannot drift out of date the
way it did the day stock support landed". This is the same trick aimed at a
picture instead of a paragraph.

The shape is deliberately the one `tools/notif_oracle.py` uses. There, an
independent OCR engine audits the corpus LABELS and is never itself the
reader; here, the import graph read straight out of the source audits the
DRAWING and is never itself the diagram. A generated graph of 43 modules is
unreadable and says nothing about why any edge exists - it makes a fine
ruler and a terrible map.

The three rules, and the asymmetry that matters:

1. **Every drawn box is a real module** (or a declared external). A typo
   becomes a phantom component nobody can find.
2. **Every drawn arrow is a real import.** `A --> B` reads "A feeds B", so
   the source must show B importing A. A map may SIMPLIFY - `paths` is
   imported by half the tree and drawing it would bury everything else - but
   it may never LIE. Omission is allowed; invention is not.
3. **Every module appears somewhere.** This is the half that catches drift:
   a module added next month is invisible on the map until someone draws it,
   and rule 3 is what makes that a failing test rather than a silent gap.

A block may opt down to FUNCTION granularity by naming the file its boxes
live in (`%% functions: loom/queue.py`). Not every story is module-shaped -
`production` never imports `queue`, it is handed slot readings as data - and
such a block is checked against that file's own definitions instead of
against the import graph. Different oracle, same promise.

    python -m tools.architecture            # the real graph, as text
    python -m tools.architecture --check    # gate the drawing against it
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import ast
import argparse
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loom import paths  # noqa: E402

DIAGRAM_DOC = paths.PROJECT_ROOT / "docs" / "architecture.md"

# Boxes that are deliberately not modules: the world the code sits in. Held
# here rather than inferred, so that a MISSPELLED module name is an error
# instead of quietly becoming scenery.
EXTERNAL = {
    "game", "screen", "player", "disk", "hotkey", "replay", "builds",
}

# Modules a map is allowed to leave out entirely. Only for things imported
# so widely that drawing them buries the picture - `paths` reaches nearly
# everything and says nothing about how the system works.
MAY_BE_OMITTED = {"paths"}

ARROW = re.compile(r"^(.*?)\s*-{1,2}[.-]?->\s*(?:\|[^|]*\|\s*)?(.*)$")
LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)")


def module_imports():
    """Every module under loom/, and which other loom modules it imports.

    Handles all the shapes the codebase actually uses. `from . import a, b`
    arrives with no module name and the modules as the NAMES, while
    `from .x import y` arrives with the module as `x` and `y` as a name -
    read the wrong one and every dependency comes out as a function.

    Includes the four entry-point scripts at the repo root. Without them six
    modules - reader, debuglog, apmwin, passthrough, pace, launcher - are
    imported by nothing inside the package and look like orphans, when in
    fact they are exactly where the applications begin. The entry points ARE
    the architecture's top layer.

    A package's SUBMODULES are read too, and what they import is attributed
    to the package - `loom/capture/x11.py` counts as `capture`, because the
    map draws a box per subsystem and the per-OS backends are the inside of
    one. Reading only `__init__.py` missed every dependency a backend has
    and nothing else has, which is exactly where the platform-specific ones
    live: when xconnect landed, capture and hotkeys both took a real
    dependency on it that this function could not see, and the check called
    the correctly-drawn arrow a disagreement.
    """
    found = {}
    sources = (sorted(glob.glob(str(paths.PROJECT_ROOT / "loom" / "*.py")))
               + sorted(glob.glob(str(paths.PROJECT_ROOT / "loom" / "*"
                                      / "__init__.py")))
               + sorted(glob.glob(str(paths.PROJECT_ROOT / "loom" / "*"
                                      / "*.py")))
               + sorted(glob.glob(str(paths.PROJECT_ROOT / "loom_*.py"))))
    for source in sources:
        if os.path.dirname(source) == str(paths.PROJECT_ROOT):
            name = os.path.splitext(os.path.basename(source))[0]
        else:
            name = os.path.relpath(source, str(paths.PROJECT_ROOT / "loom"))
            name = os.path.splitext(name)[0].replace("\\", "/")
            name = name.replace("/__init__", "")
        if name == "__init__":
            continue
        with open(source, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module is None:               # from . import a, b
                    imports.update(alias.name for alias in node.names)
                elif node.level:                      # from .x import y
                    imports.add(node.module.split(".")[0])
                elif node.module == "loom":           # from loom import a
                    imports.update(alias.name for alias in node.names)
                elif node.module.startswith("loom."):  # from loom.x import y
                    imports.add(node.module[len("loom."):].split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("loom."):
                        imports.add(alias.name[len("loom."):].split(".")[0])
        # A submodule is part of its package's box, so "capture/x11" is
        # folded into "capture" and its imports joined with the rest of the
        # package's rather than replacing them.
        found.setdefault(name.split("/")[0], set()).update(imports)
    return {name: {dep for dep in deps if dep in found}
            for name, deps in found.items()}


def _node_id(text):
    """The id out of a mermaid node, with any label shape stripped off."""
    match = LABEL.match(text.strip())
    return match.group(1) if match else None


# A block may say it is drawn at function granularity instead of module
# granularity, by naming the file its boxes live in:
#
#     ```mermaid
#     %% functions: loom/queue.py
#     flowchart LR
#         classify_tint --> wash_against_icon
#     ```
#
# Some stories are not module-shaped. `production` never imports `queue` -
# it is handed slot readings as plain data - so the most useful picture of
# how a queue cell becomes a belief cannot be drawn with module boxes at
# all, and before this it could not be drawn here at all.
#
# The escape hatch keeps the promise rather than spending it. A declared
# block is not exempt from checking, it is checked against a DIFFERENT
# oracle: every box must be a function or constant that really is defined
# in the named file. So the rule is the same one the module blocks live by
# - a map may simplify, it may not invent - applied one level down. What a
# declared block does NOT do is satisfy rule 3: drawing a function is not
# drawing its module, and a module still has to appear somewhere.
FUNCTION_BLOCK = re.compile(r"^%%\s*functions:\s*(\S+)")


def _blocks(markdown):
    """Every mermaid block, as (declared source file or None, lines)."""
    blocks = []
    inside, source, lines = False, None, []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if inside:
                blocks.append((source, lines))
            inside = stripped.startswith("```mermaid")
            source, lines = None, []
            continue
        if not inside:
            continue
        declared = FUNCTION_BLOCK.match(stripped)
        if declared:
            source = declared.group(1)
            continue
        if not stripped.startswith("%%"):
            lines.append(stripped)
    if inside:
        blocks.append((source, lines))
    return blocks


def _edges_in(lines):
    edges = []
    for line in lines:
        match = ARROW.match(line)
        if not match:
            continue
        feeds, fed = _node_id(match.group(1)), _node_id(match.group(2))
        if feeds and fed:
            edges.append((feeds, fed))
    return edges


def diagram_edges(markdown):
    """Every (feeds, fed) pair drawn at MODULE granularity in a document.

    Function-level blocks are deliberately absent: their boxes are not
    modules, so measuring them against the import graph would report a
    dozen phantom modules. They get their own check in function_complaints.
    """
    edges = []
    for source, lines in _blocks(markdown):
        if source is None:
            edges.extend(_edges_in(lines))
    return edges


def defined_names(source):
    """Every function, class and module-level constant defined in a file."""
    path = paths.PROJECT_ROOT / source
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                names.update(child.name for child in node.body
                             if isinstance(child, (ast.FunctionDef,
                                                   ast.AsyncFunctionDef)))
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets
                         if isinstance(target, ast.Name))
    return names


def function_complaints(markdown=None):
    """Every box in a function-level block that names nothing real."""
    if markdown is None:
        with open(DIAGRAM_DOC, encoding="utf-8") as handle:
            markdown = handle.read()
    problems = []
    for source, lines in _blocks(markdown):
        if source is None:
            continue
        try:
            names = defined_names(source)
        except (OSError, SyntaxError):
            problems.append(
                f"a diagram declares '%% functions: {source}' but that file "
                f"cannot be read - the declaration is what makes the block "
                f"checkable, so a wrong path turns the check off silently")
            continue
        drawn = {name for edge in _edges_in(lines) for name in edge}
        for name in sorted(drawn):
            if name not in names and name not in EXTERNAL:
                problems.append(
                    f"box {name!r} is not defined in {source} - a function "
                    f"box that names nothing real is the same phantom a "
                    f"module box would be, one level down")
    return problems


LAYOUT_DOC = paths.PROJECT_ROOT / "CLAUDE.md"


def layout_block(markdown=None):
    """The fenced block under CLAUDE.md's "## Layout" heading, or None.

    None means the document is not here to be read. That is not a fault:
    CLAUDE.md is the project's working agreement and tools/release.py
    strips it from the published snapshot, so the released tree runs this
    suite without it. Raising there took the whole test session down with a
    FileNotFoundError.
    """
    if markdown is None:
        if not LAYOUT_DOC.exists():
            return None
        with open(LAYOUT_DOC, encoding="utf-8") as handle:
            markdown = handle.read()
    start = markdown.index("```", markdown.index("## Layout"))
    return markdown[start:markdown.index("```", start + 3)]


def unlisted_modules(block=None, imports=None):
    """Modules CLAUDE.md's Layout does not name.

    The map and the Layout list do different jobs and neither should copy
    the other: the map says how the parts CONNECT, the list says what each
    part IS. A reader needs both, so both have to be complete - and half the
    boxes on the map were never explained anywhere near it.

    A package is named by its directory (`capture/`), a module by its file
    (`anchor.py`), so both spellings count as named. Anything else here is a
    module the project's own map of itself does not admit exists, which is
    the fault this whole file was written to stop, in the document it has
    already happened to twice.
    """
    imports = module_imports() if imports is None else imports
    block = layout_block() if block is None else block
    if block is None:
        # No Layout to check against - see layout_block. Empty here would be
        # a gate turned into decoration if anything ASSERTED on it, so the
        # two tests that do are skipped when LAYOUT_DOC is missing rather
        # than passing vacuously. The halves live in different files and
        # only make sense together, which is why each says so.
        return []
    return sorted(name for name in imports
                  if f"{name}.py" not in block and f"{name}/" not in block)


def complaints(markdown=None, imports=None):
    """Every way the drawing and the source disagree. Empty means honest."""
    imports = module_imports() if imports is None else imports
    if markdown is None:
        with open(DIAGRAM_DOC, encoding="utf-8") as handle:
            markdown = handle.read()
    edges = diagram_edges(markdown)

    problems = []
    drawn = {name for edge in edges for name in edge}
    for name in sorted(drawn):
        if name not in imports and name not in EXTERNAL:
            problems.append(
                f"box {name!r} is neither a module in loom/ nor a declared "
                f"external - a typo becomes a component nobody can find")

    for feeds, fed in edges:
        if feeds in EXTERNAL or fed in EXTERNAL:
            continue
        if feeds not in imports or fed not in imports:
            continue                       # already reported above
        if feeds not in imports[fed]:
            problems.append(
                f"arrow {feeds} --> {fed} says {feeds} feeds {fed}, but "
                f"{fed} does not import {feeds}")

    for name in unlisted_modules(imports=imports):
        problems.append(
            f"module {name!r} is not named in CLAUDE.md's Layout - the map "
            f"says how the parts connect, the Layout says what each one IS, "
            f"and a reader needs both")

    for name in sorted(imports):
        if name not in drawn and name not in MAY_BE_OMITTED:
            problems.append(
                f"module {name!r} appears in no diagram - a part of the "
                f"system the map does not admit exists")

    problems.extend(function_complaints(markdown))
    return problems


def neighbourhood(name, imports=None, reach=1):
    """A mermaid block for just what surrounds one module.

    The whole map is 47 modules, and 47 modules on one page is a thing you
    look at rather than a thing you use. Working INSIDE a subsystem, the
    useful question is narrower: what feeds `queue`, and what does `queue`
    feed. This answers that and nothing else.

    Generated rather than drawn, so it is true by construction and needs no
    gate of its own - the opposite trade from docs/architecture.md, and the
    reason both exist.
    """
    imports = module_imports() if imports is None else imports
    if name not in imports:
        raise KeyError(name)

    edges = set()
    frontier = {name}
    seen = set()
    for _step in range(max(1, reach)):
        following = set()
        for module in frontier - seen:
            seen.add(module)
            for fed_by in imports.get(module, ()):     # module imports fed_by
                edges.add((fed_by, module))
                following.add(fed_by)
            for other, deps in imports.items():        # and who imports module
                if module in deps:
                    edges.add((module, other))
                    following.add(other)
        frontier = following

    lines = ["```mermaid", "flowchart LR"]
    for feeds, fed in sorted(edges):
        lines.append(f"    {feeds} --> {fed}")
    lines.append("```")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="keep docs/architecture.md honest about the code")
    parser.add_argument("--check", action="store_true",
                        help="fail if the drawing and the source disagree")
    parser.add_argument("--around", metavar="MODULE",
                        help="print a mermaid map of just this module's "
                             "neighbourhood, for working inside one subsystem")
    parser.add_argument("--reach", type=int, default=1,
                        help="how many hops out from --around (default 1)")
    arguments = parser.parse_args()

    imports = module_imports()
    if arguments.around:
        try:
            print(neighbourhood(arguments.around, imports, arguments.reach))
        except KeyError:
            near = [n for n in sorted(imports) if arguments.around in n]
            print(f"no module {arguments.around!r} under loom/."
                  + (f" Did you mean: {', '.join(near)}?" if near else ""),
                  file=sys.stderr)
            return 1
        return 0
    if not arguments.check:
        print(f"{len(imports)} modules under loom/\n")
        for name in sorted(imports):
            deps = " ".join(sorted(imports[name])) or "-"
            print(f"  {name:<16} imports {deps}")
        return 0

    problems = complaints(imports=imports)
    if not problems:
        print(f"the drawing agrees with all {len(imports)} modules")
        return 0
    print(f"{len(problems)} disagreement(s) between the drawing and the code:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
