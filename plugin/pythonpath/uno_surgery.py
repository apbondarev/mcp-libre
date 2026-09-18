"""Paragraph surgery: splitting, joining, moving and copying whole paragraphs.

Moving a paragraph used to mean reading it, deleting it and writing it again
somewhere else, which left its comments, its pictures and its formatting
behind. Writer can do all four properly, and this is what was measured:

  * **splitting** is a paragraph break at an offset, and a comment that spans
    the cut **survives across both halves** — measured, the anchor read
    "Пер\\nвый" afterwards;
  * **joining** is deleting the one character between two paragraphs: a
    cursor at the end of the first, `goRight(1, True)`, and `setString("")`.
    That character is a newline, and nothing else goes with it;
  * **moving** has no model call at all. `.uno:MoveDown` and `.uno:MoveUp`
    through `com.sun.star.frame.DispatchHelper` move whatever the **view
    cursor** covers, and they carry everything: a comment stayed on its
    words, and a bold run stayed bold — measured either way and back again;
  * **copying** goes through the controller's own transferable —
    `getTransferable()` on the selection and `insertTransferable()` where it
    should land — which keeps the comments (one commented paragraph copied
    came back as two comments) and never touches the system clipboard.

Both of the last two work on the reader's own selection, so it is put back
afterwards, the way the review tools put it back.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError, _get_property, _text_payload, refusal

logger = logging.getLogger(__name__)


class SurgeryMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _paragraph_block(self, doc: Any, address: Any) -> tuple:
        """(first, last) body paragraphs an address covers"""
        if isinstance(address, dict) and address.get("through") is not None:
            first = self._paragraph_index_of(
                doc, {"paragraph": address.get("paragraph")})
            last = self._paragraph_index_of(
                doc, {"paragraph": address["through"]})
            return (first, last) if first <= last else (last, first)
        index = self._paragraph_index_of(doc, address)
        return index, index

    def _select_paragraphs(self, doc: Any, first: int, last: int) -> Any:
        """Put the view cursor over a block, and hand back the view"""
        controller = doc.getCurrentController()
        view = controller.getViewCursor()
        body = doc.getText()
        start = self._paragraph_at(body, first)
        end = self._paragraph_at(body, last)
        if start is None or end is None:
            raise AddressError(f"no body paragraphs {first}..{last}")
        view.gotoRange(start.getStart(), False)
        view.gotoRange(end.getEnd(), True)
        return view

    def split_paragraph(self, address: Any,
                        track_changes: Optional[bool] = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Cut a paragraph in two at an address

        What is on the text goes with it: a comment covering the cut is
        reported on both halves afterwards, which is Writer's own doing.
        """
        doc, error = self._writer_document(doc, "Splitting a paragraph")
        if error:
            return error
        try:
            target = self._resolve_address(doc, address)
            located, _, _ = self._locate_range(
                doc, target, self._paragraph_hint(address, doc))
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        if located.get("paragraph") is None:
            return refusal("INVALID_ADDRESS",
                           "that address is outside the body text, and a "
                           "paragraph of a table cell cannot be split this way")

        index = located["paragraph"]
        offset = located["offset"]
        protected = self._refuse_protected(doc, target, False)
        if protected:
            return protected

        def edit():
            from com.sun.star.text.ControlCharacter import PARAGRAPH_BREAK

            body = doc.getText()
            at = self._resolve_address(doc, {"paragraph": index,
                                             "offset": offset, "length": 0})
            body.insertControlCharacter(
                body.createTextCursorByRange(at.getStart()), PARAGRAPH_BREAK,
                False)
            first = self._paragraph_at(body, index)
            second = self._paragraph_at(body, index + 1)
            return {"split": index, "at": offset,
                    "paragraphs": [
                        {"paragraph": index,
                         "text": _text_payload(first.getString())["text"]},
                        {"paragraph": index + 1,
                         "text": _text_payload(second.getString())["text"]}],
                    "total_paragraphs": self._count_body_paragraphs(doc)}

        return self._guarded_edit(doc, "MCP: split a paragraph", track_changes,
                                  edit)

    def merge_paragraphs(self, address: Any,
                         track_changes: Optional[bool] = None,
                         doc: Any = None) -> Dict[str, Any]:
        """
        Join a paragraph with the one after it, or a block into one

        The break between two paragraphs is a single character; taking it
        away is all a join is, and everything on either side stays.
        """
        doc, error = self._writer_document(doc, "Joining paragraphs")
        if error:
            return error
        try:
            first, last = self._paragraph_block(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        if first == last:
            last = first + 1        # "join this one with the next"
        body = doc.getText()
        if self._paragraph_at(body, last) is None:
            return refusal("INVALID_ADDRESS",
                           f"there is no paragraph {last} to join to; a last "
                           f"paragraph has nothing after it")
        try:
            span = self._resolve_address(doc, {"paragraph": first,
                                               "through": last})
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        protected = self._refuse_protected(doc, span, False)
        if protected:
            return protected

        before = self._count_body_paragraphs(doc)

        def edit():
            joined = 0
            # Backwards, so the paragraphs ahead keep their numbers while the
            # breaks are taken out one at a time.
            for index in range(last - 1, first - 1, -1):
                paragraph = self._paragraph_at(body, index)
                cursor = body.createTextCursorByRange(paragraph.getEnd())
                if not cursor.goRight(1, True):
                    continue
                if cursor.getString() not in ("\n", "\r\n"):    # Windows says CRLF
                    # Something other than a paragraph break stands there —
                    # a table, most likely — and deleting it is not a join.
                    continue
                cursor.setString("")
                joined += 1
            whole = self._paragraph_at(body, first)
            return {"merged_into": first, "joins": joined,
                    "text": _text_payload(whole.getString())["text"]
                    if whole else None,
                    "paragraphs_before": before,
                    "total_paragraphs": self._count_body_paragraphs(doc)}

        return self._guarded_edit(doc, "MCP: join paragraphs", track_changes,
                                  edit)

    def move_paragraph(self, address: Any, direction: Optional[str] = None,
                       steps: int = 1, to: Optional[int] = None,
                       track_changes: Optional[bool] = None,
                       doc: Any = None) -> Dict[str, Any]:
        """
        Move a paragraph, or a block of them, up or down

        There is no move in the model: Writer's own `.uno:MoveDown` and
        `.uno:MoveUp` are what carry a paragraph with everything on it — its
        comments, its pictures, its formatting — where reading it and writing
        it again somewhere else leaves all of that behind.
        """
        doc, error = self._writer_document(doc, "Moving a paragraph")
        if error:
            return error
        if (direction is None) == (to is None):
            return refusal("INVALID_PARAMETER",
                           "say where in exactly one way: direction (\"up\" "
                           "or \"down\", with steps) or to, the paragraph to "
                           "put it before")
        if direction is not None and direction not in ("up", "down"):
            return refusal("INVALID_PARAMETER",
                           f"direction is \"up\" or \"down\", got "
                           f"{direction!r}")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            return refusal("INVALID_PARAMETER",
                           f"steps is how many paragraphs to move past, 1 or "
                           f"more, got {steps!r}")

        try:
            first, last = self._paragraph_block(doc, address)
            if to is not None:
                self._paragraph_index_of(doc, {"paragraph": to})
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        if to is not None:
            if first <= to <= last + 1:
                return refusal("INVALID_PARAMETER",
                               f"paragraph {to} is inside the block being "
                               f"moved, or exactly where it already is")
            direction = "up" if to < first else "down"
            steps = (first - to) if to < first else (to - last - 1)
        if steps < 1:
            return refusal("INVALID_PARAMETER",
                           "that would move it nowhere")

        total = self._count_body_paragraphs(doc)
        if direction == "up" and first - steps < 0:
            return refusal("INVALID_PARAMETER",
                           f"paragraph {first} cannot move up {steps}; the "
                           f"document starts at 0")
        if direction == "down" and last + steps > total - 1:
            return refusal("INVALID_PARAMETER",
                           f"paragraph {last} cannot move down {steps}; the "
                           f"document ends at {total - 1}")

        command = ".uno:MoveDown" if direction == "down" else ".uno:MoveUp"
        held = self._selection_ranges(doc)

        def edit():
            view = self._select_paragraphs(doc, first, last)
            helper = self.smgr.createInstanceWithContext(
                "com.sun.star.frame.DispatchHelper", self.ctx)
            frame = doc.getCurrentController().getFrame()
            for _ in range(steps):
                helper.executeDispatch(frame, command, "", 0, ())
            moved_first = first + (steps if direction == "down" else -steps)
            moved_last = moved_first + (last - first)
            self._restore_selection(doc, held)
            body = doc.getText()
            landed = self._paragraph_at(body, moved_first)
            return {"moved": [first, last], "to": [moved_first, moved_last],
                    "direction": direction, "steps": steps,
                    "text": _text_payload(landed.getString())["text"]
                    if landed else None,
                    "total_paragraphs": self._count_body_paragraphs(doc)}

        return self._guarded_edit(doc, "MCP: move a paragraph", track_changes,
                                  edit)

    def _selection_ranges(self, doc: Any) -> Any:
        """Whatever the reader has selected, to put back afterwards"""
        try:
            return doc.getCurrentController().getSelection()
        except Exception as e:
            logger.info(f"Could not read the selection: {e}")
            return None

    def _restore_selection(self, doc: Any, held: Any) -> None:
        if held is None:
            return
        try:
            doc.getCurrentController().select(held)
        except Exception as e:
            logger.info(f"Could not put the selection back: {e}")

    def copy_paragraphs(self, address: Any, to: int,
                        track_changes: Optional[bool] = None,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Copy a paragraph, or a block of them, in front of another

        The copy carries what the original carried: the comments come with
        it, and so does every piece of formatting. It goes through the
        controller's own transferable, so the reader's clipboard is left
        alone.
        """
        doc, error = self._writer_document(doc, "Copying paragraphs")
        if error:
            return error
        try:
            first, last = self._paragraph_block(doc, address)
            self._paragraph_index_of(doc, {"paragraph": to})
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)
        if first <= to <= last:
            return refusal("INVALID_PARAMETER",
                           f"paragraph {to} is inside the block being copied")

        controller = doc.getCurrentController()
        if not hasattr(controller, "getTransferable"):
            return refusal("UNSUPPORTED",
                           "this document's view cannot copy, so there is no "
                           "way to carry a paragraph's comments and pictures "
                           "with it")
        held = self._selection_ranges(doc)
        count = last - first + 1

        def edit():
            self._select_paragraphs(doc, first, last)
            carried = controller.getTransferable()
            body = doc.getText()
            landing = self._paragraph_at(body, to)
            view = controller.getViewCursor()
            view.gotoRange(landing.getStart(), False)
            controller.insertTransferable(carried)
            self._restore_selection(doc, held)
            copied_first = to if to > last else to
            landed = self._paragraph_at(body, copied_first)
            return {"copied": [first, last], "to": to, "paragraphs": count,
                    "text": _text_payload(landed.getString())["text"]
                    if landed else None,
                    "total_paragraphs": self._count_body_paragraphs(doc)}

        return self._guarded_edit(doc, "MCP: copy paragraphs", track_changes,
                                  edit)
