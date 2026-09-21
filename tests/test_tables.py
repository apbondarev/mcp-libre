"""Tests for knowing about tables and reading them.

A cell is its own XText, so a caret in one belongs to no body paragraph:
paragraph_index is None there and every text tool answers "outside the body
text". The view cursor knows better — it carries the table in TextTable and
the cell in Cell — and that is what these tools report.

A table's Width is not in 1/100 mm: a table spanning a 170.01 mm text area
reports 115596, so translating it invented a table 1.16 metres wide. The
share of each column is measurable and is reported instead.
"""

import asyncio

import pytest

from tests.fake_writer import (FakeAnnotation, FakeDesktop, FakeTextTable,
                               writer_doc)
from tests.uno_stubs import install_uno_stubs

install_uno_stubs()

from uno_bridge import UNOBridge  # noqa: E402

OPERATION = "{\n  hero {\n    name\n  }\n}"
RESPONSE = '{\n  "data": {\n    "hero": {\n      "name": "R2-D2"\n    }\n  }\n}'


def graphql_table():
    return FakeTextTable("Table1",
                         cells=[["Operation", "Response"],
                                [OPERATION, RESPONSE]])


@pytest.fixture
def bridge():
    return UNOBridge()


@pytest.fixture
def doc():
    return writer_doc(["Схемы и типы", "Операция", "Ответ"], caret=(1, 0),
                      tables=[graphql_table()])


@pytest.fixture
def caret_in_table():
    return writer_doc(["Схемы и типы", "Операция"], caret=(1, 0),
                      tables=[graphql_table()],
                      caret_in_cell=("Table1", "A1"))


# --- knowing where the caret is ---------------------------------------------

def test_says_the_caret_is_in_a_table(bridge, caret_in_table):
    info = bridge.get_cursor_info(doc=caret_in_table)

    assert info["success"] is True
    assert info["in_table"]["table"] == "Table1"
    assert info["in_table"]["cell"] == "A1"
    assert (info["in_table"]["row"], info["in_table"]["column"]) == (1, 1)
    assert (info["in_table"]["rows"], info["in_table"]["columns"]) == (2, 2)


def test_gives_the_text_of_the_cell_the_caret_is_in(bridge, caret_in_table):
    info = bridge.get_cursor_info(doc=caret_in_table)

    assert info["in_table"]["cell_text"] == "Operation"
    # and it still says there is no body paragraph, which is the truth
    assert info["cursor"]["paragraph_index"] is None


def test_says_nothing_about_tables_when_the_caret_is_in_the_text(bridge, doc):
    info = bridge.get_cursor_info(doc=doc)

    assert "in_table" not in info


# --- listing tables ----------------------------------------------------------

def test_lists_the_tables_with_their_size_and_place(bridge, doc):
    listed = bridge.list_tables(doc=doc)

    assert listed["count"] == 1
    table, = listed["tables"]
    assert table["name"] == "Table1"
    assert (table["rows"], table["columns"]) == (2, 2)
    assert table["cells"] == 4
    assert table["merged"] is False
    assert table["column_widths_percent"] == [50.0, 50.0]
    assert "width_mm" not in table          # Width is not millimetres


def test_the_listing_says_which_cell_the_caret_is_in(bridge, caret_in_table):
    listed = bridge.list_tables(doc=caret_in_table)

    assert listed["caret_is_in"] == {"table": "Table1", "cell": "A1"}


def test_a_document_with_no_tables_says_so(bridge):
    empty = writer_doc(["Just text"], caret=(0, 0))

    listed = bridge.list_tables(doc=empty)

    assert listed["success"] is True
    assert listed["count"] == 0


# --- reading a table ---------------------------------------------------------

def test_reads_the_whole_table_as_a_grid(bridge, doc):
    read = bridge.read_table("Table1", doc=doc)

    assert read["success"] is True
    assert [[cell["cell"] for cell in row] for row in read["rows"]] \
        == [["A1", "B1"], ["A2", "B2"]]
    assert read["rows"][0][0]["text"] == "Operation"
    assert read["rows"][1][1]["text"] == RESPONSE


def test_a_cell_of_several_paragraphs_keeps_its_line_breaks(bridge, doc):
    read = bridge.read_table("Table1", doc=doc)

    assert read["rows"][1][0]["text"].splitlines()[:2] == ["{", "  hero {"]
    assert len(read["rows"][1][0]["text"].splitlines()) == 5


def test_reads_the_table_the_caret_is_in_when_none_is_named(bridge,
                                                            caret_in_table):
    read = bridge.read_table(doc=caret_in_table)

    assert read["table"]["name"] == "Table1"
    assert read["caret_in_cell"] == "A1"


def test_with_no_table_and_no_caret_in_one_it_says_what_there_is(bridge, doc):
    refused = bridge.read_table(doc=doc)

    assert refused["success"] is False
    assert "not in a table" in refused["error"]
    assert "Table1" in refused["error"]


def test_reads_one_cell(bridge, doc):
    read = bridge.read_table("Table1", cell="B2", doc=doc)

    assert read["text"] == RESPONSE
    assert (read["row"], read["column"]) == (2, 2)


def test_a_cell_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.read_table("Table1", cell="C9", doc=doc)

    assert refused["success"] is False
    assert "C9" in refused["error"]
    assert "A1" in refused["error"]          # says which cells there are


def test_a_table_that_is_not_there_is_refused(bridge, doc):
    refused = bridge.read_table("Table9", doc=doc)

    assert refused["success"] is False
    assert "Table9" in refused["error"]
    assert "Table1" in refused["error"]


def test_a_merged_away_cell_comes_back_as_nothing(bridge):
    merged = FakeTextTable("Table1", cells=[["one", "two"], ["three", "four"]],
                           merged_away=("B2",))
    doc = writer_doc(["Text"], caret=(0, 0), tables=[merged])

    read = bridge.read_table("Table1", doc=doc)

    assert read["table"]["merged"] is True
    assert read["rows"][1][1] is None
    assert "B2" not in read["cell_names"]


def test_the_tools_are_registered_and_dispatch():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Text"], caret=(0, 0), tables=[graphql_table()],
                     caret_in_cell=("Table1", "B1"))
    server.uno_bridge.desktop = FakeDesktop([doc])

    listed = asyncio.run(server.execute_tool("list_tables_live", {}))
    assert listed["caret_is_in"]["cell"] == "B1"

    read = asyncio.run(server.execute_tool("read_table_live", {}))
    assert read["table"]["name"] == "Table1"

    one = asyncio.run(server.execute_tool("read_table_live",
                                          {"name": "Table1", "cell": "A2"}))
    assert one["text"].startswith("{")


# A selection can run from text through a table and out the other side. UNO
# hands that back as ONE range over the body, with the cells folded into its
# string by newlines — 318 characters where the paragraph held 206 — so
# nothing in the text says a table is in there. And setString over such a
# range destroys the table: measured, one table in, none out, the whole
# document collapsed into a single paragraph.

@pytest.fixture
def selection_over_a_table():
    table = FakeTextTable("Table1",
                          cells=[["Operation", "Response"], ["query", "R2-D2"]],
                          after_paragraph=1)
    return writer_doc(["Схемы и типы", "Попробуйте запрос:", "После таблицы"],
                      caret=(1, 0),
                      selection_spans=[((1, 0), (2, 13))],
                      tables=[table])


def test_the_selection_says_it_holds_a_table(bridge, selection_over_a_table):
    info = bridge.get_cursor_info(doc=selection_over_a_table)

    assert info["selection"]["contains_table"] is True
    assert [table["name"] for table in info["selection"]["tables"]] == ["Table1"]


def test_the_selection_says_how_big_that_table_is(bridge,
                                                  selection_over_a_table):
    table, = bridge.get_cursor_info(doc=selection_over_a_table)["selection"]["tables"]

    assert (table["rows"], table["columns"]) == (2, 2)
    assert table["cells"] == 4
    assert table["column_widths_percent"] == [50.0, 50.0]


def test_the_selection_also_says_which_paragraphs_it_covers(
        bridge, selection_over_a_table):
    selected = bridge.get_cursor_info(doc=selection_over_a_table)["selection"]

    assert selected["paragraphs"] == [1, 2]
    assert selected["has_selection"] is True


def test_a_selection_of_plain_text_holds_no_table(bridge, doc):
    doc = writer_doc(["Схемы и типы", "Обычный текст"], caret=(1, 0),
                     selection_spans=[((1, 0), (1, 6))])

    selected = bridge.get_cursor_info(doc=doc)["selection"]

    assert selected["contains_table"] is False
    assert selected["tables"] == []


def test_the_table_listing_says_which_are_selected(bridge,
                                                   selection_over_a_table):
    listed = bridge.list_tables(doc=selection_over_a_table)

    assert listed["in_selection"] == ["Table1"]


def test_replacing_such_a_selection_is_refused(bridge, selection_over_a_table):
    refused = bridge.replace_selection("перевод", doc=selection_over_a_table)

    assert refused["success"] is False
    assert "Table1 (2x2)" in refused["error"]
    assert "destroys them" in refused["error"]
    assert "read_table" in refused["error"]
    # and the table is still there
    assert bridge.list_tables(doc=selection_over_a_table)["count"] == 1


def test_flatten_goes_ahead_and_counts_the_table_it_destroyed(
        bridge, selection_over_a_table):
    flattened = bridge.replace_selection("перевод", flatten=True,
                                         doc=selection_over_a_table)

    assert flattened["success"] is True
    assert flattened["tables_dropped"] == 1


def test_reading_runs_of_such_a_range_says_what_it_cannot_reach(
        bridge, selection_over_a_table):
    runs = bridge.read_runs({"selection": True}, doc=selection_over_a_table)

    assert runs["success"] is True
    assert runs["spans_tables"] == [{"name": "Table1", "rows": 2,
                                     "columns": 2}]
    assert runs["spans_paragraphs"] == [1, 2]
    assert "read_table" in runs["note"]


# --- giving a table a look ---------------------------------------------------
# The grid lives in the TableBorder2 struct, and the Is*Valid flags beside
# each line are what make a change stick. A cell's background is BackColor
# with BackTransparent off — a cell has no FillStyle and no FillColor, which
# is the opposite of a paragraph. Both measured on a live LibreOffice.

@pytest.fixture
def table_doc():
    table = FakeTextTable("Table1",
                          cells=[["Operation", "Response"],
                                 ["{\n  hero\n}", "R2-D2"]],
                          after_paragraph=0)
    return writer_doc(["Пример:", "После таблицы"], caret=(0, 0),
                      tables=[table])


def styled(doc, cell):
    return doc.tables[0].getCellByName(cell)


def test_draws_the_grid(bridge, table_doc):
    done = bridge.format_table("Table1", border=True, border_color="#B0B0B0",
                               border_width=0.5, doc=table_doc)

    assert done["success"] is True
    border = table_doc.tables[0].TableBorder2
    assert border.TopLine.LineWidth == 50
    assert border.TopLine.Color == 0xB0B0B0
    assert border.HorizontalLine.LineWidth == 50
    assert border.IsTopLineValid is True
    assert border.IsHorizontalLineValid is True


def test_takes_the_grid_away(bridge, table_doc):
    bridge.format_table("Table1", border=True, doc=table_doc)

    bridge.format_table("Table1", border=False, doc=table_doc)

    assert table_doc.tables[0].TableBorder2.TopLine.LineWidth == 0


def test_only_the_outline_when_asked(bridge, table_doc):
    bridge.format_table("Table1", border=True, border_width=0.5,
                        inner_borders=False, doc=table_doc)

    border = table_doc.tables[0].TableBorder2
    assert border.TopLine.LineWidth == 50
    assert border.HorizontalLine.LineWidth == 18      # left as it was


def test_sets_the_padding_inside_the_cells(bridge, table_doc):
    bridge.format_table("Table1", padding_mm=1.5, doc=table_doc)

    border = table_doc.tables[0].TableBorder2
    assert border.Distance == 150
    assert border.IsDistanceValid is True


def test_backgrounds_every_cell_by_default(bridge, table_doc):
    done = bridge.format_table("Table1", background_color="#F7F7F7",
                               doc=table_doc)

    assert done["cells_touched"] == 4
    for cell in ("A1", "B1", "A2", "B2"):
        assert styled(table_doc, cell).BackColor == 0xF7F7F7
        assert styled(table_doc, cell).BackTransparent is False


def test_backgrounds_only_the_cells_asked_for(bridge, table_doc):
    bridge.format_table("Table1", cells=["A2"], background_color="#EEEEEE",
                        doc=table_doc)

    assert styled(table_doc, "A2").BackColor == 0xEEEEEE
    assert styled(table_doc, "A1").BackTransparent is True


def test_a_row_a_column_and_a_rectangle_can_be_named(bridge, table_doc):
    bridge.format_table("Table1", cells="row:2", background_color="#111111",
                        doc=table_doc)
    assert styled(table_doc, "A2").BackColor == 0x111111
    assert styled(table_doc, "A1").BackTransparent is True

    bridge.format_table("Table1", cells="column:B", background_color="#222222",
                        doc=table_doc)
    assert styled(table_doc, "B1").BackColor == 0x222222

    bridge.format_table("Table1", cells="A1:B2", background_color="#333333",
                        doc=table_doc)
    assert styled(table_doc, "A1").BackColor == 0x333333


def test_the_header_rows_are_marked_and_dressed(bridge, table_doc):
    done = bridge.format_table("Table1", header_rows=1, repeat_heading=True,
                               header_background_color="#E4E4E4",
                               header_bold=True, doc=table_doc)

    assert done["success"] is True
    table = table_doc.tables[0]
    assert table.HeaderRowCount == 1
    assert table.RepeatHeadline is True
    assert styled(table_doc, "A1").BackColor == 0xE4E4E4
    assert styled(table_doc, "A1").formatting["CharWeight"] > 120
    # and the body row is left alone
    assert styled(table_doc, "A2").BackTransparent is True
    assert "CharWeight" not in styled(table_doc, "A2").formatting


def test_puts_a_paragraph_style_on_the_cells(bridge, table_doc):
    bridge.format_table("Table1", cells="row:2",
                        paragraph_style="Preformatted Text", doc=table_doc)

    assert styled(table_doc, "A2").styles == ["Preformatted Text"] * 3
    assert styled(table_doc, "A1").styles == ["Table Contents"]


def test_a_style_the_document_lacks_is_refused(bridge, table_doc):
    refused = bridge.format_table("Table1", paragraph_style="Nope",
                                  doc=table_doc)

    assert refused["success"] is False
    assert "list_styles" in refused["error"]


def test_sets_the_column_widths(bridge, table_doc):
    bridge.format_table("Table1", column_widths_percent=[45, 55],
                        doc=table_doc)

    positions = [separator.Position
                 for separator in table_doc.tables[0].TableColumnSeparators]
    assert positions == [4500]


def test_shares_that_do_not_add_up_are_refused(bridge, table_doc):
    refused = bridge.format_table("Table1", column_widths_percent=[10, 10],
                                  doc=table_doc)

    assert refused["success"] is False
    assert "add up to 100" in refused["error"]


def test_one_share_per_column_or_nothing(bridge, table_doc):
    refused = bridge.format_table("Table1", column_widths_percent=[50, 30, 20],
                                  doc=table_doc)

    assert refused["success"] is False
    assert "one number per column" in refused["error"]


def test_a_cell_that_is_not_there_is_refused_before_anything_changes(
        bridge, table_doc):
    refused = bridge.format_table("Table1", cells=["Z9"], border=True,
                                  doc=table_doc)

    assert refused["success"] is False
    assert "Z9" in refused["error"]
    assert table_doc.tables[0].TableBorder2.IsTopLineValid is False


def test_asking_for_nothing_is_refused(bridge, table_doc):
    refused = bridge.format_table("Table1", doc=table_doc)

    assert refused["success"] is False
    assert "Nothing to change" in refused["error"]


def test_formats_the_table_the_caret_is_in(bridge):
    table = FakeTextTable("Table1", cells=[["a", "b"], ["c", "d"]],
                          after_paragraph=0)
    doc = writer_doc(["Пример:"], caret=(0, 0), tables=[table],
                     caret_in_cell=("Table1", "A1"))

    done = bridge.format_table(background_color="#F0F0F0", doc=doc)

    assert done["success"] is True
    assert done["table"] == "Table1"


def test_the_formatting_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    table = FakeTextTable("Table1", cells=[["a", "b"], ["c", "d"]],
                          after_paragraph=0)
    doc = writer_doc(["Пример:"], caret=(0, 0), tables=[table])
    server.uno_bridge.desktop = FakeDesktop([doc])

    done = asyncio.run(server.execute_tool(
        "format_table_live", {"name": "Table1", "border": True,
                              "background_color": "#F7F7F7",
                              "header_rows": 1, "header_bold": True}))

    assert done["success"] is True
    assert done["cells_touched"] == 4
    assert "border" in done["changed"]


# --- addressing a cell -------------------------------------------------------
# A cell is its own text, so a hit found in one used to come back as
# {"paragraph": null} — an address nothing could act on. A cell has an
# address of its own now, and the text tools take it like any other.

@pytest.fixture
def addressable():
    table = FakeTextTable("Table1",
                          cells=[["Operation", "Response"],
                                 ["{\n  hero\n}", "R2-D2"]],
                          after_paragraph=0)
    return writer_doc(["Пример:", "После"], caret=(0, 0), tables=[table])


def test_a_cell_can_be_addressed(bridge, addressable):
    span = bridge._resolve_address(addressable,
                                   {"table": "Table1", "cell": "A1"})

    assert span.getString() == "Operation"


def test_part_of_a_cell_can_be_addressed(bridge, addressable):
    span = bridge._resolve_address(addressable, {"table": "Table1",
                                                 "cell": "A1",
                                                 "offset": 0, "length": 5})

    assert span.getString() == "Opera"


def test_an_offset_counts_across_the_paragraphs_of_a_cell(bridge, addressable):
    # "{\n  hero\n}" — the break counts as one character, as the cursor counts it
    span = bridge._resolve_address(addressable, {"table": "Table1",
                                                 "cell": "A2",
                                                 "offset": 2, "length": 6})

    assert span.getString() == "  hero"


def test_the_table_can_be_left_out_when_the_caret_is_in_one(bridge):
    table = FakeTextTable("Table1", cells=[["a", "b"], ["c", "d"]],
                          after_paragraph=0)
    doc = writer_doc(["Пример:"], caret=(0, 0), tables=[table],
                     caret_in_cell=("Table1", "B2"))

    span = bridge._resolve_address(doc, {"cell": "B2"})

    assert span.getString() == "d"


def test_a_cell_address_is_checked(bridge, addressable):
    from uno_bridge import AddressError

    for address, expected in (
            ({"table": "Nope", "cell": "A1"}, "no table called"),
            ({"table": "Table1", "cell": "Z9"}, "has no cell"),
            ({"table": "Table1"}, "needs a cell"),
            ({"table": "Table1", "cell": "A1", "offset": 99}, "is outside cell"),
            ({"table": "Table1", "cell": "A1", "length": 99}, "runs past")):
        with pytest.raises(AddressError, match=expected):
            bridge._resolve_address(addressable, address)


def test_text_found_in_a_cell_gets_a_usable_address(bridge, addressable):
    cell = addressable.tables[0].getCellByName("B2")
    hit = cell.model.createTextCursorByRange(
        bridge._position_in_text(cell, 0))
    hit.goRight(5, True)

    located, _cursor, _before = bridge._locate_range(addressable, hit)

    assert located["paragraph"] is None        # honestly, no body paragraph
    assert located["table"] == "Table1"
    assert located["cell"] == "B2"
    assert located["offset"] == 0
    assert located["length"] == 5
    # and that address resolves straight back
    assert bridge._resolve_address(addressable, located).getString() == "R2-D2"


def test_the_tools_take_a_cell_address(bridge, addressable):
    read = bridge.read_runs({"table": "Table1", "cell": "A1"},
                            doc=addressable)
    assert read["success"] is True
    assert [run["text"] for run in read["runs"]] == ["Operation"]

    written = bridge.replace_range({"table": "Table1", "cell": "B2"},
                                   "R2-D2 и C-3PO", doc=addressable)
    assert written["success"] is True
    assert bridge.read_table("Table1", cell="B2",
                             doc=addressable)["text"] == "R2-D2 и C-3PO"

    styled = bridge.apply_paragraph_style({"table": "Table1", "cell": "A2"},
                                          "Preformatted Text", doc=addressable)
    assert styled["success"] is True

    marked = bridge.set_language({"table": "Table1", "cell": "A1"}, "ru-RU",
                                 doc=addressable)
    assert marked["success"] is True


def test_a_comment_can_be_put_on_a_cell(bridge, addressable):
    added = bridge.add_comment({"table": "Table1", "cell": "A1"},
                               "Термин", doc=addressable)

    assert added["success"] is True
    assert added["anchor_text"] == "Operation"


# Formatting the text inside cells: a heading row, code, and colours on the
# pieces of it. The runs of a cell must come back addressed to that cell —
# a body address with paragraph None resolves to nothing, which is what made
# replace_runs fail there with "paragraph must be a non-negative integer".

def test_the_runs_of_a_cell_are_addressed_to_that_cell(bridge, addressable):
    runs = bridge.read_runs({"table": "Table1", "cell": "A2"},
                            doc=addressable)["runs"]

    assert runs[0]["address"]["table"] == "Table1"
    assert runs[0]["address"]["cell"] == "A2"
    assert "paragraph" not in runs[0]["address"]
    # and each address resolves back to its own run
    for run in runs:
        assert bridge._resolve_address(
            addressable, run["address"]).getString() == run["text"]


def test_a_piece_of_a_cell_can_be_coloured(bridge, addressable):
    """The fake records the formatting against the span it was given; that a
    coloured piece then reads back as its own run is a question only a live
    LibreOffice answers, and the live checks ask it."""
    done = bridge.format_range({"table": "Table1", "cell": "A1",
                                "offset": 0, "length": 5},
                               color="#0B7285", doc=addressable)

    assert done["success"] is True
    cell = addressable.tables[0].getCellByName("A1")
    recorded = cell.model.char_formatting[-1]
    assert recorded["span"] == ((0, 0), (0, 5))
    assert recorded["CharColor"] == 0x0B7285


def test_the_runs_of_a_cell_go_back_through_replace_runs(bridge, addressable):
    runs = bridge.read_runs({"table": "Table1", "cell": "B2"},
                            doc=addressable)["runs"]

    written = bridge.replace_runs({"table": "Table1", "cell": "B2"},
                                  [dict(run, text="R2-D2 и C-3PO")
                                   for run in runs], doc=addressable)

    assert written["success"] is True
    assert bridge.read_table("Table1", cell="B2",
                             doc=addressable)["text"] == "R2-D2 и C-3PO"


def test_the_first_row_can_be_made_a_heading(bridge, addressable):
    for cell in ("A1", "B1"):
        styled = bridge.apply_paragraph_style({"table": "Table1", "cell": cell},
                                              "Table Heading", doc=addressable)
        assert styled["success"] is True

    assert addressable.tables[0].getCellByName("A1").styles \
        == ["Table Heading"]


def test_a_code_row_can_be_made_monospace(bridge, addressable):
    done = bridge.format_table("Table1", cells="row:2",
                               paragraph_style="Preformatted Text",
                               doc=addressable)

    assert done["success"] is True
    assert addressable.tables[0].getCellByName("A2").styles[0] \
        == "Preformatted Text"


# --- making a table, and taking one away -------------------------------------
# A table goes in *before* the paragraph the address points at: inserting at
# the end of one splits it and leaves an empty paragraph behind — measured.
# With replace, the paragraphs it stands in for are cleared, which needs two
# steps because clearing a span leaves one empty paragraph.

@pytest.fixture
def plain():
    return writer_doc(["Перед таблицей.", "Операция", "{ hero }", "Ответ",
                       '{ "R2-D2" }', "После таблицы."], caret=(1, 0))


def test_makes_a_table_where_it_is_told(bridge, plain):
    made = bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                               doc=plain)

    assert made["success"] is True
    assert (made["rows"], made["columns"]) == (2, 2)
    listed = bridge.list_tables(doc=plain)
    assert listed["count"] == 1
    # and the text is untouched
    assert bridge.read_paragraphs(start=0, count=9,
                                  doc=plain)["count"] == 6


def test_fills_the_cells_it_is_given(bridge, plain):
    made = bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                               cells=[["Операция", "Ответ"],
                                      ["{ hero }", '{ "R2-D2" }']],
                               doc=plain)

    assert made["cells_filled"] == 4
    read = bridge.read_table(made["table"], doc=plain)
    assert [[cell["text"] for cell in row] for row in read["rows"]] \
        == [["Операция", "Ответ"], ["{ hero }", '{ "R2-D2" }']]


def test_takes_a_name_and_a_heading(bridge, plain):
    made = bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                               name="QueryAndResponse", header_rows=1,
                               repeat_heading=True, doc=plain)

    assert made["table"] == "QueryAndResponse"
    described = bridge.list_tables(doc=plain)["tables"][0]
    assert described["header_rows"] == 1
    assert described["repeat_heading"] is True


def test_replaces_the_paragraphs_it_stands_in_for(bridge, plain):
    made = bridge.create_table({"paragraph": 1, "offset": 0, "length": 8},
                               rows=2, columns=2,
                               cells=[["Операция", "Ответ"]],
                               replace=True, doc=plain)

    assert made["success"] is True
    assert made["paragraphs_replaced"] == [1]
    left = [p["text"] for p in
            bridge.read_paragraphs(start=0, count=9, doc=plain)["paragraphs"]]
    assert "Операция" not in left
    assert left[0] == "Перед таблицей."
    assert bridge.list_tables(doc=plain)["count"] == 1


def test_a_table_is_refused_where_it_would_eat_a_comment(bridge):
    note = FakeAnnotation("Ревьюер", "не трогать")
    doc = writer_doc(["Перед", "Операция"], caret=(1, 0),
                     portions={1: [{"kind": "Annotation", "text": "",
                                    "field": note},
                                   {"text": "Операция"},
                                   {"kind": "AnnotationEnd", "text": ""}]})

    refused = bridge.create_table({"paragraph": 1}, replace=True, doc=doc)

    assert refused["success"] is False
    assert "comment" in refused["error"]
    assert bridge.list_tables(doc=doc)["count"] == 0

    allowed = bridge.create_table({"paragraph": 1}, replace=True, flatten=True,
                                  doc=doc)
    assert allowed["success"] is True


def test_the_shape_is_checked_before_anything_is_made(bridge, plain):
    for arguments, expected in (
            ({"rows": 0}, "rows must be"),
            ({"columns": -1}, "columns must be"),
            ({"rows": 9000}, "at most"),
            ({"cells": "not rows"}, "list of rows"),
            ({"rows": 1, "cells": [["a"], ["b"]]}, "rows of text"),
            ({"columns": 1, "cells": [["a", "b"]]}, "cells for a table"),
            ({"header_rows": 5, "rows": 2}, "header_rows must be")):
        refused = bridge.create_table({"paragraph": 1}, doc=plain, **arguments)
        assert refused["success"] is False, arguments
        assert expected in refused["error"], (arguments, refused["error"])
    assert bridge.list_tables(doc=plain)["count"] == 0


def test_a_name_already_taken_is_refused(bridge, plain):
    bridge.create_table({"paragraph": 1}, name="Пример", doc=plain)

    refused = bridge.create_table({"paragraph": 1}, name="Пример", doc=plain)

    assert refused["success"] is False
    assert "already has a table" in refused["error"]


def test_deletes_a_table_and_says_what_it_held(bridge, plain):
    bridge.create_table({"paragraph": 1}, rows=2, columns=2,
                        cells=[["Операция", "Ответ"]], name="Пример",
                        doc=plain)

    removed = bridge.delete_table("Пример", doc=plain)

    assert removed["success"] is True
    assert (removed["rows"], removed["columns"]) == (2, 2)
    assert {entry["text"] for entry in removed["held"]} == {"Операция", "Ответ"}
    assert bridge.list_tables(doc=plain)["count"] == 0
    # the text around it is untouched
    assert [p["text"] for p in bridge.read_paragraphs(
        start=0, count=9, doc=plain)["paragraphs"]][0] == "Перед таблицей."


def test_deleting_a_table_that_is_not_there_is_refused(bridge, plain):
    refused = bridge.delete_table("Нет такой", doc=plain)

    assert refused["success"] is False
    assert "Нет такой" in refused["error"]


def test_the_making_tools_are_registered_and_dispatch(tmp_path):
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    doc = writer_doc(["Перед", "Операция", "Ответ"], caret=(1, 0))
    server.uno_bridge.desktop = FakeDesktop([doc])

    made = asyncio.run(server.execute_tool(
        "create_table_live", {"address": {"paragraph": 1}, "rows": 2,
                              "columns": 2,
                              "cells": [["Операция", "Ответ"]],
                              "name": "Пример"}))
    assert made["success"] is True
    assert made["cells_filled"] == 2

    removed = asyncio.run(server.execute_tool("delete_table_live",
                                              {"name": "Пример"}))
    assert removed["success"] is True


# --- reading a table's look --------------------------------------------------
# Nothing reported a border, a background or a padding, so an assistant asked
# to make a second table look like the first unzipped the document and read
# styles.xml. describe_table answers the same questions in the units
# format_table takes, so a look can be read off one table and put on another.

def test_describes_the_grid_and_the_padding(bridge, table_doc):
    bridge.format_table("Table1", border=True, border_color="#B0B0B0",
                        border_width=0.35, padding_mm=2.0, doc=table_doc)

    described = bridge.describe_table("Table1", doc=table_doc)["table"]

    assert described["border"]["outer"] == {"width_mm": 0.35,
                                            "color": "#B0B0B0"}
    assert described["border"]["inner"] == {"width_mm": 0.35,
                                            "color": "#B0B0B0"}
    assert described["padding_mm"] == 2.0


def test_a_fresh_table_reports_the_grid_writer_gives_it(bridge, table_doc):
    """A table comes with a thin grid of its own — 0.18 mm, measured — so
    "no border asked for" is not the same as "no border"."""
    described = bridge.describe_table("Table1", doc=table_doc)["table"]

    assert described["border"]["outer"] == {"width_mm": 0.18,
                                            "color": "#000000"}

    bridge.format_table("Table1", border=False, doc=table_doc)
    assert bridge.describe_table("Table1",
                                 doc=table_doc)["table"]["border"]["outer"] \
        is None


def test_describes_the_background_of_every_cell(bridge, table_doc):
    bridge.format_table("Table1", background_color="#F7F7F7", header_rows=1,
                        header_background_color="#EFEFEF", doc=table_doc)

    cells = {cell["cell"]: cell
             for cell in bridge.describe_table("Table1", doc=table_doc)["cells"]}

    assert cells["A1"]["background_color"] == "#EFEFEF"
    assert cells["A2"]["background_color"] == "#F7F7F7"
    assert (cells["A1"]["row"], cells["A1"]["column"]) == (1, 1)


def test_a_cell_with_no_background_says_none(bridge, table_doc):
    cells = {cell["cell"]: cell
             for cell in bridge.describe_table("Table1", doc=table_doc)["cells"]}

    assert cells["A1"]["background_color"] is None


def test_describes_the_styles_the_cells_use(bridge, table_doc):
    bridge.format_table("Table1", cells="row:2",
                        paragraph_style="Preformatted Text", doc=table_doc)

    cells = {cell["cell"]: cell
             for cell in bridge.describe_table("Table1", doc=table_doc)["cells"]}

    assert cells["A2"]["paragraph_styles"] == ["Preformatted Text"]
    assert cells["A1"]["paragraph_styles"] == ["Table Contents"]


def test_the_cells_can_be_left_out(bridge, table_doc):
    described = bridge.describe_table("Table1", cells=False, doc=table_doc)

    assert "cells" not in described
    assert described["table"]["rows"] == 2


def test_the_runs_of_the_cells_come_too_when_asked(bridge, table_doc):
    described = bridge.describe_table("Table1", runs=True, doc=table_doc)

    cells = {cell["cell"]: cell for cell in described["cells"]}
    assert [run["text"] for run in cells["A1"]["runs"]] == ["Operation"]


def test_describing_a_table_that_is_not_there_is_refused(bridge, table_doc):
    refused = bridge.describe_table("Нет такой", doc=table_doc)

    assert refused["success"] is False
    assert "Нет такой" in refused["error"]


def test_the_describing_tool_is_registered_and_dispatches():
    from mcp_server import LibreOfficeMCPServer

    server = LibreOfficeMCPServer()
    table = FakeTextTable("Table1", cells=[["a", "b"], ["c", "d"]],
                          after_paragraph=0)
    doc = writer_doc(["Пример:"], caret=(0, 0), tables=[table])
    server.uno_bridge.desktop = FakeDesktop([doc])

    described = asyncio.run(server.execute_tool("describe_table_live",
                                                {"name": "Table1"}))
    assert described["success"] is True
    assert described["table"]["columns"] == 2
    assert len(described["cells"]) == 4


# --- a block says what it spans without being compared with the document ----
#
# A block address names its own paragraphs, and _range_spans compared it with
# every paragraph and every table anyway: on a 6981-paragraph document
# read_runs over two paragraphs cost 23s against 1.5s for one.

def test_runs_over_a_block_say_which_table_stands_in_it(bridge, table_doc):
    runs = bridge.read_runs({"paragraph": 0, "through": 1}, doc=table_doc)

    assert runs["success"] is True
    assert runs["spans_paragraphs"] == [0, 1]
    assert runs["spans_tables"] == [{"name": "Table1", "rows": 2,
                                     "columns": 2}]
    assert "read_table" in runs["note"]


def test_a_block_is_not_compared_with_every_paragraph_of_the_document(
        bridge, table_doc, monkeypatch):
    def refuse(*arguments, **named):
        raise AssertionError("read_runs walked the body to place a block")

    monkeypatch.setattr(bridge, "_range_spans", refuse)

    runs = bridge.read_runs({"paragraph": 0, "through": 1}, doc=table_doc)

    assert runs["spans_paragraphs"] == [0, 1]
    assert runs["spans_tables"][0]["name"] == "Table1"


def test_a_table_outside_the_block_is_not_reported(bridge):
    table = FakeTextTable("Table1", cells=[["a", "b"]], after_paragraph=2)
    doc = writer_doc(["Ноль", "Один", "Два"], caret=(0, 0), tables=[table])

    runs = bridge.read_runs({"paragraph": 0, "through": 1}, doc=doc)

    assert "spans_tables" not in runs
    assert runs["spans_paragraphs"] == [0, 1]
