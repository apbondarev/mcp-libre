"""Tests for replacing text at an address, with no human selection involved.

This is what makes the addresses from get_outline and find_text actionable:
without it an assistant asked to "translate all headings" has no way to write
the translations back, which is exactly what happened.
"""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Schemas and Types", "Body text here.", "Type system"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def test_replaces_a_whole_paragraph_by_index(bridge, doc):
    result = bridge.replace_range({"paragraph": 2}, "Система типов", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs == [
        "Schemas and Types", "Body text here.", "Система типов"]


def test_replaces_part_of_a_paragraph(bridge, doc):
    result = bridge.replace_range({"paragraph": 0, "offset": 0, "length": 7},
                                  "Схемы", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[0] == "Схемы and Types"


def test_replaces_an_empty_paragraph(bridge):
    doc = writer_doc(["", "Body."], caret=(0, 0))

    result = bridge.replace_range({"paragraph": 0}, "New heading", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[0] == "New heading"


def test_accepts_the_selection_as_an_address(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(1, 0), selection_spans=[((1, 0), (1, 4))])

    result = bridge.replace_range({"selection": True}, "Текст", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[1] == "Текст text here."


def test_rejects_an_address_past_the_end_of_the_document(bridge, doc):
    result = bridge.replace_range({"paragraph": 99}, "nowhere", doc=doc)

    assert result["success"] is False
    assert "no body paragraph 99" in result["error"]
    assert doc.getText().paragraphs == PARAGRAPHS


def test_rejects_a_read_only_document(bridge, doc):
    doc.readonly = True

    result = bridge.replace_range({"paragraph": 0}, "nope", doc=doc)

    assert result["success"] is False
    assert "read-only" in result["error"].lower()
    assert doc.getText().paragraphs == PARAGRAPHS


def test_rejects_a_non_writer_document(bridge):
    result = bridge.replace_range({"paragraph": 0}, "text", doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_wraps_the_edit_in_one_undo_step(bridge, doc):
    bridge.replace_range({"paragraph": 2}, "Система типов", doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: replace text"),
                                     ("leave", None)]


def test_reports_the_paragraph_and_the_new_total(bridge, doc):
    result = bridge.replace_range({"paragraph": 2}, "Система типов", doc=doc)

    assert result["paragraph"] == 2
    assert result["total_paragraphs"] == 3
    assert result["replaced_length"] == len("Type system")
    assert result["inserted_length"] == len("Система типов")


def test_records_the_change_when_asked_and_restores_the_setting(bridge, doc):
    seen = []
    text = doc.getText()
    original = text.replace_range

    def spy(start, end, value):
        seen.append(doc.RecordChanges)
        original(start, end, value)

    text.replace_range = spy

    result = bridge.replace_range({"paragraph": 0}, "Схемы", track_changes=True,
                                  doc=doc)

    assert result["tracked"] is True
    assert seen == [True]
    assert doc.RecordChanges is False


def test_tool_is_registered_and_replaces_through_the_dispatcher():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "replace_range_live" in server.tools
    schema = server.tools["replace_range_live"]["parameters"]
    assert schema["required"] == ["address", "text"]

    result = asyncio.run(server.execute_tool(
        "replace_range_live", {"address": {"paragraph": 2}, "text": "Система типов"}))

    assert result["success"] is True
    assert doc.getText().paragraphs[2] == "Система типов"
