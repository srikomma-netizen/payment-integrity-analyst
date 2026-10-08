"""FastAPI surface for the analyst agent and the policy navigator.

    POST /ask                    start an analyst run (may pause for approval)
    GET  /runs/{run_id}          inspect a run (status, SQL, rows, trace)
    POST /runs/{run_id}/decision resume a paused run with a human decision
    POST /policy/ask             agentic navigation over the policy document
    GET  /health

Run:  uvicorn api.main:app --reload
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from analyst.agent import AnalystAgent
from analyst.evals.run_evals import fake_playbook, load_golden
from analyst.llm import make_llm
from analyst.warehouse import build_warehouse
from analyst.workflow import RunState
from docnav.document import load_default
from docnav.navigator import DocumentNavigator

ROLES = ("analyst", "siu_lead")


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    role: str = Field(default="analyst", pattern="^(analyst|siu_lead)$")


class DecisionRequest(BaseModel):
    approved: bool
    reviewer: str = Field(default="unknown", max_length=80)


class PolicyRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def run_view(s: RunState, *, max_rows: int = 50) -> dict[str, Any]:
    return {
        "run_id": s.run_id,
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
        "attempts": s.attempts,
        "error": s.error,
        "trace": s.trace,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = build_warehouse()
    llm = make_llm(playbook=fake_playbook(load_golden()))
    app.state.agent = AnalystAgent(conn, llm)
    app.state.navigator = DocumentNavigator(load_default())
    app.state.provider = type(llm).__name__
    yield
    conn.close()


app = FastAPI(title="Payment Integrity Analyst Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health():
    return {"ok": True, "llm": app.state.provider}


@app.post("/ask")
def ask(req: AskRequest):
    state = app.state.agent.ask(req.question, role=req.role)
    return run_view(state)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    state = app.state.agent.get(run_id)
    if state is None:
        raise HTTPException(404, "run not found")
    return run_view(state)


@app.post("/runs/{run_id}/decision")
def decide(run_id: str, req: DecisionRequest):
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
