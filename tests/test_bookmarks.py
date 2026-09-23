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

from uno_bridge import AddressError, UNOBridge  # noqa: E402

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
    # A bookmark names itself: the document keeps the place, so there is no
    # number to work out and no session anchor to hold.
    assert made["address"] == {"bookmark": "запрос"}
    assert made["is_a_point"] is False


def test_a_bookmark_at_a_caret_marks_a_spot(bridge, doc):
    made = bridge.add_bookmark({"paragraph": 1, "offset": 6, "length": 0},
                               "здесь", doc=doc)

    assert made["is_a_point"] is True
    assert made["text"] == ""


def test_they_are_listed_in_the_order_they_sit_in(bridge, doc):
    bridge.add_bookmark({"paragraph": 2}, "конец", doc=doc)
    bridge.add_bookmark({"paragraph": 0}, "начало", doc=doc)

    # Reading order is what numbers give, and numbering is a sweep of the
    # body: 195 bookmarks of a real guide cost 20s to number and 0.4s to
    # anchor. So the fast listing is in the document's own order and says so.
    listed = bridge.list_bookmarks(number=True, doc=doc)

    assert [one["name"] for one in listed["bookmarks"]] == ["начало", "конец"]
    assert listed["order"] == "reading"
    assert bridge.list_bookmarks(doc=doc)["order"] \
        == "as the document names them"
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


# --- placed by anchors, not by numbers -------------------------------------
#
# Working out a paragraph number is a sweep of the body — two UNO calls for
# every paragraph of the document — so listing 195 bookmarks of a real guide
# cost 20 seconds. An anchor is two calls for each bookmark.

def test_bookmarks_are_placed_by_anchors_without_counting_paragraphs(
        bridge, doc, monkeypatch):
    bridge.add_bookmark({"paragraph": 1}, "середина", doc=doc)

    def refuse(*arguments, **named):
        raise AssertionError("list_bookmarks counted the paragraphs")

    monkeypatch.setattr(bridge, "_locate_paragraph", refuse)
    monkeypatch.setattr(bridge, "_locate_matches", refuse)

    listed = bridge.list_bookmarks(doc=doc)

    one, = [item for item in listed["bookmarks"] if item["name"] == "середина"]
    assert one["address"] == {"bookmark": "середина"}
    assert bridge._resolve_address(doc, one["address"]).getString() \
        == one["text"]


def test_the_numbers_come_when_they_are_asked_for(bridge, doc):
    bridge.add_bookmark({"paragraph": 1}, "середина", doc=doc)

    one, = [item for item in bridge.list_bookmarks(number=True,
                                                   doc=doc)["bookmarks"]
            if item["name"] == "середина"]

    assert one["address"]["paragraph"] == 1
    assert one["address"]["bookmark"] == "середина"


def test_a_scoped_listing_needs_no_numbers_either(bridge, doc,
                                                   monkeypatch):
    bridge.add_bookmark({"paragraph": 0}, "первая", doc=doc)
    bridge.add_bookmark({"paragraph": 2}, "третья", doc=doc)

    def refuse(*arguments, **named):
        raise AssertionError("a scoped listing counted the paragraphs")

    monkeypatch.setattr(bridge, "_locate_matches", refuse)

    listed = bridge.list_bookmarks(address={"paragraph": 2}, doc=doc)

    assert [one["name"] for one in listed["bookmarks"]] == ["третья"]


# A bookmark is an address, and the one address the *document* keeps. It costs
# two UNO calls to resolve, where a session anchor has to be held one per item
# out of a store a single listing of 195 filled a tenth of.

def test_a_bookmark_is_an_address(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)

    assert bridge._resolve_address(doc, {"bookmark": "запрос"}).getString() \
        == "query"


def test_a_bookmark_address_survives_the_paragraphs_moving(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)
    text = doc.getText()
    text.removeTextContent(text.createEnumeration().nextElement())

    # The number it was on names another paragraph now; the name does not.
    assert bridge._resolve_address(doc, {"bookmark": "запрос"}).getString() \
        == "query"


def test_a_bookmark_address_reaches_the_tools(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)

    bridge.replace_range({"bookmark": "запрос"}, "запрос", doc=doc)

    assert bridge.read_paragraphs(start=1, count=1,
                                  doc=doc)["paragraphs"][0]["text"] \
        == "запрос is the entry point"


def test_a_name_no_bookmark_has_is_refused_by_name(bridge, doc):
    with pytest.raises(AddressError, match="list_bookmarks"):
        bridge._resolve_address(doc, {"bookmark": "нетакой"})


def test_a_bookmark_covers_its_own_stretch_and_takes_no_offset(bridge, doc):
    bridge.add_bookmark({"paragraph": 1, "offset": 0, "length": 5}, "запрос",
                        doc=doc)

    with pytest.raises(AddressError, match="offset"):
        bridge._resolve_address(doc, {"bookmark": "запрос", "offset": 2})


def test_the_listing_holds_no_anchors_at_all(bridge, doc, monkeypatch):
    bridge.add_bookmark({"paragraph": 1}, "строка", doc=doc)

    def refuse(*arguments, **named):
        raise AssertionError("list_bookmarks held a session anchor")

    monkeypatch.setattr(bridge, "_hold_anchor", refuse)

    listed = bridge.list_bookmarks(doc=doc)

    assert [one["address"] for one in listed["bookmarks"]] \
        == [{"bookmark": "строка"}]
