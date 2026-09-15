"""Tests that the tools stop walking the whole document to answer about one place.

Reading the runs of a paragraph cost half a second on a three-hundred-paragraph
document because three habits each swept it: every range was compared with
every paragraph and table, every picture in the document was described to find
the ones in this paragraph, and every table was placed by a walk of its own.
The timings live in CLAUDE.md; what is checked here is that the sweeps are not
made, since a fake cannot be slow.
"""

import pytest

from tests.fake_writer import writer_doc
from tests.fakes_tables import FakeTextTable
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

PARAGRAPHS = ["Введение", "Operation", "{ hero }", "Response", "Конец"]


@pytest.fixture
def bridge():
    return UNOBridge()


def counting(bridge, name):
    """Replace a method with one that counts how often it is called."""
    calls = []
    original = getattr(bridge, name)

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    setattr(bridge, name, counted)
    return calls


def test_reading_runs_describes_only_the_pictures_of_that_paragraph(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0),
                     images=[{"name": "far", "paragraph": 0, "offset": 0},
                             {"name": "near", "paragraph": 2, "offset": 2}])
    described = counting(bridge, "_describe_image")

    runs = bridge.read_runs({"paragraph": 2}, doc=doc)

    assert runs["success"] is True
    assert len(described) <= 1, "a picture elsewhere was described anyway"


def test_reading_runs_does_not_compare_the_range_with_the_whole_body(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    compared = counting(bridge, "_covers")

    bridge.read_runs({"paragraph": 2}, doc=doc)

    assert compared == [], "a range inside one paragraph was swept for tables"


def test_reading_runs_by_index_does_not_look_the_index_up_again(bridge):
    doc = writer_doc(PARAGRAPHS, caret=(0, 0))
    walked = counting(bridge, "_locate_paragraph")

    read = bridge.read_runs({"paragraph": 3}, doc=doc)

    assert read["paragraph"] == 3
    assert walked == [], "the address said which paragraph, and it was sought"


def test_listing_tables_places_them_all_in_one_walk(bridge):
    tables = [FakeTextTable(f"Table{number}", cells=[["a", "b"]],
                            after_paragraph=number)
              for number in (1, 2, 3)]
    doc = writer_doc(PARAGRAPHS, caret=(0, 0), tables=tables)
    walked = counting(bridge, "_paragraphs_before_table")

    listed = bridge.list_tables(doc=doc)

    assert listed["count"] == 3
    assert walked == [], "each table was placed by a walk of its own"


def test_a_range_in_a_cell_is_not_looked_for_in_the_body(bridge):
    table = FakeTextTable("Table1", cells=[["in the cell", "and here"]],
                          after_paragraph=1)
    doc = writer_doc(PARAGRAPHS, caret=(0, 0), tables=[table])
    compared = counting(bridge, "_covers")

    spans = bridge._range_spans(
        doc, bridge._resolve_address(doc, {"table": "Table1", "cell": "A1"}))

    assert spans == {"paragraphs": [], "tables": []}
    assert compared == [], "a cell's range was compared with the body text"
