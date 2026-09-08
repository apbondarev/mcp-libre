"""Tests for not destroying comments, and for working with them.

A comment is not text. Writer represents one as empty marker portions —
Annotation ... AnnotationEnd around the commented range, or a lone Annotation
for a point anchor — and they occupy no characters, so offsets are unaffected.
Reading runs skipped them as "empty", so writing runs back destroyed them:
measured on LibreOffice 24.2.7.2, one comment in, zero out. The document this
happened in carries four, on the very terms saying "do not translate".
"""

import asyncio

import pytest

from tests.fake_writer import FakeAnnotation, FakeDesktop, FakeLocale, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

EN = FakeLocale("en", "US")
TERM_NOTE = FakeAnnotation("Aleksandr", "Термин – не переводится")
COMMENTED = [
    {"kind": "Annotation", "text": "", "field": TERM_NOTE},
    {"text": "query", "locale": EN},
    {"kind": "AnnotationEnd", "text": ""},
    {"text": " is the entry point", "locale": EN},
]
COMMENTED_TEXT = "query is the entry point"


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Heading", COMMENTED_TEXT], caret=(1, 0),
                      portions={1: COMMENTED})


def test_a_comment_occupies_no_characters(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)

    assert [run["text"] for run in runs["runs"]] == ["query", " is the entry point"]
    assert sum(run["length"] for run in runs["runs"]) == len(COMMENTED_TEXT)


def test_reports_the_comment_on_the_run_it_covers(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]

    assert runs[0]["comments"] == [
        {"author": "Aleksandr", "content": "Термин – не переводится",
         "resolved": False}]
    assert runs[1]["comments"] == []


def test_refuses_a_flat_replacement_that_would_drop_a_comment(bridge, doc):
    result = bridge.replace_range({"paragraph": 1}, "перевод", doc=doc)

    assert result["success"] is False
    assert "comment" in result["error"].lower()
    assert doc.getText().paragraphs[1] == COMMENTED_TEXT


def test_refuses_even_when_the_comment_is_the_only_reason(bridge):
    """One run, no link — the run count alone would have let this through."""
    doc = writer_doc(["Heading", "queryText"], caret=(1, 0), portions={1: [
        {"kind": "Annotation", "text": "", "field": TERM_NOTE},
        {"text": "queryText", "locale": EN},
        {"kind": "AnnotationEnd", "text": ""},
    ]})

    result = bridge.replace_range({"paragraph": 1}, "текст", doc=doc)

    assert result["success"] is False
    assert "comment" in result["error"].lower()
    assert doc.getText().paragraphs[1] == "queryText"


def test_flatten_true_drops_it_but_says_so(bridge, doc):
    result = bridge.replace_range({"paragraph": 1}, "перевод", flatten=True,
                                  doc=doc)

    assert result["success"] is True
    assert result["comments_dropped"] == 1


def test_replace_runs_refuses_when_the_comment_is_not_carried_over(bridge, doc):
    """The route I recommended as safe was destroying comments silently."""
    result = bridge.replace_runs({"paragraph": 1}, [
        {"text": "запрос"}, {"text": " — точка входа"},
    ], doc=doc)

    assert result["success"] is False
    assert "comment" in result["error"].lower()
    assert doc.getText().paragraphs[1] == COMMENTED_TEXT


def test_replace_runs_recreates_a_carried_comment(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    translated = [dict(runs[0], text="запрос"),
                  dict(runs[1], text=" — точка входа")]

    result = bridge.replace_runs({"paragraph": 1}, translated, doc=doc)

    assert result["success"] is True
    assert result["comments_written"] == 1
    created = doc.getText().created_comments
    assert len(created) == 1
    assert created[0]["note"].Content == "Термин – не переводится"
    assert created[0]["note"].Author == "Aleksandr"


def test_a_comment_is_anchored_to_the_run_that_carried_it(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)["runs"]
    translated = [dict(runs[0], text="запрос"),
                  dict(runs[1], text=" — точка входа")]

    bridge.replace_runs({"paragraph": 1}, translated, doc=doc)

    span = doc.getText().created_comments[0]["span"]
    assert span == ((1, 0), (1, len("запрос")))


def test_lists_the_comments_of_a_document(bridge, doc):
    result = bridge.list_comments(doc=doc)

    assert result["success"] is True
    assert result["count"] == 1
    assert result["comments"][0]["content"] == "Термин – не переводится"
    assert result["comments"][0]["anchor_text"] == "query"
    assert result["comments"][0]["address"]["paragraph"] == 1


def test_adds_a_comment_to_a_range(bridge):
    doc = writer_doc(["Heading", "query is the entry point"], caret=(1, 0))

    result = bridge.add_comment({"paragraph": 1, "offset": 0, "length": 5},
                                "Термин", author="Claude", doc=doc)

    assert result["success"] is True
    created = doc.getText().created_comments
    assert created[0]["note"].Content == "Термин"
    assert created[0]["note"].Author == "Claude"


def test_adding_a_comment_needs_text(bridge):
    doc = writer_doc(["Heading", "query"], caret=(1, 0))

    result = bridge.add_comment({"paragraph": 1}, "", doc=doc)

    assert result["success"] is False
    assert "text" in result["error"].lower()


def test_comment_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Heading", COMMENTED_TEXT], caret=(1, 0),
                     portions={1: COMMENTED})
    server.uno_bridge.desktop = FakeDesktop([doc])

    listed = asyncio.run(server.execute_tool("list_comments_live", {}))
    assert listed["count"] == 1

    added = asyncio.run(server.execute_tool(
        "add_comment_live", {"address": {"paragraph": 1, "offset": 0, "length": 5},
                             "text": "ещё один"}))
    assert added["success"] is True


# A comment whose anchor covers several runs: read_runs reports it on each of
# them, so counting the per-run lists reported one comment as three, and
# writing the runs back created three copies of it.
SPANNING = [
    {"kind": "Annotation", "text": "", "field": FakeAnnotation("Ревьюер", "Уточнить")},
    {"text": "query", "locale": EN, "properties": {"CharStyleName": "Source Text"}},
    {"text": " and ", "locale": EN},
    {"text": "mutation", "locale": EN, "properties": {"CharStyleName": "Source Text"}},
    {"kind": "AnnotationEnd", "text": ""},
    {"text": " are roots", "locale": EN},
]
SPANNING_TEXT = "query and mutation are roots"


@pytest.fixture
def spanning_doc():
    return writer_doc(["Heading", SPANNING_TEXT], caret=(1, 0),
                      portions={1: SPANNING})


def test_a_comment_over_several_runs_is_reported_on_each_but_counted_once(
        bridge, spanning_doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=spanning_doc)["runs"]

    assert [len(run["comments"]) for run in runs] == [1, 1, 1, 0]

    refused = bridge.replace_range({"paragraph": 1}, "перевод", doc=spanning_doc)
    assert refused["success"] is False
    assert "1 comment" in refused["error"]

    flattened = bridge.replace_range({"paragraph": 1}, "перевод",
                                     flatten=True, doc=spanning_doc)
    assert flattened["comments_dropped"] == 1


def test_carrying_a_spanning_comment_back_recreates_one_comment(
        bridge, spanning_doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=spanning_doc)["runs"]
    translated = [dict(run, text=run["text"].upper()) for run in runs]

    written = bridge.replace_runs({"paragraph": 1}, translated, doc=spanning_doc)

    assert written["success"] is True
    assert written["comments_written"] == 1

    listed = bridge.list_comments(doc=spanning_doc)
    assert listed["count"] == 1
    assert listed["comments"][0]["content"] == "Уточнить"
    # anchored over the whole stretch it covered, not just its first run
    assert listed["comments"][0]["anchor_text"] == "QUERY AND MUTATION"
