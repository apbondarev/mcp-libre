"""Tests for footnotes and endnotes.

The measurement they stand on: a note's mark is a character of the paragraph,
so a rewrite of the run it sits in destroys the note — see
plugin/pythonpath/uno_notes.py for the rest.
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
    return writer_doc(["A query is the entry point", "Second paragraph here"],
                      caret=(0, 0))


def line(doc, index=0):
    return doc.getText().paragraphs[index]


def test_a_footnote_costs_the_paragraph_one_character(bridge, doc):
    made = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                           "The entry point of a schema.", doc=doc)

    assert made["success"] is True
    assert made["address"] == {"paragraph": 0, "offset": 7, "length": 1}
    assert line(doc) == "A query1 is the entry point"


def test_an_endnote_can_wear_a_mark_of_its_own(bridge, doc):
    made = bridge.add_note({"paragraph": 1, "offset": 0, "length": 6},
                           "An endnote.", kind="endnote", label="*", doc=doc)

    assert (made["kind"], made["mark"]) == ("endnote", "*")
    assert line(doc, 1) == "Second* paragraph here"


def test_listing_says_what_each_note_holds(bridge, doc):
    bridge.add_note({"paragraph": 0, "offset": 2, "length": 5}, "Entry point.",
                    doc=doc)
    bridge.add_note({"paragraph": 1, "offset": 0, "length": 6}, "An endnote.",
                    kind="endnote", doc=doc)

    listed = bridge.list_notes(doc=doc)

    assert (listed["count"], listed["footnotes"], listed["endnotes"]) == (2, 1, 1)
    assert [one["kind"] for one in listed["notes"]] == ["footnote", "endnote"]
    assert listed["notes"][0]["text"] == "Entry point."
    assert bridge.list_notes({"paragraph": 1}, doc=doc)["count"] == 1
    assert bridge.list_notes(kind="endnote", doc=doc)["count"] == 1


def test_a_kind_nobody_knows(bridge, doc):
    assert bridge.list_notes(kind="marginalia",
                             doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.add_note({"paragraph": 0}, "x", kind="marginalia",
                           doc=doc)["code"] == "INVALID_PARAMETER"
    assert bridge.add_note({"paragraph": 0}, "   ",
                           doc=doc)["code"] == "INVALID_PARAMETER"


def test_the_runs_say_which_one_is_a_mark(bridge, doc):
    bridge.add_note({"paragraph": 0, "offset": 2, "length": 5}, "Entry point.",
                    doc=doc)

    runs = bridge.read_runs({"paragraph": 0}, doc=doc)

    marked = [run for run in runs["runs"] if run.get("note")]
    assert len(marked) == 1
    assert marked[0]["note"]["kind"] == "footnote"
    assert marked[0]["note"]["text"] == "Entry point."


def test_a_flat_rewrite_is_refused(bridge, doc):
    bridge.add_note({"paragraph": 0, "offset": 2, "length": 5}, "Entry point.",
                    doc=doc)

    refused = bridge.replace_range({"paragraph": 0}, "переписано", doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "WOULD_LOSE_FORMATTING")
    assert "footnote or endnote mark" in refused["error"]
    assert bridge.list_notes(doc=doc)["count"] == 1


def test_and_so_is_rewriting_the_run_the_mark_sits_in(bridge, doc):
    bridge.add_note({"paragraph": 0, "offset": 2, "length": 5}, "Entry point.",
                    doc=doc)
    runs = bridge.read_runs({"paragraph": 0}, doc=doc)["runs"]
    rewritten = [dict(run, text="X" if run.get("note") else run["text"])
                 for run in runs]

    refused = bridge.replace_runs({"paragraph": 0}, rewritten, doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "WOULD_LOSE_FORMATTING")


def test_but_the_text_around_the_mark_can_be_rewritten(bridge, doc):
    bridge.add_note({"paragraph": 0, "offset": 2, "length": 5}, "Entry point.",
                    doc=doc)
    runs = bridge.read_runs({"paragraph": 0}, doc=doc)["runs"]
    translated = []
    for run in runs:
        if run.get("note"):
            translated.append(dict(run))
        elif run["text"] == "A query":
            translated.append(dict(run, text="Запрос"))
        else:
            translated.append(dict(run, text=" — точка входа"))

    written = bridge.replace_runs({"paragraph": 0}, translated, doc=doc)

    assert written["success"] is True
    assert bridge.list_notes(doc=doc)["count"] == 1
    assert line(doc) == "Запрос1 — точка входа"


def test_changing_a_note_leaves_the_sentence_alone(bridge, doc):
    made = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                           "Entry point.", doc=doc)

    changed = bridge.update_note(made["address"], text="Точка входа.",
                                 label="†", doc=doc)

    assert (changed["success"], changed["text"]) == (True, "Точка входа.")
    assert changed["label"] == "†"
    assert line(doc) == "A query1 is the entry point", "the text was rewritten"


def test_a_note_is_named_by_where_its_mark_sits(bridge, doc):
    made = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                           "Entry point.", doc=doc)
    bridge.add_note({"paragraph": 0, "offset": 20, "length": 5}, "Another.",
                    doc=doc)

    # A whole paragraph covers both, so it has to be one of them by address.
    vague = bridge.update_note({"paragraph": 0}, text="x", doc=doc)
    assert (vague["success"], vague["code"]) == (False, "INVALID_PARAMETER")
    assert len(vague["notes"]) == 2

    assert bridge.update_note(made["address"], text="x", doc=doc)["success"]
    assert bridge.update_note({"paragraph": 1}, text="x",
                              doc=doc)["code"] == "NOT_FOUND"


def test_saying_nothing_to_change(bridge, doc):
    made = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                           "Entry point.", doc=doc)

    refused = bridge.update_note(made["address"], doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")


def test_deleting_takes_the_mark_out_and_keeps_the_words(bridge, doc):
    made = bridge.add_note({"paragraph": 0, "offset": 2, "length": 5},
                           "Entry point.", doc=doc)

    gone = bridge.delete_note(made["address"], doc=doc)

    assert (gone["success"], gone["was_saying"]) == (True, "Entry point.")
    assert line(doc) == "A query is the entry point"
    assert bridge.list_notes(doc=doc)["count"] == 0
