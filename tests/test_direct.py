"""Tests for finding by style and for the formatting applied over one.

Three measurements hold these up. A paragraph style is *searchable* —
`SearchStyles` with the style's name — while a character style is not, and
is walked for. Direct formatting is `getPropertyState` on the range, DIRECT
or AMBIGUOUS where the parts differ. And character formatting applied to a
**whole** paragraph sits on the paragraph, where a range reports nothing at
all, so both are asked and both are cleared.
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
    return writer_doc(["Заголовок", "Обычный абзац тут", "query { hero }",
                       "Ещё обычный", "mutation { }"],
                      styles=["Heading 1", "Standard", "Preformatted Text",
                              "Standard", "Preformatted Text"],
                      outline_levels=[1, 0, 0, 0, 0], caret=(1, 0))


def test_a_paragraph_style_is_found_without_walking_for_it(bridge, doc):
    found = bridge.find_by_style("Preformatted Text", doc=doc)

    assert found["success"] is True
    assert [hit["text"] for hit in found["hits"]] == ["query { hero }",
                                                      "mutation { }"]
    # Each hit is named by an anchor. Numbering them is a sweep of the body
    # that runs as far as the last hit — 4.7s for the 23 places one style was
    # used in a real guide — and the anchor names the same text to every tool.
    assert [hit["address"]["anchor"]["type"] for hit in found["hits"]] \
        == ["text", "text"]
    assert all("paragraph" not in hit["address"] for hit in found["hits"])
    assert [bridge._resolve_address(doc, hit["address"]).getString()
            for hit in found["hits"]] == ["query { hero }", "mutation { }"]


def test_the_numbers_come_when_they_are_asked_for(bridge, doc):
    found = bridge.find_by_style("Preformatted Text", number=True, doc=doc)

    assert [hit["address"]["paragraph"] for hit in found["hits"]] == [2, 4]
    assert found["hits"][0]["address"]["anchor"]["type"] == "text"


def test_a_scoped_search_compares_ranges_rather_than_numbers(bridge, doc,
                                                             monkeypatch):
    def refuse(*arguments, **named):
        raise AssertionError("a scoped search numbered the hits")

    monkeypatch.setattr(bridge, "_addresses_in_order", refuse)
    monkeypatch.setattr(bridge, "_locate_matches", refuse)

    found = bridge.find_by_style("Preformatted Text",
                                 address={"paragraph": 4}, doc=doc)

    assert [hit["text"] for hit in found["hits"]] == ["mutation { }"]
    # What is not reported is what the scope holds and the cap left out, not
    # the hits elsewhere in the document that the scope threw away.
    assert found["not_reported"] is None


def test_a_character_style_is_walked_for(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 8, "length": 5},
                        character_style="Emphasis", doc=doc)

    found = bridge.find_by_style("Emphasis", family="character", doc=doc)

    assert [(hit["text"], hit["address"]["offset"])
            for hit in found["hits"]] == [("абзац", 8)]
    # The runs are walked for, so their numbers come free with the walk; the
    # anchor goes beside them rather than instead of them.
    assert found["hits"][0]["address"]["paragraph"] == 1
    assert found["hits"][0]["address"]["anchor"]["type"] == "text"
    assert "range" not in found["hits"][0]


def test_finding_is_scoped_and_refuses_what_it_cannot_do(bridge, doc):
    assert bridge.find_by_style("Preformatted Text", address={"paragraph": 2},
                                doc=doc)["count"] == 1
    assert bridge.find_by_style("Нетакой", doc=doc)["code"] == "NOT_FOUND"
    assert bridge.find_by_style("Emphasis", family="page",
                                doc=doc)["code"] == "INVALID_PARAMETER"


def test_what_is_applied_over_a_style(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 7}, bold=True,
                        color="#CC0000", doc=doc)

    direct = bridge.get_direct_formatting({"paragraph": 1, "offset": 0,
                                           "length": 7}, doc=doc)

    assert sorted(direct["character"]) == ["bold", "color"]
    assert direct["character"]["bold"]["value"] == "bold"
    assert direct["character"]["color"]["value"] == "#CC0000"
    assert direct["character"]["bold"]["where"] == "text"
    assert direct["paragraph_style"] == "Standard"


def test_a_paragraph_wearing_only_its_style_has_nothing_over_it(bridge, doc):
    direct = bridge.get_direct_formatting({"paragraph": 2}, doc=doc)

    assert direct["character"] == {}
    assert direct["paragraph_style"] == "Preformatted Text"


def test_a_range_whose_parts_differ_says_so(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 7}, bold=True,
                        doc=doc)

    direct = bridge.get_direct_formatting({"paragraph": 1}, doc=doc)

    assert direct["character"]["bold"]["mixed"] is True


def test_formatting_applied_to_a_whole_paragraph_is_seen(bridge, doc):
    # Measured: it sits on the paragraph, and the range reports nothing.
    bridge.format_range({"paragraph": 2}, bold=True,
                        font_name="Liberation Mono", doc=doc)

    direct = bridge.get_direct_formatting({"paragraph": 2}, doc=doc)

    assert sorted(direct["character"]) == ["bold", "font_name"]
    assert direct["character"]["bold"]["where"] == "paragraph"


def test_clearing_takes_it_off_again(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 7}, bold=True,
                        color="#CC0000", doc=doc)

    cleared = bridge.clear_direct_formatting({"paragraph": 1, "offset": 0,
                                              "length": 7}, doc=doc)

    assert cleared["success"] is True
    assert sorted(cleared["cleared"]["characters"]) == ["bold", "color"]
    assert bridge.get_direct_formatting({"paragraph": 1, "offset": 0,
                                         "length": 7},
                                        doc=doc)["character"] == {}


def test_clearing_a_whole_paragraph_reaches_the_paragraph(bridge, doc):
    bridge.format_range({"paragraph": 2}, bold=True, doc=doc)

    cleared = bridge.clear_direct_formatting({"paragraph": 2}, doc=doc)

    assert "bold" in cleared["cleared"]["characters"]
    assert bridge.get_direct_formatting({"paragraph": 2},
                                        doc=doc)["character"] == {}


def test_a_character_style_and_a_language_are_left_alone(bridge, doc):
    # Measured: setAllPropertiesToDefault keeps a character style, and
    # taking a run's language off would hand it back to the style's.
    bridge.format_range({"paragraph": 1, "offset": 8, "length": 5},
                        character_style="Emphasis", bold=True, doc=doc)

    cleared = bridge.clear_direct_formatting({"paragraph": 1, "offset": 8,
                                              "length": 5}, doc=doc)

    assert "bold" in cleared["cleared"]["characters"]
    assert "character_style" not in cleared["cleared"]["characters"]
    span = bridge._resolve_address(doc, {"paragraph": 1, "offset": 8,
                                         "length": 5})
    assert span.CharStyleName == "Emphasis"


def test_but_they_go_when_they_are_named(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 8, "length": 5},
                        character_style="Emphasis", doc=doc)

    cleared = bridge.clear_direct_formatting({"paragraph": 1, "offset": 8,
                                              "length": 5},
                                             properties=["character_style"],
                                             doc=doc)

    assert cleared["cleared"]["characters"] == ["character_style"]


def test_only_what_was_named_goes(bridge, doc):
    bridge.format_range({"paragraph": 1, "offset": 0, "length": 7}, bold=True,
                        color="#CC0000", doc=doc)

    cleared = bridge.clear_direct_formatting({"paragraph": 1, "offset": 0,
                                              "length": 7},
                                             properties=["bold"], doc=doc)

    assert cleared["cleared"]["characters"] == ["bold"]
    left = bridge.get_direct_formatting({"paragraph": 1, "offset": 0,
                                         "length": 7}, doc=doc)
    assert sorted(left["character"]) == ["color"]


def test_what_the_clean_up_refuses(bridge, doc):
    assert bridge.clear_direct_formatting({"paragraph": 1}, characters=False,
                                          doc=doc)["code"] == \
        "INVALID_PARAMETER"
    assert bridge.clear_direct_formatting({"paragraph": 1},
                                          properties=["sparkle"],
                                          doc=doc)["code"] == \
        "INVALID_PARAMETER"
    assert bridge.clear_direct_formatting({"paragraph": 99},
                                          doc=doc)["code"] == "INVALID_ADDRESS"
