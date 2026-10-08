"""Parse a markdown policy into a section tree with ids, hierarchy and cross-references."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# "3.2 Title", "3. Title" or "Appendix A Title"; the id is "3.2" / "3" / "A"
_NUMBERED = re.compile(r"^(?:Appendix\s+)?([A-Z]|\d+(?:\.\d+)*)\.?\s+(.*)$")
# ids must be written the same way as in headings for a reference to resolve
_XREF = re.compile(r"(?:Section|Appendix)\s+([A-Z]|\d+(?:\.\d+)*)")


@dataclass
class Section:
    id: str
    title: str
    level: int
    text: str = ""
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    order: int = 0  # position in the source file; ids aren't guaranteed to sort that way

    @property
    def cross_refs(self) -> list[str]:
        # recomputed on each access; sections are small, and it keeps text the single source of truth
        return sorted({m for m in _XREF.findall(self.text) if m != self.id})

    def word_count(self) -> int:
        return len(self.text.split())


class Document:
    def __init__(self, title: str, sections: dict[str, Section]):
        self.title = title
        self.sections = sections
        self.ordered = sorted(sections.values(), key=lambda s: s.order)

    @classmethod
    def from_markdown(cls, text: str) -> "Document":
        """One pass over the lines. H1 is the document title; H2-H6 become sections."""
        title = "Untitled"
        sections: dict[str, Section] = {}
        stack: list[Section] = []
        current: Section | None = None
        body: list[str] = []
        order = 0

        # closes over `current` and `body`, so it always writes the section being built
        def flush():
            if current is not None:
                current.text = "\n".join(body).strip()

        for line in text.splitlines():
            m = _HEADING.match(line)
            if not m:
                body.append(line)
                continue
            level, heading = len(m.group(1)), m.group(2)
            if level == 1:
                title = heading
                flush()
                current, body = None, []  # text under the H1 (preamble) isn't kept anywhere
                continue
            flush()
            num = _NUMBERED.match(heading)
            # unnumbered headings get a slug id; a duplicate slug or number overwrites the earlier section
            sec_id, sec_title = (num.group(1), num.group(2)) if num else (
                re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-"), heading)
            # parent = nearest open heading with a smaller level; tolerates skipped levels (## then ####)
            while stack and stack[-1].level >= level:
                stack.pop()
            parent = stack[-1] if stack else None
            order += 1
            current = Section(sec_id, sec_title, level, parent_id=parent.id if parent else None, order=order)
            if parent:
                parent.children.append(sec_id)
            sections[sec_id] = current
            stack.append(current)
            body = []
        flush()
        return cls(title, sections)

    @classmethod
    def from_path(cls, path: str | Path) -> "Document":
        return cls.from_markdown(Path(path).read_text(encoding="utf-8"))

    def get(self, sec_id: str) -> Section | None:
        return self.sections.get(sec_id)

    def cited_by(self, sec_id: str) -> list[str]:
        """Sections whose text references this one (reverse cross-references)."""
        return [s.id for s in self.ordered if sec_id in s.cross_refs and s.id != sec_id]

    def breadcrumb(self, sec_id: str) -> list[str]:
        out, cur = [], self.get(sec_id)
        while cur:
            out.append(f"{cur.id} {cur.title}")
            cur = self.get(cur.parent_id) if cur.parent_id else None
        return list(reversed(out))

    def neighbors(self, sec_id: str) -> tuple[str | None, str | None]:
        """(previous, next) in reading order, across hierarchy levels."""
        ids = [s.id for s in self.ordered]
        i = ids.index(sec_id)  # raises ValueError for unknown ids; callers check get() first
        return (ids[i - 1] if i > 0 else None, ids[i + 1] if i + 1 < len(ids) else None)

    def outline(self) -> str:
        # H2 is the top level (H1 is the title), hence level - 2 for indentation
        return "\n".join(f"{'  ' * (s.level - 2)}{s.id} {s.title}" for s in self.ordered)


DEFAULT_POLICY = Path(__file__).with_name("data") / "payment_integrity_policy.md"


def load_default() -> Document:
    return Document.from_path(DEFAULT_POLICY)
