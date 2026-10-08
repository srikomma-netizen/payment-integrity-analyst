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
