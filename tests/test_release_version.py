"""
Loom — the version grammar that separates a release from a nightly.

`loom/__init__.py` carries one number and everything downstream trusts it:
the launcher's title bar, every stats file, the tag the public snapshot is
published under, and the Flatpak metainfo. `tools/release.py` exists partly
because that number used to have TWO independent sources - the code and a
string typed on the command line - with nothing checking they agreed.

Adding a nightly channel put a second fork in the same road, so the same
discipline applies. A `-dev` suffix means "between releases", and it decides
which of two things a publish is:

  1.0.8      a release          `release.py`            -> main
  1.0.8-dev  on the way to one  `release.py --nightly`  -> nightly branch

Each mode REFUSES the other's tree, and both refusals are tested here. They
are not politeness. One stops untested code going out under a release
number; the other stops a version number being spent on a throwaway build.
Both are pushes to a public repo, so neither is undone quietly.

No network and no git: every test here stops at a `sys.exit` that happens
before the script looks at a remote.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import datetime
import re

import pytest

import loom
from tools import release


# ---- the grammar --------------------------------------------------------


def test_a_dev_tree_says_so():
    assert release.is_development("1.0.8-dev")
    assert not release.is_development("1.0.8")


def test_the_base_is_the_release_the_tree_is_heading_for():
    assert release.base_version("1.0.8-dev") == "1.0.8"


def test_a_release_version_is_already_its_own_base():
    """So callers can ask without checking first."""
    assert release.base_version("1.0.7") == "1.0.7"


def test_a_nightly_is_named_after_the_release_it_precedes():
    """Not after the -dev string it came from: "1.0.8-dev-nightly..." would
    carry a suffix that means nothing to anyone."""
    when = datetime.date(2026, 8, 28)

    assert release.nightly_version("1.0.8-dev", when) == "1.0.8-nightly.20260828"


def test_the_date_is_zero_padded_so_nightlies_sort():
    """A directory listing or a tag list is read in order, and 20260901
    must not fall between 202609 and 20261001."""
    early = release.nightly_version("1.0.8-dev", datetime.date(2026, 1, 2))

    assert early == "1.0.8-nightly.20260102"


# ---- what the shipped version is allowed to be --------------------------


def test_the_shipped_version_is_publishable_by_one_channel_or_the_other():
    """The load-bearing one, and it walks the real value rather than a
    copy of it.

    Every other test here is about strings handed in by the test. This asks
    what `loom/__init__.py` ACTUALLY says today, because a hand-edit to
    something neither channel accepts - "1.0.8dev", "1.1", a trailing
    space - is invisible until the day someone tries to publish, and the
    two failure modes then look identical: both channels refuse and neither
    explains that the version itself is the problem.
    """
    version = loom.__version__
    base = release.base_version(version)

    assert release.RELEASE_NUMBER.fullmatch(base), (
        f"loom.__version__ is {version!r}, which is neither X.Y.Z nor"
        f" X.Y.Z{release.DEV_SUFFIX} — no channel can publish it")


def test_a_dev_version_still_yields_a_usable_nightly_tag():
    """Follows the shipped value too, so this keeps working after a bump."""
    if not release.is_development(loom.__version__):
        pytest.skip("this tree is a release, so there is no nightly to name")

    tag = release.nightly_version(loom.__version__, datetime.date(2026, 8, 28))

    assert re.fullmatch(r"\d+\.\d+\.\d+-nightly\.\d{8}", tag), tag


# ---- the two refusals ---------------------------------------------------


def run(monkeypatch, version, *arguments):
    """Run main() against a pretend loom/__init__.py, and return the exit.

    `release.py` does `from loom import __version__`, so the name to patch
    is the one bound in ITS namespace - patching loom.__version__ after the
    import would change nothing, which is the sort of test that passes for
    the wrong reason.
    """
    monkeypatch.setattr(release, "__version__", version)
    monkeypatch.setattr("sys.argv", ["release.py", *arguments])
    with pytest.raises(SystemExit) as stop:
        release.main()
    return str(stop.value)


def test_a_release_tree_cannot_be_published_as_a_nightly(monkeypatch):
    reason = run(monkeypatch, "1.0.7", "--nightly")

    assert "1.0.7" in reason
    assert release.DEV_SUFFIX in reason, "the fix should be in the message"


def test_the_refusal_names_the_next_version_rather_than_the_rule(monkeypatch):
    """Being told "needs a -dev version" leaves you to work out which one.
    The answer is knowable, so the message gives it."""
    reason = run(monkeypatch, "1.0.7", "--nightly")

    assert "1.0.8-dev" in reason


def test_a_dev_tree_cannot_be_published_as_a_release(monkeypatch):
    """The dangerous direction: this one puts untested code out under a
    version number that can never be reused."""
    reason = run(monkeypatch, "1.0.8-dev")

    assert "1.0.8-dev" in reason
    assert "1.0.8" in reason and "--nightly" in reason, (
        "both ways out should be named — cut the release, or go nightly")


def test_erasing_public_history_for_a_throwaway_build_is_refused(monkeypatch):
    reason = run(monkeypatch, "1.0.8-dev", "--nightly", "--fresh")

    assert "--fresh" in reason and "--nightly" in reason


def test_a_confirming_version_that_disagrees_is_refused_on_both_channels(
        monkeypatch):
    """The original reason this script stopped taking the version as an
    independent argument: two sources of one number drift, silently."""
    as_release = run(monkeypatch, "1.0.7", "1.0.6")
    as_nightly = run(monkeypatch, "1.0.8-dev", "1.0.6", "--nightly")

    assert "1.0.6" in as_release and "1.0.7" in as_release
    assert "1.0.6" in as_nightly and "1.0.8" in as_nightly


def test_a_nightly_is_confirmed_with_the_release_number_not_the_dev_string(
        monkeypatch):
    """`release.py 1.0.8 --nightly` is the natural thing to type, so it has
    to be the thing that works — the nightly IS for 1.0.8. Confirmed by the
    absence of a refusal: it gets past the version block and fails later,
    on the remote, which this test does not reach."""
    monkeypatch.setattr(release, "__version__", "1.0.8-dev")
    monkeypatch.setattr("sys.argv", ["release.py", "1.0.8", "--nightly"])
    monkeypatch.setattr(release, "release_url",
                        lambda: (_ for _ in ()).throw(SystemExit("reached the remote")))

    with pytest.raises(SystemExit) as stop:
        release.main()

    assert "reached the remote" in str(stop.value), (
        "the version block should have accepted 1.0.8 as the confirmation")
