"""Document navigation tools (outline, search, read) and a small BM25 index.

Every call is recorded in `calls` so the UI can show the agent's path.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from .document import Document, Section

_TOKEN = re.compile(r"[a-z0-9]+")
# short list on purpose: words like "not", "must", "may" carry meaning in policy text
_STOP = {"the", "a", "an", "and", "or", "of", "to", "is", "in", "for", "on", "by", "with",
         "are", "be", "as", "at", "it", "this", "that", "we", "do", "can", "what", "any"}


def tokenize(text: str) -> list[str]:
    # no stemming, so "payment" and "payments" are different terms
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


class BM25:
    """Okapi BM25 over a handful of sections. Brute-force scoring; no inverted index needed at this size."""

    # k1 = term-frequency saturation, b = length normalization; the usual defaults
    def __init__(self, docs: dict[str, str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.ids = list(docs)
        self.tf = {i: Counter(tokenize(t)) for i, t in docs.items()}
        self.len = {i: sum(c.values()) for i, c in self.tf.items()}
        # empty corpus: fall back to 1.0 so score() never divides by zero
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf.values():
            df.update(c.keys())  # keys only: document frequency, not term frequency
        n = len(docs)
        # the "1 +" (Lucene-style) keeps idf positive even for terms in more than half the sections
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def score(self, query: str, doc_id: str) -> float:
        s, tf, dl = 0.0, self.tf[doc_id], self.len[doc_id]
        # a repeated query term counts once per repetition, same as most BM25 implementations
        for q in tokenize(query):
            if q not in tf:
                continue
            f = tf[q]
            s += self.idf[q] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
        return s

    def top(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        scored = [(i, self.score(query, i)) for i in self.ids]
        scored = [(i, s) for i, s in scored if s > 0]  # zero = no shared terms; not worth returning
        scored.sort(key=lambda x: -x[1])  # stable, so ties stay in document order
        return scored[:k]


@dataclass
class ToolCall:
    name: str
    args: dict
    result_summary: str


class DocumentTools:
    def __init__(self, doc: Document):
        self.doc = doc
        # titles are indexed with the body; they're often the most specific words in a section
        self.index = BM25({s.id: f"{s.title}\n{s.text}" for s in doc.ordered})
        self.calls: list[ToolCall] = []
        self.read_ids: list[str] = []  # read order with repeats; the navigator dedupes it into sections_read

    # -- tools ---------------------------------------------------------
    def outline(self) -> str:
        out = self.doc.outline()
        self.calls.append(ToolCall("outline", {}, f"{len(self.doc.sections)} sections"))
        return out

    def search(self, query: str, k: int = 5) -> list[dict]:
        hits = []
        for sec_id, score in self.index.top(query, k):
            s = self.doc.get(sec_id)
            hits.append({"section_id": sec_id, "title": s.title, "score": round(score, 3),
                         "snippet": s.text[:160].replace("\n", " ")})
        self.calls.append(ToolCall("search", {"query": query, "k": k}, ", ".join(h["section_id"] for h in hits)))
        return hits

    def read(self, section_id: str) -> dict:
        s: Section | None = self.doc.get(section_id)
        if s is None:
            self.calls.append(ToolCall("read", {"section_id": section_id}, "NOT FOUND"))
            # returned as data, not raised, so the model can recover by calling outline()
            return {"error": f"no section '{section_id}'. Use outline() to list valid ids."}
        prev_id, next_id = self.doc.neighbors(section_id)
        self.read_ids.append(section_id)
        self.calls.append(ToolCall("read", {"section_id": section_id}, f"{s.word_count()} words, xrefs={s.cross_refs}"))
        return {
            "section_id": s.id,
            "title": s.title,
            "breadcrumb": " > ".join(self.doc.breadcrumb(s.id)),
            "text": s.text,
            # the point of read(): the agent can follow "see Section 2.1" instead of hoping search finds it
            "cross_references": s.cross_refs,
            "cited_by": self.doc.cited_by(s.id),
            "children": s.children,
            "previous": prev_id,
            "next": next_id,
        }

    # -- JSON schemas for the model (strict) -----------------------------
    # descriptions are the only guidance the model gets on when to use each tool, so they carry the policy
    @staticmethod
    def schemas() -> list[dict]:
        return [
            {
                "name": "outline",
                "description": "List every section id and title in the policy document. Cheap; call first if unsure where to look.",
                "strict": True,
                "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            },
            {
                "name": "search",
                "description": "Lexical search over sections. Returns candidate section ids with a short snippet. Use specific terms (amounts, nouns).",
                "strict": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}, "k": {"type": "integer", "minimum": 1, "maximum": 10}},
                    "required": ["query", "k"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "read",
                "description": "Read one section in full with its breadcrumb, neighbours, cross-references, and the sections that cite it. Follow links that matter to the question.",
                "strict": True,
                "input_schema": {
                    "type": "object",
                    "properties": {"section_id": {"type": "string"}},
                    "required": ["section_id"],
                    "additionalProperties": False,
                },
            },
        ]

    def dispatch(self, name: str, args: dict):
        if name == "outline":
            return self.outline()
        if name == "search":
            # the schema requires k, but be lenient about type and absence here
            return self.search(args["query"], int(args.get("k", 5)))
        if name == "read":
            return self.read(args["section_id"])
        raise KeyError(name)  # unknown tool name: a bug on our side, not something to feed back to the model
