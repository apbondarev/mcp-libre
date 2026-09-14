"""The tools for how text looks, and for the styles behind it."""

from typing import Any, Dict, Optional


class FormattingTools:
    """Part of LibreOfficeMCPServer — see mcp_server.py."""

    def _register_formatting(self):
        """The tools of this part, as clients see them."""
        # Text formatting tools
        self.tools["format_text_live"] = {
            "description": "Apply formatting to selected text in active document",
            "parameters": {
                "type": "object",
                "properties": {
                    "bold": {
                        "type": "boolean",
                        "description": "Apply bold formatting"
                    },
                    "italic": {
                        "type": "boolean",
                        "description": "Apply italic formatting"
                    },
                    "underline": {
                        "type": "boolean",
                        "description": "Apply underline formatting"
                    },
                    "font_size": {
                        "type": "number",
                        "description": "Font size in points"
                    },
                    "font_name": {
                        "type": "string",
                        "description": "Font family name"
                    }
                }
            },
            "handler": self.format_text_live
        }

        # Formatting tools
        self.tools["format_range_live"] = {
            "description": "Apply character formatting to the text at an address in the active Writer document. Unlike format_text_live this needs no selection, so an assistant can format a paragraph it found with get_outline_live or find_text_live",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to act: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "bold": {"type": "boolean", "description": "Bold on or off"},
                    "italic": {"type": "boolean", "description": "Italic on or off"},
                    "underline": {"type": "boolean", "description": "Underline on or off"},
                    "font_size": {"type": "number", "description": "Font size in points"},
                    "font_name": {"type": "string", "description": "Font family, e.g. \"Liberation Mono\" for a code block"},
                    "color": {
                        "type": "string",
                        "description": "Text colour as #RRGGBB, e.g. \"#0000CC\". This is what syntax highlighting is made of: find the tokens with find_text_live and colour each one"
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Colour behind the characters as #RRGGBB, e.g. \"#FFFFCC\" to highlight a phrase"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.format_range_live
        }

        self.tools["apply_paragraph_style_live"] = {
            "description": "Give the paragraphs at an address a paragraph style. Prefer this over setting a font by hand for code blocks and quotations: \"Preformatted Text\" carries the monospace font and the spacing together. Use list_styles_live for the names this document has",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Where to act: {\"paragraph\": N} for a whole body paragraph, {\"paragraph\": N, \"offset\": K, \"length\": L} for part of one, or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "style": {
                        "type": "string",
                        "description": "Paragraph style name, e.g. \"Preformatted Text\", \"Heading 2\", \"Quotations\""
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address", "style"]
            },
            "handler": self.apply_paragraph_style_live
        }

        self.tools["format_paragraph_live"] = {
            "description": "Put a border and a background behind the paragraph at an address. Formatting several consecutive paragraphs the same way draws one box around the group, which is how a code block gets framed",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "object",
                        "description": "Which paragraph: {\"paragraph\": N} or {\"selection\": true}",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Fill behind the paragraph as #RRGGBB, e.g. \"#F5F5F5\" for a code block"
                    },
                    "border": {
                        "type": "boolean",
                        "description": "True draws a box on all four sides, false removes it"
                    },
                    "border_color": {
                        "type": "string",
                        "description": "Border colour as #RRGGBB, default \"#808080\"",
                        "default": "#808080"
                    },
                    "border_width": {
                        "type": "number",
                        "description": "Border thickness in points, default 0.5",
                        "default": 0.5
                    },
                    "padding": {
                        "type": "number",
                        "description": "Space between the border and the text, in points"
                    },
                    "track_changes": {
                        "type": "boolean",
                        "description": "Omit to follow the document's own setting; true records this change, false refuses to record it"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                },
                "required": ["address"]
            },
            "handler": self.format_paragraph_live
        }

        self.tools["list_styles_live"] = {
            "description": "List the style names the active Writer document has, so a style is never guessed at",
            "parameters": {
                "type": "object",
                "properties": {
                    "family": {
                        "type": "string",
                        "description": "Which family to list: ParagraphStyles (default), CharacterStyles, PageStyles, FrameStyles, NumberingStyles",
                        "default": "ParagraphStyles"
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.list_styles_live
        }

        self.tools["describe_style_live"] = {
            "description": "Report everything about one style: what the style sets itself — its own definition, the same handful of properties that stands in the document's styles.xml — and what is in force for text using it, with each value saying whether it comes from this style or is inherited. Also its parent, the chain it inherits from, the style that follows it, whether it is user-defined and whether it is in use. Name a style, or give an address (or nothing, for the caret) to describe the style the text there uses",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The style's name, as list_styles_live reports it. Omit it to describe the style used at the address, or at the caret"
                    },
                    "family": {
                        "type": "string",
                        "description": "Which kind of style: paragraph, character, page, frame, numbering, table or cell",
                        "default": "paragraph"
                    },
                    "address": {
                        "type": "object",
                        "description": "Whose style to describe: {\"paragraph\": N}, {\"paragraph\": N, \"offset\": K, \"length\": L} or {\"selection\": true}. Only used when no name is given",
                        "properties": {
                            "paragraph": {"type": "integer"},
                            "offset": {"type": "integer"},
                            "length": {"type": "integer"},
                            "selection": {"type": "boolean"}
                        }
                    },
                    "all_properties": {
                        "type": "boolean",
                        "description": "Also return every property the style carries, with its state — around 200 for a paragraph style",
                        "default": False
                    },
                    "document": {
                        "type": "string",
                        "description": "URL of the document to act on, from list_open_documents; defaults to the active document"
                    }
                }
            },
            "handler": self.describe_style_live
        }

    def format_text_live(self, **formatting) -> Dict[str, Any]:
        """Apply formatting to selected text"""
        return self.uno_bridge.format_text(formatting)

    def format_range_live(self, address: Any, bold: Optional[bool] = None,
                          italic: Optional[bool] = None,
                          underline: Optional[bool] = None,
                          font_size: Optional[float] = None,
                          font_name: Optional[str] = None,
                          color: Any = None,
                          background_color: Any = None,
                          track_changes: Optional[bool] = None,
                          document: Optional[str] = None) -> Dict[str, Any]:
        """Apply character formatting to the text at an address"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.format_range(address, bold=bold, italic=italic,
                                            underline=underline,
                                            font_size=font_size,
                                            font_name=font_name, color=color,
                                            background_color=background_color,
                                            track_changes=track_changes, doc=doc)

    def format_paragraph_live(self, address: Any, background_color: Any = None,
                              border: Optional[bool] = None,
                              border_color: Any = "#808080",
                              border_width: float = 0.5,
                              padding: Optional[float] = None,
                              track_changes: Optional[bool] = None,
                              document: Optional[str] = None) -> Dict[str, Any]:
        """Put a border and a background behind a paragraph"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.format_paragraph(
            address, background_color=background_color, border=border,
            border_color=border_color, border_width=border_width,
            padding=padding, track_changes=track_changes, doc=doc)

    def apply_paragraph_style_live(self, address: Any, style: str,
                                   track_changes: Optional[bool] = None,
                                   document: Optional[str] = None) -> Dict[str, Any]:
        """Give the paragraphs at an address a paragraph style"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.apply_paragraph_style(
            address, style, track_changes=track_changes, doc=doc)

    def list_styles_live(self, family: str = "ParagraphStyles",
                         document: Optional[str] = None) -> Dict[str, Any]:
        """List the style names the document has"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.list_styles(family=family, doc=doc)

    def describe_style_live(self, name: Optional[str] = None,
                            family: str = "paragraph", address: Any = None,
                            all_properties: bool = False,
                            document: Optional[str] = None) -> Dict[str, Any]:
        """Report what a style sets itself and what is in force under it"""
        doc, error = self._target_document(document)
        if error:
            return error
        return self.uno_bridge.describe_style(name=name, family=family,
                                              address=address,
                                              all_properties=all_properties,
                                              doc=doc)
