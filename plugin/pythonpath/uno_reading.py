"""Reading a document: its paragraphs, its headings, what it says where.

Every hit and every paragraph carries an address that can be handed straight
back to a tool that edits it.
"""

from typing import Any, Dict
import logging
from uno_values import (DEFAULT_PARAGRAPH_COUNT, DEFAULT_SEARCH_RESULTS, 
    MAX_OUTLINE_ENTRIES, MAX_PARAGRAPH_COUNT, MAX_SEARCH_RESULTS, 
    MAX_TEXT_CHARS, WRITER_SERVICE, _get_property, _heading_level, 
    _supports, _text_payload)

logger = logging.getLogger(__name__)


class ReadingMixin:
    """Part of UNOBridge — see uno_bridge.py for how the parts meet."""

    def get_text_content(self, doc: Any = None) -> Dict[str, Any]:
        """Get text content from a document"""
        try:
            if doc is None:
                doc = self.get_active_document()
            
            if not doc:
                return {"success": False, "error": "No document available"}
            
            if _supports(doc, WRITER_SERVICE):
                text = doc.getText().getString()
                return {"success": True, "content": text, "length": len(text)}
            else:
                return {"success": False, "error": f"Text extraction not supported for {self._get_document_type(doc)}"}
                
        except Exception as e:
            logger.error(f"Failed to get text content: {e}")
            return {"success": False, "error": str(e)}

    def read_paragraphs(self, start: int = 0,
                        count: int = DEFAULT_PARAGRAPH_COUNT,
                        doc: Any = None) -> Dict[str, Any]:
        """
        Read a window of body paragraphs with their indices and styles

        count is capped at MAX_PARAGRAPH_COUNT. total_paragraphs always
        reflects the whole document, so the caller can page through it.
        """
        try:
            doc, error = self._writer_document(doc, "Reading paragraphs")
            if error:
                return error

            if not isinstance(start, int) or isinstance(start, bool) or start < 0:
                return {"success": False,
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
            return {"success": False, "error": str(e)}

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
            return {"success": False, "error": str(e)}

    def find_text(self, query: str, regex: bool = False,
                  case_sensitive: bool = False,
                  max_results: int = DEFAULT_SEARCH_RESULTS,
                  doc: Any = None) -> Dict[str, Any]:
        """
        Find text in the active Writer document

        Each hit carries an address that resolves back to the match, so a hit
        can be handed straight to a tool that edits it, plus the containing
        paragraph as context. total_hits is the real number of matches even
        when the list is capped.
        """
        try:
            doc, error = self._writer_document(doc, "Searching")
            if error:
                return error

            if not isinstance(query, str) or not query:
                return {"success": False, "error": "query must be a non-empty string"}

            limit = max(1, min(int(max_results), MAX_SEARCH_RESULTS))

            descriptor = doc.createSearchDescriptor()
            descriptor.SearchString = query
            descriptor.SearchRegularExpression = bool(regex)
            descriptor.SearchCaseSensitive = bool(case_sensitive)

            found = doc.findAll(descriptor)
            total = found.getCount()

            hits = []
            for position in range(min(total, limit)):
                match = found.getByIndex(position)
                address, paragraph_cursor, _ = self._locate_range(doc, match)
                context = _text_payload(paragraph_cursor.getString())
                hits.append({
                    "address": address,
                    "matched": match.getString(),
                    "context": context["text"],
                    "context_truncated": context["truncated"]
                })

            logger.info(f"Found {total} matches for {query!r}, returning {len(hits)}")
            return {
                "success": True,
                "hits": hits,
                "total_hits": total,
                "truncated": total > len(hits)
            }

        except Exception as e:
            logger.error(f"Failed to search: {e}")
            return {"success": False, "error": str(e)}
