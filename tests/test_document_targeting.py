"""Tests for naming which open document a tool should act on."""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, FakeModalDialog, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Alpha.", "Beta."]


@pytest.fixture
def bridge():
    return UNOBridge()


def test_falls_back_to_the_active_document(bridge):
    active = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.get_active_document = lambda: active

    assert bridge.document_for(None) is active


def test_returns_the_document_matching_a_url(bridge):
    first = writer_doc(PARAGRAPHS, caret=(0, 0))
    first.Title = "first.odt"
    second = writer_doc(PARAGRAPHS, caret=(0, 0))
    second.Title = "second.odt"
    bridge.desktop = FakeDesktop([first, second])

    assert bridge.document_for("file:///tmp/second.odt") is second


def test_returns_nothing_for_a_url_that_is_not_open(bridge):
    only = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.desktop = FakeDesktop([only])

    assert bridge.document_for("file:///tmp/absent.odt") is None


def test_a_tool_reads_the_document_it_was_told_to_read():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    active = writer_doc(["Active document."], caret=(0, 0))
    active.Title = "active.odt"
    other = writer_doc(["Other document."], caret=(0, 0))
    other.Title = "other.odt"
    server.uno_bridge.get_active_document = lambda: active
    server.uno_bridge.desktop = FakeDesktop([active, other])

    result = asyncio.run(server.execute_tool(
        "read_paragraphs_live", {"document": "file:///tmp/other.odt"}))

    assert result["paragraphs"][0]["text"] == "Other document."


def test_a_tool_reports_a_document_it_cannot_find():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    active = writer_doc(["Active document."], caret=(0, 0))
    server.uno_bridge.get_active_document = lambda: active
    server.uno_bridge.desktop = FakeDesktop([active])

    result = asyncio.run(server.execute_tool(
        "read_paragraphs_live", {"document": "file:///tmp/absent.odt"}))

    assert result["success"] is False
    assert "absent.odt" in result["error"]


def test_uses_the_current_component_when_it_is_a_document(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.desktop = FakeDesktop([doc])

    assert bridge.get_active_document() is doc


def test_falls_back_to_an_open_document_when_a_dialog_is_current(bridge):
    """A modal dialog or the Start Center answers getCurrentComponent().

    Trusting it made every tool report "not a Writer document" while a Writer
    document was open — observed live, with a status dialog on screen.
    """
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    bridge.desktop = FakeDesktop([doc], current=FakeModalDialog())

    assert bridge.get_active_document() is doc


def test_reports_no_document_when_none_of_the_components_is_one(bridge):
    bridge.desktop = FakeDesktop([], current=FakeModalDialog())

    assert bridge.get_active_document() is None


def test_a_tool_still_works_while_a_dialog_holds_the_focus():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Chapter", "Body."], caret=(0, 0),
                     styles=["Heading 1", "Standard"], outline_levels=[1, 0])
    server.uno_bridge.desktop = FakeDesktop([doc], current=FakeModalDialog())

    result = asyncio.run(server.execute_tool("get_outline_live", {}))

    assert result["success"] is True
    assert [h["text"] for h in result["headings"]] == ["Chapter"]


def test_listing_open_documents_skips_components_that_are_not_documents():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([FakeModalDialog(), doc],
                                            current=FakeModalDialog())

    result = asyncio.run(server.execute_tool("list_open_documents", {}))

    assert result["count"] == 1
    assert result["documents"][0]["type"] == "writer"
