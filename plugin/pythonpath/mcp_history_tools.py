"""The tools for the undo history, and for text↔table conversion."""

from typing import Any, Dict, Optional

OURS = ("The undo history belongs to the document, so the reader's own typing "
        "sits in it beside this server's edits — each step says which it is, "
        "and this stops at the first one it did not make unless "
        "include_others says otherwise")


class HistoryTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_history(self):
        """The tools of this part, as clients see them."""
        self.tools["list_undo_steps_live"] = {
            "description": "What can be taken back in a Writer document, and what can be put back again, newest first. Each step carries the title the office gives it — this server's edits read \"MCP: …\", the reader's typing reads \"Typing: …\" — so it is clear whose work a step is before anything is undone",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many steps of each list to report",
                        "default": 20
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_undo_steps_live
        }

        self.tools["undo_live"] = {
            "description": "Take the last edit back — the way out of a mistake that would otherwise have to be edited away. " + OURS,
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "integer",
                        "description": "How many steps to take back",
                        "default": 1
                    },
                    "include_others": {
                        "type": "boolean",
                        "description": "Take back steps this server did not make, such as the reader's own typing",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.undo_live
        }

        self.tools["redo_live"] = {
            "description": "Put back what was taken away, under the same rule as undo_live",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "integer",
                        "description": "How many steps to put back",
                        "default": 1
                    },
                    "include_others": {
                        "type": "boolean",
                        "description": "Put back steps this server did not make",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.redo_live
        }

        self.tools["convert_text_to_table_live"] = {
            "description": "Turn paragraphs into a table, one row each, split at a separator — Writer's own conversion, so the formatting inside the cells survives. The separators themselves are taken out, since the conversion hands every character between the first cell and the last to some cell",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "The paragraphs to turn into rows: {\"paragraph\": N, \"through\": M}, {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "separator": {
                        "type": "string",
                        "description": "What divides the cells: \"tab\", \"semicolon\", \"comma\", \"pipe\", or the character itself",
                        "default": "tab"
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
            "handler": self.convert_text_to_table_live
        }

        self.tools["convert_table_to_text_live"] = {
            "description": "Turn a table back into paragraphs, its cells divided by a separator. What is inside the cells comes through; the table's own look — its borders and backgrounds — goes with the table",
            "parameters": {
                "type": "object",
                "properties": {
                    "table": {
                        "type": "string",
                        "description": "The table's name, from list_tables_live"
                    },
                    "separator": {
                        "type": "string",
                        "description": "What to put between the cells: \"tab\", \"semicolon\", \"comma\", \"pipe\", or the character itself",
                        "default": "tab"
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
                "required": ["table"]
            },
            "handler": self.convert_table_to_text_live
        }

    def list_undo_steps_live(self, limit: int = 20,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """What can be taken back, and what can be put back"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_undo_steps(limit=limit, doc=doc)

    def undo_live(self, steps: int = 1, include_others: bool = False,
                  document: Optional[str] = None) -> Dict[str, Any]:
        """Take the last edit back"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.undo(steps=steps,
                                    include_others=include_others, doc=doc)

    def redo_live(self, steps: int = 1, include_others: bool = False,
                  document: Optional[str] = None) -> Dict[str, Any]:
        """Put back what was taken away"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.redo(steps=steps,
                                    include_others=include_others, doc=doc)

    def convert_text_to_table_live(self, address: Any, separator: str = "tab",
                                   track_changes: Optional[bool] = None,
                                   document: Optional[str] = None
                                   ) -> Dict[str, Any]:
        """Turn paragraphs into a table"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.convert_text_to_table(
            address, separator=separator, track_changes=track_changes, doc=doc)

    def convert_table_to_text_live(self, table: str, separator: str = "tab",
                                   track_changes: Optional[bool] = None,
                                   document: Optional[str] = None
                                   ) -> Dict[str, Any]:
        """Turn a table back into paragraphs"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.convert_table_to_text(
            table, separator=separator, track_changes=track_changes, doc=doc)
