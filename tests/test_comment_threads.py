"""Tests for review conversations: a comment, and the replies hanging off it.

Writer joins a reply to its parent with the annotation's ParentName, which
survives saving as loext:parent-name — measured. A reply sits on its parent's
own anchor, so a thread arrives as several comments over one stretch of text.
Two things about that were measured and are guarded here: deleting a parent
leaves its replies in the margin pointing at nothing, and re-anchoring a
comment gives it a new id, which would leave its replies pointing at the old
one.
"""

import pytest

from tests.fake_writer import FakeAnnotation, FakeLocale, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

EN = FakeLocale("en", "US")
PARENT = FakeAnnotation("Reviewer", "Is this the right term?")
COMMENTED = [
    {"kind": "Annotation", "text": "", "field": PARENT},
    {"text": "query", "locale": EN},
    {"kind": "AnnotationEnd", "text": ""},
    {"text": " is the entry point", "locale": EN},
]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Heading", "query is the entry point"], caret=(1, 0),
                      portions={1: dict(enumerate(COMMENTED)) and
                                [dict(piece) for piece in COMMENTED]})


def only_comment(bridge, doc):
    return bridge.list_comments(doc=doc)["comments"][0]


def test_a_reply_goes_on_its_parents_anchor(bridge, doc):
    parent = only_comment(bridge, doc)

    replied = bridge.add_comment(text="Yes — the spec uses it.",
                                 author="Claude", reply_to=parent["id"],
                                 doc=doc)

    assert replied["success"] is True
    assert replied["reply_to"] == parent["id"]
    assert replied["anchor_text"] == "query"


def test_a_reply_needs_no_address_and_refuses_one(bridge, doc):
    parent = only_comment(bridge, doc)

    refused = bridge.add_comment(address={"paragraph": 0}, text="No",
                                 reply_to=parent["id"], doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "INVALID_PARAMETER"
    assert "its parent's own anchor" in refused["error"]


def test_replying_to_a_comment_that_is_not_there(bridge, doc):
    refused = bridge.add_comment(text="Hello?", reply_to="__Annotation__no",
                                 doc=doc)

    assert (refused["success"], refused["code"]) == (False, "NOT_FOUND")


def test_a_comment_needs_somewhere_to_go(bridge, doc):
    refused = bridge.add_comment(text="Where?", doc=doc)

    assert (refused["success"], refused["code"]) == (False, "INVALID_PARAMETER")
    assert "reply_to" in refused["error"]


def test_listing_shows_the_thread(bridge, doc):
    parent = only_comment(bridge, doc)
    bridge.add_comment(text="Yes.", author="Claude", reply_to=parent["id"],
                       doc=doc)

    listed = bridge.list_comments(doc=doc)

    assert (listed["count"], listed["threads"], listed["replies"]) == (2, 1, 1)
    top = [one for one in listed["comments"] if not one["reply_to"]][0]
    reply = [one for one in listed["comments"] if one["reply_to"]][0]
    assert top["replies"] == [reply["id"]]
    assert reply["reply_to"] == top["id"]
    assert reply["replies"] == []


def test_a_reply_to_a_reply_is_a_chain(bridge, doc):
    parent = only_comment(bridge, doc)
    first = bridge.add_comment(text="Yes.", reply_to=parent["id"], doc=doc)
    second = bridge.add_comment(text="Agreed.", reply_to=first["id"], doc=doc)

    listed = {one["id"]: one for one in bridge.list_comments(doc=doc)["comments"]}

    assert listed[first["id"]]["replies"] == [second["id"]]
    assert listed[second["id"]]["reply_to"] == first["id"]


def test_deleting_a_parent_alone_is_refused(bridge, doc):
    parent = only_comment(bridge, doc)
    reply = bridge.add_comment(text="Yes.", reply_to=parent["id"], doc=doc)

    refused = bridge.delete_comment(parent["id"], doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "INVALID_PARAMETER"
    assert refused["replies"] == [reply["id"]]
    assert bridge.list_comments(doc=doc)["count"] == 2


def test_the_whole_thread_can_be_taken(bridge, doc):
    parent = only_comment(bridge, doc)
    reply = bridge.add_comment(text="Yes.", reply_to=parent["id"], doc=doc)
    deeper = bridge.add_comment(text="Agreed.", reply_to=reply["id"], doc=doc)

    removed = bridge.delete_comment(parent["id"], with_replies=True, doc=doc)

    assert removed["success"] is True
    assert sorted(removed["replies_deleted"]) == sorted([reply["id"],
                                                          deeper["id"]])
    assert bridge.list_comments(doc=doc)["count"] == 0


def test_a_reply_can_be_deleted_on_its_own(bridge, doc):
    parent = only_comment(bridge, doc)
    reply = bridge.add_comment(text="Yes.", reply_to=parent["id"], doc=doc)

    removed = bridge.delete_comment(reply["id"], doc=doc)

    assert removed["success"] is True
    left = bridge.list_comments(doc=doc)
    assert (left["count"], left["threads"]) == (1, 1)
    assert left["comments"][0]["replies"] == []


def test_making_a_parent_again_keeps_its_replies(bridge, doc):
    parent = only_comment(bridge, doc)
    reply = bridge.add_comment(text="Yes.", reply_to=parent["id"], doc=doc)

    # A language can only be given to a new note, so the parent is made
    # again — with a new id the reply would otherwise be left pointing at.
    remade = bridge.update_comment(parent["id"], language="ru-RU", doc=doc)

    assert remade["recreated"] is True
    assert remade["replies_repointed"] == [reply["id"]]
    listed = {one["id"]: one for one in bridge.list_comments(doc=doc)["comments"]}
    assert listed[reply["id"]]["reply_to"] == remade["id"]
    assert listed[remade["id"]]["replies"] == [reply["id"]]


def test_a_thread_survives_a_rewrite_of_the_text_it_sits_on(bridge, doc):
    parent = only_comment(bridge, doc)
    bridge.add_comment(text="Yes.", author="Claude", reply_to=parent["id"],
                       doc=doc)

    runs = bridge.read_runs({"paragraph": 1}, doc=doc)
    translated = [dict(run, text="запрос" if run["text"] == "query"
                       else run["text"]) for run in runs["runs"]]
    written = bridge.replace_runs({"paragraph": 1}, translated, doc=doc)

    assert written["success"] is True
    listed = bridge.list_comments(doc=doc)
    assert (listed["threads"], listed["replies"]) == (1, 1)
    top = [one for one in listed["comments"] if not one["reply_to"]][0]
    reply = [one for one in listed["comments"] if one["reply_to"]][0]
    assert reply["reply_to"] == top["id"], "the reply lost its parent"
    assert top["content"] == "Is this the right term?"
