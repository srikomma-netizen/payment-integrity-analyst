"""Deterministic SQL guard. Runs *after* the LLM and *before* the database.

Everything that must be true for safety is checked here with a real SQL
parser, not with prompt instructions:

  1. exactly one statement, and it is a SELECT (CTEs / UNIONs of SELECTs ok)
  2. every referenced table is in the catalog and allowed for the role
  3. no restricted column is referenced anywhere (projection, filter, join)
  4. no dangerous functions (extension loading, file I/O)
  5. a row LIMIT is present and capped

The guard returns the *rewritten* SQL (with LIMIT applied) so the agent
executes exactly what was validated.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .catalog import RESTRICTED_COLUMNS, TABLES

DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "fts3_tokenizer", "edit"}
DEFAULT_MAX_ROWS = 200


@dataclass
class GuardResult:
    ok: bool
    sql: str
    violations: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    requires_approval: bool = False

    @property
    def message(self) -> str:
        return "; ".join(self.violations)


def _is_select_like(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except))


def guard_sql(sql: str, *, role: str, max_rows: int = DEFAULT_MAX_ROWS) -> GuardResult:
    violations: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as e:
        return GuardResult(False, sql, [f"parse error: {e}"])

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return GuardResult(False, sql, [f"expected exactly one statement, got {len(statements)}"])
    tree = statements[0]

    if not _is_select_like(tree):
        return GuardResult(False, sql, [f"only SELECT is allowed, got {type(tree).__name__}"])

    # Any write / DDL / PRAGMA nested anywhere in the tree is rejected.
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                             exp.Alter, exp.Command, exp.Pragma)):
            violations.append(f"disallowed statement node: {type(node).__name__}")

    # Table allow-list and role check. CTE names are not real tables.
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    requires_approval = False
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        if name in cte_names:
            continue
        tables.add(name)
        meta = TABLES.get(name)
        if meta is None:
            violations.append(f"unknown table '{name}'")
            continue
        if role not in meta.allowed_roles:
            violations.append(f"table '{name}' is not permitted for role '{role}'")
        if meta.requires_approval:
            requires_approval = True

    # Column-level deny list (restricted columns are globally unique by design).
    for c in tree.find_all(exp.Column):
        if c.name.lower() in RESTRICTED_COLUMNS:
            violations.append(f"restricted column '{c.name}' referenced")
    # SELECT * (or t.*) on a table with restricted columns would leak them.
    # COUNT(*) is also a Star node in the AST but its parent is a function, not a projection.
    projection_stars = [st for st in tree.find_all(exp.Star) if isinstance(st.parent, (exp.Select, exp.Column))]
    if projection_stars:
        leaky = tables & {t for t, m in TABLES.items() if any(col.restricted for col in m.columns)}
        if leaky:
            violations.append(f"SELECT * not allowed on tables with restricted columns: {sorted(leaky)}")

    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in DENIED_FUNCTIONS:
            violations.append(f"disallowed function '{fn.name}'")

    if violations:
        return GuardResult(False, sql, violations, tables, requires_approval)

    # Enforce / cap LIMIT on the outermost query.
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)
    else:
        try:
            current = int(limit.expression.this)
        except (AttributeError, ValueError, TypeError):
            current = max_rows + 1
        if current > max_rows:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return GuardResult(True, tree.sql(dialect="sqlite"), [], tables, requires_approval)
