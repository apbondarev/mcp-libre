"""Tests for fields: the bits of a document that write themselves.

A field is the other way round from a comment. A comment's markers carry no
characters and cost one position for cursor movement; a field **carries the
text it shows** and costs one position — so the paragraph's string is longer
than a walk over its Text portions, and the addresses of everything after a
field came out to the right of where they belonged. Measured on a live
Writer, on "составлено 9/17/26", and this is where that is held.
"""

import pytest

from tests.fake_writer import FakeField, writer_doc
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

SHOWN = "9/17/26"
LINE = f"Составлено {SHOWN} нами"
WITH_A_FIELD = [
    {"text": "Составлено "},
    {"kind": "TextField", "text": SHOWN,
     "field": FakeField(SHOWN, command="Date", service="DateTime")},
    {"text": " нами"},
]


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Заголовок", LINE], caret=(1, 0),
                     portions={1: [dict(piece) for piece in WITH_A_FIELD]})


def test_a_field_is_reported_with_what_it_shows(bridge, doc):
    listed = bridge.list_fields(doc=doc)

    assert listed["count"] == 1
    field = listed["fields"][0]
    assert field["text"] == SHOWN
    assert field["command"] == "Date"
    assert field["kind"] == "date"
    # An anchor on where it stands: a field's offset can only come from
    # walking every paragraph's portions, which `number: true` pays for.
    assert field["address"]["anchor"]["type"] == "text"
    assert "paragraph" not in field["address"]


def test_the_run_that_is_a_field_says_so(bridge, doc):
    runs = bridge.read_runs({"paragraph": 1}, doc=doc)

    carrying = [run for run in runs["runs"] if run.get("field")]
    assert len(carrying) == 1
    assert carrying[0]["text"] == SHOWN
    assert carrying[0]["field"]["command"] == "Date"
    assert [run.get("field") for run in runs["runs"]].count(None) == 2


def test_an_address_after_a_field_counts_the_characters_it_shows(bridge, doc):
    # The words after the field start where the string says they do. Walking
    # only the Text portions put this seven characters to the left of "нами".
    after = bridge._resolve_address(doc, {"paragraph": 1,
                                          "offset": len("Составлено ")
                                          + len(SHOWN) + 1, "length": 4})

    assert after.getString() == "нами"


def test_an_address_inside_a_field_stands_where_it_begins(bridge, doc):
    # No cursor can stand in the middle of a date.
    inside = bridge._resolve_address(doc, {"paragraph": 1,
                                           "offset": len("Составлено ") + 3,
                                           "length": 0})

    assert inside.getString() == ""


def test_a_rewrite_over_a_field_is_refused(bridge, doc):
    refused = bridge.replace_range({"paragraph": 1}, "переписано", doc=doc)

    assert refused["success"] is False
    assert refused["code"] == "WOULD_LOSE_FORMATTING"
    assert "1 field" in refused["error"]
    assert "destroyed outright" in refused["error"]
    assert doc.getText().paragraphs[1] == LINE


def test_flatten_writes_over_it_and_says_so(bridge, doc):
    written = bridge.replace_range({"paragraph": 1}, "переписано",
                                   flatten=True, doc=doc)

    assert written["success"] is True
    assert written["fields_dropped"] == 1


def test_a_kind_nobody_has_heard_of_is_refused(bridge, doc):
    refused = bridge.insert_field({"paragraph": 1, "offset": 0}, "weather",
                                  doc=doc)

    assert (refused["success"], refused["code"]) == (False,
                                                     "INVALID_PARAMETER")


def test_fields_can_be_asked_for_by_place(bridge, doc):
    assert bridge.list_fields({"paragraph": 0}, doc=doc)["count"] == 0
    assert bridge.list_fields({"paragraph": 1}, doc=doc)["count"] == 1


def test_a_field_showing_nothing_does_not_hide_its_neighbour(bridge):
    """A title the document has not got is an empty field, and an empty
    range overlaps every address beside it — which left the field next to it
    impossible to name. An address that matches one exactly names that one."""
    empty = FakeField("", command="DocInformation:Title", service="DocInfo.Title")
    number = FakeField("15", command="Page number", service="PageNumber")
    doc = writer_doc(["15"], caret=(0, 0), portions={0: [
        {"kind": "TextField", "text": "", "field": empty},
        {"kind": "TextField", "text": "15", "field": number}]})

    listed = bridge.list_fields(doc=doc)
    assert listed["count"] == 2

    at = [one for one in listed["fields"]
          if one["kind"] == "page_number"][0]["address"]
    gone = bridge.delete_field(at, doc=doc)

    assert gone["success"] is True
    assert gone["deleted"] == "page_number"


def test_a_field_is_numbered_when_that_is_asked_for(bridge, doc):
    field, = bridge.list_fields(number=True, doc=doc)["fields"]

    assert field["address"]["paragraph"] == 1
    assert isinstance(field["address"]["offset"], int)


def test_a_field_is_deleted_by_the_anchored_address_it_was_listed_with(bridge,
                                                                       doc):
    # The address is compared by the place it resolves to, not by the way it
    # is written, or an anchor could never name the field it holds.
    at = bridge.list_fields(doc=doc)["fields"][0]["address"]

    gone = bridge.delete_field(at, doc=doc)

    assert gone["success"] is True
    assert bridge.list_fields(doc=doc)["count"] == 0
