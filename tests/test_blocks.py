"""Tests for addressing a block of paragraphs, and for selecting one.

An address covered one paragraph — offset and length count characters — so
replacing a fifteen-paragraph query with a table could not be said at all,
and an assistant went to raw UNO to set a selection instead. Now a block is
an address, and a selection can be set.
"""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import AddressError, UNOBridge  # noqa: E402

BLOCK = ["Введение", "Operation", "{", "  hero {", "    name", "  }", "}",
         "Response", "{", '  "hero": "R2-D2"', "}", "Конец"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(BLOCK, caret=(0, 0))


def test_a_block_of_paragraphs_is_one_range(bridge, doc):
    span = bridge._resolve_address(doc, {"paragraph": 1, "through": 6})

    assert span.getString() == "Operation\n{\n  hero {\n    name\n  }\n}"


def test_one_paragraph_through_itself_is_that_paragraph(bridge, doc):
    span = bridge._resolve_address(doc, {"paragraph": 1, "through": 1})

    assert span.getString() == "Operation"


def test_a_block_is_checked(bridge, doc):
    for address, expected in (
            ({"paragraph": 6, "through": 1}, "comes before"),
            ({"paragraph": 1, "through": 99}, "no body paragraph 99"),
            ({"paragraph": 1, "through": "x"}, "through must be"),
            ({"paragraph": 1, "through": 3, "offset": 2}, "takes no offset")):
        with pytest.raises(AddressError, match=expected):
            bridge._resolve_address(doc, address)


def test_a_table_can_replace_a_whole_block(bridge, doc):
    made = bridge.create_table({"paragraph": 1, "through": 10}, rows=2,
                               columns=2,
                               cells=[["Operation", "Response"],
                                      ["{ hero }", '{ "R2-D2" }']],
                               name="Пример", replace=True, doc=doc)

    assert made["success"] is True
    assert made["paragraphs_replaced"] == list(range(1, 11))
    left = [p["text"] for p in
            bridge.read_paragraphs(start=0, count=20, doc=doc)["paragraphs"]]
    assert left == ["Введение", "Конец"]
    assert bridge.list_tables(doc=doc)["count"] == 1


def test_a_block_can_be_replaced_with_text(bridge, doc):
    written = bridge.replace_range({"paragraph": 1, "through": 6},
                                   "Запрос убран", flatten=True, doc=doc)

    assert written["success"] is True
    left = [p["text"] for p in
            bridge.read_paragraphs(start=0, count=20, doc=doc)["paragraphs"]]
    assert left[1] == "Запрос убран"
    assert "Response" in left


def test_selecting_a_block(bridge, doc):
    selected = bridge.select({"paragraph": 1, "through": 6}, doc=doc)

    assert selected["success"] is True
    # From the range itself: how many, not which — naming them is a walk.
    assert selected["paragraphs_selected"] == 6
    assert bridge.select({"paragraph": 1, "through": 6}, number=True,
                         doc=doc)["paragraphs"] == [1, 2, 3, 4, 5, 6]
    assert selected["selected"].startswith("Operation")
    # and the document's own selection is now that range
    assert bridge._resolve_address(doc, {"selection": True}).getString() \
        == selected["selected"]


def test_selecting_a_number_of_paragraphs_from_a_place(bridge, doc):
    # Every route to a paragraph *number* on a long document is a sweep of
    # the body — the outline 3.2s, a text search 2.9s — so a block is asked
    # for from a place already in hand, walking forward from it.
    held = bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["address"]

    selected = bridge.select(held, paragraphs=3, doc=doc)

    assert selected["success"] is True
    assert selected["paragraphs_selected"] == 4
    assert bridge._resolve_address(doc, {"selection": True}).getString() \
        == selected["selected"]
    refused = bridge.select(held, paragraphs=-2, doc=doc)
    assert refused["code"] == "INVALID_PARAMETER"


def test_selecting_one_paragraph(bridge, doc):
    selected = bridge.select({"paragraph": 7}, doc=doc)

    assert selected["selected"] == "Response"
    assert selected["length"] == 8


def test_selecting_an_address_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.select({"paragraph": 99}, doc=doc)

    assert refused["success"] is False
    assert "99" in refused["error"]


def test_the_selecting_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(BLOCK, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    selected = asyncio.run(server.execute_tool(
        "select_live", {"address": {"paragraph": 1, "through": 3}}))

    assert selected["success"] is True
    assert selected["paragraphs_selected"] == 3
    numbered = asyncio.run(server.execute_tool(
        "select_live", {"address": {"paragraph": 1, "through": 3},
                        "number": True}))
    assert numbered["paragraphs"] == [1, 2, 3]
