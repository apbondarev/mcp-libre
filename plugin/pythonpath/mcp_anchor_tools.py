"""The tools that name a place and keep pointing at it while the text moves."""

from typing import Any, Dict, List, Optional


class AnchorTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_anchor(self):
        """The tools of this part, as clients see them."""
        self.tools["anchor_live"] = {
            "description": "Name one or more places in the document so they can still be found after the paragraphs have moved. A paragraph's number changes with every insertion or deletion above it, so a plan made from one search goes stale as it is carried out; an anchor points at the text itself and travels with it. Pass an anchor wherever an address is taken: {\"anchor\": \"a7f3c1\"}. Anchors last as long as the server runs — they are not saved in the document",
            "parameters": {
                "type": "object",
                "properties": {
                    "addresses": {
                        "type": "array",
                        "description": "The places to anchor. Each is an address: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"table\": \"Table1\", \"cell\": \"A2\"} or {\"selection\": true}. Every one is checked before any anchor is made",
                        "items": {"type": "object"}
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["addresses"]
            },
            "handler": self.anchor_live
        }

        self.tools["list_anchors_live"] = {
            "description": "List the anchors held for this document, each with the text it covers now and its address as it stands. An anchor whose text has been replaced or deleted is reported alive: false with the reason, instead of quietly resolving to the empty place it was left in. A session that reads by address holds them in the hundreds, so the listing is paged: at most 200 a call, `held` counting them all and `more` saying there are further ones",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "integer",
                        "description": "Which anchor to begin at, in the order they were made (0-based)",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many anchors to report. 200 when nobody says, and that is a default rather than a limit: ask for more and the rest come with it",
                        "default": 200
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_anchors_live
        }

        self.tools["drop_anchors_live"] = {
            "description": "Let anchors go when the work they were made for is done: the ones named, or every anchor of this document when none are named",
            "parameters": {
                "type": "object",
                "properties": {
                    "anchors": {
                        "type": "array",
                        "description": "The anchors to let go. Omit to drop every anchor of this document",
                        "items": {"type": "string"}
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.drop_anchors_live
        }

    def anchor_live(self, addresses: Any,
                    document: Optional[str] = None) -> Dict[str, Any]:
        """Hold one or more places, and hand back a token for each"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.anchor(addresses, doc=doc)

    def list_anchors_live(self, start: int = 0, count: Optional[int] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Report the anchors held for a document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_anchors(start=start, count=count, doc=doc)

    def drop_anchors_live(self, anchors: Optional[List[str]] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Let anchors go"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.drop_anchors(anchors, doc=doc)
