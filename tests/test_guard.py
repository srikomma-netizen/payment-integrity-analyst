import pytest

from analyst.guard import guard_sql


@pytest.mark.parametrize("sql", [
    "DELETE FROM vendors",
    "UPDATE cases SET status='closed'",
    "DROP TABLE claims",
    "SELECT 1; SELECT 2",
    "PRAGMA table_info(members)",
    "SELECT load_extension('evil')",
    "SELECT * FROM sqlite_master",
    "INSERT INTO risk_flags SELECT * FROM risk_flags",
])
def test_rejects_non_select_and_dangerous(sql):
    assert not guard_sql(sql, role="analyst").ok


def test_rejects_restricted_phi_column_anywhere():
    assert not guard_sql("SELECT member_id, mrn FROM members", role="analyst").ok
    assert not guard_sql("SELECT member_id FROM members WHERE dob < '1960-01-01'", role="analyst").ok
    assert not guard_sql("SELECT * FROM members", role="analyst").ok
    assert not guard_sql("SELECT (SELECT npi FROM providers LIMIT 1)", role="siu_lead").ok
    assert not guard_sql("SELECT name, bank_account_last4 FROM vendors", role="siu_lead").ok


def test_count_star_is_not_a_projection_star():
    assert guard_sql("SELECT COUNT(*) AS n FROM members", role="analyst").ok
    assert guard_sql("SELECT * FROM cases", role="analyst").ok   # no restricted columns on cases


def test_role_permissions_and_approval_flag():
    denied = guard_sql("SELECT note FROM investigator_notes", role="analyst")
    assert not denied.ok and "not permitted" in denied.message
    allowed = guard_sql("SELECT note FROM investigator_notes", role="siu_lead")
    assert allowed.ok and allowed.requires_approval


def test_limit_is_added_and_capped():
    r = guard_sql("SELECT period FROM claims", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 50")
    r = guard_sql("SELECT period FROM claims LIMIT 9999", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 50")
    r = guard_sql("SELECT period FROM claims LIMIT 5", role="analyst", max_rows=50)
    assert r.ok and r.sql.endswith("LIMIT 5")


def test_cte_and_join_allowed():
    sql = ("WITH s AS (SELECT provider_id, SUM(billed_amount) b FROM claims GROUP BY 1) "
           "SELECT p.name, s.b FROM s JOIN providers p ON p.provider_id = s.provider_id")
    r = guard_sql(sql, role="analyst")
    assert r.ok and r.tables == {"claims", "providers"}


def test_unknown_table_rejected():
    r = guard_sql("SELECT * FROM salaries", role="siu_lead")
    assert not r.ok and "unknown table" in r.message
