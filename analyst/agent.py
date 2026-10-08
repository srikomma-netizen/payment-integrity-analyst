"""The analyst agent: question -> SQL -> guard -> execute -> grounded answer.

The model picks what to query; the guard decides whether it may run, and
verify checks the answer only cites numbers from the result.
"""
from __future__ import annotations

import re
import sqlite3

from . import catalog
from .guard import guard_sql
from .llm import AnalystLLM, LLMRefusal
from .warehouse import QueryTimeout, execute_readonly
from .workflow import END, CheckpointStore, Graph, RunState

MAX_SQL_ATTEMPTS = 3  # counts planner calls, so the first try plus two retries
# allows thousands separators; dates like 2025-03 come out as 2025 and -3
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _numbers_in(text: str) -> list[float]:
    out = []
    for tok in _NUM.findall(text):
        tok = tok.replace(",", "")
        try:
            out.append(float(tok))
        except ValueError:  # defensive; the regex shouldn't produce unparseable tokens
            pass
    return out


def _result_numbers(columns: list[str], rows: list[list]) -> set[float]:
    """Every number a faithful answer could cite: cells, numbers inside string cells, and the row count."""
    allowed: set[float] = {float(len(rows))}  # "12 providers" is a legitimate number to cite
    for row in rows:
        for v in row:
            # bool is an int subclass, so True/False land here as 1.0/0.0; harmless
            if isinstance(v, (int, float)) and v is not None:
                # pre-rounded variants so "0.27" matches a cell of 0.2683 without fuzzy matching
                allowed.update({float(v), round(float(v)), round(float(v), 1), round(float(v), 2)})
            elif isinstance(v, str):
                # periods and ids ("2025-03", "PRV-503") carry digits the answer may repeat
                allowed.update(_numbers_in(v))
    return allowed


def is_grounded(answer: str, columns: list[str], rows: list[list]) -> tuple[bool, list[float]]:
    """Check every number in `answer` against the result set.

    Returns (ok, unsupported_numbers). Rounding and k/M abbreviations are tolerated.
    """
    allowed = _result_numbers(columns, rows)
    unsupported = []
    for n in _numbers_in(answer):
        if n in allowed:
            continue
        # the 1.0 floor absorbs whole-number rounding of small values; 0.5% relative covers big sums.
        # NOTE: a percent restated from a fraction cell (27% vs 0.27) won't match and gets flagged.
        if any(abs(n - a) <= max(1.0, abs(a) * 0.005) for a in allowed):   # rounding tolerance
            continue
        # scale the answer number up rather than the cells down, so "96.6k" compares in raw units
        if any(abs(n * k - a) <= abs(a) * 0.005 for a in allowed for k in (1_000, 1_000_000)):  # "96.6k"
            continue
        unsupported.append(n)
    return (not unsupported), unsupported


class AnalystAgent:
    def __init__(self, conn: sqlite3.Connection, llm: AnalystLLM, store: CheckpointStore | None = None):
        self.conn = conn
        self.llm = llm
        self.store = store or CheckpointStore()
        self.graph = self._build()

    # ---- nodes -------------------------------------------------------
    def understand(self, s: RunState) -> RunState:
        s.attempts += 1  # the only place attempts moves; guard and execute just read it
        # rebuilt every attempt; cheap, and keeps the node free of cached state
        cslice = catalog.build_slice(s.question, s.role)
        s.log(f"catalog slice: {sorted(cslice.table_names)}; metrics: {[m.name for m in cslice.metrics]}")
        try:
            plan = self.llm.plan(s.question, cslice.schema_text, s.role, feedback=s.feedback)
        except LLMRefusal as e:
            s.status, s.error = "failed", f"model refusal: {e}"
            return s
        s.plan = plan.model_dump()
        s.feedback = None  # consumed; leaving it set would loop the routers back here
        if plan.refuse:
            s.status, s.answer = "refused", plan.refuse_reason or "That data is not available."
        elif plan.needs_clarification:
            s.status, s.answer = "needs_clarification", plan.clarification_question
        else:
            s.sql = plan.sql
            s.log(f"plan: {plan.intent} | assumptions={plan.assumptions}")
        return s

    def guard(self, s: RunState) -> RunState:
        result = guard_sql(s.sql or "", role=s.role)
        if not result.ok:
            # violations go back to the model verbatim so the retry can fix the specific problem
            s.feedback = result.message
            s.log(f"guard REJECTED: {result.message}")
            if s.attempts >= MAX_SQL_ATTEMPTS:
                s.status, s.error = "failed", f"SQL rejected after {s.attempts} attempts: {result.message}"
            return s
        s.sql = result.sql  # guard returns the rewritten SQL; execute that, never the model's string
        s.tables = sorted(result.tables)
        s.log(f"guard OK: tables={s.tables} approval={'required' if result.requires_approval else 'no'}")
        # approved is None only before a decision; after resume() it's True and we fall through
        if result.requires_approval and s.approved is None:
            s.status = "awaiting_approval"
        return s

    def execute(self, s: RunState) -> RunState:
        try:
            res = execute_readonly(self.conn, s.sql or "")
        except QueryTimeout as e:
            # not retried: a rewrite of a slow query is likely just as slow
            s.status, s.error = "failed", str(e)
            return s
        except sqlite3.Error as e:
            # A runtime SQL error is feedback for the model, same as a guard rejection.
            s.feedback = f"SQLite error: {e}"
            s.log(f"execute ERROR: {e}")
            if s.attempts >= MAX_SQL_ATTEMPTS:
                s.status, s.error = "failed", f"SQL failed after {s.attempts} attempts: {e}"
            return s
        s.columns, s.rows = res.columns, res.rows
        s.log(f"execute OK: {res.row_count} rows in {res.elapsed_ms:.1f} ms{' (truncated)' if res.truncated else ''}")
        return s

    def compose(self, s: RunState) -> RunState:
        try:
            draft = self.llm.answer(s.question, s.sql or "", s.columns, s.rows)
        except LLMRefusal as e:
            s.status, s.error = "failed", f"model refusal: {e}"
            return s
        s.answer, s.caveats = draft.answer, draft.caveats
        return s

    def verify(self, s: RunState) -> RunState:
        ok, unsupported = is_grounded(s.answer or "", s.columns, s.rows)
        s.grounded = ok
        # an ungrounded answer is still returned, with a caveat; the UI shows the flag
        if not ok:
            s.caveats.append(f"Unverified numbers in answer: {unsupported}")
            s.log(f"verify: UNGROUNDED {unsupported}")
        else:
            s.log("verify: grounded")
        return s

    # ---- routing -----------------------------------------------------
    # Routers only read status and feedback. Order matters: check "failed" before
    # feedback, because the last rejected attempt sets both.
    @staticmethod
    def _after_understand(s: RunState) -> str:
        return END if s.status != "running" else "guard"

    @staticmethod
    def _after_guard(s: RunState) -> str:
        if s.status == "failed":
            return END
        if s.feedback:
            return "understand"
        return "execute"  # also the resume point after approval

    @staticmethod
    def _after_execute(s: RunState) -> str:
        if s.status == "failed":
            return END
        return "understand" if s.feedback else "compose"

    def _build(self) -> Graph:
        #   understand -> guard -> (approval?) -> execute -> compose -> verify -> END
        #   guard / execute with feedback -> understand (until MAX_SQL_ATTEMPTS)
        #   understand with refuse / needs_clarification -> END
        g = Graph(entry="understand")
        g.add_node("understand", self.understand).add_edge("understand", self._after_understand)
        g.add_node("guard", self.guard).add_edge("guard", self._after_guard)
        g.add_node("execute", self.execute).add_edge("execute", self._after_execute)
        g.add_node("compose", self.compose).add_edge("compose", "verify")
        g.add_node("verify", self.verify).add_edge("verify", END)
        return g

    # ---- public API --------------------------------------------------
    def ask(self, question: str, role: str = "analyst") -> RunState:
        return self.graph.run(RunState(question=question, role=role), self.store)

    def decide(self, run_id: str, approved: bool) -> RunState:
        # same KeyError / ValueError contract as Graph.resume; the API maps them to 404 / 409
        return self.graph.resume(run_id, self.store, approved=approved)

    def get(self, run_id: str) -> RunState | None:
        return self.store.get(run_id)

    def runs(self) -> list[RunState]:
        return self.store.all()
