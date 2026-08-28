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
    """Would the release script delete this path from the export?

    Answers for a NAME. PurePath.match matches from the right, so this
    cannot answer for a file inside an excluded directory - use
    release.strip_excluded over a real tree for that.
    """
    return any(pathlib.PurePath(name).match(pattern)
               for pattern in release.EXCLUDE)


strip_excluded = release.strip_excluded


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
                 "docs/architecture.md", "tools/architecture.py",
                 # The Linux package is BUILT FROM the public snapshot: a
                 # Flathub manifest points at the release tarball and
                 # installs these out of it. Stripped, the build fails on
                 # Flathub's machines rather than here, which is the
                 # slowest possible place to learn about it.
                 "packaging/linux/io.github.gwaihirf22.loom_AOE.yml",
                 "packaging/linux/io.github.gwaihirf22.loom_AOE.metainfo.xml",
                 "packaging/linux/io.github.gwaihirf22.loom_AOE.desktop",
                 "packaging/linux/io.github.gwaihirf22.loom_AOE.png",
                 "packaging/linux/loom.sh"):
        assert not excluded(name), f"{name} would be stripped from a release"


def test_the_game_knowledge_gathered_for_development_stays_private(tmp_path):
    """reference/ is a transcription of the game's own design - which is
    Microsoft's to publish and not mine - and it is a working aid rather
    than part of the program: Loom never reads it and would not behave
    differently without it.

    Asserted for the FOLDER and for anything under it, because the
    release script removes a directory whole and a test that only checked
    the name would pass while a file inside it shipped.

    The "anything under it" half is asserted by RUNNING THE REMOVAL over a
    planted tree, not by asking excluded(). It cannot be asked: excluded()
    is PurePath.match, which matches from the right, so
    excluded("reference/civilisations.md") is False while the script deletes
    the whole directory. An earlier version of this test asserted
    `inside.startswith("reference/")` - a tautology about its own string
    literals that never called excluded() at all, and would have passed
    just as happily if the entry had been dropped from EXCLUDE.
    """
    assert excluded("reference")
    assert "reference" in release.EXCLUDE

    export = tmp_path / "export"
    (export / "reference" / "civs").mkdir(parents=True)
    (export / "reference" / "civilisations.md").write_text("private",
                                                           encoding="utf-8")
    (export / "reference" / "civs" / "BRITONS.md").write_text("private",
                                                              encoding="utf-8")
    (export / "loom").mkdir()
    (export / "loom" / "reader.py").write_text("ships", encoding="utf-8")

    strip_excluded(export)

    assert not (export / "reference").exists(), (
        "the reference folder survived the export")
    assert (export / "loom" / "reader.py").exists(), (
        "the exclusion took the program with it")


def test_what_measures_the_game_ships_and_what_it_measured_does_not():
    """The TOOL is part of the project and belongs in the open; the data
    it writes about one player's own games does not, and never enters the
    repository at all - it is written beside their statistics."""
    assert not excluded("tools/measure_durations.py")
    assert not excluded("tools/reader_sweep.py")
    assert not excluded("loom/durations.py")
