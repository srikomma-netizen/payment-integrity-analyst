import pytest

from analyst.agent import AnalystAgent, is_grounded
from analyst.llm import FakeAnalystLLM, QueryPlan
from analyst.warehouse import build_warehouse


@pytest.fixture(scope="module")
def conn():
    return build_warehouse()


def make_agent(conn, playbook):
    return AnalystAgent(conn, FakeAnalystLLM(playbook))


def test_happy_path_returns_grounded_answer(conn):
    agent = make_agent(conn, {
        "how many vendors do we have": QueryPlan(intent="count vendors", tables=["vendors"],
                                                 sql="SELECT COUNT(*) AS vendor_count FROM vendors"),
    })
    s = agent.ask("How many vendors do we have?")
    assert s.status == "done"
    assert s.rows == [[8]]
    assert s.grounded is True
    assert "8" in s.answer
    assert s.sql.endswith("LIMIT 200")


def test_guard_rejection_feeds_back_and_retries(conn):
    agent = make_agent(conn, {
        "vendor bank details": [
            QueryPlan(intent="x", sql="SELECT name, bank_account_last4 FROM vendors"),
            QueryPlan(intent="x", sql="SELECT name, country FROM vendors"),
        ],
    })
    s = agent.ask("Vendor bank details")
    assert s.status == "done" and s.attempts == 2
    _, feedback = agent.llm.calls[1]
    assert "restricted column" in feedback
    assert all("4421" not in str(r) for r in s.rows)


def test_gives_up_after_max_attempts(conn):
    agent = make_agent(conn, {"bad": QueryPlan(intent="x", sql="DELETE FROM vendors")})
    s = agent.ask("bad")
    assert s.status == "failed" and "3 attempts" in s.error


def test_runtime_sql_error_is_retried(conn):
    agent = make_agent(conn, {
        "overdue count": [
            QueryPlan(intent="x", sql="SELECT COUNT(*) FROM invoices WHERE stat = 'overdue'"),
            QueryPlan(intent="x", sql="SELECT COUNT(*) AS n FROM invoices WHERE status = 'overdue'"),
        ],
    })
    s = agent.ask("overdue count")
    assert s.status == "done" and s.attempts == 2
    assert "no such column" in agent.llm.calls[1][1]


def test_refusal_and_clarification_do_not_touch_db(conn):
    agent = make_agent(conn, {
        "secret": QueryPlan(intent="x", refuse=True, refuse_reason="not available"),
    })
    s = agent.ask("secret")
    assert s.status == "refused" and s.rows == [] and s.sql is None
    s2 = agent.ask("something vague")
    assert s2.status == "needs_clarification" and "?" in s2.answer


def test_payroll_requires_approval_then_runs(conn):
    agent = make_agent(conn, {
        "headcount": QueryPlan(intent="x", sql="SELECT SUM(headcount) AS total FROM payroll WHERE period='2025-06'"),
    })
    paused = agent.ask("headcount", role="finance_manager")
    assert paused.status == "awaiting_approval" and paused.rows == []
    done = agent.decide(paused.run_id, approved=True)
    assert done.status == "done" and done.rows[0][0] > 0
    # analyst role is denied by the guard before any approval question arises
    denied = agent.ask("headcount", role="analyst")
    assert denied.status == "failed" and "not permitted" in denied.error


def test_groundedness_check():
    cols, rows = ["cost_center", "total_spend"], [["Engineering", 96646.55], ["Sales", 74080.01]]
    assert is_grounded("Engineering spent $96,646.55 and Sales $74,080.01 (2 rows).", cols, rows)[0]
    assert is_grounded("Engineering spent about 96.6k.", cols, rows)[0]
    ok, bad = is_grounded("Engineering spent 120,000.", cols, rows)
    assert not ok and bad == [120000.0]
