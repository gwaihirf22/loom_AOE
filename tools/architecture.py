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
    """
    found = {}
    sources = (sorted(glob.glob(str(paths.PROJECT_ROOT / "loom" / "*.py")))
               + sorted(glob.glob(str(paths.PROJECT_ROOT / "loom" / "*"
                                      / "__init__.py")))
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
        found[name] = imports
    return {name: {dep for dep in deps if dep in found}
            for name, deps in found.items()}


def _node_id(text):
    """The id out of a mermaid node, with any label shape stripped off."""
    match = LABEL.match(text.strip())
    return match.group(1) if match else None


def diagram_edges(markdown):
    """Every (feeds, fed) pair drawn in the mermaid blocks of a document."""
    edges = []
    inside = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            inside = stripped.startswith("```mermaid")
            continue
        if not inside or stripped.startswith("%%"):
            continue
        match = ARROW.match(stripped)
        if not match:
            continue
        source, target = _node_id(match.group(1)), _node_id(match.group(2))
        if source and target:
            edges.append((source, target))
    return edges


LAYOUT_DOC = paths.PROJECT_ROOT / "CLAUDE.md"


def layout_block(markdown=None):
    """The fenced block under CLAUDE.md's "## Layout" heading."""
    if markdown is None:
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
