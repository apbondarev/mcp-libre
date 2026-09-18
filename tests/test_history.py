"""Tests for undo and redo, and for text turning into a table and back.

The undo stack belongs to the **document**: the reader's typing sits in it
beside this server's edits, and the titles are what tell them apart — ours
read "MCP: …", theirs "Typing: …". So undo stops at the first step it did
not make unless it is told otherwise, which is the point of these.
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
    return writer_doc(["Имя\tВозраст", "Аня\t31", "Борис\t45",
                       "После таблицы"], caret=(0, 0))


def lines(doc):
    return list(doc.getText().paragraphs)


def test_the_history_says_whose_each_step_is(bridge, doc):
    bridge.replace_range({"paragraph": 3}, "ПОСЛЕ", flatten=True, doc=doc)
    doc.UndoManager.entries.insert(0, ("Typing: “Аня”", None))

    listed = bridge.list_undo_steps(doc=doc)

    assert listed["can_undo"] is True
    assert listed["undo"][0]["made_here"] is True
    assert listed["ours_on_top"] is True
    assert any(not step["made_here"] for step in listed["undo"])
    assert listed["can_redo"] is False


def test_an_edit_goes_back_and_comes_again(bridge, doc):
    bridge.replace_range({"paragraph": 3}, "ПОСЛЕ", flatten=True, doc=doc)

    back = bridge.undo(doc=doc)

    assert (back["success"], back["steps"]) == (True, 1)
    assert lines(doc)[3] == "После таблицы"
    assert bridge.list_undo_steps(doc=doc)["can_redo"] is True
    again = bridge.redo(doc=doc)
    assert (again["success"], again["steps"]) == (True, 1)
    assert lines(doc)[3] == "ПОСЛЕ"


def test_undo_stops_at_a_step_it_did_not_make(bridge, doc):
    # The reader typed, and then this server edited nothing since.
    doc.UndoManager.entries.append(("Typing: “Аня”", doc.UndoManager._state()))

    refused = bridge.undo(doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")
    assert refused["next_step"] == "Typing: “Аня”"
    assert "include_others" in refused["error"]


def test_but_it_can_be_told_to(bridge, doc):
    doc.UndoManager.entries.append(("Typing: “Аня”", doc.UndoManager._state()))

    taken = bridge.undo(include_others=True, doc=doc)

    assert (taken["success"], taken["steps"]) == (True, 1)


def test_several_steps_at_once(bridge, doc):
    bridge.replace_range({"paragraph": 0}, "ОДИН", flatten=True, doc=doc)
    bridge.replace_range({"paragraph": 1}, "ДВА", flatten=True, doc=doc)

    back = bridge.undo(steps=2, doc=doc)

    assert back["steps"] == 2
    assert lines(doc)[:2] == ["Имя\tВозраст", "Аня\t31"]


def test_what_undo_refuses(bridge, doc):
    assert bridge.undo(steps=0, doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.undo(steps=1000, doc=doc)["code"] == "INVALID_PARAMETER"
    # Nothing has been done at all yet.
    assert bridge.undo(doc=doc)["code"] == "NOT_FOUND"
    assert bridge.redo(doc=doc)["code"] == "NOT_FOUND"


def test_paragraphs_become_a_table(bridge, doc):
    made = bridge.convert_text_to_table({"paragraph": 0, "through": 2},
                                        doc=doc)

    assert made["success"] is True
    assert (made["rows"], made["columns"]) == (3, 2)
    assert made["first_row"] == ["Имя", "Возраст"]
    assert lines(doc) == ["После таблицы"], "the rows are gone from the text"


def test_the_separator_can_be_named_or_given(bridge):
    doc = writer_doc(["a;b", "c;d", ""], caret=(0, 0))

    made = bridge.convert_text_to_table({"paragraph": 0, "through": 1},
                                        separator="semicolon", doc=doc)

    assert made["first_row"] == ["a", "b"]
    assert bridge.convert_text_to_table({"paragraph": 0}, separator="",
                                        doc=doc)["code"] == \
        "INVALID_PARAMETER"


def test_ragged_rows_are_refused(bridge, doc):
    refused = bridge.convert_text_to_table({"paragraph": 0, "through": 3},
                                           doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")
    assert refused["cells_per_row"] == [1, 2]
    assert lines(doc)[0] == "Имя\tВозраст", "nothing was converted"


def test_a_table_goes_back_to_text(bridge, doc):
    made = bridge.convert_text_to_table({"paragraph": 0, "through": 2},
                                        doc=doc)

    back = bridge.convert_table_to_text(made["table"], doc=doc)

    assert (back["success"], back["gone"]) == (True, True)
    assert lines(doc)[:3] == ["Имя\tВозраст", "Аня\t31", "Борис\t45"]


def test_a_table_nobody_has(bridge, doc):
    assert bridge.convert_table_to_text("Нетакой",
                                        doc=doc)["code"] == "NOT_FOUND"
