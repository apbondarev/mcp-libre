"""The tools that read a document: where the reader is, what it says."""

from typing import Any, Dict, Optional


class ReadingTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_reading(self):
        """The tools of this part, as clients see them."""
        # Cursor and selection tools
        self.tools["select_live"] = {
            "description": "Select the text at an address, the way a reader would with the mouse — so the human sees what is about to be worked on, and so anything that acts on a selection can be pointed at it. Selecting changes no text",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "What to select: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block of whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L}, or {\"table\": \"Table1\", \"cell\": \"A2\"}. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"}
                        }
                    },
                    "number": {
                        "type": "boolean",
                        "description": "Say which paragraph numbers were selected. That means comparing the range with every paragraph of the document — 15 to 23 seconds on a real guide — where the count, the tables and the anchor come from the range itself",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.select_live
        }
        
        self.tools["get_cursor_info_live"] = {
            "description": "Get the cursor position, the paragraph containing the cursor, and the selected text of a Writer document — where the reader is standing and what they have picked out. The paragraph comes back as `address`, an anchor: pass it straight to the next tool. Its **number** is not counted unless `number` asks — a paragraph has no index in UNO, so working one out means counting every paragraph before it, which took three seconds with the caret deep in a real 6981-paragraph document",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "boolean",
                        "description": "Count the caret's paragraph number as well, for showing a human where they are. It walks the body — 0.55 ms a paragraph — and `address` names the same paragraph without it",
                        "default": False
                    },
                    "character_offset": {
                        "type": "boolean",
                        "description": "Add `document_offset`, the caret's place in characters from the start of the body. It counts the number too and pulls every paragraph's text over the bridge on the way",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.get_cursor_info_live
        }

        # Reading tools
        self.tools["read_paragraphs_live"] = {
            "description": "Read a window of paragraphs from the active Writer document, with their indices and styles. A formula is an object, not text, so a paragraph's `text` leaves it out (\"equals  of the whole\"): a paragraph that holds one also carries `formulas` (name, StarMath text, offset) and `text_with_formulas`, its text with each formula put back as ⟦formula: …⟧ where it stands — read that one, when it is there. `text` is unchanged, since every offset counts in it",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": ["integer", "object"],
                        "description": "Where to start: a 0-based paragraph index, or an address — {\"anchor\": \"a7f3c1\"}, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, or the `address` a previous read or a search handed back, passed straight through. Prefer the address: it still names the same paragraph after edits above it have renumbered the document, so a long document can be walked without carrying a number from one call to the next. The `start` in the result says which number it came to",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many paragraphs to read. 50 when nobody says, and a block address says its own length; it is a window rather than a limit, so a whole document can be asked for — but with `anchors` on, at most 2000, since that is how many anchors a session keeps",
                        "default": 50
                    },
                    "anchors": {
                        "type": "boolean",
                        "description": "Hand every paragraph out with an `address` holding an anchor beside its index — pass that address back as it is and it reaches the same paragraph after edits above it, yours or the reader's, have renumbered the document. On by default; false saves the cost on a large read that nothing will be written back to",
                        "default": True
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
            "description": "List the headings of the active Writer document with the paragraph index of each — the map of a long document, without reading it. One call carries at most 200 headings, so a long document is paged: `more` says there are further headings, and the last heading's own `address` is what to pass back as `start`",
            "parameters": {
                "type": "object",
                "properties": {
                    "start": {
                        "type": ["integer", "object"],
                        "description": "Where in the document to begin: a 0-based paragraph index, or an address — an anchor, {\"paragraph\": N}, or the `address` of a heading from an earlier call, passed straight through. The headings from there on are returned",
                        "default": 0
                    },
                    "count": {
                        "type": "integer",
                        "description": "How many headings to return. 200 when nobody says, and that is a default, not a limit: ask for more and the rest come with it, so a whole map — hundreds of headings — is one call",
                        "default": 200
                    },
                    "anchors": {
                        "type": "boolean",
                        "description": "Give every heading an `address` holding an anchor beside its index, so the map still points at the right paragraphs after edits have renumbered them. On by default",
                        "default": True
                    },
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
                    "paragraphs_before": {
                        "type": "integer",
                        "description": "Bring this many paragraphs before each hit — index, style and text — so a hit arrives with the block around it instead of needing a second call",
                        "default": 0
                    },
                    "paragraphs_after": {
                        "type": "integer",
                        "description": "Bring this many paragraphs after each hit, the same way. A hit inside a table cell has no body neighbours and reports null",
                        "default": 0
                    },
                    "anchors": {
                        "type": "boolean",
                        "description": "Put an anchor in every hit's address, so passing the address back reaches the match after the document has changed around it — a plan made from one search survives its own edits and the reader's typing. On by default",
                        "default": True
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
            "description": "Get the whole text of a document as one string. For anything but a quick look prefer read_paragraphs_live, whose paragraphs carry indices and styles to act on",
            "parameters": {
                "type": "object",
                "properties": {
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.get_text_content_live
        }

    def select_live(self, address: Any, number: bool = False,
                    document: Optional[str] = None) -> Dict[str, Any]:
        """Select the text at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.select(address, number=number, doc=doc)

    def get_cursor_info_live(self, number: bool = False,
                             character_offset: bool = False,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Get cursor position, current paragraph and selected text"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_cursor_info(
            number=number, character_offset=character_offset, doc=doc)

    def read_paragraphs_live(self, start: Any = 0,
                             count: Optional[int] = None,
                             anchors: bool = True,
                             document: Optional[str] = None) -> Dict[str, Any]:
        """Read a window of paragraphs from a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_paragraphs(start=start, count=count,
                                               anchors=anchors, doc=doc)

    def get_outline_live(self, start: Any = 0, count: Optional[int] = None,
                         anchors: bool = True,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """List the headings of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_outline(start=start, count=count,
                                           anchors=anchors, doc=doc)

    def find_text_live(self, query: str, regex: bool = False,
                       case_sensitive: bool = False, max_results: int = 50,
                       paragraphs_before: int = 0, paragraphs_after: int = 0,
                       anchors: bool = True,
                       document: Optional[str] = None) -> Dict[str, Any]:
        """Find text in a Writer document, with the block around each hit"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.find_text(
            query, regex=regex, case_sensitive=case_sensitive,
            max_results=max_results, paragraphs_before=paragraphs_before,
            paragraphs_after=paragraphs_after, anchors=anchors, doc=doc)

    def get_text_content_live(self,
                              document: Optional[str] = None) -> Dict[str, Any]:
        """Get the whole text of a document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_text_content(doc=doc)
