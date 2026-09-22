"""The tools for indexes: the tables a document writes about itself."""

from typing import Any, Dict, Optional

MOVES = ("An index's entries are body paragraphs, so inserting or updating "
         "one moves every address below it — the result says by how many")


class IndexTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_index(self):
        """The tools of this part, as clients see them."""
        self.tools["list_indexes_live"] = {
            "description": "List the tables of contents and other indexes of a Writer document, each with what it is built from, how many entries it holds and where it sits. " + MOVES,
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "boolean",
                        "description": "Work out each index's paragraph number as well, which is a sweep of the body where its anchor is two UNO calls",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_indexes_live
        }

        self.tools["insert_index_live"] = {
            "description": "Insert a table of contents, an alphabetical index or one of the other indexes before the paragraph an address names. It is written at once rather than left empty, and a table of contents is built from the headings — LibreOffice's own default builds it from index marks and lists nothing. " + MOVES,
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "The index goes before this paragraph: {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["contents", "alphabetical", "illustrations",
                                 "tables", "objects", "user", "bibliography"],
                        "description": "What to build: \"contents\" is a table of contents from the headings, \"alphabetical\" an index from the marks add_index_mark_live leaves, \"illustrations\" and \"tables\" lists of the captions",
                        "default": "contents"
                    },
                    "title": {
                        "type": "string",
                        "description": "The heading the index wears, e.g. \"Содержание\"; each kind has a default in the office's language"
                    },
                    "levels": {
                        "type": "integer",
                        "description": "How many heading levels a table of contents takes in, 1 to 10 (10 by default)"
                    },
                    "from_outline": {
                        "type": "boolean",
                        "description": "Build a table of contents from the headings",
                        "default": True
                    },
                    "from_marks": {
                        "type": "boolean",
                        "description": "Build a table of contents from index marks as well",
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
                },
                "required": ["address"]
            },
            "handler": self.insert_index_live
        }

        self.tools["update_indexes_live"] = {
            "description": "Write the indexes again from what the document says now — a heading that has been rewritten or translated shows in the old words until this is run. Say which in exactly one way: name for a single index, or all=true for every one. " + MOVES,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The index's name, from list_indexes_live, e.g. \"Table of Contents1\""
                    },
                    "all": {
                        "type": "boolean",
                        "description": "Update every index in the document",
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
            "handler": self.update_indexes_live
        }

        self.tools["delete_index_live"] = {
            "description": "Remove an index, and the paragraphs it wrote with it. The result says how many went, since every address below the index moves back by that many",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The index's name, from list_indexes_live"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["name"]
            },
            "handler": self.delete_index_live
        }

        self.tools["add_index_mark_live"] = {
            "description": "Mark a piece of text for the alphabetical index, under a key it should be listed by. The text itself is untouched — the mark covers it the way a bookmark does — and an alphabetical index built afterwards lists it",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "The text to mark: {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"}, {\"table\": \"Table1\", \"cell\": \"A2\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "key": {
                        "type": "string",
                        "description": "The word the index lists this under"
                    },
                    "secondary_key": {
                        "type": "string",
                        "description": "A second level under that key"
                    },
                    "text": {
                        "type": "string",
                        "description": "What the index shows instead of the marked text"
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
                "required": ["address", "key"]
            },
            "handler": self.add_index_mark_live
        }

    def list_indexes_live(self, number: bool = False,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """List the indexes of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_indexes(number=number, doc=doc)

    def insert_index_live(self, address: Any, kind: str = "contents",
                          title: Optional[str] = None,
                          levels: Optional[int] = None,
                          from_outline: bool = True, from_marks: bool = False,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Insert a table of contents or another index"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_index(
            address, kind=kind, title=title, levels=levels,
            from_outline=from_outline, from_marks=from_marks,
            track_changes=track_changes, doc=doc)

    def update_indexes_live(self, name: Optional[str] = None,
                            all: bool = False,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Write the indexes again from what the document says now"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_indexes(name=name, all=all,
                                              track_changes=track_changes,
                                              doc=doc)

    def delete_index_live(self, name: str,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Remove an index and the paragraphs it wrote"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_index(name, doc=doc)

    def add_index_mark_live(self, address: Any, key: str,
                            secondary_key: Optional[str] = None,
                            text: Optional[str] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Mark a piece of text for the alphabetical index"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.add_index_mark(
            address, key, secondary_key=secondary_key, text=text,
            track_changes=track_changes, doc=doc)
