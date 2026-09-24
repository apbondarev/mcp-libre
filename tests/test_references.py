"""Tests for captions and the cross-references that follow them.

A caption's number is a field that counts itself, and a reference is a field
that shows what another place says. The measurements behind these tests are
in plugin/pythonpath/uno_references.py; what is checked here is the
bookkeeping around them — where a caption paragraph lands, which target a
reference is allowed to name, and what a caller is told when the target is
not there.
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
    return writer_doc(["Scalar types", "A picture sits here", "Body text",
                       "See also"],
                      outline_levels=[1, 0, 0, 0], caret=(1, 0))


def paragraphs(doc):
    return list(doc.getText().paragraphs)


def test_a_caption_goes_below_the_paragraph_it_is_about(bridge, doc):
    made = bridge.insert_caption("The first picture", address={"paragraph": 1},
                                 doc=doc)

    assert made["success"] is True
    assert (made["category"], made["number"]) == ("Figure", "1")
    assert made["address"] == {"paragraph": 2}
    assert paragraphs(doc)[2] == "Figure 1: The first picture"
    assert paragraphs(doc)[3] == "Body text", "the text below was not disturbed"


def test_or_above_it(bridge, doc):
    made = bridge.insert_caption("Above", address={"paragraph": 1},
                                 position="above", doc=doc)

    assert made["address"] == {"paragraph": 1}
    assert paragraphs(doc)[2] == "A picture sits here"


def test_the_number_counts_itself(bridge, doc):
    bridge.insert_caption("One", address={"paragraph": 1}, doc=doc)
    second = bridge.insert_caption("Two", address={"paragraph": 3}, doc=doc)

    assert second["number"] == "2"
    # A caption put in front of both is the first, and they renumber.
    bridge.insert_caption("Nought", address={"paragraph": 0}, doc=doc)
    numbers = [one["number"] for one in bridge._captions(doc)]
    assert numbers == ["1", "2", "3"]


def test_each_category_counts_on_its_own(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)
    table = bridge.insert_caption("A table", address={"paragraph": 3},
                                  category="Table", doc=doc)

    assert (table["category"], table["number"]) == ("Table", "1")


def test_a_category_the_document_never_had(bridge, doc):
    made = bridge.insert_caption("Схема", address={"paragraph": 1},
                                 category="Листинг", doc=doc)

    assert (made["success"], made["number"]) == (True, "1")
    assert [one["category"] for one in bridge._captions(doc)] == ["Листинг"]


def test_numbering_can_be_roman(bridge, doc):
    made = bridge.insert_caption("Roman", address={"paragraph": 1},
                                 numbering="roman_upper", doc=doc)

    assert made["number"] == "I"


def test_a_caption_wears_the_style_of_its_category(bridge, doc):
    made = bridge.insert_caption("A picture", address={"paragraph": 1},
                                 doc=doc)

    assert made["paragraph_style"] == "Figure"


def test_what_a_caption_will_not_do(bridge, doc):
    for refused in (
            bridge.insert_caption("x", address={"paragraph": 1},
                                  position="sideways", doc=doc),
            bridge.insert_caption("x", address={"paragraph": 1},
                                  numbering="cuneiform", doc=doc),
            bridge.insert_caption("x", address={"paragraph": 1}, category=" ",
                                  doc=doc),
            bridge.insert_caption("x", doc=doc),
            bridge.insert_caption("x", address={"paragraph": 1}, table="T",
                                  doc=doc)):
        assert refused["success"] is False
        assert refused["code"] == "INVALID_PARAMETER"


def test_the_targets_a_reference_can_name(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)
    bridge.add_bookmark({"paragraph": 3, "offset": 0, "length": 3}, "Место",
                        doc=doc)

    listed = bridge.list_reference_targets(doc=doc)

    kinds = {one["kind"] for one in listed["targets"]}
    assert kinds == {"heading", "caption", "bookmark"}
    heading = [one for one in listed["targets"] if one["kind"] == "heading"][0]
    assert heading["reference"] == {"heading": 0}
    caption = [one for one in listed["targets"] if one["kind"] == "caption"][0]
    assert caption["reference"] == {"caption": "Figure 1"}
    assert caption["text"] == "Figure 1: A picture"


def test_a_reference_to_a_caption_shows_its_number(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)

    put = bridge.insert_cross_reference({"paragraph": 4, "offset": 0,
                                         "length": 0},
                                        {"caption": "Figure 1"}, doc=doc)

    assert (put["success"], put["shows"]) == (True, "Figure 1")
    assert put["part"] == "category_and_number"


def test_or_the_words_of_the_caption(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)

    put = bridge.insert_cross_reference({"paragraph": 4, "offset": 0,
                                         "length": 0},
                                        {"caption": "Figure 1"},
                                        part="caption_text", doc=doc)

    assert put["shows"] == "A picture"


def test_a_reference_to_a_heading_leaves_a_bookmark_on_it(bridge, doc):
    put = bridge.insert_cross_reference({"paragraph": 3, "offset": 0,
                                         "length": 0}, {"heading": 0},
                                        doc=doc)

    assert (put["success"], put["shows"]) == (True, "Scalar types")
    assert put["target"]["name"] == "Scalar types"
    again = bridge.insert_cross_reference({"paragraph": 3, "offset": 0,
                                           "length": 0}, {"heading": 0},
                                          doc=doc)
    assert again["target"]["name"] == "Scalar types", "a second bookmark"
    assert bridge.list_bookmarks(doc=doc)["count"] == 1


def test_a_heading_target_is_taken_by_the_address_it_was_listed_with(bridge):
    # Numbering the headings of a document is a walk of the body — 4.3s on a
    # real guide — so the listing searches for the heading styles and hands
    # out anchors, and a reference made through one must reach the same
    # heading.
    doc = writer_doc(["Scalar types", "Body text", "See also"],
                     styles=["Heading 1", "Standard", "Standard"],
                     outline_levels=[1, 0, 0], caret=(1, 0))

    target, = bridge.list_reference_targets(kinds=["heading"],
                                            doc=doc)["targets"]

    assert target["text"] == "Scalar types"
    assert target["address"]["anchor"]["type"] == "paragraph"

    put = bridge.insert_cross_reference({"paragraph": 2, "offset": 0,
                                         "length": 0}, target["reference"],
                                        doc=doc)

    assert (put["success"], put["shows"]) == (True, "Scalar types")
    assert bridge.list_bookmarks(doc=doc)["count"] == 1


def test_the_listing_is_paged_because_each_heading_is_held(bridge):
    # Measured: a run that filled the anchor store and then evicted from it
    # took a headless office down, SIGABRT inside libuno_cppu. A listing of
    # 938 headings held 938 anchors.
    lines, styles, levels = [], [], []
    for number in range(6):
        lines += [f"Заголовок {number}", "Текст"]
        styles += ["Heading 1", "Standard"]
        levels += [1, 0]
    doc = writer_doc(lines, caret=(0, 0), styles=styles, outline_levels=levels)

    page = bridge.list_reference_targets(kinds=["heading"], count=2, doc=doc)

    assert (page["count"], page["total"], page["more"]) == (2, 6, True)
    assert all(one["address"]["anchor"]["type"] == "paragraph"
               for one in page["targets"])
    rest = bridge.list_reference_targets(kinds=["heading"], start=2,
                                         count=100, doc=doc)
    assert (rest["count"], rest["more"]) == (4, False)
    # Nothing outside the window is anchored, and nothing leaks the range it
    # was found by.
    assert all("_range" not in one for one in rest["targets"])


def test_the_numbers_of_the_targets_come_when_asked_for(bridge, doc):
    listed = bridge.list_reference_targets(kinds=["heading"], number=True,
                                           doc=doc)

    assert [one["reference"] for one in listed["targets"]][:1] \
        == [{"heading": 0}]


def test_ordinary_text_is_not_a_heading(bridge, doc):
    refused = bridge.insert_cross_reference({"paragraph": 3, "offset": 0,
                                             "length": 0}, {"heading": 2},
                                            doc=doc)

    assert (refused["success"], refused["code"]) == (False, "INVALID_PARAMETER")
    assert "add_bookmark" in refused["error"]


def test_a_target_that_is_not_there_is_refused_before_anything_is_written(
        bridge, doc):
    spot = {"paragraph": 3, "offset": 0, "length": 0}
    was = list(paragraphs(doc))

    for refused in (bridge.insert_cross_reference(spot, {"caption": "Figure 9"},
                                                  doc=doc),
                    bridge.insert_cross_reference(spot, {"bookmark": "нет"},
                                                  doc=doc),
                    bridge.insert_cross_reference(spot,
                                                  {"reference_mark": "нет"},
                                                  doc=doc)):
        assert (refused["success"], refused["code"]) == (False, "NOT_FOUND")
    assert paragraphs(doc) == was


def test_a_target_has_to_be_named_in_one_way(bridge, doc):
    for target in ({}, {"bookmark": "a", "heading": 0}, {"nonsense": 1}):
        refused = bridge.insert_cross_reference(
            {"paragraph": 3, "offset": 0, "length": 0}, target, doc=doc)
        assert (refused["success"], refused["code"]) == (False,
                                                         "INVALID_PARAMETER")


def test_a_part_nobody_knows(bridge, doc):
    bridge.add_bookmark({"paragraph": 3, "offset": 0, "length": 3}, "Место",
                        doc=doc)

    refused = bridge.insert_cross_reference(
        {"paragraph": 3, "offset": 0, "length": 0}, {"bookmark": "Место"},
        part="colour", doc=doc)

    assert (refused["success"], refused["code"]) == (False, "INVALID_PARAMETER")


def test_references_are_listed_with_what_they_point_at(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)
    bridge.add_bookmark({"paragraph": 4, "offset": 0, "length": 3}, "Место",
                        doc=doc)
    spot = {"paragraph": 4, "offset": 0, "length": 0}
    bridge.insert_cross_reference(spot, {"caption": "Figure 1"}, doc=doc)
    bridge.insert_cross_reference(spot, {"bookmark": "Место"}, doc=doc)

    listed = bridge.list_references(doc=doc)

    assert (listed["count"], listed["broken"]) == (2, 0)
    assert sorted(one["target"]["kind"] for one in listed["references"]) == \
        ["bookmark", "caption"]
    assert sorted(one["part"] for one in listed["references"]) == \
        ["category_and_number", "text"]


def test_a_reference_whose_target_went_is_reported_broken(bridge, doc):
    bridge.add_bookmark({"paragraph": 3, "offset": 0, "length": 3}, "Место",
                        doc=doc)
    bridge.insert_cross_reference({"paragraph": 3, "offset": 0, "length": 0},
                                  {"bookmark": "Место"}, doc=doc)

    bridge.delete_bookmark("Место", doc=doc)
    listed = bridge.list_references(doc=doc)

    assert listed["broken"] == 1
    assert listed["references"][0]["target"]["name"] == "Место"
    assert listed["references"][0]["shows"] == \
        "Error: Reference source not found"


def test_listing_is_scoped_like_the_comments(bridge, doc):
    bridge.insert_caption("A picture", address={"paragraph": 1}, doc=doc)
    bridge.insert_caption("Another", address={"paragraph": 3}, doc=doc)

    scoped = bridge.list_reference_targets(kinds=["caption"],
                                           address={"paragraph": 2}, doc=doc)

    assert scoped["count"] == 1
    assert scoped["targets"][0]["number"] == "1"


def test_a_kind_nobody_knows(bridge, doc):
    refused = bridge.list_reference_targets(kinds=["footnote"], doc=doc)

    assert (refused["success"], refused["code"]) == (False, "INVALID_PARAMETER")
