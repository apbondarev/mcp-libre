"""Tests for sections, and for the protection UNO does not enforce.

Measured on a live Writer: a section leaves the paragraph numbering exactly
as it was, and `IsProtected` stops the reader's keyboard and nothing else —
`replace_range` and a bare `setString` both wrote through it. So the refusal
belongs to this server, and these tests are what hold it in place.
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
    return writer_doc(["First paragraph", "Inside one", "Inside two",
                       "After the section", "Last line"], caret=(0, 0))


def lines(doc):
    return list(doc.getText().paragraphs)


def test_a_section_covers_paragraphs_and_moves_nothing(bridge, doc):
    before = lines(doc)

    made = bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                                 doc=doc)

    assert made["success"] is True
    assert made["text"] == "Inside one\nInside two"
    assert lines(doc) == before, "a section moved the text"
    assert made["address"]["paragraph"] == 1


def test_a_name_is_needed_and_has_to_be_free(bridge, doc):
    bridge.create_section({"paragraph": 1}, "Правила", doc=doc)

    assert bridge.create_section({"paragraph": 0}, "Правила",
                                 doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.create_section({"paragraph": 0}, "   ",
                                 doc=doc)["code"] == "INVALID_PARAMETER"


def test_a_protected_section_refuses_the_writing_tools(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                          protected=True, doc=doc)

    refused = bridge.replace_range({"paragraph": 1}, "ПЕРЕПИСАНО",
                                   flatten=True, doc=doc)

    assert (refused["success"], refused["code"]) == (False, "READ_ONLY")
    assert refused["section"] == "Правила"
    assert lines(doc)[1] == "Inside one"
    assert bridge.replace_runs({"paragraph": 1}, [{"text": "x"}],
                               doc=doc)["code"] == "READ_ONLY"


def test_text_outside_a_protected_section_is_untouched_by_the_guard(bridge,
                                                                    doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                          protected=True, doc=doc)

    assert bridge.replace_range({"paragraph": 0}, "Первый", flatten=True,
                                doc=doc)["success"] is True
    assert bridge.replace_range({"paragraph": 3}, "Позже", flatten=True,
                                doc=doc)["success"] is True


def test_saying_you_mean_it_writes_anyway(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                          protected=True, doc=doc)

    written = bridge.replace_range({"paragraph": 1}, "ВСЁ РАВНО", flatten=True,
                                   allow_protected=True, doc=doc)

    assert written["success"] is True
    assert lines(doc)[1] == "ВСЁ РАВНО"


def test_unprotecting_gives_the_text_back(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила",
                          protected=True, doc=doc)

    changed = bridge.update_section("Правила", protected=False, doc=doc)

    assert (changed["success"], changed["protected"]) == (True, False)
    assert changed["was"]["protected"] is True
    assert bridge.replace_range({"paragraph": 1}, "Теперь можно", flatten=True,
                                doc=doc)["success"] is True


def test_a_hidden_section_is_still_read_and_still_numbered(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила", doc=doc)

    hidden = bridge.update_section("Правила", visible=False, doc=doc)

    assert hidden["visible"] is False
    read = bridge.read_paragraphs(start=1, count=1, doc=doc)
    assert read["paragraphs"][0]["text"] == "Inside one"


def test_columns_renaming_and_what_is_refused(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Правила", doc=doc)

    assert bridge.update_section("Правила", columns=2, doc=doc)["columns"] == 2
    assert bridge.update_section("Правила", columns=0,
                                 doc=doc)["code"] == "INVALID_PARAMETER"
    renamed = bridge.update_section("Правила", new_name="Раздел", doc=doc)
    assert (renamed["name"], renamed["was"]["name"]) == ("Раздел", "Правила")
    assert bridge.update_section("Раздел", doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.update_section("Нетакой", visible=True,
                                 doc=doc)["code"] == "NOT_FOUND"


def test_a_section_with_no_columns_of_its_own_counts_as_one(bridge, doc):
    # Measured: TextColumns answers 0 on a section nobody has given columns
    # to, which is not a number of columns anybody means.
    made = bridge.create_section({"paragraph": 1}, "Правила", doc=doc)

    assert made["columns"] == 1


def test_sections_nest_and_say_so(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Раздел", doc=doc)
    bridge.create_section({"paragraph": 2}, "Вложенный", doc=doc)

    listed = bridge.list_sections(doc=doc)

    assert [one["name"] for one in listed["sections"]] == ["Раздел",
                                                            "Вложенный"]
    assert [one["inside"] for one in listed["sections"]] == [None, "Раздел"]
    assert listed["sections"][0]["holds"] == ["Вложенный"]


def test_listing_by_paragraph_asks_which_sections_cover_it(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Раздел", doc=doc)
    bridge.create_section({"paragraph": 2}, "Вложенный", doc=doc)

    # The outer section starts in paragraph 1 and covers 2 as well.
    assert bridge.list_sections({"paragraph": 2}, doc=doc)["count"] == 2
    assert bridge.list_sections({"paragraph": 0}, doc=doc)["count"] == 0


def test_counting_what_is_protected_and_hidden(bridge, doc):
    bridge.create_section({"paragraph": 1}, "Один", protected=True, doc=doc)
    bridge.create_section({"paragraph": 3}, "Два", visible=False, doc=doc)

    listed = bridge.list_sections(doc=doc)

    assert (listed["count"], listed["protected"], listed["hidden"]) == (2, 1, 1)


def test_removing_a_section_keeps_its_paragraphs(bridge, doc):
    bridge.create_section({"paragraph": 1, "through": 2}, "Раздел", doc=doc)
    before = lines(doc)

    gone = bridge.delete_section("Раздел", doc=doc)

    assert (gone["success"], gone["kept_text"]) == (True,
                                                    "Inside one\nInside two")
    assert lines(doc) == before
    assert bridge.list_sections(doc=doc)["count"] == 0
    assert bridge.delete_section("Раздел", doc=doc)["code"] == "NOT_FOUND"
