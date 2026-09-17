"""The changes a document is keeping: reading them, and settling them.

`track_changes` on every mutating tool decides whether an edit is *recorded*.
This is the other half — what a reviewer does with the recordings: read them,
accept them, throw them away. Without it the server could fill a document
with redlines and offer no way out of them but the user's own mouse.

Measured on a real Writer, and it shapes everything here:

  * a redline is a property set — `RedlineType` ("Insert", "Delete",
    "Format"), `RedlineAuthor`, `RedlineDateTime`, `RedlineComment`,
    `RedlineDescription` (Writer's own sentence, in the UI's language),
    `RedlineIdentifier`, and `RedlineStart`/`RedlineEnd`, which are ranges;
  * `getString()` on the redline itself **throws**
    (`unoredline.cxx:531`), so the text of a change is read by walking a
    cursor from its start to its end;
  * the author is the office's user name, not anything the document carries:
    `doc.Author` does not exist;
  * there is **no accept or reject on the model** — `acceptAllRedlines` and
    friends are not there. The way through is the dispatcher: select the
    change and send `.uno:AcceptTrackedChange` / `.uno:RejectTrackedChange`,
    or `.uno:AcceptAllTrackedChanges` / `.uno:RejectAllTrackedChanges` for
    the lot. Verified headless: three redlines to none, and the text left as
    the decision says.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import (MAX_TEXT_CHARS, REDLINE_KINDS, _get_property,
                        _stamp_of, _text_payload, refusal)

logger = logging.getLogger(__name__)

ACCEPT_ONE = ".uno:AcceptTrackedChange"
REJECT_ONE = ".uno:RejectTrackedChange"
ACCEPT_ALL = ".uno:AcceptAllTrackedChanges"
REJECT_ALL = ".uno:RejectAllTrackedChanges"


class RedlinesMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _redlines(self, doc: Any) -> List[Any]:
        """Every recorded change in the document, in its own order."""
        try:
            redlines = doc.getRedlines()
            return [redlines.getByIndex(index)
                    for index in range(redlines.getCount())]
        except Exception as e:
            logger.error(f"Could not enumerate the tracked changes: {e}")
            return []

    def _redline_span(self, redline: Any) -> Any:
        """A cursor over what the change covers, or None.

        RedlineStart and RedlineEnd are ranges; the change's own getString()
        throws, so the text is read from a cursor walked between them.
        """
        try:
            start, end = redline.RedlineStart, redline.RedlineEnd
            owner = start.getText()
            span = owner.createTextCursorByRange(start)
            span.gotoRange(end, True)
            return span
        except Exception as e:
            logger.info(f"Could not reach a tracked change: {e}")
            return None

    def _describe_redline(self, doc: Any, redline: Any) -> Dict[str, Any]:
        """A recorded change as a caller sees it."""
        kind = _get_property(redline, "RedlineType", "") or ""
        described = {
            "id": _get_property(redline, "RedlineIdentifier", "") or "",
            "kind": REDLINE_KINDS.get(kind, kind.lower() or "unknown"),
            "author": _get_property(redline, "RedlineAuthor", "") or "",
            "date": _stamp_of(redline, "RedlineDateTime"),
            "comment": _get_property(redline, "RedlineComment", "") or None,
            # Writer's own sentence about the change, in the interface's
            # language — useful to show a human, useless to branch on.
            "description": (_get_property(redline, "RedlineDescription", "")
                            or "")[:MAX_TEXT_CHARS],
        }
        span = self._redline_span(redline)
        if span is None:
            described["text"] = None
            described["truncated"] = False
            described["address"] = None
            return described
        payload = _text_payload(span.getString())
        described["text"] = payload["text"]
        described["truncated"] = payload["truncated"]
        try:
            located, _, _ = self._locate_range(doc, span)
            described["address"] = located
        except Exception as e:
            logger.info(f"Could not place a tracked change: {e}")
            described["address"] = None
        return described

    def list_tracked_changes(self, address: Any = None,
                             author: Optional[str] = None,
                             doc: Any = None) -> Dict[str, Any]:
        """
        The changes recorded in a document, with what each one did and where

        Scoped like the comments — the whole document, a section, a
        paragraph, a range or the selection — and narrowed by author, which
        is how a reviewer takes one person's work at a time. `recording` says
        whether the document is still recording, since a document can carry
        changes with recording since switched off.
        """
        doc, error = self._writer_document(doc, "Listing tracked changes")
        if error:
            return error

        try:
            covers, scope = self._comment_scope(doc, address)
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        changes = []
        for redline in self._redlines(doc):
            described = self._describe_redline(doc, redline)
            if author is not None and described["author"] != author:
                continue
            if not covers(described["address"]):
                continue
            changes.append(described)

        return {"success": True, "changes": changes, "count": len(changes),
                "recording": bool(_get_property(doc, "RecordChanges", False)),
                "authors": sorted({one["author"] for one in changes
                                   if one["author"]}),
                "scope": scope}

    def _settle(self, decision: str, change_id: Optional[str],
                author: Optional[str], address: Any, everything: bool,
                doc: Any) -> Dict[str, Any]:
        """Accept or reject the changes a caller names. One undo step."""
        wanted = [one for one in (change_id, author, address) if one is not None]
        if len(wanted) + (1 if everything else 0) != 1:
            return refusal(
                "INVALID_PARAMETER",
                'say which changes to settle, and only one way: change_id for '
                'one of them, author for everyone\'s by that name, address for '
                'a part of the document, or all=true for the lot')

        doc, error = self._writer_document(
            doc, "Settling tracked changes")
        if error:
            return error

        controller = doc.getCurrentController()
        if controller is None:
            return refusal(
                "UNSUPPORTED",
                "this document has no view, and a change is settled by "
                "selecting it and telling LibreOffice to accept or reject it")

        listed = self.list_tracked_changes(
            address=address, author=author,
            doc=doc) if not everything else {"changes": [
                self._describe_redline(doc, redline)
                for redline in self._redlines(doc)]}
        if not listed.get("changes") and listed.get("success") is False:
            return listed
        matches = [one for one in listed["changes"]
                   if change_id is None or one["id"] == change_id]

        if change_id is not None and not matches:
            return refusal(
                "NOT_FOUND",
                f"no tracked change with id {change_id} in this document; "
                f"take an id from list_tracked_changes")
        if not matches:
            return {"success": True, "settled": 0, "decision": decision,
                    "left": len(self._redlines(doc)),
                    "note": "nothing matched, so nothing was settled"}

        held = None
        try:
            held = controller.getSelection()
        except Exception as e:
            logger.info(f"Could not remember the selection: {e}")

        def edit():
            helper = self.smgr.createInstanceWithContext(
                "com.sun.star.frame.DispatchHelper", self.ctx)
            frame = controller.getFrame()
            whole = everything and len(matches) == len(self._redlines(doc))
            if whole:
                helper.executeDispatch(
                    frame, ACCEPT_ALL if decision == "accept" else REJECT_ALL,
                    "", 0, ())
                return {"settled": len(matches), "one_by_one": False}

            # Last first: settling a change moves the text after it, and the
            # ones still to do are found again by their own identifier.
            settled = 0
            command = ACCEPT_ONE if decision == "accept" else REJECT_ONE
            for one in reversed(matches):
                redline = next((candidate for candidate in self._redlines(doc)
                                if (_get_property(candidate,
                                                  "RedlineIdentifier", "")
                                    or "") == one["id"]), None)
                if redline is None:
                    continue          # gone already, with one settled before it
                span = self._redline_span(redline)
                if span is None:
                    continue
                try:
                    controller.select(span)
                    helper.executeDispatch(frame, command, "", 0, ())
                    settled += 1
                except Exception as e:
                    logger.error(f"Could not settle a tracked change: {e}")
            return {"settled": settled, "one_by_one": True}

        outcome = self._guarded_edit(
            doc, f"MCP: {decision} tracked changes", None, edit)

        if held is not None:
            try:
                controller.select(held)      # leave the reader where they were
            except Exception as e:
                logger.info(f"Could not put the selection back: {e}")

        if outcome.get("success"):
            # _guarded_edit reports whether the edit was recorded, which means
            # nothing here: settling a change is not itself a change Writer
            # records, so saying "tracked: false" would only mislead.
            outcome.pop("tracked", None)
            outcome["decision"] = decision
            outcome["left"] = len(self._redlines(doc))
            outcome["authors"] = sorted({one["author"] for one in matches
                                         if one["author"]})
        return outcome

    def accept_tracked_changes(self, change_id: Optional[str] = None,
                               author: Optional[str] = None,
                               address: Any = None, all: bool = False,
                               doc: Any = None) -> Dict[str, Any]:
        """Take the recorded changes into the text, and stop recording them"""
        return self._settle("accept", change_id, author, address, all, doc)

    def reject_tracked_changes(self, change_id: Optional[str] = None,
                               author: Optional[str] = None,
                               address: Any = None, all: bool = False,
                               doc: Any = None) -> Dict[str, Any]:
        """Throw the recorded changes away, leaving the text as it was"""
        return self._settle("reject", change_id, author, address, all, doc)
