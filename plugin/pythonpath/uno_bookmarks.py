"""Bookmarks: a name for a place that the document itself keeps.

The other half of the trade an anchor makes. An anchor is a held cursor and
lives as long as the server; a bookmark is saved in the file, comes back when
the document is reopened, and shows in the Navigator — measured, along with
the fact that it moves with its text exactly as a held cursor does.

Measured besides:

  * a bookmark over a range and a bookmark at a caret are both ordinary
    `com.sun.star.text.Bookmark` contents, and their anchors address cleanly;
  * the markers cost nothing that disturbs the offsets around them, unlike a
    comment's;
  * `setName` renames one in place, and `removeTextContent` takes it away
    leaving the text;
  * a **rewrite does not destroy a bookmark** — replacing the very words it
    covered left it in the document, where the same rewrite would have taken
    a comment or a field with it;
  * a name that is already taken is **not** refused by Writer: it silently
    mints "name Copy 1" instead, which is why adding one under a taken name
    is refused here.
"""

from typing import Any, Dict, List, Optional
import logging

from uno_values import AddressError, _text_payload, refusal

logger = logging.getLogger(__name__)


class BookmarksMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _bookmarks(self, doc: Any) -> Any:
        try:
            return doc.getBookmarks()
        except Exception as e:
            logger.error(f"Could not reach the bookmarks: {e}")
            return None

    def _describe_bookmark(self, doc: Any, name: str, mark: Any,
                           address: Any = "unplaced", anchor: Any = None,
                           text: Any = None) -> Dict[str, Any]:
        """A bookmark as a caller sees it: its name, what it covers, where.

        The address is taken from the caller when it has already been worked
        out — placing one anchor walks the body, so a list of them is placed
        in a single sweep instead (see `list_bookmarks`). So are the anchor
        and its text: a listing holds both already, and asking the bookmark
        for them again is two UNO calls per bookmark for nothing.
        """
        described = {"name": name}
        try:
            if anchor is None:
                anchor = mark.getAnchor()
            if address == "unplaced":
                address, _, _ = self._locate_range(doc, anchor)
            payload = _text_payload(anchor.getString() if text is None
                                    else text)
            described["address"] = address
            described["text"] = payload["text"]
            described["truncated"] = payload["truncated"]
        except Exception as e:
            logger.info(f"Could not place bookmark {name}: {e}")
            described["address"] = None
            described["text"] = None
            described["truncated"] = False
        described["is_a_point"] = not described.get("text")
        return described

    def list_bookmarks(self, address: Any = None, number: bool = False,
                       doc: Any = None) -> Dict[str, Any]:
        """
        The bookmarks of a document, each with the text it covers and where

        A bookmark over a range reports that text; one set at a caret reports
        nothing and says so in `is_a_point`. Scoped like the comments.

        Each is placed by an **anchor** it is held with, not by a paragraph
        number: a number is a sweep of the body — two UNO calls for every
        paragraph of the document — and 195 bookmarks of a real guide cost
        20 seconds to number and 0.4 to anchor. `number: true` works them out
        as well, for showing a human where things are.
        """
        doc, error = self._writer_document(doc, "Listing bookmarks")
        if error:
            return error
        marks = self._bookmarks(doc)
        if marks is None:
            return refusal("UNSUPPORTED", "this document keeps no bookmarks")

        try:
            covers, scope = (self._comment_scope(doc, address) if number
                             else self._scope_over(doc, address))
        except Exception as e:
            return refusal("INVALID_ADDRESS", e)

        names, marked, anchors = [], [], []
        for name in marks.getElementNames():
            try:
                mark = marks.getByName(name)
                anchor = mark.getAnchor()
            except Exception as e:
                logger.info(f"Could not read bookmark {name}: {e}")
                continue
            if not number and not covers(anchor):
                continue
            names.append(name)
            marked.append(mark)
            anchors.append(anchor)

        # Numbering them means one sweep of the body; anchoring them is two
        # UNO calls each and says the same thing to every tool.
        placed = self._addresses_in_order(doc, anchors) if number \
            else [None] * len(anchors)

        found = []
        for name, mark, located, anchor in zip(names, marked, placed, anchors):
            # The text is read once and spent twice: on the anchor that is
            # held and on the bookmark that is described.
            try:
                text = anchor.getString()
            except Exception as e:
                logger.info(f"Could not read what bookmark {name} covers: {e}")
                text = None
            held = self._anchor_handle(
                self._hold_anchor(doc, anchor, known=text), "text")
            if number:
                if not covers(located):
                    continue
                located = dict(located or {}, anchor=held)
            else:
                located = {"anchor": held}
            found.append(self._describe_bookmark(doc, name, mark,
                                                 address=located,
                                                 anchor=anchor, text=text))
        # A bookmark in a table cell has no body paragraph, so it sorts
        # after the ones that do, by table and cell — as the comments do.
        def where(one):
            address = one["address"] or {}
            paragraph = address.get("paragraph")
            return (10 ** 9 if paragraph is None else paragraph,
                    address.get("table") or "", address.get("cell") or "",
                    address.get("offset") or 0)

        if number:
            found.sort(key=where)      # in reading order, which numbers give
        return {"success": True, "bookmarks": found, "count": len(found),
                "order": "reading" if number else "as the document names them",
                "scope": scope}

    def add_bookmark(self, address: Any, name: str,
                     track_changes: Optional[bool] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Name a place in the document, so the document remembers it

        A name that is already taken is refused: Writer would take it,
        silently minting "name Copy 1" and leaving a caller pointing at a
        bookmark that is not the one it asked for.
        """
        doc, error = self._writer_document(doc, "Adding a bookmark")
        if error:
            return error
        if not isinstance(name, str) or not name.strip():
            return refusal("INVALID_PARAMETER", "a bookmark needs a name")
        name = name.strip()
        marks = self._bookmarks(doc)
        if marks is not None and marks.hasByName(name):
            return refusal(
                "INVALID_PARAMETER",
                f"this document already has a bookmark called {name!r}; "
                f"Writer would quietly make {name + ' Copy 1'!r} instead")

        try:
            target = self._resolve_address(doc, address)
        except AddressError as e:
            return refusal("INVALID_ADDRESS", e)

        def edit():
            mark = doc.createInstance("com.sun.star.text.Bookmark")
            mark.setName(name)
            owner = target.getText()
            owner.insertTextContent(target, mark, bool(target.getString()))
            return self._describe_bookmark(doc, name, mark)

        return self._guarded_edit(doc, f"MCP: bookmark {name}", track_changes,
                                  edit)

    def rename_bookmark(self, name: str, new_name: str,
                        doc: Any = None) -> Dict[str, Any]:
        """Give a bookmark another name, leaving it where it is"""
        doc, error = self._writer_document(doc, "Renaming a bookmark")
        if error:
            return error
        marks = self._bookmarks(doc)
        if marks is None or not marks.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no bookmark called {name!r}; list_bookmarks says "
                           f"which there are")
        if not isinstance(new_name, str) or not new_name.strip():
            return refusal("INVALID_PARAMETER", "a bookmark needs a name")
        new_name = new_name.strip()
        if marks.hasByName(new_name):
            return refusal("INVALID_PARAMETER",
                           f"this document already has a bookmark called "
                           f"{new_name!r}")

        mark = marks.getByName(name)

        def edit():
            mark.setName(new_name)
            return {"renamed": name, "to": new_name,
                    "address": self._describe_bookmark(doc, new_name,
                                                       mark)["address"]}

        return self._guarded_edit(doc, "MCP: rename a bookmark", None, edit)

    def delete_bookmark(self, name: str, doc: Any = None) -> Dict[str, Any]:
        """Take a bookmark away, leaving the text it was on"""
        doc, error = self._writer_document(doc, "Deleting a bookmark")
        if error:
            return error
        marks = self._bookmarks(doc)
        if marks is None or not marks.hasByName(name):
            return refusal("NOT_FOUND",
                           f"no bookmark called {name!r}; list_bookmarks says "
                           f"which there are")
        mark = marks.getByName(name)
        described = self._describe_bookmark(doc, name, mark)

        def edit():
            anchor = mark.getAnchor()
            anchor.getText().removeTextContent(mark)
            return {"deleted": name, "was_on": described["text"],
                    "address": described["address"]}

        return self._guarded_edit(doc, "MCP: delete a bookmark", None, edit)
