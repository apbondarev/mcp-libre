"""Tests for how the tools relate to Writer's change recording.

Two problems drove these. Nothing reported whether recording was on, so an
assistant looking for it went around the server into raw UNO. And
track_changes=false claimed the edit was clean while a document with recording
already on recorded it anyway, which is how a replacement shows up in red.
"""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["The quick brown fox.", "Second line here."]


@pytest.fixture
def bridge():
    return UNOBridge()


def selected(spans=None):
    return writer_doc(PARAGRAPHS, caret=(0, 0),
                      selection_spans=spans or [((0, 0), (0, 9))])


def test_document_info_reports_that_recording_is_off(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))

    info = bridge.get_document_info(doc)

    assert info["track_changes"] is False
    assert info["tracked_changes"] == 0


def test_document_info_reports_that_recording_is_on(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.RecordChanges = True
    doc.redline_count = 3

    info = bridge.get_document_info(doc)

    assert info["track_changes"] is True
    assert info["tracked_changes"] == 3


def test_by_default_the_document_decides_and_the_result_says_so(bridge):
    """Recording already on: the edit is recorded, and the caller is told."""
    doc = selected()
    doc.RecordChanges = True

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert result["success"] is True
    assert result["tracked"] is True, "the edit really was recorded"
    assert doc.RecordChanges is True, "the document's setting is left alone"


def test_by_default_a_document_that_records_nothing_produces_a_clean_edit(bridge):
    doc = selected()

    result = bridge.replace_selection("Быстрая", doc=doc)

    assert result["tracked"] is False
    assert doc.RecordChanges is False


def test_asking_for_tracking_turns_it_on_for_the_edit_only(bridge):
    doc = selected()
    seen = []
    text = doc.getText()
    original = text.replace_range
    text.replace_range = lambda *args: (seen.append(doc.RecordChanges),
                                        original(*args))[1]

    result = bridge.replace_selection("Быстрая", track_changes=True, doc=doc)

    assert result["tracked"] is True
    assert seen == [True]
    assert doc.RecordChanges is False


def test_asking_for_no_tracking_overrides_a_recording_document(bridge):
    """The explicit opt-out has to actually opt out, or it is a lie."""
    doc = selected()
    doc.RecordChanges = True
    seen = []
    text = doc.getText()
    original = text.replace_range
    text.replace_range = lambda *args: (seen.append(doc.RecordChanges),
                                        original(*args))[1]

    result = bridge.replace_selection("Быстрая", track_changes=False, doc=doc)

    assert result["tracked"] is False
    assert seen == [False], "recording must be off while the edit happens"
    assert doc.RecordChanges is True, "the document's setting is restored"


def test_replace_range_follows_the_same_rules(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.RecordChanges = True

    result = bridge.replace_range({"paragraph": 1}, "Вторая", doc=doc)

    assert result["tracked"] is True
    assert doc.RecordChanges is True


def test_document_info_tool_exposes_recording_state():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.RecordChanges = True
    doc.redline_count = 2
    server.uno_bridge.desktop = FakeDesktop([doc])

    result = asyncio.run(server.execute_tool("get_document_info_live", {}))

    assert result["document_info"]["track_changes"] is True
    assert result["document_info"]["tracked_changes"] == 2
