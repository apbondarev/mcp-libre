"""Reading a document: its paragraphs, its headings, what it says where.

Every hit and every paragraph carries an address that can be handed straight
back to a tool that edits it.
"""

from typing import Any, Dict, List, Optional
import logging
from uno_values import (AddressError, DEFAULT_PARAGRAPH_COUNT,
    DEFAULT_SEARCH_RESULTS, 
    MAX_OUTLINE_ENTRIES, MAX_PARAGRAPH_COUNT, MAX_SEARCH_RESULTS, 
    MAX_TEXT_CHARS, WRITER_SERVICE, _get_property, _heading_level, 
    _supports, _text_payload, refusal)

logger = logging.getLogger(__name__)

# How much of a hit's neighbourhood one call will carry.
MAX_NEIGHBOUR_PARAGRAPHS = 50


class ReadingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def get_text_content(self, doc: Any = None) -> Dict[str, Any]:
        """Get text content from a document"""
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "code": "NO_DOCUMENT", "error": "No document available"}
            
            if _supports(doc, WRITER_SERVICE):
                text = doc.getText().getString()
                return {"success": True, "content": text, "length": len(text)}
            else:
                return {"success": False, "code": "WRONG_DOCUMENT_TYPE", "error": f"Text extraction not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to get text content: {e}")
            return refusal("FAILED", e)

    def _paragraphs_from(self, doc: Any, address: Any) -> tuple:
        """(first paragraph, how many it covers) for a read that starts at an
        address rather than at a number.

        The reading tools hand out addresses precisely so a caller need never
        carry a number from one call to the next; the tool that pages through
        a document has to take one back, or the habit breaks at the first
        page. A block says its own length, which is what "read this section"
        means.
        """
        first = self._paragraph_index_of(doc, address)
        through = address.get("through")
        if isinstance(through, int) and not isinstance(through, bool):
            last = self._paragraph_index_of(doc, {"paragraph": through})
            return min(first, last), abs(last - first) + 1
        return first, None

    def read_paragraphs(self, start: Any = 0,
                        count: Optional[int] = None,
                        anchors: bool = True,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        count is capped at MAX_PARAGRAPH_COUNT. total_paragraphs always
        reflects the whole document, so the caller can page through it.

        `start` is a number **or an address**: an anchor, a paragraph, or a
        block — `{"anchor": "a7f3c1"}` and the `address` a previous read
        handed back are both accepted, so a long document can be walked
        without a number ever being carried from one call to the next. A
        block (`through`) also says how many to read when `count` does not.
        The `start` in the result is the number the address came to.

        Every paragraph comes with an `address` holding a paragraph anchor
        beside its number, so passing that address back reaches the same
        paragraph after the numbers have moved — by the caller's own edits or
        the reader's typing. It is on unless `anchors` is false: a plan made
        from numbers alone went wrong on a real document the moment the
        reader pressed Enter.

        A formula is an object, not text, so a paragraph's string passes over
        it ("equals  of the whole"). A paragraph that holds one also carries
        `formulas` — name, StarMath text and offset — and `text_with_formulas`,
        its string with each put back where it stands. `text` itself is left
        as it is: every offset in every address counts in it.
        """
        try:
            doc, error = self._writer_document(doc, "Reading paragraphs")
            if error:
                return error

            asked = count
            if isinstance(start, dict):
                try:
                    start, spans = self._paragraphs_from(doc, start)
                except AddressError as e:
                    return refusal("INVALID_ADDRESS", e)
                if asked is None and spans is not None:
                    asked = spans
            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"start must be a non-negative integer or an "
                                 f"address, got {start!r}"}

            window = max(1, min(int(DEFAULT_PARAGRAPH_COUNT if asked is None
                                    else asked), MAX_PARAGRAPH_COUNT))
            paragraphs = []
            total = 0
            standing: Dict[int, List[Dict[str, Any]]] = {}
            for one in self._formula_places(doc):
                standing.setdefault(one["paragraph"], []).append(one)

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                if start <= total < start + window:
                    raw = element.getString()
                    entry = _text_payload(raw)
                    entry["paragraph"] = total
                    if total in standing:
                        entry["formulas"] = [
                            {"name": one["name"], "formula": one["formula"],
                             "offset": one["offset"]}
                            for one in standing[total]]
                        entry["text_with_formulas"] = _text_payload(
                            self._with_formulas(raw, standing[total]))["text"]
                    entry["style"] = _get_property(element, "ParaStyleName")
                    if anchors:
                        # The paragraph itself, held with a cursor at its
                        # start — see uno_anchors for why both.
                        token = self._hold_paragraph_anchor(doc, element, total)
                        entry["anchor"] = token
                        entry["address"] = {"paragraph": total,
                                            "anchor": token} if token \
                            else {"paragraph": total}
                    else:
                        entry["address"] = {"paragraph": total}
                    paragraphs.append(entry)
                total += 1

            return {
                "success": True,
                "paragraphs": paragraphs,
                "start": start,
                "count": len(paragraphs),
                "total_paragraphs": total
            }

        except Exception as e:
            logger.error(f"Failed to read paragraphs: {e}")
            return refusal("FAILED", e)

    def get_outline(self, start: Any = 0, count: Optional[int] = None,
                    anchors: bool = True, doc: Any = None) -> Dict[str, Any]:
        """
        List the document's headings with the paragraph index of each

        Gives an assistant a map of a long document without reading it, and
        every entry doubles as an address to read or edit from — with an
        anchor beside the number unless `anchors` is false, so the map keeps
        pointing at the right paragraphs after edits have moved them.

        Long documents are paged the way `read_paragraphs` pages: `start` is
        a place in the document — a number or an address — and the headings
        from there on are returned, at most `count` of them. A real guide of
        519 pages has more than the 200 one call carries, and before this the
        rest could not be reached at all: the chapter being looked for simply
        was not in the answer. `more` says another call is worth making, and
        the last heading's own address is what to hand back as `start`.
        """
        try:
            doc, error = self._writer_document(doc, "An outline")
            if error:
                return error

            if isinstance(start, dict):
                try:
                    start, _ = self._paragraphs_from(doc, start)
                except AddressError as e:
                    return refusal("INVALID_ADDRESS", e)
            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"start must be a non-negative integer or an "
                                 f"address, got {start!r}"}
            window = max(1, min(int(MAX_OUTLINE_ENTRIES if count is None
                                    else count), MAX_OUTLINE_ENTRIES))

            headings = []
            total = 0
            before = 0
            after = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                level = _heading_level(element)
                if level > 0:
                    if total < start:
                        before += 1
                    elif len(headings) < window:
                        entry = {
                            "paragraph": total,
                            "level": level,
                            "text": element.getString()[:MAX_TEXT_CHARS]
                        }
                        token = self._hold_paragraph_anchor(
                            doc, element, total) if anchors else None
                        entry["address"] = {"paragraph": total,
                                            "anchor": token} if token \
                            else {"paragraph": total}
                        if token:
                            entry["anchor"] = token
                        headings.append(entry)
                    else:
                        after += 1
                total += 1

            if after:
                logger.info(f"Outline paged, {after} headings after this window")

            return {
                "success": True,
                "headings": headings,
                "start": start,
                "count": len(headings),
                "headings_before": before,
                "total_headings": before + len(headings) + after,
                "total_paragraphs": total,
                "more": after > 0,
                # What this key meant before paging existed: there are
                # headings this answer does not carry.
                "truncated": after > 0
            }

        except Exception as e:
            logger.error(f"Failed to get outline: {e}")
            return refusal("FAILED", e)

    def _match_in(self, body: Any, paragraph: Any, index: int, match: Any,
                  start: Any) -> Dict[str, Any]:
        """One match, addressed inside the paragraph that holds it

        The offset is measured backwards, from the match's start to the start
        of its paragraph, the way _locate_range measures it. Walking forwards
        instead — a cursor at the paragraph start told to reach the match —
        overshoots a field: a date came back at the offset of the field after
        it, and deleting it was refused as running past the paragraph.
        """
        cursor = body.createTextCursorByRange(start)
        cursor.gotoStartOfParagraph(True)
        payload = _text_payload(paragraph.getString())
        return {"address": {"paragraph": index,
                            "offset": len(cursor.getString()),
                            "length": len(match.getString())},
                "matched": match.getString(),
                "context": payload["text"],
                "context_truncated": payload["truncated"]}

    def _locate_matches(self, doc: Any, matches: List[Any]) -> List[Dict[str, Any]]:
        """
        Address every match, in one walk of the body

        Asking each match separately walks the document from the start again:
        measured at 0.85s per hit on a 500-paragraph document over a socket,
        so twenty hits took ten seconds — slow enough to send a caller off to
        write its own script. findAll returns matches in document order, so
        one sweep can hand each of them its paragraph as it passes.
        """
        body = doc.getText()
        located = [None] * len(matches)
        if not matches:
            return located

        # Every call crosses the bridge, so the sweep spends as few as it can:
        # each match's start once, and per paragraph one getStart and one
        # comparison. A match belongs to the last paragraph that began before
        # it, which is known as soon as the next paragraph begins after it.
        try:
            starts = [match.getStart() for match in matches]
        except Exception as e:
            logger.info(f"Could not take the starts of the matches: {e}")
            starts = [None] * len(matches)

        pointer = 0
        index = -1
        previous = None
        previous_index = None

        enumeration = body.createEnumeration()
        while enumeration.hasMoreElements() and pointer < len(matches):
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            index += 1
            start = element.getStart()

            while pointer < len(matches) and previous is not None:
                if starts[pointer] is None:
                    break
                try:
                    if body.compareRegionStarts(starts[pointer], start) != 1:
                        break          # the match is not before this paragraph
                except Exception:
                    # A match in a table cell is not comparable with the body
                    # at all; it is left for the slow path, which knows how to
                    # address a cell. Stalling here would stop the sweep.
                    pointer += 1
                    continue
                try:
                    located[pointer] = self._match_in(body, previous,
                                                      previous_index,
                                                      matches[pointer],
                                                      starts[pointer])
                except Exception as e:
                    # The comparison did not refuse it, but the cursor does:
                    # measured on a hit inside a cell, which compares against
                    # the body without complaint and then will not be reached
                    # from it.
                    logger.info(f"A match was left for the slow path: {e}")
                pointer += 1

            previous, previous_index = element, index

        # The last paragraph has nothing after it to mark its end.
        while pointer < len(matches) and previous is not None:
            if starts[pointer] is None:
                break
            try:
                located[pointer] = self._match_in(body, previous, previous_index,
                                                  matches[pointer],
                                                  starts[pointer])
            except Exception as e:
                logger.info(f"A match was left for the slow path: {e}")
            pointer += 1

        # Whatever the sweep could not place — matches inside table cells —
        # is asked the slow way, since each has an address of its own.
        for position, match in enumerate(matches):
            if located[position] is not None:
                continue
            address, paragraph_cursor, _ = self._locate_range(doc, match)
            context = _text_payload(paragraph_cursor.getString())
            located[position] = {"address": address,
                                 "matched": match.getString(),
                                 "context": context["text"],
                                 "context_truncated": context["truncated"]}
        return located

    def _paragraphs_by_index(self, doc: Any,
                             wanted: Any) -> Dict[int, Dict[str, Any]]:
        """
        The paragraphs at these indices, in one walk of the body

        Asking for them one at a time walks the document from the start each
        time, which for twenty hits with ten neighbours apiece is two hundred
        walks of a five-hundred-paragraph document.
        """
        asked = {index for index in wanted if isinstance(index, int)
                 and index >= 0}
        found = {}
        if not asked:
            return found
        position = 0
        enumeration = doc.getText().createEnumeration()
        while enumeration.hasMoreElements() and len(found) < len(asked):
            element = enumeration.nextElement()
            if not hasattr(element, "getStart"):
                continue
            if position in asked:
                payload = _text_payload(element.getString())
                found[position] = {
                    "paragraph": position,
                    "text": payload["text"],
                    "truncated": payload["truncated"],
                    "style": _get_property(element, "ParaStyleName", "") or ""}
            position += 1
        return found

    def find_text(self, query: str, regex: bool = False,
                  case_sensitive: bool = False,
                  max_results: int = DEFAULT_SEARCH_RESULTS,
                  paragraphs_before: int = 0, paragraphs_after: int = 0,
                  anchors: bool = True,
                  doc: Any = None) -> Dict[str, Any]:
        """
        Find text in the active Writer document

        Each hit carries an address that resolves back to the match, so a hit
        can be handed straight to a tool that edits it, plus the containing
        paragraph as context. total_hits is the real number of matches even
        when the list is capped.

        `paragraphs_before` and `paragraphs_after` bring the neighbourhood of
        each hit along — index, style and text — which is what a caller does
        next anyway: a heading like "Operation" is only interesting together
        with the code block under it, and fetching that separately is a
        second call per hit.

        Every hit's `address` carries an anchor that goes on pointing at the
        match while the document changes around it — a plan made from one
        search survives its own edits and the reader's typing, where the
        paragraph numbers alone do not. It is on unless `anchors` is false.
        """
        try:
            doc, error = self._writer_document(doc, "Searching")
            if error:
                return error

            if not isinstance(query, str) or not query:
                return {"success": False, "code": "INVALID_PARAMETER", "error": "query must be a non-empty string"}

            limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))
            for label, value in (("paragraphs_before", paragraphs_before),
                                 ("paragraphs_after", paragraphs_after)):
                if not isinstance(value, int) or isinstance(value, bool) \
                        or value < 0 or value > MAX_NEIGHBOUR_PARAGRAPHS:
                    return {"success": False, "code": "INVALID_PARAMETER",
                            "error": f"{label} must be between 0 and "
                                     f"{MAX_NEIGHBOUR_PARAGRAPHS}, got "
                                     f"{value!r}"}

            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = query
            descriptor.SearchRegularExpression = bool(regex)
            descriptor.SearchCaseSensitive = bool(case_sensitive)

            found = doc.findAll(descriptor)
            total = found.getCount()

            matches = [found.getByIndex(position)
                       for position in range(min(total, limit))]
            hits = self._locate_matches(doc, matches)

            if anchors:
                for hit, match in zip(hits, matches):
                    token = self._hold_anchor(doc, match)
                    hit["anchor"] = token
                    if token and isinstance(hit.get("address"), dict):
                        # Handed out inside the address, so passing the
                        # address back is all it takes to use it.
                        hit["address"] = dict(hit["address"], anchor=token)

            if paragraphs_before or paragraphs_after:
                wanted = set()
                for hit in hits:
                    index = (hit["address"] or {}).get("paragraph")
                    if index is None:
                        continue          # a hit in a cell has no neighbours
                    wanted.update(range(max(0, index - paragraphs_before),
                                        index + paragraphs_after + 1))
                paragraphs = self._paragraphs_by_index(doc, wanted)
                for hit in hits:
                    index = (hit["address"] or {}).get("paragraph")
                    if index is None:
                        hit["before"] = None
                        hit["after"] = None
                        continue
                    hit["before"] = [paragraphs[number] for number
                                     in range(max(0, index - paragraphs_before),
                                              index)
                                     if number in paragraphs]
                    hit["after"] = [paragraphs[number] for number
                                    in range(index + 1,
                                             index + paragraphs_after + 1)
                                    if number in paragraphs]

            logger.info(f"Found {total} matches for {query!r}, returning {len(hits)}")
            return {
                "success": True,
                "hits": hits,
                "total_hits": total,
                "truncated": total > len(hits)
            }

        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return refusal("FAILED", e)
