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
from uno_values import (AddressError, TABLE_SERVICE, _cell_position, 
    _colour, _colour_name, _column_letters, _column_shares, _get_property, 
    _supports, _table_size, _text_payload)

logger = logging.getLogger(__name__)


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

    def _range_spans(self, doc: Any, span: Any) -> Dict[str, Any]:
        """
        What a range actually covers: which body paragraphs, which tables

        A selection can run from text through a table and out the other side,
        and its string gives no hint of that — the cells arrive folded in
        with newlines. Walking the body and comparing regions says what is
        really in there.
        """
        found = {"paragraphs": [], "tables": []}
        try:
            body = doc.getText()
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
                        found["tables"].append(self._describe_table(doc,
                                                                    element))
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

    def _describe_table(self, doc: Any, table: Any) -> Dict[str, Any]:
        """A table as a caller sees it, without its contents"""
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
                "after_paragraph": self._paragraphs_before_table(doc, table)}

    def _note_what_is_out_of_reach(self, doc: Any, span: Any,
                                   result: Dict[str, Any]) -> Dict[str, Any]:
        """Say when a range reaches past the paragraph its runs come from"""
        spans = self._range_spans(doc, span)
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

    def list_tables(self, doc: Any = None) -> Dict[str, Any]:
        """
        Every table in the document: its name, its size and where it sits

        `after_paragraph` says how many body paragraphs come before it, since
        addresses count paragraphs and skip tables.
        """
        doc, error = self._writer_document(doc, "Listing tables")
        if error:
            return error

        tables = [self._describe_table(doc, table)
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
                touched += 1

            if fill is not None:
                changed.append("background")
            if header_fill is not None:
                changed.append("header background")
            if paragraph_style is not None:
                changed.append(f"paragraph style {paragraph_style}")
            if character or header_bold is not None:
                changed.append("character formatting")

            return {"table": name, "cells_touched": touched,
                    "cells": wanted_cells if cells is not None else "all",
                    "changed": changed}

        return self._guarded_edit(doc, "MCP: format table", track_changes, edit)
