import pytest
from fastapi.testclient import TestClient

from api.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ask_happy_path(client):
    r = client.post("/ask", json={"question": "What share of paid claims were flagged in Q2 2025?"})
    body = r.json()
    assert r.status_code == 200 and body["status"] == "done"
    assert body["sql"].upper().startswith("SELECT") and body["grounded"] is True
    assert client.get(f"/runs/{body['run_id']}").json()["status"] == "done"


def test_ask_rejects_bad_role(client):
    r = client.post("/ask", json={"question": "anything", "role": "admin"})
    assert r.status_code == 422


def test_phi_question_is_refused(client):
    body = client.post("/ask", json={"question": "List the medical record numbers of members with flagged claims."}).json()
    assert body["status"] == "refused" and body["rows"] == []
    assert any("REJECTED" in t for t in body["trace"])


def test_approval_round_trip(client):
    q = "Show the investigator notes for open cases."
    paused = client.post("/ask", json={"question": q, "role": "siu_lead"}).json()
    assert paused["status"] == "awaiting_approval" and paused["rows"] == []
    done = client.post(f"/runs/{paused['run_id']}/decision", json={"approved": True, "reviewer": "lead_2"}).json()
    assert done["status"] == "done" and done["row_count"] > 0
    again = client.post(f"/runs/{paused['run_id']}/decision", json={"approved": True})
    assert again.status_code == 409


def test_policy_ask(client):
    r = client.post("/policy/ask", json={"question": "What must happen before vendor bank details are changed?"})
    body = r.json()
    assert r.status_code == 200 and "4.1" in body["sections_read"]
    assert body["answer"]["citations"]


def test_console_and_static_assets_served(client):
    assert "Payment Integrity Analyst" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200


def test_pipeline_events_show_retry_and_guard_rejection(client):
    retry = client.post("/ask", json={"question": "How many open cases are there?"}).json()
    nodes = [(e["attempt"], e["node"], e["outcome"]) for e in retry["events"]]
    assert (1, "execute", "error") in nodes and (2, "execute", "ok") in nodes
    assert retry["pipeline"]["verify"] == "ok" and retry["execute_ms"] is not None

    phi = client.post("/ask", json={"question": "List the medical record numbers of members with flagged claims."}).json()
    assert phi["pipeline"]["guard"] == "error" and phi["pipeline"]["understand"] == "stop"
    assert phi["pipeline"]["execute"] == "skipped"


def test_queue_listing_and_role_enforced_decision(client):
    q = "Show the investigator notes for open cases."
    paused = client.post("/ask", json={"question": q, "role": "siu_lead"}).json()
    assert paused["pipeline"]["approve"] == "pending"
    queue = client.get("/runs", params={"status": "awaiting_approval"}).json()
    assert paused["run_id"] in [r["run_id"] for r in queue]
    denied = client.post(f"/runs/{paused['run_id']}/decision", json={"approved": True, "reviewer_role": "analyst"})
    assert denied.status_code == 403
    done = client.post(f"/runs/{paused['run_id']}/decision", json={"approved": True, "reviewer": "lead_2", "reviewer_role": "siu_lead"}).json()
    assert done["pipeline"]["approve"] == "ok" and done["status"] == "done"
    assert paused["run_id"] not in [r["run_id"] for r in client.get("/runs", params={"status": "awaiting_approval"}).json()]


def test_schema_meta_outline_and_evals(client):
    analyst = client.get("/schema", params={"role": "analyst"}).json()
    notes = next(t for t in analyst["tables"] if t["name"] == "investigator_notes")
    assert notes["allowed"] is False and notes["requires_approval"] is True
    members = next(t for t in analyst["tables"] if t["name"] == "members")
    assert {c["name"] for c in members["columns"] if c["restricted"]} == {"mrn", "dob"}
    assert client.get("/schema", params={"role": "admin"}).status_code == 422

    meta = client.get("/meta").json()
    assert meta["offline"] is True and len(meta["examples"]) >= 10

    outline = client.get("/policy/outline").json()
    s23 = next(s for s in outline["sections"] if s["id"] == "2.3")
    assert "5.3" in s23["cited_by"]
    assert client.get("/policy/section/9.9").status_code == 404

    ev = client.post("/evals/run").json()
    assert ev["summary"]["passed"] == ev["summary"]["cases"]
    assert all(r["navigator_recall"] >= r["baseline_recall"] for r in ev["retrieval"])
