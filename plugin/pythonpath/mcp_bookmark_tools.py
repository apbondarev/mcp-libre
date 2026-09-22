"""The tools for bookmarks: names the document itself keeps for places."""

from typing import Any, Dict, Optional

SCOPE = ("Which bookmarks: omit for the whole document, {\"heading\": N} for a "
         "section, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, a "
         "range, or {\"selection\": true}")

DOCUMENT = ("URL of the document to act on, from list_open_documents; "
            "defaults to the active document")

ADDRESS_PARTS = {
    "anchor": {"type": ["string", "object"]},
    "heading": {"type": "integer"},
    "paragraph": {"type": "integer"},
    "through": {"type": "integer"},
    "offset": {"type": "integer"},
    "length": {"type": "integer"},
    "selection": {"type": "boolean"},
}


class BookmarkTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_bookmark(self):
        """The tools of this part, as clients see them."""
        self.tools["list_bookmarks_live"] = {
            "description": "List the bookmarks of a document, each with the text it covers and the address of that text. A bookmark is the document's own name for a place: unlike an anchor it is saved in the file, survives reopening and shows in the Navigator, and it moves with its text as the document is edited — so it is the handle to use when a place must be found again tomorrow",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {"type": "object", "description": SCOPE,
                                "properties": ADDRESS_PARTS},
                    "number": {
                        "type": "boolean",
                        "description": "Work out each bookmark's paragraph number as well. It is a sweep of the body — 195 bookmarks of a real guide cost 20s to number and 0.4s to anchor — so it is off unless a human needs to be shown where things are",
                        "default": False
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                }
            },
            "handler": self.list_bookmarks_live
        }

        self.tools["add_bookmark_live"] = {
            "description": "Name a place in the document so the document remembers it. Over a range the bookmark covers that text; at a caret it marks the spot. A name that is already taken is refused, because Writer would take it anyway and quietly mint \"name Copy 1\" instead",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "What to mark: a paragraph, a block, a range, a table cell, the selection, or an anchor",
                        "properties": ADDRESS_PARTS
                    },
                    "name": {"type": "string",
                             "description": "The bookmark's name, as the Navigator will show it"},
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this edit, false refuses to record it"
                    },
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["address", "name"]
            },
            "handler": self.add_bookmark_live
        }

        self.tools["rename_bookmark_live"] = {
            "description": "Give a bookmark another name, leaving it where it is",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The bookmark as it is called now"},
                    "new_name": {"type": "string", "description": "What to call it"},
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["name", "new_name"]
            },
            "handler": self.rename_bookmark_live
        }

        self.tools["delete_bookmark_live"] = {
            "description": "Take a bookmark away. The text it was on stays, and the result says what it covered so it can be put back",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Which bookmark"},
                    "document": {"type": "string", "description": DOCUMENT}
                },
                "required": ["name"]
            },
            "handler": self.delete_bookmark_live
        }

    def list_bookmarks_live(self, address: Any = None, number: bool = False,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """The bookmarks of a document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_bookmarks(address=address, number=number,
                                              doc=doc)

    def add_bookmark_live(self, address: Any, name: str,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Name a place in the document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.add_bookmark(address, name,
                                            track_changes=track_changes,
                                            doc=doc)

    def rename_bookmark_live(self, name: str, new_name: str,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Give a bookmark another name"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.rename_bookmark(name, new_name, doc=doc)

    def delete_bookmark_live(self, name: str,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Take a bookmark away"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_bookmark(name, doc=doc)
