"""Flat-chunk retrieval baseline and a recall comparison against the navigator.

Run:  python -m docnav.baseline
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
    """Fixed word windows with overlap, the usual first-cut RAG chunking. Sizes are in words."""
    chunks: list[Chunk] = []
    # chunks never span a section boundary, which is slightly kinder to the baseline than
    # chunking the raw file; each chunk still maps back to exactly one section for scoring
    for s in doc.ordered:
        words = f"{s.title} {s.text}".split()
        start, n = 0, 0
        while start < len(words):
            piece = " ".join(words[start:start + window])
            chunks.append(Chunk(f"{s.id}#{n}", s.id, piece))
            n += 1
            if start + window >= len(words):  # last window reached the end; don't emit a tail of pure overlap
                break
            start += window - overlap  # requires overlap < window or this never advances
    return chunks


class FlatChunkRetriever:
    def __init__(self, doc: Document, window: int = 80, overlap: int = 20):
        self.chunks = {c.id: c for c in flat_chunks(doc, window, overlap)}
        self.index = BM25({cid: c.text for cid, c in self.chunks.items()})

    def retrieve(self, question: str, k: int = 3) -> list[Chunk]:
        return [self.chunks[cid] for cid, _ in self.index.top(question, k)]

    def sections(self, question: str, k: int = 3) -> list[str]:
        # k counts chunks, so two hits in one section leave fewer distinct sections
        return list(dict.fromkeys(c.section_id for c in self.retrieve(question, k)))


# Expert-labelled: which sections are needed for a complete, correct answer.
# Most of these hinge on a cross-reference (e.g. a definition in Section 2.x) that flat chunks miss.
RETRIEVAL_EVALS: list[dict] = [
    {"question": "A claim was paid twice after a portal resubmission. Can we auto-recover, and when does an investigator need to approve?",
     "required": ["5.2", "8.1", "2.1"]},
    {"question": "A vendor emailed new bank details six days before a 48,000 USD invoice. What must happen before payment?",
     "required": ["4.1", "4.2", "6"]},
    {"question": "A provider billed 99215 at three times the peer median and has a prior confirmed case. What severity and who reviews it?",
     "required": ["2.2", "3.1", "3.2"]},
    {"question": "Which member fields may an analyst see, and what can be sent to the model?",
     "required": ["7.1", "7.2"]},
    {"question": "Two panel component codes were billed separately on the same day. What is the disposition?",
     "required": ["2.3", "5.3", "A"]},
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
    # coverage of required sections only; extra reads aren't penalized, so this says nothing about cost
    return sum(1 for r in required if r in got) / len(required)


def compare(doc: Document, driver: NavigatorDriver | None = None, k: int = 3) -> list[CompareRow]:
    """Section recall per labelled question, baseline vs navigator. No LLM needed with the fake driver."""
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
