"""Reading a document: its paragraphs, its headings, what it says where.

Every hit and every paragraph carries an address that can be handed straight
back to a tool that edits it.
"""

from typing import Any, Dict, List, Optional
import logging
from uno_values import (DEFAULT_PARAGRAPH_COUNT, DEFAULT_SEARCH_RESULTS, 
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

    def read_paragraphs(self, start: int = 0,
                        count: int = DEFAULT_PARAGRAPH_COUNT,
                        anchors: bool = False,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        count is capped at MAX_PARAGRAPH_COUNT. total_paragraphs always
        reflects the whole document, so the caller can page through it.

        `anchors` hands each paragraph a token that keeps pointing at it after
        the indices have moved, which is what a plan of several edits needs.
        """
        try:
            doc, error = self._writer_document(doc, "Reading paragraphs")
            if error:
                return error

            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False, "code": "INVALID_PARAMETER",
                        "error": f"start must be a non-negative integer, got {start!r}"}

            window = max(1, min(int(count), MAX_PARAGRAPH_COUNT))
            paragraphs = []
            total = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                if start <= total < start + window:
                    entry = _text_payload(element.getString())
                    entry["paragraph"] = total
                    entry["style"] = _get_property(element, "ParaStyleName")
                    if anchors:
                        entry["anchor"] = self._hold_anchor(doc, element)
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

    def get_outline(self, doc: Any = None) -> Dict[str, Any]:
        """
        List the document's headings with the paragraph index of each

        Gives an assistant a map of a long document without reading it, and
        every entry doubles as an address to read or edit from.
        """
        try:
            doc, error = self._writer_document(doc, "An outline")
            if error:
                return error

            headings = []
            total = 0
            dropped = 0

            enumeration = doc.getText().createEnumeration()
            while enumeration.hasMoreElements():
                element = enumeration.nextElement()
                if not hasattr(element, "getStart"):
                    continue
                level = _heading_level(element)
                if level > 0:
                    if len(headings) < MAX_OUTLINE_ENTRIES:
                        headings.append({
                            "paragraph": total,
                            "level": level,
                            "text": element.getString()[:MAX_TEXT_CHARS]
                        })
                    else:
                        dropped += 1
                total += 1

            if dropped:
                logger.info(f"Outline truncated, {dropped} headings dropped")

            return {
                "success": True,
                "headings": headings,
                "total_paragraphs": total,
                "truncated": dropped > 0
            }

        except Exception as e:
            logger.error(f"Failed to get outline: {e}")
            return refusal("FAILED", e)

    def _match_in(self, body: Any, paragraph: Any, index: int, match: Any,
                  start: Any) -> Dict[str, Any]:
        """One match, addressed inside the paragraph that holds it"""
        cursor = body.createTextCursorByRange(paragraph.getStart())
        cursor.gotoRange(start, True)
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
                  anchors: bool = False,
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

        `anchors` adds a token to every hit that goes on pointing at the match
        while the document changes around it: a plan made from one search
        survives its own edits, where the paragraph numbers in the addresses
        do not.
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
                    hit["anchor"] = self._hold_anchor(doc, match)

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
