"""Tests for naming which open document a tool should act on."""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
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
