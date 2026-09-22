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

from uno_values import (AddressError, CELL_SERVICE, MAX_ANCHORS,
                        _anchor_token, _get_property,
                        _supports, _text_payload)

logger = logging.getLogger(__name__)

# Held cursors are cheap — 300 held left an edit exactly as fast, measured —
# but they are UNO proxies and they are never asked for again once a piece of
# work is done, so the oldest are let go past MAX_ANCHORS, which lives with
# the other bounds in uno_values.
#
# How many anchors one listing carries when nobody says. A default, not a
# ceiling: reporting a thousand is a payload few want unasked, but a caller
# who asks for the lot gets it — the store itself is the only bound.
DEFAULT_ANCHOR_REPORTS = 200
MAX_ANCHOR_REPORTS = DEFAULT_ANCHOR_REPORTS    # the old name, kept


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

    def _anchor_handle(self, token: Optional[str],
                       kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """An anchor as an answer shows it: its id and what kind it is.

        A caller holding `"anchor": "84b81c"` could not tell a paragraph
        anchor from one over a stretch of text, and the two take different
        things beside them — only a paragraph anchor counts an `offset` and a
        `length` within itself. An address still takes the **id**: this is
        what a result reports, not what a tool accepts.
        """
        if not token:
            return None
        if kind is None:
            entry = self._anchor_store().get(token) or {}
            kind = entry.get("kind", "text")
        return {"anchorId": token, "type": kind}

    def _place_all(self, doc: Any, anchors: List[Any],
                   number: bool) -> List[Dict[str, Any]]:
        """An address for every range: anchored, or numbered as well.

        Numbering is one sweep of the body however many ranges there are —
        two UNO calls for each of a document's paragraphs — and that is what
        made listing the things a long document holds cost tens of seconds.
        An anchor is two calls per range and says the same thing to every
        tool that takes an address.
        """
        placed = (self._addresses_in_order(doc, anchors) if number
                  else [None] * len(anchors))
        addresses = []
        for anchor, located in zip(anchors, placed):
            held = self._anchor_handle(self._hold_anchor(doc, anchor), "text")
            addresses.append(dict(located or {}, anchor=held) if number
                             else {"anchor": held})
        return addresses

    def _anchor_entry(self, doc: Any, token: Any) -> Dict[str, Any]:
        """The registry entry for a token, or an AddressError saying why not"""
        token = _anchor_token(token)
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

    def _body_sweep(self, doc: Any) -> List[Any]:
        """Every body paragraph in order, from one walk.

        A listing places every anchor it reports, and placing one used to
        reach its paragraph by number — a walk of the body apiece. With
        hundreds of anchors held, which is what handing them out by default
        means, that is thousands of walks: `list_anchors` on a 6981-paragraph
        document did not answer in fifteen minutes, and the server takes its
        calls one at a time, so nothing else answered either.
        """
        return [paragraph for paragraph, _ in self._body_paragraphs(doc)]

    def _paragraph_holding(self, body: Any, sweep: List[Any],
                           point: Any) -> Optional[int]:
        """Which swept paragraph a position lies in, by halving the list.

        The paragraphs are in document order and a region comparison says
        which side of one a point falls, so thirteen comparisons answer where
        a walk of seven thousand elements did. A point in a table cell cannot
        be compared with the body at all — that throws — and is left to the
        slow path, which knows about cells.
        """
        low, high = 0, len(sweep) - 1
        while low <= high:
            middle = (low + high) // 2
            paragraph = sweep[middle]
            if body.compareRegionStarts(paragraph.getStart(),
                                        point.getStart()) < 0:
                high = middle - 1          # this paragraph starts after it
            elif body.compareRegionEnds(point.getEnd(),
                                        paragraph.getEnd()) < 0:
                low = middle + 1           # it ends after this paragraph
            else:
                return middle
        return None

    def paragraph_now(self, doc: Any, token: Any,
                      sweep: Optional[List[Any]] = None) -> int:
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
                paragraph = (sweep[remembered]
                             if sweep is not None and remembered < len(sweep)
                             else None if sweep is not None
                             else self._paragraph_at(body, remembered))
                if paragraph is not None and self._holds_point(
                        body, paragraph, point):
                    return remembered
            if sweep is not None:
                index = self._paragraph_holding(body, sweep, point)
            else:
                index, _ = self._locate_paragraph(body, point.getStart())
        except Exception as e:
            raise AddressError(f"could not place anchor {token!r}: {e}")
        if index is None:
            raise AddressError(f"anchor {token!r} is in no body paragraph")
        entry["index"] = index
        return index

    def _live_paragraph(self, doc: Any, token: Any,
                        entry: Dict[str, Any]) -> Any:
        """The paragraph a paragraph anchor names now, or an AddressError.

        The held object says whether the paragraph is still there — it throws
        once its paragraph has been merged away or removed — while the start
        cursor says *which* paragraph is meant, since on a split the object
        goes with the text after the cut. Neither needs the number.
        """
        try:
            entry["paragraph"].getString()
        except Exception:
            raise AddressError(
                f"the paragraph anchor {token!r} named ({entry['held'][:60]!r}) "
                f"has been merged into the one before it, or removed")
        paragraph = self._paragraph_from(entry["cursor"])
        if paragraph is None:
            raise AddressError(
                f"anchor {token!r} no longer stands in a paragraph: it held "
                f"{entry['held'][:60]!r}")
        return paragraph

    def _holds_point(self, body: Any, paragraph: Any, point: Any) -> bool:
        """Whether a position lies inside a paragraph, ends included"""
        return (body.compareRegionStarts(paragraph.getStart(),
                                         point.getStart()) >= 0
                and body.compareRegionEnds(point.getEnd(),
                                           paragraph.getEnd()) >= 0)

    def _anchor_range(self, doc: Any, token: Any) -> Any:
        """The range an anchor names, or an AddressError saying why not.

        A paragraph anchor is answered from the paragraph its start cursor
        stands in — two UNO calls — rather than from its number, which is a
        walk of every paragraph above it. The number is nobody's business
        here: what a caller wants is the range.
        """
        entry = self._anchor_entry(doc, token)
        if entry.get("kind") == "paragraph":
            paragraph = self._live_paragraph(doc, token, entry)
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

    def _anchor_report(self, doc: Any, token: str, entry: Dict[str, Any],
                       sweep: Optional[List[Any]] = None,
                       place: bool = True) -> Dict[str, Any]:
        """One anchor, as a caller sees it: where it points and whether it does.

        `sweep` is the body walked once by the caller — see `_body_sweep`.
        Without it every anchor reported here walked the body itself, twice:
        once to place it and once to read the paragraph it landed in.
        """
        report: Dict[str, Any] = {
            "anchor": self._anchor_handle(token, entry.get("kind", "text")),
            "held_when_made": entry["held"]}
        if entry.get("kind") == "paragraph":
            try:
                paragraph = self._live_paragraph(doc, token, entry)
            except AddressError as e:
                report["alive"] = False
                report["why"] = str(e)
                return report
            payload = _text_payload(paragraph.getString() if paragraph else "")
            report.update({"alive": True, "text": payload["text"],
                           "truncated": payload["truncated"]})
            if place:
                try:
                    report["address"] = {
                        "paragraph": self.paragraph_now(doc, token, sweep)}
                except AddressError as e:
                    # Its paragraph answered a moment ago and will not now:
                    # the anchor is gone, which is what a listing must say
                    # rather than throwing out of a report.
                    report.update({"alive": False, "why": str(e),
                                   "address": None})
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
        if not place:
            return report
        try:
            # The same walk the sweep already paid for: hand _locate_range the
            # paragraph so it does not go looking for it again.
            hint = None
            if sweep:
                try:
                    hint = self._paragraph_holding(doc.getText(), sweep, cursor)
                except Exception:
                    hint = None            # a cell: the slow path knows them
            address, _, _ = self._locate_range(doc, cursor, hint)
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
            made.append({"text": payload["text"],
                         "truncated": payload["truncated"],
                         "address": dict(located,
                                         anchor=self._anchor_handle(token))})

        logger.info(f"Anchored {len(made)} place(s)")
        return {"success": True, "anchors": made, "held": len(made)}

    def list_anchors(self, start: int = 0, count: Optional[int] = None,
                     number: bool = False, doc: Any = None) -> Dict[str, Any]:
        """
        The anchors held for this document, and where each points now

        An anchor whose text has been replaced or whose document part is gone
        is reported with alive: false and the reason, rather than quietly
        resolving to the empty place it was left in.

        A session that reads by address holds anchors in the hundreds, so the
        listing is paged — `start` and `count` over the anchors in the order
        they were made, `more` saying there are further ones — and `alive`
        counts among those reported, where `held` counts them all. The body
        is walked **once** for the whole call.
        """
        doc, error = self._writer_document(doc, "Listing anchors")
        if error:
            return error
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            return {"success": False, "code": "INVALID_PARAMETER",
                    "error": f"start must be a non-negative integer, "
                             f"got {start!r}"}
        window = max(1, int(DEFAULT_ANCHOR_REPORTS if count is None
                            else count))

        key = self._document_key(doc)
        held = [(token, entry) for token, entry in self._anchor_store().items()
                if entry["document"] == key]
        showing = held[start:start + window]
        # Saying *where* each anchor points is a walk of the body, even done
        # once for the whole call; whether it is alive, and what it holds,
        # costs nothing. So the places are worked out when asked for.
        sweep = self._body_sweep(doc) if showing and number else []
        anchors = [self._anchor_report(doc, token, entry, sweep,
                                       place=number)
                   for token, entry in showing]
        return {"success": True, "anchors": anchors,
                "start": start,
                "count": len(anchors),
                "held": len(held),
                "alive": sum(1 for one in anchors if one["alive"]),
                "more": start + len(anchors) < len(held),
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
