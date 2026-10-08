"""Table docs, metrics and schema retrieval. Enforced in guard.py."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str
    restricted: bool = False  # hidden from the model


@dataclass(frozen=True)
class Table:
    name: str
    description: str
    columns: tuple[Column, ...]
    keywords: tuple[str, ...]
    allowed_roles: tuple[str, ...] = ("analyst", "siu_lead")
    requires_approval: bool = False


@dataclass(frozen=True)
class Metric:
    name: str
    definition: str
    sql_hint: str
    keywords: tuple[str, ...]


# descriptions go straight into the prompt, keep them short
TABLES: dict[str, Table] = {
    "members": Table(
        "members",
        "One row per plan member. mrn and dob are PHI and RESTRICTED: never select or filter on them.",
        (
            Column("member_id", "TEXT", "Pseudonymous key, e.g. MBR-2001"),
            Column("plan", "TEXT", "PPO-Gold, HMO-Silver, PPO-Bronze"),
            Column("region", "TEXT", "IL, WI, IN"),
            Column("mrn", "TEXT", "Medical record number (PHI)", restricted=True),
            Column("dob", "TEXT", "Date of birth (PHI)", restricted=True),
        ),
        ("member", "patient", "plan", "region"),
    ),
    "providers": Table(
        "providers",
        "Provider master. npi is RESTRICTED.",
        (
            Column("provider_id", "TEXT", "Primary key, e.g. PRV-500"),
            Column("name", "TEXT", "Practice name"),
            Column("specialty", "TEXT", "Primary Care, Laboratory, Radiology, Cardiology, Orthopedics"),
            Column("state", "TEXT", "IL, WI, IN"),
            Column("npi", "TEXT", "National provider identifier", restricted=True),
        ),
        ("provider", "practice", "clinic", "specialty", "physician", "who billed"),
    ),
    "vendors": Table(
        "vendors",
        "Vendor master for facility and supply invoices. bank_account_last4 is RESTRICTED.",
        (
            Column("vendor_id", "TEXT", "Primary key, e.g. VND-700"),
            Column("name", "TEXT", "Legal vendor name"),
            Column("category", "TEXT", "Medical Supplies, Billing, Facilities, Pharmaceuticals"),
            Column("country", "TEXT", "ISO-2 country code"),
            Column("bank_account_last4", "TEXT", "Banking detail", restricted=True),
        ),
        ("vendor", "supplier", "invoice", "bank"),
    ),
    "claims": Table(
        "claims",
        "One row per adjudicated claim line. billed_amount is what was submitted; paid_amount is what was disbursed (0 unless status='paid').",
        (
            Column("claim_id", "TEXT", "Primary key CLM-1001"),
            Column("member_id", "TEXT", "FK members"),
            Column("provider_id", "TEXT", "FK providers (NULL for vendor claims)"),
            Column("vendor_id", "TEXT", "FK vendors (NULL for professional claims)"),
            Column("service_date", "TEXT", "ISO date"),
            Column("period", "TEXT", "YYYY-MM (2025-01 .. 2025-06)"),
            Column("claim_type", "TEXT", "professional | facility | vendor"),
            Column("procedure_code", "TEXT", "CPT code, e.g. 99215, 80053; FAC-INV for vendor invoices"),
            Column("procedure_desc", "TEXT", "Human-readable procedure"),
            Column("billed_amount", "REAL", "Submitted amount USD"),
            Column("paid_amount", "REAL", "Disbursed amount USD"),
            Column("status", "TEXT", "paid | denied | pended"),
        ),
        ("claim", "claims", "billed", "paid", "procedure", "cpt", "service", "period", "month", "quarter", "trend", "denied", "pended"),
    ),
    "payments": Table(
        "payments",
        "Disbursements. A claim with more than one payment row is a duplicate payment.",
        (
            Column("payment_id", "TEXT", "Primary key PAY-1001A"),
            Column("claim_id", "TEXT", "FK claims"),
            Column("paid_date", "TEXT", "ISO date"),
            Column("amount", "REAL", "Amount USD"),
            Column("payee_id", "TEXT", "provider_id or vendor_id paid"),
            Column("method", "TEXT", "ACH | WIRE | CHECK"),
        ),
        ("payment", "payments", "disbursement", "duplicate", "overpaid", "overpayment", "paid twice"),
    ),
    "risk_flags": Table(
        "risk_flags",
        "Signals raised by upstream risk rules. One row per (claim, rule). Rules: R1 duplicate_payment, R2 amount_outlier, R3 unbundling, R4 prior_confirmed_case, R5 unverified_bank_change.",
        (
            Column("flag_id", "TEXT", "Primary key"),
            Column("claim_id", "TEXT", "FK claims"),
            Column("rule_id", "TEXT", "R1..R5"),
            Column("signal_type", "TEXT", "duplicate_payment | amount_outlier | unbundling | prior_confirmed_case | unverified_bank_change"),
            Column("severity", "TEXT", "low | medium | high"),
            Column("score", "REAL", "0..1 model score"),
            Column("flagged_date", "TEXT", "ISO date"),
        ),
        ("flag", "flagged", "flags", "rule", "signal", "risk", "alert", "upcoding", "unbundling", "outlier", "bank change"),
    ),
    "cases": Table(
        "cases",
        "Investigation cases opened from flagged claims. outcome is NULL while status='open'.",
        (
            Column("case_id", "TEXT", "Primary key PC-0401"),
            Column("claim_id", "TEXT", "FK claims"),
            Column("opened_date", "TEXT", "ISO date"),
            Column("closed_date", "TEXT", "ISO date or NULL"),
            Column("status", "TEXT", "open | closed"),
            Column("outcome", "TEXT", "confirmed | false_positive | closed_no_action | NULL"),
            Column("investigator", "TEXT", "Assigned investigator id"),
            Column("recovery_amount", "REAL", "USD recovered (confirmed cases)"),
        ),
        ("case", "cases", "investigation", "investigator", "outcome", "false positive", "confirmed", "recovery", "recovered", "days to close", "open cases", "backlog"),
    ),
    "investigator_notes": Table(
        "investigator_notes",
        "Free-text investigator notes. SENSITIVE: siu_lead only, requires human approval.",
        (
            Column("note_id", "INTEGER", "Primary key"),
            Column("case_id", "TEXT", "FK cases"),
            Column("author", "TEXT", "Investigator id"),
            Column("note", "TEXT", "Free text"),
        ),
        ("note", "notes", "narrative", "comments", "what did the investigator say"),
        allowed_roles=("siu_lead",),
        requires_approval=True,
    ),
}

# model kept getting fp rate wrong without these
METRICS: list[Metric] = [
    Metric("flag_rate", "Share of claims in scope that have at least one risk flag.",
           "COUNT(DISTINCT risk_flags.claim_id) * 1.0 / COUNT(DISTINCT claims.claim_id) with a LEFT JOIN from claims to risk_flags",
           ("flag rate", "share flagged", "percent flagged", "what share")),
    Metric("false_positive_rate", "Closed cases with outcome='false_positive' divided by all closed cases.",
           "AVG(CASE WHEN outcome='false_positive' THEN 1.0 ELSE 0.0 END) WHERE status='closed'",
           ("false positive rate", "false positives", "fp rate")),
    Metric("duplicate_overpayment", "For claims with more than one payment, the amount beyond the first payment: SUM(amount) - MAX(amount) per claim.",
           "SELECT claim_id, COUNT(*) n, SUM(amount) - MAX(amount) AS overpaid FROM payments GROUP BY claim_id HAVING n > 1",
           ("duplicate", "overpaid", "overpayment", "paid twice")),
    Metric("avg_days_to_close", "Average julianday(closed_date) - julianday(opened_date) over closed cases.",
           "AVG(julianday(closed_date) - julianday(opened_date)) WHERE status='closed'",
           ("days to close", "cycle time", "how long")),
    Metric("recovery_total", "SUM(cases.recovery_amount) for confirmed cases.",
           "SUM(recovery_amount) WHERE outcome='confirmed'", ("recovery", "recovered", "recoveries")),
]

# bare names, breaks if a restricted name gets reused
RESTRICTED_COLUMNS: frozenset[str] = frozenset(
    c.name for t in TABLES.values() for c in t.columns if c.restricted
)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def select_relevant(question: str, *, max_tables: int = 4) -> list[Table]:
    """Pick candidate tables by keyword overlap."""
    # TODO: embeddings if the catalog gets big
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
    scored.sort(key=lambda s: -s[0])  # stable, ties keep TABLES order
    chosen = [t for s, t in scored if s > 0][:max_tables]
    names = {t.name for t in chosen}
    # claims is the join hub, can go past max_tables
    if names & {"payments", "risk_flags", "cases", "investigator_notes"} and "claims" not in names:
        chosen.append(TABLES["claims"])
    if "claims" in {t.name for t in chosen}:
        for dim, hint in (("providers", "provider"), ("vendors", "vendor")):
            if dim not in names and hint in q:
                chosen.append(TABLES[dim])
    return chosen or [TABLES["claims"], TABLES["risk_flags"]]


def relevant_metrics(question: str) -> list[Metric]:
    q =question.lower()
    return [m for m in METRICS if any(k in q for k in m.keywords)]


def render_schema(tables: list[Table], metrics: list[Metric], role: str) -> str:
    out: list[str] = ["SQLite dialect. Periods are 'YYYY-MM' strings. Dates are ISO 'YYYY-MM-DD'.", ""]
    for t in tables:
        # shown as unavailable so the model refuses
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
