"""Tests for writing styles: making, changing, renaming, replacing, removing.

Measured on a live Writer and held here: renaming a style carries the text
wearing it, removing one lets the text fall back to the parent, and removing
a **built-in** style is accepted by UNO and does nothing at all — which is
why it is refused rather than reported as done.
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
    return writer_doc(["Заголовок", "query { hero }", "Обычный",
                       "mutation { }"],
                      styles=["Heading 1", "Preformatted Text", "Standard",
                              "Preformatted Text"],
                      outline_levels=[1, 0, 0, 0], caret=(1, 0))


def test_a_style_is_made_with_what_it_was_given(bridge, doc):
    made = bridge.create_style("Наш код", based_on="Preformatted Text",
                               properties={"color": "#006600",
                                           "font_size": 10}, doc=doc)

    assert made["success"] is True
    assert made["based_on"] == "Preformatted Text"
    assert made["set"] == ["color", "font_size"]
    described = bridge.describe_style(name="Наш код", doc=doc)
    assert described["set_here"]["CharColor"]["value"] == "#006600"


def test_a_style_can_be_cloned_and_then_changed(bridge, doc):
    bridge.create_style("Наш код", properties={"color": "#006600"}, doc=doc)

    clone = bridge.create_style("Копия", from_style="Наш код",
                                properties={"color": "#CC0000"}, doc=doc)

    assert clone["copied_from"] == "Наш код"
    assert clone["copied_properties"] > 0
    assert bridge.describe_style(name="Копия",
                                 doc=doc)["set_here"]["CharColor"]["value"] \
        == "#CC0000"


def test_a_refused_property_leaves_no_style_behind(bridge, doc):
    # It used to be found half way through, after insertByName.
    refused = bridge.create_style("Третий", properties={"sparkle": True},
                                  doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")
    assert bridge.describe_style(name="Третий", doc=doc)["code"] == "NOT_FOUND"


def test_what_making_a_style_refuses(bridge, doc):
    bridge.create_style("Наш код", doc=doc)

    assert bridge.create_style("Наш код", doc=doc)["code"] == \
        "INVALID_PARAMETER"
    assert bridge.create_style("Другой", based_on="Нетакой",
                               doc=doc)["code"] == "NOT_FOUND"
    assert bridge.create_style("  ", doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.create_style("Пятый", properties={"alignment": "sideways"},
                               doc=doc)["code"] == "INVALID_PARAMETER"


def test_changing_a_style_and_what_wears_it(bridge, doc):
    bridge.create_style("Наш код", based_on="Preformatted Text", doc=doc)
    bridge.apply_paragraph_style({"paragraph": 1}, "Наш код", doc=doc)

    updated = bridge.update_style("Наш код", properties={"italic": True},
                                  doc=doc)

    assert (updated["success"], updated["set"]) == (True, ["italic"])
    assert bridge.describe_style(name="Наш код",
                                 doc=doc)["set_here"]["CharPosture"]["value"] \
        == "ITALIC"


def test_a_built_in_style_can_be_changed(bridge, doc):
    changed = bridge.update_style("Preformatted Text",
                                  properties={"font_size": 9}, doc=doc)

    assert changed["success"] is True
    assert changed["built_in"] is True


def test_what_updating_refuses(bridge, doc):
    assert bridge.update_style("Нетакой", properties={"bold": True},
                               doc=doc)["code"] == "NOT_FOUND"
    assert bridge.update_style("Standard", doc=doc)["code"] == \
        "INVALID_PARAMETER"


def test_renaming_carries_the_text_with_it(bridge, doc):
    bridge.create_style("Наш код", doc=doc)
    bridge.apply_paragraph_style({"paragraph": 1}, "Наш код", doc=doc)

    renamed = bridge.rename_style("Наш код", "Код дома", doc=doc)

    assert renamed["success"] is True
    assert doc.getText().styles[1] == "Код дома"


def test_a_built_in_style_keeps_its_name(bridge, doc):
    refused = bridge.rename_style("Standard", "Наш обычный", doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")
    assert "from_style" in refused["error"]


def test_one_style_takes_the_place_of_another(bridge, doc):
    bridge.create_style("Код дома", based_on="Preformatted Text", doc=doc)

    swapped = bridge.replace_style("Preformatted Text", "Код дома", doc=doc)

    assert swapped["places_changed"] == 2
    assert doc.getText().styles[1] == "Код дома"
    assert doc.getText().styles[3] == "Код дома"


def test_replacing_can_be_scoped_and_refuses_the_silly(bridge, doc):
    bridge.create_style("Код дома", doc=doc)

    scoped = bridge.replace_style("Preformatted Text", "Код дома",
                                  address={"paragraph": 1}, doc=doc)

    assert scoped["places_changed"] == 1
    assert doc.getText().styles[3] == "Preformatted Text"
    assert bridge.replace_style("Код дома", "Код дома",
                                doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.replace_style("Нетакой", "Код дома",
                                doc=doc)["code"] == "NOT_FOUND"


def test_removing_a_built_in_style_is_refused(bridge, doc):
    # Measured: UNO accepts it, removes nothing and says nothing.
    refused = bridge.delete_style("Preformatted Text", doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")
    assert bridge.find_by_style("Preformatted Text", doc=doc)["count"] == 2


def test_removing_our_own_and_moving_the_text_first(bridge, doc):
    bridge.create_style("Код дома", based_on="Preformatted Text", doc=doc)
    bridge.replace_style("Preformatted Text", "Код дома", doc=doc)

    gone = bridge.delete_style("Код дома", replace_with="Preformatted Text",
                               doc=doc)

    assert (gone["success"], gone["places_changed"]) == (True, 2)
    assert gone["text_moved_to"] == "Preformatted Text"
    assert doc.getText().styles[1] == "Preformatted Text"
    assert bridge.delete_style("Код дома", doc=doc)["code"] == "NOT_FOUND"


def test_replacing_reaches_inside_table_cells(bridge):
    """"Throughout" has to mean it.

    Measured on a real document: Writer's own search found 184 paragraphs in
    a style while the body held 162 — the other 22 were inside table cells,
    and a swap that walked only the body left them behind. The fake tables
    carry a cell's text but not a paragraph style of its own, so what is
    held here is that the cells are visited at all; that the style really
    changes in one is checked live.
    """
    doc = writer_doc(["Обычный", "После таблицы"],
                     styles=["Standard", "Standard"], caret=(0, 0))
    bridge.create_table({"paragraph": 1}, rows=1, columns=1, name="Сетка",
                        doc=doc)
    bridge.create_style("Код дома", doc=doc)
    visited = []
    original = type(doc).getTextTables

    def watching(self):
        visited.append(1)
        return original(self)

    type(doc).getTextTables = watching
    try:
        swapped = bridge.replace_style("Standard", "Код дома", doc=doc)
    finally:
        type(doc).getTextTables = original

    assert swapped["success"] is True
    assert visited, "the tables were never looked at"


def test_a_scoped_replacement_leaves_the_tables_alone(bridge):
    """A scope names body paragraphs, and a cell's paragraph is in none."""
    doc = writer_doc(["Один", "Два"], styles=["Standard", "Standard"],
                     caret=(0, 0))
    bridge.create_style("Код дома", doc=doc)
    visited = []
    original = type(doc).getTextTables
    type(doc).getTextTables = lambda self: (visited.append(1),
                                            original(self))[1]
    try:
        swapped = bridge.replace_style("Standard", "Код дома",
                                       address={"paragraph": 0}, doc=doc)
    finally:
        type(doc).getTextTables = original

    assert swapped["places_changed"] == 1
    assert visited == []
