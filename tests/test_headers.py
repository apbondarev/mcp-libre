"""Tests for headers and footers, which belong to a page style.

Measured on a live Writer and held here: nothing on a header answers until
it is switched on, switching one off throws its text away, and asking for
one side of the page is what makes the two sides differ.
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
    return writer_doc(["Title of the document", "Body text here"],
                      caret=(0, 0))


def standard(bridge, doc):
    listed = bridge.list_headers_footers(doc=doc)
    return [one for one in listed["page_styles"]
            if one["page_style"] == "Standard"][0]


def test_a_document_with_nothing_on_it(bridge, doc):
    listed = bridge.list_headers_footers(doc=doc)

    assert listed["current_page_style"] == "Standard"
    assert [one["page_style"] for one in listed["page_styles"]] == ["Standard"]
    only = listed["page_styles"][0]
    assert (only["header"]["on"], only["footer"]["on"]) == (False, False)
    # Measured: while it is off, nothing else on a header answers with a
    # value, so nothing else is reported either.
    assert "same_on_both_pages" not in only["header"]


def test_writing_a_running_title(bridge, doc):
    made = bridge.set_header_footer("header", "GraphQL: руководство", doc=doc)

    assert (made["success"], made["text"]) == (True, "GraphQL: руководство")
    assert (made["page_style"], made["which"]) == ("Standard", "all")
    assert made["same_on_both_pages"] is True
    assert standard(bridge, doc)["header"]["text"]["all"] == \
        "GraphQL: руководство"


def test_a_page_number_belongs_in_the_footer(bridge, doc):
    made = bridge.set_header_footer("footer", "Страница {page} из {pages}",
                                    doc=doc)

    assert made["fields"] == ["page", "pages"]
    assert made["text"] == "Страница 1 из 1"


def test_a_placeholder_nobody_knows_is_left_alone(bridge, doc):
    made = bridge.set_header_footer("header", "{page} и {погода}", doc=doc)

    assert made["fields"] == ["page"]
    assert made["text"] == "1 и {погода}"


def test_asking_for_one_side_makes_the_sides_differ(bridge, doc):
    left = bridge.set_header_footer("header", "Чётные", which="left", doc=doc)
    bridge.set_header_footer("header", "Нечётные", which="right", doc=doc)

    assert left["same_on_both_pages"] is False
    header = standard(bridge, doc)["header"]
    assert header["text"] == {"left": "Чётные", "right": "Нечётные"}


def test_a_first_page_of_its_own(bridge, doc):
    bridge.set_header_footer("header", "Всюду", doc=doc)

    first = bridge.set_header_footer("header", "Титул", which="first", doc=doc)

    assert first["same_on_the_first_page"] is False
    header = standard(bridge, doc)["header"]
    assert header["text"]["first"] == "Титул"
    assert header["text"]["all"] == "Всюду"


def test_another_page_style_by_name(bridge, doc):
    made = bridge.set_header_footer("header", "Первая страница",
                                    page_style="First Page", doc=doc)

    assert (made["success"], made["page_style"]) == (True, "First Page")
    listed = bridge.list_headers_footers(doc=doc)
    assert sorted(one["page_style"] for one in listed["page_styles"]) == \
        ["First Page", "Standard"]


def test_what_the_header_tools_refuse(bridge, doc):
    assert bridge.set_header_footer("marginalia", "x",
                                    doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_header_footer("header", "x", which="sideways",
                                    doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_header_footer("header", 7,
                                    doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_header_footer("header", "x", page_style="Нетакой",
                                    doc=doc)["code"] == "NOT_FOUND"
    assert bridge.list_headers_footers(page_style="Нетакой",
                                       doc=doc)["code"] == "NOT_FOUND"


def test_removing_one_throws_its_text_away(bridge, doc):
    bridge.set_header_footer("header", "Чётные", which="left", doc=doc)
    bridge.set_header_footer("header", "Нечётные", which="right", doc=doc)
    bridge.set_header_footer("footer", "Внизу", doc=doc)

    gone = bridge.remove_header_footer("header", doc=doc)

    assert gone["success"] is True
    assert gone["was_saying"]["right"] == "Нечётные"
    after = standard(bridge, doc)
    assert after["header"]["on"] is False
    assert after["footer"]["on"] is True, "the footer was taken with it"
    # And it really is gone: writing again starts from nothing.
    again = bridge.set_header_footer("header", "Заново", doc=doc)
    assert again["text"] == "Заново"


def test_removing_what_is_not_there(bridge, doc):
    assert bridge.remove_header_footer("header",
                                       doc=doc)["code"] == "NOT_FOUND"
    assert bridge.remove_header_footer("marginalia",
                                       doc=doc)["code"] == "INVALID_PARAMETER"
