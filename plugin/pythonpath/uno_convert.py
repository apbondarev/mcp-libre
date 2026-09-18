"""Text to a table, and a table back to text.

Both directions were a gap: the survey had got as far as naming
`XTextConvert.convertToTable` and stopped, because the shape of its
arguments needed a round of its own. Introspection answered it:

    convertToTable(TableRanges:     [][][]XTextRange,
                   CellProperties:  [][]PropertyValues,
                   RowProperties:   []PropertyValues,
                   TableProperties: []PropertyValue) -> XTextTable

— rows, then cells, then the **two ranges** that begin and end each cell, as
text ranges rather than properties. Measured with that shape:

  * **nothing between the first cell's start and the last cell's end is
    dropped.** Ranges laid over the pieces of "Имя\\tВозраст" put the tab at
    the *head of the next cell* ("\\tВозраст"); ranges that end after their
    own separator put it at the *tail of their own* ("Имя\\t"). Taking the
    separators out first and laying the ranges end to end is what gives
    clean cells — measured all three ways, which is why the tool removes
    them rather than hoping;
  * the paragraphs the ranges came from are gone afterwards: the table
    stands in their place, and the text that followed is untouched;
  * the way back is Writer's own `.uno:ConvertTableToText` with the caret in
    the table, and its `Delimiter` must be a **string**: with the tab's
    number (9) the dispatch did nothing at all, silently, while "\\t" did it.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (AddressError, _column_letters, _get_property,
                        _text_payload, refusal)

logger = logging.getLogger(__name__)

SEPARATORS = {"tab": "\t", "semicolon": ";", "comma": ",", "pipe": "|"}
MAX_CONVERT_ROWS = 500


class ConvertMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _separator_of(self, separator: Any) -> str:
        """The character that divides the cells, named or given outright"""
        if separator is None:
            return "\t"
        if not isinstance(separator, str) or not separator:
            raise AddressError(
                f"separator is a character, or one of "
                f"{', '.join(sorted(SEPARATORS))}; got {separator!r}")
        if separator in SEPARATORS:
            return SEPARATORS[separator]
        if len(separator) != 1:
            raise AddressError(
                f"a separator is one character, or a name like \"tab\"; got "
                f"{separator!r}")
        return separator

    def convert_text_to_table(self, address: Any, separator: Any = "tab",
                              track_changes: Optional[bool] = None,
                              doc: Any = None) -> Dict[str, Any]:
        """
        Turn paragraphs into a table, one row each, split at a separator

        The formatting inside the cells survives, which is the whole reason
        to use Writer's own conversion rather than reading the text out and
        building a table from it.
        """
        doc, error = self._writer_document(doc, "Converting text to a table")
        if error:
            return error
        try:
            divider = self._separator_of(separator)
            first, last = self._paragraph_block(doc, address)
        except AddressError as e:
            return refusal("INVALID_PARAMETER" if "separator" in str(e)
                           else "INVALID_ADDRESS", e)
        if last - first + 1 > MAX_CONVERT_ROWS:
            return refusal("INVALID_PARAMETER",
                           f"that is {last - first + 1} rows; this converts at "
                           f"most {MAX_CONVERT_ROWS} at a time")

        body = doc.getText()
        lines = []
        for index in range(first, last + 1):
            paragraph = self._paragraph_at(body, index)
            if paragraph is None:
                return refusal("INVALID_ADDRESS", f"no body paragraph {index}")
            lines.append(paragraph.getString())
        if not lines:
            return refusal("INVALID_ADDRESS", "that address covers no text")

        widths = {len(line.split(divider)) for line in lines}
        if len(widths) > 1:
            return refusal(
                "INVALID_PARAMETER",
                f"the rows do not have the same number of cells "
                f"({', '.join(str(one) for one in sorted(widths))}) — a table "
                f"cannot be ragged. Even the rows out first, or convert them "
                f"in blocks that match",
                cells_per_row=sorted(widths))
        columns = widths.pop()

        try:
            span = self._resolve_address(doc, {"paragraph": first,
                                               "through": last})
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        protected = self._refuse_protected(doc, span, False)
        if protected:
            return protected

        def edit():
            # Measured: convertToTable hands every character between the
            # first cell's start and the last cell's end to some cell, so
            # the separators come out first — right to left, so the offsets
            # ahead of each one still hold.
            for index in range(last, first - 1, -1):
                line = lines[index - first]
                for offset in range(len(line) - 1, -1, -1):
                    if line[offset] != divider:
                        continue
                    cut = self._resolve_address(doc, {"paragraph": index,
                                                      "offset": offset,
                                                      "length": 1})
                    cut.setString("")

            rows = []
            for index in range(first, last + 1):
                pieces = lines[index - first].split(divider)
                offset, cells = 0, []
                for piece in pieces:
                    at = self._resolve_address(doc, {"paragraph": index,
                                                     "offset": offset,
                                                     "length": len(piece)})
                    cells.append((at.getStart(), at.getEnd()))
                    offset += len(piece)
                rows.append(tuple(cells))

            table = body.convertToTable(tuple(rows), (), (), ())
            name = _get_property(table, "Name", None) if table else None
            return {"table": name, "rows": len(rows), "columns": columns,
                    "after_paragraph": self._table_positions(doc).get(name),
                    "first_row": [table.getCellByName(f"{letter}1").getString()
                                  for letter in
                                  self._column_letters_upto(columns)]
                    if table else None}

        return self._guarded_edit(doc, "MCP: text to a table", track_changes,
                                  edit)

    def _column_letters_upto(self, count: int) -> List[str]:
        """["A", "B", …] — _column_letters names one column, not a list"""
        return [_column_letters(index) for index in range(count)]

    def convert_table_to_text(self, table: str, separator: Any = "tab",
                              track_changes: Optional[bool] = None,
                              doc: Any = None) -> Dict[str, Any]:
        """
        Turn a table back into paragraphs, its cells divided by a separator

        Writer's own conversion, which keeps what is inside the cells; the
        table's own look — its borders and backgrounds — goes with the table.
        """
        doc, error = self._writer_document(doc, "Converting a table to text")
        if error:
            return error
        try:
            divider = self._separator_of(separator)
        except AddressError as e:
            return refusal("INVALID_PARAMETER", e)
        wanted = self._table_by_name(doc, table)
        if wanted is None:
            listed = [one.get("name") for one in self._tables(doc)]
            return refusal("NOT_FOUND",
                           f"no table called {table!r} in this document; it "
                           f"holds {', '.join(one for one in listed if one) or 'no tables'}")
        controller = doc.getCurrentController()
        if controller is None or not hasattr(controller, "getViewCursor"):
            return refusal("UNSUPPORTED",
                           "this document has no view, and the conversion "
                           "back is Writer's own command on the caret")

        rows = len(self._rows_of(wanted))
        held = self._selection_ranges(doc)

        def edit():
            view = controller.getViewCursor()
            view.gotoRange(wanted.getCellByName(
                wanted.getCellNames()[0]).getStart(), False)
            helper = self.smgr.createInstanceWithContext(
                "com.sun.star.frame.DispatchHelper", self.ctx)
            # Measured: the delimiter must be a **string**. With the tab's
            # number the dispatch did nothing at all, and said nothing.
            import uno as _uno
            argument = _uno.createUnoStruct("com.sun.star.beans.PropertyValue")
            argument.Name = "Delimiter"
            argument.Value = divider
            helper.executeDispatch(controller.getFrame(),
                                   ".uno:ConvertTableToText", "", 0,
                                   (argument,))
            self._restore_selection(doc, held)
            gone = self._table_by_name(doc, table) is None
            return {"converted": table, "gone": gone, "rows": rows,
                    "separator": divider,
                    "total_paragraphs": self._count_body_paragraphs(doc)}

        return self._guarded_edit(doc, "MCP: table to text", track_changes,
                                  edit)

    def _rows_of(self, table: Any) -> List[Any]:
        try:
            rows = table.getRows()
            return [rows.getByIndex(index) for index in range(rows.getCount())]
        except Exception as e:
            logger.info(f"Could not count a table's rows: {e}")
            return []
