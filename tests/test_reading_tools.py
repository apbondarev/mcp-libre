"""Tests for the phase 1 reading tools: read_paragraphs, get_outline, find_text."""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import MAX_PARAGRAPH_COUNT, MAX_TEXT_CHARS, UNOBridge  # noqa: E402

PARAGRAPHS = ["Alpha.", "Beta.", "Gamma.", "Delta.", "Epsilon."]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_reads_a_window_of_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=1, count=2, doc=doc)

    assert result["success"] is True
    assert [p["paragraph"] for p in result["paragraphs"]] == [1, 2]
    assert [p["text"] for p in result["paragraphs"]] == ["Beta.", "Gamma."]


def test_reports_the_total_paragraph_count(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=0, count=2, doc=doc)

    assert result["total_paragraphs"] == 5


def test_includes_the_paragraph_style(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0),
                     styles=["Heading 1", "Standard", "Standard", "Standard", "Quotations"])

    result = bridge.read_paragraphs(start=0, count=1, doc=doc)

    assert result["paragraphs"][0]["style"] == "Heading 1"


def test_caps_the_requested_count(bridge):
    doc = writer_doc(["p"] * (MAX_PARAGRAPH_COUNT + 50), caret=(0, 0))

    result = bridge.read_paragraphs(start=0, count=MAX_PARAGRAPH_COUNT + 50, doc=doc)

    assert len(result["paragraphs"]) == MAX_PARAGRAPH_COUNT
    assert result["total_paragraphs"] == MAX_PARAGRAPH_COUNT + 50


def test_returns_nothing_when_start_is_past_the_end(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=99, count=10, doc=doc)

    assert result["success"] is True
    assert result["paragraphs"] == []
    assert result["total_paragraphs"] == 5


def test_skips_tables_when_numbering_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0),
                     enumeration_items=[0, "table", 1, 2, 3, 4])

    result = bridge.read_paragraphs(start=1, count=1, doc=doc)

    assert result["paragraphs"][0]["text"] == "Beta."
    assert result["total_paragraphs"] == 5


def test_truncates_a_long_paragraph_but_reports_its_true_length(bridge):
    doc = writer_doc(["z" * (MAX_TEXT_CHARS + 10)], caret=(0, 0))

    entry = bridge.read_paragraphs(start=0, count=1, doc=doc)["paragraphs"][0]

    assert len(entry["text"]) == MAX_TEXT_CHARS
    assert entry["length"] == MAX_TEXT_CHARS + 10
    assert entry["truncated"] is True


def test_rejects_a_negative_start(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    result = bridge.read_paragraphs(start=-1, count=1, doc=doc)

    assert result["success"] is False
    assert "start" in result["error"]


def test_read_paragraphs_rejects_a_non_writer_document(bridge):
    result = bridge.read_paragraphs(doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_read_paragraphs_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: doc

    assert "read_paragraphs_live" in server.tools

    result = asyncio.run(server.execute_tool("read_paragraphs_live",
                                             {"start": 0, "count": 2}))

    assert result["success"] is True
    assert len(result["paragraphs"]) == 2


OUTLINE_PARAGRAPHS = ["Chapter One", "Body text here.", "Section A", "More body."]
OUTLINE_STYLES = ["Heading 1", "Standard", "Heading 2", "Standard"]


def test_lists_headings_with_their_levels(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])

    result = bridge.get_outline(doc)

    assert result["success"] is True
    assert result["headings"] == [
        {"paragraph": 0, "level": 1, "text": "Chapter One"},
        {"paragraph": 2, "level": 2, "text": "Section A"},
    ]


def test_reports_the_paragraph_count_alongside_the_outline(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])

    result = bridge.get_outline(doc)

    assert result["total_paragraphs"] == 4


def test_falls_back_to_style_names_when_outline_level_is_absent(bridge):
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     expose_outline_level=False)

    result = bridge.get_outline(doc)

    assert [h["paragraph"] for h in result["headings"]] == [0, 2]
    assert [h["level"] for h in result["headings"]] == [1, 2]


def test_returns_an_empty_outline_for_a_document_without_headings(bridge):
    doc = writer_doc(["Just body.", "More body."], caret=(0, 0))

    result = bridge.get_outline(doc)

    assert result["success"] is True
    assert result["headings"] == []


def test_caps_the_outline_and_flags_it(bridge):
    from uno_bridge import MAX_OUTLINE_ENTRIES

    count = MAX_OUTLINE_ENTRIES + 10
    doc = writer_doc([f"Heading {i}" for i in range(count)], caret=(0, 0),
                     styles=["Heading 1"] * count,
                     outline_levels=[1] * count)

    result = bridge.get_outline(doc)

    assert len(result["headings"]) == MAX_OUTLINE_ENTRIES
    assert result["truncated"] is True


def test_get_outline_rejects_a_non_writer_document(bridge):
    result = bridge.get_outline(FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_get_outline_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(OUTLINE_PARAGRAPHS, caret=(0, 0), styles=OUTLINE_STYLES,
                     outline_levels=[1, 0, 2, 0])
    server.uno_bridge.get_active_document = lambda: doc

    assert "get_outline_live" in server.tools

    result = asyncio.run(server.execute_tool("get_outline_live", {}))

    assert [h["text"] for h in result["headings"]] == ["Chapter One", "Section A"]


SEARCH_PARAGRAPHS = ["Alpha beta alpha.", "Gamma delta.", "ALPHA again."]


def test_finds_every_match_with_an_address(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("alpha", doc=doc)

    assert result["success"] is True
    assert result["total_hits"] == 3
    assert result["hits"][0]["address"] == {"paragraph": 0, "offset": 0, "length": 5}
    assert result["hits"][1]["address"] == {"paragraph": 0, "offset": 11, "length": 5}
    assert result["hits"][2]["address"] == {"paragraph": 2, "offset": 0, "length": 5}


def test_includes_the_matched_text_and_its_paragraph_as_context(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    hit = bridge.find_text("delta", doc=doc)["hits"][0]

    assert hit["matched"] == "delta"
    assert hit["context"] == "Gamma delta."
    assert hit["context_truncated"] is False


def test_honours_case_sensitivity(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("alpha", case_sensitive=True, doc=doc)

    # Only the lowercase occurrence in "Alpha beta alpha.", not "Alpha" or "ALPHA"
    assert result["total_hits"] == 1
    assert result["hits"][0]["address"] == {"paragraph": 0, "offset": 11, "length": 5}


def test_searches_by_regular_expression(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("g[a-z]+a", regex=True, doc=doc)

    assert [h["matched"] for h in result["hits"]] == ["Gamma"]


def test_caps_the_hits_but_reports_the_true_total(bridge):
    doc = writer_doc(["hit " * 40], caret=(0, 0))

    result = bridge.find_text("hit", max_results=5, doc=doc)

    assert len(result["hits"]) == 5
    assert result["total_hits"] == 40
    assert result["truncated"] is True


def test_reports_no_hits_without_failing(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("nowhere", doc=doc)

    assert result["success"] is True
    assert result["hits"] == []
    assert result["total_hits"] == 0


def test_rejects_an_empty_query(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    result = bridge.find_text("", doc=doc)

    assert result["success"] is False
    assert "query" in result["error"].lower()


def test_a_hit_address_resolves_back_to_the_matched_text(bridge):
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))

    hit = bridge.find_text("delta", doc=doc)["hits"][0]
    resolved = bridge._resolve_address(doc, hit["address"])

    assert resolved.getString() == hit["matched"]


def test_find_text_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(SEARCH_PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: doc

    assert "find_text_live" in server.tools

    result = asyncio.run(server.execute_tool("find_text_live", {"query": "delta"}))

    assert result["hits"][0]["matched"] == "delta"
