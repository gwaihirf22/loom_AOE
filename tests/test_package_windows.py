"""
Loom — the release zip has to unpack on the machines it was not built on.

The bug this file exists to prevent was invisible from Windows. A zip
stores every name twice - once in the local header before each file's
bytes, once in the central directory at the end - and PowerShell's
Compress-Archive wrote forward slashes in the central directory and
backslashes in every local header. Windows Explorer reads the central
directory and unpacked it perfectly. So does Python's zipfile, which is
why my first check on the report came back clean and wrong.

unzip and Ark read local headers, and the spec makes "/" the only
directory separator, so they treated the rest as ordinary characters: a
Linux user unpacking the 1.0.1 zip got 1600 files named
"Loom\\_internal\\..." in one flat heap, no folders at all. It shipped
because nobody could see it from the machine that built it.

So these tests check the half that was wrong, with an archive deliberately
built the broken way to prove the check can fail.
"""

# I used Anthropic's Claude to help with proper syntax, code organisation,
# debugging and review. The design and code are my own work.

import struct
import zipfile

from tools import package_windows

BACKSLASH = chr(92)


def tree(root):
    """A small folder with a nested directory, like the real bundle."""
    (root / "Loom" / "_internal" / "icons").mkdir(parents=True)
    (root / "Loom" / "Loom.exe").write_bytes(b"not really an executable")
    (root / "Loom" / "_internal" / "base_library.zip").write_bytes(b"x")
    (root / "Loom" / "_internal" / "icons" / "house.webp").write_bytes(b"y")
    return root / "Loom"


def backslash_zip(path, names):
    """An archive spelled the way Compress-Archive spelled it.

    Written by hand because the whole point is that no honest zip writer
    produces this - zipfile normalises arcnames, so the bug cannot be
    reproduced through its front door.
    """
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"content")

    data = bytearray(path.read_bytes())
    # Only the LOCAL headers are rewritten, leaving the central directory
    # correct - which is exactly the asymmetry that hid the original bug.
    offset = data.find(package_windows.LOCAL_SIGNATURE)
    while offset >= 0:
        length, extra = struct.unpack_from("<HH", data, offset + 26)
        start = offset + 30
        stored = data[start:start + length]
        data[start:start + length] = stored.replace(b"/", BACKSLASH.encode())
        offset = data.find(package_windows.LOCAL_SIGNATURE,
                           start + length + extra)
    path.write_bytes(bytes(data))
    return path


# ---- the check catches what shipped ---------------------------------------

def test_the_check_catches_backslashes_in_local_headers(tmp_path):
    """The regression itself. A zip whose central directory is perfect and
    whose local headers are not must be reported, or this ships again."""
    broken = backslash_zip(tmp_path / "broken.zip",
                           ["Loom/Loom.exe", "Loom/_internal/base_library.zip"])

    problems = package_windows.check(broken)

    assert problems, "the archive that shipped in 1.0.1 must not pass"
    assert any("local headers" in problem for problem in problems)


def test_the_check_reads_local_headers_not_just_the_central_directory(tmp_path):
    """Stated on its own because reading the convenient half is the mistake
    that let this through: zipfile.namelist() calls the broken file fine."""
    broken = backslash_zip(tmp_path / "broken.zip", ["Loom/Loom.exe"])

    assert zipfile.ZipFile(broken).namelist() == ["Loom/Loom.exe"]
    assert BACKSLASH in package_windows.local_header_names(broken)[0]


def test_a_disagreement_between_the_two_halves_is_reported(tmp_path):
    broken = backslash_zip(tmp_path / "broken.zip", ["Loom/a/b.txt"])

    problems = package_windows.check(broken)

    assert any("differently" in problem for problem in problems)


# ---- what the builder produces --------------------------------------------

def test_the_builder_stores_forward_slashes_in_both_halves(tmp_path):
    source = tree(tmp_path)
    archive = package_windows.build(source, tmp_path / "out.zip")

    assert package_windows.check(archive) == []
    for name in package_windows.local_header_names(archive):
        assert BACKSLASH not in name


def test_the_builder_keeps_the_folder_as_the_top_level(tmp_path):
    """Unpacking must give one folder, not a heap in the user's Downloads."""
    source = tree(tmp_path)
    archive = package_windows.build(source, tmp_path / "out.zip")

    names = zipfile.ZipFile(archive).namelist()
    assert all(name.startswith("Loom/") for name in names)


def test_every_file_survives_the_round_trip(tmp_path):
    source = tree(tmp_path)
    archive = package_windows.build(source, tmp_path / "out.zip")

    unpacked = tmp_path / "unpacked"
    zipfile.ZipFile(archive).extractall(unpacked)

    assert (unpacked / "Loom" / "Loom.exe").read_bytes() \
        == b"not really an executable"
    assert (unpacked / "Loom" / "_internal" / "icons" / "house.webp").exists()


def test_nested_directories_arrive_as_directories(tmp_path):
    """The symptom a Linux user actually saw: no folders, just names."""
    source = tree(tmp_path)
    archive = package_windows.build(source, tmp_path / "out.zip")

    unpacked = tmp_path / "unpacked"
    zipfile.ZipFile(archive).extractall(unpacked)

    assert (unpacked / "Loom" / "_internal").is_dir()
    assert (unpacked / "Loom" / "_internal" / "icons").is_dir()


def test_packaging_nothing_is_refused_rather_than_shipping_an_empty_zip(
        tmp_path):
    assert package_windows.main(["--source", str(tmp_path / "absent"),
                                 "--out", str(tmp_path / "out.zip")]) == 1


def test_the_command_fails_loudly_on_a_bad_archive(tmp_path):
    """The exit code is what a release step would stop on."""
    broken = backslash_zip(tmp_path / "broken.zip", ["Loom/Loom.exe"])

    assert package_windows.main(["--check", str(broken)]) == 1


def test_the_command_passes_a_good_one(tmp_path):
    source = tree(tmp_path)
    archive = package_windows.build(source, tmp_path / "out.zip")

    assert package_windows.main(["--check", str(archive)]) == 0
