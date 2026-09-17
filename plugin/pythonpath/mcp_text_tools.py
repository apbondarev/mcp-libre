"""The tools that change text, and the language and spelling of it."""

from typing import Any, Dict, Optional


class TextTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_text(self):
        """The tools of this part, as clients see them."""
        # Text manipulation tools
        self.tools["insert_text_live"] = {
            "description": "Insert text into a document, at the caret unless a position is given. This inserts and never replaces: with text selected it inserts at the start of the selection and leaves the original in place. To replace text use replace_selection_live or replace_range_live",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to insert"
                    },
                    "position": {
                        "type": "integer",
                        "description": "Position to insert at (optional, defaults to cursor position)"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["text"]
            },
            "handler": self.insert_text_live
        }

        # Editing tools
        self.tools["replace_selection_live"] = {
            "description": "Replace the selected text with a plain string. Fails when nothing is selected, and is refused when the selection holds several formatted runs or a hyperlink, since one string cannot carry their formatting — use read_runs_live and replace_runs_live for that",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to put in place of the selection"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting (see track_changes in get_document_info_live). True records this edit as a change to accept or reject, which leaves the original in place struck through. False refuses to record it even in a document that records everything. Either way the document's setting is left as its owner had it, and the result reports which happened"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag for the new text, such as \"ru-RU\". ALWAYS set this when writing text in a different language from what it replaces — translating, for instance. Without it the new text keeps the locale of the text it replaced, Writer spell-checks it against the wrong dictionary, and every single word appears underlined in red even though it is spelled correctly"
                    },
                    "flatten": {
                        "type": "boolean",
                        "description": "Accept losing the formatting. Without it the call is refused when the range holds more than one formatted run, or a hyperlink, because replacing such a range with one string destroys inline code, italics and links. The safe route is read_runs_live, then replace_runs_live",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["text"]
            },
            "handler": self.replace_selection_live
        }

        self.tools["replace_range_live"] = {
            "description": "Replace the text at an address with a plain string. Use the addresses returned by get_outline_live, read_paragraphs_live and find_text_live. This writes ONE stretch of uniform text, so it is refused when the range holds several formatted runs or a hyperlink — for those use read_runs_live and replace_runs_live, which keep each run's look",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to replace: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"through\": M} for a block of them, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, {\"table\": \"Table1\", \"cell\": \"A2\"} for a table cell, or {\"selection\": true} for the current selection. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string", "description": "An anchor held from an earlier call, instead of an index"},
                            "paragraph": {"type": "integer", "description": "0-based body paragraph index"},
                            "offset": {"type": "integer", "description": "Characters from the paragraph start, default 0"},
                            "length": {"type": "integer", "description": "Characters to replace, default to the end of the paragraph"},
                            "selection": {"type": "boolean", "description": "Use the current selection instead of an index"}
                        }
                    },
                    "text": {
                        "type": "string",
                        "description": "Text to put in place of what the address points at"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting (see track_changes in get_document_info_live). True records this edit as a change to accept or reject, which leaves the original in place struck through. False refuses to record it even in a document that records everything. Either way the document's setting is left as its owner had it, and the result reports which happened"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag for the new text, such as \"ru-RU\". ALWAYS set this when writing text in a different language from what it replaces — translating, for instance. Without it the new text keeps the locale of the text it replaced, Writer spell-checks it against the wrong dictionary, and every single word appears underlined in red even though it is spelled correctly"
                    },
                    "flatten": {
                        "type": "boolean",
                        "description": "Accept losing the formatting. Without it the call is refused when the range holds more than one formatted run, or a hyperlink, because replacing such a range with one string destroys inline code, italics and links. The safe route is read_runs_live, then replace_runs_live",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "text"]
            },
            "handler": self.replace_range_live
        }

        self.tools["set_language_live"] = {
            "description": "Mark the text at an address as being in a language, so Writer spell-checks it against the right dictionary. Text left with the wrong language is underlined word by word even when it is spelled correctly",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to set the language: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block of whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, {\"table\": \"Table1\", \"cell\": \"A2\"} for a table cell, or {\"selection\": true}. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "language": {
                        "type": "string",
                        "description": "Language tag such as \"ru-RU\", \"en-US\" or just \"de\""
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "language"]
            },
            "handler": self.set_language_live
        }

        self.tools["check_spelling_live"] = {
            "description": "Report misspelled words in the active Writer document, each with the address of the word and the dictionary's suggestions. Every word is judged against the language of the text it sits in, so check the reported language before trusting a hit: text marked with the wrong language is reported as misspelled even when it is correct, and set_language_live fixes that",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Limit the check to one paragraph — {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block of whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true} — body text, not a table cell. Omit to check the whole document. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "How many misspellings to report (max 200)",
                        "default": 50
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.check_spelling_live
        }

        # Runs: reading and rewriting formatted pieces
        self.tools["read_runs_live"] = {
            "description": "Read the text at an address as the formatted runs it is made of, each with its own address, font, colour and language. Use this before rewriting text that is not uniformly formatted: replacing such a range in one go flattens it, so a monospace term or a coloured phrase inside it loses its look",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which text: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"table\": \"Table1\", \"cell\": \"A2\"} for a table cell, or {\"selection\": true}. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.read_runs_live
        }

        self.tools["replace_runs_live"] = {
            "description": "Replace the text at an address with a sequence of runs, each carrying its own formatting. This is how text keeps its appearance through a translation: read the runs, translate each one's text, write them back. Also the cheap way to syntax-highlight, since the whole sequence is one edit and one undo step",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which text: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for whole paragraphs, {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"table\": \"Table1\", \"cell\": \"A2\"} for a table cell, or {\"selection\": true}. An anchor from anchor or from find_text/read_paragraphs with anchors: true can be given instead, as {\"anchor\": \"a7f3c1\"} — it keeps pointing at the same text after edits have renumbered the paragraphs",
                        "properties": {
                            "anchor": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "runs": {
                        "type": "array",
                        "description": "The pieces to write, in order. Each needs text; give it the formatting you want it to keep, since anything unspecified is left to whatever the surrounding text imposes",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "bold": {"type": "boolean"},
                                "italic": {"type": "boolean"},
                                "underline": {"type": "boolean"},
                                "font_name": {"type": "string"},
                                "font_size": {"type": "number"},
                                "color": {"type": "string", "description": "#RRGGBB"},
                                "background_color": {"type": "string", "description": "#RRGGBB"},
                                "language": {"type": "string", "description": "Language tag such as ru-RU"},
                                "link": {"type": "string", "description": "Hyperlink URL. Carry this over when rewriting text that read_runs_live reported a link on, or the link is destroyed"},
                                "link_target": {"type": "string", "description": "Where the link opens, e.g. \"_blank\""},
                                "character_style": {"type": "string", "description": "Character style name, e.g. \"Source Text\" for inline code. Prefer carrying this over instead of copying the font it implies"}
                            },
                            "required": ["text"]
                        }
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "flatten": {
                        "type": "boolean",
                        "description": "Write over the recorded changes this range carries. Without it such a rewrite is refused, because a deletion still waiting to be accepted would come back as ordinary text; settling them first with accept_tracked_changes_live or reject_tracked_changes_live is the way that keeps the record",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "runs"]
            },
            "handler": self.replace_runs_live
        }

    def insert_text_live(self, text: str, position: Optional[int] = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Insert text into a document, at the caret unless told otherwise"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.insert_text(text, position, doc=doc)

    def replace_selection_live(self, text: str,
                               track_changes: Optional[bool] = None,
                               language: Optional[str] = None,
                               flatten: bool = False,
                               document: Optional[str] = None) -> Dict[str, Any]:
        """Replace the selected text in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_selection(text, track_changes=track_changes,
                                                 language=language,
                                                 flatten=flatten, doc=doc)

    def replace_range_live(self, address: Any, text: str,
                           track_changes: Optional[bool] = None,
                           language: Optional[str] = None,
                           flatten: bool = False,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Replace the text at an address in a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_range(address, text,
                                             track_changes=track_changes,
                                             language=language,
                                             flatten=flatten, doc=doc)

    def read_runs_live(self, address: Any,
                       document: Optional[str] = None) -> Dict[str, Any]:
        """Read the text at an address as its formatted runs"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.read_runs(address, doc=doc)

    def replace_runs_live(self, address: Any, runs: Any,
                          track_changes: Optional[bool] = None,
                          flatten: bool = False,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Replace the text at an address with a sequence of formatted runs"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.replace_runs(address, runs,
                                            track_changes=track_changes,
                                            flatten=flatten, doc=doc)

    def set_language_live(self, address: Any, language: str,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Mark the text at an address as being in a language"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_language(address, language, doc=doc)

    def check_spelling_live(self, address: Any = None, max_results: int = 50,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Report misspelled words with an address and suggestions for each"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.check_spelling(address=address,
                                              max_results=max_results, doc=doc)
