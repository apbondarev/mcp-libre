"""Tests for formatting text an assistant addressed itself.

format_text only ever worked on the human's current selection, and nothing
could select, so an assistant asked to make a code block monospace had to give
up and ask the user to do it by hand.
"""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, FakeDesktop, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["query { hero { name } }", "Body text here.", "{ \"json\": true }"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def applied(doc, name):
    """The last value applied for a character property, anywhere."""
    for record in reversed(doc.getText().char_formatting):
        if name in record:
            return record[name]
    return None


def test_applies_a_font_to_an_addressed_paragraph(bridge, doc):
    result = bridge.format_range({"paragraph": 0}, font_name="Liberation Mono",
                                 doc=doc)

    assert result["success"] is True
    assert applied(doc, "CharFontName") == "Liberation Mono"
    assert result["applied"] == {"font_name": "Liberation Mono"}


def test_applies_several_properties_at_once(bridge, doc):
    result = bridge.format_range({"paragraph": 1}, bold=True, italic=True,
                                 underline=True, font_size=11.0, doc=doc)

    assert result["success"] is True
    assert applied(doc, "CharWeight") == 150.0
    assert applied(doc, "CharPosture") is not None
    assert applied(doc, "CharUnderline") == 1
    assert applied(doc, "CharHeight") == 11.0


def test_bold_false_switches_weight_back(bridge, doc):
    bridge.format_range({"paragraph": 1}, bold=False, doc=doc)

    assert applied(doc, "CharWeight") == 100.0


def test_formats_part_of_a_paragraph(bridge, doc):
    result = bridge.format_range({"paragraph": 0, "offset": 0, "length": 5},
                                 bold=True, doc=doc)

    assert result["success"] is True
    assert doc.getText().char_formatting[-1]["span"] == ((0, 0), (0, 5))


def test_refuses_when_no_property_was_asked_for(bridge, doc):
    result = bridge.format_range({"paragraph": 0}, doc=doc)

    assert result["success"] is False
    assert "nothing to apply" in result["error"].lower()


def test_format_range_rejects_a_bad_address(bridge, doc):
    result = bridge.format_range({"paragraph": 99}, bold=True, doc=doc)

    assert result["success"] is False
    assert "no body paragraph 99" in result["error"]


def test_format_range_is_one_undo_step(bridge, doc):
    bridge.format_range({"paragraph": 0}, bold=True, doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: format text"), ("leave", None)]


def test_applies_a_paragraph_style(bridge, doc):
    result = bridge.apply_paragraph_style({"paragraph": 0}, "Preformatted Text",
                                          doc=doc)

    assert result["success"] is True
    assert result["style"] == "Preformatted Text"
    assert doc.getText().styles[0] == "Preformatted Text"


def test_refuses_a_style_the_document_does_not_have(bridge, doc):
    result = bridge.apply_paragraph_style({"paragraph": 0}, "No Such Style",
                                          doc=doc)

    assert result["success"] is False
    assert "No Such Style" in result["error"]
    assert "list_styles" in result["error"]
    assert doc.getText().styles[0] == "Standard"


def test_applying_a_style_is_one_undo_step(bridge, doc):
    bridge.apply_paragraph_style({"paragraph": 0}, "Quotations", doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: apply paragraph style"),
                                     ("leave", None)]


def test_lists_the_paragraph_styles_the_document_has(bridge, doc):
    result = bridge.list_styles(doc=doc)

    assert result["success"] is True
    assert "Preformatted Text" in result["styles"]
    assert result["family"] == "ParagraphStyles"


def test_lists_character_styles_when_asked(bridge, doc):
    result = bridge.list_styles(family="CharacterStyles", doc=doc)

    assert "Emphasis" in result["styles"]


def test_rejects_a_style_family_that_does_not_exist(bridge, doc):
    result = bridge.list_styles(family="Nonsense", doc=doc)

    assert result["success"] is False
    assert "Nonsense" in result["error"]


def test_formatting_rejects_a_non_writer_document(bridge):
    assert bridge.format_range({"paragraph": 0}, bold=True,
                               doc=FakeCalcDoc())["success"] is False
    assert bridge.apply_paragraph_style({"paragraph": 0}, "Standard",
                                        doc=FakeCalcDoc())["success"] is False
    assert bridge.list_styles(doc=FakeCalcDoc())["success"] is False


def test_format_text_refuses_an_empty_selection(bridge):
    """It used to report success while changing nothing."""
    doc = writer_doc(PARAGRAPHS, caret=(0, 5))  # collapsed caret

    result = bridge.format_text({"bold": True}, doc=doc)

    assert result["success"] is False
    assert "select" in result["error"].lower()


def test_formatting_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    for name in ("format_range_live", "apply_paragraph_style_live",
                 "list_styles_live"):
        assert name in server.tools, name

    result = asyncio.run(server.execute_tool(
        "apply_paragraph_style_live",
        {"address": {"paragraph": 2}, "style": "Preformatted Text"}))

    assert result["success"] is True
    assert doc.getText().styles[2] == "Preformatted Text"
