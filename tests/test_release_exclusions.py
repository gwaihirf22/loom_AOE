"""What must never reach the public repo.

The release script exports the tracked tree and deletes an exclusion list
before pushing. That list was written by hand, and a hand-written list of
things-that-must-not-escape fails the way every hand-written list in this
project has failed: silently, on the entry nobody added.

It held `.loom-notes.md` and `.loom-roadmap.md`. Two more working documents
were written later - `.loom-next-session.md` and `.loom-replay-plan.md` -
and neither was added, so both would have been published. Nothing would
have said so. It was caught by reading a dry-run export minutes before a
push, which is not a system.

So the entries are patterns now, and this asserts the property rather than
the list: no working document reaches the export, whatever it is called.
"""

import pathlib

from tools import release

ROOT = pathlib.Path(__file__).resolve().parent.parent


def excluded(name):
    """Would the release script delete this path from the export?"""
    return any(pathlib.PurePath(name).match(pattern)
               for pattern in release.EXCLUDE)


def test_every_working_document_is_excluded():
    # Whatever is in the repo TODAY, not a list typed in here - which would
    # be the same failure one level along.
    working = [path.name for path in ROOT.glob(".loom-*.md")]
    working += [path.name for path in ROOT.glob(".agent-*.md")]
    assert working, "expected some working documents to exist"
    for name in working:
        assert excluded(name), f"{name} would be published"


def test_a_working_document_nobody_has_written_yet_is_excluded():
    # The point of a pattern: it covers the file that does not exist yet.
    assert excluded(".loom-something-invented-next-month.md")
    assert excluded(".agent-whatever.md")


def test_the_private_pipeline_stays_private():
    assert excluded("RELEASING.md")
    assert excluded("tools/release.py")
    assert excluded("CLAUDE.md")


def test_the_things_that_must_ship_are_not_excluded():
    # A pattern that swallowed the licence, the changelog or the code would
    # be a far worse failure than the one it was written to fix.
    for name in ("LICENSE", "NOTICE", "README.md", "CHANGELOG.md",
                 "loom/reader.py", "loom/replay_ids.py",
                 "docs/architecture.md", "tools/architecture.py"):
        assert not excluded(name), f"{name} would be stripped from a release"
