import pytest

from analyst.guard import guard_sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM vendors",
    "UPDATE invoices SET status='paid'",
    "DROP TABLE gl_entries",
    "SELECT 1; SELECT 2",
    "PRAGMA table_info(vendors)",
    "SELECT load_extension('evil')",
    "SELECT * FROM sqlite_master",
    "INSERT INTO budgets SELECT * FROM budgets",
])
def test_rejects_non_select_and_dangerous(sql):
    assert not guard_sql(sql, role="analyst").ok


def test_rejects_restricted_column_anywhere():
    assert not guard_sql("SELECT name, bank_account_last4 FROM vendors", role="analyst").ok
    assert not guard_sql("SELECT name FROM vendors WHERE bank_account_last4 = '1'", role="analyst").ok
    assert not guard_sql("SELECT * FROM vendors", role="analyst").ok
    # subquery leak
    assert not guard_sql("SELECT (SELECT bank_account_last4 FROM vendors LIMIT 1)", role="analyst").ok


def test_role_permissions_and_approval_flag():
    denied = guard_sql("SELECT headcount FROM payroll", role="analyst")
    assert not denied.ok and "not permitted" in denied.message
    allowed = guard_sql("SELECT headcount FROM payroll", role="finance_manager")
    assert allowed.ok and allowed.requires_approval


def test_limit_is_added_and_capped():
    r = guard_sql("SELECT period FROM gl_entries", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 50")
    r = guard_sql("SELECT period FROM gl_entries LIMIT 9999", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 50")
    r = guard_sql("SELECT period FROM gl_entries LIMIT 5", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 5")


def test_cte_and_join_allowed():
    sql = ("WITH s AS (SELECT cost_center_id, SUM(amount_usd) a FROM gl_entries GROUP BY 1) "
           "SELECT c.name, s.a FROM s JOIN cost_centers c ON c.cost_center_id = s.cost_center_id")
    r = guard_sql(sql, role="analyst")
    assert r.ok and r.tables == {"gl_entries", "cost_centers"}


def test_unknown_table_rejected():
    r = guard_sql("SELECT * FROM salaries", role="finance_manager")
    assert not r.ok and "unknown table" in r.message
