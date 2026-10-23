"""Tests for paragraph surgery: splitting, joining, moving and copying.

The measurement behind the last two: there is no move and no copy on the
model. Writer's own `.uno:MoveDown` carries a paragraph with everything on
it, and the controller's transferable copies one the same way — where
reading a paragraph, deleting it and writing it again somewhere else leaves
its comments and its pictures behind.
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
    return writer_doc(["Заголовок", "Первый абзац", "Второй абзац",
                       "Третий абзац", "Четвёртый абзац"],
                      outline_levels=[1, 0, 0, 0, 0], caret=(1, 0))


def lines(doc):
    return list(doc.getText().paragraphs)


def test_a_paragraph_comes_apart(bridge, doc):
    split = bridge.split_paragraph({"paragraph": 2, "offset": 6}, doc=doc)

    assert split["success"] is True
    assert [one["text"] for one in split["paragraphs"]] == ["Второй", " абзац"]
    assert lines(doc)[2:4] == ["Второй", " абзац"]
    assert split["total_paragraphs"] == 6


def test_and_goes_back_together(bridge, doc):
    bridge.split_paragraph({"paragraph": 2, "offset": 6}, doc=doc)

    merged = bridge.merge_paragraphs({"paragraph": 2}, doc=doc)

    assert (merged["joins"], merged["text"]) == (1, "Второй абзац")
    assert lines(doc)[2] == "Второй абзац"


def test_a_block_joins_into_one(bridge, doc):
    merged = bridge.merge_paragraphs({"paragraph": 2, "through": 4}, doc=doc)

    assert merged["joins"] == 2
    assert merged["text"] == "Второй абзацТретий абзацЧетвёртый абзац"


def test_the_last_paragraph_has_nothing_to_join_to(bridge, doc):
    refused = bridge.merge_paragraphs({"paragraph": 4}, doc=doc)

    assert (refused["success"], refused["code"]) == (False, "INVALID_ADDRESS")


def test_moving_a_paragraph_carries_what_is_on_it(bridge, doc):
    bridge.add_comment({"paragraph": 1, "offset": 0, "length": 6}, "Заметка",
                       doc=doc)

    moved = bridge.move_paragraph({"paragraph": 1}, direction="down", doc=doc)

    assert (moved["success"], moved["to"]) == (True, [2, 2])
    assert lines(doc)[2] == "Первый абзац"
    # By number, since what this is checking is *where* the comment ended up.
    comments = bridge.list_comments(number=True, doc=doc)
    assert comments["count"] == 1
    assert comments["comments"][0]["address"]["paragraph"] == 2  # numbered


def test_moving_to_a_place(bridge, doc):
    was = lines(doc)

    bridge.move_paragraph({"paragraph": 1}, to=3, doc=doc)

    assert lines(doc)[2] == "Первый абзац"
    bridge.move_paragraph({"paragraph": 2}, to=1, doc=doc)
    assert lines(doc) == was


def test_what_moving_refuses(bridge, doc):
    for refused in (bridge.move_paragraph({"paragraph": 1}, doc=doc),
                    bridge.move_paragraph({"paragraph": 1}, direction="up",
                                          to=3, doc=doc),
                    bridge.move_paragraph({"paragraph": 1},
                                          direction="sideways", doc=doc),
                    bridge.move_paragraph({"paragraph": 0}, direction="up",
                                          doc=doc),
                    bridge.move_paragraph({"paragraph": 1}, direction="down",
                                          steps=99, doc=doc),
                    bridge.move_paragraph({"paragraph": 1}, to=1, doc=doc),
                    bridge.move_paragraph({"paragraph": 1}, direction="down",
                                          steps=0, doc=doc)):
        assert (refused["success"], refused["code"]) == (False,
                                                         "INVALID_PARAMETER")


def test_copying_brings_the_comment_with_it(bridge, doc):
    bridge.add_comment({"paragraph": 1, "offset": 0, "length": 6}, "Заметка",
                       doc=doc)

    copied = bridge.copy_paragraphs({"paragraph": 1}, to=4, doc=doc)

    assert (copied["success"], copied["paragraphs"]) == (True, 1)
    assert lines(doc)[4] == "Первый абзац"
    assert lines(doc)[1] == "Первый абзац", "the original went with the copy"
    assert bridge.list_comments(doc=doc)["count"] == 2


def test_a_copy_is_a_paragraph_of_its_own(bridge, doc):
    # Measured on a live Writer: a paste joins the paragraph it lands in
    # instead of becoming one, so a copy made in front of "Третий абзац" used
    # to read back as a single paragraph holding both, and the count never
    # moved. The landing place is given an empty paragraph first, and the copy
    # fills it.
    copied = bridge.copy_paragraphs({"paragraph": 1}, to=3, doc=doc)

    assert copied["success"] is True
    assert lines(doc)[3] == "Первый абзац"
    assert lines(doc)[4] == "Третий абзац", "what it landed in front of"
    assert copied["total_paragraphs"] == len(lines(doc)) == 6


def test_the_last_paragraph_can_be_copied(bridge, doc):
    # It has no break of its own to give — the body's end sits inside it — so
    # this is the case that decided the rule.
    copied = bridge.copy_paragraphs({"paragraph": 4}, to=1, doc=doc)

    assert copied["success"] is True
    assert lines(doc)[1] == "Четвёртый абзац"
    assert lines(doc)[5] == "Четвёртый абзац", "the original stayed put"
    assert copied["total_paragraphs"] == 6


def test_a_copy_can_land_after_the_last_paragraph(bridge, doc):
    # `to` may be the number of paragraphs: the place after the last one,
    # which is what copying to the end of a document means.
    copied = bridge.copy_paragraphs({"paragraph": 1}, to=5, doc=doc)

    assert copied["success"] is True
    assert lines(doc)[5] == "Первый абзац"
    assert lines(doc)[4] == "Четвёртый абзац", "the last one was left alone"
    assert copied["total_paragraphs"] == 6


def test_a_copy_can_land_in_front_of_the_first_paragraph(bridge, doc):
    copied = bridge.copy_paragraphs({"paragraph": 2}, to=0, doc=doc)

    assert copied["success"] is True
    assert lines(doc)[0] == "Второй абзац"
    assert lines(doc)[1] == "Заголовок"
    assert copied["total_paragraphs"] == 6


def test_a_block_lands_in_order_and_shifts_what_is_below(bridge, doc):
    copied = bridge.copy_paragraphs({"paragraph": 1, "through": 2}, to=4,
                                    doc=doc)

    assert copied["paragraphs"] == 2
    assert lines(doc)[4:6] == ["Первый абзац", "Второй абзац"]
    assert lines(doc)[6] == "Четвёртый абзац"
    assert copied["total_paragraphs"] == 7


def test_what_copying_refuses(bridge, doc):
    assert bridge.copy_paragraphs({"paragraph": 1}, to=1,
                                  doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.copy_paragraphs({"paragraph": 1}, to=99,
                                  doc=doc)["code"] == "INVALID_ADDRESS"
    assert bridge.copy_paragraphs({"paragraph": 1}, to=-1,
                                  doc=doc)["code"] == "INVALID_ADDRESS"


def test_surgery_respects_a_protected_section(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                          protected=True, doc=doc)

    for refused in (bridge.split_paragraph({"paragraph": 1, "offset": 3},
                                           doc=doc),
                    bridge.merge_paragraphs({"paragraph": 1}, doc=doc)):
        assert (refused["success"], refused["code"]) == (False, "READ_ONLY")


def test_a_join_works_where_the_break_reads_as_crlf(bridge, doc):
    # Measured on Writer for Windows: a selection holding one paragraph break
    # answers "\r\n", not "\n", and a join that compared with "\n" took out
    # nothing while reporting success.
    doc.getText().break_string = "\r\n"

    merged = bridge.merge_paragraphs({"paragraph": 2, "through": 3}, doc=doc)

    assert merged["success"] is True
    assert merged["joins"] == 1
    assert lines(doc)[2] == "Второй абзацТретий абзац"
