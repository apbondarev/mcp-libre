"""Tests for the address contract: {"paragraph": i, "offset": k, "length": n}.

The round-trip test is the important one: a search hit's address must resolve
back to the text that was found, or phase 2 will edit the wrong span.
"""

import pytest

from tests.fake_writer import (
    FakeRange,
    FakeSelection,
    FakeTableCellSelection,
    writer_doc,
)
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import AddressError, UNOBridge  # noqa: E402

PARAGRAPHS = [
    "First paragraph.",  # 16 chars
    "Second paragraph here.",  # 22 chars
    "Third one.",  # 10 chars
]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def test_resolves_a_whole_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1})

    assert resolved.getString() == "Second paragraph here."


def test_resolves_an_offset_and_length_inside_a_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1, "offset": 7, "length": 9})

    assert resolved.getString() == "paragraph"


def test_offset_without_length_runs_to_the_end_of_the_paragraph(bridge, doc):
    resolved = bridge._resolve_address(doc, {"paragraph": 1, "offset": 7})

    assert resolved.getString() == "paragraph here."


def test_resolves_the_current_selection(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(2, 0), selection_spans=[((2, 0), (2, 5))])

    resolved = bridge._resolve_address(doc, {"selection": True})

    assert resolved.getString() == "Third"


def test_resolves_a_collapsed_selection_to_an_empty_range(bridge, doc):
    resolved = bridge._resolve_address(doc, {"selection": True})

    assert resolved.getString() == ""


def test_skips_tables_when_counting_paragraphs(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0), enumeration_items=[0, 1, "table", 2])

    resolved = bridge._resolve_address(doc, {"paragraph": 2})

    assert resolved.getString() == "Third one."


def test_rejects_a_paragraph_past_the_end(bridge, doc):
    with pytest.raises(AddressError, match="no body paragraph 9"):
        bridge._resolve_address(doc, {"paragraph": 9})


def test_rejects_an_offset_past_the_end_of_the_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="outside paragraph"):
        bridge._resolve_address(doc, {"paragraph": 2, "offset": 50})


def test_rejects_a_length_running_past_the_end_of_the_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="past the end"):
        bridge._resolve_address(doc, {"paragraph": 2, "offset": 5, "length": 50})


def test_rejects_a_negative_paragraph(bridge, doc):
    with pytest.raises(AddressError, match="non-negative"):
        bridge._resolve_address(doc, {"paragraph": -1})


def test_rejects_an_address_with_neither_key(bridge, doc):
    with pytest.raises(AddressError, match="'paragraph' or 'selection'"):
        bridge._resolve_address(doc, {"offset": 3})


def test_rejects_a_non_object_address(bridge, doc):
    with pytest.raises(AddressError, match="must be an object"):
        bridge._resolve_address(doc, "paragraph 1")


def test_rejects_an_empty_selection_collection(bridge, doc):
    doc.getCurrentController()._selection = FakeSelection(doc.getText(), [])

    with pytest.raises(AddressError, match="nothing is selected"):
        bridge._resolve_address(doc, {"selection": True})


def test_rejects_a_selection_that_is_not_text(bridge, doc):
    doc.getCurrentController()._selection = FakeTableCellSelection()

    with pytest.raises(AddressError, match="not a text range"):
        bridge._resolve_address(doc, {"selection": True})


def test_address_round_trips_through_locate_and_resolve(bridge, doc):
    original = FakeRange(doc.getText(), (1, 7), (1, 16))
    address, _, _ = bridge._locate_range(doc, original)

    resolved = bridge._resolve_address(doc, address)

    assert resolved.getString() == original.getString() == "paragraph"
