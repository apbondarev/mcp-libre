"""The tools for hyperlinks: what a document points at."""

from typing import Any, Dict, Optional

SCOPE = ("Which part of the document: omit for all of it, {\"heading\": N} "
         "for a section, {\"paragraph\": N}, {\"paragraph\": N, \"through\": "
         "M}, a range, or {\"selection\": true}")


class LinkTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_link(self):
        """The tools of this part, as clients see them."""
        self.tools["list_hyperlinks_live"] = {
            "description": "List the hyperlinks of a Writer document: the words each one is on, where it points, and its address. A link into the same document says whether its target is still there; one pointing outside cannot be checked from here — nothing in this server reaches the network — so its `broken` is left unknown rather than guessed. Setting a link is format_range_live's `link`",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": SCOPE,
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "number": {
                        "type": "boolean",
                        "description": "Say which paragraph each link sits in, by number. That means walking the body from its beginning to the scope — 4.5s for a selection two thirds of the way through a real guide — where the anchor each link carries names the same words",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_hyperlinks_live
        }

        self.tools["remove_hyperlink_live"] = {
            "description": "Take hyperlinks away and leave the words they were on. Clearing the address alone would leave the blue underline behind — it comes from two character styles rather than from a colour — so those go too. Say which links in exactly one way: address, url, or all",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "The links in this part of the document: " + SCOPE,
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "url": {
                        "type": "string",
                        "description": "Every link pointing at this address, wherever it is in the document"
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Every hyperlink in the document",
                        "default": False
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.remove_hyperlink_live
        }

    def list_hyperlinks_live(self, address: Any = None, number: bool = False,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """List the hyperlinks of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_hyperlinks(address=address, number=number,
                                               doc=doc)

    def remove_hyperlink_live(self, address: Any = None,
                              url: Optional[str] = None, all: bool = False,
                              track_changes: Optional[bool] = None,
                              document: Optional[str] = None) -> Dict[str, Any]:
        """Take hyperlinks away, leaving the words they were on"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.remove_hyperlink(address=address, url=url,
                                                all=all,
                                                track_changes=track_changes,
                                                doc=doc)
