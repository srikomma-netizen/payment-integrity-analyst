"""SQL guard between the model and the db."""
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
    sql: str  # rewritten when ok
    violations: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)
    requires_approval: bool = False

    @property
    def message(self) -> str:
        return "; ".join(self.violations)


def _is_select_like(node: exp.Expression) -> bool:
    return isinstance(node, (exp.Select, exp.Union, exp.Intersect, exp.Except))


def guard_sql(sql: str, *, role: str, max_rows: int = DEFAULT_MAX_ROWS) -> GuardResult:
    """Validate model SQL for a role. Run result.sql, not the input."""
    violations: list[str] = []
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except ParseError as e:
        return GuardResult(False, sql, [f"parse error: {e}"])

    # ";;" parses as None
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return GuardResult(False, sql, [f"expected exactly one statement, got {len(statements)}"])
    tree = statements[0]

    if not _is_select_like(tree):
        return GuardResult(False, sql, [f"only SELECT is allowed, got {type(tree).__name__}"])

    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop,
                             exp.Alter, exp.Command, exp.Pragma)):
            violations.append(f"disallowed statement node: {type(node).__name__}")

    # collect everything so the retry sees all violations
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

    # bare name match also catches m.mrn
    for c in tree.find_all(exp.Column):
        if c.name.lower() in RESTRICTED_COLUMNS:
            violations.append(f"restricted column '{c.name}' referenced")
    # skip COUNT(*), its parent is a function
    projection_stars = [st for st in tree.find_all(exp.Star) if isinstance(st.parent, (exp.Select, exp.Column))]
    if projection_stars:
        # overly strict, blocks t.* even on a clean table
        leaky = tables & {t for t, m in TABLES.items() if any(col.restricted for col in m.columns)}
        if leaky:
            violations.append(f"SELECT * not allowed on tables with restricted columns: {sorted(leaky)}")

    for fn in tree.find_all(exp.Anonymous):
        if fn.name.lower() in DENIED_FUNCTIONS:
            violations.append(f"disallowed function '{fn.name}'")

    if violations:
        return GuardResult(False, sql, violations, tables, requires_approval)

    limit = tree.args.get("limit")
    if limit is None:
        tree = tree.limit(max_rows)  # returns a new tree
    else:
        try:
            current = int(limit.expression.this)
        except (AttributeError, ValueError, TypeError):
            # non-literal limit, just replace it
            current = max_rows + 1
        if current > max_rows:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))

    return GuardResult(True, tree.sql(dialect="sqlite"), [], tables, requires_approval)
