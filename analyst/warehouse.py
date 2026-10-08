"""Synthetic payment-integrity mart in SQLite: claims, payments, risk flags, cases.

Seeded deterministically so eval expectations stay stable. No real data or PHI.
"""
from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass

# six monthly periods; the catalog and eval cases assume exactly this range
PERIODS = [f"2025-{m:02d}" for m in range(1, 7)]

PROVIDERS = [
    ("PRV-500", "Lakeside Family Clinic", "Primary Care", "IL"),
    ("PRV-501", "Northshore Diagnostics", "Laboratory", "IL"),
    ("PRV-502", "Prairie Imaging Center", "Radiology", "WI"),
    ("PRV-503", "Riverbend Cardiology", "Cardiology", "IL"),
    ("PRV-504", "Oakline Medical Group", "Primary Care", "IN"),
    ("PRV-505", "Harbor Orthopedics", "Orthopedics", "IL"),
]

VENDORS = [
    ("VND-700", "MedSupply Partners", "Medical Supplies", "US"),
    ("VND-701", "ClearPath Billing Services", "Billing", "US"),
    ("VND-702", "Summit Facilities", "Facilities", "US"),
    ("VND-703", "Aster Pharma Distribution", "Pharmaceuticals", "IE"),
]

# code -> (description, base billed amount in USD)
PROCEDURES = {
    "99213": ("Office visit, established patient", 110.0),
    "99214": ("Office visit, moderate complexity", 165.0),
    "99215": ("Office visit, high complexity", 230.0),
    "80053": ("Comprehensive metabolic panel", 48.0),
    "85025": ("Complete blood count", 32.0),
    "71046": ("Chest X-ray, two views", 95.0),
    "93000": ("Electrocardiogram", 70.0),
    "29881": ("Knee arthroscopy", 2_400.0),
}

# rule_id -> (signal_type, severity); keep in sync with the risk_flags description in catalog.py
RULES = {
    "R1": ("duplicate_payment", "high"),
    "R2": ("amount_outlier", "medium"),
    "R3": ("unbundling", "medium"),
    "R4": ("prior_confirmed_case", "medium"),
    "R5": ("unverified_bank_change", "high"),
}

# mrn/dob, npi and bank_account_last4 exist only so the guard has restricted
# columns to enforce; investigator_notes is the siu_lead + approval table.
SCHEMA_SQL = """
CREATE TABLE members (
    member_id TEXT PRIMARY KEY,
    plan TEXT NOT NULL,
    region TEXT NOT NULL,
    mrn TEXT NOT NULL,
    dob TEXT NOT NULL
);
CREATE TABLE providers (
    provider_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    specialty TEXT NOT NULL,
    state TEXT NOT NULL,
    npi TEXT NOT NULL
);
CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    country TEXT NOT NULL,
    bank_account_last4 TEXT NOT NULL
);
CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY,
    member_id TEXT NOT NULL REFERENCES members(member_id),
    provider_id TEXT REFERENCES providers(provider_id),
    vendor_id TEXT REFERENCES vendors(vendor_id),
    service_date TEXT NOT NULL,
    period TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type IN ('professional','facility','vendor')),
    procedure_code TEXT NOT NULL,
    procedure_desc TEXT NOT NULL,
    billed_amount REAL NOT NULL,
    paid_amount REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid','denied','pended'))
);
CREATE TABLE payments (
    payment_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    paid_date TEXT NOT NULL,
    amount REAL NOT NULL,
    payee_id TEXT NOT NULL,
    method TEXT NOT NULL
);
CREATE TABLE risk_flags (
    flag_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    rule_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    score REAL NOT NULL,
    flagged_date TEXT NOT NULL
);
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    opened_date TEXT NOT NULL,
    closed_date TEXT,
    status TEXT NOT NULL CHECK (status IN ('open','closed')),
    outcome TEXT CHECK (outcome IN ('confirmed','false_positive','closed_no_action')),
    investigator TEXT NOT NULL,
    recovery_amount REAL NOT NULL DEFAULT 0
);
CREATE TABLE investigator_notes (
    note_id INTEGER PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES cases(case_id),
    author TEXT NOT NULL,
    note TEXT NOT NULL
);
"""


def _seed(conn: sqlite3.Connection, seed: int = 7) -> None:
    # every random draw goes through this one rng, so reordering any call
    # below shifts the whole dataset and breaks the eval expectations
    rng = random.Random(seed)
    for i in range(40):
        conn.execute("INSERT INTO members VALUES (?,?,?,?,?)",
                     (f"MBR-{2000 + i}", rng.choice(["PPO-Gold", "HMO-Silver", "PPO-Bronze"]),
                      rng.choice(["IL", "WI", "IN"]), f"MRN{rng.randint(1_000_000, 9_999_999)}",
                      f"19{rng.randint(45, 99):02d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"))
    for pid, name, spec, state in PROVIDERS:
        conn.execute("INSERT INTO providers VALUES (?,?,?,?,?)", (pid, name, spec, state, f"{rng.randint(10**9, 10**10 - 1)}"))
    for vid, name, cat, country in VENDORS:
        conn.execute("INSERT INTO vendors VALUES (?,?,?,?,?)", (vid, name, cat, country, f"{rng.randint(1000, 9999)}"))

    claim_no = 1000
    flag_no = 1
    for period in PERIODS:
        month = int(period[-2:])
        for _ in range(45):
            claim_no += 1
            cid = f"CLM-{claim_no}"
            code = rng.choice(list(PROCEDURES))
            desc, base = PROCEDURES[code]
            provider = rng.choice(PROVIDERS)[0]
            vendor = None
            claim_type = "professional"
            if rng.random() < 0.12:            # vendor / facility invoice lines
                vendor, provider, claim_type = rng.choice(VENDORS)[0], None, "vendor"
                code, desc, base = "FAC-INV", "Facilities invoice", 18_000.0
            day = rng.randint(1, 28)
            billed = round(base * rng.uniform(0.85, 1.2), 2)
            roll = rng.random()
            status = "paid" if roll < 0.82 else "denied" if roll < 0.92 else "pended"
            paid = round(billed * rng.uniform(0.7, 1.0), 2) if status == "paid" else 0.0
            # planted anomalies, each tied to one rule so the flags have a real cause in the data
            flags: list[tuple[str, float]] = []
            # R2: inflate a slice of high-complexity visits to ~3x base
            if claim_type == "professional" and code == "99215" and rng.random() < 0.35:
                billed = round(base * rng.uniform(2.5, 3.2), 2); paid = round(billed * 0.9, 2) if status == "paid" else 0.0
                flags.append(("R2", round(rng.uniform(0.5, 0.9), 2)))
            if claim_type == "professional" and code in ("80053", "85025") and rng.random() < 0.3:
                flags.append(("R3", 0.55))
            if provider == "PRV-500" and rng.random() < 0.4:   # one provider with a prior-case history
                flags.append(("R4", 0.4))
            # Summit Facilities is the vendor with the unverified bank change
            if claim_type == "vendor" and vendor == "VND-702" and rng.random() < 0.6:
                flags.append(("R5", 0.85))
            conn.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, f"MBR-{2000 + rng.randint(0, 39)}", provider, vendor,
                          f"2025-{month:02d}-{day:02d}", period, claim_type, code, desc, billed, paid, status))
            # only paid claims get a payment row; denied/pended have paid_amount 0
            if status == "paid":
                payee = provider or vendor
                conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?)",
                             (f"PAY-{claim_no}A", cid, f"2025-{month:02d}-{min(28, day + 7):02d}", paid, payee, "ACH"))
                if rng.random() < 0.05:        # duplicate disbursement
                    conn.execute("INSERT INTO payments VALUES (?,?,?,?,?,?)",
                                 (f"PAY-{claim_no}B", cid, f"2025-{month:02d}-{min(28, day + 9):02d}", paid, payee, "ACH"))
                    flags.append(("R1", 0.8))
            for rule, score in flags:
                sig, sev = RULES[rule]
                conn.execute("INSERT INTO risk_flags VALUES (?,?,?,?,?,?,?)",
                             (f"FLG-{flag_no:04d}", cid, rule, sig, sev, score, f"2025-{month:02d}-{min(28, day + 10):02d}"))
                flag_no += 1

    # ~80% of flagged claims become a case; each case gets one note.
    # Days are capped at 28 everywhere so we never build an invalid date.
    investigators = ["inv_4", "inv_7", "inv_9", "lead_2"]
    case_no = 400
    for (cid, flagged_date) in conn.execute("SELECT DISTINCT claim_id, MIN(flagged_date) FROM risk_flags GROUP BY claim_id").fetchall():
        if rng.random() < 0.8:
            case_no += 1
            case_id = f"PC-{case_no:04d}"
            month = int(flagged_date[5:7])
            opened = flagged_date
            if rng.random() < 0.7:
                status = "closed"
                outcome = rng.choices(["confirmed", "false_positive", "closed_no_action"], [0.45, 0.35, 0.2])[0]
                closed = f"2025-{min(7, month + rng.randint(0, 1)):02d}-{rng.randint(1, 28):02d}"
                if closed <= opened:   # ISO strings compare correctly; keeps days-to-close positive
                    closed = f"2025-{min(7, month + 1):02d}-{rng.randint(1, 28):02d}"
                recovery = round(rng.uniform(150, 4_500), 2) if outcome == "confirmed" else 0.0
            else:
                status, outcome, closed, recovery = "open", None, None, 0.0
            conn.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?)",
                         (case_id, cid, opened, closed, status, outcome, rng.choice(investigators), recovery))
            conn.execute("INSERT INTO investigator_notes (case_id, author, note) VALUES (?,?,?)",
                         (case_id, rng.choice(investigators),
                          rng.choice(["Requested medical records from provider.", "Call-back to vendor completed; details verified.",
                                      "Resubmission after portal timeout; single payment confirmed.", "Chart supports billed level; closing.",
                                      "Escalated to SIU lead pending recovery letter."])))
    conn.commit()


def build_warehouse(path: str = ":memory:", seed: int = 7) -> sqlite3.Connection:
    # one shared connection is used from FastAPI's threadpool, hence check_same_thread=False
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    _seed(conn, seed)
    return conn


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    elapsed_ms: float
    truncated: bool = False


class QueryTimeout(Exception):
    pass


def execute_readonly(conn: sqlite3.Connection, sql: str, *, timeout_s: float = 5.0,
                     max_rows: int = 200) -> QueryResult:
    """Run an already-guarded SELECT with a wall-clock timeout and row cap."""
    deadline = time.monotonic() + timeout_s

    # a non-zero return from the progress handler makes SQLite abort with "interrupted"
    def _progress():
        return 1 if time.monotonic() > deadline else 0

    # NOTE: SQLite only; a warehouse like BigQuery would need a job timeout / maximum_bytes_billed instead
    conn.set_progress_handler(_progress, 1000)  # checked every 1000 VM instructions
    start = time.perf_counter()
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        # fetch one extra row so we can tell "exactly max_rows" from "truncated"
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeout(f"query exceeded {timeout_s}s") from e
        raise
    finally:
        conn.set_progress_handler(None, 0)  # the connection is shared, don't leave the deadline armed
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return QueryResult(columns, rows, len(rows), (time.perf_counter() - start) * 1000, truncated)
