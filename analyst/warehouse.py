"""Synthetic finance warehouse (SQLite).

Deterministic seed so eval expectations are reproducible. No real data.
Tables mirror a small accounting mart: GL entries, invoices, vendors,
cost centers, budgets, plus a *restricted* payroll table used to
demonstrate role-based access and human approval.
"""
from __future__ import annotations

import random
import sqlite3
import time
from dataclasses import dataclass

PERIODS = [f"2025-{m:02d}" for m in range(1, 7)]

COST_CENTERS = [
    ("CC100", "Engineering", "NA", "A. Rivera"),
    ("CC200", "Marketing", "NA", "J. Chen"),
    ("CC300", "Operations", "EMEA", "M. Okafor"),
    ("CC400", "Finance", "NA", "S. Patel"),
    ("CC500", "Sales", "APAC", "L. Tanaka"),
]

VENDORS = [
    ("V001", "CloudStack Inc", "US", "Software", "4421"),
    ("V002", "Northwind Travel", "US", "Travel", "9930"),
    ("V003", "Helix Contractors", "GB", "Contractors", "1182"),
    ("V004", "Metro Facilities", "US", "Facilities", "7705"),
    ("V005", "DataForge Ltd", "IE", "Software", "3310"),
    ("V006", "Apex Consulting", "SG", "Contractors", "5566"),
    ("V007", "Brightline Media", "US", "Marketing", "2208"),
    ("V008", "Orbital Office", "DE", "Facilities", "6641"),
]

ACCOUNTS = {
    "6100": ("Software & Subscriptions", "Software"),
    "6200": ("Travel & Entertainment", "Travel"),
    "6300": ("Contractors & Consulting", "Contractors"),
    "6400": ("Facilities & Rent", "Facilities"),
    "6500": ("Marketing Programs", "Marketing"),
}

SCHEMA_SQL = """
CREATE TABLE cost_centers (
    cost_center_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    owner TEXT NOT NULL
);
CREATE TABLE vendors (
    vendor_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    category TEXT NOT NULL,
    bank_account_last4 TEXT NOT NULL
);
CREATE TABLE gl_entries (
    entry_id INTEGER PRIMARY KEY,
    posted_date TEXT NOT NULL,
    period TEXT NOT NULL,
    account_code TEXT NOT NULL,
    account_name TEXT NOT NULL,
    cost_center_id TEXT NOT NULL REFERENCES cost_centers(cost_center_id),
    vendor_id TEXT REFERENCES vendors(vendor_id),
    amount_usd REAL NOT NULL,
    description TEXT
);
CREATE TABLE invoices (
    invoice_id TEXT PRIMARY KEY,
    vendor_id TEXT NOT NULL REFERENCES vendors(vendor_id),
    cost_center_id TEXT NOT NULL REFERENCES cost_centers(cost_center_id),
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    paid_date TEXT,
    amount_usd REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paid','open','overdue'))
);
CREATE TABLE budgets (
    period TEXT NOT NULL,
    cost_center_id TEXT NOT NULL,
    account_code TEXT NOT NULL,
    budget_usd REAL NOT NULL,
    PRIMARY KEY (period, cost_center_id, account_code)
);
CREATE TABLE payroll (
    period TEXT NOT NULL,
    cost_center_id TEXT NOT NULL,
    headcount INTEGER NOT NULL,
    total_comp_usd REAL NOT NULL,
    PRIMARY KEY (period, cost_center_id)
);
"""


def _seed(conn: sqlite3.Connection, seed: int = 7) -> None:
    rng = random.Random(seed)
    conn.executemany("INSERT INTO cost_centers VALUES (?,?,?,?)", COST_CENTERS)
    conn.executemany("INSERT INTO vendors VALUES (?,?,?,?,?)", VENDORS)

    vendors_by_cat: dict[str, list[str]] = {}
    for vid, _, _, cat, _ in VENDORS:
        vendors_by_cat.setdefault(cat, []).append(vid)

    entry_id = 1
    for period in PERIODS:
        month = int(period[-2:])
        for cc_id, *_ in COST_CENTERS:
            for code, (acct_name, cat) in ACCOUNTS.items():
                budget = round(rng.uniform(8_000, 40_000), 2)
                conn.execute("INSERT INTO budgets VALUES (?,?,?,?)", (period, cc_id, code, budget))
                n_entries = rng.randint(1, 3)
                for _ in range(n_entries):
                    day = rng.randint(1, 28)
                    amount = round(budget / n_entries * rng.uniform(0.6, 1.3), 2)
                    vendor = rng.choice(vendors_by_cat[cat])
                    conn.execute(
                        "INSERT INTO gl_entries VALUES (?,?,?,?,?,?,?,?,?)",
                        (entry_id, f"2025-{month:02d}-{day:02d}", period, code, acct_name,
                         cc_id, vendor, amount, f"{acct_name} - {vendor}"),
                    )
                    entry_id += 1

    inv_id = 1
    for period in PERIODS:
        month = int(period[-2:])
        for _ in range(7):
            vid, _, _, cat, _ = rng.choice(VENDORS)
            cc_id = rng.choice(COST_CENTERS)[0]
            inv_day = rng.randint(1, 25)
            invoice_date = f"2025-{month:02d}-{inv_day:02d}"
            due_month = min(month + 1, 7)
            due_date = f"2025-{due_month:02d}-{inv_day:02d}"
            amount = round(rng.uniform(1_500, 60_000), 2)
            roll = rng.random()
            if roll < 0.7:
                status = "paid"
                paid_date = f"2025-{due_month:02d}-{max(1, inv_day - rng.randint(0, 10)):02d}"
            elif roll < 0.85:
                status, paid_date = "open", None
            else:
                status, paid_date = "overdue", None
            conn.execute(
                "INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?)",
                (f"INV-{inv_id:04d}", vid, cc_id, invoice_date, due_date, paid_date, amount, status),
            )
            inv_id += 1

    for period in PERIODS:
        for cc_id, *_ in COST_CENTERS:
            headcount = rng.randint(8, 60)
            conn.execute(
                "INSERT INTO payroll VALUES (?,?,?,?)",
                (period, cc_id, headcount, round(headcount * rng.uniform(9_000, 14_000), 2)),
            )
    conn.commit()


def build_warehouse(path: str = ":memory:", seed: int = 7) -> sqlite3.Connection:
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
    """Run an already-guarded SELECT with a wall-clock timeout and row cap.

    The timeout uses SQLite's progress handler so a runaway query is
    interrupted instead of hanging the worker.
    """
    deadline = time.monotonic() + timeout_s

    def _progress():
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(_progress, 1000)
    start = time.perf_counter()
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e).lower():
            raise QueryTimeout(f"query exceeded {timeout_s}s") from e
        raise
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return QueryResult(columns, rows, len(rows), (time.perf_counter() - start) * 1000, truncated)
