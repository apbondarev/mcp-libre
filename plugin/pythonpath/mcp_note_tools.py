"""The tools for footnotes and endnotes: a text of their own, on one mark."""

from typing import Any, Dict, Optional

MARK = ("A note is named by where its mark sits, since a note carries no id "
        "of its own — pass the address list_notes_live reports")


class NoteTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_note(self):
        """The tools of this part, as clients see them."""
        self.tools["list_notes_live"] = {
            "description": "List the footnotes and endnotes of a Writer document, each with what it says, the mark it wears and the address of that mark. Use it to read the notes of a section before rewriting it — a rewrite of the mark destroys the note and leaves the number behind as ordinary text",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which notes: omit for the whole document, {\"heading\": N} for a section, {\"paragraph\": N} for one paragraph, {\"paragraph\": N, \"through\": M} for a block, a range, or {\"selection\": true}",
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
                    "kind": {
                        "type": "string",
                        "enum": ["footnote", "endnote"],
                        "description": "Only the footnotes, or only the endnotes"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_notes_live
        }

        self.tools["add_note_live"] = {
            "description": "Add a footnote or an endnote after the text an address names. The mark goes at the end of that text, where a writer would put it, and the note's own text lives at the foot of the page or the end of the document — the paragraph gains nothing but the one mark",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "What the note is about; the mark goes after it: {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"paragraph\": N}, {\"anchor\": \"a7f3c1\"}, {\"table\": \"Table1\", \"cell\": \"A2\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "text": {
                        "type": "string",
                        "description": "What the note says"
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["footnote", "endnote"],
                        "description": "A footnote sits at the foot of its page, an endnote at the end of the document",
                        "default": "footnote"
                    },
                    "label": {
                        "type": "string",
                        "description": "A mark of your own, such as \"*\". Left out, Writer numbers the note itself"
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
                "required": ["address", "text"]
            },
            "handler": self.add_note_live
        }

        self.tools["update_note_live"] = {
            "description": "Change what a note says, or the mark it wears. The document's own text is left alone: this edits the note at the foot of the page, not the sentence carrying it. " + MARK,
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where the note's mark sits, as list_notes_live reports it",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "text": {
                        "type": "string",
                        "description": "New text for the note"
                    },
                    "label": {
                        "type": "string",
                        "description": "New mark; an empty string gives the note back to Writer's own numbering"
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
            "handler": self.update_note_live
        }

        self.tools["delete_note_live"] = {
            "description": "Remove a footnote or an endnote, taking its mark out of the sentence. What the note said comes back in the result, since nothing else keeps it. " + MARK,
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where the note's mark sits, as list_notes_live reports it",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "bookmark": {"type": "string"},
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
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
            "handler": self.delete_note_live
        }

    def list_notes_live(self, address: Any = None, kind: Optional[str] = None,
                        document: Optional[str] = None) -> Dict[str, Any]:
        """List the footnotes and endnotes with their marks"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_notes(address=address, kind=kind, doc=doc)

    def add_note_live(self, address: Any, text: str, kind: str = "footnote",
                      label: Optional[str] = None,
                      track_changes: Optional[bool] = None,
                      document: Optional[str] = None) -> Dict[str, Any]:
        """Add a footnote or an endnote after the text an address names"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.add_note(address, text, kind=kind, label=label,
                                        track_changes=track_changes, doc=doc)

    def update_note_live(self, address: Any, text: Optional[str] = None,
                         label: Optional[str] = None,
                         track_changes: Optional[bool] = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Change what a note says, or the mark it wears"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_note(address, text=text, label=label,
                                           track_changes=track_changes,
                                           doc=doc)

    def delete_note_live(self, address: Any,
                         track_changes: Optional[bool] = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Remove a note and its mark"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_note(address,
                                           track_changes=track_changes,
                                           doc=doc)
