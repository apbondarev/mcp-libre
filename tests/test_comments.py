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

    note, = runs[0]["comments"]
    assert (note["author"], note["content"], note["resolved"]) == (
        "Aleksandr", "Термин – не переводится", False)
    assert note["id"]                    # names it for update_comment
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


# --- scoping: document, section, paragraph, range, selection -----------------

def commented_section():
    """A heading, two commented paragraphs under it, then another section."""
    query_note = FakeAnnotation("Aleksandr", "Термин – не переводится")
    mutation_note = FakeAnnotation("Ревьюер", "Уточнить", resolved=True)
    stray_note = FakeAnnotation("Aleksandr", "Не про этот раздел")
    doc = writer_doc(
        ["GraphQL", "The Query type", "query is the entry point",
         "mutation writes data", "Errors", "unrelated text"],
        caret=(2, 0),
        selection_spans=[((2, 0), (2, 5))],
        styles=["Heading 1", "Heading 2", "Standard", "Standard",
                "Heading 2", "Standard"],
        outline_levels=[1, 2, 0, 0, 2, 0],
        portions={
            2: [{"kind": "Annotation", "text": "", "field": query_note},
                {"text": "query", "locale": EN},
                {"kind": "AnnotationEnd", "text": ""},
                {"text": " is the entry point", "locale": EN}],
            3: [{"kind": "Annotation", "text": "", "field": mutation_note},
                {"text": "mutation", "locale": EN},
                {"kind": "AnnotationEnd", "text": ""},
                {"text": " writes data", "locale": EN}],
            5: [{"kind": "Annotation", "text": "", "field": stray_note},
                {"text": "unrelated", "locale": EN},
                {"kind": "AnnotationEnd", "text": ""},
                {"text": " text", "locale": EN}],
        })
    return doc


@pytest.fixture
def section_doc():
    return commented_section()


def test_lists_every_comment_of_the_document(bridge, section_doc):
    listed = bridge.list_comments(doc=section_doc)

    assert listed["count"] == 3
    assert [c["content"] for c in listed["comments"]] == [
        "Термин – не переводится", "Уточнить", "Не про этот раздел"]
    assert [c["address"]["paragraph"] for c in listed["comments"]] == [2, 3, 5]


def test_reports_the_identity_and_state_of_a_comment(bridge, section_doc):
    first = bridge.list_comments(doc=section_doc)["comments"][0]

    assert first["id"]                       # names one comment, for editing
    assert first["resolved"] is False
    assert first["date"] == "2026-09-08T09:32:29"
    assert first["reply_to"] is None
    assert bridge.list_comments(doc=section_doc)["comments"][1]["resolved"] is True


def test_lists_the_comments_of_a_section(bridge, section_doc):
    listed = bridge.list_comments({"heading": 1}, doc=section_doc)

    assert [c["content"] for c in listed["comments"]] == [
        "Термин – не переводится", "Уточнить"]
    assert listed["scope"] == {"heading": 1, "paragraphs": [1, 3]}


def test_a_section_of_a_higher_level_holds_the_lot(bridge, section_doc):
    listed = bridge.list_comments({"heading": 0}, doc=section_doc)

    assert listed["count"] == 3


def test_lists_the_comments_of_one_paragraph(bridge, section_doc):
    assert bridge.list_comments({"paragraph": 3}, doc=section_doc)["count"] == 1
    assert bridge.list_comments({"paragraph": 4}, doc=section_doc)["count"] == 0


def test_lists_the_comments_overlapping_a_range(bridge, section_doc):
    on_the_term = bridge.list_comments({"paragraph": 2, "offset": 0,
                                        "length": 5}, doc=section_doc)
    past_it = bridge.list_comments({"paragraph": 2, "offset": 6, "length": 10},
                                   doc=section_doc)

    assert on_the_term["count"] == 1
    assert past_it["count"] == 0


def test_lists_the_comments_of_the_selection(bridge, section_doc):
    listed = bridge.list_comments({"selection": True}, doc=section_doc)

    assert listed["count"] == 1
    assert listed["comments"][0]["content"] == "Термин – не переводится"


def test_an_unknown_section_is_refused(bridge, section_doc):
    refused = bridge.list_comments({"heading": 2}, doc=section_doc)

    assert refused["success"] is False
    assert "heading" in refused["error"].lower()


# --- editing and deleting ----------------------------------------------------

def test_edits_the_text_of_a_comment(bridge, section_doc):
    target = bridge.list_comments(doc=section_doc)["comments"][0]

    changed = bridge.update_comment(target["id"], text="Оставить как есть",
                                    doc=section_doc)

    assert changed["success"] is True
    assert changed["changed"] == ["text"]
    after = bridge.list_comments(doc=section_doc)["comments"][0]
    assert after["content"] == "Оставить как есть"
    assert after["author"] == "Aleksandr"          # untouched
    assert after["id"] == target["id"]             # still the same comment


def test_resolves_a_comment_without_touching_its_text(bridge, section_doc):
    target = bridge.list_comments(doc=section_doc)["comments"][0]

    changed = bridge.update_comment(target["id"], resolved=True, doc=section_doc)

    assert changed["changed"] == ["resolved"]
    after = bridge.list_comments(doc=section_doc)["comments"][0]
    assert after["resolved"] is True
    assert after["content"] == "Термин – не переводится"


def test_editing_a_comment_leaves_the_text_it_is_anchored_to(bridge, section_doc):
    target = bridge.list_comments(doc=section_doc)["comments"][0]

    bridge.update_comment(target["id"], text="иначе", author="Клод",
                          doc=section_doc)

    assert bridge.read_paragraphs(start=2, count=1,
                                  doc=section_doc)["paragraphs"][0]["text"] \
        == "query is the entry point"
    after = bridge.list_comments(doc=section_doc)["comments"][0]
    assert after["author"] == "Клод"


def test_editing_needs_something_to_change(bridge, section_doc):
    target = bridge.list_comments(doc=section_doc)["comments"][0]

    refused = bridge.update_comment(target["id"], doc=section_doc)

    assert refused["success"] is False
    assert "nothing" in refused["error"].lower()


def test_editing_an_unknown_comment_is_refused(bridge, section_doc):
    refused = bridge.update_comment("__Annotation__nope", text="x",
                                    doc=section_doc)

    assert refused["success"] is False
    assert "__Annotation__nope" in refused["error"]


def test_deletes_a_comment_and_keeps_its_text(bridge, section_doc):
    target = bridge.list_comments(doc=section_doc)["comments"][0]

    removed = bridge.delete_comment(target["id"], doc=section_doc)

    assert removed["success"] is True
    assert removed["content"] == "Термин – не переводится"
    remaining = bridge.list_comments(doc=section_doc)
    assert remaining["count"] == 2
    assert target["id"] not in [c["id"] for c in remaining["comments"]]
    assert bridge.read_paragraphs(start=2, count=1,
                                  doc=section_doc)["paragraphs"][0]["text"] \
        == "query is the entry point"
    assert bridge.read_runs({"paragraph": 2}, doc=section_doc)["runs"][0][
        "comments"] == []


def test_deleting_an_unknown_comment_is_refused(bridge, section_doc):
    refused = bridge.delete_comment("__Annotation__nope", doc=section_doc)

    assert refused["success"] is False
    assert "__Annotation__nope" in refused["error"]


def test_the_editing_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = commented_section()
    server.uno_bridge.desktop = FakeDesktop([doc])

    listed = asyncio.run(server.execute_tool("list_comments_live",
                                             {"address": {"heading": 1}}))
    assert listed["count"] == 2

    changed = asyncio.run(server.execute_tool(
        "update_comment_live", {"comment_id": listed["comments"][0]["id"],
                                "resolved": True}))
    assert changed["success"] is True

    removed = asyncio.run(server.execute_tool(
        "delete_comment_live", {"comment_id": listed["comments"][0]["id"]}))
    assert removed["success"] is True
    assert asyncio.run(server.execute_tool("list_comments_live", {}))["count"] == 2


def test_a_comment_the_tool_adds_can_be_edited_and_deleted(bridge, section_doc):
    """Writer leaves Name empty on an annotation made through the API, so a
    comment the tool added was unaddressable until the bridge named it."""
    added = bridge.add_comment({"paragraph": 4}, "новое замечание",
                               author="Клод", doc=section_doc)

    assert added["success"] is True
    assert added["id"]

    listed = bridge.list_comments({"paragraph": 4}, doc=section_doc)
    assert listed["count"] == 1
    assert listed["comments"][0]["id"] == added["id"]

    assert bridge.update_comment(added["id"], text="иначе",
                                 doc=section_doc)["success"] is True
    assert bridge.delete_comment(added["id"], doc=section_doc)["success"] is True
    assert bridge.list_comments({"paragraph": 4}, doc=section_doc)["count"] == 0


def test_a_comment_the_tool_adds_is_dated(bridge, section_doc):
    """An annotation made through the API carries a zeroed date, which shows
    in Writer's margin as no date at all."""
    added = bridge.add_comment({"paragraph": 4}, "новое", doc=section_doc)

    listed = bridge.list_comments({"paragraph": 4}, doc=section_doc)["comments"]
    assert listed[0]["date"] is not None
    assert listed[0]["date"][:2] == "20"
    assert added["success"] is True
