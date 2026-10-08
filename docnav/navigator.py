"""Answer policy questions by iterating search -> read -> follow cross-refs, with citations.

AnthropicNavigator runs a tool loop against the API; FakeNavigator is a
deterministic reader used by tests and offline runs.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from .document import Document
from .tools import DocumentTools, ToolCall

DEFAULT_MODEL = os.environ.get("ANALYST_MODEL", "claude-opus-5-5")


class PolicyAnswer(BaseModel):
    answer: str = Field(description="Direct answer first, then the conditions. Cite sections inline like (Section 3.2).")
    citations: list[str] = Field(description="Section ids the answer relies on, e.g. ['3.1', '3.2', '2.1'].")
    confidence: str = Field(description="high | medium | low")
    evidence_gaps: list[str] = Field(default_factory=list, description="What the policy does not say that the asker should check.")


@dataclass
class NavigationResult:
    answer: PolicyAnswer
    tool_calls: list[ToolCall] = field(default_factory=list)
    sections_read: list[str] = field(default_factory=list)
    iterations: int = 0


class NavigatorDriver(Protocol):
    def run(self, question: str, tools: DocumentTools) -> tuple[PolicyAnswer, int]: ...


NAV_SYSTEM = """You answer questions about a healthcare Payment Integrity Investigation Policy.

You have three tools: outline, search, read. Work like an auditor:
1. search with specific terms from the question (amounts, nouns like "purchase order", "bank").
2. read the most relevant sections IN FULL. Never answer from a search snippet.
3. If a section you read says "see Section X" or "as defined in Section X", or is cited by a section that matters, and that section matters to the question, read it too.
4. Stop when you can answer every part of the question from text you have read. Do not read the whole document.
5. Answer with the exact amounts, conditions, and exceptions from the text, and cite section ids.
If the policy does not address something, say so in evidence_gaps instead of guessing."""


class AnthropicNavigator:
    def __init__(self, model: str = DEFAULT_MODEL, client=None, max_iterations: int = 10):
        import anthropic  # lazy, so offline paths don't need the SDK installed
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_iterations = max_iterations
        schema = PolicyAnswer.model_json_schema()
        schema["additionalProperties"] = False  # pydantic doesn't emit this, and structured output wants it
        self._format = {"type": "json_schema", "schema": schema}

    def run(self, question: str, tools: DocumentTools) -> tuple[PolicyAnswer, int]:
        # Manual tool loop rather than an SDK runner so the stop conditions are explicit.
        messages = [{"role": "user", "content": question}]
        iterations = 0
        while True:
            iterations += 1
            response = self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                system=NAV_SYSTEM,
                tools=DocumentTools.schemas(),
                messages=messages,
                output_config={"format": self._format},
            )
            if response.stop_reason == "refusal":
                detail = getattr(response, "stop_details", None)
                raise RuntimeError(f"model refusal: {getattr(detail, 'explanation', '')}")
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses or iterations >= self.max_iterations:
                # FIXME: hitting max_iterations mid tool-use leaves no final JSON text, so validation
                # fails on "". Should send one last turn without tools to force an answer.
                text = next((b.text for b in response.content if b.type == "text"), "")
                return PolicyAnswer.model_validate_json(text), iterations
            # the assistant turn must be echoed back verbatim (tool_use blocks included) before the results
            messages.append({"role": "assistant", "content": response.content})
            results = []  # all results for this turn go back in a single user message
            for tu in tool_uses:
                try:
                    out = tools.dispatch(tu.name, dict(tu.input))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out)})
                except Exception as e:  # tool errors go back to the model, never crash the loop
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(e), "is_error": True})
            messages.append({"role": "user", "content": results})


class FakeNavigator:
    """Scripted reader: read the top search hits, then follow the most relevant links.

    Deterministic, so tests can assert on exactly which sections were read.
    """

    # top_k = initial reads, follow_budget = extra link-follows, min_words filters heading-only sections
    def __init__(self, top_k: int = 3, follow_budget: int = 4, min_words: int = 10):
        self.top_k, self.follow_budget, self.min_words = top_k, follow_budget, min_words

    def run(self, question: str, tools: DocumentTools) -> tuple[PolicyAnswer, int]:
        hits = tools.search(question, k=6)  # over-fetch so skipped container headings don't eat the top_k
        read: dict[str, dict] = {}  # insertion order = reading order, which becomes citation order
        for h in hits:
            if len(read) >= self.top_k:
                break
            sec = tools.doc.get(h["section_id"])
            if sec is None or sec.word_count() < self.min_words:   # skip bare container headings
                continue
            read[h["section_id"]] = tools.read(h["section_id"])

        for _ in range(self.follow_budget):
            # explicit "see Section X" links outrank reverse links, as they would for a human reader
            frontier: dict[str, float] = {}
            for page in read.values():
                for ref in page["cross_references"]:
                    frontier[ref] = max(frontier.get(ref, 0.0), 1.0)
                for ref in page.get("cited_by", []):
                    frontier.setdefault(ref, 0.0)
            # a link only counts if the target shares terms with the question, so the +1 bonus
            # favors explicit refs but can't rescue an irrelevant section.
            # Remaining ties go to the lower id so the order is stable.
            scored = sorted(((tools.index.score(question, ref) + bonus, ref) for ref, bonus in frontier.items()
                             if ref not in read and tools.doc.get(ref) and tools.index.score(question, ref) > 0),
                            key=lambda x: (-x[0], x[1]))
            if not scored or scored[0][0] <= 0:
                break
            best = scored[0][1]
            read[best] = tools.read(best)

        if not read:
            return PolicyAnswer(answer="The policy does not appear to address this.", citations=[],
                                confidence="low", evidence_gaps=[question]), 1
        # no synthesis offline: quote the opening of each section read and let citations do the work
        lines = [f"({sid}) {sec['title']}: {sec['text'][:220].strip()}..." for sid, sec in read.items()]
        return PolicyAnswer(
            answer="Relevant policy text:\n" + "\n".join(lines),
            citations=list(read),
            confidence="high" if len(read) >= 2 else "medium",
        ), 1 + len(read)  # one search plus one "turn" per read, roughly what a model loop would take


class DocumentNavigator:
    def __init__(self, doc: Document, driver: NavigatorDriver | None = None):
        self.doc = doc
        self.driver = driver or make_driver()

    def ask(self, question: str) -> NavigationResult:
        tools = DocumentTools(self.doc)  # fresh per question so the call log and read list start empty
        answer, iterations = self.driver.run(question, tools)
        return NavigationResult(answer, tools.calls, list(dict.fromkeys(tools.read_ids)), iterations)


def make_driver(provider: str | None = None) -> NavigatorDriver:
    # same selection rules as analyst.llm.make_llm
    provider = (provider or os.environ.get("LLM_PROVIDER", "auto")).lower()
    if provider == "anthropic" or (provider == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicNavigator()
    return FakeNavigator()
