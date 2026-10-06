"""Flat-chunk retrieval baseline and an A/B harness.

The baseline is what a "traditional RAG" first cut looks like: slide a
fixed window over the document, index the chunks, take the top-k. It has
no idea that Section 3.2 says "as defined in Section 2.1".

`compare()` scores both approaches on *retrieval coverage*: did the
sections a human expert says you need actually get read? That is the
metric that predicts answer quality, and it needs no LLM to compute.
"""
from __future__ import annotations

from dataclasses import dataclass

from .document import Document
from .navigator import DocumentNavigator, NavigatorDriver
from .tools import BM25


@dataclass
class Chunk:
    id: str
    section_id: str
    text: str


def flat_chunks(doc: Document, window: int = 80, overlap: int = 20) -> list[Chunk]:
    chunks: list[Chunk] = []
    for s in doc.ordered:
        words = f"{s.title} {s.text}".split()
        start, n = 0, 0
        while start < len(words):
            piece = " ".join(words[start:start + window])
            chunks.append(Chunk(f"{s.id}#{n}", s.id, piece))
            n += 1
            if start + window >= len(words):
                break
            start += window - overlap
    return chunks


class FlatChunkRetriever:
    def __init__(self, doc: Document, window: int = 80, overlap: int = 20):
        self.chunks = {c.id: c for c in flat_chunks(doc, window, overlap)}
        self.index = BM25({cid: c.text for cid, c in self.chunks.items()})

    def retrieve(self, question: str, k: int = 3) -> list[Chunk]:
        return [self.chunks[cid] for cid, _ in self.index.top(question, k)]

    def sections(self, question: str, k: int = 3) -> list[str]:
        return list(dict.fromkeys(c.section_id for c in self.retrieve(question, k)))


# Expert-labelled: which sections are needed for a complete, correct answer.
RETRIEVAL_EVALS: list[dict] = [
    {"question": "Can a 45,000 USD software subscription renewal be paid without a purchase order?",
     "required": ["3.1", "3.2", "2.1"]},
    {"question": "A vendor emailed us new bank account details. What must happen before we update them?",
     "required": ["4.3", "9"]},
    {"question": "An invoice is 3 percent higher than the PO value. Can AP release it automatically?",
     "required": ["5.2", "3.1"]},
    {"question": "What is the per diem for international travel and which travel items are never reimbursed?",
     "required": ["7.1", "7.2"]},
    {"question": "An invoice for last month's services arrived after the close. How is it booked?",
     "required": ["8.2", "8.1"]},
]


@dataclass
class CompareRow:
    question: str
    required: list[str]
    baseline_sections: list[str]
    navigator_sections: list[str]
    baseline_recall: float
    navigator_recall: float


def recall(required: list[str], got: list[str]) -> float:
    return sum(1 for r in required if r in got) / len(required)


def compare(doc: Document, driver: NavigatorDriver | None = None, k: int = 3) -> list[CompareRow]:
    base = FlatChunkRetriever(doc)
    nav = DocumentNavigator(doc, driver)
    rows = []
    for case in RETRIEVAL_EVALS:
        b = base.sections(case["question"], k)
        n = nav.ask(case["question"]).sections_read
        rows.append(CompareRow(case["question"], case["required"], b, n,
                               recall(case["required"], b), recall(case["required"], n)))
    return rows


def main() -> None:
    from .document import load_default
    rows = compare(load_default())
    for r in rows:
        print(f"Q: {r.question}\n   required  {r.required}\n   baseline  {r.baseline_sections}  recall={r.baseline_recall:.2f}"
              f"\n   navigator {r.navigator_sections}  recall={r.navigator_recall:.2f}\n")
    print(f"mean recall  baseline={sum(r.baseline_recall for r in rows)/len(rows):.2f}  "
          f"navigator={sum(r.navigator_recall for r in rows)/len(rows):.2f}")


if __name__ == "__main__":
    main()
