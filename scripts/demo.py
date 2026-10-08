"""Walk-through demo. Runs offline by default; set ANTHROPIC_API_KEY to use Claude.

    python scripts/demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analyst.agent import AnalystAgent          # noqa: E402
from analyst.evals.run_evals import fake_playbook, load_golden  # noqa: E402
from analyst.llm import make_llm                # noqa: E402
from analyst.warehouse import build_warehouse   # noqa: E402
from docnav.baseline import compare             # noqa: E402
from docnav.document import load_default        # noqa: E402
from docnav.navigator import DocumentNavigator  # noqa: E402


def show(state):
    print(f"\n=== {state.question}  [role={state.role}]")
    for t in state.trace:
        print("   ", t)
    print(f"status: {state.status}")
    if state.sql:
        print(f"sql:    {state.sql}")
    if state.rows:
        print(f"rows:   {state.rows[:5]}{' ...' if len(state.rows) > 5 else ''}")
    print(f"answer: {state.answer}")
    if state.caveats:
        print(f"caveats: {state.caveats}")


def main() -> None:
    conn = build_warehouse()
    llm = make_llm(playbook=fake_playbook(load_golden()))
    print(f"LLM provider: {type(llm).__name__}")
    agent = AnalystAgent(conn, llm)

    show(agent.ask("How many claims were flagged by each risk rule in March 2025?"))
    show(agent.ask("How many duplicate payments were made and what was the total overpaid amount?"))
    show(agent.ask("How many open cases are there?"))                              # self-corrects after a runtime error
    show(agent.ask("List the medical record numbers of members with flagged claims."))   # guard blocks PHI column
    show(agent.ask("Show the investigator notes for open cases.", role="analyst"))

    paused = agent.ask("Show the investigator notes for open cases.", role="siu_lead")
    show(paused)
    print("\n... SIU lead approves ...")
    show(agent.decide(paused.run_id, approved=True))

    print("\n\n=== Policy navigation ===")
    nav = DocumentNavigator(load_default())
    res = nav.ask("A claim was paid twice after a portal resubmission. Can we auto-recover, and when does an investigator need to approve?")
    for c in res.tool_calls:
        print(f"   {c.name}({c.args}) -> {c.result_summary}")
    print(f"sections read: {res.sections_read}")
    print(f"answer: {res.answer.answer[:400]}")
    print(f"citations: {res.answer.citations}  confidence: {res.answer.confidence}")

    print("\n=== Flat chunks vs navigator (retrieval recall on expert-labelled questions) ===")
    rows = compare(load_default(), nav.driver)
    for r in rows:
        print(f"   baseline={r.baseline_recall:.2f}  navigator={r.navigator_recall:.2f}  {r.question[:70]}")


if __name__ == "__main__":
    main()
