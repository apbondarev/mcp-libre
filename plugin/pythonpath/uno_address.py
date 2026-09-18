"""The address model: how a place in a document is named.

A body paragraph, a block of them, a table cell, the selection, or an anchor
made earlier — and the two directions between an address and a UNO range. An
annotation counts as a position for cursor movement while adding no
characters, so offsets are walked over the text portions rather than counted
with goRight.
The table lookups live here because a cell address needs them.
"""

from typing import Any, Optional, Dict, List
import logging
from uno_values import (AddressError, CELL_SERVICE, _cell_position, 
    _get_property, _supports, _table_size, _text_payload)

logger = logging.getLogger(__name__)


class AddressMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _resolve_address(self, doc: Any, address: Any) -> Any:
        """
        Turn an address into a text range

        Accepts {"paragraph": i, "offset": k, "length": n} against the body
        text, where offset defaults to 0 and an omitted length means the rest
        of the paragraph; {"paragraph": i, "through": j} for whole paragraphs;
        {"table": "Table1", "cell": "A1", …} inside a table; {"selection":
        true} for the current selection; or {"anchor": "a7f3c1"}, which names
        a place held from an earlier call and keeps pointing at it however
        the paragraphs around it move. Raises AddressError for anything it
        cannot resolve.

        A collapsed selection resolves to an empty range rather than an error:
        inserting at a caret is legitimate, so callers needing actual content
        check for themselves.
        """
        if not isinstance(address, dict):
            raise AddressError(
                f"address must be an object, got {type(address).__name__}")

        if "anchor" in address:
            for other in ("table", "cell", "selection"):
                if other in address:
                    raise AddressError(
                        f"an anchor already says where: it takes no "
                        f"{other!r} beside it")
            token = address["anchor"]
            entry = self._anchor_entry(doc, token)
            # The addresses the reading tools hand out carry their anchor
            # beside the numbers they had when handed out, and passing one
            # back is how a caller uses anchors without thinking about them:
            # the anchor decides, the numbers are what it said then. Only a
            # paragraph anchor takes an offset and a length of its own —
            # counted within the paragraph — since a text anchor already
            # covers exactly its stretch.
            handed_out = "paragraph" in address or "through" in address
            counts = "offset" in address or "length" in address
            if counts and not handed_out:
                if entry.get("kind") != "paragraph":
                    raise AddressError(
                        "a text anchor already covers exactly its stretch: it "
                        "takes no 'offset' or 'length' beside it")
                within = {"paragraph": self.paragraph_now(doc, token)}
                for key in ("offset", "length"):
                    if key in address:
                        within[key] = address[key]
                return self._resolve_address(doc, within)
            return self._anchor_range(doc, token)

        if address.get("selection"):
            controller = doc.getCurrentController()
            if not controller:
                raise AddressError("document has no view, so it has no selection")
            try:
                selection = controller.getSelection()
                count = selection.getCount()
            except Exception as e:
                pictures = [_get_property(image, "Name", "") or "?"
                            for image in self._selected_graphics(doc)]
                if pictures:
                    raise AddressError(
                        f"a picture is selected, not text "
                        f"({', '.join(pictures)}), so there is no text range "
                        f"here — list_images and export_image work on the "
                        f"selected picture")
                raise AddressError(f"the selection is not a text range: {e}")
            if count < 1:
                raise AddressError("nothing is selected")
            return selection.getByIndex(0)

        if "cell" in address or "table" in address:
            return self._resolve_cell_address(doc, address)

        if "paragraph" not in address:
            raise AddressError("address needs 'paragraph', 'cell', "
                               "'selection' or 'anchor'")

        if address.get("through") is not None:
            return self._resolve_block(doc, address)

        paragraph = self._paragraph_at(doc.getText(), address["paragraph"])
        if paragraph is None:
            raise AddressError(f"no body paragraph {address['paragraph']}")

        paragraph_length = len(paragraph.getString())
        offset = address.get("offset", 0)
        length = address.get("length")

        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 \
                or offset > paragraph_length:
            raise AddressError(
                f"offset {offset!r} is outside paragraph {address['paragraph']}, "
                f"which holds {paragraph_length} characters")

        cursor = self._position_in(paragraph, offset)

        if length is None:
            cursor.gotoEndOfParagraph(True)
        else:
            if not isinstance(length, int) or isinstance(length, bool) or length < 0 \
                    or offset + length > paragraph_length:
                raise AddressError(
                    f"length {length!r} from offset {offset} runs past the end of "
                    f"paragraph {address['paragraph']}")
            if length:
                cursor.gotoRange(self._position_in(paragraph, offset + length),
                                 True)
        return cursor

    def _resolve_block(self, doc: Any, address: Dict[str, Any]) -> Any:
        """
        Turn {"paragraph": N, "through": M} into a range over whole paragraphs

        An address of one paragraph could never cover a block, so replacing
        fifteen paragraphs with a table meant setting a selection by hand in
        UNO — there was no other way to say "these paragraphs". This says it:
        from the start of the first to the end of the last, the paragraphs
        between them included.
        """
        first, last = address["paragraph"], address["through"]
        for label, value in (("paragraph", first), ("through", last)):
            if not isinstance(value, int) or isinstance(value, bool) \
                    or value < 0:
                raise AddressError(f"{label} must be a whole number from 0, "
                                   f"got {value!r}")
        if last < first:
            raise AddressError(f"through ({last}) comes before paragraph "
                               f"({first})")
        if address.get("offset") or address.get("length") is not None:
            raise AddressError("a block of paragraphs takes no offset or "
                               "length — it covers them whole")

        body = doc.getText()
        start = self._paragraph_at(body, first)
        end = self._paragraph_at(body, last)
        if start is None:
            raise AddressError(f"no body paragraph {first}")
        if end is None:
            raise AddressError(f"no body paragraph {last}")

        cursor = body.createTextCursorByRange(start.getStart())
        cursor.gotoRange(end.getEnd(), True)
        return cursor

    def _resolve_cell_address(self, doc: Any, address: Dict[str, Any]) -> Any:
        """
        Turn {"table": "Table1", "cell": "A1", …} into a range

        offset and length count characters of the cell's own text, where the
        break between its paragraphs counts as one — the same arithmetic the
        body text uses, so the two kinds of address behave alike.
        """
        name = address.get("table")
        if not name:
            caret = self._caret_in_table(doc)
            if caret is None:
                raise AddressError("an address with a cell needs the table's "
                                   "name, or the caret in the table")
            name = caret["table"]
        table = self._table_by_name(doc, name)
        if table is None:
            listed = [_get_property(other, "Name", "") or "?"
                      for other in self._tables(doc)]
            raise AddressError(f"no table called {name!r}; this document "
                               f"holds: {', '.join(listed) or 'no tables'}")

        cell_name = address.get("cell")
        if not cell_name:
            raise AddressError('an address with a table needs a cell, as in '
                               '{"table": "Table1", "cell": "A1"}')
        cell_name = str(cell_name).upper()
        names = list(table.getCellNames())
        if cell_name not in names:
            raise AddressError(f"table {name} has no cell {cell_name!r}; its "
                               f"cells are {', '.join(names)}")
        cell = table.getCellByName(cell_name)

        whole = cell.getString()
        offset = address.get("offset", 0)
        length = address.get("length")
        if not isinstance(offset, int) or isinstance(offset, bool) \
                or offset < 0 or offset > len(whole):
            raise AddressError(f"offset {offset!r} is outside cell {cell_name}, "
                               f"which holds {len(whole)} characters")
        if length is None:
            length = len(whole) - offset
        if not isinstance(length, int) or isinstance(length, bool) \
                or length < 0 or offset + length > len(whole):
            raise AddressError(f"length {length!r} from offset {offset} runs "
                               f"past the end of cell {cell_name}")

        cursor = self._position_in_text(cell, offset)
        if length:
            cursor.gotoRange(self._position_in_text(cell, offset + length),
                             True)
        return cursor

    def _cell_of(self, doc: Any, text_range: Any) -> tuple:
        """
        (table, cell) owning a range, or (None, None) when the body owns it

        The range's own text *is* the cell — an SwXCell supporting
        com.sun.star.text.CellProperties, which names itself in CellName — so
        the cell needs no searching. Only its table does, and the one test
        that works is compareRegionStarts: it answers 0 for the cell itself
        and **throws** for a cell of another table. createTextCursorByRange
        is no test at all, having accepted a range from a different cell of a
        different table — which is how a hit in B2 was once reported as A1.
        """
        try:
            owner = text_range.getText()
        except Exception:
            return None, None
        if not _supports(owner, CELL_SERVICE):
            return None, None

        name = _get_property(owner, "CellName", "") or ""
        for table in self._tables(doc):
            try:
                if name not in table.getCellNames():
                    continue
                candidate = table.getCellByName(name)
                if owner.compareRegionStarts(owner.getStart(),
                                             candidate.getStart()) == 0:
                    return table, owner
            except Exception:
                continue
        return None, owner

    def _paragraph_hint(self, address: Any,
                        doc: Any = None) -> Optional[int]:
        """The paragraph an address names, when it can be had without a walk.

        Resolving {"paragraph": N} walks the body to reach that paragraph,
        and locating the range it produced walked the body again to work out
        the index N it was given. Handing the index forward saves the second
        walk. An anchor names no number, but it remembers the one it was last
        found at and checks it, which comes to the same thing.
        """
        if not isinstance(address, dict):
            return None
        if "anchor" in address:
            return (self._anchor_paragraph(doc, address["anchor"])
                    if doc is not None else None)
        index = address.get("paragraph")
        if isinstance(index, int) and not isinstance(index, bool) \
                and index >= 0 and "cell" not in address \
                and "table" not in address:
            return index
        return None

    def _locate_range(self, doc: Any, text_range: Any,
                      known_paragraph: Optional[int] = None) -> tuple:
        """
        Locate a range within the document

        Returns (address, paragraph_cursor, chars_before_paragraph), where
        address is {"paragraph": index or None, "offset": int, "length": int},
        paragraph_cursor spans the paragraph holding the range start, and
        chars_before_paragraph is None whenever the index is None.

        The cursors come from the text owning the range, which inside a table
        cell or a frame is not the body text. `known_paragraph` is for a
        caller that resolved the range from an address naming that paragraph:
        the walk that would find the index again is then skipped, and
        chars_before_paragraph comes back None, since nobody who passes the
        hint uses it.
        """
        owner = text_range.getText()
        start = text_range.getStart()

        offset_cursor = owner.createTextCursorByRange(start)
        offset_cursor.gotoStartOfParagraph(True)
        offset = len(offset_cursor.getString())

        paragraph_cursor = owner.createTextCursorByRange(start)
        paragraph_cursor.gotoStartOfParagraph(False)
        paragraph_cursor.gotoEndOfParagraph(True)

        # A range in a cell is nowhere in the body, and looking for it there
        # walks the whole document to find nothing — measured at half a
        # second on five hundred paragraphs, paid for every hit in a table.
        in_a_cell = _supports(owner, CELL_SERVICE)
        if in_a_cell:
            index, chars_before = None, None
        elif known_paragraph is not None:
            index, chars_before = known_paragraph, None
        else:
            index, chars_before = self._locate_paragraph(
                doc.getText(), paragraph_cursor.getStart())

        address = {
            "paragraph": index,
            "offset": offset,
            "length": len(text_range.getString())
        }

        if index is None:
            # Not in the body text: a table cell is the usual reason, and a
            # cell has an address of its own, so a hit found there can be
            # acted on instead of coming back as a paragraph of None.
            table, cell = self._cell_of(doc, text_range)
            if cell is not None:
                address["table"] = _get_property(table, "Name", "") or None
                address["cell"] = _get_property(cell, "CellName", "") or ""
                try:
                    from_start = cell.createTextCursorByRange(cell.getStart())
                    from_start.gotoRange(text_range.getStart(), True)
                    address["offset"] = len(from_start.getString())
                except Exception as e:
                    logger.info(f"Could not measure into a cell: {e}")
        return address, paragraph_cursor, chars_before

    def _paragraph_at(self, text: Any, index: Any) -> Any:
        """
        The index-th body paragraph, or None when there is no such paragraph

        Tables are skipped, so indices match what _locate_paragraph reports.
        """
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise AddressError(
                f"paragraph must be a non-negative integer, got {index!r}")

        position = 0
        enumeration = text.createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            if position == index:
                return element
            position += 1
        return None

    def _paragraphs_of(self, text: Any) -> List[Any]:
        """The paragraphs of any text — a cell's as readily as the body's"""
        found = []
        try:
            enumeration = text.createEnumeration()
        except Exception as e:
            logger.info(f"Could not enumerate a text: {e}")
            return found
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if hasattr(element, "getStart"):
                found.append(element)
        return found

    def _position_in_text(self, text: Any, offset: int) -> Any:
        """
        A collapsed cursor at a character offset in a whole text

        A cell holds paragraphs like the body does, and its string joins them
        with newlines — one character each, as cursor movement counts them —
        so the offset is spent paragraph by paragraph and the rest inside one.
        """
        seen = 0
        paragraphs = self._paragraphs_of(text)
        for position, paragraph in enumerate(paragraphs):
            length = len(paragraph.getString())
            if offset <= seen + length:
                return self._position_in(paragraph, offset - seen)
            seen += length + 1                 # the paragraph break
        if paragraphs:
            last = paragraphs[-1]
            return self._position_in(last, len(last.getString()))
        raise AddressError("that text holds no paragraphs")

    def _position_in(self, paragraph: Any, offset: int) -> Any:
        """
        A collapsed cursor at a character offset inside a paragraph

        Counting the offset with goRight from the paragraph start drifts in a
        paragraph that carries comments: an annotation is anchored
        AS_CHARACTER, so it counts as one position for cursor movement while
        contributing nothing to the string — measured, one position per
        comment before the offset. That is how a comment on the second term
        of a paragraph came to sit over " subscriptio" instead of
        "subscription". Walking the text portions and moving only *inside*
        one keeps positions and characters in step, since portions are split
        at every marker.
        """
        text = paragraph.getText()
        if offset <= 0:
            return text.createTextCursorByRange(paragraph.getStart())

        seen = 0
        try:
            portions = paragraph.createEnumeration()
            while portions.hasMoreElements():
                portion = portions.nextElement()
                kind = _get_property(portion, "TextPortionType", "Text")
                if kind in ("TextField", "Footnote"):
                    # A field is the other way round from a comment: it costs
                    # one position for cursor movement while *carrying* the
                    # characters it shows, so the string is longer than the
                    # walk. Skipping it the way a marker is skipped put every
                    # address after a date seven characters to the right —
                    # measured, on "составлено 9/17/26". A footnote's mark is
                    # the same shape: the superscript number is a character of
                    # the paragraph ("A query1 is the entry point") and costs
                    # one position, so skipping it put every address after a
                    # footnote one character to the right — which is how a
                    # rewrite around a mark left a stray letter behind.
                    shown = portion.getString()
                    if seen + len(shown) > offset:
                        # No cursor can stand inside a field or a mark, so it
                        # stands where that portion begins.
                        return text.createTextCursorByRange(portion.getStart())
                    seen += len(shown)
                    continue
                if kind != "Text":
                    continue
                body = portion.getString()
                if not body:
                    continue
                if seen + len(body) >= offset:
                    cursor = text.createTextCursorByRange(portion.getStart())
                    if offset - seen:
                        cursor.goRight(offset - seen, False)
                    return cursor
                seen += len(body)
        except Exception as e:
            # Better a possibly drifted cursor than no edit at all, but say so.
            logger.info(f"Could not walk the portions of a paragraph: {e}")
            cursor = text.createTextCursorByRange(paragraph.getStart())
            cursor.goRight(offset, False)
            return cursor

        cursor = text.createTextCursorByRange(paragraph.getStart())
        cursor.gotoEndOfParagraph(False)
        return cursor

    def _address_in(self, located: Dict[str, Any], offset: int,
                    length: int) -> Dict[str, Any]:
        """
        An address of the same kind as `located`, for a piece inside it

        A run of a cell has to come back addressed to that cell, or it cannot
        be handed to anything: a body address with paragraph None resolves to
        nothing at all.
        """
        if located.get("cell"):
            address = {"table": located.get("table"), "cell": located["cell"],
                       "offset": offset, "length": length}
            return address
        return {"paragraph": located.get("paragraph"), "offset": offset,
                "length": length}

    def _paragraph_of(self, doc: Any, located: Dict[str, Any],
                      paragraph_cursor: Any = None) -> Any:
        """
        The paragraph an address points into, in the body or in a cell

        A cell's paragraphs are its own, so the body enumeration does not
        hold them; the offset is spent across them the way it is anywhere.
        """
        if located.get("paragraph") is not None:
            return self._paragraph_at(doc.getText(), located["paragraph"])
        if located.get("cell"):
            table = self._table_by_name(doc, located.get("table") or "")
            if table is None:
                return None
            cell = table.getCellByName(located["cell"])
            seen = 0
            for paragraph in self._paragraphs_of(cell):
                length = len(paragraph.getString())
                if located["offset"] <= seen + length:
                    return paragraph
                seen += length + 1
            return None
        return None

    def _locate_paragraph(self, text: Any, paragraph_start: Any) -> tuple:
        """
        Find the caret's paragraph in the body text

        Returns (index, characters before it) or (None, None) when the
        paragraph is not part of the body enumeration. Tables are skipped, so
        their content does not count towards the character total.
        """
        try:
            enumeration = text.createEnumeration()
            index = 0
            chars_before = 0
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                # Only equality matters here, so the sign convention of
                # compareRegionStarts is irrelevant
                if text.compareRegionStarts(element.getStart(), paragraph_start) == 0:
                    return index, chars_before
                chars_before += len(element.getString()) + 1  # + paragraph break
                index += 1
            return None, None
        except Exception as e:
            logger.info(f"No absolute position, caret is outside the body text: {e}")
            return None, None

    def _body_paragraphs(self, doc: Any):
        """Yield (paragraph, index) for the body, skipping tables"""
        position = 0
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements():
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            yield element, position
            position += 1

    def _paragraph_index_of(self, doc: Any, address: Any) -> int:
        """The body paragraph an address points at, for scoping a check"""
        if isinstance(address, dict) and "anchor" not in address \
                and isinstance(address.get("paragraph"), int) \
                and not isinstance(address.get("paragraph"), bool):
            if self._paragraph_at(doc.getText(), address["paragraph"]) is None:
                raise AddressError(f"no body paragraph {address['paragraph']}")
            return address["paragraph"]

        located, _, _ = self._locate_range(
            doc, self._resolve_address(doc, address),
            self._paragraph_hint(address, doc))
        if located["paragraph"] is None:
            raise AddressError("that address is outside the body text, so its "
                               "paragraph cannot be spell checked")
        return located["paragraph"]

    def _count_body_paragraphs(self, doc: Any) -> Optional[int]:
        """How many paragraphs the body holds, None if it cannot be walked"""
        try:
            total = 0
            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                if hasattr(enumeration.nextElement(), "getStart"):
                    total += 1
            return total
        except Exception as e:
            logger.info(f"Could not count paragraphs: {e}")
            return None

    def _tables(self, doc: Any) -> List[Any]:
        """Every table in the document, in the order it names them"""
        try:
            tables = doc.getTextTables()
            return [tables.getByName(name)
                    for name in tables.getElementNames()]
        except Exception as e:
            logger.error(f"Could not enumerate the tables: {e}")
            return []

    def _table_by_name(self, doc: Any, name: str) -> Any:
        try:
            tables = doc.getTextTables()
            if tables.hasByName(name):
                return tables.getByName(name)
        except Exception as e:
            logger.info(f"Could not reach table {name}: {e}")
        return None

    def _caret_in_table(self, doc: Any) -> Optional[Dict[str, Any]]:
        """Which table and cell the caret is in, or None when it is not"""
        controller = doc.getCurrentController()
        if not controller:
            return None
        try:
            view = controller.getViewCursor()
            table = _get_property(view, "TextTable", None)
            if table is None:
                return None
            cell = _get_property(view, "Cell", None)
            cell_name = _get_property(cell, "CellName", "") or ""
        except Exception as e:
            logger.info(f"Could not tell whether the caret is in a table: {e}")
            return None

        described = {"table": _get_property(table, "Name", "") or "",
                     "cell": cell_name,
                     "rows": _table_size(table)[0],
                     "columns": _table_size(table)[1]}
        row, column = _cell_position(cell_name)
        described["row"] = row
        described["column"] = column
        if cell is not None:
            try:
                described["cell_text"] = _text_payload(cell.getString())["text"]
            except Exception as e:
                logger.info(f"Could not read the cell: {e}")
                described["cell_text"] = None
        return described
