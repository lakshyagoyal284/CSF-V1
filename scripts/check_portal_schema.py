"""Check that ``db/agritrust_schema.sql`` covers every portal ORM column.

The legacy ``scripts/check_schema_consistency.py`` validates ``db/schema.sql``
against ``backend/db.py``. The dual-portal deployment uses a different pair
(``db/agritrust_schema.sql`` + ``backend/portal_models.py``) and was previously
unchecked, so a model change could ship without the matching DDL.

    python scripts/check_portal_schema.py

Exit code 0 means every mapped ORM table/column exists in the reviewed DDL.
DDL-only columns (generated columns, defaults) are reported as notes.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.portal_models import Base  # noqa: E402

SCHEMA_PATH = PROJECT_ROOT / "db" / "agritrust_schema.sql"
REQUIRED_VIEWS = (
    "v_underwriting_queue",
    "v_scoring_funnel",
    "v_reason_code_frequency",
)

_CONSTRAINT_WORDS = {
    "CONSTRAINT",
    "PRIMARY",
    "UNIQUE",
    "CHECK",
    "FOREIGN",
    "LIKE",
    # Continuation lines of a multi-line column definition, e.g.
    #   verified_by VARCHAR(36) REFERENCES users (id)
    #       ON DELETE SET NULL,
    "REFERENCES",
    "ON",
    "DEFAULT",
    "NOT",
    "NULL",
    "GENERATED",
    "STORED",
    "AS",
    "AND",
    "OR",
    "SET",
    "DELETE",
    "UPDATE",
    "CASCADE",
}


def _strip_sql_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", "", text)


def _table_columns(sql: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\n\);",
        sql,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        name = match.group(1).lower()
        body = match.group(2)
        columns: set[str] = set()
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("--"):
                continue
            first = re.split(r"\s+", line, maxsplit=1)[0].upper()
            if first in _CONSTRAINT_WORDS:
                continue
            if re.match(r"^[a-z_][a-z0-9_]*$", line.split()[0], flags=re.IGNORECASE):
                columns.add(line.split()[0].lower())
        tables[name] = columns
    return tables


def main() -> int:
    if not SCHEMA_PATH.exists():
        print(f"[FAIL] {SCHEMA_PATH} not found")
        return 1

    sql = _strip_sql_comments(SCHEMA_PATH.read_text(encoding="utf-8"))
    ddl_tables = _table_columns(sql)
    problems: list[str] = []
    notes: list[str] = []

    for table in Base.metadata.sorted_tables:
        name = table.name
        if name not in ddl_tables:
            problems.append(f"{name}: no CREATE TABLE IF NOT EXISTS block in the DDL")
            continue
        orm_columns = {column.name.lower() for column in table.columns}
        missing = sorted(orm_columns - ddl_tables[name])
        if missing:
            problems.append(f"{name}: ORM column(s) missing from DDL -> {missing}")
        extra = sorted(ddl_tables[name] - orm_columns)
        if extra:
            notes.append(f"{name}: DDL-only column(s) -> {extra}")

    for view in REQUIRED_VIEWS:
        if not re.search(rf"CREATE\s+OR\s+REPLACE\s+VIEW\s+{view}\b", sql, re.IGNORECASE):
            problems.append(f"{view}: view is missing from the DDL")

    print("Portal DDL vs backend/portal_models.py")
    for table in sorted(ddl_tables):
        print(f"  {table:<24} DDL={len(ddl_tables[table])} column(s)")

    if notes:
        print("\nIntentional DDL-only columns:")
        for note in notes:
            print(f"  - {note}")

    if problems:
        print("\n[FAIL]")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(
        f"\n[OK] {len(ddl_tables)} tables and {len(REQUIRED_VIEWS)} views agree "
        "between db/agritrust_schema.sql and backend/portal_models.py"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
