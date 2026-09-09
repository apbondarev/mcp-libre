"""Tests for describing a style.

What a caller wants to know is not the ~200 properties a paragraph style
carries but the handful it sets *itself* — and UNO says which those are:
getPropertyState answers DIRECT_VALUE for a property the style defines and
DEFAULT_VALUE for one it inherits. Measured on a live LibreOffice, "Text
body" sets 8 of 195, and they are exactly its definition in styles.xml —
which is why nobody has to unzip the document to find out.
"""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Schemas and Types", "The type system describes"],
                      caret=(1, 0), styles=["Heading 2", "Text body"],
                      outline_levels=[2, 0])


def test_describes_what_the_style_sets_itself(bridge, doc):
    described = bridge.describe_style("Text body", doc=doc)

    assert described["success"] is True
    assert described["style"]["name"] == "Text body"
    assert described["style"]["parent"] == "Standard"
    assert described["style"]["inherits_from"] == ["Standard"]
    assert described["set_here"]["ParaBottomMargin"]["value"] == "2.47 mm"
    assert described["set_here"]["ParaBottomMargin"]["raw"] == 247
    assert described["set_here_count"] == len(described["set_here"])


def test_separates_what_is_inherited(bridge, doc):
    described = bridge.describe_style("Text body", doc=doc)

    assert described["effective"]["ParaBottomMargin"]["from"] == "this style"
    assert described["effective"]["CharFontName"]["from"] == "inherited"
    assert described["effective"]["CharFontName"]["value"] == "Liberation Serif"
    assert "CharFontName" not in described["set_here"]


def test_reads_the_values_the_way_a_person_would(bridge, doc):
    described = bridge.describe_style("Text body", doc=doc)["effective"]

    assert described["CharHeight"]["value"] == "12.0 pt"
    assert described["CharWeight"]["value"] == "normal"
    assert described["ParaLineSpacing"]["value"] == "115%"
    assert described["ParaAdjust"]["value"] == "left"
    assert described["CharColor"]["value"] == "automatic"


def test_a_struct_comes_back_as_fields_not_as_a_repr(bridge, doc):
    described = bridge.describe_style("Text body", doc=doc)

    assert described["set_here"]["ParaLineSpacing"]["raw"] == {
        "mode": "proportional", "height": 115}


def test_describes_the_style_of_the_text_at_an_address(bridge, doc):
    described = bridge.describe_style(address={"paragraph": 0}, doc=doc)

    assert described["style"]["name"] == "Heading 2"


def test_describes_the_style_at_the_caret_when_asked_for_nothing(bridge, doc):
    described = bridge.describe_style(doc=doc)

    assert described["style"]["name"] == "Text body"      # the caret is in ¶1


def test_says_when_there_is_no_such_style(bridge, doc):
    refused = bridge.describe_style("Nope", doc=doc)

    assert refused["success"] is False
    assert "Nope" in refused["error"]
    assert "list_styles" in refused["error"]


def test_says_when_there_is_no_such_family(bridge, doc):
    refused = bridge.describe_style("Text body", family="chair", doc=doc)

    assert refused["success"] is False
    assert "family must be one of" in refused["error"]
    assert "paragraph" in refused["error"]


def test_all_properties_gives_the_lot_with_their_states(bridge, doc):
    described = bridge.describe_style("Text body", all_properties=True, doc=doc)

    assert described["properties_in_all"] == len(described["all_properties"])
    assert described["all_properties"]["ParaBottomMargin"]["state"] \
        == "DIRECT_VALUE"
    assert described["all_properties"]["CharFontName"]["state"] \
        == "DEFAULT_VALUE"
    # and without it, the lot is not carried
    assert "all_properties" not in bridge.describe_style("Text body", doc=doc)


def test_the_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", "Body"], caret=(1, 0),
                     styles=["Heading 2", "Text body"])
    server.uno_bridge.desktop = FakeDesktop([doc])

    described = asyncio.run(server.execute_tool("describe_style_live",
                                                {"name": "Text body"}))
    assert described["style"]["parent"] == "Standard"

    by_address = asyncio.run(server.execute_tool(
        "describe_style_live", {"address": {"paragraph": 0}}))
    assert by_address["style"]["name"] == "Heading 2"
