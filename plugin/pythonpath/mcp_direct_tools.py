"""The tools for finding by style, and for formatting applied over one."""

from typing import Any, Dict, List, Optional


class DirectTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_direct(self):
        """The tools of this part, as clients see them."""
        self.tools["find_by_style_live"] = {
            "description": "Find every place a style is used: the paragraphs in \"Preformatted Text\", say, which is how the code blocks of a document are found, or the runs wearing a character style like \"Source Text\". A paragraph style is searched for rather than walked to, so it answers in milliseconds",
            "parameters": {
                "type": "object",
                "properties": {
                    "style": {
                        "type": "string",
                        "description": "The style's name, as list_styles_live reports it"
                    },
                    "family": {
                        "type": "string",
                        "enum": ["paragraph", "character"],
                        "description": "Which kind of style the name is",
                        "default": "paragraph"
                    },
                    "address": {
                        "type": "object",
                        "description": "Only inside this part of the document: {\"heading\": N} for a section, {\"paragraph\": N}, {\"paragraph\": N, \"through\": M}, a range, or {\"selection\": true}",
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
                    "max_results": {
                        "type": "integer",
                        "description": "How many hits to report",
                        "default": 200
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["style"]
            },
            "handler": self.find_by_style_live
        }

        self.tools["get_direct_formatting_live"] = {
            "description": "What is formatted by hand over the styles at an address — the bold, fonts and colours somebody applied on top of what the style says, which is what makes a document look inconsistent. Reported separately for the characters and for the paragraph, since the two are cleared separately",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which text: {\"paragraph\": N}, {\"paragraph\": N, \"offset\": K, \"length\": L}, {\"table\": \"Table1\", \"cell\": \"A2\"}, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
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
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.get_direct_formatting_live
        }

        self.tools["clear_direct_formatting_live"] = {
            "description": "Take the formatting applied over the styles off again, so the text goes back to what its style says. A hyperlink survives this and so does a character style — \"Source Text\" on inline code is not direct formatting — which is measured, not assumed: what goes is the bold, the fonts and the colours applied by hand",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which text: {\"paragraph\": N}, {\"paragraph\": N, \"through\": M} for a block, a range, {\"anchor\": \"a7f3c1\"} or {\"selection\": true}",
                        "properties": {
                            "anchor": {"type": ["string", "object"]},
                            "paragraph": {"type": "integer"},
                            "through": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "table": {"type": "string"},
                            "cell": {"type": "string"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "characters": {
                        "type": "boolean",
                        "description": "Clear the character formatting — bold, fonts, colours",
                        "default": True
                    },
                    "paragraphs": {
                        "type": "boolean",
                        "description": "Clear the paragraph formatting as well — alignment, spacing, indents, fills",
                        "default": False
                    },
                    "properties": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Only these, by the names get_direct_formatting_live reports, e.g. [\"bold\", \"color\"]"
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
            "handler": self.clear_direct_formatting_live
        }

    def find_by_style_live(self, style: str, family: str = "paragraph",
                           address: Any = None, max_results: int = 200,
                           document: Optional[str] = None) -> Dict[str, Any]:
        """Find every place a style is used"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.find_by_style(style, family=family,
                                             address=address,
                                             max_results=max_results, doc=doc)

    def get_direct_formatting_live(self, address: Any,
                                   document: Optional[str] = None
                                   ) -> Dict[str, Any]:
        """What is formatted by hand over the styles at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.get_direct_formatting(address, doc=doc)

    def clear_direct_formatting_live(self, address: Any,
                                     characters: bool = True,
                                     paragraphs: bool = False,
                                     properties: Optional[List[str]] = None,
                                     track_changes: Optional[bool] = None,
                                     document: Optional[str] = None
                                     ) -> Dict[str, Any]:
        """Take the formatting applied over the styles off again"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.clear_direct_formatting(
            address, characters=characters, paragraphs=paragraphs,
            properties=properties, track_changes=track_changes, doc=doc)
