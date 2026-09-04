"""Tests for reading and rewriting a range as formatted runs.

Replacing a mixed-formatting selection with setString collapses it to one run:
a monospace term loses its font, a coloured phrase loses its colour. Measured
on LibreOffice 24.2.7.2 — four runs in, one run out. Translating text while
keeping its look therefore needs the runs read out and written back
explicitly.
"""

import asyncio

import pytest

from tests.fake_writer import FakeCalcDoc, FakeDesktop, FakeLocale, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

EN = FakeLocale("en", "US")
MIXED = [
    {"text": "Character", "locale": EN, "CharFontName": "Liberation Mono"},
    {"text": " is a ", "locale": EN},
    {"text": "GraphQL Object type", "locale": EN, "CharColor": 0x000080},
    {"text": ", meaning it has fields.", "locale": EN},
]
PARAGRAPH = "".join(run["text"] for run in MIXED)


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Heading", PARAGRAPH], caret=(1, 0),
                      portions={1: MIXED})


def test_reads_every_run_with_its_text(bridge, doc):
    result = bridge.read_runs({"paragraph": 1}, doc=doc)

    assert result["success"] is True
    assert [run["text"] for run in result["runs"]] == [
        "Character", " is a ", "GraphQL Object type", ", meaning it has fields."]


def test_each_run_carries_an_address_that_resolves_to_it(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]

    for run in runs:
        resolved = bridge._resolve_address(doc, run["address"])
        assert resolved.getString() == run["text"]


def test_reports_the_formatting_that_makes_a_run_different(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]

    assert runs[0]["font_name"] == "Liberation Mono"
    assert runs[2]["color"] == "#000080"
    assert runs[1]["color"] is None      # automatic, not a colour
    assert runs[0]["bold"] is False


def test_clips_the_runs_to_the_addressed_span(bridge, doc):
    # "is a GraphQL" — starts inside run 1 and ends inside run 2
    result = bridge.read_runs({"paragraph": 1, "offset": 10, "length": 12},
                              doc=doc)

    assert "".join(run["text"] for run in result["runs"]) == "is a GraphQL"
    assert [run["text"] for run in result["runs"]] == ["is a ", "GraphQL"]


def test_reports_the_language_of_each_run(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]

    assert {run["language"] for run in runs} == {"en-US"}


def test_read_runs_rejects_a_bad_address(bridge, doc):
    result = bridge.read_runs({"paragraph": 99}, doc=doc)

    assert result["success"] is False
    assert "no body paragraph 99" in result["error"]


def test_read_runs_rejects_a_non_writer_document(bridge):
    assert bridge.read_runs({"paragraph": 0}, doc=FakeCalcDoc())["success"] is False


def test_writes_back_several_runs_keeping_each_ones_formatting(bridge, doc):
    result = bridge.replace_runs({"paragraph": 1}, [
        {"text": "Character", "font_name": "Liberation Mono"},
        {"text": " — это "},
        {"text": "объектный тип GraphQL", "color": "#000080"},
        {"text": ", то есть тип с полями."},
    ], doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[1] == (
        "Character — это объектный тип GraphQL, то есть тип с полями.")
    assert result["runs"] == 4


def test_applies_each_runs_formatting_at_its_own_offset(bridge, doc):
    bridge.replace_runs({"paragraph": 1}, [
        {"text": "abc", "font_name": "Liberation Mono"},
        {"text": "defg", "color": "#000080"},
    ], doc=doc)

    text = doc.getText()
    assert text.char_property((1, 0), (1, 3), "CharFontName") == "Liberation Mono"
    assert text.char_property((1, 3), (1, 7), "CharColor") == 0x000080


def test_replace_runs_can_set_the_language_per_run(bridge, doc):
    bridge.replace_runs({"paragraph": 1}, [
        {"text": "Character", "language": "en-US"},
        {"text": " — это объектный тип", "language": "ru-RU"},
    ], doc=doc)

    text = doc.getText()
    assert text.locale_at((1, 0)).Language == "en"
    assert text.locale_at((1, 12)).Language == "ru"


def test_replace_runs_is_one_undo_step(bridge, doc):
    bridge.replace_runs({"paragraph": 1}, [{"text": "a"}, {"text": "b"}], doc=doc)

    assert doc.UndoManager.calls == [("enter", "MCP: replace runs"),
                                     ("leave", None)]


def test_replace_runs_rejects_an_empty_list(bridge, doc):
    result = bridge.replace_runs({"paragraph": 1}, [], doc=doc)

    assert result["success"] is False
    assert "at least one run" in result["error"].lower()
    assert doc.getText().paragraphs[1] == PARAGRAPH


def test_replace_runs_rejects_a_run_without_text(bridge, doc):
    result = bridge.replace_runs({"paragraph": 1}, [{"color": "#000080"}], doc=doc)

    assert result["success"] is False
    assert "text" in result["error"].lower()
    assert doc.getText().paragraphs[1] == PARAGRAPH


def test_replace_runs_rejects_a_bad_colour_before_touching_anything(bridge, doc):
    result = bridge.replace_runs({"paragraph": 1},
                                 [{"text": "a", "color": "blueish"}], doc=doc)

    assert result["success"] is False
    assert doc.getText().paragraphs[1] == PARAGRAPH


def test_run_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", PARAGRAPH], caret=(1, 0), portions={1: MIXED})
    server.uno_bridge.desktop = FakeDesktop([doc])

    assert "read_runs_live" in server.tools
    assert "replace_runs_live" in server.tools

    runs = asyncio.run(server.execute_tool(
        "read_runs_live", {"address": {"paragraph": 1}}))
    assert len(runs["runs"]) == 4

    written = asyncio.run(server.execute_tool("replace_runs_live", {
        "address": {"paragraph": 1},
        "runs": [{"text": "Кто", "font_name": "Liberation Mono"},
                 {"text": " и что"}]}))
    assert written["success"] is True
    assert doc.getText().paragraphs[1] == "Кто и что"


LINKED = [
    {"text": "Character", "locale": EN, "CharStyleName": "Source Text"},
    {"text": " is a ", "locale": EN},
    {"text": "GraphQL Object type", "locale": EN, "CharColor": 0x000080,
     "CharUnderline": 1, "HyperLinkURL": "https://graphql.org/learn/schema/",
     "HyperLinkTarget": "_blank"},
    {"text": ", meaning it has fields.", "locale": EN},
]
LINKED_TEXT = "".join(run["text"] for run in LINKED)


@pytest.fixture
def linked_doc():
    return writer_doc(["Heading", LINKED_TEXT], caret=(1, 0),
                      portions={1: LINKED})


def test_reports_a_hyperlink_on_the_run_that_carries_it(bridge, linked_doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=linked_doc)["runs"]

    assert runs[2]["link"] == "https://graphql.org/learn/schema/"
    assert runs[2]["link_target"] == "_blank"
    assert runs[0]["link"] is None


def test_reports_the_character_style_of_a_run(bridge, linked_doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=linked_doc)["runs"]

    assert runs[0]["character_style"] == "Source Text"
    assert runs[1]["character_style"] is None


def test_writes_a_hyperlink_back(bridge, linked_doc):
    result = bridge.replace_runs({"paragraph": 1}, [
        {"text": "Character", "character_style": "Source Text"},
        {"text": " — это "},
        {"text": "объектный тип GraphQL",
         "link": "https://graphql.org/learn/schema/", "link_target": "_blank"},
        {"text": ", то есть тип с полями."},
    ], doc=linked_doc)

    assert result["success"] is True
    text = linked_doc.getText()
    span = ((1, 16), (1, 37))
    assert text.char_property(*span, "HyperLinkURL") == \
        "https://graphql.org/learn/schema/"
    assert text.char_property(*span, "HyperLinkTarget") == "_blank"


def test_a_written_link_gets_the_look_of_a_link(bridge, linked_doc):
    """The navy underline comes from the link character styles, not a colour."""
    bridge.replace_runs({"paragraph": 1}, [
        {"text": "ссылка", "link": "https://example.org/"},
    ], doc=linked_doc)

    text = linked_doc.getText()
    span = ((1, 0), (1, 6))
    assert text.char_property(*span, "UnvisitedCharStyleName") == "Internet link"
    assert text.char_property(*span, "VisitedCharStyleName") == \
        "Visited Internet Link"


def test_writes_a_character_style_back(bridge, linked_doc):
    bridge.replace_runs({"paragraph": 1},
                        [{"text": "Character", "character_style": "Source Text"}],
                        doc=linked_doc)

    assert linked_doc.getText().char_property(
        (1, 0), (1, 9), "CharStyleName") == "Source Text"


def test_rejects_a_link_that_is_not_a_string(bridge, linked_doc):
    result = bridge.replace_runs({"paragraph": 1},
                                 [{"text": "a", "link": 42}], doc=linked_doc)

    assert result["success"] is False
    assert "link" in result["error"].lower()
    assert linked_doc.getText().paragraphs[1] == LINKED_TEXT


def test_a_run_read_out_can_be_written_straight_back(bridge, linked_doc):
    """Read, change the text, write back: the shape of a translation.

    What the document looks like afterwards is checked live, in
    tests/live/writer_tools_check.py — the fake drops its declared runs on a
    write, since they described text that no longer exists. Here the point is
    that a run as read is accepted verbatim as a run to write, with its link
    and character style carried through.
    """
    runs = bridge.read_runs({"paragraph": 1}, doc=linked_doc)["runs"]
    rewritten = [dict(run, text=run["text"].upper()) for run in runs]

    result = bridge.replace_runs({"paragraph": 1}, rewritten, doc=linked_doc)

    assert result["success"] is True
    assert result["runs"] == 4
    assert linked_doc.getText().paragraphs[1] == LINKED_TEXT.upper()

    text = linked_doc.getText()
    assert text.char_property((1, 0), (1, 9), "CharStyleName") == "Source Text"
    assert text.char_property((1, 15), (1, 34), "HyperLinkURL") == \
        "https://graphql.org/learn/schema/"
