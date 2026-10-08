from docnav.baseline import FlatChunkRetriever, compare
from docnav.document import load_default
from docnav.navigator import DocumentNavigator, FakeNavigator
from docnav.tools import DocumentTools


def test_parse_sections_hierarchy_and_xrefs():
    doc = load_default()
    assert doc.get("3.2").parent_id == "3"
    assert "3.2" in doc.get("3").children
    assert set(doc.get("5.2").cross_refs) == {"2.1", "6", "8.1"}
    assert doc.breadcrumb("4.1")[0].startswith("4 ")
    assert doc.get("A").title.startswith("Recognized Panel Pairs")


def test_tools_search_and_read():
    tools = DocumentTools(load_default())
    hits = tools.search("bank detail change call-back", k=3)
    assert hits[0]["section_id"] == "4.1"
    page = tools.read("4.1")
    assert "call-back" in page["text"] and "6" in page["cross_references"]
    assert tools.read("nope").get("error")
    assert [c.name for c in tools.calls] == ["search", "read", "read"]


def test_navigator_follows_cross_references():
    nav = DocumentNavigator(load_default(), FakeNavigator())
    res = nav.ask("A vendor emailed new bank details six days before a 48,000 USD invoice. What must happen before payment?")
    assert "4.1" in res.sections_read and "6" in res.sections_read
    assert set(res.answer.citations) == set(res.sections_read)


def test_navigator_beats_flat_chunks_on_cross_ref_questions():
    rows = compare(load_default(), FakeNavigator())
    mean_b = sum(r.baseline_recall for r in rows) / len(rows)
    mean_n = sum(r.navigator_recall for r in rows) / len(rows)
    assert mean_n >= mean_b
    assert all(r.navigator_recall >= r.baseline_recall for r in rows)


def test_flat_chunks_cover_document():
    doc = load_default()
    retr = FlatChunkRetriever(doc)
    assert {c.section_id for c in retr.chunks.values()} == set(doc.sections)
