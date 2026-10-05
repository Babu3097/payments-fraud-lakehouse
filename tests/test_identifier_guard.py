"""Tests for the identifier guard, using made-up identifiers only (the real ones are private)."""

from payments_lakehouse.identifier_guard import load_patterns, main, scan_files, scan_text


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_a_workspace_host_is_caught_without_any_private_file():
    patterns = load_patterns(None)
    # Built from parts so this file does not itself contain something the guard rejects.
    host = "dbc-" + "1a2b3c4d-5e6f" + ".cloud.databricks.com"
    assert scan_text(f"host = {host}", patterns) == [1]


def test_clean_text_has_no_hits():
    assert scan_text("SELECT 1\nFROM workspace.silver.transactions", load_patterns(None)) == []


def test_private_patterns_are_loaded_case_insensitively_and_comments_are_skipped(tmp_path):
    patterns_file = write(tmp_path, "p.txt", "# a comment\n\nJane\\.Doe99\n")
    patterns = load_patterns(patterns_file)
    assert len(patterns) == 3  # two built-in plus one private
    assert scan_text("a\nowner is jane.doe99 here", patterns) == [2]


def test_a_missing_private_file_leaves_only_the_built_in_patterns(tmp_path):
    assert len(load_patterns(tmp_path / "nope.txt")) == 2


def test_binary_files_are_skipped(tmp_path):
    binary = tmp_path / "x.bin"
    binary.write_bytes(b"\xff\xfe\x00\x80")
    assert scan_files([binary], load_patterns(None)) == []


def test_main_fails_and_withholds_the_matched_text(tmp_path, capsys):
    patterns_file = write(tmp_path, "p.txt", "secretname\n")
    dirty = write(tmp_path, "dirty.md", "ok\nmy secretname is here\n")
    clean = write(tmp_path, "clean.md", "nothing here\n")

    assert main([str(clean), "--patterns-file", str(patterns_file)]) == 0
    assert main([str(dirty), "--patterns-file", str(patterns_file)]) == 1
    output = capsys.readouterr().out
    assert "dirty.md:2" in output
    assert "secretname" not in output
