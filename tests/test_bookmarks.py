"""Tests for bookmarks: the names a document keeps for places.

The other half of the trade an anchor makes. An anchor is a held cursor and
lives as long as the server; a bookmark is saved in the file, comes back when
the document is reopened, and shows in the Navigator. Measured on a real
Writer: it moves with its text, it survives a rewrite of the very words it
covers — where a comment or a field would go with them — and a name that is
already taken is *not* refused by Writer, which quietly mints "name Copy 1".
"""

import pytest

from tests.fake_writer import writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Введение", "query is the entry point", "Конец"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def test_a_bookmark_covers_the_text_it_was_put_on(bridge, doc):
    made = bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5},
                               "запрос", doc=doc)

    assert made["success"] is True
    assert made["name"] == "запрос"
    assert made["text"] == "query"
    assert made["address"]["paragraph"] == 1
    assert made["is_a_point"] is False


def test_a_bookmark_at_a_caret_marks_a_spot(bridge, doc):
    made = bridge.add_bookmark({"paragraph": 1, "offset": 6, "length": 0},
                               "здесь", doc=doc)

    assert made["is_a_point"] is True
    assert made["text"] == ""


def test_they_are_listed_in_the_order_they_sit_in(bridge, doc):
    bridge.add_bookmark({"paragraph": 2}, "конец", doc=doc)
    bridge.add_bookmark({"paragraph": 0}, "начало", doc=doc)

    listed = bridge.list_bookmarks(doc=doc)

    assert [one["name"] for one in listed["bookmarks"]] == ["начало", "конец"]
    assert listed["count"] == 2


def test_bookmarks_can_be_asked_for_by_place(bridge, doc):
    bridge.add_bookmark({"paragraph": 0}, "начало", doc=doc)
    bridge.add_bookmark({"paragraph": 2}, "конец", doc=doc)

    assert [one["name"] for one in
            bridge.list_bookmarks({"paragraph": 2}, doc=doc)["bookmarks"]] \
        == ["конец"]


def test_a_name_that_is_taken_is_refused(bridge, doc):
    bridge.add_bookmark({"paragraph": 0}, "тут", doc=doc)

    refused = bridge.add_bookmark({"paragraph": 2}, "тут", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "INVALID_PARAMETER"
    assert "Copy 1" in refused["error"]
    assert bridge.list_bookmarks(doc=doc)["count"] == 1


def test_a_bookmark_needs_a_name(bridge, doc):
    assert bridge.add_bookmark({"paragraph": 0}, "  ",
                               doc=doc)["code"] == "INVALID_PARAMETER"


def test_renaming_leaves_it_where_it_is(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)

    renamed = bridge.rename_bookmark("запрос", "query", doc=doc)

    assert renamed["success"] is True
    listed = bridge.list_bookmarks(doc=doc)["bookmarks"]
    assert [one["name"] for one in listed] == ["query"]
    assert listed[0]["text"] == "query"


def test_renaming_onto_a_taken_name_is_refused(bridge, doc):
    bridge.add_bookmark({"paragraph": 0}, "один", doc=doc)
    bridge.add_bookmark({"paragraph": 2}, "два", doc=doc)

    refused = bridge.rename_bookmark("один", "два", doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")


def test_deleting_one_leaves_the_text(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)

    removed = bridge.delete_bookmark("запрос", doc=doc)

    assert removed["success"] is True
    assert removed["was_on"] == "query"
    assert bridge.list_bookmarks(doc=doc)["count"] == 0
    assert doc.getText().paragraphs[1] == "query is the entry point"


def test_a_bookmark_that_is_not_there(bridge, doc):
    for call in (lambda: bridge.delete_bookmark("нет", doc=doc),
                 lambda: bridge.rename_bookmark("нет", "да", doc=doc)):
        refused = call()
        assert (refused["success"], refused["code"]) == (False, "NOT_FOUND")
