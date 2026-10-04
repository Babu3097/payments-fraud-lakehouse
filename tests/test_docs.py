"""Tests for the documentation, because a portfolio repo with a broken link or a dangling decision
number looks careless, and the docs are part of what is being shown.

Checked: every relative link resolves, the decision log is numbered without gaps and each decision
has its parts, every ADR number that is mentioned exists, and every runbook scenario in the table
has a section.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DECISIONS = (REPO / "docs/decisions.md").read_text()
RUNBOOK = (REPO / "docs/runbook.md").read_text()
FENCE = re.compile(r"```.*?```", re.DOTALL)
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
MARKDOWN = [
    REPO / "README.md",
    REPO / "CLAUDE.md",
    REPO / "LEARNING_NOTES.md",
    *sorted((REPO / "docs").glob("*.md")),
    *sorted(REPO.glob("*/README.md")),
]


def tracked_text_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    suffixes = {".md", ".py", ".sql", ".yml", ".yaml", ".toml"}
    return [REPO / name for name in listed if Path(name).suffix in suffixes]


@pytest.mark.parametrize("path", MARKDOWN, ids=lambda p: str(p.relative_to(REPO)))
def test_every_relative_link_points_at_something_that_exists(path):
    text = FENCE.sub("", path.read_text())  # a link inside a code block is an example, not a link
    for target in LINK.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        assert (path.parent / target.split("#")[0]).resolve().exists(), (
            f"{path.relative_to(REPO)} links to {target}, which does not exist"
        )


def adr_blocks() -> dict[int, str]:
    parts = re.split(r"^## ADR-(\d{3}):", DECISIONS, flags=re.MULTILINE)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts), 2)}


def test_the_decisions_are_numbered_from_one_without_gaps_or_repeats():
    numbers = [int(n) for n in re.findall(r"^## ADR-(\d{3}):", DECISIONS, flags=re.MULTILINE)]
    assert sorted(numbers) == list(range(1, len(numbers) + 1))


@pytest.mark.parametrize("number", sorted(adr_blocks()))
def test_each_decision_states_its_status_decision_reason_and_cost(number):
    fields = re.findall(r"^- \*\*([^*:]+):\*\*", adr_blocks()[number], flags=re.MULTILINE)
    assert {"Status", "Decision", "Why"} <= set(fields), f"ADR-{number:03d}: {fields}"
    # Every decision names what it costs: its trade-offs, or the question it leaves open.
    assert any(f.startswith(("Trade-offs", "Open")) for f in fields), f"ADR-{number:03d}"


def test_every_decision_number_that_is_mentioned_exists():
    known = set(adr_blocks())
    missing = {}
    for path in tracked_text_files():
        for number in re.findall(r"ADR-(\d{3})\b", path.read_text(errors="ignore")):
            if int(number) not in known:
                missing.setdefault(path.relative_to(REPO), set()).add(number)
    assert not missing, f"mentioned but not in docs/decisions.md: {missing}"


def test_every_runbook_scenario_in_the_table_has_its_own_section():
    table = re.search(r"\| Scenario \| Written in \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)", RUNBOOK)
    assert table, "the scenario table is missing"
    headings = set(re.findall(r"^## (.+)$", RUNBOOK, flags=re.MULTILINE))
    rows = [
        [cell.strip() for cell in line.strip("|\n").split("|")]
        for line in table.group(1).splitlines()
    ]
    assert len(rows) >= 10
    for name, written in rows:
        assert written.startswith("Below"), f"{name!r} is listed but not written yet: {written}"
        assert name in headings, f"the table lists {name!r} but no section has that heading"
