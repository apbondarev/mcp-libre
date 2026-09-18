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

A **paragraph anchor** is the other kind, and it names a paragraph rather
than a stretch of text — which is what `read_paragraphs`, `read_runs` and
`batch_live` need, since their unit is the paragraph and its text is what
gets rewritten. Neither a text cursor nor the paragraph object alone does the
job, measured:

  * a text cursor over the paragraph **collapses** when the paragraph's text
    is rewritten through another cursor, so the next step naming it would be
    refused although the paragraph is exactly where it was;
  * the paragraph object (`SwXParagraph`) survives that, and throws once the
    paragraph is merged into the one before it or removed — but a split
    leaves it with the text **after** the cut, so a paragraph break at its
    very end moves it to the new, empty paragraph, and after `insert_caption`
    below a paragraph its object named the caption.

So a paragraph anchor holds both: the object says whether the paragraph is
still there, and a cursor at its **start** says where it is. The start stays
with the original text through a split at the end, at the start and in the
middle alike, and through a rewrite of the whole text it stays inside the
same paragraph.
"""

from collections import OrderedDict
from typing import Any, Dict, List, Optional
import logging
import secrets

from uno_values import (AddressError, CELL_SERVICE, _get_property,
                        _supports, _text_payload)

logger = logging.getLogger(__name__)

# Held cursors are cheap — 300 held left an edit exactly as fast, measured —
# but they are UNO proxies and they are never asked for again once a piece of
# work is done, so the oldest are let go. The reading tools hand out an anchor
# per paragraph by default, so the limit is set for a whole long document.
MAX_ANCHORS = 2000


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
                        "was_empty": not held,
                        # The paragraph it was last seen in: checked before it
                        # is believed, so a wrong one costs nothing but the
                        # walk it was meant to save.
                        "index": None}
        while len(store) > MAX_ANCHORS:
            dropped, _ = store.popitem(last=False)
            logger.info(f"Anchor {dropped} let go, {MAX_ANCHORS} is the limit")
        return token

    def _hold_paragraph_anchor(self, doc: Any, paragraph: Any,
                               index: Optional[int] = None) -> Optional[str]:
        """Hold a paragraph — its object and a cursor at its start.

        The object comes free with any walk of the body; the start cursor is
        the one UNO call this costs.
        """
        try:
            start = paragraph.getText().createTextCursorByRange(
                paragraph.getStart())
            held = paragraph.getString()
        except Exception as e:
            logger.info(f"Could not hold a paragraph: {e}")
            return None
        store = self._anchor_store()
        token = secrets.token_hex(3)
        while token in store:
            token = secrets.token_hex(3)
        store[token] = {"kind": "paragraph", "paragraph": paragraph,
                        "cursor": start,
                        "document": self._document_key(doc),
                        "held": held, "was_empty": not held, "index": index}
        while len(store) > MAX_ANCHORS:
            dropped, _ = store.popitem(last=False)
            logger.info(f"Anchor {dropped} let go, {MAX_ANCHORS} is the limit")
        return token

    def _anchor_entry(self, doc: Any, token: Any) -> Dict[str, Any]:
        """The registry entry for a token, or an AddressError saying why not"""
        if not isinstance(token, str) or not token:
            raise AddressError(f"anchor must be a token from a tool that "
                               f"hands them out, got {token!r}")
        entry = self._anchor_store().get(token)
        if entry is None:
            raise AddressError(
                f"no anchor {token!r} — anchors last as long as the server "
                f"runs and the oldest are let go after {MAX_ANCHORS}; read "
                f"that part of the document again for a fresh address")
        if entry["document"] != self._document_key(doc):
            raise AddressError(
                f"anchor {token!r} was made in another document; it cannot be "
                f"used against this one")
        return entry

    def paragraph_now(self, doc: Any, token: Any) -> int:
        """Where a paragraph anchor's paragraph stands now, or AddressError.

        Gone — merged into the one before, or removed — is a refusal, never a
        guess at the neighbour.
        """
        entry = self._anchor_entry(doc, token)
        if entry.get("kind") != "paragraph":
            index = self._anchor_paragraph(doc, token)
            if index is None:
                raise AddressError(f"anchor {token!r} is in no body paragraph")
            return index
        try:
            entry["paragraph"].getString()
        except Exception:
            raise AddressError(
                f"the paragraph anchor {token!r} named ({entry['held'][:60]!r}) "
                f"has been merged into the one before it, or removed")
        point = entry["cursor"]
        body = doc.getText()
        remembered = entry.get("index")
        try:
            if remembered is not None:
                paragraph = self._paragraph_at(body, remembered)
                if paragraph is not None and self._holds_point(
                        body, paragraph, point):
                    return remembered
            index, _ = self._locate_paragraph(body, point.getStart())
        except Exception as e:
            raise AddressError(f"could not place anchor {token!r}: {e}")
        if index is None:
            raise AddressError(f"anchor {token!r} is in no body paragraph")
        entry["index"] = index
        return index

    def _holds_point(self, body: Any, paragraph: Any, point: Any) -> bool:
        """Whether a position lies inside a paragraph, ends included"""
        return (body.compareRegionStarts(paragraph.getStart(),
                                         point.getStart()) >= 0
                and body.compareRegionEnds(point.getEnd(),
                                           paragraph.getEnd()) >= 0)

    def _anchor_range(self, doc: Any, token: Any) -> Any:
        """The range an anchor names, or an AddressError saying why not."""
        entry = self._anchor_entry(doc, token)
        if entry.get("kind") == "paragraph":
            index = self.paragraph_now(doc, token)
            paragraph = self._paragraph_at(doc.getText(), index)
            if paragraph is None:
                raise AddressError(f"anchor {token!r}: no body paragraph {index}")
            return paragraph.getText().createTextCursorByRange(paragraph)

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

    def _anchor_paragraph(self, doc: Any, token: Any) -> Optional[int]:
        """The body paragraph an anchor points into, remembered and checked.

        Working it out means walking the body comparing regions, which is the
        expensive half of reading anything by anchor. The index an anchor was
        last found at is kept and verified instead — reaching the paragraph
        of that number and asking whether it starts where the anchor's does —
        and only a miss pays for the walk.
        """
        entry = self._anchor_store().get(token)
        if entry is None or entry["document"] != self._document_key(doc):
            return None
        if entry.get("kind") == "paragraph":
            try:
                return self.paragraph_now(doc, token)
            except AddressError as e:
                logger.info(f"Could not place anchor {token}: {e}")
                return None
        cursor = entry["cursor"]
        try:
            body = doc.getText()
            owner = cursor.getText()
            if _supports(owner, CELL_SERVICE):
                return None
            start = cursor.getStart()
            remembered = entry.get("index")
            if remembered is not None:
                paragraph = self._paragraph_at(body, remembered)
                if paragraph is not None and body.compareRegionStarts(
                        paragraph.getStart(), start) == 0:
                    return remembered
            index, _ = self._locate_paragraph(body, start)
            entry["index"] = index
            return index
        except Exception as e:
            logger.info(f"Could not place anchor {token}: {e}")
            return None

    def _anchor_report(self, doc: Any, token: str,
                       entry: Dict[str, Any]) -> Dict[str, Any]:
        """One anchor, as a caller sees it: where it points and whether it does."""
        report: Dict[str, Any] = {"anchor": token,
                                  "held_when_made": entry["held"],
                                  "kind": entry.get("kind", "text")}
        if entry.get("kind") == "paragraph":
            try:
                index = self.paragraph_now(doc, token)
            except AddressError as e:
                report["alive"] = False
                report["why"] = str(e)
                return report
            paragraph = self._paragraph_at(doc.getText(), index)
            payload = _text_payload(paragraph.getString() if paragraph else "")
            report.update({"alive": True, "text": payload["text"],
                           "truncated": payload["truncated"],
                           "address": {"paragraph": index}})
            return report
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
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": "addresses must hold at least one address"}

        # Every address is checked before any anchor is made, so a list with
        # one bad entry does not leave half of itself in the registry.
        ranges = []
        for position, address in enumerate(asked):
            try:
                ranges.append(self._resolve_address(doc, address))
            except AddressError as e:
                return {"success": False, "code": "INVALID_ADDRESS",
                        "error": f"address {position}: {e}"}

        made = []
        for address, text_range in zip(asked, ranges):
            token = self._hold_anchor(doc, text_range)
            if token is None:
                return {"success": False, "code": "FAILED",
                        "error": f"could not hold an anchor on {address!r}"}
            payload = _text_payload(text_range.getString())
            # With the hint this costs the walk the address already paid for;
            # without it, every anchor walked the body again comparing
            # regions — six anchors on a 300-paragraph document took 2.0s.
            located, _, _ = self._locate_range(
                doc, text_range, self._paragraph_hint(address, doc))
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
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"anchors must be a list of tokens, got "
                                 f"{type(anchors).__name__}"}
            unknown = [token for token in anchors if token not in store]
            if unknown:
                return {"success": False, "code": "NOT_FOUND",
                        "error": f"no such anchor: {', '.join(map(str, unknown))}"}
            dropped = list(anchors)

        for token in dropped:
            store.pop(token, None)
        return {"success": True, "dropped": dropped, "count": len(dropped),
                "still_held": len(store)}

    def pin_paragraph_numbers(self, doc: Any, numbers: List[int]) -> Dict[int, str]:
        """{number: paragraph anchor} for the body paragraphs named, one walk.

        What `batch_live` holds before its first step, so that every step
        finds the paragraph the plan meant however the numbers have moved —
        by the batch's own steps or by the reader typing meanwhile, measured
        to happen between the steps of one call. A number past the end of
        the document is left out: a later step may be meant to reach a
        paragraph an earlier one makes.
        """
        wanted = set(numbers)
        pins: Dict[int, str] = {}
        if not wanted:
            return pins
        last = max(wanted)
        for paragraph, index in self._body_paragraphs(doc):
            if index > last:
                break
            if index in wanted:
                token = self._hold_paragraph_anchor(doc, paragraph, index)
                if token is not None:
                    pins[index] = token
        return pins

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
