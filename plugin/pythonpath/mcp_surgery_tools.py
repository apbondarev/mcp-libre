"""The tools for paragraph surgery: splitting, joining, moving, copying."""

from typing import Any, Dict, Optional

BLOCK = ("Which paragraphs: {\"paragraph\": N}, {\"paragraph\": N, "
         "\"through\": M} for a block of them, {\"anchor\": \"a7f3c1\"} or "
         "{\"selection\": true}")

CARRIES = ("It carries everything the paragraph had — its comments, its "
           "pictures, its formatting — where reading it and writing it again "
           "somewhere else leaves all of that behind")


class SurgeryTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_surgery(self):
        """The tools of this part, as clients see them."""
        self.tools["split_paragraph_live"] = {
            "description": "Cut a paragraph in two at an address. A comment covering the cut ends up on both halves, which is Writer's own doing",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to cut: {\"paragraph\": N, \"offset\": K}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.split_paragraph_live
        }

        self.tools["merge_paragraphs_live"] = {
            "description": "Join a paragraph with the one after it, or a block of them into one. The break between two paragraphs is a single character and that is all this takes away; a break that is really a table is left alone",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": BLOCK + ". One paragraph means \"join this one with the next\"",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.merge_paragraphs_live
        }

        self.tools["move_paragraph_live"] = {
            "description": "Move a paragraph, or a block of them, up or down the document. " + CARRIES + ". Say where in exactly one way: direction with steps, or `to`",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": BLOCK,
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "Which way to move it"
                    },
                    "steps": {
                        "type": "integer",
                        "description": "How many paragraphs to move past",
                        "default": 1
                    },
                    "to": {
                        "type": "integer",
                        "description": "Put the block in front of this paragraph instead, naming it by the numbering as it stands now"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.move_paragraph_live
        }

        self.tools["copy_paragraphs_live"] = {
            "description": "Copy a paragraph, or a block of them, in front of another paragraph. " + CARRIES + " — the comments come with the copy. It goes through the document's own view rather than the system clipboard, so nothing the reader copied is disturbed",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": BLOCK,
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "to": {
                        "type": "integer",
                        "description": "The copy goes in front of this paragraph"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Record this edit as a tracked change. Omitted follows the document's own setting"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "to"]
            },
            "handler": self.copy_paragraphs_live
        }

    def split_paragraph_live(self, address: Any,
                             track_changes: Optional[bool] = None,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Cut a paragraph in two at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.split_paragraph(address,
                                               track_changes=track_changes,
                                               doc=doc)

    def merge_paragraphs_live(self, address: Any,
                              track_changes: Optional[bool] = None,
                              document: Optional[str] = None
                              ) -> Dict[str, Any]:
        """Join a paragraph with the one after it, or a block into one"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.merge_paragraphs(address,
                                                track_changes=track_changes,
                                                doc=doc)

    def move_paragraph_live(self, address: Any,
                            direction: Optional[str] = None, steps: int = 1,
                            to: Optional[int] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Move a paragraph, or a block of them, up or down"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.move_paragraph(address, direction=direction,
                                              steps=steps, to=to,
                                              track_changes=track_changes,
                                              doc=doc)

    def copy_paragraphs_live(self, address: Any, to: int,
                             track_changes: Optional[bool] = None,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Copy a paragraph, or a block of them, in front of another"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.copy_paragraphs(address, to,
                                               track_changes=track_changes,
                                               doc=doc)
