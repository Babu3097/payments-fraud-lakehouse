"""Read the Lakeflow SQL files in pipelines/ so their queries can run in a local Spark session.

A pipeline file is ordinary Spark SQL wrapped in Lakeflow statements (CREATE OR REFRESH ...,
CREATE FLOW ..., CONSTRAINT ... EXPECT). Local Spark does not know those wrappers, so this module
takes them off and leaves the query, and it reads the expectations as data so a test can apply them
to rows. It is test tooling only: nothing here runs on Databricks.
"""

import re
from dataclasses import dataclass
from pathlib import Path

PIPELINES = Path(__file__).resolve().parents[2] / "pipelines"


@dataclass(frozen=True)
class Expectation:
    name: str
    condition: str
    on_violation: str  # "WARN", "DROP ROW" or "FAIL UPDATE"


@dataclass(frozen=True)
class Statement:
    kind: str  # "table", "view", "flow", "auto_cdc" or "other"
    name: str
    query: str | None
    expectations: tuple[Expectation, ...] = ()
    target: str | None = None  # for a flow: the table it inserts into
    columns: str | None = None  # a table declared with a column list, as a DDL string


def _scan(sql: str) -> tuple[str, str]:
    """Return (clean, masked): the SQL without comments, and the same text with string contents
    blanked, so a search for a keyword or a semicolon never lands inside a string."""
    clean: list[str] = []
    masked: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end == -1 else end  # the newline itself is kept
            continue
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            clean.append(" ")
            masked.append(" ")
            continue
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if sql[j + 1 : j + 2] == ch:  # a doubled quote is an escaped quote
                        j += 2
                        continue
                    break
                if sql[j] == "\\" and ch != "`":  # Spark strings accept backslash escapes
                    j += 2
                    continue
                j += 1
            text = sql[i : j + 1]
            clean.append(text)
            masked.append(ch + " " * max(len(text) - 2, 0) + (ch if len(text) > 1 else ""))
            i = j + 1
            continue
        clean.append(ch)
        masked.append(ch)
        i += 1
    return "".join(clean), "".join(masked)


def _split(clean: str, masked: str) -> list[tuple[str, str]]:
    """Cut at each semicolon that is outside parentheses, strings and comments."""
    parts: list[tuple[str, str]] = []
    depth = start = 0
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == ";" and depth == 0:
            parts.append((clean[start:i], masked[start:i]))
            start = i + 1
    parts.append((clean[start:], masked[start:]))
    return [(c.strip(), m.strip()) for c, m in parts if m.strip()]


_FLOW_INSERT = re.compile(
    r"^CREATE\s+FLOW\s+(?P<name>\w+)\s+AS\s+INSERT\s+INTO\s+(?P<target>[\w.]+)\s+BY\s+NAME\b",
    re.IGNORECASE,
)
_FLOW_CDC = re.compile(r"^CREATE\s+FLOW\s+(?P<name>\w+)\s+AS\s+AUTO\s+CDC\b", re.IGNORECASE)
_CREATE = re.compile(
    r"^CREATE\s+(?:OR\s+(?:REFRESH|REPLACE)\s+)?(?:PRIVATE\s+)?(?:TEMPORARY\s+)?"
    r"(?P<kind>STREAMING\s+TABLE|MATERIALIZED\s+VIEW|VIEW)\s+(?P<name>[\w.]+)",
    re.IGNORECASE,
)
_CONSTRAINT = re.compile(
    r"^CONSTRAINT\s+(?P<name>\w+)\s+EXPECT\s*\((?P<cond>.*)\)\s*"
    r"(?:ON\s+VIOLATION\s+(?P<action>DROP\s+ROW|FAIL\s+UPDATE))?$",
    re.IGNORECASE | re.DOTALL,
)


def _first_top_level_as(masked: str) -> int | None:
    depth = 0
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and masked[i : i + 2].upper() == "AS":
            before = masked[i - 1 : i]
            after = masked[i + 2 : i + 3]
            if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                return i
    return None


def _group(text: str) -> str | None:
    """The text inside the first parenthesis group, if the text starts with one."""
    text = text.strip()
    if not text.startswith("("):
        return None
    depth = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return text[1:i]
    return None


def _expectations(header_clean: str, header_masked: str) -> tuple[Expectation, ...]:
    group_clean = _group(header_clean)
    group_masked = _group(header_masked)
    if group_clean is None or group_masked is None:
        return ()
    pieces: list[str] = []
    depth = start = 0
    for i, ch in enumerate(group_masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            pieces.append(group_clean[start:i])
            start = i + 1
    pieces.append(group_clean[start:])
    found = []
    for piece in pieces:
        match = _CONSTRAINT.match(piece.strip())
        if match:
            action = " ".join((match["action"] or "WARN").upper().split())
            found.append(Expectation(match["name"], " ".join(match["cond"].split()), action))
    return tuple(found)


def _columns(header_clean: str) -> str | None:
    group = _group(header_clean)
    if group is None or re.search(r"\bCONSTRAINT\b", group, re.IGNORECASE):
        return None
    return " ".join(group.split())


def parse(sql: str) -> list[Statement]:
    clean, masked = _scan(sql)
    statements = []
    for text, mask in _split(clean, masked):
        if match := _FLOW_INSERT.match(mask):
            query = text[match.end() :].strip()
            statements.append(Statement("flow", match["name"], query, (), match["target"]))
        elif match := _FLOW_CDC.match(mask):
            statements.append(Statement("auto_cdc", match["name"], None))
        elif match := _CREATE.match(mask):
            rest_mask = mask[match.end() :]
            rest_text = text[match.end() :]
            as_at = _first_top_level_as(rest_mask)
            header_text = rest_text if as_at is None else rest_text[:as_at]
            header_mask = rest_mask if as_at is None else rest_mask[:as_at]
            query = None if as_at is None else rest_text[as_at + 2 :].strip()
            kind = "table" if "TABLE" in match["kind"].upper() else "view"
            statements.append(
                Statement(
                    kind,
                    match["name"],
                    query,
                    _expectations(header_text, header_mask),
                    columns=_columns(header_text),
                )
            )
        else:
            statements.append(Statement("other", text.split("\n", 1)[0][:60], None))
    return statements


def load(relative_path: str) -> list[Statement]:
    """Parse a file under pipelines/, for example load("silver/transactions_unified.sql")."""
    return parse((PIPELINES / relative_path).read_text())


def find(statements: list[Statement], name: str) -> Statement:
    """Find a statement by its full name or by its last part (transactions, not workspace.x.y)."""
    for statement in statements:
        if statement.name == name or statement.name.split(".")[-1] == name:
            return statement
    raise KeyError(f"no statement named {name!r}; have {[s.name for s in statements]}")


def local_name(name: str) -> str:
    """The temp view name a fixture gets: workspace.silver.transactions -> silver_transactions."""
    parts = name.split(".")
    return "_".join(parts[-2:]) if len(parts) == 3 else name


def localize(query: str) -> str:
    """Make a pipeline query runnable on plain tables: drop STREAM(...) and flatten the names."""
    query = re.sub(r"\bSTREAM\s*\(\s*([\w.]+)\s*\)", r"\1", query, flags=re.IGNORECASE)
    return re.sub(r"\bworkspace\.(\w+)\.(\w+)\b", r"\1_\2", query)
