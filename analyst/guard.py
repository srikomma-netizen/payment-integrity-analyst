"""Deterministic SQL guard that sits between the model's SQL and the database.

Checks: single SELECT, allowed tables for the role, no restricted columns,
no file/extension functions, and a capped LIMIT.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .catalog import RESTRICTED_COLUMNS, TABLES

# SQLite functions that reach outside the database (extensions, file I/O, the CLI's edit())
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "fts3_tokenizer", "edit"}
DEFAULT_MAX_ROWS = 200


@dataclass
class GuardResult:
    ok: bool
    sql: str  # rewritten SQL when ok, otherwise the input echoed back
    violations: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    requires_approval: bool = False

    @property
    def message(self) -> str:
        return "; ".join(self.violations)


def _is_select_like(node: exp.Expression) -> bool:
    # a WITH ... SELECT parses as a Select with a "with" arg, so CTEs pass here too
    return isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except))


def guard_sql(sql: str, *, role: str, max_rows: int = DEFAULT_MAX_ROWS) -> GuardResult:
    """Validate model SQL for `role` and return the SQL that is safe to execute.

    Callers must run `result.sql`, never the original string: that is the
    version with the LIMIT applied and the one that was actually checked.
    """
    violations: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as e:
        return GuardResult(False, sql, [f"parse error: {e}"])

    # stray semicolons (";;") parse as None statements; drop those before counting
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:  # blocks "SELECT 1; DROP TABLE ..." stacking
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
    # Collect violations instead of returning early so the retry prompt sees all of them at once.
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
    # Matching by bare name also catches aliased refs like m.mrn and uses in WHERE/JOIN/ORDER BY.
    for c in tree.find_all(exp.Column):
        if c.name.lower() in RESTRICTED_COLUMNS:
            violations.append(f"restricted column '{c.name}' referenced")
    # SELECT * (or t.*) on a table with restricted columns would leak them.
    # COUNT(*) is also a Star node in the AST but its parent is a function, not a projection.
    projection_stars = [st for st in tree.find_all(exp.Star) if isinstance(st.parent, (exp.Select, exp.Column))]
    if projection_stars:
        # conservative: any star blocks the query if *any* referenced table has restricted columns,
        # even when the star itself is qualified to a clean table
        leaky = tables & {t for t, m in TABLES.items() if any(col.restricted for col in m.columns)}
        if leaky:
            violations.append(f"SELECT * not allowed on tables with restricted columns: {sorted(leaky)}")

    # functions sqlglot doesn't know come through as Anonymous, which is where these all land
    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in DENIED_FUNCTIONS:
            violations.append(f"disallowed function '{fn.name}'")

    if violations:
        return GuardResult(False, sql, violations, tables, requires_approval)

    # Enforce / cap LIMIT on the outermost query. Inner LIMITs in subqueries are left alone.
    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)  # returns a new tree, hence the reassignment
    else:
        try:
            current = int(limit.expression.this)
        except (AttributeError, ValueError, TypeError):
            # non-literal LIMIT (expression, bind param): treat as over the cap and replace it
            current = max_rows + 1
        if current > max_rows:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return GuardResult(True, tree.sql(dialect="sqlite"), [], tables, requires_approval)
