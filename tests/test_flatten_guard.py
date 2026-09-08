"""Tests for refusing a replacement that would silently flatten formatting.

An assistant asked to translate a section used replace_range_live on whole
paragraphs and destroyed inline code, italics and a hyperlink. It had no way
to know: the tool performed a lossy operation without a word, though it
resolves the range anyway and could count the runs first. Measured on
LibreOffice 24.2.7.2: a range of several runs collapses to one, and a
hyperlink is lost even when it is the only run — while a character style on a
single run survives.
"""

import asyncio

import pytest

from tests.fake_writer import FakeDesktop, FakeLocale, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

EN = FakeLocale("en", "US")
MIXED = [
    {"text": "query", "locale": EN, "CharStyleName": "Source Text"},
    {"text": " is an ", "locale": EN},
    {"text": "entry point", "locale": EN, "CharPosture": "ITALIC"},
    {"text": " for reads.", "locale": EN},
]
MIXED_TEXT = "".join(run["text"] for run in MIXED)
LINKED = [{"text": "the schema docs", "locale": EN,
           "HyperLinkURL": "https://graphql.org/learn/schema/"}]
STYLED = [{"text": "queryText", "locale": EN, "CharStyleName": "Source Text"}]


@pytest.fixture
def bridge():
    return UNOBridge()


def document(portions, text=None):
    body = text or "".join(run["text"] for run in portions)
    return writer_doc(["Heading", body], caret=(1, 0), portions={1: portions})


def test_refuses_to_flatten_a_paragraph_of_several_runs(bridge):
    doc = document(MIXED)

    result = bridge.replace_range({"paragraph": 1}, "перевод", doc=doc)

    assert result["success"] is False
    assert doc.getText().paragraphs[1] == MIXED_TEXT, "nothing was written"


def test_the_refusal_says_how_much_would_be_lost_and_what_to_use(bridge):
    doc = document(MIXED)

    error = bridge.replace_range({"paragraph": 1}, "перевод", doc=doc)["error"]

    assert "4" in error, "the number of runs is named"
    assert "read_runs" in error and "replace_runs" in error
    assert "flatten" in error


def test_refuses_a_single_run_that_carries_a_hyperlink(bridge):
    """A link is lost even when it is the only run — measured, not assumed."""
    doc = document(LINKED)

    result = bridge.replace_range({"paragraph": 1}, "документация", doc=doc)

    assert result["success"] is False
    assert "link" in result["error"].lower()
    assert doc.getText().paragraphs[1] == "the schema docs"


def test_allows_a_single_run_with_only_a_character_style(bridge):
    """That style survives a flat replacement, so there is nothing to refuse."""
    doc = document(STYLED)

    result = bridge.replace_range({"paragraph": 1}, "текстЗапроса", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[1] == "текстЗапроса"


def test_allows_a_uniform_paragraph(bridge):
    doc = writer_doc(["Heading", "Plain sentence."], caret=(1, 0))

    result = bridge.replace_range({"paragraph": 1}, "Простое предложение.",
                                  doc=doc)

    assert result["success"] is True


def test_flatten_true_goes_ahead_and_reports_what_it_flattened(bridge):
    doc = document(MIXED)

    result = bridge.replace_range({"paragraph": 1}, "перевод", flatten=True,
                                  doc=doc)

    assert result["success"] is True
    assert result["runs_flattened"] == 4
    assert doc.getText().paragraphs[1] == "перевод"


def test_flatten_true_reports_a_dropped_link(bridge):
    doc = document(LINKED)

    result = bridge.replace_range({"paragraph": 1}, "документация",
                                  flatten=True, doc=doc)

    assert result["success"] is True
    assert result["links_dropped"] == 1


def test_replace_selection_refuses_the_same_way(bridge):
    """This is the call that destroyed a hyperlink in a real document."""
    doc = writer_doc(["Heading", MIXED_TEXT], caret=(1, 0),
                     selection_spans=[((1, 0), (1, len(MIXED_TEXT)))],
                     portions={1: MIXED})

    result = bridge.replace_selection("перевод", doc=doc)

    assert result["success"] is False
    assert "read_runs" in result["error"]
    assert doc.getText().paragraphs[1] == MIXED_TEXT


def test_replace_selection_with_flatten_proceeds(bridge):
    doc = writer_doc(["Heading", MIXED_TEXT], caret=(1, 0),
                     selection_spans=[((1, 0), (1, len(MIXED_TEXT)))],
                     portions={1: MIXED})

    result = bridge.replace_selection("перевод", flatten=True, doc=doc)

    assert result["success"] is True
    assert result["runs_flattened"] == 4


def test_a_uniform_selection_is_replaced_without_ceremony(bridge):
    doc = writer_doc(["Heading", "Plain sentence."], caret=(1, 0),
                     selection_spans=[((1, 0), (1, 5))])

    result = bridge.replace_selection("Простое", doc=doc)

    assert result["success"] is True


def test_replace_runs_is_not_subject_to_the_guard(bridge):
    """It writes runs, so it is the answer to the guard, not a victim of it."""
    doc = document(MIXED)

    result = bridge.replace_runs({"paragraph": 1}, [
        {"text": "query", "character_style": "Source Text"},
        {"text": " — точка входа", "italic": True},
    ], doc=doc)

    assert result["success"] is True


def test_the_guard_reaches_the_tool_layer():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = document(MIXED)
    server.uno_bridge.desktop = FakeDesktop([doc])

    refused = asyncio.run(server.execute_tool(
        "replace_range_live", {"address": {"paragraph": 1}, "text": "перевод"}))
    assert refused["success"] is False

    allowed = asyncio.run(server.execute_tool(
        "replace_range_live", {"address": {"paragraph": 1}, "text": "перевод",
                               "flatten": True}))
    assert allowed["success"] is True
    assert allowed["runs_flattened"] == 4
