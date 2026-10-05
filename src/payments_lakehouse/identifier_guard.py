"""Refuse a commit that contains a personal identifier.

The repository is public, so an email address, a workspace URL or a home-directory path that slips
into a file stays in the git history for good. Two sources of patterns:

- built-in: the shape of a Databricks workspace host, which is the same for everyone;
- private: one regular expression per line in `private/forbidden_patterns.txt`, which is gitignored
  (the patterns would themselves be the leak). CI does not have that file, so there only the
  built-in patterns apply; the personal ones are enforced on the machine that holds them.

A match is reported as file and line number only, never the matched text, so the hook's own output
cannot leak what it guards.
"""

import argparse
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

DEFAULT_PATTERNS_FILE = Path("private/forbidden_patterns.txt")

BUILT_IN = [
    r"\bdbc-[0-9a-f]{8}-[0-9a-f]{4}\.cloud\.databricks\.com\b",
    r"\badb-\d{10,}\.\d+\.azuredatabricks\.net\b",
]


def load_patterns(patterns_file: Path | None) -> list[re.Pattern]:
    """Built-in patterns plus the private file's, skipping blank lines and `#` comments."""
    sources = list(BUILT_IN)
    if patterns_file is not None and patterns_file.is_file():
        for line in patterns_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                sources.append(line)
    return [re.compile(source, re.IGNORECASE) for source in sources]


def scan_text(text: str, patterns: Iterable[re.Pattern]) -> list[int]:
    """Line numbers (1-based) where any pattern matches."""
    patterns = list(patterns)
    return [
        n
        for n, line in enumerate(text.splitlines(), start=1)
        if any(p.search(line) for p in patterns)
    ]


def scan_files(paths: Iterable[Path], patterns: Sequence[re.Pattern]) -> list[tuple[Path, int]]:
    hits = []
    for path in paths:
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue  # a binary file has no readable lines to match
        hits.extend((path, n) for n in scan_text(text, patterns))
    return hits


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--patterns-file", type=Path, default=DEFAULT_PATTERNS_FILE)
    args = parser.parse_args(argv)

    hits = scan_files(args.files, load_patterns(args.patterns_file))
    for path, line in hits:
        print(f"{path}:{line}: contains a forbidden personal identifier (text withheld)")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
