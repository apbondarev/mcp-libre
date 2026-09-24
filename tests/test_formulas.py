"""Tests for formulas: the maths a document carries as objects of their own.

A formula is not text. It is an embedded Math document sitting in the
paragraph as one inline object, and the paragraph's string walks straight past
it — "the volume is  of the whole" is all a reader of the text sees of "the
volume is 1/5 of the whole". Measured on a real Writer: it adds no character
to the paragraph, its anchor is an empty range at the spot, the object's
`Model.Formula` is its text, Writer names a new one itself, a name that is
taken is refused, and getEmbeddedObjects answers in no particular order and
holds charts as well.
"""

import pytest

from tests.fake_writer import FakeDesktop, writer_doc
from tests.fakes_annotations import FakeFormulaObject
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from mcp_server import LibreOfficeMCPServer  # noqa: E402
from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Объем первой части равен  от всего объема тела.",
              "Плотность 1-й части равна .",
              "Конец"]
AFTER_EQUALS = len("Объем первой части равен ")
FIFTH = "{ frac { 1 } { 5 } }"


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(PARAGRAPHS, caret=(0, 0))


def put(bridge, doc, paragraph, offset, formula, **more):
    made = bridge.add_formula({"paragraph": paragraph, "offset": offset,
                               "length": 0}, formula, doc=doc, **more)
    assert made["success"] is True, made
    return made


def test_a_formula_is_put_at_a_caret_and_adds_no_text(bridge, doc):
    made = put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    assert made["formula"] == FIFTH
    assert made["address"] == {"paragraph": 0, "offset": AFTER_EQUALS,
                               "length": 0}
    assert made["inline"] is True
    assert list(doc.getText().paragraphs) == PARAGRAPHS      # no character


def test_a_formula_says_which_gap_in_the_sentence_it_fills(bridge, doc):
    made = put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    assert made["text_before"].endswith("части равен ")
    assert made["text_after"].startswith(" от всего объема")


def test_writer_names_a_formula_that_has_no_name(bridge, doc):
    first = put(bridge, doc, 0, AFTER_EQUALS, FIFTH)
    second = put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho _ 1")

    assert first["name"] != second["name"]
    assert first["name"] and second["name"]


def test_a_formula_is_given_the_size_it_takes_not_the_one_it_was_made_with(bridge, doc):
    # Measured: a new object stays 35.3 x 4.7 mm whatever is written in it, so
    # every formula came out squashed or stretched until it was sized.
    short = put(bridge, doc, 0, AFTER_EQUALS, "a", name="короткая")
    long = put(bridge, doc, 1, 5, "{ -b +- sqrt { b ^ 2 - 4 ac } } over { 2 a }",
               name="длинная")

    assert (short["width_mm"], short["height_mm"]) != (35.3, 4.7)
    assert long["width_mm"] > short["width_mm"]


def test_changing_a_formula_sizes_it_again(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, "a", name="доля")
    before = bridge.list_formulas(doc=doc)["formulas"][0]["width_mm"]

    changed = bridge.set_formula("доля", "a + b + c + d + e", doc=doc)

    assert changed["width_mm"] > before


def test_a_formula_can_be_given_a_name(bridge, doc):
    made = put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    assert made["name"] == "доля"


def test_a_name_that_is_taken_is_refused(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    refused = bridge.add_formula({"paragraph": 1}, "x", name="доля", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "INVALID_PARAMETER"
    assert len(bridge.list_formulas(doc=doc)["formulas"]) == 1


def test_a_formula_needs_its_text(bridge, doc):
    for blank in ("", "   ", None, 5):
        refused = bridge.add_formula({"paragraph": 0}, blank, doc=doc)

        assert refused["success"] is False
        assert refused["code"] == "INVALID_PARAMETER"


def test_a_bad_address_is_refused_not_guessed_at(bridge, doc):
    refused = bridge.add_formula({"paragraph": 99}, "x", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "INVALID_ADDRESS"


def test_over_a_range_it_goes_in_front_and_takes_nothing(bridge, doc):
    made = bridge.add_formula({"paragraph": 2, "offset": 0, "length": 5}, "x",
                              doc=doc)

    assert made["success"] is True
    assert made["address"]["offset"] == 0
    assert list(doc.getText().paragraphs)[2] == "Конец"


def test_replace_text_puts_it_in_place_of_what_the_range_covered(bridge, doc):
    made = bridge.add_formula({"paragraph": 2, "offset": 1, "length": 2}, "x",
                              replace_text=True, doc=doc)

    assert made["success"] is True
    assert list(doc.getText().paragraphs)[2] == "Кец"
    assert made["address"] == {"paragraph": 2, "offset": 1, "length": 0}


def test_formulas_are_listed_in_reading_order_not_the_order_they_were_made(bridge, doc):
    put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho _ 1")
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    listed = bridge.list_formulas(doc=doc)

    assert listed["count"] == 2
    assert [one["formula"] for one in listed["formulas"]] == [FIFTH, "rho _ 1"]
    # Reading order costs a comparison per pair, where numbering them is a
    # sweep of the body — 5.1s to place the single formula of a real guide.
    assert all(one["address"]["anchor"]["type"] == "text"
               for one in listed["formulas"])
    assert [one["address"]["paragraph"] for one
            in bridge.list_formulas(number=True, doc=doc)["formulas"]] == [0, 1]


def test_two_in_one_paragraph_keep_their_order_by_offset(bridge, doc):
    put(bridge, doc, 0, 20, "b")
    put(bridge, doc, 0, 5, "a")

    listed = bridge.list_formulas(doc=doc)

    assert [one["formula"] for one in listed["formulas"]] == ["a", "b"]


def test_a_list_can_be_scoped_to_a_paragraph(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)
    put(bridge, doc, 1, 5, "rho _ 1")

    listed = bridge.list_formulas(address={"paragraph": 1}, doc=doc)

    assert [one["formula"] for one in listed["formulas"]] == ["rho _ 1"]


def test_a_scoped_list_reads_its_own_paragraphs(bridge, doc, monkeypatch):
    # Measured on a guide holding one formula: finding it among the embedded
    # objects is 0.006s, and the call took 5.14s — all of it placing that one
    # formula by paragraph number, which is a sweep of the body.
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)
    put(bridge, doc, 1, 5, "rho _ 1")

    def refuse(*arguments, **named):
        raise AssertionError("a scoped listing swept the body")

    monkeypatch.setattr(bridge, "_addresses_in_order", refuse)
    monkeypatch.setattr(bridge, "_locate_paragraph", refuse)

    listed = bridge.list_formulas(address={"paragraph": 1}, doc=doc)

    assert [one["formula"] for one in listed["formulas"]] == ["rho _ 1"]
    assert listed["formulas"][0]["address"]["anchor"]["type"] == "text"


def test_the_whole_document_is_listed_without_a_sweep_too(bridge, doc,
                                                          monkeypatch):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    def refuse(*arguments, **named):
        raise AssertionError("listing every formula swept the body")

    monkeypatch.setattr(bridge, "_addresses_in_order", refuse)

    listed = bridge.list_formulas(doc=doc)

    assert [one["formula"] for one in listed["formulas"]] == [FIFTH]


def test_a_document_without_formulas_lists_none(bridge, doc):
    listed = bridge.list_formulas(doc=doc)

    assert listed["success"] is True
    assert (listed["count"], listed["formulas"]) == (0, [])


def test_other_embedded_objects_are_not_formulas(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)
    chart = FakeFormulaObject(name="Диаграмма1", chart=True)
    chart._anchor = doc.getText().createTextCursorByRange(
        doc.getText().createEnumeration().nextElement())
    doc.getEmbeddedObjects().items.append(chart)

    listed = bridge.list_formulas(doc=doc)

    assert [one["formula"] for one in listed["formulas"]] == [FIFTH]
    assert bridge.set_formula("Диаграмма1", "x", doc=doc)["code"] == "NOT_FOUND"
    assert bridge.delete_formula("Диаграмма1", doc=doc)["code"] == "NOT_FOUND"


def test_a_formula_is_changed_in_place_and_says_what_it_was(bridge, doc):
    made = put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    changed = bridge.set_formula("доля", "{ frac { 1 } { 4 } }", doc=doc)

    assert changed["success"] is True
    assert changed["was"] == FIFTH
    assert changed["formula"] == "{ frac { 1 } { 4 } }"
    assert changed["address"] == made["address"]
    listed = bridge.list_formulas(doc=doc)
    assert [one["formula"] for one in listed["formulas"]] == ["{ frac { 1 } { 4 } }"]


def test_changing_a_formula_that_is_not_there_is_refused_by_name(bridge, doc):
    refused = bridge.set_formula("нет такой", "x", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "NOT_FOUND"
    assert "list_formulas" in refused["error"]


def test_a_formula_cannot_be_changed_to_nothing(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    refused = bridge.set_formula("доля", "  ", doc=doc)

    assert refused["code"] == "INVALID_PARAMETER"
    assert bridge.list_formulas(doc=doc)["formulas"][0]["formula"] == FIFTH


def test_deleting_a_formula_leaves_the_text_and_says_what_it_was(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    gone = bridge.delete_formula("доля", doc=doc)

    assert gone["success"] is True
    assert gone["deleted"] == "доля"
    assert gone["was"] == FIFTH
    assert gone["address"]["paragraph"] == 0
    assert list(doc.getText().paragraphs) == PARAGRAPHS
    assert bridge.list_formulas(doc=doc)["count"] == 0


def test_a_deleted_formula_can_be_put_back_from_what_the_result_says(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")
    gone = bridge.delete_formula("доля", doc=doc)

    again = bridge.add_formula(gone["address"], gone["was"], name="доля",
                               doc=doc)

    assert again["success"] is True
    assert again["address"] == gone["address"]


def test_deleting_a_formula_twice_is_refused_the_second_time(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")
    bridge.delete_formula("доля", doc=doc)

    assert bridge.delete_formula("доля", doc=doc)["code"] == "NOT_FOUND"


def test_a_read_only_document_takes_no_formula(bridge, doc):
    doc.readonly = True

    refused = bridge.add_formula({"paragraph": 0}, "x", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "READ_ONLY"


# ---- the tools, as a client calls them ---------------------------------

@pytest.fixture
def server():
    made = LibreOfficeMCPServer()
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    doc.Title = "formulas.odt"
    made.uno_bridge.get_active_document = lambda: doc
    made.uno_bridge.desktop = FakeDesktop([doc])
    made.document = doc
    return made


def test_the_four_tools_are_registered_and_reach_the_bridge(server):
    for name in ("list_formulas_live", "add_formula_live",
                 "set_formula_live", "delete_formula_live"):
        assert name in server.tools

    added = server._run_tool("add_formula_live", {
        "address": {"paragraph": 0, "offset": AFTER_EQUALS, "length": 0},
        "formula": FIFTH, "name": "доля"})
    listed = server._run_tool("list_formulas_live", {})
    changed = server._run_tool("set_formula_live",
                               {"name": "доля", "formula": "x"})
    deleted = server._run_tool("delete_formula_live", {"name": "доля"})

    assert added["success"] is True
    assert [one["name"] for one in listed["formulas"]] == ["доля"]
    assert changed["was"] == FIFTH
    assert deleted["deleted"] == "доля"


def test_a_tool_can_be_told_which_document_it_is_for(server):
    other = writer_doc(["один", "два"], caret=(0, 0))
    other.Title = "other.odt"
    server.uno_bridge.desktop = FakeDesktop([server.document, other])

    added = server._run_tool("add_formula_live", {
        "address": {"paragraph": 0}, "formula": "x",
        "document": "file:///tmp/other.odt"})

    assert added["success"] is True
    assert server._run_tool("list_formulas_live", {})["count"] == 0
    assert server._run_tool("list_formulas_live", {
        "document": "file:///tmp/other.odt"})["count"] == 1


# ---- reading a paragraph that holds one --------------------------------
#
# The whole point of the tools above is that an assistant reading "равен  от
# всего объема" is told nothing is missing. So the reading tools say it
# themselves — without touching `text`, because every offset counts in it.

def test_a_paragraph_that_holds_a_formula_says_so_and_shows_it_in_place(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    read = bridge.read_paragraphs(doc=doc)["paragraphs"]

    assert read[0]["text"] == PARAGRAPHS[0]                  # untouched
    assert read[0]["formulas"] == [{"name": "доля", "formula": FIFTH,
                                    "offset": AFTER_EQUALS}]
    assert read[0]["text_with_formulas"] == (
        "Объем первой части равен ⟦formula: { frac { 1 } { 5 } }⟧ "
        "от всего объема тела.")


def test_a_paragraph_without_one_carries_nothing_extra(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    read = bridge.read_paragraphs(doc=doc)["paragraphs"]

    assert "formulas" not in read[2]
    assert "text_with_formulas" not in read[2]


def test_several_in_one_paragraph_each_go_where_they_stand(bridge, doc):
    put(bridge, doc, 0, 20, "b")
    put(bridge, doc, 0, 5, "a")

    shown = bridge.read_paragraphs(doc=doc)["paragraphs"][0]["text_with_formulas"]

    assert shown.index("⟦formula: a⟧") < shown.index("⟦formula: b⟧")
    assert shown.replace("⟦formula: a⟧", "").replace("⟦formula: b⟧", "") \
        == PARAGRAPHS[0]


def test_the_addresses_of_a_read_still_count_in_the_plain_text(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    read = bridge.read_paragraphs(doc=doc)["paragraphs"][0]
    hit = bridge.find_text("тела", doc=doc)["hits"][0]["address"]

    assert read["text"][hit["offset"]:hit["offset"] + hit["length"]] == "тела"


def test_a_document_without_formulas_reads_as_it_did(bridge, doc):
    read = bridge.read_paragraphs(doc=doc)["paragraphs"]

    assert all("formulas" not in one for one in read)


def test_the_runs_of_a_paragraph_say_which_formulas_stand_among_them(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")

    runs = bridge.read_runs({"paragraph": 0}, doc=doc)

    assert runs["formulas"] == [{"name": "доля", "formula": FIFTH,
                                 "paragraph": 0, "offset": AFTER_EQUALS}]


def test_runs_of_a_paragraph_without_one_say_nothing_of_formulas(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    assert "formulas" not in bridge.read_runs({"paragraph": 2}, doc=doc)


def test_a_range_that_stops_short_of_a_formula_does_not_report_it(bridge, doc):
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH)

    runs = bridge.read_runs({"paragraph": 0, "offset": 0, "length": 5}, doc=doc)

    assert "formulas" not in runs


def test_the_runs_of_a_paragraph_are_read_without_placing_every_formula(
        bridge, doc, monkeypatch):
    # Placing them all addresses every anchor, which walks the body: the cost
    # read_runs was made not to pay. Answering about one paragraph must not
    # need that sweep at all, so the sweep is made to fail here.
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")
    put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho", name="ро")

    def refuse(*arguments, **named):
        raise AssertionError("read_runs placed every formula in the document")

    monkeypatch.setattr(bridge, "_formula_places", refuse)

    runs = bridge.read_runs({"paragraph": 0}, doc=doc)

    assert runs["formulas"] == [{"name": "доля", "formula": FIFTH,
                                 "paragraph": 0, "offset": AFTER_EQUALS}]
    assert "formulas" not in bridge.read_runs({"paragraph": 2}, doc=doc)


def test_a_formula_that_will_not_answer_does_not_move_the_others(bridge, doc):
    # Both the anchor and the text, or neither: keeping an anchor whose
    # formula was not read left the two lists of different lengths, and every
    # formula after it took another's place.
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")
    put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho", name="ро")

    class Vanishing:
        """A model that answers once and is gone by the next read — what a
        formula the reader deletes mid-call looks like from here."""

        def __init__(self, text):
            self.text, self.reads = text, 0

        @property
        def Formula(self):
            self.reads += 1
            if self.reads > 1:
                raise RuntimeError("SwXTextEmbeddedObject: disposed or invalid")
            return self.text

    first = doc.getText().formulas[0]
    first.Model = Vanishing(FIFTH)

    read = bridge.read_paragraphs(doc=doc)["paragraphs"]

    assert "formulas" not in read[0]
    assert read[1]["formulas"] == [
        {"name": "ро", "formula": "rho",
         "offset": len("Плотность 1-й части равна ")}]


def test_the_runs_of_a_block_find_its_formulas_without_placing_them_all(
        bridge, doc, monkeypatch):
    # The same walk the single-paragraph path was spared: on a real guide
    # holding one formula, placing them all cost 4.0s of a read_runs that
    # asked about ten paragraphs nowhere near it.
    put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho", name="ро")

    def refuse(*arguments, **named):
        raise AssertionError("read_runs placed every formula in the document")

    monkeypatch.setattr(bridge, "_formula_places", refuse)

    inside = bridge.read_runs({"paragraph": 1, "through": 2}, doc=doc)
    outside = bridge.read_runs({"paragraph": 0, "through": 0}, doc=doc)

    assert inside["formulas"] == [{"name": "ро", "formula": "rho",
                                   "paragraph": 1,
                                   "offset": len("Плотность 1-й части равна ")}]
    assert "formulas" not in outside


def test_a_formula_is_told_from_a_chart_without_loading_it(bridge, doc):
    # Asking an embedded object for its Model *loads* it, and a real guide
    # holds hundreds: that cost 2.8s to find one formula, and every read of a
    # paragraph paid it. The class id is a plain property.
    put(bridge, doc, 0, AFTER_EQUALS, FIFTH, name="доля")
    chart = FakeFormulaObject(chart=True, name="Chart1")
    doc.getText().insertTextContent(
        bridge._resolve_address(doc, {"paragraph": 2, "offset": 0, "length": 0}),
        chart, False)

    loaded = []

    class Watched:
        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "Model":
                loaded.append(True)
            return getattr(self._real, name)

    objects = doc.getEmbeddedObjects()
    held = {name: Watched(objects.getByName(name))
            for name in objects.getElementNames()}
    objects.getByName = lambda name: held[name]

    found = bridge._formulas_of(doc)

    assert [name for name, _obj, _model in found] == ["доля"]
    # The chart's model is never touched: only the formula's is.
    assert len(loaded) == 1


def test_reading_a_paragraph_does_not_place_every_formula(bridge, doc,
                                                           monkeypatch):
    # Placing them is a sweep of the body, and read_paragraphs paid it on
    # every call: 12 seconds to read one paragraph of a guide holding one
    # formula.
    put(bridge, doc, 1, len("Плотность 1-й части равна "), "rho", name="ро")

    def refuse(*arguments, **named):
        raise AssertionError("read_paragraphs placed every formula")

    monkeypatch.setattr(bridge, "_formula_places", refuse)

    read = bridge.read_paragraphs(start=0, count=3, anchors=False,
                                  doc=doc)["paragraphs"]

    assert "formulas" not in read[0]
    assert read[1]["formulas"] == [{"name": "ро", "formula": "rho",
                                     "offset": len("Плотность 1-й части равна ")}]
    assert "⟦formula: rho⟧" in read[1]["text_with_formulas"]
