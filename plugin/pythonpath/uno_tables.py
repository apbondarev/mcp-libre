"""Tables: reading them, saying where they are, giving them a look.

A cell is its own text, so a caret in one belongs to no body paragraph. A
selection running from text through a table comes back as one range whose
string folds the cells in with newlines, and replacing it destroys the table
— which is why a range is asked separately what it spans. The grid lives in
the TableBorder2 struct, where the Is*Valid flag beside each line is what
makes a change stick.
"""

import uno
from typing import Any, Optional, Dict, List
import logging
from uno_values import (AddressError, CELL_SERVICE, TABLE_SERVICE, _cell_position, 
    _colour, _colour_name, _column_letters, _column_shares, _get_property, 
    _supports, _table_size, _text_payload, _millimetres)

logger = logging.getLogger(__name__)


def _border_look(border: Any) -> Optional[Dict[str, Any]]:
    """A border line as format_table would take it back, or None for none"""
    if border is None:
        return None
    width = _get_property(border, "LineWidth", 0) or 0
    if not width:
        return None
    colour = _get_property(border, "Color", 0) or 0
    return {"width_mm": round(width / 100.0, 2),
            "color": _colour_name(colour & 0xFFFFFF)}


def _background_of(thing: Any) -> Optional[str]:
    """The background colour, or None when there is none.

    A cell or a table shows its colour only with BackTransparent off, so the
    flag decides whether BackColor means anything at all.
    """
    if _get_property(thing, "BackTransparent", True):
        return None
    colour = _get_property(thing, "BackColor", -1)
    if colour in (-1, None):
        return None
    return _colour_name(colour & 0xFFFFFF)

# A table larger than this is a mistake, not a wish.
MAX_TABLE_ROWS, MAX_TABLE_COLUMNS = 500, 64


class TablesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _covers(self, body: Any, span: Any, element: Any) -> bool:
        """Whether `span` overlaps `element`, both being ranges of `body`"""
        try:
            ends_before = body.compareRegionStarts(element.getEnd(),
                                                   span.getStart()) == 1
            starts_after = body.compareRegionStarts(span.getEnd(),
                                                    element.getStart()) == 1
            return not ends_before and not starts_after
        except Exception as e:
            logger.info(f"Could not compare two ranges: {e}")
            return False

    def _within_one_paragraph(self, body: Any, span: Any) -> bool:
        """Whether a range begins and ends inside the same body paragraph.

        Four UNO calls, against the six per paragraph the sweep below spends:
        on a 300-paragraph document that is the difference between half a
        second and nothing, and almost every range asked about — a run, a
        phrase, a paragraph — is inside one paragraph.
        """
        try:
            reach = body.createTextCursorByRange(span.getStart())
            reach.gotoEndOfParagraph(True)
            return body.compareRegionStarts(reach.getEnd(),
                                            span.getEnd()) != 1
        except Exception:
            return False

    def _range_spans(self, doc: Any, span: Any,
                     known_paragraph: Optional[int] = None) -> Dict[str, Any]:
        """
        What a range actually covers: which body paragraphs, which tables

        A selection can run from text through a table and out the other side,
        and its string gives no hint of that — the cells arrive folded in
        with newlines. Walking the body and comparing regions says what is
        really in there.

        The walk is only made when it can tell the caller something: a range
        inside a table cell is in no body paragraph and runs through no
        table, and a range inside one paragraph holds no table either, since
        a table sits *between* paragraphs. `known_paragraph` saves the second
        case its one remaining walk when the caller already has the index.
        """
        found = {"paragraphs": [], "tables": []}
        try:
            body = doc.getText()
            if _supports(span.getText(), CELL_SERVICE):
                return found

            if self._within_one_paragraph(body, span):
                if known_paragraph is not None:
                    found["paragraphs"] = [known_paragraph]
                else:
                    index, _ = self._locate_paragraph(body, span.getStart())
                    if index is not None:
                        found["paragraphs"] = [index]
                return found

            positions = None
            index = 0
            enumeration = body.createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if _supports(element, TABLE_SERVICE):
                    try:
                        anchor = element.getAnchor()
                    except Exception as e:
                        logger.info(f"A table would not say where it is: {e}")
                        continue
                    if self._covers(body, span, anchor):
                        if positions is None:
                            positions = self._table_positions(doc)
                        found["tables"].append(
                            self._describe_table(doc, element, positions))
                    continue
                if not hasattr(element, "getStart"):
                    continue
                if self._covers(body, span, element):
                    found["paragraphs"].append(index)
                index += 1
        except Exception as e:
            logger.info(f"Could not work out what a range spans: {e}")
        return found

    def _tables_in(self, doc: Any, span: Any) -> List[Dict[str, Any]]:
        """The tables a range runs through, each described"""
        return self._range_spans(doc, span)["tables"]

    def _table_positions(self, doc: Any) -> Dict[str, int]:
        """Where every table sits, counted in body paragraphs, in one walk.

        Asking each table separately walks the whole document again: eleven
        tables in a three-hundred-paragraph document meant eleven walks, and
        list_tables spent a second of its own on them.
        """
        positions: Dict[str, int] = {}
        try:
            paragraphs = 0
            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if _supports(element, TABLE_SERVICE):
                    positions[_get_property(element, "Name", "") or ""] = \
                        paragraphs
                elif hasattr(element, "getStart"):
                    paragraphs += 1
        except Exception as e:
            logger.info(f"Could not place the tables in the text: {e}")
        return positions

    def _paragraphs_before_table(self, doc: Any, wanted: Any) -> Optional[int]:
        """
        How many body paragraphs come before a table

        Addresses count body paragraphs and skip tables, so this is the only
        honest way to say where a table sits in the text.
        """
        try:
            name = _get_property(wanted, "Name", "") or ""
            paragraphs = 0
            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if _supports(element, TABLE_SERVICE):
                    if (_get_property(element, "Name", "") or "") == name:
                        return paragraphs
                    continue
                if hasattr(element, "getStart"):
                    paragraphs += 1
        except Exception as e:
            logger.info(f"Could not place a table in the text: {e}")
        return None

    def _describe_table(self, doc: Any, table: Any,
                        positions: Optional[Dict[str, int]] = None
                        ) -> Dict[str, Any]:
        """A table as a caller sees it, without its contents

        `positions` is the map _table_positions makes in one walk; without
        it, this table's place is found by a walk of its own.
        """
        rows, columns = _table_size(table)
        try:
            names = list(table.getCellNames())
        except Exception:
            names = []
        return {"name": _get_property(table, "Name", "") or "",
                "rows": rows, "columns": columns,
                "cells": len(names),
                "merged": bool(names) and len(names) != rows * columns,
                "header_rows": _get_property(table, "HeaderRowCount", 0) or 0,
                "repeat_heading": bool(_get_property(table, "RepeatHeadline",
                                                     False)),
                "column_widths_percent": _column_shares(table),
                "width_relative_percent": (
                    round((_get_property(table, "RelativeWidth", 0) or 0) / 100.0,
                          1)
                    if _get_property(table, "IsWidthRelative", False) else None),
                "after_paragraph": (
                    positions.get(_get_property(table, "Name", "") or "")
                    if positions is not None
                    else self._paragraphs_before_table(doc, table))}

    def _note_what_is_out_of_reach(self, doc: Any, span: Any,
                                   result: Dict[str, Any],
                                   known_paragraph: Optional[int] = None
                                   ) -> Dict[str, Any]:
        """Say when a range reaches past the paragraph its runs come from"""
        spans = self._range_spans(doc, span, known_paragraph)
        if len(spans["paragraphs"]) > 1:
            result["spans_paragraphs"] = spans["paragraphs"]
        if spans["tables"]:
            result["spans_tables"] = [{"name": table["name"],
                                       "rows": table["rows"],
                                       "columns": table["columns"]}
                                      for table in spans["tables"]]
            result["note"] = ("this range runs through a table, whose cells "
                              "are not runs of this paragraph; read_table "
                              "reads them")
        return result

    def create_table(self, address: Any = None, rows: int = 2,
                     columns: int = 2, cells: Any = None,
                     name: Optional[str] = None,
                     header_rows: Optional[int] = None,
                     repeat_heading: Optional[bool] = None,
                     replace: bool = False, flatten: bool = False,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Put a new table into the text, and fill it

        The table goes in *before* the paragraph the address points at, which
        is where it lands cleanly: inserting at the end of a paragraph splits
        it and leaves an empty one behind — measured.

        With `replace`, the paragraphs the address covers are cleared away
        afterwards, so "turn this query and response into a table" is one
        call. That throws text away, so it is refused when those paragraphs
        hold comments, pictures or a table of their own, unless `flatten`
        says to go ahead.
        """
        doc, error = self._writer_document(doc, "Creating a table")
        if error:
            return error

        for label, value in (("rows", rows), ("columns", columns)):
            if not isinstance(value, int) or isinstance(value, bool) \
                    or value < 1:
                return {"success": False,
                        "error": f"{label} must be a whole number from 1, got "
                                 f"{value!r}"}
        if rows > MAX_TABLE_ROWS or columns > MAX_TABLE_COLUMNS:
            return {"success": False,
                    "error": f"a table here is at most {MAX_TABLE_ROWS} rows "
                             f"by {MAX_TABLE_COLUMNS} columns"}

        content = []
        if cells is not None:
            if not isinstance(cells, (list, tuple)):
                return {"success": False,
                        "error": 'cells must be a list of rows, as in '
                                 '[["Operation", "Response"], ["{...}", "..."]]'}
            if len(cells) > rows:
                return {"success": False,
                        "error": f"{len(cells)} rows of text were given for a "
                                 f"table of {rows}"}
            for position, row in enumerate(cells):
                if not isinstance(row, (list, tuple)):
                    return {"success": False,
                            "error": f"row {position + 1} of cells must be a "
                                     f"list of strings"}
                if len(row) > columns:
                    return {"success": False,
                            "error": f"row {position + 1} has {len(row)} cells "
                                     f"for a table of {columns} columns"}
                content.append([("" if value is None else str(value))
                                for value in row])

        if name is not None:
            if not isinstance(name, str) or not name.strip():
                return {"success": False, "error": "name must be a name"}
            name = name.strip()
            if self._table_by_name(doc, name) is not None:
                return {"success": False,
                        "error": f"this document already has a table called "
                                 f"{name!r}"}
        if header_rows is not None and (not isinstance(header_rows, int)
                                        or isinstance(header_rows, bool)
                                        or header_rows < 0
                                        or header_rows > rows):
            return {"success": False,
                    "error": f"header_rows must be between 0 and {rows}, got "
                             f"{header_rows!r}"}

        try:
            target = self._resolve_address(doc, address
                                           if address is not None
                                           else {"selection": True})
            located, paragraph_cursor, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return {"success": False, "error": str(e)}
        if located.get("paragraph") is None:
            return {"success": False,
                    "error": "A table goes into the body text, and that "
                             "address is not in it — give a paragraph"}

        covered = self._range_spans(doc, target)["paragraphs"] \
            or [located["paragraph"]]
        first, last = covered[0], covered[-1]

        losing = None
        if replace and not flatten:
            try:
                losing = self._flattening_loss(doc, located, paragraph_cursor)
            except Exception as e:
                logger.info(f"Could not count what replacing would cost: {e}")
            inside = self._tables_in(doc, target)
            if inside:
                return {"success": False,
                        "error": f"those paragraphs run through "
                                 f"{len(inside)} table"
                                 f"{'s' if len(inside) > 1 else ''}, which a "
                                 f"new table would replace outright; pass "
                                 f"flatten=true to accept that"}
            if losing and (losing.get("comments") or losing.get("inline_images")
                           or losing.get("links")):
                details = []
                for key, word in (("comments", "comment"),
                                  ("inline_images", "inline picture"),
                                  ("links", "hyperlink")):
                    if losing.get(key):
                        details.append(f"{losing[key]} {word}"
                                       f"{'s' if losing[key] > 1 else ''}")
                return {"success": False,
                        "error": f"the paragraphs this table would replace "
                                 f"hold {', '.join(details)}, which would go "
                                 f"with them; read them first, or pass "
                                 f"flatten=true"}

        def edit():
            table = doc.createInstance(TABLE_SERVICE)
            table.initialize(rows, columns)
            if name:
                table.Name = name
            body = doc.getText()
            paragraph = self._paragraph_at(body, first)
            body.insertTextContent(paragraph.getStart(), table, False)

            filled = 0
            for row_index, row in enumerate(content):
                for column_index, value in enumerate(row):
                    cell_name = f"{_column_letters(column_index)}{row_index + 1}"
                    try:
                        table.getCellByName(cell_name).setString(value)
                        filled += 1
                    except Exception as e:
                        logger.info(f"Could not fill {cell_name}: {e}")
            if header_rows is not None:
                table.HeaderRowCount = header_rows
            if repeat_heading is not None:
                table.RepeatHeadline = bool(repeat_heading)

            replaced = 0
            if replace:
                # Clearing the span leaves one empty paragraph behind, which
                # goes too — measured, and the reason this is two steps.
                start = self._paragraph_at(body, first)
                end = self._paragraph_at(body, last)
                if start is not None and end is not None:
                    span = body.createTextCursorByRange(start.getStart())
                    span.gotoRange(end.getEnd(), True)
                    replaced = len(span.getString())
                    span.setString("")
                    leftover = self._paragraph_at(body, first)
                    try:
                        body.removeTextContent(leftover)
                    except Exception as e:
                        logger.info(f"The empty paragraph stayed: {e}")

            return {"table": _get_property(table, "Name", "") or "",
                    "rows": rows, "columns": columns, "cells_filled": filled,
                    "after_paragraph": self._paragraphs_before_table(doc, table),
                    "paragraphs_replaced": covered if replace else [],
                    "characters_replaced": replaced}

        return self._guarded_edit(doc, "MCP: create table", track_changes, edit)

    def delete_table(self, name: Optional[str] = None,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Take a table out of the document, text and all

        Reports what it held, so what is lost is on the record and can be put
        back with create_table.
        """
        doc, error = self._writer_document(doc, "Deleting a table")
        if error:
            return error

        if not name:
            caret = self._caret_in_table(doc)
            if caret is None:
                listed = [_get_property(table, "Name", "") or "?"
                          for table in self._tables(doc)]
                return {"success": False,
                        "error": f"The caret is not in a table and none was "
                                 f"named. This document holds: "
                                 f"{', '.join(listed) or 'no tables'}."}
            name = caret["table"]

        table = self._table_by_name(doc, name)
        if table is None:
            listed = [_get_property(other, "Name", "") or "?"
                      for other in self._tables(doc)]
            return {"success": False,
                    "error": f"No table called {name!r} in this document. It "
                             f"holds: {', '.join(listed) or 'no tables'}."}

        described = self._describe_table(doc, table)
        held = []
        try:
            for cell_name in table.getCellNames():
                text = table.getCellByName(cell_name).getString()
                if text:
                    held.append({"cell": cell_name, "text": text})
        except Exception as e:
            logger.info(f"Could not read a table before removing it: {e}")

        def edit():
            doc.getText().removeTextContent(table)
            return {"deleted": described["name"], "rows": described["rows"],
                    "columns": described["columns"], "held": held}

        return self._guarded_edit(doc, "MCP: delete table", track_changes, edit)

    def list_tables(self, doc: Any = None) -> Dict[str, Any]:
        """
        Every table in the document: its name, its size and where it sits

        `after_paragraph` says how many body paragraphs come before it, since
        addresses count paragraphs and skip tables.
        """
        doc, error = self._writer_document(doc, "Listing tables")
        if error:
            return error

        positions = self._table_positions(doc)
        tables = [self._describe_table(doc, table, positions)
                  for table in self._tables(doc)]
        result = {"success": True, "tables": tables, "count": len(tables)}
        caret = self._caret_in_table(doc)
        if caret is not None:
            result["caret_is_in"] = {"table": caret["table"],
                                     "cell": caret["cell"]}
        try:
            span = self._resolve_address(doc, {"selection": True})
            selected = [table["name"] for table in self._tables_in(doc, span)]
            if selected:
                result["in_selection"] = selected
        except Exception:
            pass                       # no selection, or not a text one
        return result

    def describe_table(self, name: Optional[str] = None, cells: bool = True,
                       runs: bool = False, doc: Any = None) -> Dict[str, Any]:
        """
        What a table looks like: its grid, its padding, its cells

        The mirror of format_table, so the look of one table can be read and
        put on another — which otherwise means unzipping the document and
        reading styles.xml, since nothing else reports a border or a
        background. Values come back in the units format_table takes:
        millimetres and "#RRGGBB".
        """
        doc, error = self._writer_document(doc, "Describing a table")
        if error:
            return error

        if not name:
            caret = self._caret_in_table(doc)
            if caret is None:
                listed = [_get_property(table, "Name", "") or "?"
                          for table in self._tables(doc)]
                return {"success": False,
                        "error": f"The caret is not in a table and none was "
                                 f"named. This document holds: "
                                 f"{', '.join(listed) or 'no tables'}."}
            name = caret["table"]

        table = self._table_by_name(doc, name)
        if table is None:
            listed = [_get_property(other, "Name", "") or "?"
                      for other in self._tables(doc)]
            return {"success": False,
                    "error": f"No table called {name!r} in this document. It "
                             f"holds: {', '.join(listed) or 'no tables'}."}

        described = self._describe_table(doc, table)
        shape = _get_property(table, "TableBorder2", None)
        described["border"] = {
            "outer": _border_look(_get_property(shape, "TopLine", None)),
            "inner": _border_look(_get_property(shape, "HorizontalLine", None)),
            "left": _border_look(_get_property(shape, "LeftLine", None)),
            "right": _border_look(_get_property(shape, "RightLine", None)),
            "bottom": _border_look(_get_property(shape, "BottomLine", None)),
            "vertical": _border_look(_get_property(shape, "VerticalLine", None)),
        }
        described["padding_mm"] = _millimetres(_get_property(shape, "Distance",
                                                             None))
        described["background_color"] = _background_of(table)
        described["split"] = bool(_get_property(table, "Split", True))
        described["keep_together"] = bool(_get_property(table, "KeepTogether",
                                                        False))

        result = {"success": True, "table": described}
        if not cells:
            return result

        looks = []
        try:
            names = list(table.getCellNames())
        except Exception as e:
            logger.error(f"Could not read the cells of {name}: {e}")
            return {"success": False, "error": str(e)}

        for cell_name in names:
            cell = table.getCellByName(cell_name)
            row, column = _cell_position(cell_name)
            styles = []
            for paragraph in self._paragraphs_of(cell):
                style = _get_property(paragraph, "ParaStyleName", "") or ""
                if style and style not in styles:
                    styles.append(style)
            look = {"cell": cell_name, "row": row, "column": column,
                    "background_color": _background_of(cell),
                    "paragraph_styles": styles,
                    "vertical_align": _get_property(cell, "VertOrient", None),
                    "borders": {side: _border_look(_get_property(cell, side,
                                                                 None))
                                for side in ("TopBorder", "BottomBorder",
                                             "LeftBorder", "RightBorder")}}
            if runs:
                try:
                    look["runs"] = self.read_runs(
                        {"table": described["name"], "cell": cell_name},
                        doc=doc).get("runs", [])
                except Exception as e:
                    logger.info(f"Could not read the runs of {cell_name}: {e}")
                    look["runs"] = []
            looks.append(look)

        result["cells"] = looks
        return result

    def read_table(self, name: Optional[str] = None, cell: Optional[str] = None,
                   doc: Any = None) -> Dict[str, Any]:
        """
        Read a whole table, or one cell of it

        With no `name` the table the caret is in is read, which is what "this
        table" means. Cell text comes from the cell itself, so a cell holding
        several paragraphs keeps its line breaks, and a number reads as it is
        written rather than as a float.
        """
        doc, error = self._writer_document(doc, "Reading a table")
        if error:
            return error

        caret = self._caret_in_table(doc)
        if not name:
            if caret is None:
                listed = [described["name"] for described
                          in (self._describe_table(doc, table)
                              for table in self._tables(doc))]
                return {"success": False,
                        "error": f"The caret is not in a table and none was "
                                 f"named. This document holds: "
                                 f"{', '.join(listed) or 'no tables'}."}
            name = caret["table"]

        table = self._table_by_name(doc, name)
        if table is None:
            listed = [_get_property(other, "Name", "") or "?"
                      for other in self._tables(doc)]
            return {"success": False,
                    "error": f"No table called {name!r} in this document. It "
                             f"holds: {', '.join(listed) or 'no tables'}."}

        described = self._describe_table(doc, table)
        try:
            names = list(table.getCellNames())
        except Exception as e:
            logger.error(f"Could not read the cells of {name}: {e}")
            return {"success": False, "error": str(e)}

        if cell:
            if cell not in names:
                return {"success": False,
                        "error": f"Table {name} has no cell {cell!r}; its "
                                 f"cells are {', '.join(names)}"}
            text = _text_payload(table.getCellByName(cell).getString())
            row, column = _cell_position(cell)
            return {"success": True, "table": described, "cell": cell,
                    "row": row, "column": column, "text": text["text"],
                    "truncated": text.get("truncated", False)}

        # The grid, row by row, as a reader sees it.
        grid = []
        for row_index in range(described["rows"]):
            row = []
            for column_index in range(described["columns"]):
                cell_name = f"{_column_letters(column_index)}{row_index + 1}"
                if cell_name not in names:
                    row.append(None)          # a merged-away cell
                    continue
                try:
                    payload = _text_payload(
                        table.getCellByName(cell_name).getString())
                except Exception as e:
                    logger.info(f"Could not read {cell_name}: {e}")
                    row.append(None)
                    continue
                row.append({"cell": cell_name, "text": payload["text"]})
            grid.append(row)

        return {"success": True, "table": described, "rows": grid,
                "cell_names": names,
                "caret_in_cell": caret["cell"] if caret
                and caret["table"] == described["name"] else None}

    def _cells_asked_for(self, table: Any, cells: Any) -> List[str]:
        """
        Which cells a caller means

        Accepts a list of names, a rectangle "A1:B2", a whole "row:2" or
        "column:B", and None for every cell there is.
        """
        names = list(table.getCellNames())
        if cells is None:
            return names

        def known(name: str) -> str:
            if name not in names:
                raise AddressError(f"this table has no cell {name!r}; its "
                                   f"cells are {', '.join(names)}")
            return name

        if isinstance(cells, str):
            asked = cells.strip()
            lowered = asked.lower()
            if lowered.startswith("row:"):
                row = lowered[4:].strip()
                if not row.isdigit():
                    raise AddressError(f'row must be a number, as in "row:2", '
                                       f'got {cells!r}')
                wanted = [name for name in names
                          if _cell_position(name)[0] == int(row)]
                if not wanted:
                    raise AddressError(f"this table has no row {row}")
                return wanted
            if lowered.startswith("column:"):
                column = asked[7:].strip().upper()
                wanted = [name for name in names
                          if _cell_position(name)[1]
                          == _cell_position(f"{column}1")[1]]
                if not wanted:
                    raise AddressError(f"this table has no column {column!r}")
                return wanted
            if ":" in asked:
                first, last = [part.strip().upper()
                               for part in asked.split(":", 1)]
                top, left = _cell_position(first)
                bottom, right = _cell_position(last)
                if None in (top, left, bottom, right):
                    raise AddressError(f'a range reads like "A1:B2", got '
                                       f'{cells!r}')
                wanted = []
                for row in range(min(top, bottom), max(top, bottom) + 1):
                    for column in range(min(left, right), max(left, right) + 1):
                        name = f"{_column_letters(column - 1)}{row}"
                        if name in names:
                            wanted.append(name)
                if not wanted:
                    raise AddressError(f"no cells in {cells!r}")
                return wanted
            return [known(asked.upper())]

        if isinstance(cells, (list, tuple)):
            return [known(str(name).upper()) for name in cells]
        raise AddressError(f"cells must be a list of names, a range like "
                           f'"A1:B2", "row:2" or "column:B", got {cells!r}')

    def _table_border(self, table: Any, colour: int, width_mm: float,
                      outer: bool, inner: bool, padding_mm: Optional[float]):
        """Put a grid on a table; the Is*Valid flags are what make it stick"""
        line = uno.createUnoStruct("com.sun.star.table.BorderLine2")
        line.Color = colour
        line.LineWidth = max(0, int(round(width_mm * 100)))
        line.LineStyle = 0 if line.LineWidth else 0
        border = table.TableBorder2
        outer_lines = ("TopLine", "BottomLine", "LeftLine", "RightLine")
        inner_lines = ("HorizontalLine", "VerticalLine")
        for field in outer_lines:
            if outer:
                setattr(border, field, line)
        for field in inner_lines:
            if inner:
                setattr(border, field, line)
        for field in ("IsTopLineValid", "IsBottomLineValid", "IsLeftLineValid",
                      "IsRightLineValid", "IsHorizontalLineValid",
                      "IsVerticalLineValid"):
            setattr(border, field, True)
        if padding_mm is not None:
            border.Distance = max(0, int(round(padding_mm * 100)))
            border.IsDistanceValid = True
        table.TableBorder2 = border

    def _set_column_widths(self, table: Any, shares: Any):
        """Move the column separators so the columns take the shares asked for"""
        _rows, columns = _table_size(table)
        if not isinstance(shares, (list, tuple)) or len(shares) != columns:
            raise AddressError(f"column_widths_percent needs one number per "
                               f"column; this table has {columns}")
        try:
            numbers = [float(share) for share in shares]
        except (TypeError, ValueError):
            raise AddressError("column_widths_percent must be numbers")
        if any(number <= 0 for number in numbers):
            raise AddressError("every column needs a share above zero")
        total = sum(numbers)
        if abs(total - 100.0) > 1.0:
            raise AddressError(f"the shares should add up to 100, not "
                               f"{round(total, 1)}")

        scale = _get_property(table, "TableColumnRelativeSum", 0) or 10000
        separators = list(_get_property(table, "TableColumnSeparators", None)
                          or ())
        if len(separators) != columns - 1:
            raise AddressError("this table's columns cannot be moved")
        running = 0.0
        for index, separator in enumerate(separators):
            running += numbers[index]
            separator.Position = int(round(running / total * scale))
        table.TableColumnSeparators = tuple(separators)

    def format_table(self, name: Optional[str] = None, cells: Any = None,
                     border: Optional[bool] = None,
                     border_color: Any = "#808080",
                     border_width: float = 0.35,
                     inner_borders: Optional[bool] = None,
                     padding_mm: Optional[float] = None,
                     background_color: Any = None,
                     header_rows: Optional[int] = None,
                     repeat_heading: Optional[bool] = None,
                     header_background_color: Any = None,
                     header_bold: Optional[bool] = None,
                     paragraph_style: Optional[str] = None,
                     bold: Optional[bool] = None,
                     italic: Optional[bool] = None,
                     font_name: Optional[str] = None,
                     font_size: Optional[float] = None,
                     color: Any = None,
                     column_widths_percent: Any = None,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Give a table a look: its grid, its padding, its header, its cells

        With no `name` the table the caret is in is formatted. `cells` says
        which cells the cell-level settings touch — a list of names, a
        rectangle "A1:B2", "row:2", "column:B" — and means every cell when it
        is left out. The grid, the padding, the header rows and the column
        widths belong to the table itself and are applied whatever `cells`
        says.
        """
        doc, error = self._writer_document(doc, "Formatting a table")
        if error:
            return error

        caret = self._caret_in_table(doc)
        if not name:
            if caret is None:
                listed = [_get_property(table, "Name", "") or "?"
                          for table in self._tables(doc)]
                return {"success": False,
                        "error": f"The caret is not in a table and none was "
                                 f"named. This document holds: "
                                 f"{', '.join(listed) or 'no tables'}."}
            name = caret["table"]

        table = self._table_by_name(doc, name)
        if table is None:
            listed = [_get_property(other, "Name", "") or "?"
                      for other in self._tables(doc)]
            return {"success": False,
                    "error": f"No table called {name!r} in this document. It "
                             f"holds: {', '.join(listed) or 'no tables'}."}

        try:
            wanted_cells = self._cells_asked_for(table, cells)
            grid_colour = _colour(border_color) if border is not None else None
            fill = _colour(background_color) if background_color is not None \
                else None
            header_fill = _colour(header_background_color) \
                if header_background_color is not None else None
            if paragraph_style is not None and not self._has_style(
                    doc, "ParagraphStyles", paragraph_style):
                raise AddressError(
                    f"this document has no paragraph style "
                    f"{paragraph_style!r}; list_styles says which it has")
            if header_rows is not None and (not isinstance(header_rows, int)
                                            or isinstance(header_rows, bool)
                                            or header_rows < 0):
                raise AddressError(f"header_rows must be a whole number from "
                                   f"0, got {header_rows!r}")
        except AddressError as e:
            return {"success": False, "error": str(e)}

        character = {}
        for key, value in (("bold", bold), ("italic", italic),
                           ("font_name", font_name), ("font_size", font_size)):
            if value is not None:
                character[key] = value
        if color is not None:
            try:
                character["color"] = _colour_name(_colour(color))
            except AddressError as e:
                return {"success": False, "error": str(e)}

        asked_for_anything = any(value is not None for value in (
            border, padding_mm, background_color, header_rows, repeat_heading,
            header_background_color, header_bold, paragraph_style,
            column_widths_percent)) or bool(character)
        if not asked_for_anything:
            return {"success": False,
                    "error": "Nothing to change: ask for a border, a "
                             "background, a padding, header rows, a paragraph "
                             "style, character formatting or column widths"}

        def edit():
            changed = []
            if border is not None:
                self._table_border(table, grid_colour or 0,
                                   border_width if border else 0.0,
                                   True,
                                   True if inner_borders is None
                                   else bool(inner_borders),
                                   padding_mm)
                changed.append("border")
            elif padding_mm is not None:
                shape = table.TableBorder2
                shape.Distance = max(0, int(round(padding_mm * 100)))
                shape.IsDistanceValid = True
                table.TableBorder2 = shape
                changed.append("padding")

            if column_widths_percent is not None:
                self._set_column_widths(table, column_widths_percent)
                changed.append("column widths")

            if header_rows is not None:
                table.HeaderRowCount = header_rows
                changed.append("header rows")
            if repeat_heading is not None:
                table.RepeatHeadline = bool(repeat_heading)
                changed.append("repeat heading")

            touched = 0
            in_header_touched = 0
            for cell_name in wanted_cells:
                cell = table.getCellByName(cell_name)
                row = _cell_position(cell_name)[0]
                in_header = header_rows is not None and row is not None \
                    and row <= header_rows
                if fill is not None:
                    cell.BackTransparent = False
                    cell.BackColor = fill
                if header_fill is not None and in_header:
                    cell.BackTransparent = False
                    cell.BackColor = header_fill
                if paragraph_style is not None:
                    paragraphs = cell.createEnumeration()
                    while paragraphs.hasMoreElements():
                        paragraphs.nextElement().ParaStyleName = paragraph_style
                wanted_character = dict(character)
                if header_bold is not None and in_header:
                    wanted_character["bold"] = bool(header_bold)
                if wanted_character:
                    cursor = cell.createTextCursor()
                    cursor.gotoStart(False)
                    cursor.gotoEnd(True)
                    self._apply_character_formatting(cursor, wanted_character)
                # "Touched" means changed, not considered: a header-only
                # setting reported every cell as touched, which read as though
                # the body had been made bold too.
                if fill is not None or paragraph_style is not None \
                        or character:
                    touched += 1
                elif in_header and (header_fill is not None
                                    or header_bold is not None):
                    touched += 1
                if in_header and (header_fill is not None
                                  or header_bold is not None):
                    in_header_touched += 1

            if fill is not None:
                changed.append("background")
            if header_fill is not None:
                changed.append("header background")
            if paragraph_style is not None:
                changed.append(f"paragraph style {paragraph_style}")
            if character or header_bold is not None:
                changed.append("character formatting")

            return {"table": name, "cells_touched": touched,
                    "header_cells_touched": in_header_touched,
                    "cells": wanted_cells if cells is not None else "all",
                    "changed": changed}

        return self._guarded_edit(doc, "MCP: format table", track_changes, edit)
