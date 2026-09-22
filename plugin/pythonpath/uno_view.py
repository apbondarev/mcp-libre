"""Where the reader is: the caret, the selection, the page.

A caret in a table cell belongs to no body paragraph, and a selected picture
is no text selection at all — both are reported rather than left as silence.
"""

from typing import Any, Optional, Dict
import logging
from uno_values import (_text_payload, AddressError, refusal)

logger = logging.getLogger(__name__)


class ViewMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def select(self, address: Any, doc: Any = None) -> Dict[str, Any]:
        """
        Select the text at an address, as a reader would with the mouse

        Nothing here could set a selection, so anything that works on one —
        and a human watching the screen — was out of reach without driving
        UNO by hand. Selecting changes no text; it moves the view.
        """
        doc, error = self._writer_document(doc, "Selecting text")
        if error:
            return error

        controller = doc.getCurrentController()
        if not controller:
            return {"success": False, "code": "UNSUPPORTED",
                    "error": "The document has no view, so nothing can be "
                             "selected in it"}

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        try:
            controller.select(target)
        except Exception as e:
            logger.error(f"Could not select: {e}")
            return refusal("FAILED", e)

        payload = _text_payload(target.getString())
        spans = self._range_spans(doc, target)
        return {"success": True, "selected": payload["text"],
                "truncated": payload["truncated"],
                "length": len(target.getString()),
                "paragraphs": spans["paragraphs"],
                "tables": [table["name"] for table in spans["tables"]]}

    def get_cursor_info(self, number: bool = False,
                        character_offset: bool = False,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Report where the caret is and what is selected in a Writer document

        Covers the caret's offset inside its paragraph, that paragraph's text,
        the page, and what is selected — all of which the view cursor knows,
        so they cost nothing.

        **A paragraph has no number in UNO.** The only way to one is to count
        the paragraphs before it, and that is a walk of the body: measured at
        0.55 ms a paragraph over a socket, so a caret at paragraph 4072 of a
        real guide cost three seconds and one at 6900 nearly four — for a
        question the caller usually asks only in order to act there. So the
        number is counted only when `number` says to, and what comes back
        instead is `address`, an **anchor** on the caret's paragraph: it costs
        two UNO calls, every tool takes it, and it goes on naming that
        paragraph after the numbers around it have moved. `character_offset`
        adds `document_offset`, which counts the characters as well and pulls
        every paragraph's text over the bridge on the way.
        """
        try:
            doc, error = self._writer_document(doc, "Cursor info")
            if error:
                return error

            controller = doc.getCurrentController()
            pictures = self._selected_graphics(doc)
            if pictures:
                # Selecting a picture leaves no text selection at all, and
                # asking for the view cursor throws; saying what is selected
                # beats reporting nothing.
                return {"success": True, "selection_kind": "picture",
                        "selected_text": None, "paragraph_index": None,
                        "images": [self._describe_image(doc, image)
                                   for image in pictures],
                        "note": "a picture is selected, not text; "
                                "export_image writes it out and list_images "
                                "describes it"}

            view_cursor = controller.getViewCursor() if controller else None
            if not view_cursor:
                return {
                    "success": False,
                    "code": "UNSUPPORTED",
                    "error": "Document has no view cursor (is LibreOffice running headless?)"
                }

            caret = view_cursor.getStart()
            counting = bool(number or character_offset)
            address, paragraph_cursor, chars_before = self._locate_range(
                doc, caret, count_characters=bool(character_offset),
                find_paragraph=counting)
            index = address["paragraph"]
            offset_in_paragraph = address["offset"]

            info = {
                "success": True,
                "cursor": {
                    "paragraph_index": index,
                    "offset_in_paragraph": offset_in_paragraph,
                    "document_offset": None if chars_before is None
                                       else chars_before + offset_in_paragraph,
                    "page": self._get_page(view_cursor)
                },
                "paragraph": _text_payload(paragraph_cursor.getString()),
                "selection": self._get_selection_info(controller)
            }
            # An anchor on the paragraph the caret stands in: two UNO calls,
            # where its number is a walk of the body. It is what the next
            # call should be given.
            standing = self._paragraph_from(paragraph_cursor)
            held = (self._hold_paragraph_anchor(doc, standing, index)
                    if standing is not None else None)
            if held:
                info["address"] = ({"paragraph": index, "anchor": held}
                                   if index is not None else {"anchor": held})
                info["anchor"] = held
            if not counting:
                info["note"] = ("the paragraph's number is not counted unless "
                                "number: true asks for it — UNO gives none, so "
                                "it is a walk of the body; `address` names the "
                                "same paragraph without one")
            # A caret in a table cell belongs to no body paragraph, which is
            # why paragraph_index is None there; saying which table and which
            # cell beats leaving the caller to wonder where it is.
            in_table = self._caret_in_table(doc)
            if in_table is not None:
                info["in_table"] = in_table

            # A selection can hold whole tables, and its text gives no sign of
            # it: the cells arrive folded in with newlines.
            selected = info.get("selection") or {}
            if selected.get("has_selection"):
                try:
                    span = self._resolve_address(doc, {"selection": True})
                    spans = self._range_spans(doc, span)
                except Exception as e:
                    logger.info(f"Could not read what the selection spans: {e}")
                    spans = None
                if spans is not None:
                    selected["paragraphs"] = spans["paragraphs"]
                    selected["tables"] = spans["tables"]
                    selected["contains_table"] = bool(spans["tables"])
            logger.info("Retrieved cursor info")
            return info

        except Exception as e:
            logger.error(f"Failed to get cursor info: {e}")
            return refusal("FAILED", e)

    def _get_page(self, view_cursor: Any) -> Optional[int]:
        """Page the caret is on, None if the view cannot report one"""
        try:
            return view_cursor.getPage()
        except Exception as e:
            logger.info(f"Page number unavailable: {e}")
            return None

    def _get_selection_info(self, controller: Any) -> Dict[str, Any]:
        """
        Read the selection, joining the parts of a multi-range selection

        A table cell selection is not a collection of text ranges, so it is
        reported as no selection rather than failing the whole call.
        """
        try:
            selection = controller.getSelection()
            range_count = selection.getCount()
            parts = [selection.getByIndex(i).getString() for i in range(range_count)]
        except Exception as e:
            logger.info(f"Selection holds no readable text ranges: {e}")
            return {
                "has_selection": False,
                "text": "",
                "length": 0,
                "range_count": 0,
                "truncated": False
            }

        selected = "\n".join(part for part in parts if part)
        info = _text_payload(selected)
        info["has_selection"] = bool(selected)
        info["range_count"] = range_count
        return info

    def _has_selection(self, doc: Any) -> bool:
        """Check if document has selected content"""
        try:
            if hasattr(doc, 'getCurrentController'):
                controller = doc.getCurrentController()
                if hasattr(controller, 'getSelection'):
                    selection = controller.getSelection()
                    return selection.getCount() > 0
        except:
            pass
        return False
