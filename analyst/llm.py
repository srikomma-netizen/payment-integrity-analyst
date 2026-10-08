"""LLM boundary for the analyst agent.

Two implementations behind one interface:

  * AnthropicAnalystLLM  - Claude via the official SDK, structured outputs
                           validated into Pydantic models.
  * FakeAnalystLLM       - deterministic playbook used by tests, evals, and
                           offline demos. Same interface, zero network.

Keeping the interface tiny (plan + answer) is what makes the rest of the
pipeline testable without an API key.
"""
from __future__ import annotations

import os
from typing import Protocol

from pydantic import BaseModel, Field

DEFAULT_MODEL = os.environ.get("ANALYST_MODEL", "claude-opus-5-5")


class QueryPlan(BaseModel):
    """What the model decided about the question, before anything runs."""
    intent: str = Field(description="One sentence restating the question in business terms.")
    tables: list[str] = Field(default_factory=list, description="Catalog tables the SQL uses.")
    sql: str = Field(default="", description="A single SQLite SELECT statement, or empty if not answerable.")
    assumptions: list[str] = Field(default_factory=list, description="Interpretation choices made, e.g. 'flagged = at least one risk_flags row'.")
    needs_clarification: bool = Field(default=False, description="True if the question is too ambiguous to answer safely.")
    clarification_question: str = Field(default="", description="The question to ask the user if needs_clarification.")
    refuse: bool = Field(default=False, description="True if the data is not in the catalog or not permitted for this role.")
    refuse_reason: str = Field(default="")


class AnswerDraft(BaseModel):
    answer: str = Field(description="Plain-English answer. Every number must come from the result rows.")
    caveats: list[str] = Field(default_factory=list)


class LLMRefusal(Exception):
    """Raised when the model declines (stop_reason == 'refusal')."""


class AnalystLLM(Protocol):
    def plan(self, question: str, schema_text: str, role: str, feedback: str | None = None) -> QueryPlan: ...
    def answer(self, question: str, sql: str, columns: list[str], rows: list[list]) -> AnswerDraft: ...


PLAN_SYSTEM = """You are a careful payment-integrity data analyst that writes SQL for a healthcare claims and investigations mart.

Rules:
- Use ONLY the tables and columns in the catalog below. Never invent columns.
- Write exactly one SQLite SELECT statement. No writes, no PRAGMA, no multiple statements.
- Use the business metric definitions verbatim when the question matches one.
- Periods are 'YYYY-MM' strings. "Q1" means periods 2025-01, 2025-02, 2025-03; "Q2" means 2025-04..2025-06.
- Always alias aggregate columns with readable names (e.g. flagged_claims).
- Prefer joining to providers / vendors so results show names, not just ids. Never try to identify members; member_id is the only member field you may return.
- If the question is ambiguous in a way that changes the answer (e.g. unclear time range when the data spans several periods and none is implied), set needs_clarification=true and ask ONE precise question.
- If the question needs data that is not in the catalog, or a table marked NOT AVAILABLE, set refuse=true and explain in one sentence. Do not write SQL in that case.
- Record every interpretation choice in assumptions.

CATALOG:
"""

ANSWER_SYSTEM = """You write the final answer for a payment-integrity analyst.
Use ONLY the numbers in the result rows you are given. Do not compute new numbers that are not present
(you may restate a number with rounding or commas). If the result is empty, say so plainly.
Mention the time range and any filters that were applied. Keep it under 120 words."""


class AnthropicAnalystLLM:
    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        import anthropic  # imported lazily so offline paths never need it
        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model

    def _parse(self, system: str, user: str, schema: type[BaseModel]):
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
        )
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            raise LLMRefusal(getattr(detail, "explanation", "model declined the request"))
        return response.parsed_output

    def plan(self, question: str, schema_text: str, role: str, feedback: str | None = None) -> QueryPlan:
        user = f"Role of the requesting user: {role}\n\nQuestion: {question}"
        if feedback:
            user += (
                "\n\nYour previous SQL was rejected by the validation layer. Fix it.\n"
                f"Rejection: {feedback}"
            )
        return self._parse(PLAN_SYSTEM + schema_text, user, QueryPlan)

    def answer(self, question: str, sql: str, columns: list[str], rows: list[list]) -> AnswerDraft:
        preview = rows[:50]
        user = (
            f"Question: {question}\n\nSQL that was executed:\n{sql}\n\n"
            f"Columns: {columns}\nRows ({len(rows)} total, showing {len(preview)}):\n{preview}"
        )
        return self._parse(ANSWER_SYSTEM, user, AnswerDraft)


class FakeAnalystLLM:
    """Deterministic stand-in. `playbook` maps a normalized question (optionally
    prefixed "role::") to a QueryPlan, or a list of plans to simulate
    retry-after-feedback."""

    def __init__(self, playbook: dict[str, QueryPlan | list[QueryPlan]] | None = None):
        self.playbook = {self._norm_key(k): v for k, v in (playbook or {}).items()}
        self._attempts: dict[str, int] = {}
        self.calls: list[tuple[str, str | None]] = []

    @classmethod
    def _norm_key(cls, key: str) -> str:
        if "::" in key:
            role, q = key.split("::", 1)
            return f"{role.strip()}::{cls._norm(q)}"
        return cls._norm(key)

    @staticmethod
    def _norm(q: str) -> str:
        return " ".join(q.lower().strip().rstrip("?").split())

    def plan(self, question: str, schema_text: str, role: str, feedback: str | None = None) -> QueryPlan:
        self.calls.append((question, feedback))
        # role-specific entry wins ("siu_lead::question"), then the generic one
        key = self._norm(question)
        entry = self.playbook.get(f"{role}::{key}", self.playbook.get(key))
        if entry is None:
            return QueryPlan(
                intent=question, needs_clarification=True,
                clarification_question="I don't have a confident interpretation of that question. Which metric and time range do you mean?",
            )
        if isinstance(entry, list):
            i = self._attempts.get(key, 0)
            self._attempts[key] = i + 1
            return entry[min(i, len(entry) - 1)]
        return entry

    def answer(self, question: str, sql: str, columns: list[str], rows: list[list]) -> AnswerDraft:
        if not rows:
            return AnswerDraft(answer="The query returned no rows for that question.")
        if len(rows) == 1 and len(columns) == 1:
            return AnswerDraft(answer=f"{columns[0]} = {rows[0][0]}")
        lines = [", ".join(f"{c}={v}" for c, v in zip(columns, r)) for r in rows[:10]]
        more = f" (+{len(rows) - 10} more rows)" if len(rows) > 10 else ""
        return AnswerDraft(answer=f"{len(rows)} rows. " + " | ".join(lines) + more)


def make_llm(provider: str | None = None, playbook=None) -> AnalystLLM:
    provider = (provider or os.environ.get("LLM_PROVIDER", "auto")).lower()
    if provider == "fake":
        return FakeAnalystLLM(playbook)
    if provider == "anthropic" or (provider == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicAnalystLLM()
    return FakeAnalystLLM(playbook)
