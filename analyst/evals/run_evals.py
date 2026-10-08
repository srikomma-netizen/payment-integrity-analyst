"""Evals over golden.json.

Run:  python -m analyst.evals.run_evals
      LLM_PROVIDER=anthropic python -m analyst.evals.run_evals
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..agent import AnalystAgent
from ..llm import QueryPlan, make_llm
from ..warehouse import build_warehouse
from ..workflow import RunState

GOLDEN_PATH = Path(__file__).with_name("golden.json")


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def fake_playbook(cases: list[dict]) -> dict[str, QueryPlan | list[QueryPlan]]:
    book: dict[str, QueryPlan | list[QueryPlan]] = {}
    for c in cases:
        plans = [QueryPlan(**p) for p in c.get("fake_plans", [])]
        if not plans:
            continue
        key = f"{c.get('role', 'analyst')}::{c['question']}"
        book[key] = plans if len(plans) > 1 else plans[0]
    return book


def _normalize_cell(v):
    if isinstance(v, float):
        return round(v, 4)
    return v


def rows_equal(expected: list[list], actual: list[list], *, ordered: bool, tol: float = 1e-4) -> bool:
    """Compare result sets by value. Column names ignored, order not."""
    if len(expected) != len(actual):
        return False
    if expected and len(expected[0]) != len(actual[0]):
        return False

    def norm(rows):
        return [tuple(_normalize_cell(v) for v in r) for r in rows]

    e, a = norm(expected), norm(actual)
    if not ordered:
        e, a = sorted(e, key=repr), sorted(a, key=repr)
    for er, ar in zip(e, a):
        for ev, av in zip(er, ar):
            if isinstance(ev, (int, float)) and isinstance(av, (int, float)):
                if not math.isclose(ev, av, rel_tol=tol, abs_tol=tol):
                    return False
            elif ev != av:
                return False
    return True


@dataclass
class CaseResult:
    id: str
    tags: list[str]
    passed: bool
    status_ok: bool
    result_match: bool | None
    grounded: bool | None
    attempts: int
    notes: list[str] = field(default_factory=list)


def evaluate_case(agent: AnalystAgent, conn, case: dict) -> CaseResult:
    exp = case["expect"]
    notes: list[str] = []
    state: RunState = agent.ask(case["question"], role=case.get("role", "analyst"))

    status_ok = state.status == exp["status"]
    if not status_ok:
        notes.append(f"status={state.status} expected={exp['status']} error={state.error}")

    if exp.get("then_approve") and state.status == "awaiting_approval":
        state = agent.decide(state.run_id, approved=True)
        if state.status != "done":
            status_ok = False
            notes.append(f"after approval status={state.status} error={state.error}")

    if "min_attempts" in exp and state.attempts < exp["min_attempts"]:
        status_ok = False
        notes.append(f"attempts={state.attempts} < {exp['min_attempts']}")
    if "min_guard_rejections" in exp:
        rejections = sum(1 for t in state.trace if "guard REJECTED" in t)
        if rejections < exp["min_guard_rejections"]:
            status_ok = False
            notes.append(f"guard rejections={rejections}")
        if state.rows:  # blocked question leaked rows
            status_ok = False
            notes.append("rows were returned for a blocked question")

    result_match: bool | None = None
    if "reference_sql" in exp and state.status == "done":
        # computed live so a seed change doesn't break golden.json
        ref_rows = [list(r) for r in conn.execute(exp["reference_sql"]).fetchall()]
        result_match = rows_equal(ref_rows, state.rows, ordered=exp.get("ordered", False))
        if not result_match:
            notes.append(f"result mismatch: expected {ref_rows[:3]}... got {state.rows[:3]}...")

    grounded = state.grounded if state.status == "done" else None
    if grounded is False:
        notes.append(f"ungrounded: {state.caveats}")

    passed = status_ok and result_match is not False and grounded is not False  # None = n/a
    return CaseResult(case["id"], case.get("tags", []), passed, status_ok, result_match, grounded, state.attempts, notes)


def run_suite(cases: list[dict] | None = None, *, provider: str | None = None) -> list[CaseResult]:
    cases = cases or load_golden()
    conn = build_warehouse()
    llm = make_llm(provider, playbook=fake_playbook(cases))
    agent = AnalystAgent(conn, llm)
    return [evaluate_case(agent, conn, c) for c in cases]


def summarize(results: list[CaseResult]) -> dict:
    by_tag: Counter = Counter()
    tag_total: Counter = Counter()
    for r in results:
        for t in r.tags:
            tag_total[t] += 1
            by_tag[t] += int(r.passed)
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "result_match_rate": _rate([r.result_match for r in results]),
        "grounded_rate": _rate([r.grounded for r in results]),
        "by_tag": {t: f"{by_tag[t]}/{tag_total[t]}" for t in sorted(tag_total)},
    }


def _rate(vals: list[bool | None]) -> str:
    vals = [v for v in vals if v is not None]
    return f"{sum(vals)}/{len(vals)}" if vals else "n/a"


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    provider = argv[0] if argv else None  # overrides LLM_PROVIDER
    results = run_suite(provider=provider)
    width = max(len(r.id) for r in results) + 2
    print(f"{'case'.ljust(width)}pass  status  match  grounded  attempts")
    for r in results:
        print(f"{r.id.ljust(width)}{'PASS' if r.passed else 'FAIL'}  "
              f"{str(r.status_ok).ljust(6)}  {str(r.result_match).ljust(5)}  "
              f"{str(r.grounded).ljust(8)}  {r.attempts}")
        for n in r.notes:
            print(f"{''.ljust(width)}  - {n}")
    print()
    print(json.dumps(summarize(results), indent=2))
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
