"""Tests for anchors: a name for a place that survives the edits around it.

A paragraph's number changes with every insertion or deletion above it, so a
plan made from one search went stale as it was carried out, and the way round
it was to work through a document backwards. An anchor points at the text
itself. What a held cursor does when the ground moves is measured in
tests/live/writer_tools_check.py; these check what is built on top of it.
"""

import pytest

from tests.fake_writer import writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import AddressError, UNOBridge  # noqa: E402
import uno_anchors  # noqa: E402

PARAGRAPHS = ["Введение", "Operation", "{ hero }", "Response", "Конец"]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def test_anchoring_a_paragraph_hands_back_a_token(bridge, doc):
    result = bridge.anchor({"paragraph": 1}, doc=doc)

    assert result["success"] is True
    assert result["held"] == 1
    held = result["anchors"][0]
    assert held["text"] == "Operation"
    assert held["address"]["paragraph"] == 1
    assert isinstance(held["anchor"], str) and held["anchor"]


def test_an_anchor_is_an_address(bridge, doc):
    token = bridge.anchor({"paragraph": 2}, doc=doc)["anchors"][0]["anchor"]

    assert bridge._resolve_address(doc, {"anchor": token}).getString() \
        == "{ hero }"


def test_several_addresses_at_once(bridge, doc):
    result = bridge.anchor([{"paragraph": 1}, {"paragraph": 3}], doc=doc)

    assert [held["text"] for held in result["anchors"]] == ["Operation",
                                                            "Response"]


def test_one_bad_address_anchors_nothing(bridge, doc):
    result = bridge.anchor([{"paragraph": 1}, {"paragraph": 99}], doc=doc)

    assert result["success"] is False
    assert "address 1" in result["error"]
    assert bridge.list_anchors(doc=doc)["held"] == 0


def test_an_anchor_holds_its_text_when_a_paragraph_above_it_goes(bridge, doc):
    token = bridge.anchor({"paragraph": 3}, doc=doc)["anchors"][0]["anchor"]
    text = doc.getText()

    text.removeTextContent(text.createEnumeration().nextElement())

    # The index it was found at now names another paragraph; the anchor does
    # not care, which is the whole point of it.
    assert bridge._resolve_address(doc, {"paragraph": 3}).getString() == "Конец"
    assert bridge._resolve_address(doc, {"anchor": token}).getString() \
        == "Response"


def test_an_anchor_survives_a_replacement_made_through_it(bridge, doc):
    token = bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["anchor"]

    result = bridge.replace_range({"anchor": token}, "Запрос", doc=doc)

    assert result["success"] is True
    assert doc.getText().paragraphs[1] == "Запрос"
    assert bridge._resolve_address(doc, {"anchor": token}).getString() \
        == "Запрос"


def test_an_anchor_whose_text_is_rewritten_elsewhere_refuses(bridge, doc):
    token = bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["anchor"]

    bridge.replace_range({"paragraph": 1}, "Запрос", doc=doc)

    with pytest.raises(AddressError, match="no longer covers anything"):
        bridge._resolve_address(doc, {"anchor": token})


def test_a_refusal_says_what_the_anchor_held(bridge, doc):
    token = bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["anchor"]
    bridge.replace_range({"paragraph": 1}, "Запрос", doc=doc)

    with pytest.raises(AddressError, match="Operation"):
        bridge._resolve_address(doc, {"anchor": token})


def test_an_unknown_anchor_is_refused(bridge, doc):
    with pytest.raises(AddressError, match="no anchor"):
        bridge._resolve_address(doc, {"anchor": "nosuch"})


def test_an_anchor_of_another_document_is_refused(bridge, doc):
    other = writer_doc(["Другой документ"], caret=(0, 0))
    token = bridge.anchor({"paragraph": 0}, doc=other)["anchors"][0]["anchor"]

    with pytest.raises(AddressError, match="another document"):
        bridge._resolve_address(doc, {"anchor": token})


def test_an_anchor_takes_nothing_beside_it(bridge, doc):
    token = bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["anchor"]

    with pytest.raises(AddressError, match="takes no"):
        bridge._resolve_address(doc, {"anchor": token, "offset": 2})


def test_listing_says_where_each_anchor_points_now(bridge, doc):
    bridge.anchor([{"paragraph": 1}, {"paragraph": 3}], doc=doc)

    listed = bridge.list_anchors(doc=doc)

    assert listed["held"] == 2
    assert listed["alive"] == 2
    assert [one["text"] for one in listed["anchors"]] == ["Operation",
                                                          "Response"]
    assert [one["address"]["paragraph"] for one in listed["anchors"]] == [1, 3]


def test_listing_reports_a_dead_anchor_rather_than_hiding_it(bridge, doc):
    bridge.anchor({"paragraph": 1}, doc=doc)
    bridge.replace_range({"paragraph": 1}, "Запрос", doc=doc)

    listed = bridge.list_anchors(doc=doc)

    assert listed["alive"] == 0
    dead = listed["anchors"][0]
    assert dead["alive"] is False
    assert dead["held_when_made"] == "Operation"
    assert "replaced or deleted" in dead["why"]


def test_listing_shows_only_this_documents_anchors(bridge, doc):
    other = writer_doc(["Другой документ"], caret=(0, 0))
    bridge.anchor({"paragraph": 0}, doc=other)
    bridge.anchor({"paragraph": 1}, doc=doc)

    listed = bridge.list_anchors(doc=doc)

    assert listed["held"] == 1
    assert listed["held_in_all_documents"] >= 2


def test_dropping_the_anchors_of_a_document(bridge, doc):
    bridge.anchor([{"paragraph": 1}, {"paragraph": 3}], doc=doc)

    dropped = bridge.drop_anchors(doc=doc)

    assert dropped["count"] == 2
    assert bridge.list_anchors(doc=doc)["held"] == 0


def test_dropping_named_anchors(bridge, doc):
    made = bridge.anchor([{"paragraph": 1}, {"paragraph": 3}],
                         doc=doc)["anchors"]

    bridge.drop_anchors([made[0]["anchor"]], doc=doc)

    assert [one["anchor"] for one in bridge.list_anchors(doc=doc)["anchors"]] \
        == [made[1]["anchor"]]


def test_dropping_an_anchor_that_is_not_there_is_refused(bridge, doc):
    result = bridge.drop_anchors(["nosuch"], doc=doc)

    assert result["success"] is False
    assert "no such anchor" in result["error"]


def test_search_hands_back_an_anchor_per_hit(bridge, doc):
    found = bridge.find_text("Operation", anchors=True, doc=doc)

    token = found["hits"][0]["anchor"]
    assert bridge._resolve_address(doc, {"anchor": token}).getString() \
        == "Operation"


def test_search_without_anchors_holds_nothing(bridge, doc):
    # Anchors are on by default now; asking for none still holds none.
    bridge.find_text("Operation", anchors=False, doc=doc)

    assert bridge.list_anchors(doc=doc)["held"] == 0


def test_reading_paragraphs_can_anchor_them(bridge, doc):
    read = bridge.read_paragraphs(start=1, count=2, anchors=True, doc=doc)

    tokens = [entry["anchor"] for entry in read["paragraphs"]]
    assert [bridge._resolve_address(doc, {"anchor": token}).getString()
            for token in tokens] == ["Operation", "{ hero }"]


def test_the_oldest_anchors_are_let_go_when_too_many_are_held(bridge, doc,
                                                              monkeypatch):
    monkeypatch.setattr(uno_anchors, "MAX_ANCHORS", 3)
    made = [bridge.anchor({"paragraph": 1}, doc=doc)["anchors"][0]["anchor"]
            for _ in range(4)]

    held = [one["anchor"] for one in bridge.list_anchors(doc=doc)["anchors"]]

    assert made[0] not in held
    assert made[-1] in held
    assert len(held) == 3


def test_closing_a_document_takes_its_anchors_with_it(bridge, doc):
    bridge.anchor([{"paragraph": 1}, {"paragraph": 3}], doc=doc)

    closed = bridge.close_document(doc=doc, unsaved="discard")

    assert closed["anchors_let_go"] == 2
    assert bridge._anchor_store() == {}
