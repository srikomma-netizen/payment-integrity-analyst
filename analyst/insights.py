"""Fixed SQL behind the dashboard and case worklist (no model in this path).

These bypass the guard, so they must never select a restricted column.
"""
from __future__ import annotations

import sqlite3
from typing import Any

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}  # not referenced below; CASE_SQL inlines the same ranking


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    """KPIs, monthly trend, per-rule performance, outcomes and provider ranking for the dashboard."""
    # FIXME: COUNT(*) and SUM(paid_amount) run over claim x flag rows from the LEFT JOIN, so a
    # claim with two flags is counted twice (monthly totals land slightly above the KPI totals).
    # `flagged` is DISTINCT and is fine. Needs COUNT(DISTINCT) plus a pre-aggregated flag subquery.
    monthly = _rows(conn, """
        SELECT c.period,
               COUNT(*) AS claims,
               SUM(CASE WHEN c.status = 'paid' THEN c.paid_amount ELSE 0 END) AS paid_amount,
               COUNT(DISTINCT f.claim_id) AS flagged
        FROM claims c LEFT JOIN risk_flags f ON f.claim_id = c.claim_id
        GROUP BY c.period ORDER BY c.period""")
    for m in monthly:
        m["flag_rate"] = (m["flagged"] / m["claims"]) if m["claims"] else 0.0

    # same semantics as the duplicate_overpayment metric in catalog.py: everything past the largest payment
    dup = conn.execute("""
        SELECT COUNT(*), COALESCE(SUM(over), 0) FROM (
          SELECT claim_id, SUM(amount) - MAX(amount) AS over FROM payments GROUP BY claim_id HAVING COUNT(*) > 1)""").fetchone()
    closed = _one(conn, "SELECT COUNT(*) FROM cases WHERE status = 'closed'")
    fp = _one(conn, "SELECT COUNT(*) FROM cases WHERE outcome = 'false_positive'")  # outcome is only set on closed cases
    kpis = {
        "claims": _one(conn, "SELECT COUNT(*) FROM claims"),
        "paid_amount": _one(conn, "SELECT COALESCE(SUM(paid_amount), 0) FROM claims WHERE status = 'paid'"),
        "flagged_claims": _one(conn, "SELECT COUNT(DISTINCT claim_id) FROM risk_flags"),
        "flag_rate": _one(conn, "SELECT COUNT(DISTINCT f.claim_id) * 1.0 / COUNT(DISTINCT c.claim_id) FROM claims c LEFT JOIN risk_flags f ON f.claim_id = c.claim_id"),
        "open_cases": _one(conn, "SELECT COUNT(*) FROM cases WHERE status = 'open'"),
        "closed_cases": closed,
        "false_positive_rate": (fp / closed) if closed else 0.0,
        "recovered": _one(conn, "SELECT COALESCE(SUM(recovery_amount), 0) FROM cases WHERE outcome = 'confirmed'"),
        "duplicate_claims": dup[0],
        "overpaid": dup[1],
        "avg_days_to_close": _one(conn, "SELECT AVG(julianday(closed_date) - julianday(opened_date)) FROM cases WHERE status = 'closed'"),
    }
    by_rule = _rows(conn, """
        SELECT f.rule_id, f.signal_type, f.severity, COUNT(*) AS flags,
               SUM(CASE WHEN k.outcome = 'confirmed' THEN 1 ELSE 0 END) AS confirmed,
               SUM(CASE WHEN k.outcome = 'false_positive' THEN 1 ELSE 0 END) AS false_positive,
               SUM(CASE WHEN k.status = 'closed' THEN 1 ELSE 0 END) AS closed
        FROM risk_flags f LEFT JOIN cases k ON k.claim_id = f.claim_id
        GROUP BY f.rule_id, f.signal_type, f.severity ORDER BY f.rule_id""")
    for r in by_rule:
        # None, not 0.0: a rule with no closed cases has no rate yet, and the UI shows n/a
        r["false_positive_rate"] = (r["false_positive"] / r["closed"]) if r["closed"] else None
    outcomes = _rows(conn, """
        SELECT COALESCE(outcome, 'open') AS outcome, COUNT(*) AS cases, COALESCE(SUM(recovery_amount), 0) AS recovered
        FROM cases GROUP BY 1 ORDER BY cases DESC""")
    providers = _rows(conn, """
        SELECT p.provider_id, p.name, p.specialty, COUNT(DISTINCT c.claim_id) AS claims,
               COUNT(DISTINCT f.claim_id) AS flagged, SUM(c.billed_amount) AS billed
        FROM claims c JOIN providers p ON p.provider_id = c.provider_id
        LEFT JOIN risk_flags f ON f.claim_id = c.claim_id
        GROUP BY p.provider_id ORDER BY flagged DESC, billed DESC""")
    for p in providers:
        p["flag_rate"] = p["flagged"] / p["claims"] if p["claims"] else 0.0
    return {"kpis": kpis, "monthly": monthly, "by_rule": by_rule, "outcomes": outcomes, "providers": providers}


# Shared by the worklist and the detail view; callers append WHERE / ORDER BY.
# Open cases are aged against 2025-07-31, the end of the synthetic data window, not today's date.
CASE_SQL = """
    SELECT k.case_id, k.claim_id, k.status, k.outcome, k.opened_date, k.closed_date, k.investigator,
           k.recovery_amount, c.member_id, c.claim_type, c.procedure_code, c.procedure_desc, c.period,
           c.billed_amount, c.paid_amount, c.service_date,
           COALESCE(p.name, v.name) AS counterparty, COALESCE(c.provider_id, c.vendor_id) AS counterparty_id,
           (SELECT GROUP_CONCAT(rule_id, ',') FROM (SELECT DISTINCT rule_id FROM risk_flags WHERE claim_id = k.claim_id ORDER BY rule_id)) AS rules,
           (SELECT MAX(score) FROM risk_flags WHERE claim_id = k.claim_id) AS max_score,
           (SELECT MAX(CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END) FROM risk_flags WHERE claim_id = k.claim_id) AS sev_rank,
           CAST(julianday(COALESCE(k.closed_date, '2025-07-31')) - julianday(k.opened_date) AS INTEGER) AS age_days
    FROM cases k JOIN claims c ON c.claim_id = k.claim_id
    LEFT JOIN providers p ON p.provider_id = c.provider_id
    LEFT JOIN vendors v ON v.vendor_id = c.vendor_id
"""


def _severity(rank: int | None) -> str:
    return {3: "high", 2: "medium", 1: "low"}.get(rank or 0, "low")


def cases(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    # 'open' sorts after 'closed', so DESC puts open cases first
    out = _rows(conn, CASE_SQL + " ORDER BY k.status DESC, sev_rank DESC, k.opened_date DESC")
    for r in out:
        r["severity"] = _severity(r.pop("sev_rank"))
        r["rules"] = (r["rules"] or "").split(",") if r["rules"] else []
    return out


def case_detail(conn: sqlite3.Connection, case_id: str) -> dict[str, Any] | None:
    rows = _rows(conn, CASE_SQL + " WHERE k.case_id = ?", (case_id,))
    if not rows:
        return None
    case = rows[0]
    case["severity"] = _severity(case.pop("sev_rank"))
    case["rules"] = (case["rules"] or "").split(",") if case["rules"] else []
    case["flags"] = _rows(conn, "SELECT flag_id, rule_id, signal_type, severity, score, flagged_date FROM risk_flags WHERE claim_id = ? ORDER BY score DESC", (case["claim_id"],))
    case["payments"] = _rows(conn, "SELECT payment_id, paid_date, amount, payee_id, method FROM payments WHERE claim_id = ? ORDER BY paid_date", (case["claim_id"],))
    # explicit column list keeps mrn/dob out; never SELECT * from members here
    case["member"] = _rows(conn, "SELECT member_id, plan, region FROM members WHERE member_id = ?", (case["member_id"],))[0]
    case["member_history"] = _rows(conn, """
        SELECT claim_id, service_date, procedure_code, billed_amount, status FROM claims
        WHERE member_id = ? ORDER BY service_date DESC LIMIT 8""", (case["member_id"],))
    # count only; note text is siu_lead + approval and stays behind the agent's guard
    case["note_count"] = _one(conn, "SELECT COUNT(*) FROM investigator_notes WHERE case_id = ?", (case_id,))
    return case
