"""Tests for formatting many places in one call.

Syntax colouring is one span per token. A call each means twenty calls and
twenty undo steps for one code block, which is what sends an assistant off
to write its own UNO script — and one of those segfaulted against a live
office. One call, one undo step, and every address checked before anything
is written.
"""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, FakeTextTable, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Заголовок", "{ hero { name } }"], caret=(1, 0))


def test_colours_several_pieces_at_once(bridge, doc):
    done = bridge.format_ranges([
        {"address": {"paragraph": 1, "offset": 0, "length": 1},
         "color": "#868E96"},
        {"address": {"paragraph": 1, "offset": 2, "length": 4},
         "color": "#0B7285"},
        {"address": {"paragraph": 1, "offset": 9, "length": 4},
         "color": "#0B7285", "bold": True},
    ], doc=doc)

    assert done["success"] is True
    assert done["ranges"] == 3
    assert done["characters"] == 9
    recorded = doc.getText().char_formatting
    assert len(recorded) >= 3
    assert recorded[0]["CharColor"] == 0x868E96


def test_it_is_one_undo_step(bridge, doc):
    bridge.format_ranges([
        {"address": {"paragraph": 1, "offset": 0, "length": 1},
         "color": "#868E96"},
        {"address": {"paragraph": 1, "offset": 2, "length": 4},
         "color": "#0B7285"},
    ], doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: format ranges"),
                                     ("leave", None)]


def test_a_bad_address_leaves_the_document_alone(bridge, doc):
    refused = bridge.format_ranges([
        {"address": {"paragraph": 1, "offset": 0, "length": 1},
         "color": "#868E96"},
        {"address": {"paragraph": 9}, "color": "#0B7285"},
    ], doc=doc)

    assert refused["success"] is False
    assert "range 1" in refused["error"]
    assert doc.getText().char_formatting == []


def test_a_bad_colour_is_caught_before_writing(bridge, doc):
    refused = bridge.format_ranges([
        {"address": {"paragraph": 1, "offset": 0, "length": 1},
         "color": "не цвет"},
    ], doc=doc)

    assert refused["success"] is False
    assert "range 0" in refused["error"]
    assert doc.getText().char_formatting == []


def test_an_entry_that_asks_for_nothing_is_refused(bridge, doc):
    refused = bridge.format_ranges([
        {"address": {"paragraph": 1, "offset": 0, "length": 1}},
    ], doc=doc)

    assert refused["success"] is False
    assert "no formatting" in refused["error"]


def test_the_list_itself_is_checked(bridge, doc):
    for ranges, expected in (([], "must be a list"),
                             ("not a list", "must be a list"),
                             ([{"color": "#000000"}], "no address"),
                             (["not an object"], "must be an object")):
        refused = bridge.format_ranges(ranges, doc=doc)
        assert refused["success"] is False
        assert expected in refused["error"]


def test_it_reaches_into_table_cells(bridge):
    table = FakeTextTable("Table1", cells=[["Operation", "Response"],
                                           ["{ hero }", "R2-D2"]],
                          after_paragraph=0)
    doc = writer_doc(["Пример:"], caret=(0, 0), tables=[table])

    done = bridge.format_ranges([
        {"address": {"table": "Table1", "cell": "A2", "offset": 0,
                     "length": 1}, "color": "#868E96"},
        {"address": {"table": "Table1", "cell": "A2", "offset": 2,
                     "length": 4}, "color": "#0B7285"},
    ], doc=doc)

    assert done["success"] is True
    assert done["ranges"] == 2
    cell = table.getCellByName("A2")
    assert cell.model.char_formatting[-1]["CharColor"] == 0x0B7285


def test_the_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Заголовок", "{ hero }"], caret=(1, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    done = asyncio.run(server.execute_tool("format_ranges_live", {
        "ranges": [{"address": {"paragraph": 1, "offset": 0, "length": 1},
                    "color": "#868E96"}]}))

    assert done["success"] is True
    assert done["ranges"] == 1
