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


def test_applies_a_character_colour(bridge, doc):
    result = bridge.format_range({"paragraph": 0, "offset": 0, "length": 5},
                                 color="#0000CC", doc=doc)

    assert result["success"] is True
    assert applied(doc, "CharColor") == 0x0000CC
    assert result["applied"]["color"] == "#0000CC"


def test_applies_a_character_background(bridge, doc):
    bridge.format_range({"paragraph": 0}, background_color="FFFFCC", doc=doc)

    assert applied(doc, "CharBackColor") == 0xFFFFCC


def test_accepts_a_colour_as_a_number(bridge, doc):
    bridge.format_range({"paragraph": 0}, color=0x336699, doc=doc)

    assert applied(doc, "CharColor") == 0x336699


def test_rejects_something_that_is_not_a_colour(bridge, doc):
    for bad in ["blueish", "#12345", "#GGHHII", None.__class__, -1, 0x1000000]:
        result = bridge.format_range({"paragraph": 0}, color=bad, doc=doc)
        assert result["success"] is False, bad
        assert "colour" in result["error"].lower() or "color" in result["error"].lower()


def test_draws_a_box_around_a_paragraph(bridge, doc):
    result = bridge.format_paragraph({"paragraph": 0}, border=True,
                                     border_color="#808080", border_width=0.5,
                                     doc=doc)

    assert result["success"] is True
    text = doc.getText()
    for side in ("TopBorder", "BottomBorder", "LeftBorder", "RightBorder"):
        line = text.border_property((0, 0), (0, len(text.paragraphs[0])), side)
        assert line is not None, side
        assert line.Color == 0x808080
        assert line.LineWidth == 18  # 0.5pt in 1/100 mm


def test_removing_the_border_sets_zero_width_lines(bridge, doc):
    bridge.format_paragraph({"paragraph": 0}, border=False, doc=doc)

    text = doc.getText()
    line = text.border_property((0, 0), (0, len(text.paragraphs[0])), "TopBorder")
    assert line.LineWidth == 0


def test_padding_sets_the_distance_on_every_side(bridge, doc):
    bridge.format_paragraph({"paragraph": 0}, border=True, padding=2.0, doc=doc)

    text = doc.getText()
    span = ((0, 0), (0, len(text.paragraphs[0])))
    for side in ("TopBorderDistance", "BottomBorderDistance",
                 "LeftBorderDistance", "RightBorderDistance"):
        assert text.border_property(*span, side) == 71  # 2pt in 1/100 mm


def test_fills_the_paragraph_background(bridge, doc):
    """ParaBackColor does not stick; FillStyle plus FillColor is what works."""
    result = bridge.format_paragraph({"paragraph": 1}, background_color="#F5F5F5",
                                     doc=doc)

    assert result["success"] is True
    assert doc.getText().fills[1]["FillColor"] == 0xF5F5F5
    assert doc.getText().fills[1]["FillStyle"] is not None


def test_format_paragraph_refuses_when_nothing_was_asked_for(bridge, doc):
    result = bridge.format_paragraph({"paragraph": 0}, doc=doc)

    assert result["success"] is False
    assert "nothing to apply" in result["error"].lower()


def test_format_paragraph_is_one_undo_step(bridge, doc):
    bridge.format_paragraph({"paragraph": 0}, border=True, doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: format paragraph"),
                                     ("leave", None)]


def test_format_paragraph_rejects_a_bad_address(bridge, doc):
    result = bridge.format_paragraph({"paragraph": 99}, border=True, doc=doc)

    assert result["success"] is False
    assert "no body paragraph 99" in result["error"]


def test_format_paragraph_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "format_paragraph_live" in server.tools
    assert "color" in server.tools["format_range_live"]["parameters"]["properties"]

    result = asyncio.run(server.execute_tool(
        "format_paragraph_live",
        {"address": {"paragraph": 2}, "background_color": "#F5F5F5",
         "border": True}))

    assert result["success"] is True
    assert doc.getText().fills[2]["FillColor"] == 0xF5F5F5
