"""Anchors: a name for a place that keeps pointing at it while the text moves.

An address counts paragraphs, and every insertion or deletion above a
paragraph changes its number — so a plan made from one search was only good
until its first edit, and the way round it was to work through a document
backwards. A UNO text cursor does not have that problem: it is kept by the
document and moves with the text it covers. Measured, on a real Writer:

  * text inserted or a whole paragraph removed **above** a held cursor leaves
    the text it covers exactly as it was;
  * text inserted **inside** it is taken in — a cursor over a paragraph still
    covers that paragraph afterwards;
  * rewriting the stretch it covers **collapses** it to an empty position: it
    does not throw, and it is then indistinguishable from a caret by asking,
    which is why what it covered when it was made is remembered here;
  * removing the paragraph under it leaves it likewise empty but usable;
  * the cursor of a cell whose table is removed, and any cursor into a closed
    document, **throws** "SwXTextCursor: disposed or invalid".

So an anchor is a held cursor plus what it held, and resolving one refuses
rather than guesses when the ground has moved. Anchors live as long as the
server process: they are a way through one piece of work, not a mark saved in
the document — that is what a bookmark is for.
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional
import logging
import secrets

from uno_values import AddressError, _get_property, _text_payload

logger = logging.getLogger(__name__)

# Held cursors are cheap, but they are UNO proxies and they are never asked
# for again once a piece of work is done, so the oldest are let go.
MAX_ANCHORS = 500


class AnchorsMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def _anchor_store(self) -> "OrderedDict":
        """The registry, made on first use.

        Not in __init__: the live harness builds the bridge with __new__ to
        keep it from reaching for a desktop of its own.
        """
        store = getattr(self, "_anchors", None)
        if store is None:
            store = OrderedDict()
            self._anchors = store
        return store

    def _document_key(self, doc: Any) -> str:
        """What tells one open document from another.

        RuntimeUID is the document's own id for as long as it is open ('1',
        '2', …) and is the only thing here that works for two untitled
        documents; the URL is the fallback for anything that has none.
        """
        uid = _get_property(doc, "RuntimeUID", "") or ""
        if uid:
            return f"uid:{uid}"
        try:
            return f"url:{doc.getURL()}"
        except Exception:
            return "url:"

    def _hold_anchor(self, doc: Any, text_range: Any) -> Optional[str]:
        """Keep a cursor over a range and return the token naming it.

        None when the range cannot be held, which is not worth failing a
        search over — the hit still carries its address.
        """
        try:
            owner = text_range.getText()
            cursor = owner.createTextCursorByRange(text_range)
            if cursor.getString() != text_range.getString():
                # The object getSelection() hands back is not always accepted
                # whole; walking from its start to its end is the form that
                # always is — the same trick a comment's anchor needs.
                cursor = owner.createTextCursorByRange(text_range.getStart())
                cursor.gotoRange(text_range.getEnd(), True)
        except Exception as e:
            logger.info(f"Could not hold an anchor: {e}")
            return None

        store = self._anchor_store()
        token = secrets.token_hex(3)
        while token in store:
            token = secrets.token_hex(3)
        try:
            held = cursor.getString()
        except Exception:
            held = ""
        store[token] = {"cursor": cursor,
                        "document": self._document_key(doc),
                        "held": held,
                        "was_empty": not held}
        while len(store) > MAX_ANCHORS:
            dropped, _ = store.popitem(last=False)
            logger.info(f"Anchor {dropped} let go, {MAX_ANCHORS} is the limit")
        return token

    def _anchor_range(self, doc: Any, token: Any) -> Any:
        """The range an anchor names, or an AddressError saying why not."""
        if not isinstance(token, str) or not token:
            raise AddressError(f"anchor must be a token from a tool that "
                               f"hands them out, got {token!r}")
        entry = self._anchor_store().get(token)
        if entry is None:
            raise AddressError(
                f"no anchor {token!r} — anchors last as long as the server "
                f"runs, and are made by anchor, or by find_text and "
                f"read_paragraphs with anchors: true")
        if entry["document"] != self._document_key(doc):
            raise AddressError(
                f"anchor {token!r} was made in another document; it cannot be "
                f"used against this one")

        cursor = entry["cursor"]
        try:
            text = cursor.getString()
        except Exception as e:
            raise AddressError(
                f"anchor {token!r} is gone: it held {entry['held']!r}, and "
                f"what it pointed into has been removed from the document "
                f"({e})")
        if text == "" and not entry["was_empty"]:
            raise AddressError(
                f"anchor {token!r} no longer covers anything: it held "
                f"{entry['held']!r}, which has since been replaced or "
                f"deleted. The place it was is still there, so read that part "
                f"of the document again and anchor what is there now")
        return cursor

    def _anchor_report(self, doc: Any, token: str,
                       entry: Dict[str, Any]) -> Dict[str, Any]:
        """One anchor, as a caller sees it: where it points and whether it does."""
        report: Dict[str, Any] = {"anchor": token,
                                  "held_when_made": entry["held"]}
        cursor = entry["cursor"]
        try:
            text = cursor.getString()
        except Exception as e:
            report["alive"] = False
            report["why"] = f"what it pointed into is gone ({e})"
            return report
        if text == "" and not entry["was_empty"]:
            report["alive"] = False
            report["why"] = ("the text it held has been replaced or deleted")
            report["address"] = None
            return report
        payload = _text_payload(text)
        report["alive"] = True
        report["text"] = payload["text"]
        report["truncated"] = payload["truncated"]
        try:
            address, _, _ = self._locate_range(doc, cursor)
            report["address"] = address
        except Exception as e:
            logger.info(f"Could not address anchor {token}: {e}")
            report["address"] = None
        return report

    # -- the tools ---------------------------------------------------------

    def anchor(self, addresses: Any, doc: Any = None) -> Dict[str, Any]:
        """
        Name places so they can be edited after the paragraphs have moved

        Takes one address or a list of them, and hands back a token for each.
        A token stands for the text itself, not for its number: it survives
        every insertion and deletion elsewhere in the document, so a plan made
        from one search stays good while it is carried out.
        """
        doc, error = self._writer_document(doc, "Anchoring")
        if error:
            return error

        asked = addresses if isinstance(addresses, list) else [addresses]
        if not asked:
            return {"success": False,
                    "error": "addresses must hold at least one address"}

        # Every address is checked before any anchor is made, so a list with
        # one bad entry does not leave half of itself in the registry.
        ranges = []
        for position, address in enumerate(asked):
            try:
                ranges.append(self._resolve_address(doc, address))
            except AddressError as e:
                return {"success": False,
                        "error": f"address {position}: {e}"}

        made = []
        for address, text_range in zip(asked, ranges):
            token = self._hold_anchor(doc, text_range)
            if token is None:
                return {"success": False,
                        "error": f"could not hold an anchor on {address!r}"}
            payload = _text_payload(text_range.getString())
            located, _, _ = self._locate_range(doc, text_range)
            made.append({"anchor": token, "text": payload["text"],
                         "truncated": payload["truncated"],
                         "address": located})

        logger.info(f"Anchored {len(made)} place(s)")
        return {"success": True, "anchors": made, "held": len(made)}

    def list_anchors(self, doc: Any = None) -> Dict[str, Any]:
        """
        The anchors held for this document, and where each points now

        An anchor whose text has been replaced or whose document part is gone
        is reported with alive: false and the reason, rather than quietly
        resolving to the empty place it was left in.
        """
        doc, error = self._writer_document(doc, "Listing anchors")
        if error:
            return error

        key = self._document_key(doc)
        anchors = [self._anchor_report(doc, token, entry)
                   for token, entry in self._anchor_store().items()
                   if entry["document"] == key]
        return {"success": True, "anchors": anchors,
                "held": len(anchors),
                "alive": sum(1 for one in anchors if one["alive"]),
                "held_in_all_documents": len(self._anchor_store()),
                "limit": MAX_ANCHORS}

    def drop_anchors(self, anchors: Optional[List[str]] = None,
                     doc: Any = None) -> Dict[str, Any]:
        """
        Let anchors go: the ones named, or every anchor of this document
        """
        store = self._anchor_store()
        if anchors is None:
            doc, error = self._writer_document(doc, "Dropping anchors")
            if error:
                return error
            key = self._document_key(doc)
            dropped = [token for token, entry in list(store.items())
                       if entry["document"] == key]
        else:
            if isinstance(anchors, str):
                anchors = [anchors]
            if not isinstance(anchors, list):
                return {"success": False,
                        "error": f"anchors must be a list of tokens, got "
                                 f"{type(anchors).__name__}"}
            unknown = [token for token in anchors if token not in store]
            if unknown:
                return {"success": False,
                        "error": f"no such anchor: {', '.join(map(str, unknown))}"}
            dropped = list(anchors)

        for token in dropped:
            store.pop(token, None)
        return {"success": True, "dropped": dropped, "count": len(dropped),
                "still_held": len(store)}

    def _drop_document_anchors(self, doc: Any) -> int:
        """Let go of a closed document's anchors — their cursors are dead."""
        try:
            key = self._document_key(doc)
        except Exception:
            return 0
        store = self._anchor_store()
        gone = [token for token, entry in list(store.items())
                if entry["document"] == key]
        for token in gone:
            store.pop(token, None)
        return len(gone)
