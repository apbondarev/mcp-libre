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
