"""The tools a reviewer needs: reading the recorded changes, and settling them."""

from typing import Any, Dict, Optional

SCOPE = ("Which changes: omit for the whole document, {\"heading\": N} for a "
         "section, {\"paragraph\": N} for one paragraph, {\"paragraph\": N, "
         "\"through\": M} for a block, {\"paragraph\": N, \"offset\": K, "
         "\"length\": L} for a range, or {\"selection\": true} — body text, "
         "not a table cell")

PICKING = ("Say which changes in exactly one way: change_id for a single one, "
           "author for everything recorded under that name, address for a part "
           "of the document, or all=true for every change in it")


class ReviewTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_review(self):
        """The tools of this part, as clients see them."""
        self.tools["list_tracked_changes_live"] = {
            "description": "List the changes recorded in the document — what each one did (insert, delete, format), who made it, when, the text it covers and the address of that text. This is the other half of track_changes: the tools record edits, and this is how a reviewer reads what is waiting to be accepted or thrown away. The result also says whether the document is still recording, and which authors are in it",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": SCOPE,
                        "properties": {
                            "anchor": {"type": "string"},
                            "heading": {"type": "integer"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "author": {
                        "type": "string",
                        "description": "Only the changes recorded under this name, as list_tracked_changes_live reports it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_tracked_changes_live
        }

        self.tools["accept_tracked_changes_live"] = {
            "description": "Accept recorded changes: the edit becomes the text, and the mark goes. " + PICKING + ". The whole call is one undo step, and the reader's own selection is put back afterwards",
            "parameters": {
                "type": "object",
                "properties": {
                    "change_id": {
                        "type": "string",
                        "description": "The change's id, from list_tracked_changes_live"
                    },
                    "author": {
                        "type": "string",
                        "description": "Accept everything recorded under this name"
                    },
                    "address": {
                        "type": "object",
                        "description": SCOPE,
                        "properties": {
                            "anchor": {"type": "string"},
                            "heading": {"type": "integer"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Accept every change in the document",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.accept_tracked_changes_live
        }

        self.tools["reject_tracked_changes_live"] = {
            "description": "Reject recorded changes: the text goes back to what it was before the edit, and the mark goes. " + PICKING + ". The whole call is one undo step, and the reader's own selection is put back afterwards",
            "parameters": {
                "type": "object",
                "properties": {
                    "change_id": {
                        "type": "string",
                        "description": "The change's id, from list_tracked_changes_live"
                    },
                    "author": {
                        "type": "string",
                        "description": "Reject everything recorded under this name"
                    },
                    "address": {
                        "type": "object",
                        "description": SCOPE,
                        "properties": {
                            "anchor": {"type": "string"},
                            "heading": {"type": "integer"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Reject every change in the document",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.reject_tracked_changes_live
        }

    def list_tracked_changes_live(self, address: Any = None,
                                  author: Optional[str] = None,
                                  document: Optional[str] = None
                                  ) -> Dict[str, Any]:
        """Read the changes waiting to be accepted or rejected"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_tracked_changes(address=address,
                                                    author=author, doc=doc)

    def accept_tracked_changes_live(self, change_id: Optional[str] = None,
                                    author: Optional[str] = None,
                                    address: Any = None, all: bool = False,
                                    document: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """Take recorded changes into the text"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.accept_tracked_changes(
            change_id=change_id, author=author, address=address, all=all,
            doc=doc)

    def reject_tracked_changes_live(self, change_id: Optional[str] = None,
                                    author: Optional[str] = None,
                                    address: Any = None, all: bool = False,
                                    document: Optional[str] = None
                                    ) -> Dict[str, Any]:
        """Throw recorded changes away"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.reject_tracked_changes(
            change_id=change_id, author=author, address=address, all=all,
            doc=doc)
