"""Semantic layer: table/column documentation, metric definitions, and
schema retrieval.

The LLM never sees the raw database. It sees a curated catalog with
business definitions, and only the slice of it that is relevant to the
question. Access control lives here (table-level and column-level) and is
enforced deterministically by `guard.py`, not by the prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    restricted: bool = False  # never exposed to the model or returned


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]
    keywords: tuple[str, ...]
    allowed_roles: tuple[str, ...] = ("analyst", "finance_manager")
    requires_approval: bool = False  # human checkpoint before execution


@dataclass(frozen=True)
class Metric:
    name: str
    definition: str
    sql_hint: str
    keywords: tuple[str, ...]


TABLES: dict[str, Table] = {
    "cost_centers": Table(
        "cost_centers",
        "One row per cost center (department). Join key for spend, budgets, payroll.",
        (
            Column("cost_center_id", "TEXT", "Primary key, e.g. CC100"),
            Column("name", "TEXT", "Department name: Engineering, Marketing, Operations, Finance, Sales"),
            Column("region", "TEXT", "NA, EMEA, or APAC"),
            Column("owner", "TEXT", "Budget owner"),
        ),
        ("cost center", "department", "team", "region", "owner", "engineering", "marketing",
         "operations", "finance", "sales", "emea", "apac", "na"),
    ),
    "vendors": Table(
        "vendors",
        "Approved vendor master. bank_account_last4 is RESTRICTED and must never be selected.",
        (
            Column("vendor_id", "TEXT", "Primary key, e.g. V001"),
            Column("name", "TEXT", "Legal vendor name"),
            Column("country", "TEXT", "ISO-2 country code"),
            Column("category", "TEXT", "Software, Travel, Contractors, Facilities, Marketing"),
            Column("bank_account_last4", "TEXT", "Restricted banking detail", restricted=True),
        ),
        ("vendor", "supplier", "country", "category", "who do we pay"),
    ),
    "gl_entries": Table(
        "gl_entries",
        "General-ledger operating-expense lines. One row per posting. amount_usd is positive expense.",
        (
            Column("entry_id", "INTEGER", "Primary key"),
            Column("posted_date", "TEXT", "ISO date YYYY-MM-DD"),
            Column("period", "TEXT", "Accounting period YYYY-MM (2025-01 .. 2025-06)"),
            Column("account_code", "TEXT", "6100 Software, 6200 Travel, 6300 Contractors, 6400 Facilities, 6500 Marketing"),
            Column("account_name", "TEXT", "Human-readable account name"),
            Column("cost_center_id", "TEXT", "FK cost_centers"),
            Column("vendor_id", "TEXT", "FK vendors (nullable)"),
            Column("amount_usd", "REAL", "Expense amount in USD"),
            Column("description", "TEXT", "Free text"),
        ),
        ("spend", "expense", "opex", "cost", "gl", "ledger", "account", "software", "travel",
         "contractor", "facilities", "marketing", "period", "month", "quarter", "trend"),
    ),
    "invoices": Table(
        "invoices",
        "Accounts-payable invoices. status is paid / open / overdue. paid_date is NULL unless paid.",
        (
            Column("invoice_id", "TEXT", "Primary key INV-0001"),
            Column("vendor_id", "TEXT", "FK vendors"),
            Column("cost_center_id", "TEXT", "FK cost_centers"),
            Column("invoice_date", "TEXT", "ISO date"),
            Column("due_date", "TEXT", "ISO date"),
            Column("paid_date", "TEXT", "ISO date or NULL"),
            Column("amount_usd", "REAL", "Invoice amount in USD"),
            Column("status", "TEXT", "paid | open | overdue"),
        ),
        ("invoice", "payable", "ap", "overdue", "open", "paid", "due", "late", "aging", "dpo"),
    ),
    "budgets": Table(
        "budgets",
        "Monthly budget per cost center and account code. Compare with gl_entries for variance.",
        (
            Column("period", "TEXT", "YYYY-MM"),
            Column("cost_center_id", "TEXT", "FK cost_centers"),
            Column("account_code", "TEXT", "Same codes as gl_entries"),
            Column("budget_usd", "REAL", "Budgeted amount"),
        ),
        ("budget", "variance", "over budget", "under budget", "plan", "forecast"),
    ),
    "payroll": Table(
        "payroll",
        "Headcount and total compensation per cost center per period. SENSITIVE: finance_manager only, requires human approval.",
        (
            Column("period", "TEXT", "YYYY-MM"),
            Column("cost_center_id", "TEXT", "FK cost_centers"),
            Column("headcount", "INTEGER", "Employees on payroll"),
            Column("total_comp_usd", "REAL", "Total compensation paid"),
        ),
        ("payroll", "headcount", "compensation", "salary", "comp", "employees"),
        allowed_roles=("finance_manager",),
        requires_approval=True,
    ),
}

METRICS: list[Metric] = [
    Metric("total_spend", "Sum of gl_entries.amount_usd for the filters given.",
           "SUM(gl_entries.amount_usd)", ("spend", "expense", "opex", "cost", "total")),
    Metric("budget_variance", "Actual spend minus budget for the same period, cost center, and account. Positive = over budget.",
           "SUM(gl_entries.amount_usd) - SUM(budgets.budget_usd) joined on (period, cost_center_id, account_code)",
           ("variance", "over budget", "under budget", "vs budget", "against budget")),
    Metric("open_payables", "Sum of invoices.amount_usd where status IN ('open','overdue').",
           "SUM(amount_usd) FILTER (WHERE status IN ('open','overdue'))", ("open payables", "outstanding", "unpaid", "owe")),
    Metric("overdue_rate", "Count of overdue invoices divided by count of all invoices, as a fraction.",
           "AVG(CASE WHEN status='overdue' THEN 1.0 ELSE 0.0 END)", ("overdue rate", "late rate", "percent overdue")),
    Metric("avg_days_to_pay", "Average julianday(paid_date) - julianday(invoice_date) over paid invoices.",
           "AVG(julianday(paid_date) - julianday(invoice_date)) WHERE status='paid'", ("days to pay", "payment cycle", "dpo")),
]

RESTRICTED_COLUMNS: frozenset[str] = frozenset(
    c.name for t in TABLES.values() for c in t.columns if c.restricted
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def select_relevant(question: str, *, max_tables: int = 4) -> list[Table]:
    """Cheap lexical retrieval of candidate tables.

    In production this is an embedding search over the catalog; for a
    six-table mart a keyword overlap is honest and debuggable. Joins work
    because join-key tables (cost_centers, vendors) get a small boost when
    any fact table matches.
    """
    q = question.lower()
    q_tokens = _tokens(q)
    scored: list[tuple[float, Table]] = []
    for t in TABLES.values():
        score = 0.0
        for kw in t.keywords:
            if " " in kw and kw in q:
                score += 2.0
            elif kw in q_tokens:
                score += 1.0
        scored.append((score, t))
    scored.sort(key=lambda s: -s[0])
    chosen = [t for s, t in scored if s > 0][:max_tables]
    names = {t.name for t in chosen}
    if any(n in names for n in ("gl_entries", "invoices", "budgets", "payroll")):
        for dim in ("cost_centers", "vendors"):
            if dim not in names and len(chosen) < max_tables + 2:
                if dim == "vendors" and not any(k in q for k in ("vendor", "supplier", "who")):
                    continue
                chosen.append(TABLES[dim])
    return chosen or [TABLES["gl_entries"], TABLES["cost_centers"]]


def relevant_metrics(question: str) -> list[Metric]:
    q = question.lower()
    return [m for m in METRICS if any(k in q for k in m.keywords)]


def render_schema(tables: list[Table], metrics: list[Metric], role: str) -> str:
    """Prompt-ready schema text. Restricted columns are omitted entirely so
    the model cannot even name them; tables the role cannot see are listed
    as unavailable so the model refuses instead of guessing."""
    out: list[str] = ["SQLite dialect. Periods are 'YYYY-MM' strings. Dates are ISO 'YYYY-MM-DD'.", ""]
    for t in tables:
        if role not in t.allowed_roles:
            out.append(f"TABLE {t.name}: NOT AVAILABLE to role '{role}'. If the question needs it, set refuse=true.")
            out.append("")
            continue
        out.append(f"TABLE {t.name} -- {t.description}")
        for c in t.columns:
            if c.restricted:
                continue
            out.append(f"  {c.name} {c.type} -- {c.description}")
        out.append("")
    if metrics:
        out.append("BUSINESS METRIC DEFINITIONS (use these exact semantics):")
        for m in metrics:
            out.append(f"  {m.name}: {m.definition} SQL: {m.sql_hint}")
    return "\n".join(out)


@dataclass
class CatalogSlice:
    tables: list[Table]
    metrics: list[Metric]
    schema_text: str
    table_names: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.table_names = {t.name for t in self.tables}


def build_slice(question: str, role: str) -> CatalogSlice:
    tables = select_relevant(question)
    metrics = relevant_metrics(question)
    return CatalogSlice(tables, metrics, render_schema(tables, metrics, role))
