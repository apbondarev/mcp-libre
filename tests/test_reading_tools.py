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
