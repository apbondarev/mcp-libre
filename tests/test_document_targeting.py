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


def _server_with(active, other):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    server.uno_bridge.get_active_document = lambda: active
    server.uno_bridge.desktop = FakeDesktop([active, other])
    return server


def test_every_tool_that_acts_on_a_document_can_be_told_which():
    """Four of them could not, and two of those write.

    insert_text_live and format_text_live went to whichever document was
    active, which is how a call meant for a scratch document reached the
    reader's own.
    """
    import inspect

    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    server.tools = {}
    server._register_tools()
    # These are about the session, not about a document: making a new one,
    # and listing what is open.
    session = {"create_document_live", "list_open_documents"}

    without = {name for name, tool in server.tools.items()
               if "document" not in inspect.signature(tool["handler"]).parameters}

    assert without == session


def test_inserting_text_goes_to_the_document_it_was_told_to():
    active = writer_doc(["Active document."], caret=(0, 0))
    active.Title = "active.odt"
    other = writer_doc(["Other document."], caret=(0, 0))
    other.Title = "other.odt"
    server = _server_with(active, other)

    server.insert_text_live("вставлено", document="file:///tmp/other.odt")

    assert "вставлено" in other.getText().getString()
    assert "вставлено" not in active.getText().getString()


def test_reading_the_whole_text_of_the_document_it_was_told_to():
    active = writer_doc(["Active document."], caret=(0, 0))
    active.Title = "active.odt"
    other = writer_doc(["Other document."], caret=(0, 0))
    other.Title = "other.odt"
    server = _server_with(active, other)

    read = server.get_text_content_live(document="file:///tmp/other.odt")

    assert read["content"] == "Other document."


def test_the_cursor_of_the_document_it_was_told_about():
    active = writer_doc(["Active document."], caret=(0, 0))
    active.Title = "active.odt"
    other = writer_doc(["Other document.", "Second line."], caret=(1, 3))
    other.Title = "other.odt"
    server = _server_with(active, other)

    where = server.get_cursor_info_live(document="file:///tmp/other.odt")

    assert where["paragraph"]["text"] == "Second line."


def test_a_document_that_is_not_open_is_refused_by_all_of_them():
    active = writer_doc(["Active document."], caret=(0, 0))
    other = writer_doc(["Other document."], caret=(0, 0))
    server = _server_with(active, other)

    for call in (lambda: server.insert_text_live("x", document="file:///tmp/no.odt"),
                 lambda: server.get_text_content_live(document="file:///tmp/no.odt"),
                 lambda: server.get_cursor_info_live(document="file:///tmp/no.odt"),
                 lambda: server.get_document_info_live(document="file:///tmp/no.odt"),
                 lambda: server.format_text_live(bold=True,
                                                 document="file:///tmp/no.odt")):
        refused = call()
        assert refused["success"] is False
        assert refused["code"] == "NOT_FOUND"
