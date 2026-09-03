"""Tests for replacing the human's selection.

The tool exists because insert_text passes bAbsorb=False, so asking an
assistant to "translate and replace" left both texts in the document. The
contract here is the one every mutation carries: one undo step, refuse a
read-only document, and never touch the selection when there is none.
"""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["The quick brown fox.", "Second line here."]


@pytest.fixture
def bridge():
    return UNOBridge()


def selected(spans, paragraphs=None):
    """A document with `spans` selected."""
    return writer_doc(paragraphs or PARAGRAPHS, caret=(0, 0), selection_spans=spans)


def test_replaces_the_selected_text(bridge):
    doc = selected([((0, 0), (0, 9))])  # "The quick"

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[0] == "Быстрая brown fox."


def test_replaces_a_selection_spanning_paragraphs(bridge):
    doc = selected([((0, 4), (1, 6))])  # "quick brown fox.\nSecond"

    result = bridge.replace_selection("X", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs == ["The X line here."]


def test_reports_what_it_replaced_and_where(bridge):
    doc = selected([((1, 0), (1, 6))])  # "Second"

    result = bridge.replace_selection("Второй", doc=doc)

    assert result["replaced_length"] == 6
    assert result["inserted_length"] == len("Второй")
    assert result["paragraph"] == 1
    assert result["total_paragraphs"] == 2


def test_refuses_when_nothing_is_selected(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 5))  # collapsed caret

    result = bridge.replace_selection("anything", doc=doc)

    assert result["success"] is False
    assert "select" in result["error"].lower()
    assert doc.getText().paragraphs == PARAGRAPHS


def test_refuses_a_read_only_document(bridge):
    doc = selected([((0, 0), (0, 9))])
    doc.readonly = True

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert result["success"] is False
    assert "read-only" in result["error"].lower()
    assert doc.getText().paragraphs == PARAGRAPHS


def test_refuses_a_non_writer_document(bridge):
    result = bridge.replace_selection("text", doc=FakeCalcDoc())

    assert result["success"] is False
    assert "writer" in result["error"].lower()


def test_wraps_the_edit_in_one_undo_step(bridge):
    doc = selected([((0, 0), (0, 9))])

    bridge.replace_selection("Быстрая", doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: replace selection"),
                                     ("leave", None)]


def test_leaves_the_undo_context_even_when_the_edit_fails(bridge, monkeypatch):
    doc = selected([((0, 0), (0, 9))])

    class Exploding:
        def setString(self, value):
            raise RuntimeError("UNO said no")

        def getString(self):
            return "The quick"

    monkeypatch.setattr(bridge, "_resolve_address", lambda d, a: Exploding())

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert result["success"] is False
    assert doc.UndoManager.calls[-1] == ("leave", None)


def test_does_not_record_changes_by_default(bridge):
    doc = selected([((0, 0), (0, 9))])

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert doc.RecordChanges is False
    assert result["tracked"] is False


def test_records_the_change_when_asked_and_restores_the_setting(bridge):
    doc = selected([((0, 0), (0, 9))])
    seen = []
    text = doc.getText()
    original_replace = text.replace_range

    def spy(start, end, value):
        seen.append(doc.RecordChanges)
        original_replace(start, end, value)

    text.replace_range = spy

    result = bridge.replace_selection("Быстрая", track_changes=True, doc=doc)

    assert result["tracked"] is True
    assert seen == [True], "recording must be on while the edit happens"
    assert doc.RecordChanges is False, "the document's own setting is restored"


def test_tool_is_registered_and_replaces_through_the_dispatcher():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = selected([((0, 0), (0, 9))])
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "replace_selection_live" in server.tools
    schema = server.tools["replace_selection_live"]["parameters"]
    assert schema["required"] == ["text"]
    assert "track_changes" in schema["properties"]
    assert "document" in schema["properties"]

    result = asyncio.run(server.execute_tool("replace_selection_live",
                                             {"text": "Быстрая"}))

    assert result["success"] is True
    assert doc.getText().paragraphs[0] == "Быстрая brown fox."
