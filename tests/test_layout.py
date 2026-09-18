"""Tests for page layout: the page, its breaks and its line numbers.

The measurements these hold: `IsLandscape` turns nothing on its own — the
size has to be swapped as well — page lengths round on the way through
twips, and a page style told to use one column still counts 0.
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
    return writer_doc(["First", "Second", "Third"], caret=(0, 0))


def test_reading_the_page(bridge, doc):
    layout = bridge.get_page_layout(doc=doc)

    assert layout["success"] is True
    assert (layout["paper"], layout["orientation"]) == ("a4", "portrait")
    # Measured: A4 answers 21001, so the millimetres are 210.01 and never
    # exactly 210.
    assert round(layout["width_mm"]) == 210
    assert layout["margins_mm"]["left"] == 20.0
    assert layout["columns"] == 1
    assert layout["line_numbering"]["on"] is False


def test_turning_the_page_swaps_the_size(bridge, doc):
    turned = bridge.set_page_layout(orientation="landscape", doc=doc)

    assert turned["orientation"] == "landscape"
    assert turned["width_mm"] > turned["height_mm"], \
        "the flag moved and the page did not"
    assert turned["paper"] == "a4"
    back = bridge.set_page_layout(orientation="portrait", doc=doc)
    assert back["width_mm"] < back["height_mm"]


def test_paper_margins_and_columns(bridge, doc):
    assert bridge.set_page_layout(paper="letter", doc=doc)["paper"] == "letter"

    margins = bridge.set_page_layout(margins_mm={"left": 30, "right": 15},
                                     doc=doc)

    assert round(margins["margins_mm"]["left"]) == 30
    assert round(margins["margins_mm"]["right"]) == 15
    assert round(margins["was"]["margins_mm"]["left"]) == 20
    assert bridge.set_page_layout(columns=2, doc=doc)["columns"] == 2
    assert bridge.set_page_layout(columns=1, doc=doc)["columns"] == 1


def test_what_the_layout_tool_refuses(bridge, doc):
    for refused in (bridge.set_page_layout(paper="papyrus", doc=doc),
                    bridge.set_page_layout(orientation="sideways", doc=doc),
                    bridge.set_page_layout(columns=0, doc=doc),
                    bridge.set_page_layout(margins_mm={"inner": 10}, doc=doc),
                    bridge.set_page_layout(margins_mm=7, doc=doc),
                    bridge.set_page_layout(doc=doc)):
        assert (refused["success"], refused["code"]) == (False,
                                                         "INVALID_PARAMETER")
    assert bridge.set_page_layout(page_style="Нетакой", paper="a4",
                                  doc=doc)["code"] == "NOT_FOUND"


def test_a_page_break_belongs_to_the_paragraph_after_it(bridge, doc):
    broken = bridge.set_page_break({"paragraph": 1}, doc=doc)

    assert (broken["success"], broken["break_type"]) == (True, "PAGE_BEFORE")
    assert broken["paragraph"] == 1


def test_a_break_can_switch_the_page_style(bridge, doc):
    switched = bridge.set_page_break({"paragraph": 2}, page_style="Landscape",
                                     page_number=1, doc=doc)

    assert (switched["page_style"], switched["page_number"]) == ("Landscape", 1)


def test_taking_a_break_away(bridge, doc):
    bridge.set_page_break({"paragraph": 1}, page_style="Landscape", doc=doc)

    cleared = bridge.set_page_break({"paragraph": 1}, kind="none", doc=doc)

    # Measured: PageDescName refuses None and takes "" instead, reading back
    # as None afterwards.
    assert (cleared["break_type"], cleared["page_style"]) == ("NONE", None)


def test_what_the_break_tool_refuses(bridge, doc):
    assert bridge.set_page_break({"paragraph": 1}, kind="sideways",
                                 doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_page_break({"paragraph": 1}, page_style="Нетакой",
                                 doc=doc)["code"] == "NOT_FOUND"
    assert bridge.set_page_break({"paragraph": 1}, kind="none",
                                 page_style="Landscape",
                                 doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_page_break({"paragraph": 99},
                                 doc=doc)["code"] == "INVALID_ADDRESS"
    assert bridge.set_page_break({"paragraph": 1}, page_number=0,
                                 doc=doc)["code"] == "INVALID_PARAMETER"


def test_numbering_the_lines(bridge, doc):
    numbered = bridge.set_line_numbering(on=True, interval=5,
                                         restart_each_page=True, doc=doc)

    assert (numbered["on"], numbered["interval"]) == (True, 5)
    assert numbered["restart_each_page"] is True
    assert numbered["was"]["on"] is False
    assert bridge.get_page_layout(doc=doc)["line_numbering"]["on"] is True
    assert bridge.set_line_numbering(on=False, doc=doc)["on"] is False


def test_what_the_numbering_tool_refuses(bridge, doc):
    assert bridge.set_line_numbering(doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.set_line_numbering(interval=0,
                                     doc=doc)["code"] == "INVALID_PARAMETER"


def test_describe_style_answers_page_questions_about_a_page(bridge, doc):
    described = bridge.describe_style(name="Standard", family="page", doc=doc)

    in_force = described["effective"]
    assert in_force["Width"]["value"].endswith(" mm")
    assert in_force["TextColumns"]["value"] == 1
    assert in_force["IsLandscape"]["value"] is False
    assert [key for key in in_force if key.startswith("Char")] == [], \
        "a page style was asked about character properties"
