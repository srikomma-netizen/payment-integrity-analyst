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
        "how many providers do we have": QueryPlan(intent="count providers", tables=["providers"],
                                                   sql="SELECT COUNT(*) AS provider_count FROM providers"),
    })
    s = agent.ask("How many providers do we have?")
    assert s.status == "done"
    assert s.rows == [[6]]
    assert s.grounded is True
    assert "6" in s.answer
    assert s.sql.endswith("LIMIT 200")


def test_guard_rejection_feeds_back_and_retries(conn):
    agent = make_agent(conn, {
        "member details": [
            QueryPlan(intent="x", sql="SELECT member_id, mrn FROM members"),
            QueryPlan(intent="x", sql="SELECT member_id, plan FROM members"),
        ],
    })
    s = agent.ask("Member details")
    assert s.status == "done" and s.attempts == 2
    _, feedback = agent.llm.calls[1]
    assert "restricted column 'mrn'" in feedback
    assert all(not str(v).startswith("MRN") for r in s.rows for v in r)


def test_gives_up_after_max_attempts(conn):
    agent = make_agent(conn, {"bad": QueryPlan(intent="x", sql="DELETE FROM cases")})
    s = agent.ask("bad")
    assert s.status == "failed" and "3 attempts" in s.error


def test_runtime_sql_error_is_retried(conn):
    agent = make_agent(conn, {
        "open case count": [
            QueryPlan(intent="x", sql="SELECT COUNT(*) FROM cases WHERE stat = 'open'"),
            QueryPlan(intent="x", sql="SELECT COUNT(*) AS n FROM cases WHERE status = 'open'"),
        ],
    })
    s = agent.ask("open case count")
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


def test_notes_require_approval_then_run(conn):
    agent = make_agent(conn, {
        "notes": QueryPlan(intent="x", sql="SELECT COUNT(*) AS total FROM investigator_notes"),
    })
    paused = agent.ask("notes", role="siu_lead")
    assert paused.status == "awaiting_approval" and paused.rows == []
    done = agent.decide(paused.run_id, approved=True)
    assert done.status == "done" and done.rows[0][0] > 0
    # analyst role is denied by the guard before any approval question arises
    denied = agent.ask("notes", role="analyst")
    assert denied.status == "failed" and "not permitted" in denied.error


def test_groundedness_check():
    cols, rows = ["provider", "total_billed"], [["Lakeside Family Clinic", 13029.3], ["Northshore Diagnostics", 12990.41]]
    assert is_grounded("Lakeside billed $13,029.30 and Northshore $12,990.41 (2 rows).", cols, rows)[0]
    assert is_grounded("Lakeside billed about 13k.", cols, rows)[0]
    ok, bad = is_grounded("Lakeside billed 20,000.", cols, rows)
    assert not ok and bad == [20000.0]


def test_fake_script_restarts_each_run(conn):
    """Asking the same question twice must replay the full script both times."""
    agent = make_agent(conn, {
        "member details": [
            QueryPlan(intent="x", sql="SELECT member_id, mrn FROM members"),
            QueryPlan(intent="x", sql="SELECT member_id, plan FROM members"),
        ],
    })
    first, second = agent.ask("Member details"), agent.ask("Member details")
    assert first.attempts == second.attempts == 2
    assert all(any("REJECTED" in t for t in s.trace) for s in (first, second))
