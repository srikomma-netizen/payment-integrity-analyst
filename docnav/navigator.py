"""Agentic document navigation ("agentic RAG").

Instead of retrieving N chunks once and hoping they contain the answer,
the agent iterates: search -> read -> follow cross-references -> decide
whether evidence is sufficient -> answer with citations.

Two drivers share one interface:
  AnthropicNavigator  Claude with the three document tools (manual tool
                      loop, so the control flow is explicit), and a JSON
                      schema on the final turn so the answer is structured.
  FakeNavigator       deterministic navigation policy for tests and
                      offline demos: same tools, same output type.
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


NAV_SYSTEM = """You answer questions about a corporate Expense and Vendor Payment Policy.

You have three tools: outline, search, read. Work like an auditor:
1. search with specific terms from the question (amounts, nouns like "purchase order", "bank").
2. read the most relevant sections IN FULL. Never answer from a search snippet.
3. If a section you read says "see Section X" or "as defined in Section X" and X matters to the question, read X too.
4. Stop when you can answer every part of the question from text you have read. Do not read the whole document.
5. Answer with the exact amounts, conditions, and exceptions from the text, and cite section ids.
If the policy does not address something, say so in evidence_gaps instead of guessing."""


class AnthropicNavigator:
    def __init__(self, model: str = DEFAULT_MODEL, client=None, max_iterations: int = 10):
        import anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.max_iterations = max_iterations
        schema = PolicyAnswer.model_json_schema()
        schema["additionalProperties"] = False
        self._format = {"type": "json_schema", "schema": schema}

    def run(self, question: str, tools: DocumentTools) -> tuple[PolicyAnswer, int]:
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
                text = next((b.text for b in response.content if b.type == "text"), "")
                return PolicyAnswer.model_validate_json(text), iterations
            messages.append({"role": "assistant", "content": response.content})
            results = []
            for tu in tool_uses:
                try:
                    out = tools.dispatch(tu.name, dict(tu.input))
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(out)})
                except Exception as e:  # tool errors go back to the model, never crash the loop
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(e), "is_error": True})
            messages.append({"role": "user", "content": results})


class FakeNavigator:
    """A scripted navigation policy: search, read top hits, follow
    cross-references one hop, answer from what was read. Deterministic, so
    tests can assert on which sections were consulted."""

    def __init__(self, top_k: int = 3, follow_refs: int = 3, min_words: int = 10):
        self.top_k, self.follow_refs, self.min_words = top_k, follow_refs, min_words

    def run(self, question: str, tools: DocumentTools) -> tuple[PolicyAnswer, int]:
        hits = tools.search(question, k=6)
        read: dict[str, dict] = {}
        for h in hits:
            if len(read) >= self.top_k:
                break
            sec = tools.doc.get(h["section_id"])
            if sec is None or sec.word_count() < self.min_words:   # skip bare container headings
                continue
            read[h["section_id"]] = tools.read(h["section_id"])
        followed = 0
        for sec in list(read.values()):
            for ref in sec["cross_references"]:
                if ref not in read and followed < self.follow_refs and tools.doc.get(ref):
                    read[ref] = tools.read(ref)
                    followed += 1
        if not read:
            return PolicyAnswer(answer="The policy does not appear to address this.", citations=[],
                                confidence="low", evidence_gaps=[question]), 1
        lines = [f"({sid}) {sec['title']}: {sec['text'][:220].strip()}..." for sid, sec in read.items()]
        return PolicyAnswer(
            answer="Relevant policy text:\n" + "\n".join(lines),
            citations=list(read),
            confidence="high" if len(read) >= 2 else "medium",
        ), 1 + len(read)


class DocumentNavigator:
    def __init__(self, doc: Document, driver: NavigatorDriver | None = None):
        self.doc = doc
        self.driver = driver or make_driver()

    def ask(self, question: str) -> NavigationResult:
        tools = DocumentTools(self.doc)
        answer, iterations = self.driver.run(question, tools)
        return NavigationResult(answer, tools.calls, list(dict.fromkeys(tools.read_ids)), iterations)


def make_driver(provider: str | None = None) -> NavigatorDriver:
    provider = (provider or os.environ.get("LLM_PROVIDER", "auto")).lower()
    if provider == "anthropic" or (provider == "auto" and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicNavigator()
    return FakeNavigator()
