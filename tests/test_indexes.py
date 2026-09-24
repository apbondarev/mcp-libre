"""Tests for the indexes a document writes about itself.

The measurement behind all of them is that an index's entries are body
paragraphs: inserting one moves the text below it, and so does updating it.
See plugin/pythonpath/uno_indexes.py for the rest of what was measured.
"""

import pytest

from tests.fake_writer import writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Introduction", "Intro body with GraphQL",
                       "Scalar types", "Scalars body", "Conclusion",
                       "The end"],
                      outline_levels=[1, 0, 2, 0, 1, 0], caret=(1, 0))


def lines(doc):
    return list(doc.getText().paragraphs)


def test_a_table_of_contents_writes_itself(bridge, doc):
    made = bridge.insert_index({"paragraph": 0}, title="Содержание", doc=doc)

    assert made["success"] is True
    assert (made["kind"], made["title"]) == ("contents", "Содержание")
    assert made["entries"] == 3
    assert lines(doc)[:4] == ["Содержание", "Introduction\t1",
                              "Scalar types\t1", "Conclusion\t1"]


def test_and_says_how_far_it_pushed_the_text_down(bridge, doc):
    made = bridge.insert_index({"paragraph": 0}, doc=doc)

    assert made["paragraphs_added"] == 4
    assert lines(doc)[4] == "Introduction", "the text moved down by four"


def test_it_is_built_from_the_headings_and_not_from_marks(bridge, doc):
    # UNO's own default is the other way round, which lists nothing at all.
    made = bridge.insert_index({"paragraph": 0}, doc=doc)

    assert (made["from_outline"], made["from_marks"]) == (True, False)


def test_only_as_many_levels_as_asked_for(bridge, doc):
    made = bridge.insert_index({"paragraph": 0}, levels=1, doc=doc)

    assert made["entries"] == 2
    assert "Scalar types\t1" not in lines(doc)[:3]


def test_an_entry_is_stale_until_the_index_is_updated(bridge, doc):
    bridge.insert_index({"paragraph": 0}, doc=doc)
    bridge.replace_range({"paragraph": 4}, "Введение", flatten=True, doc=doc)

    assert lines(doc)[1] == "Introduction\t1", "the entry followed by itself"

    updated = bridge.update_indexes(all=True, doc=doc)

    assert updated["success"] is True
    assert updated["updated"] == ["Table of Contents1"]
    assert lines(doc)[1] == "Введение\t1"


def test_updating_says_whether_the_addresses_moved(bridge, doc):
    bridge.insert_index({"paragraph": 0}, doc=doc)
    # A heading more means an entry more, and everything below it moves.
    bridge.apply_paragraph_style({"paragraph": 5}, "Heading 1", doc=doc)

    updated = bridge.update_indexes(all=True, doc=doc)

    assert updated["paragraphs_moved"] == 1
    assert updated["paragraphs_after"] - updated["paragraphs_before"] == 1


def test_an_index_is_picked_in_exactly_one_way(bridge, doc):
    bridge.insert_index({"paragraph": 0}, doc=doc)

    for refused in (bridge.update_indexes(doc=doc),
                    bridge.update_indexes(name="Table of Contents1", all=True,
                                          doc=doc)):
        assert (refused["success"], refused["code"]) == (False,
                                                         "INVALID_PARAMETER")
    assert bridge.update_indexes(name="Нетакой", doc=doc)["code"] == "NOT_FOUND"


def test_listing_says_what_each_index_is(bridge, doc):
    bridge.insert_index({"paragraph": 0}, title="Содержание", doc=doc)

    listed = bridge.list_indexes(number=True, doc=doc)

    assert listed["count"] == 1
    only = listed["indexes"][0]
    assert (only["kind"], only["title"], only["entries"]) == ("contents",
                                                              "Содержание", 3)
    assert only["protected"] is True
    assert only["address"]["paragraph"] == 0


def test_marking_a_word_leaves_the_word(bridge, doc):
    marked = bridge.add_index_mark({"paragraph": 1, "offset": 16,
                                    "length": 7}, "GraphQL", doc=doc)

    assert (marked["success"], marked["marked"]) == (True, "GraphQL")
    assert lines(doc)[1] == "Intro body with GraphQL"


def test_an_alphabetical_index_lists_what_was_marked(bridge, doc):
    bridge.add_index_mark({"paragraph": 1, "offset": 16, "length": 7},
                          "GraphQL", doc=doc)

    made = bridge.insert_index({"paragraph": 5}, kind="alphabetical",
                               title="Указатель", doc=doc)

    assert (made["success"], made["kind"]) == (True, "alphabetical")
    assert lines(doc)[6].startswith("GraphQL")


def test_removing_an_index_takes_its_paragraphs(bridge, doc):
    bridge.insert_index({"paragraph": 0}, doc=doc)

    gone = bridge.delete_index("Table of Contents1", doc=doc)

    assert (gone["success"], gone["paragraphs_removed"]) == (True, 4)
    assert lines(doc)[0] == "Introduction"
    assert bridge.list_indexes(number=True, doc=doc)["count"] == 0


def test_what_the_index_tools_refuse(bridge, doc):
    assert bridge.insert_index({"paragraph": 0}, kind="gossip",
                               doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.insert_index({"paragraph": 0}, levels=99,
                               doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.insert_index({"paragraph": 99},
                               doc=doc)["code"] == "INVALID_ADDRESS"
    assert bridge.add_index_mark({"paragraph": 0}, "  ",
                                 doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.delete_index("Нетакой", doc=doc)["code"] == "NOT_FOUND"


def test_two_indexes_keep_their_places(bridge, doc):
    bridge.insert_index({"paragraph": 0}, doc=doc)
    bridge.add_index_mark({"paragraph": 5, "offset": 16, "length": 7},
                          "GraphQL", doc=doc)
    total = bridge.read_paragraphs(start=0, count=1, number=True,
                                   doc=doc)["total_paragraphs"]
    bridge.insert_index({"paragraph": total - 1}, kind="alphabetical", doc=doc)

    listed = bridge.list_indexes(number=True, doc=doc)

    assert [one["kind"] for one in listed["indexes"]] == ["contents",
                                                          "alphabetical"]
    # The one below did not disturb the one above it.
    assert listed["indexes"][0]["address"]["paragraph"] == 0
