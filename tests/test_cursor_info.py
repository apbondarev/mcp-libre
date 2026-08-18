"""Tests for UNOBridge.get_cursor_info() and the get_cursor_info_live tool.

Unlike the other scripts in this directory these are real pytest tests: they
assert instead of printing, and they need no LibreOffice, because the UNO layer
is faked (see tests/fake_writer.py for what that can and cannot prove).
"""

import asyncio

import pytest

from tests.fake_writer import (
    FakeCalcDoc,
    FakeTableCellSelection,
    writer_doc,
    writer_doc_with_caret_in_cell,
)
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import MAX_TEXT_CHARS, UNOBridge  # noqa: E402

PARAGRAPHS = [
    "First paragraph.",  # 16 chars
    "Second paragraph here.",  # 22 chars
    "Third one.",  # 10 chars
]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_reports_caret_offset_within_its_paragraph(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 7))

    result = bridge.get_cursor_info(doc)

    assert result["success"] is True
    assert result["cursor"]["offset_in_paragraph"] == 7


def test_reports_text_of_paragraph_containing_caret(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 7))

    result = bridge.get_cursor_info(doc)

    assert result["paragraph"]["text"] == "Second paragraph here."
    assert result["paragraph"]["length"] == 22
    assert result["paragraph"]["truncated"] is False


def test_reports_paragraph_index_and_document_offset(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(2, 4))

    result = bridge.get_cursor_info(doc)

    assert result["cursor"]["paragraph_index"] == 2
    # 16 + 1 newline + 22 + 1 newline + 4 into the third paragraph
    assert result["cursor"]["document_offset"] == 44


def test_reports_page_number(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0), page=7)

    result = bridge.get_cursor_info(doc)

    assert result["cursor"]["page"] == 7


def test_reports_selected_text(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 0), selection_spans=[((1, 0), (1, 6))])

    result = bridge.get_cursor_info(doc)

    assert result["selection"]["has_selection"] is True
    assert result["selection"]["text"] == "Second"
    assert result["selection"]["length"] == 6
    assert result["selection"]["range_count"] == 1


def test_reports_no_selection_when_caret_is_collapsed(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 7))

    result = bridge.get_cursor_info(doc)

    assert result["selection"]["has_selection"] is False
    assert result["selection"]["text"] == ""
    assert result["selection"]["length"] == 0


def test_joins_multiple_selection_ranges(bridge):
    doc = writer_doc(
        PARAGRAPHS,
        caret=(0, 0),
        selection_spans=[((0, 0), (0, 5)), ((2, 0), (2, 5))],
    )

    result = bridge.get_cursor_info(doc)

    assert result["selection"]["text"] == "First\nThird"
    assert result["selection"]["range_count"] == 2


def test_selection_spanning_paragraphs_keeps_paragraph_breaks(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 6), selection_spans=[((0, 6), (1, 6))])

    result = bridge.get_cursor_info(doc)

    assert result["selection"]["text"] == "paragraph.\nSecond"


def test_truncates_long_paragraph_but_reports_true_length(bridge):
    long_paragraph = "x" * (MAX_TEXT_CHARS + 500)
    doc = writer_doc([long_paragraph], caret=(0, 0))

    result = bridge.get_cursor_info(doc)

    assert len(result["paragraph"]["text"]) == MAX_TEXT_CHARS
    assert result["paragraph"]["length"] == MAX_TEXT_CHARS + 500
    assert result["paragraph"]["truncated"] is True


def test_truncates_long_selection_but_reports_true_length(bridge):
    long_paragraph = "y" * (MAX_TEXT_CHARS + 500)
    doc = writer_doc(
        [long_paragraph],
        caret=(0, 0),
        selection_spans=[((0, 0), (0, MAX_TEXT_CHARS + 500))],
    )

    result = bridge.get_cursor_info(doc)

    assert len(result["selection"]["text"]) == MAX_TEXT_CHARS
    assert result["selection"]["length"] == MAX_TEXT_CHARS + 500
    assert result["selection"]["truncated"] is True


def test_skips_tables_when_walking_to_the_caret_paragraph(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(2, 0), enumeration_items=[0, 1, "table", 2])

    result = bridge.get_cursor_info(doc)

    assert result["cursor"]["paragraph_index"] == 2


def test_reads_the_paragraph_from_the_text_owning_the_caret(bridge):
    """In a table cell the caret's paragraph lives in the cell's own text."""
    doc = writer_doc_with_caret_in_cell(PARAGRAPHS, "Inside a cell.", caret_offset=7)

    result = bridge.get_cursor_info(doc)

    assert result["success"] is True
    assert result["paragraph"]["text"] == "Inside a cell."
    assert result["cursor"]["offset_in_paragraph"] == 7


def test_omits_document_position_when_caret_is_outside_the_body(bridge):
    """A cell range cannot be compared against body ranges, so it degrades."""
    doc = writer_doc_with_caret_in_cell(PARAGRAPHS, "Inside a cell.", caret_offset=7)

    result = bridge.get_cursor_info(doc)

    assert result["cursor"]["paragraph_index"] is None
    assert result["cursor"]["document_offset"] is None


def test_omits_paragraph_index_when_caret_paragraph_is_not_enumerated(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(2, 0), enumeration_items=[0, 1])

    result = bridge.get_cursor_info(doc)

    assert result["cursor"]["paragraph_index"] is None
    assert result["cursor"]["document_offset"] is None


def test_reports_selection_unreadable_for_table_cell_selection(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 0))
    doc.getCurrentController()._selection = FakeTableCellSelection()

    result = bridge.get_cursor_info(doc)

    assert result["success"] is True
    assert result["selection"]["has_selection"] is False
    assert result["selection"]["range_count"] == 0


def test_fails_for_non_writer_document(bridge):
    result = bridge.get_cursor_info(FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_fails_when_no_document_is_available(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "get_active_document", lambda: None)

    result = bridge.get_cursor_info()

    assert result["success"] is False
    assert "no document" in result["error"].lower()


def test_fails_when_document_has_no_view(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc._controller = None

    result = bridge.get_cursor_info(doc)

    assert result["success"] is False
    assert "view" in result["error"].lower()


def test_tool_is_registered_and_dispatches_to_the_bridge():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(1, 7))
    server.uno_bridge.get_active_document = lambda: doc

    assert "get_cursor_info_live" in server.tools
    assert server.tools["get_cursor_info_live"]["parameters"]["properties"] == {}

    result = asyncio.run(server.execute_tool("get_cursor_info_live", {}))

    assert result["success"] is True
    assert result["cursor"]["offset_in_paragraph"] == 7
