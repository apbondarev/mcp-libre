"""The tools for comments: the notes in the margin and what they sit on."""

from typing import Any, Dict, Optional


class CommentTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_comment(self):
        """The tools of this part, as clients see them."""
        # Comments
        self.tools["list_comments_live"] = {
            "description": "List the comments (annotations) of the active Writer document, each with its author, text, resolved state, the address of the text it is anchored to, and that text itself. Use it to see what a reviewer asked for, and to check which text carries a comment before rewriting it",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which comments: omit for the whole document, {\"heading\": N} for a section (the heading's paragraph index from get_outline_live, covering everything under it), {\"paragraph\": N} for one paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for the comments overlapping that range, or {\"selection\": true} for what is selected",
                        "properties": {
                            "heading": {"type": "integer"},
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
                }
            },
            "handler": self.list_comments_live
        }

        self.tools["add_comment_live"] = {
            "description": "Anchor a new comment to the text at an address, the way a reviewer's margin note is anchored. Use it to answer a question or flag a passage without changing the text itself",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which text the comment is about: {\"paragraph\": N}, {\"paragraph\": N, \"offset\": K, \"length\": L} or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "text": {
                        "type": "string",
                        "description": "The comment's text"
                    },
                    "author": {
                        "type": "string",
                        "description": "Name shown as the comment's author; defaults to LibreOffice's own user name"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language of the comment's own text, as a tag like \"ru-RU\". Writer spell checks the note in the margin against it, so a Russian comment left at the document's language is underlined word by word"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "text"]
            },
            "handler": self.add_comment_live
        }

        self.tools["update_comment_live"] = {
            "description": "Change a comment's text, author or resolved state. The comment is named by the id list_comments_live reports; the text it is anchored to is left alone, since this edits the note in the margin and not the document",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment_id": {
                        "type": "string",
                        "description": "The comment's id, from list_comments_live"
                    },
                    "text": {
                        "type": "string",
                        "description": "New text for the comment"
                    },
                    "author": {
                        "type": "string",
                        "description": "New author name"
                    },
                    "resolved": {
                        "type": "boolean",
                        "description": "Mark the comment resolved, or reopen it"
                    },
                    "language": {
                        "type": "string",
                        "description": "Language of the comment's own text, as a tag like \"ru-RU\". Writer marks a note with a language only when the note is created, so this makes the comment again on the same anchor: the text, author and resolved state are kept, the id and the date are new, and the result says so"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["comment_id"]
            },
            "handler": self.update_comment_live
        }

        self.tools["delete_comment_live"] = {
            "description": "Delete a comment, named by the id list_comments_live reports. The text it was anchored to stays; the result says what was removed, so it can be put back if that was a mistake",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment_id": {
                        "type": "string",
                        "description": "The comment's id, from list_comments_live"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["comment_id"]
            },
            "handler": self.delete_comment_live
        }

        self.tools["set_comment_language_live"] = {
            "description": "Set the language the document's comments are written in, so Writer stops underlining Russian notes as misspelled English. This reaches the comments added from then on: Writer marks a note when it is created, so the ones already in the document keep their language and update_comment_live changes one of those",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Language tag, e.g. \"ru-RU\" or \"en-US\""
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["language"]
            },
            "handler": self.set_comment_language_live
        }

    def list_comments_live(self, address: Any = None,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """List the comments of a Writer document with their anchors"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_comments(address=address, doc=doc)

    def add_comment_live(self, address: Any, text: str, author: str = "",
                         language: Optional[str] = None,
                         document: Optional[str] = None) -> Dict[str, Any]:
        """Anchor a new comment to the text at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.add_comment(address, text, author=author,
                                           language=language, doc=doc)

    def update_comment_live(self, comment_id: str, text: Optional[str] = None,
                            author: Optional[str] = None,
                            resolved: Optional[bool] = None,
                            language: Optional[str] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Change a comment's text, author, language or resolved state"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_comment(comment_id, text=text,
                                              author=author, resolved=resolved,
                                              language=language, doc=doc)

    def delete_comment_live(self, comment_id: str,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Delete a comment, leaving the text it was anchored to"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_comment(comment_id, doc=doc)

    def set_comment_language_live(self, language: str,
                                  document: Optional[str] = None
                                  ) -> Dict[str, Any]:
        """Set the language the document's comments are written in"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.set_comment_language(language, doc=doc)
