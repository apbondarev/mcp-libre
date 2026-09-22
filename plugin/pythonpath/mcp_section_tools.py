"""The tools for sections: the named regions a document is divided into."""

from typing import Any, Dict, Optional

PROTECTION = ("Writer protects a section against the reader's keyboard only — "
              "measured: the API writes straight through it — so the writing "
              "tools refuse a protected section themselves, and take "
              "allow_protected=true when you mean it")


class SectionTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_section(self):
        """The tools of this part, as clients see them."""
        self.tools["list_sections_live"] = {
            "description": "List the named sections of a Writer document, each with the text it covers and what makes it different: whether it is protected, whether it is hidden, how many columns it is set in, and whether its content is linked from another file. This is how to find out why a part of a document behaves unlike the rest. " + PROTECTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which sections: omit for the whole document, {\"heading\": N} for a section of the outline, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, a range, or {\"selection\": true}",
                        "properties": {
                            "heading": {"type": "integer"},
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "number": {
                        "type": "boolean",
                        "description": "Work out each section's paragraph number as well. That is a sweep of the body, where an anchor is two UNO calls, so it is off unless a human needs to be shown where things are",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_sections_live
        }

        self.tools["create_section_live"] = {
            "description": "Make a named section of the paragraphs an address covers — a region that can be protected, hidden, or set in columns of its own. The paragraphs stay where they are and keep their numbers, so no address changes",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "What the section covers: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "name": {
                        "type": "string",
                        "description": "The section's name, which is how it is named back to update or delete it"
                    },
                    "protected": {
                        "type": "boolean",
                        "description": "Protect it against being typed into",
                        "default": False
                    },
                    "visible": {
                        "type": "boolean",
                        "description": "false hides the section; its paragraphs are still there, still numbered and still readable",
                        "default": True
                    },
                    "columns": {
                        "type": "integer",
                        "description": "Set this part of the document in this many columns, 1 to 99"
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
                "required": ["address", "name"]
            },
            "handler": self.create_section_live
        }

        self.tools["update_section_live"] = {
            "description": "Protect a section, unprotect it, hide or show it, rename it, or set its columns. The text inside is left alone — this changes what the section is, not what it says",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The section's name, from list_sections_live"
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Rename it, in place"
                    },
                    "protected": {
                        "type": "boolean",
                        "description": "Protect the section, or unprotect it so the writing tools will work on its text"
                    },
                    "visible": {
                        "type": "boolean",
                        "description": "Show or hide it"
                    },
                    "columns": {
                        "type": "integer",
                        "description": "How many columns this part of the document is set in, 1 to 99"
                    },
                    "condition": {
                        "type": "string",
                        "description": "The condition under which Writer hides the section, in its own formula language; an empty string clears it"
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
                "required": ["name"]
            },
            "handler": self.update_section_live
        }

        self.tools["delete_section_live"] = {
            "description": "Remove a section, leaving every paragraph it held exactly where it was — only the region goes, with its protection, its columns and its link",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The section's name, from list_sections_live"
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
                "required": ["name"]
            },
            "handler": self.delete_section_live
        }

    def list_sections_live(self, address: Any = None, number: bool = False,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """List the sections of a Writer document"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_sections(address=address, number=number,
                                             doc=doc)

    def create_section_live(self, address: Any, name: str,
                            protected: bool = False, visible: bool = True,
                            columns: Optional[int] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Make a named section of the paragraphs an address covers"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.create_section(address, name,
                                              protected=protected,
                                              visible=visible, columns=columns,
                                              track_changes=track_changes,
                                              doc=doc)

    def update_section_live(self, name: str, new_name: Optional[str] = None,
                            protected: Optional[bool] = None,
                            visible: Optional[bool] = None,
                            columns: Optional[int] = None,
                            condition: Optional[str] = None,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Protect, hide, rename a section or set its columns"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.update_section(name, new_name=new_name,
                                              protected=protected,
                                              visible=visible, columns=columns,
                                              condition=condition,
                                              track_changes=track_changes,
                                              doc=doc)

    def delete_section_live(self, name: str,
                            track_changes: Optional[bool] = None,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Remove a section, leaving its paragraphs"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.delete_section(name,
                                              track_changes=track_changes,
                                              doc=doc)
