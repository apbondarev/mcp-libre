"""The tools that read a document: where the reader is, what it says."""

from typing import Any, Dict, Optional


class ReadingTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_reading(self):
        """The tools of this part, as clients see them."""
        # Cursor and selection tools
        self.tools["get_cursor_info_live"] = {
            "description": "Get the cursor position, the paragraph containing the cursor, and the selected text in the active Writer document",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_cursor_info_live
        }

        # Reading tools
        self.tools["read_paragraphs_live"] = {
            "description": "Read a window of paragraphs from the active Writer document, with their indices and styles",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": "integer",
                        "description": "Index of the first paragraph to read (0-based)",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many paragraphs to read (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.read_paragraphs_live
        }

        self.tools["get_outline_live"] = {
            "description": "List the headings of the active Writer document with the paragraph index of each",
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.get_outline_live
        }

        self.tools["find_text_live"] = {
            "description": "Find text in the active Writer document, returning an address for each match that other tools can act on",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text or regular expression to search for"
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "Treat the query as a regular expression",
                        "default": False
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Match case exactly",
                        "default": False
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many matches to return (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["query"]
            },
            "handler": self.find_text_live
        }

        # Content reading tools
        self.tools["get_text_content_live"] = {
            "description": "Get the text content of the currently active document",
            "parameters": {
                "type": "object",
                "properties": {}
            },
            "handler": self.get_text_content_live
        }

    def get_cursor_info_live(self) -> Dict[str, Any]:
        """Get cursor position, current paragraph and selected text"""
        return self.uno_bridge.get_cursor_info()

    def read_paragraphs_live(self, start: int = 0, count: int = 50,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Read a window of paragraphs from a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_paragraphs(start=start, count=count, doc=doc)

    def get_outline_live(self, document: Optional[str] = None) -> Dict[str, Any]:
        """List the headings of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_outline(doc=doc)

    def find_text_live(self, query: str, regex: bool = False,
                       case_sensitive: bool = False, max_results: int = 50,
                       document: Optional[str] = None) -> Dict[str, Any]:
        """Find text in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.find_text(query, regex=regex,
                                         case_sensitive=case_sensitive,
                                         max_results=max_results, doc=doc)

    def get_text_content_live(self) -> Dict[str, Any]:
        """Get text content of the currently active document"""
        return self.uno_bridge.get_text_content()
