from docnav.baseline import FlatChunkRetriever, compare
from docnav.document import load_default
from docnav.navigator import DocumentNavigator, FakeNavigator
from docnav.tools import DocumentTools


def test_parse_sections_hierarchy_and_xrefs():
    doc = load_default()
    assert doc.get("3.2").parent_id == "3"
    assert "3.2" in doc.get("3").children
    assert set(doc.get("3.2").cross_refs) == {"2.1", "3.1"}
    assert doc.breadcrumb("4.3")[0].startswith("4 ")


def test_tools_search_and_read():
    tools = DocumentTools(load_default())
    hits = tools.search("bank account change call-back", k=3)
    assert hits[0]["section_id"] == "4.3"
    page = tools.read("4.3")
    assert "call-back" in page["text"] and "9" in page["cross_references"]
    assert tools.read("nope").get("error")
    assert [c.name for c in tools.calls] == ["search", "read", "read"]


def test_navigator_follows_cross_references():
    nav = DocumentNavigator(load_default(), FakeNavigator())
    res = nav.ask("A vendor emailed us new bank account details. What must happen before we update them?")
    assert "4.3" in res.sections_read and "9" in res.sections_read
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
