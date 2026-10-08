"""FastAPI surface for the analyst agent, the policy navigator, and the web console.

    GET  /                       web console (static, no build step)
    POST /ask                    start an analyst run (may pause for approval)
    GET  /runs                   list runs, newest first (?status=awaiting_approval for the queue)
    GET  /runs/{run_id}          inspect a run (status, SQL, rows, trace, pipeline)
    POST /runs/{run_id}/decision resume a paused run with a human decision
    POST /policy/ask             agentic navigation over the policy document
    GET  /policy/outline         section tree with cross-references and reverse references
    GET  /schema?role=           catalog as the given role sees it, plus metric definitions
    GET  /meta                   provider, roles, example questions
    POST /evals/run              run the golden suite and the retrieval A/B
    GET  /dashboard              operational KPIs, monthly trend, rule performance, providers
    GET  /cases                  investigation worklist (pseudonymous member ids only)
    GET  /cases/{case_id}        case detail: claim, flags, payments, member history
    GET  /audit                  audit trail derived from every run: PHI blocks, approvals, refusals
    GET  /health

Run:  uvicorn api.main:app --reload     then open http://localhost:8000/
"""
from __future__ import annotations

import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from analyst import catalog, insights
from analyst.agent import AnalystAgent
from analyst.evals.run_evals import fake_playbook, load_golden, run_suite, summarize
from analyst.llm import FakeAnalystLLM, make_llm
from analyst.warehouse import build_warehouse
from analyst.workflow import RunState
from docnav.baseline import compare
from docnav.document import load_default
from docnav.navigator import DocumentNavigator

ROLES = ("analyst", "siu_lead")
ROLE_PATTERN = "^(analyst|siu_lead)$"
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
PIPELINE = ("understand", "guard", "approve", "execute", "compose", "verify")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    role: str = Field(default="analyst", pattern=ROLE_PATTERN)


class DecisionRequest(BaseModel):
    approved: bool
    reviewer: str = Field(default="unknown", max_length=80)
    # In production this comes from the caller's verified identity, not the body.
    reviewer_role: str = Field(default="siu_lead", pattern=ROLE_PATTERN)


class PolicyRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


# --------------------------------------------------------------------------- #
# trace -> structured pipeline events (the UI renders these as a stepper)
# --------------------------------------------------------------------------- #
_MS = re.compile(r"in ([\d.]+) ms")


def pipeline_events(s: RunState) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    attempt = 0

    def add(node: str, outcome: str, detail: str = "") -> None:
        events.append({"node": node, "outcome": outcome, "detail": detail, "attempt": attempt})

    for line in s.trace:
        if line == "-> understand":
            attempt += 1
            add("understand", "ok")
        elif line.startswith("catalog slice:") or line.startswith("plan:"):
            if events and events[-1]["node"] == "understand":
                events[-1]["detail"] = (events[-1]["detail"] + " | " if events[-1]["detail"] else "") + line
        elif line.startswith("guard REJECTED:"):
            add("guard", "error", line.split(":", 1)[1].strip())
        elif line.startswith("guard OK:"):
            add("guard", "ok", line.split(":", 1)[1].strip())
            if "approval=required" in line:
                add("approve", "pending", "waiting for SIU-lead decision")
        elif line.startswith("human decision:"):
            decision = line.split(":", 1)[1].strip()
            for e in reversed(events):
                if e["node"] == "approve" and e["outcome"] == "pending":
                    e["outcome"], e["detail"] = ("ok" if decision == "approved" else "error"), f"human {decision}"
                    break
        elif line.startswith("execute OK:"):
            add("execute", "ok", line.split(":", 1)[1].strip())
        elif line.startswith("execute ERROR:"):
            add("execute", "error", line.split(":", 1)[1].strip())
        elif line == "-> compose":
            add("compose", "ok")
        elif line.startswith("verify:"):
            add("verify", "ok" if "UNGROUNDED" not in line else "error", line.split(":", 1)[1].strip())
        elif line.startswith("reviewer:"):
            for e in reversed(events):
                if e["node"] == "approve":
                    e["detail"] += f" by {line.split(':', 1)[1].strip()}"
                    break

    # Terminal outcomes that end the run in understand (refusal / clarification) or fail.
    if s.status in ("refused", "needs_clarification") and events:
        last_understand = next((e for e in reversed(events) if e["node"] == "understand"), None)
        if last_understand:
            last_understand["outcome"] = "stop"
            last_understand["detail"] = ("refused: " if s.status == "refused" else "clarification: ") + (s.answer or "")
    return events


def pipeline_summary(events: list[dict[str, Any]], status: str) -> dict[str, str]:
    """Final state per node for the stepper: ok | error | pending | stop | skipped."""
    out = {n: "skipped" for n in PIPELINE}
    for e in events:
        out[e["node"]] = e["outcome"]
    if status == "failed":
        for n in reversed(PIPELINE):
            if out[n] != "skipped":
                if out[n] == "ok":
                    out[n] = "error"
                break
    return out


def run_view(s: RunState, *, max_rows: int = 200) -> dict[str, Any]:
    events = pipeline_events(s)
    exec_ms = next((float(m.group(1)) for e in reversed(events) if e["node"] == "execute" and e["outcome"] == "ok"
                    for m in [_MS.search(e["detail"])] if m), None)
    return {
        "run_id": s.run_id,
        "created_at": s.created_at,
        "status": s.status,
        "question": s.question,
        "role": s.role,
        "answer": s.answer,
        "sql": s.sql,
        "tables": s.tables,
        "columns": s.columns,
        "rows": s.rows[:max_rows],
        "row_count": len(s.rows),
        "grounded": s.grounded,
        "caveats": s.caveats,
        "assumptions": (s.plan or {}).get("assumptions", []),
        "intent": (s.plan or {}).get("intent"),
        "attempts": s.attempts,
        "error": s.error,
        "trace": s.trace,
        "events": events,
        "pipeline": pipeline_summary(events, s.status),
        "execute_ms": exec_ms,
    }


def run_brief(s: RunState) -> dict[str, Any]:
    return {"run_id": s.run_id, "created_at": s.created_at, "status": s.status, "question": s.question,
            "role": s.role, "sql": s.sql, "tables": s.tables, "attempts": s.attempts}


# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = build_warehouse()
    llm = make_llm(playbook=fake_playbook(load_golden()))
    app.state.agent = AnalystAgent(conn, llm)
    app.state.navigator = DocumentNavigator(load_default())
    app.state.provider = type(llm).__name__
    app.state.offline = isinstance(llm, FakeAnalystLLM)
    yield
    conn.close()


app = FastAPI(title="Payment Integrity Analyst Agent", version="0.2.0", lifespan=lifespan)
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", include_in_schema=False)
def console():
    index = WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, "web console not built")
    return FileResponse(index)


@app.get("/health")
def health():
    return {"ok": True, "llm": app.state.provider}


@app.get("/meta")
def meta():
    seen: set[tuple[str, str]] = set()
    examples = []
    for case in load_golden():
        key = (case["question"], case.get("role", "analyst"))
        if key in seen:
            continue
        seen.add(key)
        examples.append({"question": case["question"], "role": case.get("role", "analyst"), "tags": case.get("tags", [])})
    return {"provider": app.state.provider, "offline": app.state.offline, "roles": list(ROLES),
            "pipeline": list(PIPELINE), "examples": examples}


@app.get("/schema")
def schema(role: str = Query("analyst", pattern=ROLE_PATTERN)):
    tables = []
    for t in catalog.TABLES.values():
        tables.append({
            "name": t.name, "description": t.description,
            "allowed": role in t.allowed_roles, "requires_approval": t.requires_approval,
            "allowed_roles": list(t.allowed_roles),
            "columns": [{"name": c.name, "type": c.type, "description": c.description, "restricted": c.restricted}
                        for c in t.columns],
        })
    metrics = [{"name": m.name, "definition": m.definition, "sql_hint": m.sql_hint} for m in catalog.METRICS]
    return {"role": role, "tables": tables, "metrics": metrics}


@app.post("/ask")
def ask(req: AskRequest):
    state = app.state.agent.ask(req.question, role=req.role)
    return run_view(state)


@app.get("/runs")
def list_runs(status: str | None = None, limit: int = Query(50, ge=1, le=500)):
    runs = [r for r in app.state.agent.runs() if status is None or r.status == status]
    return [run_brief(r) for r in runs[:limit]]


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    state = app.state.agent.get(run_id)
    if state is None:
        raise HTTPException(404, "run not found")
    return run_view(state)


@app.post("/runs/{run_id}/decision")
def decide(run_id: str, req: DecisionRequest):
    if req.reviewer_role != "siu_lead":
        raise HTTPException(403, "only an SIU lead can approve or reject a held query")
    try:
        state = app.state.agent.decide(run_id, approved=req.approved)
    except KeyError:
        raise HTTPException(404, "run not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    state.log(f"reviewer: {req.reviewer}")
    return run_view(state)


@app.post("/policy/ask")
def policy_ask(req: PolicyRequest):
    result = app.state.navigator.ask(req.question)
    return {
        "answer": result.answer.model_dump(),
        "sections_read": result.sections_read,
        "iterations": result.iterations,
        "tool_calls": [{"name": c.name, "args": c.args, "result": c.result_summary} for c in result.tool_calls],
    }


@app.get("/policy/outline")
def policy_outline():
    doc = app.state.navigator.doc
    return {"title": doc.title, "sections": [
        {"id": s.id, "title": s.title, "level": s.level, "parent_id": s.parent_id, "words": s.word_count(),
         "cross_refs": s.cross_refs, "cited_by": doc.cited_by(s.id)} for s in doc.ordered]}


@app.get("/policy/section/{section_id}")
def policy_section(section_id: str):
    doc = app.state.navigator.doc
    s = doc.get(section_id)
    if s is None:
        raise HTTPException(404, "section not found")
    return {"id": s.id, "title": s.title, "text": s.text, "breadcrumb": doc.breadcrumb(s.id),
            "cross_refs": s.cross_refs, "cited_by": doc.cited_by(s.id)}


@app.post("/evals/run")
def evals_run():
    """Pipeline evals always run against the deterministic fake so the button
    is free and repeatable; point the CLI at Claude to measure the model."""
    t0 = time.perf_counter()
    results = run_suite(provider="fake")
    retrieval = compare(load_default())
    return {
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
        "summary": summarize(results),
        "cases": [{"id": r.id, "tags": r.tags, "passed": r.passed, "status_ok": r.status_ok,
                   "result_match": r.result_match, "grounded": r.grounded, "attempts": r.attempts,
                   "notes": r.notes} for r in results],
        "retrieval": [{"question": r.question, "required": r.required, "baseline": r.baseline_sections,
                       "navigator": r.navigator_sections, "baseline_recall": r.baseline_recall,
                       "navigator_recall": r.navigator_recall} for r in retrieval],
    }


# --------------------------------------------------------------------------- #
# operations views (fixed, reviewed queries; never model-generated)
# --------------------------------------------------------------------------- #
@app.get("/dashboard")
def dashboard():
    data = insights.overview(app.state.agent.conn)
    data["queue"] = [run_brief(r) for r in app.state.agent.runs() if r.status == "awaiting_approval"]
    return data


@app.get("/cases")
def list_cases():
    return insights.cases(app.state.agent.conn)


@app.get("/cases/{case_id}")
def get_case(case_id: str):
    case = insights.case_detail(app.state.agent.conn, case_id)
    if case is None:
        raise HTTPException(404, "case not found")
    return case


def _audit_events(s: RunState) -> list[dict[str, Any]]:
    base = {"run_id": s.run_id, "role": s.role, "question": s.question}
    out = [{**base, "ts": s.created_at, "type": "query", "severity": "info", "detail": f"Question asked as {s.role}"}]
    for line in s.trace:
        if line.startswith("guard REJECTED:"):
            msg = line.split(":", 1)[1].strip()
            phi = "restricted column" in msg
            out.append({**base, "ts": s.created_at, "type": "phi_blocked" if phi else "guard_rejected",
                        "severity": "critical" if phi else "warning", "detail": msg})
        elif "approval=required" in line:
            out.append({**base, "ts": s.created_at, "type": "approval_requested", "severity": "warning",
                        "detail": "Query on a sensitive table held for SIU-lead approval"})
        elif line.startswith("human decision:"):
            decision = line.split(":", 1)[1].strip()
            reviewer = next((t.split(":", 1)[1].strip() for t in s.trace if t.startswith("reviewer:")), "unknown")
            out.append({**base, "ts": s.created_at, "type": f"approval_{decision}", "severity": "info" if decision == "approved" else "warning",
                        "detail": f"{decision.capitalize()} by {reviewer}"})
        elif line.startswith("verify: UNGROUNDED"):
            out.append({**base, "ts": s.created_at, "type": "ungrounded", "severity": "warning", "detail": line.split(":", 1)[1].strip()})
    if s.status == "needs_clarification":
        out.append({**base, "ts": s.created_at, "type": "clarification", "severity": "info", "detail": s.answer or "Clarification requested"})
    elif s.status == "refused":
        out.append({**base, "ts": s.created_at, "type": "refused", "severity": "warning", "detail": s.answer or "Refused"})
    elif s.status == "failed":
        out.append({**base, "ts": s.created_at, "type": "failed", "severity": "critical", "detail": s.error or "Failed"})
    elif s.status == "done":
        out.append({**base, "ts": s.created_at, "type": "answered", "severity": "info",
                    "detail": f"{len(s.rows)} rows from {', '.join(s.tables) or 'n/a'}; grounded={s.grounded}"})
    return out


@app.get("/audit")
def audit(limit: int = Query(300, ge=1, le=2000)):
    events: list[dict[str, Any]] = []
    for r in app.state.agent.runs():
        events.extend(reversed(_audit_events(r)))
    counts: dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    return {"events": events[:limit], "counts": counts}
